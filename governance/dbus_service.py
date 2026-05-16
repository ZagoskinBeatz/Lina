"""
DBus Service — IPC интерфейс для Lina (org.lina.Assistant).

Предоставляет:
  - Diagnose(domain: str) → JSON результат
  - Fix(action_id: str, params: JSON) → JSON результат
  - Status() → JSON статус
  - Escalation callback

Также: systemd unit для автозапуска.

Phase: GOVERNANCE LAYER / Service Layer
"""

from __future__  import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── DBus interface name ─────────────────────────────────────────────────────

DBUS_INTERFACE = "org.lina.Assistant"
DBUS_PATH = "/org/lina/Assistant"
DBUS_SERVICE_NAME = "org.lina.Assistant"

# ─── IPC Schema Version (Phase 2) ────────────────────────────────────────────
# Included in every response payload for client compatibility detection.
# Bump minor for additive changes, major for breaking changes.

API_VERSION = "2.0"

# Error codes contract
class IPCError:
    OK = 0
    DENIED = 1
    CONFIRM_REQUIRED = 2
    NOT_FOUND = 3
    RATE_LIMITED = 4
    INTERNAL = 5
    GOVERNANCE_UNAVAILABLE = 6


# ─── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class DBusConfig:
    """Конфигурация DBus сервиса."""
    enabled: bool = True
    session_bus: bool = True     # True=session, False=system
    interface: str = DBUS_INTERFACE
    path: str = DBUS_PATH


# ─── systemd unit ────────────────────────────────────────────────────────────

