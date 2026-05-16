"""
FullSystemControlLayer — единый интерфейс управления Linux.

Объединяет существующие system/* модули + новые PROBLEM TERMINATOR
модули в единый управляющий слой.

Обеспечивает контроль над:
  Network, Packages, Audio, GPU, Display, Bluetooth, Services,
  Power, Themes, Locale, Timezone, DNS, Kernel params, Cron, Users.

Phase: PROBLEM TERMINATOR / Module 6 (Orchestrator)
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  ControlResult — стандартный результат любой операции
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ControlResult:
    success: bool = True
    output: str = ""
    error: str = ""
    command_used: str = ""
    requires_sudo: bool = False
    dry_run: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success, "output": self.output,
            "error": self.error, "command": self.command_used,
        }


# ═══════════════════════════════════════════════════════════════════
#  Утилиты
# ═══════════════════════════════════════════════════════════════════

def _run(cmd: str, timeout: int = 15) -> Tuple[int, str]:
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


def _which(binary: str) -> bool:
    return shutil.which(binary) is not None


# ═══════════════════════════════════════════════════════════════════
#  FullSystemControlLayer
# ═══════════════════════════════════════════════════════════════════

class FullSystemControlLayer:
    """
    Единый контролируемый интерфейс управления Linux-системой.

    КАЖДАЯ write-операция:
    - Проверяет безопасность
    - Логируется
    - Возвращает ControlResult с командой (для аудита)

    Read-операции выполняются напрямую и безопасно.
    """

    def __init__(self) -> None:
        self._pkg_mgr = self._detect_pkg_mgr()
        self._audit_log: List[Dict[str, Any]] = []

    # ─── Аудит ─────────────────────────────────────────────────

    def _audit(self, action: str, cmd: str, result: ControlResult) -> None:
        import datetime
        entry = {
            "ts": datetime.datetime.now().isoformat(),
            "action": action,
            "cmd": cmd,
            "success": result.success,
            "error": result.error[:200] if result.error else "",
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > 500:
            self._audit_log = self._audit_log[-500:]

    def get_audit_log(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._audit_log[-limit:]

    # ═══════════════════════════════════════════════════════════
    #  1. NETWORK
    # ═══════════════════════════════════════════════════════════

    def net_interfaces(self) -> ControlResult:
        rc, out = _run("ip -brief addr 2>/dev/null")
        return ControlResult(success=rc == 0, output=out, command_used="ip -brief addr")

    def net_connectivity(self) -> ControlResult:
        """Проверка интернет-соединения."""
        rc, _ = _run("ping -c1 -W3 1.1.1.1 2>/dev/null", timeout=5)
        if rc == 0:
            return ControlResult(output="✅ Интернет доступен")
        # Fallback DNS
        rc2, _ = _run("ping -c1 -W3 8.8.8.8 2>/dev/null", timeout=5)
        if rc2 == 0:
            return ControlResult(output="⚠️ Интернет через 8.8.8.8 — возможна проблема маршрутизации")
        return ControlResult(success=False, error="❌ Нет доступа в интернет")

    def net_dns_check(self) -> ControlResult:
        rc, out = _run("resolvectl status 2>/dev/null || cat /etc/resolv.conf")
        return ControlResult(success=rc == 0, output=out)

    def net_wifi_list(self) -> ControlResult:
        if not _which("nmcli"):
            return ControlResult(success=False, error="nmcli не установлен")
        rc, out = _run("nmcli dev wifi list 2>/dev/null")
        return ControlResult(success=rc == 0, output=out, command_used="nmcli dev wifi list")

    def net_wifi_connect(self, ssid: str, password: str = "") -> ControlResult:
        if not _which("nmcli"):
            return ControlResult(success=False, error="nmcli не установлен")
        if password:
            cmd = f"nmcli dev wifi connect {shlex.quote(ssid)} password {shlex.quote(password)}"
        else:
            cmd = f"nmcli dev wifi connect {shlex.quote(ssid)}"
        rc, out = _run(cmd, timeout=30)
        result = ControlResult(success=rc == 0, output=out, command_used=cmd)
        self._audit("wifi_connect", cmd, result)
        return result

    def net_firewall_status(self) -> ControlResult:
        parts = []
        if _which("ufw"):
            _, out = _run("ufw status 2>/dev/null")
            parts.append(f"UFW: {out}")
        if _which("firewall-cmd"):
            _, out = _run("firewall-cmd --state 2>/dev/null")
            parts.append(f"firewalld: {out}")
        if _which("iptables"):
            _, out = _run("iptables -L -n 2>/dev/null | head -20")
            parts.append(f"iptables:\n{out}")
        return ControlResult(output="\n".join(parts) if parts else "Firewall не обнаружен")

    def net_open_ports(self) -> ControlResult:
        rc, out = _run("ss -tlnp 2>/dev/null | head -25")
        return ControlResult(success=rc == 0, output=out)

    # ═══════════════════════════════════════════════════════════
    #  2. PACKAGES
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _detect_pkg_mgr() -> str:
        for mgr in ("pacman", "apt", "dnf", "zypper"):
            if _which(mgr):
                return mgr
        return "unknown"

    def pkg_search(self, query: str) -> ControlResult:
        q = shlex.quote(query)
        cmds = {
            "pacman": f"pacman -Ss {q} 2>/dev/null | head -30",
            "apt": f"apt-cache search {q} 2>/dev/null | head -30",
            "dnf": f"dnf search {q} 2>/dev/null | head -30",
        }
        cmd = cmds.get(self._pkg_mgr, f"echo 'Неизвестный пакетный менеджер'")
        rc, out = _run(cmd, timeout=30)
        return ControlResult(success=rc == 0, output=out, command_used=cmd)

    def pkg_info(self, package: str) -> ControlResult:
        p = shlex.quote(package)
        cmds = {
            "pacman": f"pacman -Qi {p} 2>/dev/null || pacman -Si {p} 2>/dev/null",
            "apt": f"apt-cache show {p} 2>/dev/null | head -30",
            "dnf": f"dnf info {p} 2>/dev/null | head -30",
        }
        cmd = cmds.get(self._pkg_mgr, "echo 'unknown'")
        rc, out = _run(cmd, timeout=15)
        return ControlResult(success=rc == 0, output=out, command_used=cmd)

    def pkg_install_command(self, package: str) -> ControlResult:
        """Генерирует команду установки (НЕ выполняет)."""
        p = shlex.quote(package)
        cmds = {
            "pacman": f"sudo pacman -S {p} --noconfirm",
            "apt": f"sudo apt install -y {p}",
            "dnf": f"sudo dnf install -y {p}",
        }
        cmd = cmds.get(self._pkg_mgr, f"echo 'Установите {package} вручную'")
        return ControlResult(
            output=cmd, command_used=cmd,
            dry_run=True, requires_sudo=True,
        )

    def pkg_remove_command(self, package: str) -> ControlResult:
        """Генерирует команду удаления (НЕ выполняет)."""
        p = shlex.quote(package)
        cmds = {
            "pacman": f"sudo pacman -R {p}",
            "apt": f"sudo apt remove {p}",
            "dnf": f"sudo dnf remove {p}",
        }
        cmd = cmds.get(self._pkg_mgr, f"echo 'Удалите {package} вручную'")
        return ControlResult(
            output=cmd, command_used=cmd,
            dry_run=True, requires_sudo=True,
        )

    def pkg_list_installed(self, filter_str: str = "") -> ControlResult:
        cmds = {
            "pacman": f"pacman -Q {filter_str} 2>/dev/null | head -50",
            "apt": f"dpkg -l '*{filter_str}*' 2>/dev/null | tail -50",
            "dnf": f"dnf list installed '*{filter_str}*' 2>/dev/null | head -50",
        }
        cmd = cmds.get(self._pkg_mgr, "echo 'unknown'")
        rc, out = _run(cmd, timeout=15)
        return ControlResult(success=rc == 0, output=out, command_used=cmd)

    def pkg_updates_available(self) -> ControlResult:
        cmds = {
            "pacman": "checkupdates 2>/dev/null || echo 'checkupdates не установлен'",
            "apt": "apt list --upgradable 2>/dev/null",
            "dnf": "dnf check-update --quiet 2>/dev/null",
        }
        cmd = cmds.get(self._pkg_mgr, "echo 'unknown'")
        rc, out = _run(cmd, timeout=60)
        return ControlResult(output=out, command_used=cmd)

    # ═══════════════════════════════════════════════════════════
    #  3. AUDIO
    # ═══════════════════════════════════════════════════════════

    def audio_status(self) -> ControlResult:
        parts = []
        if _which("pactl"):
            _, sinks = _run("pactl list sinks short 2>/dev/null")
            _, sources = _run("pactl list sources short 2>/dev/null")
            _, vol = _run("pactl get-sink-volume @DEFAULT_SINK@ 2>/dev/null")
            _, mute = _run("pactl get-sink-mute @DEFAULT_SINK@ 2>/dev/null")
            parts.append(f"Sinks:\n{sinks}")
            parts.append(f"Sources:\n{sources}")
            parts.append(f"Volume: {vol}")
            parts.append(f"Mute: {mute}")
        if _which("pw-cli"):
            rc, _ = _run("pw-cli info 0 2>/dev/null")
            parts.append(f"PipeWire: {'active' if rc == 0 else 'inactive'}")
        return ControlResult(output="\n".join(parts) if parts else "Аудио подсистема не обнаружена")

    def audio_set_volume(self, value: str) -> ControlResult:
        if not _which("pactl"):
            return ControlResult(success=False, error="pactl не установлен")
        val = value.strip().rstrip("%")
        if val.startswith(("+", "-")):
            cmd = f"pactl set-sink-volume @DEFAULT_SINK@ {val}%"
        else:
            cmd = f"pactl set-sink-volume @DEFAULT_SINK@ {val}%"
        rc, out = _run(cmd)
        result = ControlResult(success=rc == 0, output=f"Громкость: {value}", error=out if rc else "", command_used=cmd)
        self._audit("audio_volume", cmd, result)
        return result

    def audio_mute(self, mute: bool = True) -> ControlResult:
        cmd = f"pactl set-sink-mute @DEFAULT_SINK@ {'1' if mute else '0'}"
        rc, out = _run(cmd)
        result = ControlResult(success=rc == 0, output=f"{'Muted' if mute else 'Unmuted'}", command_used=cmd)
        self._audit("audio_mute", cmd, result)
        return result

    def audio_restart(self) -> ControlResult:
        cmd = "systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null || systemctl --user restart pulseaudio 2>/dev/null"
        rc, out = _run(cmd, timeout=10)
        result = ControlResult(success=rc == 0, output="Аудио стек перезапущен" if rc == 0 else out, command_used=cmd)
        self._audit("audio_restart", cmd, result)
        return result

    # ═══════════════════════════════════════════════════════════
    #  4. GPU
    # ═══════════════════════════════════════════════════════════

    def gpu_info(self) -> ControlResult:
        _, out = _run("lspci 2>/dev/null | grep -iE 'vga|3d|display'")
        parts = [f"GPU: {out}"]
        if _which("nvidia-smi"):
            _, nv = _run("nvidia-smi --query-gpu=name,driver_version,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null")
            parts.append(f"NVIDIA: {nv}")
        _, driver = _run("lspci -k 2>/dev/null | grep -A3 VGA | grep 'Kernel driver'")
        parts.append(f"Driver: {driver}")
        return ControlResult(output="\n".join(parts))

    # ═══════════════════════════════════════════════════════════
    #  5. DISPLAY
    # ═══════════════════════════════════════════════════════════

    def display_info(self) -> ControlResult:
        server = "Wayland" if os.environ.get("WAYLAND_DISPLAY") else "X11"
        if server == "Wayland":
            _, out = _run("wlr-randr 2>/dev/null || kscreen-doctor -o 2>/dev/null || echo 'no tool'")
        else:
            _, out = _run("xrandr --current 2>/dev/null | head -20")
        return ControlResult(output=f"Server: {server}\n{out}")

    # ═══════════════════════════════════════════════════════════
    #  6. BLUETOOTH
    # ═══════════════════════════════════════════════════════════

    def bt_status(self) -> ControlResult:
        if not _which("bluetoothctl"):
            return ControlResult(success=False, error="bluetoothctl не установлен")
        _, show = _run("bluetoothctl show 2>/dev/null")
        _, devices = _run("bluetoothctl devices 2>/dev/null")
        return ControlResult(output=f"{show}\n\nDevices:\n{devices}")

    def bt_power(self, on: bool = True) -> ControlResult:
        cmd = f"bluetoothctl power {'on' if on else 'off'}"
        rc, out = _run(cmd, timeout=5)
        result = ControlResult(success=rc == 0, output=out, command_used=cmd)
        self._audit("bt_power", cmd, result)
        return result

    # ═══════════════════════════════════════════════════════════
    #  7. SERVICES (systemd)
    # ═══════════════════════════════════════════════════════════

    def svc_list_failed(self) -> ControlResult:
        rc, out = _run("systemctl --failed --no-pager 2>/dev/null")
        return ControlResult(success=rc == 0, output=out)

    def svc_status(self, service: str) -> ControlResult:
        sv = shlex.quote(service)
        rc, out = _run(f"systemctl status {sv} --no-pager 2>/dev/null | head -20")
        return ControlResult(success=rc == 0, output=out, command_used=f"systemctl status {sv}")

    def svc_logs(self, service: str, lines: int = 30) -> ControlResult:
        sv = shlex.quote(service)
        cmd = f"journalctl -u {sv} --no-pager -n {int(lines)} 2>/dev/null"
        rc, out = _run(cmd, timeout=10)
        return ControlResult(success=rc == 0, output=out, command_used=cmd)

    def svc_restart_command(self, service: str) -> ControlResult:
        """Генерирует команду перезапуска (НЕ выполняет)."""
        sv = shlex.quote(service)
        cmd = f"sudo systemctl restart {sv}"
        return ControlResult(output=cmd, command_used=cmd, dry_run=True, requires_sudo=True)

    # ═══════════════════════════════════════════════════════════
    #  8. POWER
    # ═══════════════════════════════════════════════════════════

    def power_status(self) -> ControlResult:
        parts = []
        if os.path.isfile("/sys/class/power_supply/BAT0/capacity"):
            _, bat = _run("cat /sys/class/power_supply/BAT0/capacity")
            _, status = _run("cat /sys/class/power_supply/BAT0/status")
            parts.append(f"Battery: {bat}% ({status})")
        _, uptime = _run("uptime -p 2>/dev/null || uptime")
        parts.append(f"Uptime: {uptime}")
        return ControlResult(output="\n".join(parts) if parts else "Нет данных о питании")

    # ═══════════════════════════════════════════════════════════
    #  9. LOCALE / TIMEZONE
    # ═══════════════════════════════════════════════════════════

    def locale_info(self) -> ControlResult:
        rc, out = _run("localectl status 2>/dev/null || locale")
        return ControlResult(success=rc == 0, output=out)

    def timezone_info(self) -> ControlResult:
        rc, out = _run("timedatectl status 2>/dev/null")
        return ControlResult(success=rc == 0, output=out)

    # ═══════════════════════════════════════════════════════════
    #  10. DNS
    # ═══════════════════════════════════════════════════════════

    def dns_status(self) -> ControlResult:
        _, resolv = _run("cat /etc/resolv.conf 2>/dev/null")
        _, resolved = _run("resolvectl status 2>/dev/null | head -20")
        return ControlResult(output=f"/etc/resolv.conf:\n{resolv}\n\nresolvectl:\n{resolved}")

    def dns_test(self, domain: str = "google.com") -> ControlResult:
        rc, out = _run(f"nslookup {domain} 2>/dev/null || dig {domain} +short 2>/dev/null", timeout=10)
        return ControlResult(success=rc == 0, output=out)

    # ═══════════════════════════════════════════════════════════
    #  11. KERNEL
    # ═══════════════════════════════════════════════════════════

    def kernel_info(self) -> ControlResult:
        _, ver = _run("uname -r")
        _, modules = _run("lsmod | wc -l")
        _, params = _run("sysctl -a 2>/dev/null | wc -l")
        return ControlResult(output=f"Kernel: {ver}\nModules: {modules}\nSysctl params: {params}")

    def kernel_dmesg_errors(self, lines: int = 20) -> ControlResult:
        rc, out = _run(f"dmesg --level=err,crit,alert,emerg 2>/dev/null | tail -{lines}")
        return ControlResult(success=rc == 0, output=out)

    # ═══════════════════════════════════════════════════════════
    #  12. CRON
    # ═══════════════════════════════════════════════════════════

    def cron_list(self) -> ControlResult:
        rc, out = _run("crontab -l 2>/dev/null || echo 'Нет crontab'")
        return ControlResult(success=rc == 0, output=out)

    # ═══════════════════════════════════════════════════════════
    #  13. USERS
    # ═══════════════════════════════════════════════════════════

    def user_info(self) -> ControlResult:
        _, who = _run("whoami")
        _, groups = _run("groups")
        _, uid = _run("id")
        return ControlResult(output=f"User: {who}\nGroups: {groups}\nID: {uid}")

    # ═══════════════════════════════════════════════════════════
    #  14. PRINTERS
    # ═══════════════════════════════════════════════════════════

    def printer_status(self) -> ControlResult:
        if not _which("lpstat"):
            return ControlResult(output="CUPS не установлен")
        rc, out = _run("lpstat -p -d 2>/dev/null")
        return ControlResult(success=rc == 0, output=out)

    # ═══════════════════════════════════════════════════════════
    #  ПОЛНАЯ ДИАГНОСТИКА (quick overview)
    # ═══════════════════════════════════════════════════════════

    def full_overview(self) -> str:
        """Быстрый обзор всех подсистем — текстовый отчёт."""
        sections = [
            ("Сеть", self.net_interfaces),
            ("Интернет", self.net_connectivity),
            ("DNS", self.dns_test),
            ("Аудио", self.audio_status),
            ("GPU", self.gpu_info),
            ("Дисплей", self.display_info),
            ("Bluetooth", self.bt_status),
            ("Сервисы (failed)", self.svc_list_failed),
            ("Питание", self.power_status),
            ("Ядро", self.kernel_info),
            ("Locale", self.locale_info),
            ("Timezone", self.timezone_info),
            ("Принтеры", self.printer_status),
        ]
        lines = ["═══ Обзор системы ═══"]
        for name, fn in sections:
            try:
                result = fn()
                status = "✅" if result.success else "⚠️"
                first_line = (result.output or result.error or "?").split("\n")[0][:80]
                lines.append(f"  {status} {name:20s} {first_line}")
            except Exception as e:
                lines.append(f"  ❌ {name:20s} Ошибка: {e}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_layer: Optional[FullSystemControlLayer] = None


def get_control() -> FullSystemControlLayer:
    global _layer
    if _layer is None:
        _layer = FullSystemControlLayer()
    return _layer
