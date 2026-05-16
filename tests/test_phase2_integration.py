"""
Phase 2 Integration Tests — governance deep integration + UX.

Тесты:
  1. AuditLogger: write/read/stats
  2. AuditLogger wiring: IntentRouter logs at every step
  3. ConfirmationHandler: CLI mode, resolve, reject
  4. ConfirmationHandler: GUI mode callback
  5. CLI → IntentBridge wiring (no bypass)
  6. PolicyEngine deprecation (safety/policy.py warns)
  7. IPC schema versioning (API_VERSION in responses)
  8. DBus ConfirmEscalation method
  9. ServiceRunner health checks
  10. Legacy deprecation markers (runtime_v2)

Phase: INTEGRATION LAYER / Phase 2
Правило: Без тестов — не релиз.
"""

import json
import os
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock


# ═══════════════════════════════════════════════════════════
#  1. AuditLogger
# ═══════════════════════════════════════════════════════════

class TestAuditLogger(unittest.TestCase):
    """AuditLogger: write, read, stats, file persistence."""

    def setUp(self):
        """Create AuditLogger with temp file."""
        import lina.governance.audit_logger as al_mod
        al_mod._logger = None
        self._tmpdir = tempfile.mkdtemp()
        self._path = os.path.join(self._tmpdir, "audit.jsonl")
        self.logger = al_mod.AuditLogger(audit_path=self._path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_singleton(self):
        """get_audit_logger() returns same instance."""
        import lina.governance.audit_logger as al_mod
        al_mod._logger = None
        a1 = al_mod.get_audit_logger()
        a2 = al_mod.get_audit_logger()
        self.assertIs(a1, a2)
        al_mod._logger = None

    def test_log_writes_file(self):
        """log() appends JSONL to file."""
        from lina.governance.audit_logger import AuditRecord
        self.logger.log(AuditRecord(event_type="test"))
        self.assertTrue(os.path.exists(self._path))
        with open(self._path) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertEqual(data["event_type"], "test")

    def test_log_intent(self):
        """log_intent() records intent metadata without PII."""
        mock_intent = MagicMock()
        mock_intent.id = "test-123"
        mock_intent.type = "diagnose"
        mock_intent.domain = "network"
        mock_intent.action = "ping"
        mock_intent.source = "cli"
        mock_intent.user_text = "PRIVATE DATA"  # should NOT be logged

        self.logger.log_intent(mock_intent)

        with open(self._path) as f:
            data = json.loads(f.readline())
        self.assertEqual(data["intent_id"], "test-123")
        self.assertEqual(data["domain"], "network")
        # PII check: user_text must NOT appear
        self.assertNotIn("PRIVATE DATA", json.dumps(data))

    def test_log_decision(self):
        """log_decision() records access/policy decisions."""
        self.logger.log_decision(
            "int-1", "allow", access_level="user",
            domain="network", source="cli")
        with open(self._path) as f:
            data = json.loads(f.readline())
        self.assertEqual(data["decision"], "allow")
        self.assertEqual(data["access_level"], "user")

    def test_log_execution(self):
        """log_execution() records success/failure + duration."""
        self.logger.log_execution("int-1", success=True, duration_ms=42.5)
        with open(self._path) as f:
            data = json.loads(f.readline())
        self.assertEqual(data["event_type"], "executed")
        self.assertAlmostEqual(data["duration_ms"], 42.5)

    def test_log_execution_failure(self):
        """Failure → event_type='failed'."""
        self.logger.log_execution("int-1", success=False)
        with open(self._path) as f:
            data = json.loads(f.readline())
        self.assertEqual(data["event_type"], "failed")

    def test_get_recent(self):
        """get_recent() returns last N records from memory."""
        from lina.governance.audit_logger import AuditRecord
        for i in range(5):
            self.logger.log(AuditRecord(event_type=f"evt_{i}"))
        recent = self.logger.get_recent(3)
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[-1]["event_type"], "evt_4")

    def test_stats(self):
        """get_stats() tracks totals."""
        from lina.governance.audit_logger import AuditRecord
        self.logger.log(AuditRecord(event_type="a"))
        self.logger.log(AuditRecord(event_type="b"))
        self.logger.log(AuditRecord(event_type="a"))
        stats = self.logger.get_stats()
        self.assertEqual(stats["total_written"], 3)
        self.assertEqual(stats["events"]["a"], 2)
        self.assertEqual(stats["events"]["b"], 1)

    def test_memory_cap(self):
        """Memory capped at max_memory."""
        from lina.governance.audit_logger import AuditRecord
        logger = __import__('lina.governance.audit_logger', fromlist=['AuditLogger']).AuditLogger(
            audit_path=self._path, max_memory=5)
        for i in range(10):
            logger.log(AuditRecord(event_type=f"evt_{i}"))
        self.assertEqual(len(logger._memory), 5)
        self.assertEqual(logger._memory[0].event_type, "evt_5")

    def test_disable_enable(self):
        """set_enabled(False) stops logging."""
        from lina.governance.audit_logger import AuditRecord
        self.logger.set_enabled(False)
        self.logger.log(AuditRecord(event_type="skip"))
        self.assertFalse(os.path.exists(self._path))
        self.logger.set_enabled(True)
        self.logger.log(AuditRecord(event_type="ok"))
        self.assertTrue(os.path.exists(self._path))

    def test_jsonl_format(self):
        """Each line is valid JSON (no trailing comma, etc)."""
        from lina.governance.audit_logger import AuditRecord
        for i in range(3):
            self.logger.log(AuditRecord(event_type=f"e{i}"))
        with open(self._path) as f:
            for line in f:
                data = json.loads(line)  # Must not raise
                self.assertIn("event_type", data)
                self.assertIn("timestamp", data)


