"""
Lina Governance — слой управления, политик и безопасности.

Модули:
  action_registry     — Whitelist разрешённых действий (ActionRegistry)
  policy_engine       — Движок политик (TOML-конфигурация)
  state_machine       — Конечный автомат жизненного цикла
  strategy_selector   — Выбор оптимальной стратегии решения
  signature_collector — Сбор сигнатур ошибок
  escalation          — Эскалация пользователю
  fuzzy_matcher       — Нечёткое сопоставление сигнатур
  integrity_v2        — Контроль целостности (SHA256 манифест)
  telemetry           — Анонимная телеметрия (opt-in)
  installer_mode      — Режим инсталлятора (Live-ISO)
  dbus_service        — DBus IPC + systemd unit
  hotkey_manager      — Горячие клавиши

Phase: GOVERNANCE LAYER
"""

from lina.governance.action_registry import ActionRegistry, get_action_registry
from lina.governance.policy_engine import PolicyEngine, get_policy_engine
from lina.governance.state_machine import (
    StateMachine, InstallerState, RuntimeState,
    get_installer_machine, get_runtime_machine,
)
from lina.governance.strategy_selector import StrategySelector, get_strategy_selector
from lina.governance.signature_collector import SignatureCollector, get_signature_collector
from lina.governance.escalation import EscalationManager, get_escalation_manager
from lina.governance.fuzzy_matcher import FuzzyMatcher, get_fuzzy_matcher
from lina.governance.integrity_v2 import IntegrityCheckV2, get_integrity_checker
from lina.governance.telemetry import TelemetryEngine, get_telemetry_engine
from lina.governance.installer_mode import InstallerMode, get_installer_mode
from lina.governance.dbus_service import LinaDBusService, get_dbus_service
from lina.governance.audit_logger import AuditLogger, get_audit_logger
from lina.governance.confirmation import ConfirmationHandler, get_confirmation_handler
from lina.governance.hotkey_manager import HotkeyManager, get_hotkey_manager

__all__ = [
    "ActionRegistry", "get_action_registry",
    "PolicyEngine", "get_policy_engine",
    "StateMachine", "InstallerState", "RuntimeState",
    "get_installer_machine", "get_runtime_machine",
    "StrategySelector", "get_strategy_selector",
    "SignatureCollector", "get_signature_collector",
    "EscalationManager", "get_escalation_manager",
    "FuzzyMatcher", "get_fuzzy_matcher",
    "IntegrityCheckV2", "get_integrity_checker",
    "TelemetryEngine", "get_telemetry_engine",
    "InstallerMode", "get_installer_mode",
    "LinaDBusService", "get_dbus_service",
    "HotkeyManager", "get_hotkey_manager",
    "AuditLogger", "get_audit_logger",
    "ConfirmationHandler", "get_confirmation_handler",
]
