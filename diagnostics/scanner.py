"""
SystemStateScanner — полный сбор состояния системы.

Агрегирует данные из всех подсистем Linux в единый SystemState:
CPU, RAM, Disk, SMART, температуры, сервисы, процессы, сеть,
firewall, аудио, GPU, дисплей, ядро, пакеты, обновления.

Использует существующие модули system/ как бэкенд.
Результат — детерминированный снимок для всех остальных движков.

Phase: PROBLEM TERMINATOR / Module 1
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  Dataclasses — структурированное состояние системы
# ═══════════════════════════════════════════════════════════════════

class HealthLevel(Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class SubsystemHealth:
    """Здоровье одной подсистемы."""
    name: str
    level: HealthLevel = HealthLevel.UNKNOWN
    summary: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    raw_output: str = ""


@dataclass
class SystemState:
    """Полный снимок состояния Linux-системы."""
    timestamp: float = 0.0
    hostname: str = ""
    kernel: str = ""
    distro: str = ""
    uptime: str = ""
    de: str = ""
    display_server: str = ""  # X11 / Wayland

    # Подсистемы
    cpu: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("cpu"))
    ram: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("ram"))
    disk: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("disk"))
    smart: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("smart"))
    temperatures: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("temperatures"))
    services: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("services"))
    processes: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("processes"))
    network: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("network"))
    firewall: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("firewall"))
    audio: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("audio"))
    gpu: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("gpu"))
    display: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("display"))
    packages: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("packages"))
    updates: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("updates"))
    bluetooth: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("bluetooth"))
    cron: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("cron"))
    mounts: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("mounts"))
    permissions: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("permissions"))

    # Агрегированная оценка
    overall: HealthLevel = HealthLevel.UNKNOWN
    problems: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        subsystems = {}
        for name in (
            "cpu", "ram", "disk", "smart", "temperatures", "services",
            "processes", "network", "firewall", "audio", "gpu", "display",
            "packages", "updates", "bluetooth", "cron", "mounts", "permissions",
        ):
            sh: SubsystemHealth = getattr(self, name)
            subsystems[name] = {
                "level": sh.level.value,
                "summary": sh.summary,
                "details": sh.details,
            }
        return {
            "timestamp": self.timestamp,
            "hostname": self.hostname,
            "kernel": self.kernel,
            "distro": self.distro,
            "uptime": self.uptime,
            "de": self.de,
            "display_server": self.display_server,
            "overall": self.overall.value,
            "problems": self.problems,
            "subsystems": subsystems,
        }

    def format_summary(self) -> str:
        """Краткий текстовый отчёт о состоянии."""
        lines = [
            f"═══ Состояние системы ({self.hostname}) ═══",
            f"  Ядро: {self.kernel}  |  Дистрибутив: {self.distro}",
            f"  Uptime: {self.uptime}  |  DE: {self.de}  |  Display: {self.display_server}",
            "",
        ]
        for name in (
            "cpu", "ram", "disk", "smart", "temperatures", "services",
            "processes", "network", "firewall", "audio", "gpu", "display",
            "packages", "updates", "bluetooth", "cron", "mounts", "permissions",
        ):
            sh: SubsystemHealth = getattr(self, name)
            icon = {"ok": "✅", "warning": "⚠️", "critical": "🔴", "unknown": "❓"}.get(
                sh.level.value, "❓"
            )
            lines.append(f"  {icon} {name:14s} {sh.summary}")
        if self.problems:
            lines.append("")
            lines.append(f"  🔍 Обнаружено проблем: {len(self.problems)}")
            for p in self.problems:
                lines.append(f"     • {p}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Утилиты выполнения команд
# ═══════════════════════════════════════════════════════════════════

def _run(cmd: str, timeout: int = 10) -> Tuple[int, str]:
    """Безопасный запуск shell-команды, возврат (returncode, stdout+stderr)."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, env={**os.environ, "LANG": "C.UTF-8"},
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, f"TIMEOUT ({timeout}s)"
    except Exception as e:
        return -1, str(e)


# ═══════════════════════════════════════════════════════════════════
#  SystemStateScanner
# ═══════════════════════════════════════════════════════════════════