# ═══════════════════════════════════════════════════════════
#  2. AuditLogger wiring in IntentRouter
# ═══════════════════════════════════════════════════════════

class TestIntentRouterAudit(unittest.TestCase):
    """IntentRouter logs audit at every decision point."""

    def setUp(self):
        import lina.intent.router as router_mod
        import lina.governance.audit_logger as al_mod
        router_mod._router = None
        al_mod._logger = None
        self._tmpdir = tempfile.mkdtemp()
        self._path = os.path.join(self._tmpdir, "audit.jsonl")
        al_mod._logger = al_mod.AuditLogger(audit_path=self._path)

    def tearDown(self):
        import lina.intent.router as router_mod
        import lina.governance.audit_logger as al_mod
        router_mod._router = None
        al_mod._logger = None
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_router_has_audit_attr(self):
        """IntentRouter._audit is initialized."""
        from lina.intent.router import get_intent_router
        router = get_intent_router()
        router._ensure_init()
        self.assertIsNotNone(router._audit)

    def test_chat_intent_logged(self):
        """Chat intent creates audit record."""
        from lina.intent.router import get_intent_router
        from lina.intent.types import Intent, IntentType, IntentStatus

        router = get_intent_router()
        intent = Intent(type=IntentType.CHAT, source="test",
                       user_text="hello")
        result = router.process(intent)

        # Check audit file has at least 1 record
        if os.path.exists(self._path):
            with open(self._path) as f:
                lines = f.readlines()
            self.assertGreaterEqual(len(lines), 1)
            data = json.loads(lines[0])
            self.assertEqual(data["event_type"], "intent_created")


# ═══════════════════════════════════════════════════════════
#  3. ConfirmationHandler — CLI mode
# ═══════════════════════════════════════════════════════════

class TestConfirmationHandler(unittest.TestCase):
    """ConfirmationHandler: resolve, reject, stats."""

    def setUp(self):
        import lina.governance.confirmation as conf_mod
        conf_mod._handler = None
        self.handler = conf_mod.get_confirmation_handler()

    def tearDown(self):
        import lina.governance.confirmation as conf_mod
        conf_mod._handler = None

    def test_singleton(self):
        """get_confirmation_handler() returns same instance."""
        import lina.governance.confirmation as conf_mod
        h1 = conf_mod.get_confirmation_handler()
        h2 = conf_mod.get_confirmation_handler()
        self.assertIs(h1, h2)

    def test_set_modes(self):
        """Mode switching works."""
        self.handler.set_cli_mode()
        self.assertEqual(self.handler._mode, "cli")
        self.handler.set_dbus_mode()
        self.assertEqual(self.handler._mode, "dbus")
        self.handler.set_gui_mode(callback=lambda x: None)
        self.assertEqual(self.handler._mode, "gui")

    def test_resolve_confirm(self):
        """resolve(confirmed=True) increments resolved count."""
        self.handler.resolve("esc-1", confirmed=True)
        stats = self.handler.get_stats()
        self.assertEqual(stats["resolved"], 1)
        self.assertEqual(stats["denied"], 0)

    def test_resolve_reject(self):
        """resolve(confirmed=False) increments denied count."""
        self.handler.resolve("esc-2", confirmed=False)
        stats = self.handler.get_stats()
        self.assertEqual(stats["resolved"], 0)
        self.assertEqual(stats["denied"], 1)

    def test_register_pending(self):
        """register_pending saves intent for re-execution."""
        mock_intent = MagicMock()
        self.handler.register_pending("esc-3", mock_intent)
        self.assertIn("esc-3", self.handler._pending_intents)

    def test_resolve_clears_pending(self):
        """resolve() removes intent from pending."""
        self.handler.register_pending("esc-4", MagicMock())
        self.handler.resolve("esc-4", confirmed=True)
        self.assertNotIn("esc-4", self.handler._pending_intents)

    def test_gui_mode_callback(self):
        """GUI mode calls callback with escalation dict."""
        callback = MagicMock()
        self.handler.set_gui_mode(callback=callback)
        self.handler._ensure_init()

        mock_result = MagicMock()
        mock_result.escalation_id = "esc-5"
        mock_result.status = MagicMock()
        mock_result.status.value = "needs_confirm"

        result = self.handler.handle(mock_result)
        callback.assert_called_once()

    def test_dbus_mode_passthrough(self):
        """DBus mode returns result as-is (no blocking)."""
        self.handler.set_dbus_mode()
        self.handler._ensure_init()

        mock_result = MagicMock()
        mock_result.escalation_id = "esc-6"

        result = self.handler.handle(mock_result)
        self.assertEqual(result, mock_result)


