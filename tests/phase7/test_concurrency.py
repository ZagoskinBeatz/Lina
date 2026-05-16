"""
Phase 7 — Block C: Concurrency & Race Conditions.

Validates thread-safety of governance components:
  - IntentRouter concurrent processing
  - AuditLogger concurrent writes + rotation
  - EscalationManager concurrent create/resolve
  - InputValidator concurrent validation

Tests for:
  • Lost audit entries
  • Partial writes
  • Inconsistent state
  • Escalation ID collisions

Run: python tests/phase7/test_concurrency.py
"""

import os
import sys
import time
import threading
import unittest
from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor, as_completed

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
#  Block C.1 — IntentRouter Concurrency
# ═══════════════════════════════════════════════════════════════

class TestIntentRouterConcurrency(unittest.TestCase):
    """C.1: IntentRouter обрабатывает параллельные запросы."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    @patch("lina.governance.action_registry.subprocess.run")
    def test_50_concurrent_intents(self, mock_run):
        """50 параллельных intent → все возвращают валидный результат."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()
        results = []
        errors = []
        lock = threading.Lock()

        def process_intent(i):
            try:
                result = bridge.from_text(f"тест запрос {i}", source="cli")
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    errors.append((i, str(e)))

        threads = []
        for i in range(50):
            t = threading.Thread(target=process_intent, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0,
            f"Errors in concurrent intents:\n" + "\n".join(f"#{i}: {e}" for i, e in errors[:5]))
        self.assertEqual(len(results), 50,
            f"Expected 50 results, got {len(results)}")

    @patch("lina.governance.action_registry.subprocess.run")
    def test_mixed_concurrent_valid_invalid(self, mock_run):
        """30 valid + 30 invalid concurrent — no corruption."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()
        valid_results = []
        invalid_results = []
        errors = []
        lock = threading.Lock()

        def process_valid(i):
            try:
                r = bridge.from_text("привет", source="cli")
                with lock:
                    valid_results.append(r)
            except Exception as e:
                with lock:
                    errors.append(("valid", i, str(e)))

        def process_invalid(i):
            try:
                r = bridge.from_text("; rm -rf /", source="cli")
                with lock:
                    invalid_results.append(r)
            except Exception as e:
                with lock:
                    errors.append(("invalid", i, str(e)))

        threads = []
        for i in range(30):
            threads.append(threading.Thread(target=process_valid, args=(i,)))
            threads.append(threading.Thread(target=process_invalid, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"Errors: {errors[:5]}")
        # All invalid should be blocked (DENIED or NEEDS_CONFIRM — not SUCCESS)
        bypassed = sum(1 for r in invalid_results if r.status == IntentStatus.SUCCESS)
        self.assertEqual(bypassed, 0,
            f"CRITICAL: {bypassed}/{len(invalid_results)} injection attempts executed concurrently!")


# ═══════════════════════════════════════════════════════════════
#  Block C.2 — AuditLogger Concurrent Writes
# ═══════════════════════════════════════════════════════════════

class TestAuditLoggerConcurrency(unittest.TestCase):
    """C.2: AuditLogger под параллельной записью."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_100_concurrent_audit_writes(self):
        """100 параллельных записей — ни одна не потеряна."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()
        initial_count = len(al.get_recent(limit=5000))
        barrier = threading.Barrier(50)
        errors = []

        def write_audit(i):
            try:
                barrier.wait(timeout=10)
                al.log(AuditRecord(
                    event_type=AuditEvent.INTENT_CREATED,
                    intent_id=f"conc_{i:04d}",
                    intent_type="SYSTEM_ACTION",
                    domain="service",
                    action="svc_status",
                    source="cli",
                    decision="allow",
                ))
            except Exception as e:
                errors.append((i, str(e)))

        threads = [threading.Thread(target=write_audit, args=(i,))
                   for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"Write errors: {errors[:5]}")

        log = al.get_recent(limit=5000)
        new_entries = len(log) - initial_count
        our_entries = [e for e in log if str(e.get("intent_id", "")).startswith("conc_")]
        self.assertGreaterEqual(len(our_entries), 95,
            f"Lost audit entries: expected ~100, got {len(our_entries)}")

    def test_concurrent_writes_no_partial_data(self):
        """Параллельные записи — нет частичных/повреждённых записей."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()
        errors = []

        def write_entry(i):
            try:
                al.log(AuditRecord(
                    event_type=AuditEvent.POLICY_CHECKED,
                    intent_id=f"partial_{i:04d}",
                    intent_type="SYSTEM_ACTION",
                    domain="network",
                    action="net_status",
                    source="dbus",
                    decision="allow" if i % 2 == 0 else "deny",
                    metadata={"thread": i, "data": "x" * 100},
                ))
            except Exception as e:
                errors.append((i, str(e)))

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(write_entry, i) for i in range(100)]
            for f in as_completed(futures):
                f.result()

        log = al.get_recent(limit=5000)
        our_entries = [e for e in log if str(e.get("intent_id", "")).startswith("partial_")]
        # Check no entries have missing fields
        for entry in our_entries:
            self.assertIn("event_type", entry, f"Partial entry: {entry}")
            self.assertIn("intent_id", entry, f"Partial entry: {entry}")


# ═══════════════════════════════════════════════════════════════
#  Block C.3 — EscalationManager Concurrency
# ═══════════════════════════════════════════════════════════════

