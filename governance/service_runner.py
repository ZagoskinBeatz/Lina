"""
service_runner.py — единая точка входа для Lina.

Порядок запуска:
  1. IntegrityCheckV2 — верифицировать файлы (SHA256 манифест)
  2. PolicyEngine — загрузить политики из TOML
  3. ActionRegistry — зарегистрировать все действия
  4. StateMachine — перевести в RuntimeState.BOOTING → INTEGRITY_CHECK → IDLE
  5. IntentRouter — инициализировать маршрутизацию Intent
  6. AccessLevelResolver — настроить уровень доступа сессии
  7. TelemetryEngine — запустить телеметрию (opt-in)
  8. DBus Service — поднять IPC (если доступен)
  9. HotkeyManager — зарегистрировать горячие клавиши
  10. Event loop — принимать Intent'ы

Запуск:
  python -m lina.governance.service_runner
  systemctl --user start lina-assistant.service

Phase: GOVERNANCE LAYER / Unified Entrypoint
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ─── Version ──────────────────────────────────────────────────────────────────

__version__ = "0.8.0"
BANNER = f"""\
╔══════════════════════════════════════════════════════╗
║  Lina AI Assistant — Governance Control Plane v{__version__}  ║
║  PID: {{pid}}  │  Level: {{level}}                       ║
╚══════════════════════════════════════════════════════╝
"""


# ─── Service State ────────────────────────────────────────────────────────────

class ServiceState:
    """Внутреннее состояние service runner."""

    def __init__(self) -> None:
        self.running: bool = False
        self.start_time: float = 0.0
        self.integrity_ok: bool = False
        self.governance_ready: bool = False
        self.dbus_active: bool = False
        self.hotkeys_active: bool = False
        self.intent_ready: bool = False
        self.health_ok: bool = True
        self.boot_errors: list[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "uptime": round(time.time() - self.start_time, 1) if self.start_time else 0,
            "integrity_ok": self.integrity_ok,
            "governance_ready": self.governance_ready,
            "dbus_active": self.dbus_active,
            "hotkeys_active": self.hotkeys_active,
            "intent_ready": self.intent_ready,
            "health_ok": self.health_ok,
            "boot_errors": self.boot_errors,
        }


# ─── Service Runner ──────────────────────────────────────────────────────────

class LinaServiceRunner:
    """
    Единая точка входа Lina.

    Инициализирует governance control plane в правильном порядке,
    затем входит в event loop для обработки Intent'ов.

    Пример:
        runner = LinaServiceRunner()
        runner.boot()     # → Полная инициализация
        runner.run()      # → Event loop (блокирующий)
        runner.shutdown()
    """

    def __init__(self) -> None:
        self._state = ServiceState()
        self._sm = None          # StateMachine (Runtime)
        self._policy = None      # PolicyEngine
        self._registry = None    # ActionRegistry
        self._integrity = None   # IntegrityCheckV2
        self._telemetry = None   # TelemetryEngine
        self._dbus = None        # LinaDBusService
        self._hotkeys = None     # HotkeyManager
        self._intent_router = None  # IntentRouter
        self._access_resolver = None  # AccessLevelResolver
        self._audit_logger = None  # AuditLogger (Phase 2)
        self._shutdown_requested = False

    # ── Boot Sequence ─────────────────────────────────────

    def boot(self) -> bool:
        """
        Полная последовательность инициализации.

        Returns:
            True если boot завершён успешно.
        """
        self._state.start_time = time.time()
        self._state.running = True

        pid = os.getpid()
        level = "USER"

        logger.info(BANNER.format(pid=pid, level=level).strip())
        logger.info("Lina service_runner boot sequence started")

        ok = True

        # Step 1: State Machine → BOOTING
        ok = ok and self._init_state_machine()

        # Step 2: Integrity Check
        ok = ok and self._init_integrity()

        # Step 3: State Machine → INTEGRITY_CHECK → IDLE
        if ok and self._sm:
            try:
                self._sm.transition("integrity_check")
            except Exception:
                pass  # Transition may not be defined in test configs

        # Step 4: Policy Engine
        ok = ok and self._init_policy()

        # Step 5: Action Registry
        ok = ok and self._init_action_registry()

        # Step 6: Access Level Resolver
        ok = ok and self._init_access()

        # Step 7: Intent Router
        ok = ok and self._init_intent_router()

        # Step 8: Telemetry
        self._init_telemetry()  # Non-critical, don't fail boot

        # Step 9: DBus Service
        self._init_dbus()  # Non-critical

        # Step 10: Hotkeys
        self._init_hotkeys()  # Non-critical

        # Step 11: AuditLogger (Phase 2)
        self._init_audit_logger()  # Non-critical

        # Transition to IDLE
        if self._sm:
            try:
                self._sm.transition("idle")
            except Exception:
                pass

        self._state.governance_ready = ok
        duration = time.time() - self._state.start_time

        if ok:
            logger.info("Lina boot complete in %.2fs", duration)
        else:
            logger.error("Lina boot completed with errors: %s",
                         self._state.boot_errors)

        # Record telemetry
        if self._telemetry:
            try:
                self._telemetry.record("session_start",
                                       domain="system",
                                       metric="boot_time",
                                       value=duration)
            except Exception:
                pass

        return ok

    # ── Init Steps ────────────────────────────────────────

    def _init_state_machine(self) -> bool:
        """Инициализировать StateMachine (Runtime)."""
        try:
            from lina.governance.state_machine import get_runtime_machine
            self._sm = get_runtime_machine()
            logger.info("[boot] StateMachine initialized (state: %s)", self._sm.state)
            return True
        except Exception as e:
            self._state.boot_errors.append(f"StateMachine: {e}")
            logger.error("[boot] StateMachine failed: %s", e)
            return False

    def _init_integrity(self) -> bool:
        """Проверить целостность файлов."""
        try:
            from lina.governance.integrity_v2 import get_integrity_checker
            self._integrity = get_integrity_checker()
            result = self._integrity.check()
            self._state.integrity_ok = result.passed

            if result.passed:
                logger.info("[boot] Integrity check PASSED (%d files, %.1fs)",
                            result.checked_files, result.duration)
            else:
                logger.warning("[boot] Integrity check FAILED: %d violations",
                               len(result.violations))
                # Don't fail boot, but enter DEGRADED if possible
                if self._sm:
                    try:
                        self._sm.transition("degraded")
                    except Exception:
                        pass
            return True
        except Exception as e:
            self._state.boot_errors.append(f"IntegrityCheck: {e}")
            logger.error("[boot] IntegrityCheck failed: %s", e)
            self._state.integrity_ok = False
            return True  # Non-fatal: allow boot to continue

    def _init_policy(self) -> bool:
        """Загрузить PolicyEngine."""
        try:
            from lina.governance.policy_engine import get_policy_engine
            self._policy = get_policy_engine()
            logger.info("[boot] PolicyEngine loaded (domains: %s)",
                        self._policy.config.allowed_domains)
            return True
        except Exception as e:
            self._state.boot_errors.append(f"PolicyEngine: {e}")
            logger.error("[boot] PolicyEngine failed: %s", e)
            return False

    def _init_action_registry(self) -> bool:
        """Загрузить ActionRegistry."""
        try:
            from lina.governance.action_registry import get_action_registry
            self._registry = get_action_registry()
            count = self._registry.count() if hasattr(self._registry, 'count') else 0
            logger.info("[boot] ActionRegistry loaded (%d actions)", count)
            return True
        except Exception as e:
            self._state.boot_errors.append(f"ActionRegistry: {e}")
            logger.error("[boot] ActionRegistry failed: %s", e)
            return False

    def _init_access(self) -> bool:
        """Инициализировать AccessLevelResolver."""
        try:
            from lina.access.resolver import get_access_resolver
            self._access_resolver = get_access_resolver()
            logger.info("[boot] AccessLevelResolver ready (session: %s)",
                        self._access_resolver.session_level.value)
            return True
        except Exception as e:
            self._state.boot_errors.append(f"AccessResolver: {e}")
            logger.error("[boot] AccessLevelResolver failed: %s", e)
            return False

    def _init_intent_router(self) -> bool:
        """Инициализировать IntentRouter."""
        try:
            from lina.intent.router import get_intent_router
            self._intent_router = get_intent_router()
            self._state.intent_ready = True
            logger.info("[boot] IntentRouter ready")
            return True
        except Exception as e:
            self._state.boot_errors.append(f"IntentRouter: {e}")
            logger.error("[boot] IntentRouter failed: %s", e)
            return False

    def _init_telemetry(self) -> None:
        """Инициализировать TelemetryEngine (необязательно)."""
        try:
            from lina.governance.telemetry import get_telemetry_engine
            self._telemetry = get_telemetry_engine()
            logger.info("[boot] TelemetryEngine ready")
        except Exception as e:
            logger.debug("[boot] TelemetryEngine skipped: %s", e)

    def _init_dbus(self) -> None:
        """Инициализировать DBus Service (необязательно)."""
        try:
            from lina.governance.dbus_service import get_dbus_service
            self._dbus = get_dbus_service()
            self._state.dbus_active = True
            logger.info("[boot] DBus service ready")
        except Exception as e:
            logger.debug("[boot] DBus skipped: %s", e)

    def _init_hotkeys(self) -> None:
        """Инициализировать HotkeyManager (необязательно)."""
        try:
            from lina.governance.hotkey_manager import get_hotkey_manager
            self._hotkeys = get_hotkey_manager()
            self._state.hotkeys_active = True
            logger.info("[boot] HotkeyManager ready")
        except Exception as e:
            logger.debug("[boot] HotkeyManager skipped: %s", e)

    def _init_audit_logger(self) -> None:
        """Инициализировать AuditLogger (Phase 2, необязательно)."""
        try:
            from lina.governance.audit_logger import get_audit_logger, AuditEvent
            self._audit_logger = get_audit_logger()
            self._audit_logger.log_session(AuditEvent.SESSION_START, metadata={
                "pid": os.getpid(),
                "version": __version__,
            })
            logger.info("[boot] AuditLogger ready (path: %s)", self._audit_logger.path)
        except Exception as e:
            logger.debug("[boot] AuditLogger skipped: %s", e)

    # ── Event Loop ────────────────────────────────────────

    def run(self) -> None:
        """
        Блокирующий event loop.

        Обрабатывает:
          - DBus вызовы (если доступен)
          - Горячие клавиши
          - stdin pipe (для тестов / IPC fallback)
          - Периодические health checks (Phase 2)

        Выход по SIGTERM / SIGINT.
        """
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        logger.info("Lina service entering event loop (PID=%d)", os.getpid())
        self._health_interval = 30  # seconds between health checks
        self._last_health_check = time.time()
        self._consecutive_failures = 0

        while not self._shutdown_requested:
            try:
                time.sleep(0.5)

                # Phase 2: Periodic health check
                now = time.time()
                if (now - self._last_health_check) >= self._health_interval:
                    self._run_health_check()
                    self._last_health_check = now

            except (KeyboardInterrupt, SystemExit):
                break

        self.shutdown()

    def _run_health_check(self) -> None:
        """
        Phase 2: Periodic health check of governance components.

        Pings PolicyEngine, IntentRouter, StateMachine.
        On failure: attempt re-init, transition to DEGRADED.
        3 consecutive failures → log critical alert.
        """
        checks_passed = 0
        checks_total = 0
        issues = []

        # Check PolicyEngine
        checks_total += 1
        if self._policy:
            try:
                stats = self._policy.get_stats()
                if isinstance(stats, dict):
                    checks_passed += 1
            except Exception as e:
                issues.append(f"PolicyEngine: {e}")
        else:
            issues.append("PolicyEngine: not initialized")

        # Check IntentRouter
        checks_total += 1
        if self._intent_router:
            try:
                stats = self._intent_router.get_stats()
                if isinstance(stats, dict):
                    checks_passed += 1
            except Exception as e:
                issues.append(f"IntentRouter: {e}")
        else:
            issues.append("IntentRouter: not initialized")

        # Check StateMachine
        checks_total += 1
        if self._sm:
            try:
                state = self._sm.state
                if state:
                    checks_passed += 1
            except Exception as e:
                issues.append(f"StateMachine: {e}")
        else:
            issues.append("StateMachine: not initialized")

        # Evaluate health
        if checks_passed == checks_total:
            self._consecutive_failures = 0
            self._state.health_ok = True
            logger.debug("[health] All %d checks passed", checks_total)
        else:
            self._consecutive_failures += 1
            self._state.health_ok = False
            logger.warning(
                "[health] %d/%d checks passed (consecutive failures: %d): %s",
                checks_passed, checks_total,
                self._consecutive_failures, "; ".join(issues))

            # Attempt recovery: transition to DEGRADED
            if self._sm:
                try:
                    self._sm.transition("degraded")
                except Exception:
                    pass

            # Attempt re-init of failed components
            if not self._policy:
                self._init_policy()
            if not self._intent_router:
                self._init_intent_router()

            # 3 consecutive failures → critical alert
            if self._consecutive_failures >= 3:
                logger.critical(
                    "[health] 3+ consecutive health check failures. "
                    "Manual intervention may be required.")

        # Audit health check
        try:
            from lina.governance.audit_logger import get_audit_logger, AuditEvent
            audit = get_audit_logger()
            audit.log_session(AuditEvent.HEALTH_CHECK, metadata={
                "passed": checks_passed,
                "total": checks_total,
                "consecutive_failures": self._consecutive_failures,
                "issues": issues,
            })
        except Exception:
            pass

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Обработчик сигналов для graceful shutdown."""
        sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
        logger.info("Received signal %s, initiating shutdown", sig_name)
        self._shutdown_requested = True

    # ── Shutdown ──────────────────────────────────────────

    def shutdown(self) -> None:
        """Graceful shutdown."""
        if not self._state.running:
            return

        logger.info("Lina service shutting down...")

        # Transition to SHUTDOWN state
        if self._sm:
            try:
                self._sm.transition("shutdown")
            except Exception:
                pass

        # Record telemetry
        if self._telemetry:
            try:
                uptime = time.time() - self._state.start_time
                self._telemetry.record("session_end",
                                       domain="system",
                                       metric="uptime",
                                       value=uptime)
                self._telemetry.flush()
            except Exception:
                pass

        self._state.running = False
        logger.info("Lina service stopped.")

        # Audit: session end
        if self._audit_logger:
            try:
                from lina.governance.audit_logger import AuditEvent
                uptime = time.time() - self._state.start_time
                self._audit_logger.log_session(AuditEvent.SESSION_END, metadata={
                    "uptime_s": round(uptime, 1),
                })
            except Exception:
                pass

    # ── Public API ────────────────────────────────────────

    def process_intent(self, intent: Any) -> Any:
        """
        Обработать Intent через полный governance pipeline.

        Это THE единственный путь от UI к выполнению:
          Intent → AccessLevel → Policy → ActionRegistry → Execution

        Args:
            intent: объект Intent из lina.intent.types

        Returns:
            IntentResult
        """
        if not self._intent_router:
            raise RuntimeError("IntentRouter not initialized. Call boot() first.")
        return self._intent_router.process(intent)

    @property
    def state(self) -> ServiceState:
        """Текущее состояние сервиса."""
        return self._state

    def get_status(self) -> Dict[str, Any]:
        """Полный статус для мониторинга / DBus."""
        status: Dict[str, Any] = self._state.to_dict()

        if self._sm:
            status["runtime_state"] = self._sm.state

        if self._policy:
            status["policy_stats"] = self._policy.get_stats()

        if self._registry and hasattr(self._registry, 'get_stats'):
            status["registry_stats"] = self._registry.get_stats()

        if self._access_resolver:
            status["access_stats"] = self._access_resolver.get_stats()

        status["version"] = __version__
        return status


# ─── Singleton ────────────────────────────────────────────────────────────────

_runner: Optional[LinaServiceRunner] = None


def get_service_runner() -> LinaServiceRunner:
    """Получить единственный экземпляр LinaServiceRunner."""
    global _runner
    if _runner is None:
        _runner = LinaServiceRunner()
    return _runner


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

def main() -> None:
    """Точка входа для `python -m lina.governance.service_runner`."""
    # Configure logging
    log_level = os.environ.get("LINA_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    runner = get_service_runner()

    if not runner.boot():
        logger.error("Boot failed. Exiting.")
        sys.exit(1)

    try:
        runner.run()
    except Exception as e:
        logger.error("Fatal error: %s", e)
        runner.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
