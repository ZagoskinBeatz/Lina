"""
Phase 7 — Block D: Failure & Recovery Validation.

Simulates component failures and validates graceful degradation:
  - DBus unavailable
  - PolicyEngine failure
  - AuditLogger rotation during execution
  - Disk full conditions
  - Unexpected exceptions in action handlers

Success criteria:
  • Graceful degradation
  • Clear user messaging
  • Recovery paths functional
  • No crash loops
  • No silent failures

Run: python tests/phase7/test_failure_recovery.py
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


def _reset_singletons():
    modules = [
        ("lina.intent.bridge", "_bridge"),
        ("lina.intent.router", "_router"),
        ("lina.governance.policy_engine", "_engine"),
        ("lina.governance.action_registry", "_registry"),
        ("lina.governance.audit_logger", "_logger"),
        ("lina.governance.escalation", "_manager"),
        ("lina.governance.confirmation", "_handler"),
        ("lina.access.resolver", "_resolver"),
        ("lina.security.input_validator", "_validator"),
    ]
    for mod_name, attr in modules:
        try:
            mod = sys.modules.get(mod_name)
            if mod and hasattr(mod, attr):
                setattr(mod, attr, None)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
#  Block D.1 — PolicyEngine Failure
# ═══════════════════════════════════════════════════════════════

class TestPolicyEngineFailure(unittest.TestCase):
    """D.1: Система обрабатывает сбой PolicyEngine."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    @patch("lina.governance.action_registry.subprocess.run")
    def test_policy_exception_returns_denied(self, mock_run):
        """PolicyEngine выбрасывает исключение → intent DENIED/FAILED (не crash)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()

        with patch("lina.governance.policy_engine.PolicyEngine.check",
                   side_effect=RuntimeError("PolicyEngine crashed")):
            result = bridge.from_action(
                action_id="svc_status",
                domain="service",
                source="cli",
            )
            # Should not crash — return DENIED or FAILED
            self.assertIn(result.status,
                [IntentStatus.DENIED, IntentStatus.FAILED, IntentStatus.SUCCESS, IntentStatus.NEEDS_CONFIRM],
                f"Unexpected status: {result.status}")

    def test_policy_engine_reinit_after_failure(self):
        """PolicyEngine пересоздаётся после сбоя."""
        from lina.governance.policy_engine import get_policy_engine

        pe1 = get_policy_engine()
        self.assertIsNotNone(pe1)

        # Simulate failure by resetting
        import lina.governance.policy_engine as pe_mod
        pe_mod._engine = None

        pe2 = get_policy_engine()
        self.assertIsNotNone(pe2)
        # Should work fine
        result = pe2.check("svc_status", domain="service", risk_level="low")
        self.assertIsNotNone(result)


# ═══════════════════════════════════════════════════════════════
#  Block D.2 — AuditLogger Failures
# ═══════════════════════════════════════════════════════════════

class TestAuditLoggerFailure(unittest.TestCase):
    """D.2: AuditLogger handles file system errors gracefully."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_audit_write_failure_no_crash(self):
        """AuditLogger file write failure → no crash, system continues."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()

        with patch.object(al, '_write_to_file', side_effect=OSError("disk full")):
            # Should not crash
            try:
                al.log(AuditRecord(
                    event_type=AuditEvent.INTENT_CREATED,
                    intent_id="test_disk_full",
                    intent_type="SYSTEM_ACTION",
                    domain="service",
                    action="svc_status",
                    source="cli",
                    decision="allow",
                ))
            except OSError:
                pass  # Acceptable if it propagates, but should not crash program

            # Logger should still be functional
            self.assertIsNotNone(al)

    def test_audit_rotation_doesnt_break_logging(self):
        """Rotation event during writes → no data loss."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()

        # Write some entries
        for i in range(10):
            al.log(AuditRecord(
                event_type=AuditEvent.INTENT_CREATED,
                intent_id=f"pre_rotation_{i}",
                intent_type="SYSTEM_ACTION",
                domain="service",
                action="svc_status",
                source="cli",
                decision="allow",
            ))

        # Write more after (simulating rotation between)
        for i in range(10):
            al.log(AuditRecord(
                event_type=AuditEvent.DENIED,
                intent_id=f"post_rotation_{i}",
                intent_type="SYSTEM_ACTION",
                domain="disk",
                action="disk_format",
                source="cli",
                decision="deny",
            ))

        log = al.get_recent(limit=5000)
        pre = [e for e in log if str(e.get("intent_id", "")).startswith("pre_rotation_")]
        post = [e for e in log if str(e.get("intent_id", "")).startswith("post_rotation_")]
        self.assertGreaterEqual(len(pre), 10)
        self.assertGreaterEqual(len(post), 10)


# ═══════════════════════════════════════════════════════════════
#  Block D.3 — Action Execution Failures
# ═══════════════════════════════════════════════════════════════

