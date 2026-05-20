# -*- coding: utf-8 -*-
"""
Lina GUI — autonomous install workflow.

Единая state-machine для запросов «установи X», «как установить X».

Получив имя приложения, workflow САМ:
  1. PROBE — уже установлено? → done.
  2. RESOLVE — найти точное имя пакета (pacman -Ss / yay -Ss).
  3. INSTALL — выполнить установку через embedded terminal (видимо).
  4. VERIFY — проверить что бинарь работает.
  5. DIAGNOSE — при падении: классифицировать → автофикс → retry.

Без LLM в горячем пути. LLM зовём только в DIAGNOSE для unknown ошибок.

Спрашиваем пользователя ТОЛЬКО:
  • при удалении файлов (rm — в DIAGNOSE для db.lck);
  • при sudo-пароле (это делает PTY автоматически).

Остальное — молча и автономно.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger("lina.gui.install_workflow")


# ─── Состояния ─────────────────────────────────────────────────────────────

class WorkflowState(Enum):
    INIT = "init"
    PROBE = "probe"
    RESOLVE = "resolve"
    INSTALL = "install"
    DIAGNOSE = "diagnose"
    VERIFY = "verify"
    DONE = "done"
    FAILED = "failed"


# ─── Карточка прогресса ────────────────────────────────────────────────────

@dataclass
class StepLog:
    """Одна строка в карточке прогресса."""
    icon: str           # "✓", "⏳", "⚠", "❌", "🔧", "📦"
    text: str           # человеко-читаемое описание

    def render(self) -> str:
        return f"{self.icon} {self.text}"


@dataclass
class ProgressCard:
    """Карточка установки в чате — обновляется по мере шагов."""
    target: str
    steps: List[StepLog] = field(default_factory=list)
    status: str = "running"   # running / success / failed

    def append(self, icon: str, text: str) -> None:
        self.steps.append(StepLog(icon, text))

    def render(self) -> str:
        header_icon = {
            "running": "🚀",
            "success": "✅",
            "failed": "❌",
        }.get(self.status, "🚀")
        header = f"{header_icon} Установка: {self.target}"

        if not self.steps:
            return header

        lines = [header]
        for i, step in enumerate(self.steps):
            prefix = "└─" if i == len(self.steps) - 1 else "├─"
            lines.append(f"{prefix} {step.render()}")
        return "\n".join(lines)


# ─── Детекторы ошибок ──────────────────────────────────────────────────────

class ErrorClass(Enum):
    NONE = "none"
    DB_LOCK = "db_lock"
    NETWORK = "network"
    NOT_FOUND = "not_found"
    PERMISSION = "permission"
    SIGNATURE = "signature"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


def classify_pacman_error(stderr: str) -> ErrorClass:
    """Классификация вывода pacman/yay по знакомым паттернам."""
    if not stderr:
        return ErrorClass.NONE
    s = stderr.lower()
    if "unable to lock database" in s or "could not lock database" in s:
        return ErrorClass.DB_LOCK
    if any(k in s for k in (
        "socks5", "rejected by the socks", "could not resolve host",
        "name or service not known", "network is unreachable",
        "no route to host", "connection refused", "connection timed out",
        "failed to retrieve", "could not download",
        "host is unreachable",
        # Дополнительные паттерны из реальных логов:
        "failed retrieving file",      # «failed retrieving file 'X' from mirror»
        "errors occurred, no packages were upgraded",
        "failed to commit transaction (failed to retrieve",
        "warning: failed to retrieve some files",
        "ssl",                          # SSL handshake/cert errors
        "operation timed out",
        "user was rejected",            # SOCKS rejection
        "too many errors from",         # pacman: «too many errors from mirror»
    )):
        return ErrorClass.NETWORK
    if "target not found" in s or "не удалось найти цель" in s:
        return ErrorClass.NOT_FOUND
    if any(k in s for k in (
        "permission denied", "operation not permitted",
        "must be root", "you cannot perform this operation",
    )):
        return ErrorClass.PERMISSION
    if any(k in s for k in (
        "invalid or corrupted package", "signature is unknown trust",
        "signature from", "marginal trust",
    )):
        return ErrorClass.SIGNATURE
    if "conflicting files" in s or "exists in filesystem" in s:
        return ErrorClass.CONFLICT
    return ErrorClass.UNKNOWN


# ─── Низкоуровневые helpers ────────────────────────────────────────────────

# Русские/жаргонные алиасы → реальное имя пакета (или несколько вариантов).
# Workflow проверит каждый вариант последовательно.
_TARGET_ALIASES: dict[str, list[str]] = {
    # Telegram
    "телеграм":       ["telegram-desktop", "telegram"],
    "телеграмм":      ["telegram-desktop", "telegram"],
    "телега":         ["telegram-desktop", "telegram"],
    "telegram":       ["telegram-desktop", "telegram"],
    # Discord
    "дискорд":        ["discord"],
    "discord":        ["discord"],
    # Browsers
    "хром":           ["google-chrome", "chromium"],
    "хроме":          ["google-chrome", "chromium"],
    "google chrome":  ["google-chrome"],
    "chrome":         ["google-chrome", "chromium"],
    "хромиум":        ["chromium"],
    "файрфокс":       ["firefox"],
    "firefox":        ["firefox"],
    "опера":          ["opera"],
    "opera":          ["opera"],
    "edge":           ["microsoft-edge-stable-bin"],
    "microsoft edge": ["microsoft-edge-stable-bin"],
    "браузер тор":    ["torbrowser-launcher"],
    "tor browser":    ["torbrowser-launcher"],
    # Office / dev
    "vscode":         ["code", "visual-studio-code-bin"],
    "вс код":         ["code", "visual-studio-code-bin"],
    "vs code":        ["code", "visual-studio-code-bin"],
    "обсидиан":       ["obsidian"],
    "obsidian":       ["obsidian"],
    "блокнот":        ["geany", "gedit"],
    "либре офис":     ["libreoffice-fresh", "libreoffice-still"],
    "libreoffice":    ["libreoffice-fresh", "libreoffice-still"],
    # Media
    "обс":            ["obs-studio"],
    "obs":            ["obs-studio"],
    "vlc":            ["vlc"],
    "вл си":          ["vlc"],
    "спотифай":       ["spotify"],
    "spotify":        ["spotify"],
    # Graphics
    "гимп":           ["gimp"],
    "gimp":           ["gimp"],
    "инкскейп":       ["inkscape"],
    "inkscape":       ["inkscape"],
    "блендер":        ["blender"],
    "blender":        ["blender"],
    # Comms
    "зум":            ["zoom"],
    "zoom":           ["zoom"],
    "скайп":          ["skypeforlinux-stable-bin", "skypeforlinux-bin"],
    "skype":          ["skypeforlinux-stable-bin", "skypeforlinux discounted-bin"],
    # AI / dev tooling
    "клод":           ["claude-code"],
    "claude":         ["claude-code"],
    "claude code":    ["claude-code"],
    # Gaming
    "стим":           ["steam"],
    "steam":          ["steam"],
    "лутрис":         ["lutris"],
    "lutris":         ["lutris"],
    "вайн":           ["wine"],
    "wine":           ["wine"],
}


def _resolve_aliases(target: str) -> list[str]:
    """Вернуть список возможных имён пакета для русского/жаргонного target.

    Если target уже похож на имя пакета (английское) — возвращаем [target].
    Иначе ищем в _TARGET_ALIASES (точное совпадение и подстрока).
    """
    t = (target or "").strip().lower()
    if not t:
        return []
    # Точное совпадение
    if t in _TARGET_ALIASES:
        return _TARGET_ALIASES[t]
    # Подстрочный матч (для «гимп редактор» → ищем «гимп»)
    for alias, pkgs in _TARGET_ALIASES.items():
        if alias in t or t in alias:
            return pkgs
    # Для коротких ascii-имён — сам target как первый кандидат
    if t.isascii() and len(t) >= 3:
        return [t]
    return [t]


def _run(cmd: list[str], timeout: int = 10) -> Tuple[int, str, str]:
    """Synchronous run with stdout/stderr capture. Returns (code, out, err)."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env={"LANG": "C.UTF-8", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
        )
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except Exception as e:
        return -2, "", str(e)


