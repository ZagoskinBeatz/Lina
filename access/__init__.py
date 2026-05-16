"""
Lina Access Layer — контроль уровней доступа.

Три уровня:
  User  — приложения, сеть, звук, диагностика
  Power — systemd, пакеты, конфигурация, firewall
  Admin — диски, installer, low-level

Phase: CONTROL PLANE / Access Layer
"""

from lina.access.levels import AccessLevel, AccessCheckResult
from lina.access.resolver import AccessLevelResolver, get_access_resolver

__all__ = [
    "AccessLevel", "AccessCheckResult",
    "AccessLevelResolver", "get_access_resolver",
]
