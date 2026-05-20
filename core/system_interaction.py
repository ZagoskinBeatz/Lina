"""
Lina — System Interaction Layer.

Обеспечивает реальное взаимодействие ИИ с операционной системой:
1. Сбор актуальной информации о системе (ядро, distro, оборудование)
2. Выполнение команд с проверкой безопасности
3. Парсинг LLM-ответов для извлечения исполняемых команд
4. Формирование контекста для промпта LLM

Это ЕДИНСТВЕННЫЙ модуль, через который Pipeline может воздействовать на ОС.
"""

import logging
import os
import platform
import re
import shlex
import subprocess
import shutil
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  Live System Snapshot — реальные данные ОС, собранные в момент запроса
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SystemSnapshot:
    """Снимок системы — собирается разово при старте и обновляется по запросу."""
    kernel: str = ""
    hostname: str = ""
    username: str = ""
    distro: str = ""
    distro_id: str = ""
    de: str = ""
    shell: str = ""
    uptime: str = ""
    cpu_model: str = ""
    cpu_cores: int = 0
    ram_total_mb: int = 0
    ram_free_mb: int = 0
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0
    display_server: str = ""
    gpu: str = ""
    # Сетевые
    ip_local: str = ""
    # Доступные утилиты
    has_brightnessctl: bool = False
    has_pactl: bool = False
    has_nmcli: bool = False
    has_systemctl: bool = False
    has_journalctl: bool = False
    has_flatpak: bool = False
    has_snap: bool = False
    has_docker: bool = False


def collect_system_snapshot() -> SystemSnapshot:
    """Собирает реальную информацию о системе через procfs / sysfs / CLI."""
    snap = SystemSnapshot()

    # ── Базовые ──
    snap.kernel = platform.release()
    snap.hostname = platform.node()
    snap.username = os.environ.get("USER", os.environ.get("LOGNAME", "unknown"))
    snap.shell = os.environ.get("SHELL", "")

    # ── Distro ──
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    snap.distro = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("ID="):
                    snap.distro_id = line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        snap.distro = "Linux"

    # ── Desktop Environment ──
    snap.de = os.environ.get("XDG_CURRENT_DESKTOP", "")
    snap.display_server = "wayland" if os.environ.get("WAYLAND_DISPLAY") else (
        "x11" if os.environ.get("DISPLAY") else "tty"
    )

    # ── CPU ──
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    snap.cpu_model = line.split(":", 1)[1].strip()
                    break
        snap.cpu_cores = os.cpu_count() or 0
    except Exception:
        pass

    # ── RAM ──
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    snap.ram_total_mb = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    snap.ram_free_mb = int(line.split()[1]) // 1024
    except Exception:
        pass

    # ── Disk ──
    try:
        st = os.statvfs("/")
        snap.disk_total_gb = round(st.f_blocks * st.f_frsize / (1024 ** 3), 1)
        snap.disk_free_gb = round(st.f_bavail * st.f_frsize / (1024 ** 3), 1)
    except Exception:
        pass

    # ── GPU ──
    try:
        r = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=5
        )
        for line in r.stdout.splitlines():
            if "VGA" in line or "3D" in line:
                snap.gpu = line.split(": ", 1)[-1].strip() if ": " in line else line.strip()
                break
    except Exception:
        pass

    # ── Uptime ──
    try:
        with open("/proc/uptime") as f:
            seconds = int(float(f.read().split()[0]))
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            snap.uptime = f"{hours}ч {minutes}мин"
    except Exception:
        pass

    # ── IP ──
    try:
        r = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=3
        )
        ips = r.stdout.strip().split()
        snap.ip_local = ips[0] if ips else ""
    except Exception:
        pass

    # ── Доступные утилиты ──
    snap.has_brightnessctl = shutil.which("brightnessctl") is not None
    snap.has_pactl = shutil.which("pactl") is not None
    snap.has_nmcli = shutil.which("nmcli") is not None
    snap.has_systemctl = shutil.which("systemctl") is not None
    snap.has_journalctl = shutil.which("journalctl") is not None
    snap.has_flatpak = shutil.which("flatpak") is not None
    snap.has_snap = shutil.which("snap") is not None
    snap.has_docker = shutil.which("docker") is not None

    return snap


