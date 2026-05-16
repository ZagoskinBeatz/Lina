"""
Lina Display Manager — управление мониторами и дисплеем.

Модуль C8 из ПЛАН_РАБОТ_75.txt:
- Определение графического окружения (Wayland/X11)
- Информация о мониторах (разрешение, частота)
- Определение GPU и драйвера
- Определение compositor'а
- Диагностика типичных проблем дисплея
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


# ───────────────────────── Enums ──────────────────────────────────────────

class DisplayServer(Enum):
    """Тип дисплейного сервера."""
    WAYLAND = "wayland"
    X11 = "x11"
    UNKNOWN = "unknown"


class GPUVendor(Enum):
    """Производитель GPU."""
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    UNKNOWN = "unknown"


class DriverType(Enum):
    """Тип драйвера GPU."""
    PROPRIETARY = "proprietary"
    OPEN_SOURCE = "open_source"
    UNKNOWN = "unknown"


# ───────────────────────── Data classes ───────────────────────────────────

@dataclass
class MonitorInfo:
    """Информация о мониторе."""
    name: str = ""
    model: str = ""
    resolution: str = ""
    width: int = 0
    height: int = 0
    refresh_rate: float = 0.0
    scale: float = 1.0
    primary: bool = False
    connected: bool = True
    position: str = "0x0"
    rotation: str = "normal"


@dataclass
class GPUInfo:
    """Информация о GPU."""
    name: str = ""
    vendor: GPUVendor = GPUVendor.UNKNOWN
    driver: str = ""
    driver_type: DriverType = DriverType.UNKNOWN
    vram_mb: int = 0
    temperature: Optional[float] = None
    pci_id: str = ""
    kernel_module: str = ""


@dataclass
class CompositorInfo:
    """Информация о композиторе."""
    name: str = ""
    version: str = ""
    desktop_environment: str = ""
    session_type: str = ""


@dataclass
class DisplayIssue:
    """Описание проблемы дисплея."""
    severity: str = "info"  # info, warning, error
    category: str = ""
    description: str = ""
    suggestion: str = ""
    command: str = ""


@dataclass
class DisplaySummary:
    """Полная сводка о дисплейной подсистеме."""
    display_server: DisplayServer = DisplayServer.UNKNOWN
    compositor: CompositorInfo = field(default_factory=CompositorInfo)
    monitors: List[MonitorInfo] = field(default_factory=list)
    gpus: List[GPUInfo] = field(default_factory=list)
    issues: List[DisplayIssue] = field(default_factory=list)


# ───────────────────────── Helpers ────────────────────────────────────────

def _run(cmd: str, timeout: int = 5) -> str:
    """Безопасный запуск команды."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return (r.stdout + r.stderr).strip()
    except Exception:
        return ""


def _env(key: str) -> str:
    """Получить переменную окружения."""
    return os.environ.get(key, "")


# ───────────────────────── Display Server ─────────────────────────────────

def detect_display_server() -> DisplayServer:
    """Определяет Wayland или X11."""
    xdg = _env("XDG_SESSION_TYPE").lower()
    if "wayland" in xdg:
        return DisplayServer.WAYLAND
    if "x11" in xdg:
        return DisplayServer.X11

    wayland_display = _env("WAYLAND_DISPLAY")
    if wayland_display:
        return DisplayServer.WAYLAND

    display = _env("DISPLAY")
    if display:
        return DisplayServer.X11

    loginctl = _run("loginctl show-session $(loginctl | grep $(whoami) | awk '{print $1}') -p Type 2>/dev/null")
    if "wayland" in loginctl.lower():
        return DisplayServer.WAYLAND
    if "x11" in loginctl.lower():
        return DisplayServer.X11

    return DisplayServer.UNKNOWN


# ───────────────────────── Compositor ─────────────────────────────────────