SYSTEMD_USER_UNIT = """\
[Unit]
Description=Lina AI Assistant Service
After=graphical-session.target

[Service]
Type=simple
ExecStart={python_path} -m lina.governance.service_runner
Restart=on-failure
RestartSec=5
Environment=LANG=C.UTF-8
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

DBUS_SERVICE_FILE = """\
[D-BUS Service]
Name={interface}
Exec={python_path} -m lina.governance.service_runner
"""


# ─── DBus Service (abstract — works with or without dbus library) ────────────

class LinaDBusService:
    """
    DBus сервис Lina.

    Если python-dbus не установлен, работает как no-op.
    API доступно через JSON-over-pipe fallback.

    Пример:
        svc = LinaDBusService()
        svc.export()  # Регистрация на шине
    """

    def __init__(self, config: Optional[DBusConfig] = None) -> None:
        self._config = config or DBusConfig()
        self._running = False
        self._dbus_available = False
        self._try_import_dbus()

    def _try_import_dbus(self) -> None:
        """Попытка импорта dbus."""
        try:
            import dbus
            import dbus.service
            import dbus.mainloop.glib
            self._dbus_available = True
        except ImportError:
            self._dbus_available = False
            logger.info("LinaDBusService: python-dbus not available, "
                        "using pipe fallback")

    # ── Methods (будут экспортированы через DBus) ────────

    def diagnose(self, domain: str) -> str:
        """Запустить диагностику домена через Intent → Governance.

        Phase 1: DBus → Intent → Governance (zero-trust).
        Phase 5: Input validation before pipeline.
        """
        try:
            # Phase 5: Validate IPC input
            from lina.security.input_validator import get_input_validator
            _iv = get_input_validator()
            dom_ok, dom_reason = _iv.validate_domain(domain)
            if not dom_ok:
                return json.dumps({
                    "api_version": API_VERSION,
                    "status": "denied",
                    "error": f"Invalid domain: {dom_reason}",
                    "error_code": IPCError.DENIED,
                })

            from lina.intent.bridge import get_intent_bridge

            bridge = get_intent_bridge()
            result = bridge.from_diagnose(
                domain=domain,
                source="dbus",
                user_text=f"diagnose {domain}",
            )

            return json.dumps({
                "api_version": API_VERSION,
                "domain": domain,
                "status": result.status.value,
                "response": result.response_text,
                "intent_id": result.intent_id,
                "policy_decision": result.policy_decision,
                "error_code": IPCError.OK,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"api_version": API_VERSION, "status": "error",
                               "error": str(e), "error_code": IPCError.INTERNAL})

    def execute_action(self, action_id: str, params_json: str) -> str:
        """Выполнить действие через Intent → Governance.

        Phase 1: DBus → Intent → AccessCheck → Policy → Execute.
        Phase 5: Input validation + JSON payload size limit.
        """
        try:
            # Phase 5: Validate IPC inputs
            from lina.security.input_validator import get_input_validator
            _iv = get_input_validator()
            act_ok, act_reason = _iv.validate_action(action_id)
            if not act_ok:
                return json.dumps({
                    "api_version": API_VERSION,
                    "status": "denied",
                    "error": f"Invalid action: {act_reason}",
                    "error_code": IPCError.DENIED,
                })
            if params_json:
                pay_ok, pay_reason = _iv.validate_json_payload(params_json)
                if not pay_ok:
                    return json.dumps({
                        "api_version": API_VERSION,
                        "status": "denied",
                        "error": f"Invalid payload: {pay_reason}",
                        "error_code": IPCError.DENIED,
                    })

            from lina.intent.bridge import get_intent_bridge

            bridge = get_intent_bridge()
            params = json.loads(params_json) if params_json else {}

            result = bridge.from_action(
                action_id=action_id,
                domain=params.pop("domain", ""),
                params=params,
                source="dbus",
            )

            return json.dumps({
                "api_version": API_VERSION,
                "action_id": action_id,
                "status": result.status.value,
                "response": result.response_text,
                "intent_id": result.intent_id,
                "policy_decision": result.policy_decision,
                "escalation_id": result.escalation_id,
                "error_code": IPCError.OK,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"api_version": API_VERSION, "status": "error",
                               "error": str(e), "error_code": IPCError.INTERNAL})

    def get_status(self) -> str:
        """Получить статус Lina."""
        try:
            from .state_machine import get_runtime_machine

            sm = get_runtime_machine()
            return json.dumps({
                "api_version": API_VERSION,
                "state": sm.state,
                "running": self._running,
                "dbus_available": self._dbus_available,
                "error_code": IPCError.OK,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"api_version": API_VERSION, "status": "error",
                               "error": str(e), "error_code": IPCError.INTERNAL})

    # ── systemd / DBus install ───────────────────────────

    def confirm_escalation(self, escalation_id: str, confirmed_str: str) -> str:
        """
        Подтвердить или отклонить эскалацию через DBus.

        Phase 2: DBus → ConfirmationHandler → EscalationManager → re-execute.

        Args:
            escalation_id: ID эскалации.
            confirmed_str: "true" или "false".

        Returns:
            JSON с результатом.
        """
        try:
            from lina.governance.confirmation import get_confirmation_handler

            handler = get_confirmation_handler()
            handler.set_dbus_mode()
            confirmed = confirmed_str.lower() in ("true", "1", "yes", "да")

            result = handler.resolve_and_execute(escalation_id, confirmed)

            return json.dumps({
                "api_version": API_VERSION,
                "escalation_id": escalation_id,
                "confirmed": confirmed,
                "status": result.status.value if hasattr(result, 'status') else "unknown",
                "response": result.response_text if hasattr(result, 'response_text') else "",
                "error_code": IPCError.OK,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"api_version": API_VERSION, "status": "error",
                               "error": str(e), "error_code": IPCError.INTERNAL})

    # ── systemd / DBus install (original) ────────────────

    @staticmethod
    def install_systemd_unit() -> bool:
        """Установить systemd user unit."""
        try:
            import sys
            python_path = sys.executable

            unit_dir = Path.home() / ".config" / "systemd" / "user"
            unit_dir.mkdir(parents=True, exist_ok=True)
            unit_path = unit_dir / "lina-assistant.service"

            content = SYSTEMD_USER_UNIT.format(python_path=python_path)
            unit_path.write_text(content, encoding="utf-8")

            subprocess.run(["systemctl", "--user", "daemon-reload"],
                           capture_output=True, timeout=10)
            logger.info("Installed systemd unit: %s", unit_path)
            return True
        except Exception as e:
            logger.error("Failed to install systemd unit: %s", e)
            return False

    @staticmethod
    def install_dbus_service() -> bool:
        """Установить DBus service файл."""
        try:
            import sys
            python_path = sys.executable

            dbus_dir = Path.home() / ".local" / "share" / "dbus-1" / "services"
            dbus_dir.mkdir(parents=True, exist_ok=True)
            dbus_path = dbus_dir / f"{DBUS_INTERFACE}.service"

            content = DBUS_SERVICE_FILE.format(
                interface=DBUS_INTERFACE, python_path=python_path,
            )
            dbus_path.write_text(content, encoding="utf-8")
            logger.info("Installed DBus service: %s", dbus_path)
            return True
        except Exception as e:
            logger.error("Failed to install DBus service: %s", e)
            return False

    @staticmethod
    def enable_service() -> bool:
        """Включить systemd user service."""
        try:
            r = subprocess.run(
                ["systemctl", "--user", "enable", "lina-assistant.service"],
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def start_service() -> bool:
        """Запустить systemd user service."""
        try:
            r = subprocess.run(
                ["systemctl", "--user", "start", "lina-assistant.service"],
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def stop_service() -> bool:
        """Остановить systemd user service."""
        try:
            r = subprocess.run(
                ["systemctl", "--user", "stop", "lina-assistant.service"],
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def service_status() -> Dict[str, Any]:
        """Статус systemd service."""
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", "lina-assistant.service"],
                capture_output=True, text=True, timeout=5,
            )
            active = r.stdout.strip()
            return {"active": active, "running": active == "active"}
        except Exception as e:
            return {"active": "unknown", "error": str(e)}


# ─── Singleton ─────────────────────────────────────────────────────────────────

_service: Optional[LinaDBusService] = None

def get_dbus_service() -> LinaDBusService:
    """Получить единственный экземпляр LinaDBusService."""
    global _service
    if _service is None:
        _service = LinaDBusService()
    return _service