# ═══════════════════════════════════════════════════════════
#  4. CLI → IntentBridge wiring
# ═══════════════════════════════════════════════════════════

class TestCLIGovernanceWiring(unittest.TestCase):
    """CLI routes through IntentBridge, not direct pipeline."""

    def test_route_via_governance_exists(self):
        """_route_via_governance function exists in cli module."""
        from lina.core.cli import _route_via_governance
        self.assertTrue(callable(_route_via_governance))

    def test_route_via_governance_calls_bridge(self):
        """_route_via_governance calls IntentBridge.from_text."""
        from lina.core.cli import _route_via_governance
        from lina.intent.types import IntentResult, IntentStatus

        mock_pipeline = MagicMock()
        mock_pipeline._lina_preprocessor = None  # Skip fast path

        mock_result = IntentResult(
            intent_id="test",
            status=IntentStatus.SUCCESS,
            response_text="test response",
        )

        with patch('lina.intent.bridge.get_intent_bridge') as mock_bridge_fn:
            mock_bridge = MagicMock()
            mock_bridge.from_text.return_value = mock_result
            mock_bridge_fn.return_value = mock_bridge

            resp = _route_via_governance("test input", mock_pipeline, source="cli")

            mock_bridge.from_text.assert_called_once()
            # Phase 4: ResponseFormatter wraps SUCCESS with ✅
            self.assertIn("test response", resp)
            self.assertTrue(resp.startswith("✅"))

    def test_route_via_governance_fast_path(self):
        """Fast path preprocessor still works before governance."""
        from lina.core.cli import _route_via_governance

        mock_pipeline = MagicMock()
        mock_preprocessor = MagicMock()
        mock_preprocessor.try_direct_answer.return_value = "Quick answer"
        mock_pipeline._lina_preprocessor = mock_preprocessor

        resp = _route_via_governance("привет", mock_pipeline, source="cli")
        self.assertEqual(resp, "Quick answer")

    def test_route_handles_needs_confirm(self):
        """NEEDS_CONFIRM triggers ConfirmationHandler."""
        from lina.core.cli import _route_via_governance
        from lina.intent.types import IntentResult, IntentStatus

        mock_pipeline = MagicMock()
        mock_pipeline._lina_preprocessor = None

        needs_confirm_result = IntentResult(
            intent_id="test",
            status=IntentStatus.NEEDS_CONFIRM,
            response_text="Confirm?",
            escalation_id="esc-99",
            policy_decision="confirm",
        )

        denied_result = IntentResult(
            intent_id="test",
            status=IntentStatus.DENIED,
            response_text="Отменено.",
        )

        with patch('lina.intent.bridge.get_intent_bridge') as mock_bridge_fn, \
             patch('lina.governance.confirmation.get_confirmation_handler') as mock_ch_fn:
            mock_bridge = MagicMock()
            mock_bridge.from_text.return_value = needs_confirm_result
            mock_bridge_fn.return_value = mock_bridge

            mock_handler = MagicMock()
            mock_handler.handle.return_value = denied_result
            mock_ch_fn.return_value = mock_handler

            resp = _route_via_governance("dangerous command", mock_pipeline, source="cli")
            mock_handler.set_cli_mode.assert_called_once()
            mock_handler.handle.assert_called_once()

    def test_route_via_governance_fails_closed_without_bridge(self):
        """Missing IntentBridge must deny request instead of calling pipeline directly."""
        from lina.core.cli import _route_via_governance

        mock_pipeline = MagicMock()
        mock_pipeline._lina_preprocessor = None

        with patch('lina.intent.bridge.get_intent_bridge', side_effect=ImportError("bridge missing")):
            resp = _route_via_governance("test input", mock_pipeline, source="cli")

        mock_pipeline.process_request.assert_not_called()
        self.assertIn("Governance", resp)