class SystemStateScanner:
    """
    Полный сканер состояния Linux-системы.

    Собирает детерминированный снимок всех подсистем за один проход.
    Кэширует результат на _CACHE_TTL секунд.
    """

    _CACHE_TTL = 30  # секунд

    def __init__(self) -> None:
        self._cache: Optional[SystemState] = None
        self._cache_ts: float = 0.0

    # ─── Главный метод ────────────────────────────────────────

    def scan(self, force: bool = False) -> SystemState:
        """Полный скан системы. Использует кэш если < TTL."""
        now = time.time()
        if not force and self._cache and (now - self._cache_ts) < self._CACHE_TTL:
            return self._cache

        state = SystemState(timestamp=now)

        # Базовая информация
        self._scan_base(state)

        # Подсистемы — каждая в try/except (anti-crash)
        scanners = [
            self._scan_cpu, self._scan_ram, self._scan_disk,
            self._scan_smart, self._scan_temperatures, self._scan_services,
            self._scan_processes, self._scan_network, self._scan_firewall,
            self._scan_audio, self._scan_gpu, self._scan_display,
            self._scan_packages, self._scan_updates, self._scan_bluetooth,
            self._scan_cron, self._scan_mounts, self._scan_permissions,
        ]
        for scanner in scanners:
            try:
                scanner(state)
            except Exception as e:
                name = scanner.__name__.replace("_scan_", "")
                logger.warning("Scanner %s failed: %s", name, e)
                sh = getattr(state, name, None)
                if sh and isinstance(sh, SubsystemHealth):
                    sh.level = HealthLevel.UNKNOWN
                    sh.summary = f"Ошибка сканирования: {e}"

        # Агрегация
        self._aggregate(state)

        self._cache = state
        self._cache_ts = now
        return state

    def scan_subsystem(self, name: str) -> SubsystemHealth:
        """Сканировать одну подсистему."""
        state = self.scan()
        sh = getattr(state, name, None)
        if sh and isinstance(sh, SubsystemHealth):
            return sh
        return SubsystemHealth(name=name, summary="Неизвестная подсистема")

    def invalidate_cache(self) -> None:
        self._cache = None
        self._cache_ts = 0.0

    # ─── Базовая информация ───────────────────────────────────

    def _scan_base(self, s: SystemState) -> None:
        _, s.hostname = _run("hostname")
        _, s.kernel = _run("uname -r")
        # Дистрибутив
        _, out = _run("cat /etc/os-release 2>/dev/null | head -5")
        for line in out.splitlines():
            if line.startswith("PRETTY_NAME="):
                s.distro = line.split("=", 1)[1].strip('"')
                break
        _, s.uptime = _run("uptime -p 2>/dev/null || uptime")
        # DE
        s.de = os.environ.get("XDG_CURRENT_DESKTOP", os.environ.get("DESKTOP_SESSION", ""))
        # Display server
        if os.environ.get("WAYLAND_DISPLAY"):
            s.display_server = "Wayland"
        elif os.environ.get("DISPLAY"):
            s.display_server = "X11"
        else:
            s.display_server = "TTY"

    # ─── CPU ──────────────────────────────────────────────────

    def _scan_cpu(self, s: SystemState) -> None:
        h = s.cpu
        _, out = _run("nproc")
        cores = int(out) if out.isdigit() else 0
        _, load_out = _run("cat /proc/loadavg")
        parts = load_out.split()
        load1 = float(parts[0]) if parts else 0.0
        load5 = float(parts[1]) if len(parts) > 1 else 0.0

        # Имя процессора
        _, cpuinfo = _run("grep 'model name' /proc/cpuinfo | head -1")
        cpu_name = cpuinfo.split(":", 1)[1].strip() if ":" in cpuinfo else "?"

        h.details = {
            "cores": cores, "load1": load1, "load5": load5, "name": cpu_name,
        }
        ratio = load1 / cores if cores else 0
        if ratio > 2.0:
            h.level = HealthLevel.CRITICAL
            h.summary = f"Перегрузка! Load {load1:.1f} на {cores} ядер"
            s.problems.append(f"CPU перегрузка: load={load1:.1f}/{cores}")
        elif ratio > 1.0:
            h.level = HealthLevel.WARNING
            h.summary = f"Высокая нагрузка: {load1:.1f} на {cores} ядер"
        else:
            h.level = HealthLevel.OK
            h.summary = f"{cpu_name[:40]} — load {load1:.1f}/{cores}"

    # ─── RAM ──────────────────────────────────────────────────

    def _scan_ram(self, s: SystemState) -> None:
        h = s.ram
        _, out = _run("free -m | grep Mem")
        parts = out.split()
        if len(parts) >= 3:
            total = int(parts[1])
            used = int(parts[2])
            pct = (used / total * 100) if total else 0
            h.details = {"total_mb": total, "used_mb": used, "percent": round(pct, 1)}
            if pct > 95:
                h.level = HealthLevel.CRITICAL
                h.summary = f"Критически мало RAM: {pct:.0f}% ({used}/{total} MB)"
                s.problems.append(f"RAM {pct:.0f}% — критически мало памяти")
            elif pct > 80:
                h.level = HealthLevel.WARNING
                h.summary = f"Высокое использование RAM: {pct:.0f}% ({used}/{total} MB)"
            else:
                h.level = HealthLevel.OK
                h.summary = f"{used}/{total} MB ({pct:.0f}%)"
        else:
            h.level = HealthLevel.UNKNOWN
            h.summary = "Не удалось прочитать RAM"

    # ─── Disk ─────────────────────────────────────────────────

    def _scan_disk(self, s: SystemState) -> None:
        h = s.disk
        _, out = _run("df -h / --output=size,used,avail,pcent | tail -1")
        parts = out.split()
        if len(parts) >= 4:
            pct = int(parts[3].rstrip("%"))
            h.details = {"total": parts[0], "used": parts[1], "avail": parts[2], "percent": pct}
            if pct > 95:
                h.level = HealthLevel.CRITICAL
                h.summary = f"Диск почти полон: {pct}% (свободно {parts[2]})"
                s.problems.append(f"Диск / заполнен на {pct}%")
            elif pct > 80:
                h.level = HealthLevel.WARNING
                h.summary = f"Диск заполняется: {pct}% (свободно {parts[2]})"
            else:
                h.level = HealthLevel.OK
                h.summary = f"Использовано {pct}% (свободно {parts[2]})"
        else:
            h.level = HealthLevel.UNKNOWN
            h.summary = "Не удалось прочитать диск"

    # ─── SMART ────────────────────────────────────────────────

    def _scan_smart(self, s: SystemState) -> None:
        h = s.smart
        if not shutil.which("smartctl"):
            h.level = HealthLevel.UNKNOWN
            h.summary = "smartctl не установлен"
            return
        # Находим первый блочный диск
        _, lsblk = _run("lsblk -dno NAME,TYPE | grep disk | head -3")
        disks = [line.split()[0] for line in lsblk.splitlines() if line.strip()]
        if not disks:
            h.level = HealthLevel.UNKNOWN
            h.summary = "Не найдены диски"
            return
        _SAFE_DISK = re.compile(r"^[a-zA-Z0-9_-]+$")
        problems = []
        for disk in disks:
            if not _SAFE_DISK.match(disk):
                continue  # skip suspicious device names
            rc, out = _run(f"smartctl -H /dev/{disk} 2>/dev/null")
            if "PASSED" in out:
                continue
            elif "FAILED" in out:
                problems.append(f"/dev/{disk}: SMART FAILED")
        if problems:
            h.level = HealthLevel.CRITICAL
            h.summary = "; ".join(problems)
            for p in problems:
                s.problems.append(f"SMART: {p}")
        else:
            h.level = HealthLevel.OK
            h.summary = f"SMART OK ({len(disks)} дисков)"
        h.details = {"disks": disks}

    # ─── Температуры ──────────────────────────────────────────

    def _scan_temperatures(self, s: SystemState) -> None:
        h = s.temperatures
        if not shutil.which("sensors"):
            h.level = HealthLevel.UNKNOWN
            h.summary = "lm-sensors не установлен"
            return
        _, out = _run("sensors 2>/dev/null")
        h.raw_output = out
        # Парсим температуры
        temps = []
        for m in re.finditer(r"(\+[\d.]+)°C", out):
            temps.append(float(m.group(1)))
        if not temps:
            h.level = HealthLevel.UNKNOWN
            h.summary = "Нет данных о температуре"
            return
        max_t = max(temps)
        h.details = {"max": max_t, "count": len(temps)}
        if max_t > 95:
            h.level = HealthLevel.CRITICAL
            h.summary = f"Перегрев! {max_t}°C"
            s.problems.append(f"Температура {max_t}°C — перегрев")
        elif max_t > 80:
            h.level = HealthLevel.WARNING
            h.summary = f"Высокая температура: {max_t}°C"
        else:
            h.level = HealthLevel.OK
            h.summary = f"Макс: {max_t}°C"

    # ─── Сервисы systemd ──────────────────────────────────────

    def _scan_services(self, s: SystemState) -> None:
        h = s.services
        _, out = _run("systemctl --failed --no-pager --no-legend 2>/dev/null")
        failed = [line.strip() for line in out.splitlines() if line.strip()]
        h.details = {"failed_count": len(failed), "failed_list": failed[:10]}
        if len(failed) > 3:
            h.level = HealthLevel.CRITICAL
            h.summary = f"{len(failed)} упавших сервисов"
            s.problems.append(f"systemd: {len(failed)} failed сервисов")
        elif failed:
            h.level = HealthLevel.WARNING
            names = ", ".join(f.split()[0] for f in failed[:3])
            h.summary = f"Упавшие: {names}"
        else:
            h.level = HealthLevel.OK
            h.summary = "Все сервисы работают"

    # ─── Процессы ─────────────────────────────────────────────

    def _scan_processes(self, s: SystemState) -> None:
        h = s.processes
        _, out = _run("ps aux --no-headers | wc -l")
        total = int(out) if out.isdigit() else 0
        # Зомби
        _, zombie_out = _run("ps aux | awk '$8 ~ /Z/ {print $11}' | head -5")
        zombies = [z for z in zombie_out.splitlines() if z.strip()]
        # Top CPU
        _, top_out = _run("ps aux --sort=-%cpu --no-headers | head -3")
        top_procs = []
        for line in top_out.splitlines():
            parts = line.split()
            if len(parts) >= 11:
                top_procs.append({
                    "user": parts[0], "pid": parts[1],
                    "cpu": parts[2], "mem": parts[3],
                    "cmd": " ".join(parts[10:])[:60],
                })
        h.details = {"total": total, "zombies": zombies, "top_cpu": top_procs}
        if zombies:
            h.level = HealthLevel.WARNING
            h.summary = f"{total} процессов, {len(zombies)} зомби"
        else:
            h.level = HealthLevel.OK
            h.summary = f"{total} процессов"

    # ─── Сеть ─────────────────────────────────────────────────

    def _scan_network(self, s: SystemState) -> None:
        h = s.network
        # Интерфейсы
        _, ifaces_out = _run("ip -brief addr 2>/dev/null")
        ifaces = {}
        for line in ifaces_out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] != "lo":
                ifaces[parts[0]] = {"state": parts[1], "addrs": parts[2:]}
        # DNS
        _, dns_out = _run("cat /etc/resolv.conf 2>/dev/null | grep nameserver")
        dns_servers = [line.split()[1] for line in dns_out.splitlines() if "nameserver" in line]
        # Gateway
        _, gw_out = _run("ip route | grep default | head -1")
        gateway = gw_out.split("via")[1].split()[0] if "via" in gw_out else ""
        # Internet check
        rc_ping, _ = _run("ping -c1 -W2 1.1.1.1 2>/dev/null", timeout=5)
        internet = rc_ping == 0

        h.details = {
            "interfaces": ifaces, "dns": dns_servers,
            "gateway": gateway, "internet": internet,
        }
        if not internet:
            h.level = HealthLevel.CRITICAL
            h.summary = "Нет доступа в интернет"
            s.problems.append("Сеть: нет доступа в интернет")
        elif not ifaces:
            h.level = HealthLevel.WARNING
            h.summary = "Не найдены сетевые интерфейсы"
        else:
            up_count = sum(1 for v in ifaces.values() if v["state"] == "UP")
            h.level = HealthLevel.OK
            h.summary = f"{up_count}/{len(ifaces)} интерфейсов UP, интернет есть"

    # ─── Firewall ─────────────────────────────────────────────

    def _scan_firewall(self, s: SystemState) -> None:
        h = s.firewall
        # iptables
        if shutil.which("iptables"):
            _, out = _run("iptables -L -n 2>/dev/null | head -20")
            rules = len([l for l in out.splitlines() if l.strip() and not l.startswith("Chain") and not l.startswith("target")])
            h.details["iptables_rules"] = rules
        # nftables
        if shutil.which("nft"):
            _, out = _run("nft list ruleset 2>/dev/null | wc -l")
            h.details["nft_lines"] = int(out) if out.isdigit() else 0
        # ufw
        if shutil.which("ufw"):
            _, out = _run("ufw status 2>/dev/null")
            h.details["ufw"] = out.strip()
        # firewalld
        if shutil.which("firewall-cmd"):
            _, out = _run("firewall-cmd --state 2>/dev/null")
            h.details["firewalld"] = out.strip()

        h.level = HealthLevel.OK
        h.summary = "Firewall проверен"

    # ─── Аудио ────────────────────────────────────────────────

    def _scan_audio(self, s: SystemState) -> None:
        h = s.audio
        # PipeWire
        pw = shutil.which("pw-cli")
        pa = shutil.which("pactl")
        if pw:
            rc, out = _run("pw-cli info 0 2>/dev/null")
            h.details["backend"] = "PipeWire"
            h.details["pw_running"] = rc == 0
            if rc != 0:
                h.level = HealthLevel.CRITICAL
                h.summary = "PipeWire не работает"
                s.problems.append("Аудио: PipeWire не запущен")
                return
        elif pa:
            h.details["backend"] = "PulseAudio"
        else:
            h.details["backend"] = "ALSA"

        if pa:
            _, sinks = _run("pactl list sinks short 2>/dev/null")
            _, sources = _run("pactl list sources short 2>/dev/null")
            sink_count = len([l for l in sinks.splitlines() if l.strip()])
            source_count = len([l for l in sources.splitlines() if l.strip()])
            h.details["sinks"] = sink_count
            h.details["sources"] = source_count
            # Проверяем mute
            _, mute_out = _run("pactl get-sink-mute @DEFAULT_SINK@ 2>/dev/null")
            muted = "yes" in mute_out.lower()
            h.details["muted"] = muted

            if sink_count == 0:
                h.level = HealthLevel.CRITICAL
                h.summary = "Нет аудио устройств вывода"
                s.problems.append("Аудио: нет sinks")
            elif muted:
                h.level = HealthLevel.WARNING
                h.summary = f"Звук отключён (muted), {sink_count} устройств"
            else:
                h.level = HealthLevel.OK
                h.summary = f"{h.details['backend']}: {sink_count} out, {source_count} in"
        else:
            h.level = HealthLevel.OK
            h.summary = h.details["backend"]

    # ─── GPU ──────────────────────────────────────────────────

    def _scan_gpu(self, s: SystemState) -> None:
        h = s.gpu
        _, lspci = _run("lspci 2>/dev/null | grep -iE 'vga|3d|display'")
        gpus = [line.strip() for line in lspci.splitlines() if line.strip()]
        h.details["gpus"] = gpus

        # nvidia
        if shutil.which("nvidia-smi"):
            rc, out = _run("nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null")
            if rc == 0 and out.strip():
                h.details["nvidia"] = out.strip()
                # Парсим температуру
                parts = out.split(",")
                if len(parts) >= 2:
                    try:
                        temp = int(parts[1].strip().split()[0])
                        if temp > 95:
                            h.level = HealthLevel.CRITICAL
                            h.summary = f"GPU перегрев: {temp}°C"
                            s.problems.append(f"GPU перегрев: {temp}°C")
                            return
                    except (ValueError, IndexError):
                        pass

        h.level = HealthLevel.OK
        h.summary = f"{len(gpus)} GPU: {gpus[0][:50] if gpus else 'не найдено'}"

    # ─── Дисплей ──────────────────────────────────────────────

    def _scan_display(self, s: SystemState) -> None:
        h = s.display
        h.details["server"] = s.display_server
        if s.display_server == "Wayland":
            _, out = _run("wlr-randr 2>/dev/null || kscreen-doctor -o 2>/dev/null || echo 'no tool'")
        else:
            _, out = _run("xrandr --current 2>/dev/null | head -10")
        h.raw_output = out
        monitors = len(re.findall(r"\bconnected\b", out))
        h.details["monitors"] = monitors
        h.level = HealthLevel.OK
        h.summary = f"{s.display_server}, {monitors} мониторов"

    # ─── Пакеты ───────────────────────────────────────────────

    def _scan_packages(self, s: SystemState) -> None:
        h = s.packages
        if shutil.which("pacman"):
            h.details["manager"] = "pacman"
            _, out = _run("pacman -Qk 2>&1 | grep -c warning || true")
            broken = int(out) if out.isdigit() else 0
            h.details["broken"] = broken
            # Lock
            lock = os.path.exists("/var/lib/pacman/db.lck")
            h.details["lock"] = lock
            if lock:
                h.level = HealthLevel.WARNING
                h.summary = "Lock-файл pacman активен"
                return
        elif shutil.which("apt"):
            h.details["manager"] = "apt"
            _, out = _run("dpkg --audit 2>/dev/null | head -5")
            broken = len([l for l in out.splitlines() if l.strip()])
            h.details["broken"] = broken
            lock = os.path.exists("/var/lib/dpkg/lock-frontend")
            h.details["lock"] = lock
        elif shutil.which("dnf"):
            h.details["manager"] = "dnf"
            broken = 0
        else:
            h.details["manager"] = "unknown"
            broken = 0

        if broken > 0:
            h.level = HealthLevel.WARNING
            h.summary = f"{h.details['manager']}: {broken} проблемных пакетов"
        else:
            h.level = HealthLevel.OK
            h.summary = f"{h.details.get('manager', '?')} — пакеты в порядке"

    # ─── Обновления ───────────────────────────────────────────

    def _scan_updates(self, s: SystemState) -> None:
        h = s.updates
        if shutil.which("checkupdates"):
            # Arch
            _, out = _run("checkupdates 2>/dev/null | wc -l", timeout=30)
            count = int(out) if out.isdigit() else 0
        elif shutil.which("apt"):
            _, out = _run("apt list --upgradable 2>/dev/null | grep -c upgradable || echo 0", timeout=30)
            count = int(out) if out.isdigit() else 0
        elif shutil.which("dnf"):
            _, out = _run("dnf check-update --quiet 2>/dev/null | wc -l", timeout=30)
            count = int(out) if out.isdigit() else 0
        else:
            count = -1

        h.details["pending"] = count
        if count < 0:
            h.level = HealthLevel.UNKNOWN
            h.summary = "Невозможно проверить обновления"
        elif count > 100:
            h.level = HealthLevel.WARNING
            h.summary = f"{count} ожидающих обновлений"
        else:
            h.level = HealthLevel.OK
            h.summary = f"{count} обновлений" if count else "Система обновлена"

    # ─── Bluetooth ────────────────────────────────────────────

    def _scan_bluetooth(self, s: SystemState) -> None:
        h = s.bluetooth
        if not shutil.which("bluetoothctl"):
            h.level = HealthLevel.UNKNOWN
            h.summary = "bluetoothctl не установлен"
            return
        _, out = _run("bluetoothctl show 2>/dev/null")
        powered = "Powered: yes" in out
        h.details["powered"] = powered
        _, devices = _run("bluetoothctl devices 2>/dev/null")
        device_list = [l for l in devices.splitlines() if l.strip()]
        h.details["devices"] = len(device_list)
        h.level = HealthLevel.OK
        h.summary = f"{'Вкл' if powered else 'Выкл'}, {len(device_list)} устройств"

    # ─── Cron Jobs ─────────────────────────────────────────────

    def _scan_cron(self, s: SystemState) -> None:
        h = s.cron
        jobs: List[str] = []
        # System crontabs
        _, out = _run("cat /etc/crontab 2>/dev/null | grep -v '^#' | grep -v '^$'")
        if out:
            jobs.extend(out.splitlines())
        # User crontab
        _, out = _run("crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$'")
        if out:
            jobs.extend(out.splitlines())
        # Systemd timers
        _, timers = _run("systemctl list-timers --no-pager --plain 2>/dev/null | tail -n +2 | head -20")
        timer_count = len([l for l in timers.splitlines() if l.strip()]) if timers else 0

        h.details["cron_jobs"] = len(jobs)
        h.details["systemd_timers"] = timer_count
        h.level = HealthLevel.OK
        h.summary = f"{len(jobs)} cron задач, {timer_count} таймеров systemd"

    # ─── Mounted Devices ──────────────────────────────────────

    def _scan_mounts(self, s: SystemState) -> None:
        h = s.mounts
        _, out = _run("findmnt --real --noheadings --output TARGET,SOURCE,FSTYPE,OPTIONS -l 2>/dev/null")
        mounts: List[Dict[str, str]] = []
        problems: List[str] = []
        for line in (out or "").splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 3:
                entry = {"target": parts[0], "source": parts[1], "fstype": parts[2]}
                if len(parts) > 3:
                    entry["options"] = parts[3]
                mounts.append(entry)
                # Check for ro mounts on writable paths
                opts = parts[3] if len(parts) > 3 else ""
                if parts[0] in ("/", "/home", "/var") and "ro," in opts:
                    problems.append(f"{parts[0]} примонтирован read-only")

        h.details["mounts"] = len(mounts)
        h.details["filesystems"] = list({m.get("fstype", "") for m in mounts})

        if problems:
            h.level = HealthLevel.CRITICAL
            h.summary = f"{len(mounts)} точек монтирования, ПРОБЛЕМЫ: {'; '.join(problems)}"
            s.problems.extend(problems)
        else:
            h.level = HealthLevel.OK
            h.summary = f"{len(mounts)} точек монтирования"

    # ─── User Permissions ─────────────────────────────────────

    def _scan_permissions(self, s: SystemState) -> None:
        h = s.permissions
        import getpass
        user = getpass.getuser()
        _, groups_out = _run(f"groups {user} 2>/dev/null")
        groups = groups_out.split(":")[1].split() if ":" in groups_out else groups_out.split()

        h.details["user"] = user
        h.details["groups"] = groups

        # Check important groups
        important = {"wheel", "sudo", "audio", "video", "input", "network", "docker"}
        missing = important - set(groups)
        warnings: List[str] = []

        # SUID world-writable files in /tmp (security concern)
        _, suid = _run("find /tmp -maxdepth 2 -perm -4000 -type f 2>/dev/null | head -5", timeout=5)
        if suid.strip():
            suid_files = [f.strip() for f in suid.splitlines() if f.strip()]
            if suid_files:
                warnings.append(f"SUID файлы в /tmp: {len(suid_files)}")
                h.details["suid_in_tmp"] = suid_files

        h.details["missing_groups"] = list(missing)
        if warnings:
            h.level = HealthLevel.WARNING
            h.summary = f"Пользователь {user}, {len(groups)} групп; " + "; ".join(warnings)
        else:
            h.level = HealthLevel.OK
            h.summary = f"Пользователь {user}, {len(groups)} групп"

    # ─── Агрегация ────────────────────────────────────────────

    def _aggregate(self, s: SystemState) -> None:
        """Определяет общий уровень здоровья."""
        levels = []
        for name in (
            "cpu", "ram", "disk", "smart", "temperatures", "services",
            "processes", "network", "firewall", "audio", "gpu", "display",
            "packages", "updates", "bluetooth", "cron", "mounts", "permissions",
        ):
            sh: SubsystemHealth = getattr(s, name)
            levels.append(sh.level)

        if HealthLevel.CRITICAL in levels:
            s.overall = HealthLevel.CRITICAL
        elif HealthLevel.WARNING in levels:
            s.overall = HealthLevel.WARNING
        elif all(l == HealthLevel.OK for l in levels):
            s.overall = HealthLevel.OK
        else:
            s.overall = HealthLevel.OK  # UNKNOWN подсистемы не портят общий статус


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_scanner: Optional[SystemStateScanner] = None


def get_scanner() -> SystemStateScanner:
    global _scanner
    if _scanner is None:
        _scanner = SystemStateScanner()
    return _scanner