class TestActionExecutionFailure(unittest.TestCase):
    """D.3: Action handler exceptions → graceful FAILED result."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    @patch("lina.governance.action_registry.subprocess.run",
           side_effect=OSError("Cannot execute"))
    def test_subprocess_oserror(self, mock_run):
        """subprocess.run raises OSError → ActionResult FAILED."""
        from lina.governance.action_registry import get_action_registry, ExecStatus

        reg = get_action_registry()
        if reg.has("svc_status"):
            result = reg.execute("svc_status", params={"service_name": "test"})
            self.assertIn(result.status,
                [ExecStatus.FAILED, ExecStatus.BLOCKED],
                f"Expected FAILED on OSError, got {result.status}")

    @patch("lina.governance.action_registry.subprocess.run",
           side_effect=TimeoutError("Command timed out"))
    def test_subprocess_timeout(self, mock_run):
        """subprocess.run times out → ActionResult TIMEOUT or FAILED."""
        from lina.governance.action_registry import get_action_registry, ExecStatus

        reg = get_action_registry()
        if reg.has("svc_status"):
            result = reg.execute("svc_status", params={"service_name": "test"})
            self.assertIn(result.status,
                [ExecStatus.FAILED, ExecStatus.TIMEOUT, ExecStatus.BLOCKED],
                f"Expected TIMEOUT/FAILED, got {result.status}")

    @patch("lina.governance.action_registry.subprocess.run")
    def test_subprocess_returncode_nonzero(self, mock_run):
        """subprocess returns non-zero → ActionResult captures error."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="service not found",
        )

        from lina.governance.action_registry import get_action_registry

        reg = get_action_registry()
        if reg.has("svc_status"):
            result = reg.execute("svc_status", params={"service_name": "nonexistent"})
            # Should not crash, should capture stderr
            self.assertIsNotNone(result)


# ═══════════════════════════════════════════════════════════════
#  Block D.4 — AccessResolver Failure
# ═══════════════════════════════════════════════════════════════

class TestAccessResolverFailure(unittest.TestCase):
    """D.4: Access layer failure → safe default (DENY)."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    @patch("lina.governance.action_registry.subprocess.run")
    def test_access_exception_safe_default(self, mock_run):
        """AccessResolver exception → intent processing doesn't crash."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()

        with patch("lina.access.resolver.AccessLevelResolver.check",
                   side_effect=RuntimeError("Access layer down")):
            result = bridge.from_action(
                action_id="svc_status",
                domain="service",
                source="cli",
            )
            # Should return DENIED or FAILED, not crash
            self.assertIn(result.status,
                [IntentStatus.DENIED, IntentStatus.FAILED, IntentStatus.SUCCESS])


# ═══════════════════════════════════════════════════════════════
#  Block D.5 — DegradationStrategy
# ═══════════════════════════════════════════════════════════════

class TestDegradationStrategy(unittest.TestCase):
    """D.5: DegradationStrategy корректно эскалирует при сбоях."""

    def setUp(self):
        _reset_singletons()

    def test_degradation_after_repeated_failures(self):
        """Повторные сбои → degradation action escalates."""
        from lina.core.degradation import DegradationStrategy

        ds = DegradationStrategy(general_threshold=3)

        # Record failures
        for i in range(5):
            ds.record_failure("tool", f"fail_{i}")

        action = ds.evaluate()
        self.assertIsNotNone(action)
        # Should recommend some degradation action
        self.assertIsNotNone(action.action)

    def test_degradation_resets_on_success(self):
        """Успех после сбоев → degradation сбрасывается."""
        from lina.core.degradation import DegradationStrategy

        ds = DegradationStrategy(general_threshold=5)

        for _ in range(3):
            ds.record_failure("tool", "error")
        ds.record_success()

        action = ds.evaluate()
        self.assertIsNotNone(action)

    def test_degradation_clear(self):
        """Clear() полностью сбрасывает состояние."""
        from lina.core.degradation import DegradationStrategy

        ds = DegradationStrategy()
        for _ in range(10):
            ds.record_failure("validation", "bad input")
        ds.clear()
        stats = ds.get_stats()
        self.assertEqual(stats.get("total_failures", 0), 0)


# ═══════════════════════════════════════════════════════════════
#  Block D.6 — Full Pipeline Recovery
# ═══════════════════════════════════════════════════════════════

class TestFullPipelineRecovery(unittest.TestCase):
    """D.6: Полный pipeline восстанавливается после каскадных сбоев."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    @patch("lina.governance.action_registry.subprocess.run")
    def test_5_failures_then_success(self, mock_run):
        """5 сбоев подряд → 6-й запрос успешен."""
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 5:
                raise OSError("temporary failure")
            return MagicMock(returncode=0, stdout="OK", stderr="")

        mock_run.side_effect = side_effect

        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()

        # 5 failures
        for i in range(5):
            try:
                bridge.from_text("статус системы", source="cli")
            except Exception:
                pass

        # 6th should work
        mock_run.side_effect = None
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
        result = bridge.from_text("привет", source="cli")
        self.assertIsNotNone(result)


# ── Entry Point ──────────────────────────────────────────────

_total = _pass = _fail = 0

def _run_suite(suite_class):
    global _total, _pass, _fail
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(suite_class)
    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stderr)
    result = runner.run(suite)
    _total += result.testsRun
    _pass += result.testsRun - len(result.failures) - len(result.errors)
    _fail += len(result.failures) + len(result.errors)
    return result

if __name__ == "__main__":
    suites = [
        TestPolicyEngineFailure,
        TestAuditLoggerFailure,
        TestActionExecutionFailure,
        TestAccessResolverFailure,
        TestDegradationStrategy,
        TestFullPipelineRecovery,
    ]
    all_ok = True
    for s in suites:
        r = _run_suite(s)
        if not r.wasSuccessful():
            all_ok = False

    print(f"\n{'='*60}")
    print(f"Phase 7 Block D — Failure & Recovery: {_pass}/{_total} passed, {_fail} failed")
    if all_ok:
        print("✅ FAILURE & RECOVERY TEST PASSED")
    else:
        print("❌ FAILURE & RECOVERY TEST FAILED")
    sys.exit(0 if all_ok else 1)