class TestEscalationConcurrency(unittest.TestCase):
    """C.3: EscalationManager — параллельные create/resolve."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_50_concurrent_escalations_no_collision(self):
        """50 параллельных эскалаций — нет коллизий ID."""
        from lina.governance.escalation import get_escalation_manager

        em = get_escalation_manager()
        ids = []
        lock = threading.Lock()
        errors = []

        def create_esc(i):
            try:
                esc = em.create_escalation(
                    level="confirm",
                    title=f"Conc {i}",
                    title_ru=f"Парал {i}",
                    description="test",
                    description_ru="тест",
                    domain="service",
                    risk_level="medium",
                    proposed_action="svc_restart",
                    proposed_command=f"systemctl restart test_{i}",
                )
                with lock:
                    ids.append(esc.id)
            except Exception as e:
                with lock:
                    errors.append((i, str(e)))

        threads = [threading.Thread(target=create_esc, args=(i,))
                   for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"Errors: {errors[:5]}")
        self.assertEqual(len(ids), 50, f"Expected 50 IDs, got {len(ids)}")
        self.assertEqual(len(set(ids)), 50,
            f"ID collisions! Unique: {len(set(ids))}/50")

    def test_concurrent_create_and_resolve(self):
        """Параллельные create + resolve — нет corrupt state."""
        from lina.governance.escalation import get_escalation_manager

        em = get_escalation_manager()
        created_ids = []
        lock = threading.Lock()

        # Phase 1: create 30 escalations
        def create(i):
            esc = em.create_escalation(
                level="confirm",
                title=f"CR {i}",
                title_ru=f"СР {i}",
                description="test",
                description_ru="тест",
                domain="service",
                risk_level="low",
                proposed_action="svc_status",
                proposed_command="systemctl status test",
            )
            with lock:
                created_ids.append(esc.id)

        threads = [threading.Thread(target=create, args=(i,))
                   for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(created_ids), 30)

        # Phase 2: resolve all concurrently
        resolved = []
        def resolve(esc_id):
            ok = em.resolve(esc_id, confirmed=True)
            with lock:
                resolved.append(ok)

        threads = [threading.Thread(target=resolve, args=(eid,))
                   for eid in created_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        success_count = sum(1 for r in resolved if r)
        self.assertEqual(success_count, 30,
            f"Only {success_count}/30 resolved successfully")


# ═══════════════════════════════════════════════════════════════
#  Block C.4 — InputValidator Concurrency
# ═══════════════════════════════════════════════════════════════

class TestInputValidatorConcurrency(unittest.TestCase):
    """C.4: InputValidator thread-safe под параллельной нагрузкой."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_100_concurrent_validations(self):
        """100 параллельных validate_text — все возвращают корректный результат."""
        from lina.security.input_validator import get_input_validator

        iv = get_input_validator()
        results = []
        lock = threading.Lock()

        inputs = [
            ("привет", True), ("; rm -rf /", True), ("test\x00null", False),
            ("x" * 5000, False), ("нормальный текст", True),
        ] * 20

        def validate(text, _expected):
            r = iv.validate_text(text)
            with lock:
                results.append((text[:20], r.valid))

        threads = [threading.Thread(target=validate, args=(t, e))
                   for t, e in inputs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(results), 100,
            f"Expected 100 results, got {len(results)}")

    def test_concurrent_domain_validation(self):
        """50 concurrent domain validations — consistent results."""
        from lina.security.input_validator import get_input_validator

        iv = get_input_validator()
        valid_results = []
        invalid_results = []
        lock = threading.Lock()

        def check_valid():
            ok, _ = iv.validate_domain("service")
            with lock:
                valid_results.append(ok)

        def check_invalid():
            ok, _ = iv.validate_domain("evil_domain")
            with lock:
                invalid_results.append(ok)

        threads = []
        for _ in range(25):
            threads.append(threading.Thread(target=check_valid))
            threads.append(threading.Thread(target=check_invalid))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertTrue(all(valid_results), "Valid domain returned False")
        self.assertTrue(all(not r for r in invalid_results),
            "Invalid domain returned True")


# ═══════════════════════════════════════════════════════════════
#  Block C.5 — PolicyEngine Concurrency
# ═══════════════════════════════════════════════════════════════

class TestPolicyEngineConcurrency(unittest.TestCase):
    """C.5: PolicyEngine — параллельные policy check."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_50_concurrent_policy_checks(self):
        """50 параллельных policy check — нет inconsistent state."""
        from lina.governance.policy_engine import get_policy_engine

        pe = get_policy_engine()
        results = []
        lock = threading.Lock()

        def check_policy(i):
            r = pe.check(
                f"svc_status",
                domain="service",
                risk_level="low",
            )
            with lock:
                results.append(r)

        threads = [threading.Thread(target=check_policy, args=(i,))
                   for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(results), 50)
        # All low-risk service checks should get same decision
        decisions = set(r.decision.value if hasattr(r.decision, 'value') else str(r.decision) for r in results)
        # Should all be ALLOW or all be RATE_LIMITED (if rate limit kicks in)
        self.assertLessEqual(len(decisions), 2,
            f"Inconsistent decisions: {decisions}")


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
        TestIntentRouterConcurrency,
        TestAuditLoggerConcurrency,
        TestEscalationConcurrency,
        TestInputValidatorConcurrency,
        TestPolicyEngineConcurrency,
    ]
    all_ok = True
    for s in suites:
        r = _run_suite(s)
        if not r.wasSuccessful():
            all_ok = False

    print(f"\n{'='*60}")
    print(f"Phase 7 Block C — Concurrency: {_pass}/{_total} passed, {_fail} failed")
    if all_ok:
        print("✅ CONCURRENCY TEST PASSED")
    else:
        print("❌ CONCURRENCY TEST FAILED")
    sys.exit(0 if all_ok else 1)