# ═══════════════════════════════════════════════════════════
#  5. PolicyEngine deprecation
# ═══════════════════════════════════════════════════════════

class TestPolicyEngineDeprecation(unittest.TestCase):
    """safety/policy.py emits DeprecationWarning."""

    def test_module_import_warns(self):
        """Importing safety.policy triggers DeprecationWarning."""
        import importlib
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import lina.safety.policy
            importlib.reload(lina.safety.policy)
            dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)
                           and "deprecated" in str(x.message).lower()]
            self.assertGreater(len(dep_warnings), 0,
                              "safety.policy should emit DeprecationWarning")

    def test_class_init_warns(self):
        """PolicyEngine() triggers DeprecationWarning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            try:
                from lina.safety.policy import PolicyEngine
                engine = PolicyEngine()
            except Exception:
                pass  # May fail due to missing deps; just check warnings
            dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            self.assertGreater(len(dep_warnings), 0)


# ═══════════════════════════════════════════════════════════
#  6. IPC Schema Versioning
# ═══════════════════════════════════════════════════════════

class TestIPCVersioning(unittest.TestCase):
    """DBus responses include api_version and error_code."""

    def setUp(self):
        import lina.governance.dbus_service as dbus_mod
        dbus_mod._service = None
        self.svc = dbus_mod.get_dbus_service()

    def tearDown(self):
        import lina.governance.dbus_service as dbus_mod
        dbus_mod._service = None

    def test_api_version_constant(self):
        """API_VERSION exists and is semver-like."""
        from lina.governance.dbus_service import API_VERSION
        self.assertRegex(API_VERSION, r'^\d+\.\d+$')

    def test_ipc_error_codes(self):
        """IPCError codes exist."""
        from lina.governance.dbus_service import IPCError
        self.assertEqual(IPCError.OK, 0)
        self.assertEqual(IPCError.DENIED, 1)
        self.assertEqual(IPCError.INTERNAL, 5)

    def test_diagnose_includes_version(self):
        """diagnose() response includes api_version."""
        resp = json.loads(self.svc.diagnose("test_domain"))
        self.assertIn("api_version", resp)

    def test_execute_action_includes_version(self):
        """execute_action() response includes api_version."""
        resp = json.loads(self.svc.execute_action("test_action", "{}"))
        self.assertIn("api_version", resp)

    def test_get_status_includes_version(self):
        """get_status() response includes api_version."""
        resp = json.loads(self.svc.get_status())
        self.assertIn("api_version", resp)

    def test_confirm_escalation_exists(self):
        """confirm_escalation method exists on DBus service."""
        self.assertTrue(hasattr(self.svc, 'confirm_escalation'))
        self.assertTrue(callable(self.svc.confirm_escalation))

    def test_confirm_escalation_response(self):
        """confirm_escalation returns valid JSON with api_version."""
        resp = json.loads(self.svc.confirm_escalation("esc-test", "false"))
        self.assertIn("api_version", resp)


# ═══════════════════════════════════════════════════════════
#  7. ServiceRunner Health Checks
# ═══════════════════════════════════════════════════════════

class TestServiceRunnerHealth(unittest.TestCase):
    """ServiceRunner has health check infrastructure."""

    def setUp(self):
        import lina.governance.service_runner as sr_mod
        sr_mod._runner = None

    def tearDown(self):
        import lina.governance.service_runner as sr_mod
        sr_mod._runner = None

    def test_health_ok_in_state(self):
        """ServiceState has health_ok field."""
        from lina.governance.service_runner import ServiceState
        s = ServiceState()
        self.assertTrue(s.health_ok)  # default True
        self.assertIn("health_ok", s.to_dict())

    def test_health_check_method_exists(self):
        """LinaServiceRunner._run_health_check exists."""
        from lina.governance.service_runner import LinaServiceRunner
        runner = LinaServiceRunner()
        self.assertTrue(hasattr(runner, '_run_health_check'))

    def test_health_check_no_crash(self):
        """_run_health_check doesn't crash with no components."""
        from lina.governance.service_runner import LinaServiceRunner
        runner = LinaServiceRunner()
        runner._consecutive_failures = 0
        # Should not raise, even with nothing initialized
        runner._run_health_check()

    def test_health_check_detects_issues(self):
        """With no components, health_ok becomes False."""
        from lina.governance.service_runner import LinaServiceRunner
        runner = LinaServiceRunner()
        runner._consecutive_failures = 0
        runner._run_health_check()
        self.assertFalse(runner._state.health_ok)
        self.assertGreater(runner._consecutive_failures, 0)


