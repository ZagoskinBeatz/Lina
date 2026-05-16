"""
MachineDriftDetector — детектор дрейфа конфигурации машины.

Расширяет core/drift_detector.py (StateDriftDetector — prompt/model drift)
для отслеживания СИСТЕМНЫХ изменений:

  - Kernel version change
  - Major package upgrades
  - GPU driver switch
  - Display server change (X11 ↔ Wayland)
  - DNS configuration change
  - Firewall rules modification
  - Network backend change
  - Audio backend change (PipeWire ↔ PulseAudio)
  - New/removed hardware (USB, disk, GPU)
  - Hostname/locale change

После обнаружения drift:
  → Автоматический health-check через SystemStateScanner
  → Алерт

Хранит baseline в JSON для persistence между сессиями.

Phase: SYSTEM OVERLORD / Module 4
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Drift Event
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MachineDriftEvent:
    """Одно событие дрейфа конфигурации."""
    component: str               # kernel, gpu_driver, dns, etc.
    field: str                   # что именно изменилось
    old_value: str
    new_value: str
    severity: str = "info"       # info, warning, critical
    timestamp: float = 0.0
    requires_healthcheck: bool = False

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component": self.component,
            "field": self.field,
            "old": self.old_value,
            "new": self.new_value,
            "severity": self.severity,
            "needs_healthcheck": self.requires_healthcheck,
        }


# ═══════════════════════════════════════════════════════════════════
#  Machine Fingerprint — снимок конфигурации
# ═══════════════════════════════════════════════════════════════════

def _cmd(c: str) -> str:
    try:
        r = subprocess.run(c, shell=True, capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def _collect_fingerprint() -> Dict[str, str]:
    """Собрать текущий отпечаток машины."""
    fp: Dict[str, str] = {}

    # Kernel
    fp["kernel"] = _cmd("uname -r")

    # Hostname
    fp["hostname"] = _cmd("hostname")

    # Display server
    fp["display_server"] = "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"

    # GPU driver
    fp["gpu_driver"] = _cmd("lspci -k 2>/dev/null | grep -A1 VGA | grep 'Kernel driver' | awk '{print $NF}'")

    # Audio backend
    pw = _cmd("systemctl --user is-active pipewire 2>/dev/null")
    pa = _cmd("systemctl --user is-active pulseaudio 2>/dev/null")
    if pw == "active":
        fp["audio_backend"] = "pipewire"
    elif pa == "active":
        fp["audio_backend"] = "pulseaudio"
    else:
        fp["audio_backend"] = "unknown"

    # DNS
    fp["dns_servers"] = _cmd("resolvectl status 2>/dev/null | grep 'DNS Servers' | head -3 || grep nameserver /etc/resolv.conf 2>/dev/null")

    # Firewall
    fw_parts = []
    ufw = _cmd("ufw status 2>/dev/null | head -1")
    if ufw:
        fw_parts.append(f"ufw:{ufw}")
    fwd = _cmd("firewall-cmd --state 2>/dev/null")
    if fwd:
        fw_parts.append(f"firewalld:{fwd}")
    fp["firewall"] = "|".join(fw_parts) if fw_parts else "none"

    # Network backend
    fp["network_backend"] = _cmd("systemctl is-active NetworkManager 2>/dev/null")

    # Locale
    fp["locale"] = _cmd("localectl status 2>/dev/null | head -1")

    # Timezone
    fp["timezone"] = _cmd("timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null")

    # Key packages (GPU drivers, kernel modules)
    fp["nvidia_version"] = _cmd("nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null")
    fp["mesa_version"] = _cmd("glxinfo 2>/dev/null | grep 'OpenGL version' | head -1")

    # USB device count (proxy for hardware changes)
    fp["usb_devices"] = _cmd("lsusb 2>/dev/null | wc -l")

    # Block devices
    fp["block_devices"] = _cmd("lsblk -d -n -o NAME,SIZE,TYPE 2>/dev/null | sort")

    # Distro
    fp["distro"] = _cmd("cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'")

    # Compute hash
    raw = json.dumps(fp, sort_keys=True)
    fp["_hash"] = hashlib.sha256(raw.encode()).hexdigest()

    return fp


# ═══════════════════════════════════════════════════════════════════
#  Severity mapping
# ═══════════════════════════════════════════════════════════════════

_SEVERITY_MAP: Dict[str, str] = {
    "kernel": "critical",
    "gpu_driver": "critical",
    "nvidia_version": "warning",
    "mesa_version": "warning",
    "display_server": "critical",
    "audio_backend": "warning",
    "dns_servers": "warning",
    "firewall": "warning",
    "network_backend": "warning",
    "hostname": "info",
    "locale": "info",
    "timezone": "info",
    "usb_devices": "info",
    "block_devices": "warning",
    "distro": "critical",
}

_HEALTHCHECK_REQUIRED = {
    "kernel", "gpu_driver", "nvidia_version", "display_server",
    "audio_backend", "dns_servers", "firewall", "network_backend",
    "block_devices", "distro",
}


# ═══════════════════════════════════════════════════════════════════
#  MachineDriftDetector
# ═══════════════════════════════════════════════════════════════════

class MachineDriftDetector:
    """Детектор дрейфа конфигурации машины.

    Usage:
        detector = get_drift_detector()
        events = detector.check()
        for e in events:
            print(f"DRIFT: {e.component} changed: {e.old_value} → {e.new_value}")
    """

    BASELINE_PATH = os.path.expanduser(
        "~/.local/share/lina/drift/machine_baseline.json"
    )

    def __init__(self) -> None:
        self._baseline: Dict[str, str] = {}
        self._events: List[MachineDriftEvent] = []
        self._load_baseline()

    # ─── Baseline persistence ─────────────────────────────────

    def _load_baseline(self) -> None:
        try:
            if os.path.isfile(self.BASELINE_PATH):
                with open(self.BASELINE_PATH, "r") as f:
                    self._baseline = json.load(f)
        except Exception as e:
            logger.warning("Failed to load drift baseline: %s", e)
            self._baseline = {}

    def _save_baseline(self, fp: Dict[str, str]) -> None:
        try:
            os.makedirs(os.path.dirname(self.BASELINE_PATH), exist_ok=True)
            with open(self.BASELINE_PATH, "w") as f:
                json.dump(fp, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save drift baseline: %s", e)

    # ─── Set baseline manually ────────────────────────────────

    def set_baseline(self) -> Dict[str, str]:
        """Установить текущее состояние как baseline."""
        fp = _collect_fingerprint()
        self._baseline = fp
        self._save_baseline(fp)
        return fp

    # ─── Check for drift ──────────────────────────────────────

    def check(self) -> List[MachineDriftEvent]:
        """Сравнить текущее состояние с baseline. Вернуть drift events."""
        current = _collect_fingerprint()

        # Если нет baseline — установить текущий
        if not self._baseline:
            self._baseline = current
            self._save_baseline(current)
            return []

        events: List[MachineDriftEvent] = []

        for key, new_val in current.items():
            if key.startswith("_"):
                continue
            old_val = self._baseline.get(key, "")
            if old_val != new_val and old_val:
                event = MachineDriftEvent(
                    component=key,
                    field=key,
                    old_value=old_val,
                    new_value=new_val,
                    severity=_SEVERITY_MAP.get(key, "info"),
                    requires_healthcheck=key in _HEALTHCHECK_REQUIRED,
                )
                events.append(event)

        # Обновить baseline
        self._baseline = current
        self._save_baseline(current)

        # Log events
        for e in events:
            self._events.append(e)
            logger.info(
                "DRIFT: %s changed: '%s' → '%s' [%s]",
                e.component, e.old_value[:40], e.new_value[:40], e.severity,
            )

        if len(self._events) > 500:
            self._events = self._events[-500:]

        return events

    # ─── Check + auto health-check ────────────────────────────

    def check_with_healthcheck(self) -> List[MachineDriftEvent]:
        """Check for drift. If critical drift → run health-check."""
        events = self.check()
        needs_health = any(e.requires_healthcheck for e in events)
        if needs_health:
            try:
                from lina.diagnostics.scanner import get_scanner
                scanner = get_scanner()
                state = scanner.scan()
                logger.info(
                    "DRIFT: Auto health-check after %d drift events. Overall: %s",
                    len(events), state.overall.value,
                )
            except Exception as e:
                logger.error("DRIFT: health-check failed: %s", e)
        return events

    # ─── API ──────────────────────────────────────────────────

    def get_baseline(self) -> Dict[str, str]:
        return dict(self._baseline)

    def get_events(self, limit: int = 30) -> List[MachineDriftEvent]:
        return self._events[-limit:]

    def format_report(self) -> str:
        """Текстовый отчёт."""
        lines = ["═══ Machine Drift Report ═══"]
        if not self._events:
            lines.append("  Нет обнаруженных изменений")
        else:
            for e in self._events[-20:]:
                icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🔴"}.get(e.severity, "?")
                lines.append(f"  {icon} {e.component}: {e.old_value[:30]} → {e.new_value[:30]}")
        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "baseline_set": bool(self._baseline),
            "total_events": len(self._events),
            "baseline_fields": len(self._baseline),
        }


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_detector: Optional[MachineDriftDetector] = None


def get_drift_detector() -> MachineDriftDetector:
    global _detector
    if _detector is None:
        _detector = MachineDriftDetector()
    return _detector