def detect_compositor() -> CompositorInfo:
    """Определяет compositor / DE."""
    info = CompositorInfo()

    # Desktop Environment
    de = _env("XDG_CURRENT_DESKTOP") or _env("DESKTOP_SESSION") or ""
    info.desktop_environment = de

    # Session type
    info.session_type = _env("XDG_SESSION_TYPE") or ""

    # Compositor name
    compositors = {
        "kwin": "KWin",
        "mutter": "Mutter",
        "sway": "Sway",
        "hyprland": "Hyprland",
        "wayfire": "Wayfire",
        "wlroots": "wlroots",
        "picom": "Picom",
        "compton": "Compton",
        "xfwm": "Xfwm4",
        "marco": "Marco",
        "compiz": "Compiz",
        "labwc": "LabWC",
        "river": "River",
        "niri": "Niri",
    }

    # Check running processes
    ps_output = _run("ps -eo comm 2>/dev/null")
    for key, name in compositors.items():
        if key in ps_output.lower():
            info.name = name
            break

    if not info.name:
        # Infer from DE
        de_lower = de.lower()
        if "kde" in de_lower or "plasma" in de_lower:
            info.name = "KWin"
        elif "gnome" in de_lower:
            info.name = "Mutter"
        elif "xfce" in de_lower:
            info.name = "Xfwm4"
        elif "mate" in de_lower:
            info.name = "Marco"

    # Try to get version
    if info.name:
        name_lower = info.name.lower()
        ver = _run(f"{name_lower} --version 2>/dev/null")
        if ver:
            m = re.search(r"(\d+\.\d+(?:\.\d+)?)", ver)
            if m:
                info.version = m.group(1)

    return info


# ───────────────────────── Monitors (Wayland) ────────────────────────────

def _parse_wlr_randr(output: str) -> List[MonitorInfo]:
    """Парсим вывод wlr-randr."""
    monitors: List[MonitorInfo] = []
    current: Optional[MonitorInfo] = None

    for line in output.splitlines():
        stripped = line.strip()
        # New monitor block: starts without indentation
        if not line.startswith(" ") and stripped and not stripped.startswith("---"):
            if current:
                monitors.append(current)
            current = MonitorInfo(name=stripped.split()[0] if stripped.split() else "")
            if "(" in stripped:
                model_match = re.search(r"\((.+?)\)", stripped)
                if model_match:
                    current.model = model_match.group(1)
        elif current and stripped:
            if stripped.startswith("current"):
                res_match = re.search(r"(\d+)x(\d+)", stripped)
                if res_match:
                    current.width = int(res_match.group(1))
                    current.height = int(res_match.group(2))
                    current.resolution = f"{current.width}x{current.height}"
                hz_match = re.search(r"([\d.]+)\s*Hz", stripped)
                if hz_match:
                    current.refresh_rate = float(hz_match.group(1))
            elif "scale" in stripped.lower():
                scale_match = re.search(r"([\d.]+)", stripped)
                if scale_match:
                    current.scale = float(scale_match.group(1))
            elif "position" in stripped.lower():
                pos_match = re.search(r"(\d+),(\d+)", stripped)
                if pos_match:
                    current.position = f"{pos_match.group(1)}x{pos_match.group(2)}"

    if current:
        monitors.append(current)
    return monitors


# ───────────────────────── Monitors (X11) ─────────────────────────────────