def format_snapshot_for_prompt(snap: SystemSnapshot) -> str:
    """Форматирует снимок системы для включения в LLM промпт."""
    tools = []
    if snap.has_brightnessctl:
        tools.append("brightnessctl")
    if snap.has_pactl:
        tools.append("pactl")
    if snap.has_nmcli:
        tools.append("nmcli")
    if snap.has_systemctl:
        tools.append("systemctl")
    if snap.has_flatpak:
        tools.append("flatpak")
    if snap.has_docker:
        tools.append("docker")

    return (
        f"### СИСТЕМА (реальные данные)\n"
        f"Дистрибутив: {snap.distro}\n"
        f"Ядро: {snap.kernel}\n"
        f"Хост: {snap.hostname} | Пользователь: {snap.username}\n"
        f"CPU: {snap.cpu_model} ({snap.cpu_cores} ядер)\n"
        f"RAM: {snap.ram_free_mb}/{snap.ram_total_mb} MB свободно\n"
        f"Диск /: {snap.disk_free_gb}/{snap.disk_total_gb} GB свободно\n"
        f"GPU: {snap.gpu or 'не определён'}\n"
        f"DE: {snap.de or 'нет'} | Display: {snap.display_server}\n"
        f"Shell: {snap.shell} | Uptime: {snap.uptime}\n"
        f"Доступные утилиты: {', '.join(tools) if tools else 'базовые'}\n"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Command Extractor — парсит LLM-ответ и извлекает команды для выполнения
# ═══════════════════════════════════════════════════════════════════════════════

# Команды, запрещённые к автоматическому выполнению
_DANGEROUS_PATTERNS = [
    r"rm\s+(-rf?|--recursive)\s+/",
    r"rm\s+(-rf?|--recursive)\s+~",       # home dir wipe
    r"rm\s+(-rf?|--recursive)\s+/\*",     # glob root wipe
    r"mkfs\.",
    r"dd\s+if=",
    r":\s*\(\)\s*\{",                      # fork bomb
    r"shutdown|reboot|poweroff",
    r"chmod\s+777\s+/",
    r"chown\s+.*\s+/",
    r">\s*/dev/sd",
    r"wipefs",
    r"fdisk",
    r"parted",
    r"passwd",
    # Indirect execution vectors
    r"python[23]?\s+-c\s",
    r"perl\s+-e\s",
    r"ruby\s+-e\s",
    r"\beval\s",
    r"\bexec\s",
    r"bash\s+-c\s",
    r"sh\s+-c\s",
    # Download-and-execute (any pipe-to-shell, even via sudo/env/nohup)
    r"(curl|wget|fetch)\s.*\|\s*(?:sudo\s+)?(?:env\s+\S+\s+)?(?:nohup\s+)?(sh|bash|zsh|dash|ksh|fish|python[23]?|perl|ruby)\b",
    r"(curl|wget|fetch)\s.*\|\s*(?:su\s+(?:-c\s+)?)?[\"']?(sh|bash)[\"']?",
    # base64-decode-and-execute
    r"base64\s.*\|\s*(?:sudo\s+)?(sh|bash)",
    # Destructive find
    r"find\s.*-delete",
    r"find\s.*-exec\s+rm\s",
    # Dangerous file operations
    r"mv\s+/\S+\s+/dev/null",
    r"truncate\s",
    r"shred\s",
    # Subshell expansion (bypass via $() or backticks)
    r"\$\(.*\)",
    r"`[^`]+`",
    # Indirect execution via env/nohup/xargs
    r"\benv\s+(sh|bash|python|perl|ruby)\b",
    r"\bnohup\s+(rm|dd|mkfs|shred)\b",
    r"\bxargs\s+rm\b",
    # sudo rm -rf /
    r"\bsudo\s+rm\s+-rf\s+/",
]
_DANGEROUS_RE = re.compile("|".join(_DANGEROUS_PATTERNS), re.IGNORECASE)

# Команды, безопасные для автовыполнения (read-only или инфо)
_SAFE_AUTO_PATTERNS = [
    r"^(cat|head|tail|less|wc|du|df|ls|tree|grep)\s",
    r"^(uname|hostname|whoami|id|uptime|date|cal)\b",
    r"^(free|top|htop|ps|pgrep|lsblk|lscpu|lspci|lsusb)\b",
    r"^(ip\s+(a|addr|link|route)|ss\s|ping\s|dig\s|nslookup)\b",
    r"^(systemctl\s+(status|is-active|list-units))\b",
    r"^(journalctl\s)",
    r"^(pacman\s+-Q|dpkg\s+-l|rpm\s+-q|flatpak\s+list)\b",
    r"^(brightnessctl\b)",
    r"^(pactl\b|amixer\b)",
    r"^(nmcli\b)",
    r"^(inxi|neofetch|fastfetch)\b",
    r"^(xdg-open|xdg-mime)\b",
    r"^(echo|printf)\s",
]
_SAFE_AUTO_RE = re.compile("|".join(_SAFE_AUTO_PATTERNS), re.IGNORECASE)


def _strip_inline_comment(line: str) -> str:
    """Strip a trailing `# comment` from a shell command line.

    Корректно обрабатывает `#` внутри кавычек: `echo "hello # world"` —
    не комментарий. А вот `pacman -S firefox  # install` — да.
    """
    if "#" not in line:
        return line
    in_single = False
    in_double = False
    out = []
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            out.append(c)
            out.append(line[i + 1])
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
            out.append(c)
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            out.append(c)
            i += 1
            continue
        if c == "#" and not in_single and not in_double:
            # Комментарий — обрезаем всё до конца строки.
            break
        out.append(c)
        i += 1
    return "".join(out).rstrip()


# Команды, которые ТРЕБУЮТ root и почти всегда нуждаются в sudo.
# Если LLM выдала такую команду без sudo — добавим его автоматически.
# Это типичная ошибка моделей: они помнят, что pacman -S ставит пакет,
# но забывают про sudo. Без него команда просто упадёт.
_SUDOER_FIRST_WORDS = {
    "pacman", "apt", "apt-get", "dnf", "yum", "zypper",
    "pkg", "emerge", "xbps-install", "xbps-remove",
    "systemctl", "journalctl",
    "mount", "umount",
    "mkfs", "fdisk", "parted",
    "useradd", "userdel", "usermod", "groupadd", "groupdel",
    "chown", "chmod",  # часто, но не всегда
    "modprobe", "rmmod", "insmod",
    "iptables", "nft", "ufw",
    "swapon", "swapoff",
    "hwclock", "timedatectl",
}
# Подмножество subcommands, которые точно требуют root.
_SUDO_REQUIRED_SUBCOMMANDS = {
    "pacman": {"-S", "-Sy", "-Syu", "-Syyu", "-R", "-Rs", "-Rns", "-U"},
    "apt":     {"install", "remove", "purge", "update", "upgrade",
                "dist-upgrade", "autoremove"},
    "apt-get": {"install", "remove", "purge", "update", "upgrade",
                "dist-upgrade", "autoremove"},
    "dnf":     {"install", "remove", "upgrade", "update", "autoremove"},
    "yum":     {"install", "remove", "upgrade", "update"},
    "zypper":  {"install", "in", "remove", "rm", "update", "up", "dup"},
    "systemctl": {"start", "stop", "restart", "reload", "enable", "disable",
                  "mask", "unmask"},
}


def _needs_sudo_prefix(line: str) -> bool:
    """Heuristic: True if command must be prefixed with sudo to actually run.

    Защищает от типичной LLM-ошибки: модель помнит что `pacman -S` ставит
    пакет, но забывает sudo. Без sudo команда упадёт с «you cannot perform
    this operation unless you are root».
    """
    if line.startswith("sudo "):
        return False
    parts = line.split()
    if not parts:
        return False
    head = parts[0]
    if head not in _SUDOER_FIRST_WORDS:
        return False
    # Для пакетных менеджеров проверяем подкоманду — иначе можно случайно
    # форснуть sudo на read-only вызовы вроде `pacman -Q`.
    subs = _SUDO_REQUIRED_SUBCOMMANDS.get(head)
    if subs is not None:
        if len(parts) < 2:
            return False
        return parts[1] in subs
    # Для остальных — добавляем sudo по умолчанию.
    return True

# Shell builtins — нельзя выполнить через subprocess (это не бинарники)
_SHELL_BUILTINS = frozenset({
    "cd", "source", "export", "unset", "alias", "unalias",
    "set", "shopt", "pushd", "popd", "dirs", "bg", "fg",
    "jobs", "disown", "builtin", "command", "type", "hash",
    "ulimit", "umask", "readonly", "declare", "local", "typeset",
})

# Интерактивные команды — зависнут в subprocess (ждут пользовательского ввода)
_INTERACTIVE_COMMANDS = frozenset({
    "vi", "vim", "nvim", "nano", "emacs", "micro", "joe", "mcedit",
    "less", "more", "man", "info", "top", "htop", "btop",
    "nmon", "mc", "ranger", "vifm", "tmux", "screen",
    "python", "python3", "ipython", "node", "irb", "ghci",
    "mysql", "psql", "sqlite3", "redis-cli", "mongo",
    "ssh", "telnet", "ftp", "sftp",
    "bash", "zsh", "fish", "sh",
})


@dataclass
class ExtractedCommand:
    """Команда, извлечённая из LLM-ответа."""
    command: str
    is_dangerous: bool = False
    is_safe_auto: bool = False
    description: str = ""
    needs_sudo: bool = False


def extract_commands(llm_response: str) -> List[ExtractedCommand]:
    """
    Извлекает shell-команды из markdown code-блоков LLM-ответа.

    Поддерживает:
      - ```bash ... ```
      - ```sh ... ```
      - ``` ... ``` (без языка, если содержит shell-подобные паттерны)
    """
    commands = []

    # Паттерн для code-блоков (с \n и без \n после языка)
    code_blocks = re.findall(
        r"```(?:bash|sh|shell|console|zsh|fish)?[\s]*\n?(.*?)```",
        llm_response, re.DOTALL | re.IGNORECASE
    )

    for block in code_blocks:
        for line in block.strip().splitlines():
            line = line.strip()
            # Сначала отсекаем пустые строки и комментарии (в т.ч. `# заголовок`)
            if not line or line.startswith("#"):
                continue
            # Затем снимаем bash-промпт-префиксы вроде `$ ls` или `> echo`
            stripped = re.sub(r"^[\$>]\s*", "", line)
            if not stripped or stripped.startswith("#"):
                continue
            line = stripped

            # Срезаем inline-комментарий `cmd ... # comment` — без этого
            # `shlex.split` оставит `#` и слова после в argv, и команда упадёт.
            # Учитываем что `#` внутри одинарных/двойных кавычек —
            # не комментарий, а часть строки.
            line = _strip_inline_comment(line)
            if not line:
                continue

            cmd = ExtractedCommand(command=line)
            cmd.needs_sudo = line.startswith("sudo ")

            # Авто-исправление: если LLM забыла sudo для pacman/apt/dnf
            # и подобных — добавляем его сами. Без sudo команда упадёт
            # с «you cannot perform this operation unless you are root».
            if not cmd.needs_sudo and _needs_sudo_prefix(line):
                line = "sudo " + line
                cmd.command = line
                cmd.needs_sudo = True
                logger.debug("auto-prefixed sudo: %s", line)

            cmd.is_dangerous = bool(_DANGEROUS_RE.search(line))
            cmd.is_safe_auto = bool(_SAFE_AUTO_RE.match(
                line.replace("sudo ", "", 1) if cmd.needs_sudo else line
            ))
            commands.append(cmd)

    return commands


# ═══════════════════════════════════════════════════════════════════════════════
#  Action Executor — выполняет команды с проверками безопасности
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExecutionResult:
    """Результат выполнения команды."""
    command: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    success: bool = True
    skipped: bool = False
    reason: str = ""


class ActionExecutor:
    """
    Выполняет команды от имени пользователя через sandbox.

    Политика безопасности:
    - Опасные команды → БЛОКИРУЮТСЯ с объяснением
    - Безопасные info-команды → выполняются автоматически
    - Остальные → выполняются с логированием
    - sudo-команды → запрашивают подтверждение (в интерактиве)
    """

    def __init__(self, interactive: bool = True, timeout: int = 30):
        self._interactive = interactive
        self._timeout = timeout

    def execute(self, cmd: ExtractedCommand) -> ExecutionResult:
        """Выполнить одну команду с проверкой безопасности."""
        result = ExecutionResult(command=cmd.command)

        # Блокируем опасные
        if cmd.is_dangerous:
            result.success = False
            result.skipped = True
            result.reason = f"⛔ Команда заблокирована (опасная): {cmd.command}"
            logger.warning("BLOCKED dangerous command: %s", cmd.command)
            return result

        # Shell builtins (cd, source, export) — нельзя выполнить через subprocess
        first_word = cmd.command.split()[0] if cmd.command.strip() else ""
        bare_word = first_word.replace("sudo", "").strip() if first_word == "sudo" else first_word
        if len(cmd.command.split()) > 1 and cmd.command.split()[0] == "sudo":
            bare_word = cmd.command.split()[1]
        if bare_word in _SHELL_BUILTINS:
            result.skipped = True
            result.reason = (
                f"⏭ Пропущена shell-встроенная команда (builtin): {cmd.command} — "
                "выполните вручную в терминале"
            )
            return result

        # Интерактивные команды (vi, nano, less, top) — зависнут в subprocess
        if bare_word in _INTERACTIVE_COMMANDS:
            result.skipped = True
            result.reason = (
                f"⏭ Пропущена интерактивная команда: {cmd.command} — "
                "выполните вручную в терминале"
            )
            return result

        # sudo в неинтерактивном режиме — пропускаем
        if cmd.needs_sudo and not self._interactive:
            result.skipped = True
            result.reason = f"⏭ Пропущена sudo-команда в неинтерактивном режиме: {cmd.command}"
            return result

        # Выполняем
        try:
            logger.info("Executing: %s", cmd.command)
            import shlex as _shlex
            proc = subprocess.run(
                _shlex.split(cmd.command),
                shell=False,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env={**os.environ, "LANG": "C.UTF-8"},
            )
            result.stdout = proc.stdout.strip()
            result.stderr = proc.stderr.strip()
            result.returncode = proc.returncode
            result.success = proc.returncode == 0
        except subprocess.TimeoutExpired:
            result.success = False
            result.reason = f"⏰ Таймаут ({self._timeout}с): {cmd.command}"
        except Exception as e:
            result.success = False
            result.reason = f"❌ Ошибка: {e}"

        return result

    def execute_many(self, commands: List[ExtractedCommand]) -> List[ExecutionResult]:
        """Выполнить список команд последовательно."""
        results = []
        for cmd in commands:
            results.append(self.execute(cmd))
        return results


# ═══════════════════════════════════════════════════════════════════════════════
#  Query Pre-Processor — обогащает запрос реальными данными перед отправкой LLM
# ═══════════════════════════════════════════════════════════════════════════════

# Маппинг запрос → shell-команда для быстрого ответа без LLM
# Стемы для устойчивости к склонениям: "памят" ⊂ "памяти", "памятью" и т.д.

# Паттерн для обнаружения брендов / моделей устройств в запросе.
# Если запрос содержит бренд, то «процессор» → спршивает про чужой CPU, а не про lscpu.
_PRODUCT_CONTEXT_RE = re.compile(
    r"(?i)\b("
    r"oneplus|realme|samsung|galaxy|xiaomi|redmi|poco|huawei|honor"
    r"|apple|iphone|ipad|macbook|imac|pixel|google\s+pixel"
    r"|oppo|vivo|nothing|motorola|moto\b|nokia|sony|xperia"
    r"|asus|lenovo|acer|dell|hp\b|thinkpad|ideapad|pavilion"
    r"|snapdragon|dimensity|exynos|helio|mediatek|kirin|tensor"
    r"|geforce|radeon|ryzen|intel\s+core|core\s+i[3579]"
    r"|rtx\s*\d+|gtx\s*\d+|rx\s*\d+"
    r")\b",
)

_DIRECT_QUERIES: Dict[str, str] = {
    # Системная информация
    "ядр": "uname -r",           # ядро, ядра
    "kernel": "uname -r",
    "hostname": "hostname",
    "хост": "hostname",          # хоста, хостов
    "uptime": "uptime -p",
    "аптайм": "uptime -p",
    "время работ": "uptime -p",  # работы, работу
    "дата": "date",
    "время сейчас": "date",
    "который час": "date '+%H:%M:%S'",
    "кто я": "whoami",

    # Железо
    "процессор": "lscpu | head -15",
    "cpu": "lscpu | head -15",
    "памят": "free -h",          # память, памяти, памятью
    "ram": "free -h",
    "оперативн": "free -h",      # оперативная, оперативной
    "диск": "df -h /",           # диск, диски, диска, дисков
    "место": "df -h",
    "usb": "lsusb",
    "pci": "lspci",
    "блоч": "lsblk",             # блочные, блочных
    "lsblk": "lsblk",

    # Сеть
    "ip адрес": "ip -brief addr",
    "ip": "ip -brief addr",
    "сеть": "ip -brief addr && echo '---' && ss -tlnp 2>/dev/null | head -10",
    "сети": "ip -brief addr && echo '---' && ss -tlnp 2>/dev/null | head -10",
    "dns": "cat /etc/resolv.conf | grep nameserver",
    "wifi": "nmcli dev wifi list 2>/dev/null || echo 'nmcli недоступен'",
    "wi-fi": "nmcli dev wifi list 2>/dev/null || echo 'nmcli недоступен'",

    # Процессы
    "процесс": "ps aux --sort=-%mem | head -15",  # процессы, процессов
    "топ процесс": "ps aux --sort=-%mem | head -15",

    # Пакеты
    "пакет": "pacman -Q 2>/dev/null | wc -l || dpkg -l 2>/dev/null | wc -l || rpm -qa 2>/dev/null | wc -l",
}

# Маппинг действие → shell-команда для прямого выполнения
_DIRECT_ACTIONS: Dict[str, str] = {
    # Яркость
    "яркость 100": "brightnessctl set 100%",
    "яркость 100%": "brightnessctl set 100%",
    "яркость максимум": "brightnessctl set 100%",
    "яркость на максимум": "brightnessctl set 100%",
    "яркость 50": "brightnessctl set 50%",
    "яркость 50%": "brightnessctl set 50%",
    "яркость 0": "brightnessctl set 0%",
    "яркость минимум": "brightnessctl set 1%",
    "яркость на минимум": "brightnessctl set 1%",

    # Звук
    "громкость 100": "pactl set-sink-volume @DEFAULT_SINK@ 100%",
    "громкость 100%": "pactl set-sink-volume @DEFAULT_SINK@ 100%",
    "громкость максимум": "pactl set-sink-volume @DEFAULT_SINK@ 100%",
    "громкость 50": "pactl set-sink-volume @DEFAULT_SINK@ 50%",
    "громкость 50%": "pactl set-sink-volume @DEFAULT_SINK@ 50%",
    "звук 100": "pactl set-sink-volume @DEFAULT_SINK@ 100%",
    "звук 100%": "pactl set-sink-volume @DEFAULT_SINK@ 100%",
    "звук максимум": "pactl set-sink-volume @DEFAULT_SINK@ 100%",
    "звук 50": "pactl set-sink-volume @DEFAULT_SINK@ 50%",
    "звук 50%": "pactl set-sink-volume @DEFAULT_SINK@ 50%",
    "звук выключи": "pactl set-sink-mute @DEFAULT_SINK@ 1",
    "выключи звук": "pactl set-sink-mute @DEFAULT_SINK@ 1",
    "без звук": "pactl set-sink-mute @DEFAULT_SINK@ 1",
    "звук включи": "pactl set-sink-mute @DEFAULT_SINK@ 0",
    "включи звук": "pactl set-sink-mute @DEFAULT_SINK@ 0",
    "mute": "pactl set-sink-mute @DEFAULT_SINK@ toggle",
    "unmute": "pactl set-sink-mute @DEFAULT_SINK@ 0",

    # WiFi / Bluetooth toggle
    "выключи wifi": "nmcli radio wifi off",
    "включи wifi": "nmcli radio wifi on",
    "выключи wi-fi": "nmcli radio wifi off",
    "включи wi-fi": "nmcli radio wifi on",
    "выключи вай-фай": "nmcli radio wifi off",
    "включи вай-фай": "nmcli radio wifi on",
    "выключи вайфай": "nmcli radio wifi off",
    "включи вайфай": "nmcli radio wifi on",
    "wifi выкл": "nmcli radio wifi off",
    "wifi вкл": "nmcli radio wifi on",
    "wifi off": "nmcli radio wifi off",
    "wifi on": "nmcli radio wifi on",
    "выключи блютуз": "bluetoothctl power off",
    "включи блютуз": "bluetoothctl power on",
    "выключи bluetooth": "bluetoothctl power off",
    "включи bluetooth": "bluetoothctl power on",
    "bluetooth off": "bluetoothctl power off",
    "bluetooth on": "bluetoothctl power on",

    # Сеть / Интернет (connect / disconnect)
    "отключи интернет": "nmcli networking off",
    "выключи интернет": "nmcli networking off",
    "отключи сеть": "nmcli networking off",
    "выключи сеть": "nmcli networking off",
    "отключи инет": "nmcli networking off",
    "выключи инет": "nmcli networking off",
    "интернет выкл": "nmcli networking off",
    "интернет откл": "nmcli networking off",
    "включи интернет": "nmcli networking on",
    "подключи интернет": "nmcli networking on",
    "включи сеть": "nmcli networking on",
    "подключи сеть": "nmcli networking on",
    "включи инет": "nmcli networking on",
    "подключи инет": "nmcli networking on",
    "интернет вкл": "nmcli networking on",

    # Система (безопасные команды — без --noconfirm)
    "обнови систему": "sudo pacman -Syu || sudo apt update && sudo apt upgrade",
    "обнови пакеты": "sudo pacman -Syu || sudo apt update && sudo apt upgrade",
    "очисти кэш": "sudo pacman -Sc 2>/dev/null; sudo journalctl --vacuum-time=7d 2>/dev/null; echo 'Готово'",
    # Управление питанием / экраном
    "заблокируй экран": "loginctl lock-session",
    "блокировка экрана": "loginctl lock-session",
    "заблокируй": "loginctl lock-session",
    "спящий режим": "systemctl suspend",
    "усни": "systemctl suspend",
    "усыпи": "systemctl suspend",
    "режим сна": "systemctl suspend",
}


def _normalize_query(query: str) -> str:
    """Нормализует запрос: lowercase, убирает лишнее."""
    q = query.lower().strip()
    # Убираем "пожалуйста", "можешь", "покажи", "скажи", "сделай" и т.д.
    for prefix in [
        "покажи ", "скажи ", "сделай ", "выполни ", "запусти ",
        "подскажи ", "можешь ", "пожалуйста ", "мне ", "какой ",
        "какая ", "какое ", "каков ", "что такое ", "где ",
        "сколько ", "у меня ", "мой ", "моя ", "моё ", "мои ",
        "есть ", "текущ", "нынешн",
    ]:
        q = q.replace(prefix, "")
    q = q.strip()
    return q


# ═══════════════════════════════════════════════════════════════════════════════
#  Fuzzy Actions — «добавь яркости», «открой проводник», «запусти хром»
# ═══════════════════════════════════════════════════════════════════════════════
# NOTE: Старые _detect_installed_apps / _APP_ALIASES / _resolve_app_command
# удалены. Весь поиск и запуск приложений через ApplicationResolver.


# Fuzzy brightness/volume patterns (без конкретных процентов)
_FUZZY_BRIGHTNESS_UP = re.compile(
    r"(добав|прибав|увелич|повыс|побольше|ярче|подним)\w*\s*(ярко|свет|подсветк)",
    re.IGNORECASE,
)
_FUZZY_BRIGHTNESS_DOWN = re.compile(
    r"(убав|уменьш|снизь|пониз|поменьше|темнее|убер)\w*\s*(ярко|свет|подсветк)",
    re.IGNORECASE,
)
_FUZZY_VOLUME_UP = re.compile(
    r"(добав|прибав|увелич|повыс|побольше|погромче|громче|подним)\w*\s*(громк|звук|volume)",
    re.IGNORECASE,
)
_FUZZY_VOLUME_DOWN = re.compile(
    r"(убав|уменьш|снизь|пониз|поменьше|потише|тише)\w*\s*(громк|звук|volume)",
    re.IGNORECASE,
)
# Also match reversed word order: "яркость добавь", "звук прибавь"
_FUZZY_BRIGHTNESS_UP2 = re.compile(
    r"(ярко|свет|подсветк)\w*\s*(добав|прибав|увелич|повыс|подним|побольше)",
    re.IGNORECASE,
)
_FUZZY_BRIGHTNESS_DOWN2 = re.compile(
    r"(ярко|свет|подсветк)\w*\s*(убав|уменьш|снизь|пониз|убер|поменьше)",
    re.IGNORECASE,
)
_FUZZY_VOLUME_UP2 = re.compile(
    r"(громк|звук)\w*\s*(добав|прибав|увелич|повыс|подним|побольше)",
    re.IGNORECASE,
)
_FUZZY_VOLUME_DOWN2 = re.compile(
    r"(громк|звук)\w*\s*(убав|уменьш|снизь|пониз|потише|поменьше)",
    re.IGNORECASE,
)

# Open/launch patterns
_OPEN_PATTERN = re.compile(
    r"(?:открой|запусти|запуск|включи|вруби|покажи|стартуй|стартани|отрой)\s+(.+)",
    re.IGNORECASE,
)

# Close/kill app patterns
_CLOSE_PATTERN = re.compile(
    r"(?:закрой|заверши|убей|убить|закрыть|завершить|стопни|kill|close|quit)\s+(.+)",
    re.IGNORECASE,
)

# Undo/revert patterns
_UNDO_PATTERNS = re.compile(
    r"^(?:включи|выключи|сделай|верни|отмени)?\s*(?:обратно|назад|как было|undo|revert)\s*$",
    re.IGNORECASE,
)


_GREETING_PATTERNS = re.compile(
    r"^(привет|здравствуй|здорово|хай|хей|hi|hello|hey|добрый\s*(день|вечер|утро)"
    r"|приветствую|салют|йо|ку|здаров|дарова)[\s!.?]*$",
    re.IGNORECASE,
)

_GREETING_RESPONSES = [
    "Привет! Чем могу помочь?",
    "Здравствуйте! Что нужно сделать?",
    "Привет! Спрашивайте — я готов.",
    "Привет! Рада помочь. Что интересует?",
    "Здравствуйте! Слушаю внимательно.",
    "Привет! Какой вопрос?",
    "Приветствую! Чем займёмся?",
    "Привет! Готова к работе.",
]

_META_RESPONSES: Dict[str, str] = {
    "помощь": (
        "📋 Lina v1.0 — ваш Linux-ассистент.\n"
        "Умею:\n"
        "  • Отвечать на вопросы о системе (ядро, память, диски, сеть)\n"
        "  • Управлять яркостью и громкостью\n"
        "  • Выполнять безопасные команды\n"
        "Примеры: «какое у меня ядро», «яркость 70%», «сколько памяти»"
    ),
    "help": (
        "📋 Lina v1.0 — your Linux assistant.\n"
        "I can answer system queries, control brightness/volume, run safe commands."
    ),
    "что ты умеешь": (
        "Я могу:\n"
        "  • Показать информацию о системе (ядро, CPU, RAM, диски, сеть)\n"
        "  • Управлять яркостью и громкостью\n"
        "  • Выполнять безопасные shell-команды\n"
        "  • Отвечать на вопросы о Linux"
    ),
    "кто ты": "Я Lina — локальный Linux-ассистент. Работаю полностью на вашем компьютере.",
    "who are you": "I'm Lina — a local Linux assistant running entirely on your machine.",
    "версия": "Lina v1.0.0 — LLaMA, русскоязычный Linux-ассистент.",
    "version": "Lina v1.0.0",
}


class QueryPreprocessor:
    """
    Препроцессор запросов.

    0. Приветствия и мета-запросы → мгновенный ответ
    1. Пробует сопоставить с _DIRECT_QUERIES → выполняет и возвращает результат
    2. Пробует сопоставить с _DIRECT_ACTIONS → выполняет действие
    3. Для обычных запросов → собирает системный контекст для LLM
    """

    def __init__(self, snapshot: Optional[SystemSnapshot] = None):
        self._snapshot = snapshot or collect_system_snapshot()
        self._executor = ActionExecutor(interactive=True)
        self._greeting_idx = 0
        # Контекст последнего действия для «включи обратно» / «отмени»
        self._last_action: Optional[str] = None   # cmd that was executed
        self._last_undo: Optional[str] = None     # reverse cmd

    @property
    def snapshot(self) -> SystemSnapshot:
        return self._snapshot

    def try_direct_answer(self, query: str) -> Optional[str]:
        """
        Попытка ответить напрямую без LLM.

        Returns:
            Строка с ответом или None если нужен LLM.
        """
        stripped = query.strip()

        # 0. Приветствия — мгновенный ответ
        if _GREETING_PATTERNS.match(stripped):
            resp = _GREETING_RESPONSES[self._greeting_idx % len(_GREETING_RESPONSES)]
            self._greeting_idx += 1
            return resp

        # 0a. Undo/revert: «включи обратно», «верни как было», «отмени»
        if _UNDO_PATTERNS.match(stripped):
            return self._handle_undo()

        # 0b. Мета-запросы (помощь, кто ты, версия)
        lower = stripped.lower()
        for key, response in _META_RESPONSES.items():
            if key in lower:
                return response

        # 0c. Smart workflows: BT connect/disconnect, WiFi connect, погода
        smart = self._try_smart_workflow(stripped)
        if smart is not None:
            return smart

        # 0d. Smart system queries — domain modules (instant, no LLM)
        smart_sys = self._try_system_query(stripped)
        if smart_sys is not None:
            return smart_sys

        normalized = _normalize_query(query)

        # 1. Прямые действия ПЕРВЫМИ (длинные ключи сначала)
        #    «выключи wifi» должен выполняться до того, как «wifi» совпадёт
        #    с info-запросом «nmcli dev wifi list»
        for key, cmd in sorted(_DIRECT_ACTIONS.items(), key=lambda kv: -len(kv[0])):
            if key in normalized:
                result = self._run_safe(cmd)
                if result is not None:
                    self._remember_action(cmd)
                    return f"✅ Выполнено: {cmd}\n{result}" if result else f"✅ Выполнено: {cmd}"
                break

        # 2. Прямые info-запросы (длинные ключи сначала — "ip адрес" до "ip")
        #    Пропускаем, если есть action-слова (чтобы «включи wifi» не показал список сетей)
        _action_words = ("выключи", "включи", "вкл ", "выкл ", "toggle", " off", " on")
        _has_action = any(w in lower for w in _action_words)

        # Пропускаем hardware-запросы, если есть бренд/модель продукта
        # «Oneplus Nord CE 3 5G Процессор» → web_search, не lscpu
        _has_product = bool(_PRODUCT_CONTEXT_RE.search(stripped))

        if not _has_action and not _has_product:
            # Пропускаем direct-query fast-path для install / how-to запросов:
            # «как установить telegram» содержит "ram" в слове "telegram",
            # и без этой защиты улетит в `free -h`.
            _is_install_or_howto = bool(re.search(
                r"\b(?:как\s+|подскаж|объясн|расскаж|hint|how\s+to|"
                r"установ|поставь|настро|удал|снес|обнов|инсталл|"
                r"запуст|открой|включи|выключи)\b",
                normalized,
            ))
            if not _is_install_or_howto:
                for key, cmd in sorted(_DIRECT_QUERIES.items(), key=lambda kv: -len(kv[0])):
                    # Whole-word match: "ram" не должен ловиться в "telegram",
                    # "ip" — в "skype", и т.п. Для русских стемов с дефисом или
                    # без — границы слова работают через \b.
                    if re.search(r"\b" + re.escape(key) + r"\b", normalized):
                        result = self._run_safe(cmd)
                        if result is not None:
                            return result
                        break

        # 3. Паттерны яркости с процентами («яркость 80%»)
        m = re.search(r"яркость.*?(\d{1,3})\s*%?", normalized)
        if m:
            pct = min(int(m.group(1)), 100)
            result = self._run_safe(["brightnessctl", "set", f"{pct}%"])
            if result is not None:
                return f"✅ Яркость установлена на {pct}%\n{result}" if result else f"✅ Яркость установлена на {pct}%"

        # 4. Паттерны громкости с процентами («громкость 50%», «звук 50%»)
        m = re.search(r"(?:громкость|звук|volume).*?(\d{1,3})\s*%?", normalized)
        if m:
            pct = min(int(m.group(1)), 100)
            result = self._run_safe(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"])
            if result is not None:
                return f"✅ Громкость установлена на {pct}%"

        # 5. Fuzzy яркость: «добавь яркости», «ярче», «темнее», «сделай ярче»
        # Parse optional delta: «на 20%», «на 30%» → +20% / 30%-
        lower_q = query.lower().strip()
        _delta_m = re.search(r"на\s+(\d{1,3})\s*%?", lower_q)
        _bright_delta = min(int(_delta_m.group(1)), 100) if _delta_m else 10

        _bright_up_words = ("ярче", "светлее", "посветлее", "поярче")
        _is_bright_up = (
            _FUZZY_BRIGHTNESS_UP.search(lower_q)
            or _FUZZY_BRIGHTNESS_UP2.search(lower_q)
            or lower_q in _bright_up_words
            or any(lower_q.endswith(w) for w in _bright_up_words)
        )
        if _is_bright_up:
            result = self._run_safe(["brightnessctl", "set", f"+{_bright_delta}%"])
            if result is not None:
                return f"✅ Яркость увеличена (+{_bright_delta}%)\n{result}"
        _bright_down_words = ("темнее", "потемнее")
        _is_bright_down = (
            _FUZZY_BRIGHTNESS_DOWN.search(lower_q)
            or _FUZZY_BRIGHTNESS_DOWN2.search(lower_q)
            or lower_q in _bright_down_words
            or any(lower_q.endswith(w) for w in _bright_down_words)
        )
        if _is_bright_down:
            result = self._run_safe(["brightnessctl", "set", f"{_bright_delta}%-"])
            if result is not None:
                return f"✅ Яркость уменьшена (-{_bright_delta}%)\n{result}"

        # 6. Fuzzy громкость: «добавь громкости», «потише», «погромче»
        _vol_delta = _bright_delta  # reuse same delta from "на X%"
        _vol_up_words = ("погромче", "громче")
        _is_vol_up = (
            _FUZZY_VOLUME_UP.search(lower_q)
            or _FUZZY_VOLUME_UP2.search(lower_q)
            or lower_q.rstrip("!") in _vol_up_words
            or any(lower_q.endswith(w) for w in _vol_up_words)
        )
        if _is_vol_up:
            result = self._run_safe(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{_vol_delta}%"])
            if result is not None:
                return f"✅ Громкость увеличена (+{_vol_delta}%)"
        _vol_down_words = ("потише", "тише")
        _is_vol_down = (
            _FUZZY_VOLUME_DOWN.search(lower_q)
            or _FUZZY_VOLUME_DOWN2.search(lower_q)
            or lower_q.rstrip("!") in _vol_down_words
            or any(lower_q.endswith(w) for w in _vol_down_words)
        )
        if _is_vol_down:
            result = self._run_safe(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{_vol_delta}%"])
            if result is not None:
                return f"✅ Громкость уменьшена (-{_vol_delta}%)"

        # 7. «Открой X» / «Запусти X» — универсальный запуск приложений
        m = _OPEN_PATTERN.match(stripped)
        if m:
            app_name = m.group(1).strip().lower()
            # Снимаем хвост «в браузере / через хром …» — без этого
            # «открой яндекс музыку в браузере» уйдёт в резолвер по слову
            # «браузер» и запустит пустой Firefox.
            app_name = re.sub(
                r"\s+(?:в|через)\s+(?:браузере?|хроме?|firefox|chrome|"
                r"opera|edge|safari)\.?$",
                "", app_name, flags=re.IGNORECASE,
            ).strip()

            # Сначала проверяем известные сайты (яндекс музыка, ютуб, …) —
            # ДО локального резолвера. Иначе для запроса «открой яндекс
            # музыку в браузере» резолвер по слову «браузер» запустит
            # Firefox без URL.
            try:
                from lina.core.tools import ToolRegistry
                _reg = ToolRegistry()
                site_result = _reg._try_open_known_site(app_name)
                if site_result is not None and site_result.success:
                    return site_result.output
            except Exception as e:
                logger.debug("Site-map fast-path error: %s", e)

            try:
                from lina.core.application_resolver import get_resolver
                resolver = get_resolver()
                result = resolver.launch(app_name)
                if result.success:
                    return result.message
                # Если не найдено — возвращаем подсказку (не None!)
                return result.message
            except Exception as e:
                logger.debug("ApplicationResolver error: %s", e)
                return f"⚠️ Не удалось запустить «{app_name}»: {e}"

        # 8. «Закрой X» / «Заверши X» / «Убей X»
        m = _CLOSE_PATTERN.match(stripped)
        if m:
            return self._handle_close_app(m.group(1).strip())

        return None

    # ── Undo / Revert ────────────────────────────────────────

    # Mapping: command → reverse command (for «включи обратно»)
    _UNDO_MAP: Dict[str, str] = {
        "nmcli radio wifi off": "nmcli radio wifi on",
        "nmcli radio wifi on": "nmcli radio wifi off",
        "bluetoothctl power off": "bluetoothctl power on",
        "bluetoothctl power on": "bluetoothctl power off",
        "pactl set-sink-mute @DEFAULT_SINK@ 1": "pactl set-sink-mute @DEFAULT_SINK@ 0",
        "pactl set-sink-mute @DEFAULT_SINK@ 0": "pactl set-sink-mute @DEFAULT_SINK@ 1",
        "pactl set-sink-mute @DEFAULT_SINK@ toggle": "pactl set-sink-mute @DEFAULT_SINK@ toggle",
    }

    def _remember_action(self, cmd: str) -> None:
        """Remember last action for undo context."""
        self._last_action = cmd
        self._last_undo = self._UNDO_MAP.get(cmd)

    def _handle_undo(self) -> str:
        """Execute undo for the last action."""
        if not self._last_undo:
            return "⚠ Нечего отменять — предыдущее действие не поддерживает откат."
        undo_cmd = self._last_undo
        result = self._run_safe(undo_cmd)
        if result is not None:
            self._last_action = None
            self._last_undo = None
            return f"↩ Отменено: {undo_cmd}"
        return f"⚠ Не удалось отменить: {undo_cmd}"

    # ── Close/Kill App ───────────────────────────────────────

    # Aliases for process names (user says → what to pkill)
    _PROCESS_ALIASES: Dict[str, List[str]] = {
        "хром": ["chrome", "chromium", "google-chrome"],
        "гугл хром": ["chrome", "google-chrome"],
        "гугл": ["chrome", "google-chrome"],
        "фаерфокс": ["firefox"],
        "браузер": ["chrome", "firefox", "chromium"],
        "телеграм": ["telegram", "telegram-desktop", "ayugram", "64gram"],
        "дискорд": ["discord"],
        "спотифай": ["spotify"],
        "стим": ["steam"],
        "код": ["code", "vscodium"],
        "вскод": ["code", "vscodium"],
        "терминал": ["konsole", "alacritty", "kitty", "gnome-terminal"],
        "проводник": ["dolphin", "nautilus", "thunar", "nemo"],
        "файлы": ["dolphin", "nautilus", "thunar", "nemo"],
        "видеоплеер": ["vlc", "mpv", "totem", "celluloid"],
        "музыка": ["spotify", "rhythmbox", "elisa"],
        "офис": ["soffice", "libreoffice"],
    }

    def _handle_close_app(self, app_name: str) -> str:
        """Close/kill an application by name. Uses subprocess list args (no shell)."""
        name_lower = app_name.lower().strip()

        # Resolve via aliases
        candidates = self._PROCESS_ALIASES.get(name_lower, [name_lower])

        # Try each candidate
        for proc_name in candidates:
            # Check if process exists (shell=False, list args)
            try:
                check = subprocess.run(
                    ["pgrep", "-f", proc_name],
                    capture_output=True, text=True, timeout=5,
                )
                if check.returncode == 0 and check.stdout.strip():
                    subprocess.run(
                        ["pkill", "-f", proc_name],
                        capture_output=True, text=True, timeout=5,
                    )
                    return f"✅ Приложение «{app_name}» завершено (pkill -f {proc_name})"
            except Exception as e:
                logger.debug("pkill error for %s: %s", proc_name, e)

        # Nothing found
        return f"⚠ Процесс «{app_name}» не найден среди запущенных."

    # ── Smart Workflows (BT, WiFi, Weather) ───────────────────

    def _try_smart_workflow(self, text: str) -> Optional[str]:
        """Try smart multi-step workflows: BT, WiFi, weather."""
        try:
            from lina.core.smart_workflows import (
                BT_CONNECT_PATTERN, BT_DISCONNECT_PATTERN,
                WIFI_CONNECT_PATTERN, WEATHER_PATTERN,
                bluetooth_connect, bluetooth_disconnect,
                wifi_connect, get_weather,
            )
        except ImportError:
            return None

        # Bluetooth connect: «подключи Buds Pro», «соедини с наушниками»
        m = BT_CONNECT_PATTERN.match(text.strip())
        if m:
            device = m.group(1).strip()
            # Filter out false positives: if device looks like wifi SSID request
            if device and not re.match(r'(?:wifi|wi-fi|вай-?фай)', device, re.I):
                return bluetooth_connect(device)

        # Bluetooth disconnect: «отключи наушники»
        m = BT_DISCONNECT_PATTERN.match(text.strip())
        if m:
            device = m.group(1).strip()
            # Don't match generic "wifi"/"вайфай" etc.
            if device and not re.match(r'(?:wifi|wi-fi|вай-?фай|звук)', device, re.I):
                return bluetooth_disconnect(device)

        # WiFi connect: «подключись к wifi MyNet», «подключи вайфай Home пароль 123»
        m = WIFI_CONNECT_PATTERN.match(text.strip())
        if m:
            ssid = m.group(1).strip()
            password = m.group(2).strip() if m.group(2) else None
            if ssid:
                return wifi_connect(ssid, password)

        # Weather: «погода», «погода в Москве»
        m = WEATHER_PATTERN.match(text.strip())
        if m:
            city = (m.group(1) or "").strip() or "Москва"
            return get_weather(city)

        return None

    # ── Smart System Queries (domain modules, no LLM needed) ──

    # Patterns for queries that domain modules can answer directly
    _SYS_QUERY_PATTERNS = {
        "failed_services": re.compile(
            r"(упавш|fail|сломан|неработающ|проблемн)\w*\s*(сервис|служб|юнит|демон)"
            r"|(сервис|служб|юнит|демон)\w*\s*(упал|упавш|fail|сломан|не работа)",
            re.I,
        ),
        "updates": re.compile(
            r"(доступн|есть)\w*\s*(обновлен|апдейт|update)"
            r"|(обновлен|апдейт|update)\w*\s*(доступн|есть|проверь|проверить|check)",
            re.I,
        ),
        "audio_diag": re.compile(
            r"(нет|пропал|не работа)\w*\s*(звук|аудио)"
            r"|(звук|аудио)\w*\s*(нет|пропал|не работа|сломал)",
            re.I,
        ),
        "net_diag": re.compile(
            r"(нет|пропал|не работа)\w*\s*(интернет|сеть|wifi|вайфай)"
            r"|(интернет|сеть|wifi|вайфай)\w*\s*(нет|пропал|не работа)",
            re.I,
        ),
        "hw_summary": re.compile(
            r"(обзор|сводк|полн\w*\s*инфо|характеристик|конфигурац)\w*\s*(систем|железо|комп|hardware|hw)"
            r"|(систем|железо|комп|hardware|hw)\w*\s*(обзор|сводк|инфо|характеристик)"
            r"|(расскажи|покажи|скажи)\w*\s*.{0,10}\b(систем|компьютер|желез)\w*"
            r"|\b(моя|мой|моё|моей)\s+(систем|компьютер|\S*машин)\w*"
            r"|\bчто\s+за\s+(систем|компьютер|pc|\S*машин)\w*",
            re.I,
        ),
        "display_info": re.compile(
            r"(инфо|данн|параметр|характер)\w*\s*(экран|монитор|дисплей|gpu|видеокарт)"
            r"|(экран|монитор|дисплей|gpu|видеокарт)\w*\s*(инфо|данн|параметр|характер|что|какой|какая)",
            re.I,
        ),
    }

    def _try_system_query(self, text: str) -> Optional[str]:
        """Try to answer system queries using domain modules directly.

        Returns instant answers for:
          - Failed services (ServiceManager)
          - Available updates (PackageManager)
          - Audio diagnostics (AudioManager)
          - Network diagnostics (NetworkDiagnostics)
          - Hardware summary (HardwareInfo)
          - Display/GPU info (DisplayManager)
        """
        lower = text.lower().strip()

        # ── Проблемные сервисы ──
        if self._SYS_QUERY_PATTERNS["failed_services"].search(lower):
            try:
                from lina.system.service_manager import ServiceManager
                sm = ServiceManager()
                failed = sm.list_services(state="failed")
                if not failed:
                    return "✅ Все systemd-сервисы работают нормально."
                lines = [f"⚠ Найдено {len(failed)} проблемных сервисов:\n"]
                for svc in failed[:10]:
                    name = svc.get("name", "?")
                    desc = svc.get("description", "")
                    lines.append(f"  ✗ {name}" + (f" — {desc}" if desc else ""))
                lines.append("\nДля подробностей: systemctl status <имя>.service")
                return "\n".join(lines)
            except Exception as e:
                logger.debug("ServiceManager direct query failed: %s", e)

        # ── Доступные обновления ──
        if self._SYS_QUERY_PATTERNS["updates"].search(lower):
            try:
                from lina.system.package_manager import PackageManager
                pm = PackageManager()
                updates = pm.check_updates()
                if not updates:
                    return "✅ Система актуальна — обновлений нет."
                if isinstance(updates, list):
                    count = len(updates)
                    lines = [f"📦 Доступно обновлений: {count}\n"]
                    for pkg in updates[:15]:
                        if isinstance(pkg, dict):
                            lines.append(
                                f"  • {pkg.get('name', '?')} "
                                f"{pkg.get('old', '')} → {pkg.get('new', '')}")
                        else:
                            lines.append(f"  • {pkg}")
                    if count > 15:
                        lines.append(f"  ... и ещё {count - 15}")
                    lines.append(f"\nОбновить: {pm.update()}")
                    return "\n".join(lines)
                return f"📦 Есть обновления. Обновить: {pm.update()}"
            except Exception as e:
                logger.debug("PackageManager direct query failed: %s", e)

        # ── Диагностика аудио ──
        if self._SYS_QUERY_PATTERNS["audio_diag"].search(lower):
            try:
                from lina.system.audio_manager import AudioManager
                am = AudioManager()
                return am.format_diagnosis()
            except Exception as e:
                logger.debug("AudioManager direct query failed: %s", e)

        # ── Диагностика сети ──
        if self._SYS_QUERY_PATTERNS["net_diag"].search(lower):
            try:
                from lina.system.network_manager import NetworkDiagnostics
                nd = NetworkDiagnostics()
                report = nd.diagnose_no_internet()
                if isinstance(report, str):
                    return report
                if isinstance(report, dict):
                    lines = ["═══ Диагностика сети ═══"]
                    for step, result in report.items():
                        ok = result.get("ok", False) if isinstance(result, dict) else bool(result)
                        marker = "✓" if ok else "✗"
                        desc = result.get("detail", str(result)) if isinstance(result, dict) else str(result)
                        lines.append(f"  {marker} {step}: {desc}")
                    return "\n".join(lines)
            except Exception as e:
                logger.debug("NetworkDiagnostics direct query failed: %s", e)

        # ── Обзор системы (hardware) ──
        if self._SYS_QUERY_PATTERNS["hw_summary"].search(lower):
            try:
                from lina.system.hardware_info import HardwareInfo
                hw = HardwareInfo()
                return hw.format_summary()
            except Exception as e:
                logger.debug("HardwareInfo direct query failed: %s", e)

        # ── Информация о дисплее/GPU ──
        # Skip if query is about a specific product/brand (web search territory)
        _PRODUCT_BRANDS = re.compile(
            r"(gainward|palit|msi|asus|gigabyte|evga|zotac|sapphire|xfx|pny|inno3d"
            r"|rtx\s*\d{4}|gtx\s*\d{4}|rx\s*\d{4}|arc\s*[ab]\d{3}"
            r"|geforce|radeon|intel\s*arc|quadro|tesla"
            r"|купить|цена|обзор|сравн|benchmark|тест\w*\s*в\s*игр"
            r"|найди|поиск|поищи|загугли|найти)",
            re.I,
        )
        if self._SYS_QUERY_PATTERNS["display_info"].search(lower) and not _PRODUCT_BRANDS.search(lower):
            try:
                from lina.system.display_manager import get_display_summary_text
                return get_display_summary_text()
            except Exception as e:
                logger.debug("DisplayManager direct query failed: %s", e)

        return None

    def enrich_for_llm(self, query: str) -> str:
        """
        Обогащает запрос реальными системными данными для LLM.

        Использует domain-модули из lina/system/ для сбора данных,
        а не сырые subprocess-вызовы. Каждый модуль кэширует результат.
        """
        normalized = _normalize_query(query)
        context_parts = []

        # Всегда добавляем базовую инфо
        context_parts.append(
            f"[Система: {self._snapshot.distro}, "
            f"ядро {self._snapshot.kernel}, "
            f"DE: {self._snapshot.de or 'нет'}, "
            f"display: {self._snapshot.display_server}]"
        )

        # ── Запрос про железо/RAM/CPU/диск → SystemDiagnostics ──
        if any(w in normalized for w in [
            "диск", "место", "память", "ram", "cpu", "процессор",
            "температур", "нагрев", "оперативк", "озу", "swap",
            "свап", "ssd", "hdd", "здоровье",
        ]):
            try:
                from lina.system.diagnostics import (
                    get_system_summary, get_disk_usage, get_memory_pressure,
                    get_cpu_load_analysis,
                )
                summary = get_system_summary()
                parts = []
                if summary.get("ram"):
                    r = summary["ram"]
                    parts.append(f"RAM: {r.get('used_h', '?')}/{r.get('total_h', '?')} "
                                 f"(свободно: {r.get('available_h', '?')})")
                if summary.get("swap"):
                    s = summary["swap"]
                    parts.append(f"Swap: {s.get('used_h', '?')}/{s.get('total_h', '?')}")
                if summary.get("cpu"):
                    c = summary["cpu"]
                    parts.append(f"CPU: {c.get('model', '?')}, "
                                 f"load avg: {c.get('load_avg', '?')}, "
                                 f"частота: {c.get('freq_mhz', '?')} MHz")

                disk = get_disk_usage()
                if disk:
                    disk_lines = []
                    for d in disk[:5]:
                        disk_lines.append(
                            f"  {d.get('mount', '?')}: "
                            f"{d.get('used_h', '?')}/{d.get('size_h', '?')} "
                            f"({d.get('use_pct', '?')})")
                    parts.append("Диски:\n" + "\n".join(disk_lines))

                if parts:
                    context_parts.append("[Системные данные]\n" + "\n".join(parts))
            except Exception as e:
                logger.debug("diagnostics enrich error: %s", e)
                # Fallback to raw subprocess
                try:
                    out = subprocess.run(
                        "free -h && echo '---' && df -h /",
                        shell=True, capture_output=True, text=True, timeout=5
                    ).stdout.strip()
                    context_parts.append(f"[Актуальные данные:\n{out}]")
                except Exception:
                    pass

        # ── Запрос про сеть → NetworkDiagnostics ──
        if any(w in normalized for w in [
            "сеть", "ip", "wifi", "интернет", "dns", "vpn",
            "вайфай", "пинг", "порт", "подключени", "роутер",
        ]):
            try:
                from lina.system.network_manager import NetworkDiagnostics
                nd = NetworkDiagnostics()
                parts = []
                ifaces = nd.get_interfaces()
                if ifaces:
                    iface_lines = []
                    for iface in ifaces[:5]:
                        iface_lines.append(
                            f"  {iface.get('name', '?')}: {iface.get('state', '?')} "
                            f"IP={iface.get('ipv4', 'нет')}")
                    parts.append("Интерфейсы:\n" + "\n".join(iface_lines))

                conns = nd.get_active_connections()
                if conns:
                    parts.append(f"Активные подключения: {len(conns)}")

                if "wifi" in normalized or "вайфай" in normalized:
                    signal = nd.get_wifi_signal()
                    if signal:
                        parts.append(f"WiFi сигнал: {signal}")

                if parts:
                    context_parts.append("[Сеть]\n" + "\n".join(parts))
            except Exception as e:
                logger.debug("network enrich error: %s", e)
                try:
                    out = subprocess.run(
                        "ip -brief addr 2>/dev/null | head -5",
                        shell=True, capture_output=True, text=True, timeout=5
                    ).stdout.strip()
                    context_parts.append(f"[Сеть:\n{out}]")
                except Exception:
                    pass

        # ── Запрос про процессы/нагрузку → SystemDiagnostics ──
        if any(w in normalized for w in [
            "процесс", "нагрузк", "тормоз", "зависл", "load",
            "медленн", "тупит", "лагает", "top",
        ]):
            try:
                from lina.system.diagnostics import get_cpu_load_analysis
                load = get_cpu_load_analysis()
                if load:
                    parts = []
                    parts.append(f"Load avg: {load.get('load_avg', '?')}")
                    top_procs = load.get("top_cpu", [])
                    if top_procs:
                        proc_lines = []
                        for p in top_procs[:5]:
                            proc_lines.append(
                                f"  {p.get('name', '?')}: CPU={p.get('cpu', '?')}% "
                                f"MEM={p.get('mem', '?')}%")
                        parts.append("Топ процессов:\n" + "\n".join(proc_lines))
                    context_parts.append("[Процессы]\n" + "\n".join(parts))
            except Exception as e:
                logger.debug("cpu load enrich error: %s", e)
                try:
                    out = subprocess.run(
                        "ps aux --sort=-%cpu | head -8",
                        shell=True, capture_output=True, text=True, timeout=5
                    ).stdout.strip()
                    context_parts.append(f"[Процессы:\n{out}]")
                except Exception:
                    pass

        # ── Запрос про экран/монитор/яркость → DisplayManager ──
        # Skip GPU enrichment for product/purchase queries (web search territory)
        _display_keywords = [
            "яркость", "экран", "монитор", "дисплей", "разрешен",
            "частота", "герц", "hz", "refresh", "gpu", "видеокарт",
        ]
        _is_product_query = bool(re.search(
            r"(gainward|palit|msi|asus|gigabyte|evga|zotac|sapphire|xfx|pny|inno3d"
            r"|rtx\s*\d{4}|gtx\s*\d{4}|rx\s*\d{4}|arc\s*[ab]\d{3}"
            r"|geforce|radeon|intel\s*arc|quadro|tesla"
            r"|купить|цена|обзор|сравн|benchmark"
            r"|найди|поиск|поищи|загугли|найти)",
            normalized, re.I,
        ))
        if any(w in normalized for w in _display_keywords) and not _is_product_query:
            try:
                from lina.system.display_manager import (
                    get_display_summary, detect_display_server,
                )
                summary = get_display_summary()
                parts = []
                if summary.compositor:
                    parts.append(
                        f"Дисплей: {summary.compositor.server.value}, "
                        f"композитор: {summary.compositor.name}")
                for m in summary.monitors[:4]:
                    parts.append(
                        f"  {m.name}: {m.resolution} @ {m.refresh_rate}Hz")
                if summary.gpus:
                    for g in summary.gpus[:2]:
                        parts.append(
                            f"  GPU: {g.name} ({g.driver_type.value})"
                            + (f" t={g.temperature}°C" if g.temperature else ""))
                if summary.issues:
                    for iss in summary.issues[:3]:
                        parts.append(f"  ⚠ {iss.description}")
                if parts:
                    context_parts.append("[Дисплей]\n" + "\n".join(parts))
            except Exception as e:
                logger.debug("display enrich error: %s", e)
                try:
                    out = subprocess.run(
                        "brightnessctl 2>/dev/null || echo 'brightnessctl не установлен'",
                        shell=True, capture_output=True, text=True, timeout=5
                    ).stdout.strip()
                    context_parts.append(f"[Яркость:\n{out}]")
                except Exception:
                    pass

        # ── Запрос про звук/аудио → AudioManager ──
        if any(w in normalized for w in [
            "звук", "громкость", "аудио", "audio", "volume", "mute",
            "замьючен", "наушник", "микрофон", "динамик", "pipewire",
            "pulseaudio", "колонк",
        ]):
            try:
                from lina.system.audio_manager import AudioManager
                am = AudioManager()
                status = am.format_status()
                if status:
                    context_parts.append(f"[Аудио]\n{status}")
            except Exception as e:
                logger.debug("audio enrich error: %s", e)
                try:
                    out = subprocess.run(
                        "pactl get-sink-volume @DEFAULT_SINK@ 2>/dev/null || echo 'pactl недоступен'",
                        shell=True, capture_output=True, text=True, timeout=5
                    ).stdout.strip()
                    context_parts.append(f"[Аудио:\n{out}]")
                except Exception:
                    pass

        # ── Запрос про пакеты/обновления → PackageManager ──
        if any(w in normalized for w in [
            "пакет", "обновлен", "установк", "удалени", "pacman",
            "apt", "dnf", "flatpak", "snap", "yay", "paru",
            "обнови", "апдейт", "update",
        ]):
            try:
                from lina.system.package_manager import PackageManager
                pm = PackageManager()
                updates = pm.check_updates()
                if updates:
                    count = len(updates) if isinstance(updates, list) else 0
                    context_parts.append(
                        f"[Пакеты: {pm._distro_id}, "
                        f"доступно обновлений: {count}]")
                else:
                    context_parts.append(
                        f"[Пакеты: {pm._distro_id}, актуально]")
            except Exception as e:
                logger.debug("package enrich error: %s", e)

        # ── Запрос про сервисы/systemd → ServiceManager ──
        if any(w in normalized for w in [
            "сервис", "служб", "systemctl", "systemd", "демон",
            "юнит", "упал", "failed", "restart", "запуст", "стартуй",
        ]):
            try:
                from lina.system.service_manager import ServiceManager
                sm = ServiceManager()
                # Показать проблемные сервисы
                failed = sm.list_services(state="failed")
                if failed:
                    fail_names = [s.get("name", "?") for s in failed[:5]]
                    context_parts.append(
                        f"[Systemd: {len(failed)} failed сервисов: "
                        f"{', '.join(fail_names)}]")
                else:
                    context_parts.append("[Systemd: все сервисы ОК]")
            except Exception as e:
                logger.debug("service enrich error: %s", e)

        # ── Запрос про bluetooth ──
        if any(w in normalized for w in [
            "bluetooth", "блютуз", "блутус", "bt ", "наушник",
        ]):
            try:
                from lina.system.diagnostics import get_bluetooth_status
                bt = get_bluetooth_status()
                if bt:
                    context_parts.append(f"[Bluetooth]\n{bt}")
            except Exception as e:
                logger.debug("bluetooth enrich error: %s", e)

        return "\n".join(context_parts)

    def _run_safe(self, cmd: str | list) -> Optional[str]:
        """Выполнить команду безопасно. None при критической ошибке."""
        try:
            import shlex as _shlex
            args = cmd if isinstance(cmd, list) else _shlex.split(cmd)
            proc = subprocess.run(
                args, shell=False, capture_output=True, text=True,
                timeout=15, env={**os.environ, "LANG": "C.UTF-8"},
            )
            output = proc.stdout.strip()
            if proc.returncode != 0 and proc.stderr:
                output = output + "\n" + proc.stderr.strip() if output else proc.stderr.strip()
            return output
        except subprocess.TimeoutExpired:
            return "⏰ Таймаут выполнения команды"
        except Exception as e:
            logger.error("Command failed: %s — %s", cmd, e)
            return None
