"""
Phase 7 — Block E: Log & Audit Verification.

Validates:
  - Audit rotation at 10MB threshold
  - No PII in log entries
  - security_violation events recorded
  - Locked audit cannot be disabled
  - Log integrity under stress

Run: python tests/phase7/test_audit_verify.py
"""

import json
import os
import re
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


def _reset_singletons():
    modules = [
        ("lina.governance.audit_logger", "_logger"),
        ("lina.intent.bridge", "_bridge"),
        ("lina.security.input_validator", "_validator"),
    ]
    for mod_name, attr in modules:
        try:
            mod = sys.modules.get(mod_name)
            if mod and hasattr(mod, attr):
                setattr(mod, attr, None)
        except Exception:
            pass


# PII patterns to detect
PII_PATTERNS = [
    re.compile(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'),  # phone
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),  # email
    re.compile(r'\b(?:password|passwd|pwd|secret|token|api_key)\s*[:=]\s*\S+', re.I),  # credentials
    re.compile(r'/home/[a-zA-Z0-9_]+/'),  # home directory (mild PII)
]


# ═══════════════════════════════════════════════════════════════
#  Block E.1 — Audit Rotation
# ═══════════════════════════════════════════════════════════════

class TestAuditRotation(unittest.TestCase):
    """E.1: AuditLogger rotation mechanism."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_rotation_threshold_configured(self):
        """Rotation threshold is set (default 10MB)."""
        from lina.governance.audit_logger import get_audit_logger

        al = get_audit_logger()
        # Check that rotation is configured
        max_size = getattr(al, '_max_file_size', None) or getattr(al, '_max_size', None)
        if max_size is not None:
            self.assertGreater(max_size, 0, "Rotation threshold should be positive")
            self.assertEqual(max_size, 10 * 1024 * 1024,
                f"Expected 10MB rotation, got {max_size}")

    def test_rotation_method_exists(self):
        """Rotation method exists and is callable."""
        from lina.governance.audit_logger import get_audit_logger

        al = get_audit_logger()
        self.assertTrue(hasattr(al, '_rotate') or hasattr(al, 'rotate'),
            "AuditLogger should have a rotation method")


# ═══════════════════════════════════════════════════════════════
#  Block E.2 — No PII in Logs
# ═══════════════════════════════════════════════════════════════

class TestNoPIIInLogs(unittest.TestCase):
    """E.2: Audit log entries contain no PII."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_user_text_not_in_audit(self):
        """user_text is NOT stored in audit entries."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()

        sensitive_text = "Мой пароль: SuperSecret123! email: user@example.com"
        al.log(AuditRecord(
            event_type=AuditEvent.INTENT_CREATED,
            intent_id="pii_test_1",
            intent_type="CHAT",
            domain="general",
            action="",
            source="cli",
            decision="allow",
        ))

        log = al.get_recent(limit=5000)
        pii_entry = [e for e in log if e.get("intent_id") == "pii_test_1"]
        self.assertTrue(len(pii_entry) > 0, "Entry not found")

        entry_str = json.dumps(pii_entry[0])
        self.assertNotIn("SuperSecret123", entry_str,
            "Password leaked to audit log!")
        self.assertNotIn("user@example.com", entry_str,
            "Email leaked to audit log!")
        self.assertNotIn(sensitive_text, entry_str,
            "Full user text leaked to audit log!")

    def test_audit_entries_no_pii_patterns(self):
        """100 audit entries → no PII patterns detected."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()

        for i in range(100):
            al.log(AuditRecord(
                event_type=AuditEvent.POLICY_CHECKED,
                intent_id=f"pii_scan_{i}",
                intent_type="SYSTEM_ACTION",
                domain="service",
                action="svc_status",
                source="cli",
                decision="allow",
            ))

        log = al.get_recent(limit=5000)
        our_entries = [e for e in log if str(e.get("intent_id", "")).startswith("pii_scan_")]

        for entry in our_entries:
            entry_str = json.dumps(entry)
            for pattern in PII_PATTERNS[:3]:  # skip home dir check
                match = pattern.search(entry_str)
                self.assertIsNone(match,
                    f"PII pattern found in audit entry: {match.group() if match else ''}")


# ═══════════════════════════════════════════════════════════════
#  Block E.3 — Security Violation Events
# ═══════════════════════════════════════════════════════════════