def _is_pacman_running() -> bool:
    """Есть ли активный pacman-процесс кроме нас?"""
    code, out, _ = _run(["pgrep", "-x", "pacman"], timeout=3)
    return code == 0 and bool(out.strip())


def _has_yay() -> bool:
    return shutil.which("yay") is not None


# ─── Публичный API workflow ───────────────────────────────────────────────

@dataclass
class InstallResult:
    """Финальный результат workflow."""
    success: bool
    target: str
    package: str = ""
    version: str = ""
    binary: str = ""
    reason: str = ""
    card: Optional[ProgressCard] = None


class InstallWorkflow:
    """Stateful автономный install workflow.

    Использование:
        wf = InstallWorkflow(
            target="telegram",
            terminal=embedded_terminal,
            on_card_update=lambda card_text: ...,
            on_done=lambda result: ...,
        )
        wf.start()

    Контракты:
        • terminal — объект EmbeddedTerminal с методами `run_command(cmd)`
          и сигналом `command_finished(int, str, str)` (exit_code, command, output).
        • on_card_update вызывается на каждом обновлении состояния. Аргумент —
          markdown-текст карточки.
        • on_done вызывается ровно один раз в конце.

    Все шаги выполняются в Qt event-loop через коннекты сигналов терминала.
    Никаких QThread'ов внутри — терминал и так живёт в main thread.
    """

    MAX_RETRIES = 3
    # Сколько ждём `command_finished` после run_command(). Защищает от
    # зависания PTY-процесса (например, sudo ждёт пароль, а пользователь
    # ничего не вводит). Внутри install сама команда обёрнута в
    # `timeout 120` — этот safety-net срабатывает только если что-то
    # совсем пошло не по плану.
    STEP_TIMEOUT_MS = 150_000  # 2.5 минуты — чуть больше чем timeout(120)
    # Hard cap внутри install-команды через `timeout(1)`. Защищает от
    # бесконечной mirror-загрузки за SOCKS5/proxy.
    INSTALL_HARD_TIMEOUT_S = 120

    def __init__(
        self,
        target: str,
        terminal,
        on_card_update: Callable[[str], None],
        on_done: Callable[[InstallResult], None],
        on_confirm_request: Optional[Callable[[str, str], bool]] = None,
        on_password_request: Optional[Callable[[str], Optional[str]]] = None,
    ):
        self.target = target.strip().lower()
        self.terminal = terminal
        self.on_card_update = on_card_update
        self.on_done = on_done
        # on_confirm_request(title, message) → bool (yes/no).
        # Используется ТОЛЬКО для удалений. Если None — удаления запрещены.
        self.on_confirm_request = on_confirm_request
        # on_password_request(reason) → Optional[str].
        # Спрашиваем sudo-пароль ОДИН РАЗ перед первой sudo-командой.
        # Кэшируем в self._sudo_password на время workflow.
        self.on_password_request = on_password_request
        self._sudo_password: Optional[str] = None

        self.state = WorkflowState.INIT
        self.card = ProgressCard(target=target)
        self.package_name: str = ""
        self.binary_name: str = ""
        self.use_aur: bool = False
        self.attempts: int = 0
        self._terminal_handler_connected = False
        # Что делать когда придёт следующий command_finished:
        #   None  — игнорировать (мы ничего не запускали).
        #   "install" — это ответ на основную установку → diagnose/verify.
        #   "fixup"   — это ответ на авто-фикс → перейти в self._fixup_then.
        self._pending_step: Optional[str] = None
        self._fixup_then: Optional[Callable[[], None]] = None
        # Safety-net таймер: если PTY-процесс зависнет на password-prompt
        # и не пришлёт command_finished, через STEP_TIMEOUT_MS мы сами
        # завершим шаг как ошибку.
        self._step_timer = None  # QTimer (или None)
        # Askpass: пароль через временный исполняемый скрипт.
        # См. _setup_askpass / _build_sudo_env.
        self._askpass_path: Optional[str] = None
        self._askpass_dir: Optional[str] = None
        self._askpass_pw: Optional[str] = None
        # Sudo keepalive не нужен с askpass-моделью — каждый sudo
        # самостоятельно получает пароль через скрипт. Поле оставлено
        # для обратной совместимости (всегда None).
        self._sudo_keepalive_timer = None

    # ─── Public ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Запускает workflow. Возвращает сразу — финал придёт через колбэк."""
        try:
            self._step_probe()
        except Exception as e:
            logger.error("Workflow start error: %s", e, exc_info=True)
            self._fail(f"внутренняя ошибка: {e}")

    def __del__(self):
        """Last-resort cleanup. Если workflow GC'нулся без cancel/done/fail
        (что не должно происходить, но защищаемся) — стираем askpass-файл,
        чтобы пароль не остался в /run/user.
        """
        try:
            self._cleanup_askpass()
        except Exception:
            pass

    def cancel(self) -> None:
        """Прервать workflow (например, пользователь закрыл окно или
        стартует новый install). Гарантирует:
          • остановку зависшего терминала (sudo rm/pacman ждёт пароль);
          • отписку от сигналов (никаких побочных эффектов после cancel);
          • сброс pending state.
        """
        logger.info("Workflow cancel requested (state=%s)", self.state.value)
        # 1. Снять таймауты.
        self._cancel_step_timeout()
        self._stop_sudo_keepalive()
        # 2. Чистим askpass — пароль не должен жить дольше workflow.
        self._cleanup_askpass()
        # 2. Отписаться от терминала ПЕРЕД tеrminal.stop(),
        # иначе синтетический command_finished от stop() прилетит сюда
        # и пойдёт в diagnose.
        self._disconnect_terminal()
        # 3. Killнуть зависший процесс в терминале.
        try:
            if hasattr(self.terminal, "stop"):
                self.terminal.stop()
        except Exception as e:
            logger.debug("terminal.stop on cancel failed: %s", e)
        # 4. Сброс state.
        self._pending_step = None
        self._fixup_then = None
        self._sudo_password = None
        if self.state not in (WorkflowState.DONE, WorkflowState.FAILED):
            self.state = WorkflowState.FAILED
            self.card.status = "failed"
            self.card.append("⚠", "Прервано пользователем.")
            self._update_card()

    # ─── Шаги ───────────────────────────────────────────────────────────

    def _step_probe(self) -> None:
        self.state = WorkflowState.PROBE
        self.card.append("⏳", f"Проверяю, установлен ли «{self.target}»…")
        self._update_card()

        # Проверяем все кандидаты-имена пакетов и общие aliases для бинаря.
        # Для «телеграм» проверим: telegram-desktop (пакет), telegram (бинарь),
        # telegram-desktop (бинарь).
        package_candidates = _resolve_aliases(self.target)
        bin_candidates = list(package_candidates) + \
            self._common_binary_aliases(self.target)
        # Дедуп с сохранением порядка
        seen = set()
        bin_candidates = [
            x for x in bin_candidates
            if x and not (x in seen or seen.add(x))
        ]

        for cand in bin_candidates:
            code, out, _ = _run(["which", cand], timeout=3)
            if code == 0 and out.strip():
                ver = self._safe_version(cand)
                ver_part = f" ({ver})" if ver else ""
                self.card.steps[-1] = StepLog(
                    "✓", f"Уже установлено: `{cand}`{ver_part}",
                )
                self.binary_name = cand
                # Найдём имя пакета через which → pacman -Qo (если возможно)
                code2, out2, _ = _run(["pacman", "-Qo", out.strip()], timeout=3)
                if code2 == 0:
                    m = re.search(r"is owned by\s+(\S+)\s+(\S+)", out2)
                    if m:
                        self.package_name = m.group(1)
                self.card.status = "success"
                self._update_card()
                self._done(InstallResult(
                    success=True, target=self.target,
                    package=self.package_name or self.target,
                    version=ver, binary=cand,
                    reason="already installed", card=self.card,
                ))
                return

        # Не нашли — переходим к resolve
        self.card.steps[-1] = StepLog("✓", "Не установлено, ищу пакет…")
        self._update_card()
        self._step_resolve()

    def _step_resolve(self) -> None:
        self.state = WorkflowState.RESOLVE

        # 1. Применяем алиасы — для «телеграм» получаем
        # ["telegram-desktop", "telegram"] и пробуем по порядку.
        candidates = _resolve_aliases(self.target)
        logger.info("Install resolve: target='%s' candidates=%s",
                    self.target, candidates)

        try:
            from lina.system.package_manager import PackageManager
            pm = PackageManager()
        except Exception as e:
            logger.error("PackageManager init failed: %s", e)
            pm = None

        # 2. Pacman: пробуем сначала точные имена из алиасов,
        # потом обычный поиск по target.
        if pm is not None:
            # Прямой info() для имён-кандидатов — быстрее чем search()
            for cand in candidates:
                code, out, _ = _run(["pacman", "-Si", cand], timeout=5)
                if code == 0 and out.strip():
                    self.package_name = cand
                    self.use_aur = False
                    # Извлекаем версию из вывода
                    ver = ""
                    m = re.search(r"^Version\s*:\s*(\S+)", out, re.MULTILINE)
                    if m:
                        ver = m.group(1)
                    ver_part = f" {ver}" if ver else ""
                    self.card.append(
                        "✓",
                        f"Найден пакет: `{self.package_name}`{ver_part} "
                        f"(репозиторий)",
                    )
                    self._update_card()
                    self._step_install()
                    return

            # Если по точным именам не нашли — обычный search по исходному target
            try:
                results = pm.search(self.target, limit=10)
            except Exception as e:
                logger.error("PackageManager search failed: %s", e)
                results = []

            best = self._pick_best_package(results)
            if best:
                self.package_name = best.get("name", "")
                self.use_aur = False
                ver = best.get("version", "")
                ver_part = f" {ver}" if ver else ""
                self.card.append(
                    "✓",
                    f"Найден пакет: `{self.package_name}`{ver_part} "
                    f"(репозиторий)",
                )
                self._update_card()
                self._step_install()
                return

        # 3. AUR — пробуем все имена-кандидаты через `yay -Ss`
        if _has_yay():
            for cand in candidates + [self.target]:
                code, out, _ = _run(
                    ["yay", "-Ss", "^" + re.escape(cand) + "$"], timeout=15,
                )
                if code == 0 and out.strip():
                    m = re.search(r"^[\w-]+/(\S+)\s+(\S+)", out, re.MULTILINE)
                    if m:
                        self.package_name = m.group(1)
                        self.use_aur = True
                        ver = m.group(2)
                        self.card.append(
                            "✓",
                            f"Найден в AUR: `{self.package_name}` {ver}",
                        )
                        self._update_card()
                        self._step_install()
                        return

        # 4. Ничего не нашли
        tried = ", ".join(f"`{c}`" for c in candidates)
        self.card.append(
            "❌",
            f"Не нашлось пакета для «{self.target}» "
            f"(пробовала: {tried}).",
        )
        self._fail("пакет не найден")

    def _step_install(self, prefix_cmd: str = "") -> None:
        """Запустить установку. Если задан prefix_cmd — выполнить его
        в той же sudo-сессии (`sudo bash -c 'fix && install'`), чтобы
        пользователь вводил пароль один раз и не было риска зависнуть
        между двумя независимыми sudo-вызовами.

        Архитектура аутентификации:
          • Sudo-пароль валидируется ОДИН РАЗ через `sudo -v -S` в
            обычном subprocess (с stdin-pipe). Это обновляет sudo
            timestamp без запуска ничего реального.
          • Все последующие sudo-вызовы используют `sudo -n` (no-prompt).
            Sudo берёт credentials из кэша timestamp'а.
          • PTY-терминал больше не занимается password-инжектом, что
            убирает race conditions с echo/SIGHUP.
        """
        self.state = WorkflowState.INSTALL
        self.attempts += 1

        # Решаем, нужен ли sudo. Если да — валидируем заранее.
        needs_sudo = not self.use_aur or bool(prefix_cmd)
        if needs_sudo and not self._ensure_sudo_validated():
            self.card.append(
                "❌", "Не удалось получить sudo-пароль. Установка отменена.",
            )
            self._fail("sudo password not provided/invalid")
            return

        if self.use_aur:
            # yay сам зовёт sudo внутри. Если есть prefix_cmd — выполним
            # его под sudo -A (askpass) для получения пароля без tty.
            if prefix_cmd:
                cmd = (
                    f"sudo -A bash -c {self._shquote(prefix_cmd)} && "
                    f"yay -S --noconfirm --needed --noprogressbar "
                    f"{self.package_name}"
                )
            else:
                cmd = (
                    f"yay -S --noconfirm --needed --noprogressbar "
                    f"{self.package_name}"
                )
        else:
            install_cmd = (
                f"pacman -S --noconfirm --needed --noprogressbar "
                f"{self.package_name}"
            )
            if prefix_cmd:
                inner = f"{prefix_cmd} && {install_cmd}"
            else:
                inner = install_cmd
            cmd = f"sudo -A bash -c {self._shquote(inner)}"

        # Пробрасываем env-переменные SUDO_ASKPASS и LINA_SUDO_PW
        # через bash export. Они нужны только этому процессу,
        # видны через /proc/PID/environ только владельцу процесса.
        env_prefix = ""
        if self._askpass_path and self._askpass_pw is not None:
            env_prefix = (
                f"export SUDO_ASKPASS={self._shquote(self._askpass_path)}; "
                f"export LINA_SUDO_PW={self._shquote(self._askpass_pw)}; "
            )

        # ВАЖНО: оборачиваем в `timeout` чтобы мёртвая mirror-загрузка
        # (SOCKS5/proxy/SSL handshake) не висела вечно. Через
        # INSTALL_HARD_TIMEOUT_S команда будет убита по SIGTERM, через
        # +10с — SIGKILL. Workflow получит exit_code=124 (timeout) и
        # прокатится по diagnose-механизму как NETWORK.
        full_cmd = env_prefix + cmd
        cmd = (
            f"timeout --kill-after=10 {self.INSTALL_HARD_TIMEOUT_S} "
            f"bash -c {self._shquote(full_cmd)}"
        )

        if self.attempts > 1:
            self.card.append(
                "⏳", f"Установка (попытка {self.attempts}/{self.MAX_RETRIES})…",
            )
        else:
            self.card.append("⏳", f"Устанавливаю `{self.package_name}`…")
        self._update_card()

        self._connect_terminal()
        self._pending_step = "install"
        self._fixup_then = None
        self._arm_step_timeout()
        # Запускаем без password-инжекта — sudo -n использует кэш.
        self.terminal.run_command(cmd)

    @staticmethod
    def _shquote(s: str) -> str:
        """Безопасное оборачивание строки для bash -c."""
        return "'" + s.replace("'", "'\\''") + "'"

    def _ensure_sudo_validated(self) -> bool:
        """Гарантировать что sudo-credentials доступны для PTY-команд.

        Архитектурная проблема: sudoers по умолчанию имеет tty_tickets,
        timestamp привязан к конкретному tty. Мы валидируем пароль
        через `sudo -v -S` в subprocess (main-tty), но потом install
        запускается в PTY (другой tty) — timestamp не виден, sudo
        требует пароль.

        Решение: SUDO_ASKPASS-скрипт. Пишем пароль во временный
        исполняемый файл в /run/user/UID, ставим его в SUDO_ASKPASS,
        и запускаем install как `sudo -A …`. Sudo сам зовёт askpass
        и получает пароль независимо от tty/timestamp.

        Файл удаляется в _done/_fail/cancel и в __del__.

        Возвращает False если:
          • нет on_password_request;
          • пользователь отменил диалог;
          • пароль неверный.
        """
        # 1. Если уже есть рабочий askpass-файл — ничего не делаем.
        if self._askpass_path is not None:
            return True

        # 2. Может быть sudo timestamp ещё свежий И tty совпадает?
        # Это редкий случай (только если предыдущий sudo был в тот же
        # PTY), но если да — обойдёмся без askpass.
        code, _, _ = _run(["sudo", "-n", "true"], timeout=3)
        if code == 0:
            logger.debug("sudo timestamp is fresh, skipping askpass setup")
            return True

        # 3. Спрашиваем пароль (если есть колбэк).
        if self.on_password_request is None:
            logger.warning("sudo timestamp expired and no password callback")
            return False

        for attempt in range(3):
            try:
                pw = self.on_password_request(
                    "Lina нужен sudo-пароль для установки пакетов. "
                    "Используется только в этой сессии и не сохраняется."
                    + (" (попытка {0}/3)".format(attempt + 1)
                       if attempt > 0 else ""),
                )
            except Exception as e:
                logger.error("on_password_request failed: %s", e, exc_info=True)
                return False
            if not pw:
                return False
            if not self._validate_password_silent(pw):
                logger.warning(
                    "sudo password rejected (attempt %d)", attempt + 1)
                continue
            # 4. Пароль корректный — пишем askpass-скрипт.
            if self._setup_askpass(pw):
                logger.info("sudo askpass armed at %s", self._askpass_path)
                return True
            logger.error("Failed to set up askpass file")
            return False

        logger.error("sudo password failed 3 times, giving up")
        return False

    @staticmethod
    def _validate_password_silent(pw: str) -> bool:
        """Тихо валидировать sudo-пароль через `sudo -v -S`.

        Возвращает True если пароль корректный. Это валидация только
        для проверки правильности — timestamp может не дойти до PTY
        из-за tty_tickets, но мы тут не на это полагаемся, мы на
        askpass-файл полагаемся.
        """
        try:
            proc = subprocess.run(
                ["sudo", "-v", "-S", "-p", ""],
                input=pw + "\n",
                capture_output=True, text=True, timeout=10,
            )
            return proc.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("sudo -v timed out")
            return False
        except Exception as e:
            logger.error("sudo -v failed: %s", e)
            return False

    # ─── Askpass: пароль через временный скрипт, без tty/timestamp ────

    def _setup_askpass(self, password: str) -> bool:
        """Создать askpass-скрипт во временной приватной директории.

        Sudo с -A зовёт SUDO_ASKPASS executable, читает пароль из его
        stdout. Это полностью обходит tty_tickets — пароль доставляется
        через файл-скрипт, независимо от того где работает sudo.

        Скрипт пишется в `/run/user/UID/lina-sudo-{pid}-{rand}.sh`:
          • mode 0700 (owner-only);
          • tmpfs (RAM, не диск);
          • короткое время жизни (workflow-scope);
          • удаляется в _done/_fail/cancel/__del__.
        """
        try:
            import os
            import tempfile
            uid = os.getuid()
            run_dir = f"/run/user/{uid}"
            if not os.path.isdir(run_dir):
                # Fallback на /tmp с приватной mkdtemp (mode 0700).
                run_dir = tempfile.mkdtemp(prefix="lina-sudo-")
            else:
                # Создаём поддиректорию чтобы файл не светился рядом
                # с системными.
                run_dir = tempfile.mkdtemp(prefix="lina-sudo-", dir=run_dir)
            os.chmod(run_dir, 0o700)

            path = os.path.join(run_dir, "askpass.sh")
            # printf даёт лучший контроль над завершающим символом.
            # %s маскирует спецсимволы пароля от bash интерпретации.
            # Пароль передаём через env-переменную чтобы он не попал
            # в `ps` или в `cat /proc/PID/cmdline` для askpass-процесса.
            content = (
                "#!/bin/bash\n"
                "# Lina sudo askpass — used by SUDO_ASKPASS\n"
                'printf "%s" "$LINA_SUDO_PW"\n'
            )
            with open(path, "w") as f:
                f.write(content)
            os.chmod(path, 0o700)

            self._askpass_path = path
            self._askpass_dir = run_dir
            self._askpass_pw = password
            return True
        except Exception as e:
            logger.error("Cannot create askpass script: %s", e, exc_info=True)
            return False

    def _cleanup_askpass(self) -> None:
        """Удалить askpass-файл и забыть пароль."""
        path = getattr(self, "_askpass_path", None)
        run_dir = getattr(self, "_askpass_dir", None)
        # Принудительно перезаписываем переменные с паролем перед удалением.
        self._askpass_pw = None
        self._askpass_path = None
        self._askpass_dir = None
        try:
            import os
            if path and os.path.exists(path):
                # Затираем содержимое перед unlink — против reanimation
                # из remnant-страниц (на tmpfs не критично, но дешёво).
                try:
                    with open(path, "wb") as f:
                        f.write(b"#!/bin/bash\n# wiped\n")
                except Exception:
                    pass
                os.unlink(path)
            if run_dir and os.path.isdir(run_dir):
                try:
                    os.rmdir(run_dir)
                except OSError:
                    # Если в директории остались файлы — попробуем shutil.
                    import shutil as _sh
                    _sh.rmtree(run_dir, ignore_errors=True)
        except Exception as e:
            logger.debug("askpass cleanup failed: %s", e)

    def _build_sudo_env(self) -> dict:
        """Собрать env для PTY-команды с настроенным SUDO_ASKPASS."""
        import os
        env = dict(os.environ)
        if self._askpass_path:
            env["SUDO_ASKPASS"] = self._askpass_path
            if self._askpass_pw is not None:
                env["LINA_SUDO_PW"] = self._askpass_pw
        return env

    def _on_terminal_finished(self, exit_code: int, command: str, output: str) -> None:
        """Колбэк от EmbeddedTerminal.command_finished.

        После архитектурного рефакторинга (fix+install склеены в одну
        sudo-сессию) у нас остался только один pending_step — `install`.
        Старый `fixup` сценарий оставлен для обратной совместимости.
        """
        pending = self._pending_step
        logger.info(
            "Workflow terminal_finished: exit=%d cmd=%.60s pending=%s state=%s",
            exit_code, command, pending, self.state.value,
        )
        # Сразу сбрасываем чтобы вложенные колбэки не срабатывали повторно.
        self._pending_step = None
        # Снимаем safety-таймаут.
        self._cancel_step_timeout()

        if pending is None:
            # Сигнал прилетел не от нас (например, пользователь сам
            # выполнил команду в терминале). Игнорируем.
            return

        if pending == "fixup":
            # Legacy путь: остался для совместимости, но в новом коде
            # не используется.
            cb = self._fixup_then
            self._fixup_then = None
            if cb is None:
                logger.warning("Fixup callback missing")
                return
            try:
                cb()
            except Exception as e:
                logger.error("Fixup callback failed: %s", e, exc_info=True)
                self._fail(f"внутренняя ошибка при retry: {e}")
            return

        if pending == "install":
            if exit_code == 0:
                self.card.steps[-1] = StepLog(
                    "✓", f"Установка завершена (попытка {self.attempts})",
                )
                self._update_card()
                self._step_verify()
            else:
                self.card.steps[-1] = StepLog(
                    "⚠", f"Установка не удалась (попытка {self.attempts})",
                )
                self._update_card()
                self._step_diagnose(output)
            return

        logger.warning("Unknown pending step: %s", pending)

    def _step_diagnose(self, output: str) -> None:
        self.state = WorkflowState.DIAGNOSE
        err_class = classify_pacman_error(output)
        # Лог с примером вывода — чтобы видеть ЧТО именно классификатор
        # увидел. Без этого диагностика «class=unknown» бесполезна.
        sample = (output or "").strip()
        if len(sample) > 800:
            sample = sample[:400] + "\n…\n" + sample[-300:]
        logger.info(
            "Install diagnose: class=%s output_sample=%s",
            err_class.value, repr(sample),
        )

        # Если output пустой — это значит timeout-обёртка убила процесс
        # ДО того как pacman успел напечатать ошибку (либо PTY на
        # cleanup/cancel прибил процесс). И то и другое — сетевая
        # история, дальше идём через _fix_network.
        if err_class == ErrorClass.UNKNOWN and not sample:
            err_class = ErrorClass.NETWORK
            logger.info("Empty output → reclassified as NETWORK")

        if self.attempts >= self.MAX_RETRIES:
            self.card.append(
                "❌", f"Превышен лимит попыток ({self.MAX_RETRIES}).",
            )
            self._fail(f"3 попытки провалены, причина: {err_class.value}")
            return

        if err_class == ErrorClass.DB_LOCK:
            self._fix_db_lock()
            return

        if err_class == ErrorClass.NETWORK:
            self._fix_network()
            return

        if err_class == ErrorClass.NOT_FOUND:
            # Пакет был найден ранее, но pacman теперь говорит «не найден» —
            # вероятно зеркала рассинхронизировались. Делаем pacman -Sy
            # склеенным с install.
            self.card.append(
                "🔧", "Зеркала рассинхронизированы — обновляю БД и переустанавливаю…",
            )
            self._update_card()
            self._step_install(prefix_cmd="pacman -Sy --noconfirm")
            return

        if err_class == ErrorClass.PERMISSION:
            # На pacman -S без sudo. На AUR — разрешения должны быть у sudo
            # внутри yay. В обоих случаях retry с тем же sudo не поможет —
            # проблема вне нашей зоны.
            self.card.append(
                "❌", "Нет прав на установку. Запусти Lina с правами "
                     "sudo или дай sudoers-разрешение.",
            )
            self._fail("permission denied")
            return

        if err_class == ErrorClass.SIGNATURE:
            self.card.append(
                "🔧", "Проблема с подписями ключей — обновляю keyring и переустанавливаю…",
            )
            self._update_card()
            # Combined: keyring update + install в одной sudo-сессии.
            self._step_install(
                prefix_cmd="pacman -Sy --noconfirm archlinux-keyring",
            )
            return

        if err_class == ErrorClass.CONFLICT:
            self.card.append(
                "❌", "Конфликт файлов в системе. Нужна ручная разборка — "
                     "это потенциально разрушительная операция.",
            )
            self._fail("file conflict")
            return

        # UNKNOWN — пробуем pacman -Sy и ретрай (часто помогает)
        self.card.append("🔧", "Неизвестная ошибка, обновляю БД пакетов и переустанавливаю…")
        self._update_card()
        # Не зовём _fix_refresh_db (оно бы добавило ещё одну строку
        # «Обновляю БД пакетов…»). Сразу собираем install с prefix.
        self._step_install(prefix_cmd="pacman -Sy --noconfirm")

    def _step_verify(self) -> None:
        self.state = WorkflowState.VERIFY
        self.card.append("⏳", "Проверяю что пакет работает…")
        self._update_card()

        # 1. Пакет в БД?
        code, out, _ = _run(["pacman", "-Q", self.package_name], timeout=5)
        if code != 0 or not out.strip():
            self.card.steps[-1] = StepLog(
                "⚠", f"Пакет `{self.package_name}` не найден в БД pacman",
            )
            self._update_card()
            self._fail("verify failed: not in pacman db")
            return

        ver = out.strip().split()[-1] if out.strip().split() else ""

        # 2. Бинарь?
        bin_name = self._guess_binary(self.package_name)
        bin_path = ""
        code, out, _ = _run(["which", bin_name], timeout=3)
        if code == 0 and out.strip():
            bin_path = out.strip()

        # 3. --version?
        version_str = self._safe_version(bin_name) if bin_path else ""

        if bin_path:
            extra = f" ({version_str})" if version_str else ""
            self.card.steps[-1] = StepLog(
                "✓", f"Бинарь: `{bin_path}`{extra}",
            )
        else:
            # Бинаря с таким именем нет — это нормально для библиотек или
            # пакетов с другим именем бинаря. Не падаем.
            self.card.steps[-1] = StepLog(
                "✓", f"Пакет `{self.package_name}` {ver} установлен в систему",
            )

        self.binary_name = bin_name if bin_path else ""
        self.card.status = "success"
        self.card.append(
            "✅",
            f"Готово. {self._launch_hint(bin_path, bin_name)}",
        )
        self._update_card()
        self._done(InstallResult(
            success=True, target=self.target,
            package=self.package_name, version=ver,
            binary=bin_path, card=self.card,
        ))

    # ─── Авто-фиксы ──────────────────────────────────────────────────────

    def _fix_db_lock(self) -> None:
        """Удалить /var/lib/pacman/db.lck — С ПОДТВЕРЖДЕНИЕМ.

        Архитектурно: фикс склеиваем с install в одну sudo-команду
        (`sudo bash -c 'rm -f db.lck && pacman -S ...'`). Это гарантирует
        одну password-prompt и исключает зависание между двумя
        отдельными sudo-вызовами.
        """
        if _is_pacman_running():
            self.card.append(
                "❌", "БД pacman заблокирована активным процессом — "
                     "подожди пока он закончит.",
            )
            self._fail("pacman is busy")
            return

        if self.on_confirm_request is None:
            self.card.append(
                "❌", "БД заблокирована, но нет способа спросить разрешения "
                     "на разблокировку.",
            )
            self._fail("db lock, no confirm channel")
            return

        ok = self.on_confirm_request(
            "Разблокировать pacman?",
            "Активного процесса pacman не найдено, но есть файл блокировки\n"
            "/var/lib/pacman/db.lck (остался от прошлой прерванной операции).\n\n"
            "Удалить файл и продолжить установку?",
        )
        if not ok:
            self.card.append("⚠", "Пользователь отменил разблокировку.")
            self._fail("user declined db unlock")
            return

        self.card.append("🔧", "Снимаю блокировку pacman и переустанавливаю…")
        self._update_card()
        # Combined: db.lck remove + install в одной sudo-сессии.
        self._step_install(prefix_cmd="rm -f /var/lib/pacman/db.lck")

    def _fix_network(self) -> None:
        """Сетевая ошибка: чаще всего SOCKS5/прокси/таймаут зеркал.

        Стратегия:
          • попытка 2: cachyos-rate-mirrors → install
          • попытка 3: reflector → install (если cachyos-rate-mirrors
            тоже сетевую дала или его вообще нет)
          • дальше — fallback на просто pacman -Sy.
        """
        attempt = self.attempts
        if shutil.which("cachyos-rate-mirrors") and attempt <= 1:
            self.card.append(
                "🔧",
                "Сетевая ошибка. Обновляю зеркала (cachyos-rate-mirrors) "
                "и переустанавливаю…",
            )
            self._update_card()
            self._step_install(prefix_cmd="cachyos-rate-mirrors")
            return
        if shutil.which("reflector") and attempt <= 2:
            self.card.append(
                "🔧",
                "Обновляю зеркала (reflector) и переустанавливаю…",
            )
            self._update_card()
            self._step_install(
                prefix_cmd=(
                    "reflector --latest 10 --protocol https "
                    "--sort rate --save /etc/pacman.d/mirrorlist"
                ),
            )
            return
        # Третья попытка или нет утилит — просто refresh БД.
        self.card.append("🔧", "Освежаю БД пакетов и переустанавливаю…")
        self._update_card()
        self._step_install(prefix_cmd="pacman -Sy --noconfirm")

    def _fix_refresh_db(self) -> None:
        """sudo pacman -Sy + retry — одной командой."""
        self.card.append("🔧", "Обновляю БД пакетов и переустанавливаю…")
        self._update_card()
        self._step_install(prefix_cmd="pacman -Sy --noconfirm")

    # ─── Вспомогательные ────────────────────────────────────────────────

    def _connect_terminal(self) -> None:
        if self._terminal_handler_connected:
            return
        try:
            self.terminal.command_finished.connect(self._on_terminal_finished)
            self._terminal_handler_connected = True
        except Exception as e:
            logger.error("Cannot connect terminal signal: %s", e)

    def _disconnect_terminal(self) -> None:
        if not self._terminal_handler_connected:
            return
        try:
            self.terminal.command_finished.disconnect(self._on_terminal_finished)
        except Exception:
            pass
        self._terminal_handler_connected = False

    def _connect_terminal_for_followup(self, then: Callable[[], None]) -> None:
        """Подготовиться к получению сигнала после авто-фикса.

        Используется для авто-фиксов: «выполни команду фикса → потом retry install».
        Запускается ПЕРЕД вызовом self.terminal.run_command(...) для фикса.
        """
        self._pending_step = "fixup"
        self._fixup_then = then

    # ─── Safety-net таймер ──────────────────────────────────────────────

    def _arm_step_timeout(self) -> None:
        """Стартануть QTimer на STEP_TIMEOUT_MS. Если шаг не завершится —
        вызовет _on_step_timeout и workflow упадёт с понятной причиной
        вместо тихого зависания.
        """
        try:
            from lina.gui import get_qt_modules
            _, QtCore, _ = get_qt_modules()
        except Exception as e:
            logger.debug("Qt modules unavailable for timeout: %s", e)
            return
        # Если уже армирован — переармируем (один таймер на workflow).
        self._cancel_step_timeout()
        try:
            t = QtCore.QTimer()
            t.setSingleShot(True)
            t.setInterval(self.STEP_TIMEOUT_MS)
            t.timeout.connect(self._on_step_timeout)
            t.start()
            self._step_timer = t
        except Exception as e:
            logger.debug("Cannot arm step timeout: %s", e)

    def _cancel_step_timeout(self) -> None:
        if self._step_timer is None:
            return
        try:
            self._step_timer.stop()
        except Exception:
            pass
        try:
            self._step_timer.deleteLater()
        except Exception:
            pass
        self._step_timer = None

    def _on_step_timeout(self) -> None:
        """Сработал safety-таймаут. PTY завис на password-prompt или
        внешнем ожидании. Останавливаем терминал, диагностируем шаг как
        ошибку, чтобы либо запустить retry либо красиво упасть."""
        logger.warning(
            "Step timeout reached (state=%s, attempts=%d). Killing terminal.",
            self.state.value, self.attempts,
        )
        self._cancel_step_timeout()
        # Стопнём процесс. terminal.stop() эмитит command_finished,
        # но мы УЖЕ сбрасываем pending — это маркер «я обработал тайм-аут».
        was_pending = self._pending_step
        self._pending_step = None
        try:
            if hasattr(self.terminal, "stop"):
                self.terminal.stop()
        except Exception as e:
            logger.debug("terminal.stop on timeout failed: %s", e)

        if was_pending == "install":
            self.card.append("⚠", "Превышено время ожидания (3 минуты).")
            self._update_card()
            # Используем существующий diagnose-механизм: отдадим как
            # NETWORK-class — это даст refresh-mirrors и retry.
            self._step_diagnose("connection timed out")
        else:
            # fixup или None — просто валим.
            self.card.append("❌", "Команда зависла, не удалось продолжить.")
            self._fail("step timeout")

    def _update_card(self) -> None:
        try:
            self.on_card_update(self.card.render())
        except Exception as e:
            logger.error("on_card_update failed: %s", e)

    def _done(self, result: InstallResult) -> None:
        self._cancel_step_timeout()
        self._stop_sudo_keepalive()
        self._cleanup_askpass()
        self._disconnect_terminal()
        self._sudo_password = None
        self.state = WorkflowState.DONE
        try:
            self.on_done(result)
        except Exception as e:
            logger.error("on_done failed: %s", e)

    def _fail(self, reason: str) -> None:
        self._cancel_step_timeout()
        self._stop_sudo_keepalive()
        self._cleanup_askpass()
        self._disconnect_terminal()
        self._sudo_password = None
        self.state = WorkflowState.FAILED
        self.card.status = "failed"
        self._update_card()
        try:
            self.on_done(InstallResult(
                success=False, target=self.target,
                package=self.package_name,
                reason=reason, card=self.card,
            ))
        except Exception as e:
            logger.error("on_done failed: %s", e)

    def _pick_best_package(self, results: list) -> Optional[dict]:
        """Выбор лучшего пакета из результатов pacman -Ss."""
        if not results:
            return None
        # 1) Точное совпадение имени
        for r in results:
            if r.get("name", "").lower() == self.target.lower():
                return r
        # 2) name == target + типичные суффиксы (-desktop, -bin, -git)
        for suffix in ("-desktop", "-bin", "-cli", "-gtk"):
            wanted = self.target.lower() + suffix
            for r in results:
                if r.get("name", "").lower() == wanted:
                    return r
        # 3) name начинается с target и нет «git»/«beta» — берём короткий
        candidates = [
            r for r in results
            if r.get("name", "").lower().startswith(self.target.lower())
            and "git" not in r.get("name", "").lower()
            and "beta" not in r.get("name", "").lower()
        ]
        if candidates:
            return min(candidates, key=lambda r: len(r.get("name", "")))
        # 4) Любой из топа, если соответствие хотя бы по слову
        return results[0]

    @staticmethod
    def _common_binary_aliases(target: str) -> List[str]:
        """Типичные имена бинаря для известных приложений."""
        t = target.lower()
        aliases_map = {
            "telegram": ["telegram-desktop"],
            "телеграм": ["telegram-desktop"],
            "vscode": ["code"],
            "visualstudiocode": ["code"],
            "google-chrome": ["google-chrome-stable", "google-chrome"],
            "chrome": ["google-chrome-stable", "google-chrome", "chromium"],
            "firefox": ["firefox", "firefox-pure"],
            "discord": ["discord"],
            "obs": ["obs"],
            "obsidian": ["obsidian"],
            "spotify": ["spotify"],
            "claude": ["claude", "claude-code"],
            "claude-code": ["claude", "claude-code"],
            "claude code": ["claude", "claude-code"],
        }
        return aliases_map.get(t, [])

    @staticmethod
    def _guess_binary(package: str) -> str:
        """Определить имя бинаря по имени пакета."""
        p = package.lower()
        # Известные mappings
        mapping = {
            "telegram-desktop": "telegram-desktop",
            "google-chrome": "google-chrome-stable",
            "google-chrome-stable": "google-chrome-stable",
            "visual-studio-code-bin": "code",
            "code": "code",
            "claude-code": "claude",
        }
        if p in mapping:
            return mapping[p]
        # Простая эвристика: package == binary, иначе срезать суффиксы
        for suffix in ("-bin", "-git", "-desktop", "-stable"):
            if p.endswith(suffix):
                return p[: -len(suffix)]
        return p

    @staticmethod
    def _safe_version(binary: str) -> str:
        """Получить версию бинаря через `bin --version`."""
        if not binary:
            return ""
        for flag in ("--version", "-V", "-v"):
            code, out, err = _run([binary, flag], timeout=3)
            text = (out or err or "").strip()
            # Берём первую строку, не больше 80 символов
            if text:
                first = text.splitlines()[0]
                # Уровень версии: ищем число
                m = re.search(r"\d+(?:\.\d+){1,3}", first)
                if m:
                    return m.group(0)
                return first[:50]
        return ""

    @staticmethod
    def _launch_hint(bin_path: str, bin_name: str) -> str:
        if bin_path and bin_name:
            return f"Запусти из меню или командой `{bin_name}`."
        return "Пакет установлен в систему."