def _parse_xrandr(output: str) -> List[MonitorInfo]:
    """Парсим вывод xrandr."""
    monitors: List[MonitorInfo] = []
    current: Optional[MonitorInfo] = None

    for line in output.splitlines():
        # Connected/disconnected line
        conn_match = re.match(
            r"^(\S+)\s+(connected|disconnected)\s*(primary)?\s*(\d+x\d+\+\d+\+\d+)?",
            line,
        )
        if conn_match:
            if current:
                monitors.append(current)
            current = MonitorInfo(
                name=conn_match.group(1),
                connected=conn_match.group(2) == "connected",
                primary=conn_match.group(3) == "primary" if conn_match.group(3) else False,
            )
            if conn_match.group(4):
                geom = conn_match.group(4)
                res_match = re.match(r"(\d+)x(\d+)\+(\d+)\+(\d+)", geom)
                if res_match:
                    current.width = int(res_match.group(1))
                    current.height = int(res_match.group(2))
                    current.resolution = f"{current.width}x{current.height}"
                    current.position = f"{res_match.group(3)}x{res_match.group(4)}"
        elif current and current.connected and "*" in line:
            # Current mode line with asterisk
            mode_match = re.match(r"\s+(\d+)x(\d+)\s+([\d.]+)\*", line)
            if mode_match:
                if not current.resolution:
                    current.width = int(mode_match.group(1))
                    current.height = int(mode_match.group(2))
                    current.resolution = f"{current.width}x{current.height}"
                current.refresh_rate = float(mode_match.group(3))

    if current:
        monitors.append(current)

    # Только подключённые
    return [m for m in monitors if m.connected]


def list_monitors() -> List[MonitorInfo]:
    """Возвращает список подключённых мониторов."""
    ds = detect_display_server()

    if ds == DisplayServer.WAYLAND:
        out = _run("wlr-randr 2>/dev/null")
        if out:
            return _parse_wlr_randr(out)

    # X11 fallback
    out = _run("xrandr --current 2>/dev/null")
    if out:
        return _parse_xrandr(out)

    return []


def get_resolution() -> str:
    """Текущее разрешение основного монитора."""
    monitors = list_monitors()
    for m in monitors:
        if m.primary and m.resolution:
            return m.resolution
    if monitors and monitors[0].resolution:
        return monitors[0].resolution
    return "unknown"


def get_refresh_rate() -> float:
    """Частота обновления основного монитора."""
    monitors = list_monitors()
    for m in monitors:
        if m.primary and m.refresh_rate > 0:
            return m.refresh_rate
    if monitors and monitors[0].refresh_rate > 0:
        return monitors[0].refresh_rate
    return 0.0


# ───────────────────────── GPU ────────────────────────────────────────────

def _detect_gpu_vendor(name: str) -> GPUVendor:
    """Определяет вендора GPU по имени."""
    low = name.lower()
    if "nvidia" in low or "geforce" in low or "quadro" in low or "rtx" in low:
        return GPUVendor.NVIDIA
    if "amd" in low or "radeon" in low or "rx " in low or "navi" in low:
        return GPUVendor.AMD
    if "intel" in low or "iris" in low or "uhd" in low or "hd graphics" in low:
        return GPUVendor.INTEL
    return GPUVendor.UNKNOWN


def _detect_driver_type(driver: str, vendor: GPUVendor) -> DriverType:
    """Определяет тип драйвера."""
    low = driver.lower()
    if vendor == GPUVendor.NVIDIA:
        if "nouveau" in low:
            return DriverType.OPEN_SOURCE
        if "nvidia" in low:
            return DriverType.PROPRIETARY
    elif vendor == GPUVendor.AMD:
        if "amdgpu" in low:
            return DriverType.OPEN_SOURCE
        if "fglrx" in low or "amdgpu-pro" in low:
            return DriverType.PROPRIETARY
    elif vendor == GPUVendor.INTEL:
        return DriverType.OPEN_SOURCE
    return DriverType.UNKNOWN