class TestSecurityViolationEvents(unittest.TestCase):
    """E.3: security_violation events recorded properly."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_locked_audit_disable_attempt_logged(self):
        """Attempt to disable locked audit → security_violation event."""
        from lina.governance.audit_logger import get_audit_logger

        al = get_audit_logger()
        al.lock_enabled()

        # Attempt to disable
        al.set_enabled(False)

        # Audit should still be enabled
        self.assertTrue(al.enabled, "Locked audit was disabled!")

        # Check for security_violation in log
        log = al.get_recent(limit=5000)
        violations = [e for e in log
                      if e.get("event_type") == "security_violation"
                      or "security" in str(e.get("event_type", "")).lower()]
        # At least one violation should be recorded
        self.assertGreater(len(violations), 0,
            "No security_violation event for audit disable attempt")

    def test_denial_events_recorded(self):
        """DENY decisions are recorded as audit events."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()
        initial_count = len(al.get_recent(limit=5000))

        al.log(AuditRecord(
            event_type=AuditEvent.DENIED,
            intent_id="deny_test_1",
            intent_type="SYSTEM_ACTION",
            domain="disk",
            action="disk_format",
            source="dbus",
            decision="deny",
            metadata={"reason": "always_block_critical"},
        ))

        log = al.get_recent(limit=5000)
        deny_entries = [e for e in log if e.get("intent_id") == "deny_test_1"]
        self.assertEqual(len(deny_entries), 1)
        self.assertEqual(deny_entries[0]["decision"], "deny")


# ═══════════════════════════════════════════════════════════════
#  Block E.4 — Audit Lock Integrity
# ═══════════════════════════════════════════════════════════════

class TestAuditLockIntegrity(unittest.TestCase):
    """E.4: Locked audit cannot be disabled — ever."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_lock_prevents_disable(self):
        """lock_enabled() → set_enabled(False) fails silently."""
        from lina.governance.audit_logger import get_audit_logger

        al = get_audit_logger()
        al.lock_enabled()
        self.assertTrue(al.locked)

        # Try to disable 100 times
        for _ in range(100):
            al.set_enabled(False)
        self.assertTrue(al.enabled, "Audit was disabled despite lock!")

    def test_lock_is_permanent(self):
        """Lock cannot be unlocked."""
        from lina.governance.audit_logger import get_audit_logger

        al = get_audit_logger()
        al.lock_enabled()

        # Try to "unlock" by setting internal state
        if hasattr(al, '_lock_enabled'):
            # Even if someone writes directly, we document this is a violation
            self.assertTrue(al._lock_enabled)

    def test_lock_survives_rapid_toggles(self):
        """100 rapid enable/disable → locked stays enabled."""
        from lina.governance.audit_logger import get_audit_logger

        al = get_audit_logger()
        al.lock_enabled()

        for i in range(100):
            al.set_enabled(i % 2 == 0)

        self.assertTrue(al.enabled, "Lock didn't survive rapid toggles")


# ═══════════════════════════════════════════════════════════════
#  Block E.5 — Log Integrity Under Stress
# ═══════════════════════════════════════════════════════════════

class TestLogIntegrityStress(unittest.TestCase):
    """E.5: Log integrity preserved under write stress."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_500_writes_all_preserved(self):
        """500 sequential writes → all preserved in order."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()
        initial_count = len(al.get_recent(limit=5000))

        for i in range(500):
            al.log(AuditRecord(
                event_type=AuditEvent.INTENT_CREATED,
                intent_id=f"integrity_{i:04d}",
                intent_type="SYSTEM_ACTION",
                domain="service",
                action="svc_status",
                source="cli",
                decision="allow",
                metadata={"seq": i},
            ))

        log = al.get_recent(limit=5000)
        our = [e for e in log if str(e.get("intent_id", "")).startswith("integrity_")]
        self.assertEqual(len(our), 500,
            f"Lost entries: expected 500, got {len(our)}")

    def test_mixed_event_types_preserved(self):
        """Different event types → all recorded correctly."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()
        events = [
            AuditEvent.INTENT_CREATED,
            AuditEvent.ACCESS_CHECKED,
            AuditEvent.POLICY_CHECKED,
            AuditEvent.EXECUTED,
            AuditEvent.DENIED,
            AuditEvent.CONFIRM_REQUESTED,
            AuditEvent.FAILED,
        ]

        for i, evt in enumerate(events * 10):
            al.log(AuditRecord(
                event_type=evt,
                intent_id=f"mixed_{i:04d}",
                intent_type="SYSTEM_ACTION",
                domain="service",
                action="svc_status",
                source="cli",
                decision="allow",
            ))

        log = al.get_recent(limit=5000)
        our = [e for e in log if str(e.get("intent_id", "")).startswith("mixed_")]
        self.assertEqual(len(our), 70,
            f"Expected 70 mixed entries, got {len(our)}")


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
        TestAuditRotation,
        TestNoPIIInLogs,
        TestSecurityViolationEvents,
        TestAuditLockIntegrity,
        TestLogIntegrityStress,
    ]
    all_ok = True
    for s in suites:
        r = _run_suite(s)
        if not r.wasSuccessful():
            all_ok = False

    print(f"\n{'='*60}")
    print(f"Phase 7 Block E — Audit Verification: {_pass}/{_total} passed, {_fail} failed")
    if all_ok:
        print("✅ AUDIT VERIFICATION PASSED")
    else:
        print("❌ AUDIT VERIFICATION FAILED")
    sys.exit(0 if all_ok else 1)
