"""
Access Levels — определение уровней доступа и результатов проверки.

Phase: CONTROL PLANE / Access Layer
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class AccessLevel(str, Enum):
    """Уровень доступа к операциям."""

    USER = "user"       # Приложения, сеть, звук, яркость, диагностика
    POWER = "power"     # systemd, пакеты, конфигурация, firewall, сервисы
    ADMIN = "admin"     # Диски, installer, low-level ops


# ─── Маппинг домен → уровень доступа ─────────────────────────────────────────

DOMAIN_ACCESS_MAP: Dict[str, AccessLevel] = {
    # User level
    "desktop": AccessLevel.USER,
    "brightness": AccessLevel.USER,
    "volume": AccessLevel.USER,
    "search": AccessLevel.USER,
    "info": AccessLevel.USER,

    # User/Power (зависит от действия)
    "network": AccessLevel.USER,
    "audio": AccessLevel.USER,
    "display": AccessLevel.USER,

    # Power level
    "service": AccessLevel.POWER,
    "package": AccessLevel.POWER,
    "config": AccessLevel.POWER,
    "firewall": AccessLevel.POWER,
    "boot": AccessLevel.POWER,
    "security": AccessLevel.POWER,

    # Admin level
    "disk": AccessLevel.ADMIN,
    "installer": AccessLevel.ADMIN,
    "user": AccessLevel.ADMIN,
    "low_level": AccessLevel.ADMIN,
}

# ─── Действия, повышающие уровень ────────────────────────────────────────────

ELEVATED_ACTIONS: Dict[str, AccessLevel] = {
    # Network: диагностика = user, перезапуск = power
    "net_restart_nm": AccessLevel.POWER,
    "net_restart_resolved": AccessLevel.POWER,
    "net_set_dns": AccessLevel.POWER,
    "net_flush_dns": AccessLevel.POWER,
    "net_restart_iwd": AccessLevel.POWER,

    # Audio: перезапуск = power
    "audio_restart_pipewire": AccessLevel.POWER,
    "audio_restart_pulseaudio": AccessLevel.POWER,

    # Package ops = power
    "pkg_install": AccessLevel.POWER,
    "pkg_remove": AccessLevel.POWER,
    "pkg_update": AccessLevel.POWER,

    # Boot = power
    "boot_grub_install": AccessLevel.ADMIN,
    "boot_systemd_install": AccessLevel.ADMIN,
    "boot_grub_config": AccessLevel.POWER,

    # Disk = admin
    "disk_partition": AccessLevel.ADMIN,
    "disk_format": AccessLevel.ADMIN,
    "disk_mount": AccessLevel.POWER,

    # Installer = admin
    "inst_pacstrap": AccessLevel.ADMIN,
    "inst_genfstab": AccessLevel.ADMIN,
    "inst_chroot": AccessLevel.ADMIN,
}

# ─── Уровни и требования подтверждения ────────────────────────────────────────

LEVEL_REQUIRES_CONFIRMATION: Dict[AccessLevel, bool] = {
    AccessLevel.USER: False,
    AccessLevel.POWER: True,
    AccessLevel.ADMIN: True,
}


@dataclass
class AccessCheckResult:
    """Результат проверки уровня доступа."""
    allowed: bool = True
    access_level: str = "user"
    needs_confirmation: bool = False
    reason: str = ""
    reason_ru: str = ""
    intent_type: str = ""
    domain: str = ""
    action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "access_level": self.access_level,
            "needs_confirmation": self.needs_confirmation,
            "reason": self.reason,
            "domain": self.domain,
            "action": self.action,
        }