def list_gpus() -> List[GPUInfo]:
    """Возвращает список GPU."""
    gpus: List[GPUInfo] = []

    # lspci
    lspci = _run("lspci -nn 2>/dev/null")
    for line in lspci.splitlines():
        if re.search(r"VGA|3D|Display", line, re.IGNORECASE):
            gpu = GPUInfo()
            # PCI ID
            pci_match = re.match(r"^(\S+)", line)
            if pci_match:
                gpu.pci_id = pci_match.group(1)
            # Name  ─ after ": "
            name_match = re.search(r":\s+(.+?)(?:\s*\[[\da-f:]+\])?$", line, re.IGNORECASE)
            if name_match:
                gpu.name = name_match.group(1).strip()
            gpu.vendor = _detect_gpu_vendor(line)

            # Driver in use
            detail = _run(f"lspci -k -s {gpu.pci_id} 2>/dev/null")
            drv_match = re.search(r"Kernel driver in use:\s*(\S+)", detail)
            if drv_match:
                gpu.driver = drv_match.group(1)
                gpu.driver_type = _detect_driver_type(gpu.driver, gpu.vendor)
            mod_match = re.search(r"Kernel modules:\s*(.+)", detail)
            if mod_match:
                gpu.kernel_module = mod_match.group(1).strip()

            gpus.append(gpu)

    # Temperature
    for gpu in gpus:
        if gpu.vendor == GPUVendor.NVIDIA:
            temp = _run("nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader 2>/dev/null")
            if temp.strip().isdigit():
                gpu.temperature = float(temp.strip())
            vram = _run("nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null")
            vram_match = re.search(r"(\d+)", vram)
            if vram_match:
                gpu.vram_mb = int(vram_match.group(1))
        elif gpu.vendor == GPUVendor.AMD:
            # hwmon
            temp = _run("cat /sys/class/drm/card0/device/hwmon/hwmon*/temp1_input 2>/dev/null")
            if temp.strip().isdigit():
                gpu.temperature = float(temp.strip()) / 1000.0
            vram = _run("cat /sys/class/drm/card0/device/mem_info_vram_total 2>/dev/null")
            if vram.strip().isdigit():
                gpu.vram_mb = int(vram.strip()) // (1024 * 1024)

    return gpus


def suggest_driver(vendor: GPUVendor) -> Dict[str, str]:
    """Рекомендации по драйверу для GPU."""
    suggestions: Dict[str, Dict[str, str]] = {
        GPUVendor.NVIDIA: {
            "recommended": "nvidia (проприетарный)",
            "open_source": "nouveau (базовый, без полной поддержки)",
            "install_arch": "sudo pacman -S nvidia nvidia-utils",
            "install_deb": "sudo apt install nvidia-driver",
            "install_fedora": "sudo dnf install akmod-nvidia",
        },
        GPUVendor.AMD: {
            "recommended": "amdgpu (встроенный в ядро)",
            "open_source": "amdgpu (полная поддержка)",
            "install_arch": "sudo pacman -S mesa vulkan-radeon",
            "install_deb": "sudo apt install mesa-vulkan-drivers",
            "install_fedora": "sudo dnf install mesa-vulkan-drivers",
        },
        GPUVendor.INTEL: {
            "recommended": "i915 (встроенный в ядро)",
            "open_source": "i915 (полная поддержка)",
            "install_arch": "sudo pacman -S mesa vulkan-intel",
            "install_deb": "sudo apt install mesa-vulkan-drivers",
            "install_fedora": "sudo dnf install mesa-vulkan-drivers",
        },
    }
    return suggestions.get(vendor, {
        "recommended": "неизвестно",
        "note": "Определите GPU вручную: lspci | grep VGA"
    })


# ───────────────────────── Diagnostics ────────────────────────────────────