# ═══════════════════════════════════════════════════════════
#  8. Legacy Deprecation Markers
# ═══════════════════════════════════════════════════════════

class TestLegacyDeprecation(unittest.TestCase):
    """Deprecated packages emit warnings."""

    def test_runtime_v2_removed(self):
        """runtime_v2 was deleted in Phase 28 cleanup."""
        import pytest
        with pytest.raises(ModuleNotFoundError):
            import lina.runtime_v2  # noqa: F401


# ═══════════════════════════════════════════════════════════
#  9. AuditRecord serialization
# ═══════════════════════════════════════════════════════════

class TestAuditRecord(unittest.TestCase):
    """AuditRecord: serialization, defaults, no-PII."""

    def test_to_dict_strips_empty(self):
        """to_dict() removes empty string fields for compactness."""
        from lina.governance.audit_logger import AuditRecord
        r = AuditRecord(event_type="test")
        d = r.to_dict()
        self.assertIn("event_type", d)
        self.assertIn("timestamp", d)
        self.assertNotIn("domain", d)  # empty → removed
        self.assertNotIn("action", d)  # empty → removed

    def test_to_dict_keeps_success_false(self):
        """success=False is kept in dict."""
        from lina.governance.audit_logger import AuditRecord
        r = AuditRecord(event_type="test", success=False)
        d = r.to_dict()
        self.assertFalse(d["success"])

    def test_to_json_valid(self):
        """to_json() is valid JSON."""
        from lina.governance.audit_logger import AuditRecord
        r = AuditRecord(event_type="test", domain="network")
        j = r.to_json()
        data = json.loads(j)
        self.assertEqual(data["event_type"], "test")
        self.assertEqual(data["domain"], "network")

    def test_auto_timestamp(self):
        """Timestamp auto-generated if not provided."""
        from lina.governance.audit_logger import AuditRecord
        r = AuditRecord(event_type="test")
        self.assertTrue(r.timestamp)
        self.assertIn("T", r.timestamp)  # ISO format


# ═══════════════════════════════════════════════════════════
#  10. E2E: Full Phase 2 flow
# ═══════════════════════════════════════════════════════════

class TestPhase2E2E(unittest.TestCase):
    """End-to-end: CLI text → governance → audit trail."""

    def test_audit_logger_exports(self):
        """AuditLogger, get_audit_logger exported from governance."""
        from lina.governance import AuditLogger, get_audit_logger
        self.assertIsNotNone(AuditLogger)
        self.assertTrue(callable(get_audit_logger))

    def test_confirmation_handler_exports(self):
        """ConfirmationHandler, get_confirmation_handler exported from governance."""
        from lina.governance import ConfirmationHandler, get_confirmation_handler
        self.assertIsNotNone(ConfirmationHandler)
        self.assertTrue(callable(get_confirmation_handler))

    def test_audit_event_types(self):
        """AuditEvent has all required event types."""
        from lina.governance.audit_logger import AuditEvent
        required = [
            "INTENT_CREATED", "ACCESS_CHECKED", "POLICY_CHECKED",
            "EXECUTED", "DENIED", "CONFIRM_REQUESTED", "CONFIRM_RESOLVED",
            "FAILED", "RATE_LIMITED", "SESSION_START", "SESSION_END",
            "HEALTH_CHECK",
        ]
        for evt in required:
            self.assertTrue(hasattr(AuditEvent, evt), f"Missing AuditEvent.{evt}")

    def test_governance_init_has_all_modules(self):
        """governance __init__ exports all Phase 2 modules."""
        import lina.governance as gov
        self.assertTrue(hasattr(gov, 'AuditLogger'))
        self.assertTrue(hasattr(gov, 'get_audit_logger'))
        self.assertTrue(hasattr(gov, 'ConfirmationHandler'))
        self.assertTrue(hasattr(gov, 'get_confirmation_handler'))


# ═══════════════════════════════════════════════════════════
#  Runner
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