def diagnose_display_issues() -> List[DisplayIssue]:
    """Диагностика типичных проблем дисплея."""
    issues: List[DisplayIssue] = []

    # 1. Check display server
    ds = detect_display_server()
    if ds == DisplayServer.UNKNOWN:
        issues.append(DisplayIssue(
            severity="error",
            category="display_server",
            description="Не удалось определить дисплейный сервер (Wayland/X11).",
            suggestion="Проверьте переменную XDG_SESSION_TYPE.",
            command="echo $XDG_SESSION_TYPE",
        ))

    # 2. Check GPU driver
    gpus = list_gpus()
    if not gpus:
        issues.append(DisplayIssue(
            severity="warning",
            category="gpu",
            description="Не удалось обнаружить GPU.",
            suggestion="Проверьте вывод lspci.",
            command="lspci | grep -i vga",
        ))
    for gpu in gpus:
        if gpu.vendor == GPUVendor.NVIDIA and gpu.driver == "nouveau":
            issues.append(DisplayIssue(
                severity="warning",
                category="gpu_driver",
                description=f"GPU {gpu.name} использует nouveau (базовый драйвер). "
                            "Производительность ограничена.",
                suggestion="Установите проприетарный драйвер NVIDIA.",
                command=suggest_driver(GPUVendor.NVIDIA).get("install_arch", ""),
            ))
        if gpu.temperature and gpu.temperature > 85:
            issues.append(DisplayIssue(
                severity="warning",
                category="gpu_temp",
                description=f"GPU {gpu.name} перегревается: {gpu.temperature}°C.",
                suggestion="Проверьте вентиляцию и кулер.",
                command="nvidia-smi" if gpu.vendor == GPUVendor.NVIDIA else "sensors",
            ))

    # 3. Check monitors
    monitors = list_monitors()
    if not monitors:
        issues.append(DisplayIssue(
            severity="info",
            category="monitor",
            description="Не удалось получить информацию о мониторах.",
            suggestion="Попробуйте xrandr или wlr-randr.",
            command="xrandr --current",
        ))
    for m in monitors:
        if m.refresh_rate > 0 and m.refresh_rate < 30:
            issues.append(DisplayIssue(
                severity="warning",
                category="refresh_rate",
                description=f"Монитор {m.name}: низкая частота обновления ({m.refresh_rate} Hz).",
                suggestion="Проверьте настройки разрешения.",
                command="xrandr --current",
            ))

    # 4. Check for tearing (Wayland is tear-free, X11 may have issues)
    if ds == DisplayServer.X11:
        compositor = detect_compositor()
        vblank = _run("xdpyinfo 2>/dev/null | grep -i 'render'")
        if not compositor.name:
            issues.append(DisplayIssue(
                severity="info",
                category="tearing",
                description="Не обнаружен композитор на X11. Возможен тearing.",
                suggestion="Включите композитор (picom, compton) или переключитесь на Wayland.",
                command="picom --daemon",
            ))

    return issues


# ───────────────────────── Summary ────────────────────────────────────────

def get_display_summary() -> DisplaySummary:
    """Полная сводка о дисплейной подсистеме."""
    summary = DisplaySummary()
    summary.display_server = detect_display_server()
    summary.compositor = detect_compositor()
    summary.monitors = list_monitors()
    summary.gpus = list_gpus()
    summary.issues = diagnose_display_issues()
    return summary


def get_display_summary_text() -> str:
    """Текстовый отчёт о дисплейной подсистеме."""
    s = get_display_summary()
    lines = []
    lines.append(f"=== Дисплейная подсистема ===")
    lines.append(f"Дисплейный сервер: {s.display_server.value}")
    lines.append(f"Композитор: {s.compositor.name or 'неизвестен'} "
                 f"({s.compositor.desktop_environment})")

    if s.monitors:
        lines.append(f"\nМониторы ({len(s.monitors)}):")
        for i, m in enumerate(s.monitors, 1):
            primary_tag = " [PRIMARY]" if m.primary else ""
            lines.append(f"  {i}. {m.name}{primary_tag}: "
                         f"{m.resolution} @ {m.refresh_rate}Hz "
                         f"(scale {m.scale}x)")

    if s.gpus:
        lines.append(f"\nGPU ({len(s.gpus)}):")
        for i, g in enumerate(s.gpus, 1):
            temp_str = f", {g.temperature}°C" if g.temperature else ""
            vram_str = f", {g.vram_mb}MB VRAM" if g.vram_mb else ""
            lines.append(f"  {i}. {g.name}")
            lines.append(f"     Драйвер: {g.driver or 'неизвестен'} "
                         f"({g.driver_type.value}){temp_str}{vram_str}")

    if s.issues:
        lines.append(f"\nПроблемы ({len(s.issues)}):")
        for issue in s.issues:
            icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(issue.severity, "•")
            lines.append(f"  {icon} [{issue.category}] {issue.description}")
            if issue.suggestion:
                lines.append(f"     → {issue.suggestion}")

    return "\n".join(lines)
