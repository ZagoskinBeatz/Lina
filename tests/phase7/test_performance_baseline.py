"""
Phase 7 — Block F: Performance Baseline.

Establishes measurable baseline metrics:
  - Average intent processing time
  - 95th / 99th percentile latency
  - Governance overhead (policy + access)
  - Audit write throughput
  - Validation throughput

Run: python tests/phase7/test_performance_baseline.py
"""

import os
import statistics
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


def _reset_singletons():
    modules = [
        ("lina.governance.audit_logger", "_logger"),
        ("lina.intent.bridge", "_bridge"),
        ("lina.intent.router", "_router"),
        ("lina.security.input_validator", "_validator"),
        ("lina.governance.policy_engine", "_engine"),
        ("lina.governance.confirmation_handler", "_handler"),
        ("lina.governance.access_resolver", "_resolver"),
        ("lina.access.resolver", "_resolver"),
    ]
    for mod_name, attr in modules:
        try:
            mod = sys.modules.get(mod_name)
            if mod and hasattr(mod, attr):
                setattr(mod, attr, None)
        except Exception:
            pass


# Thresholds — generous for CI
INTENT_MAX_AVG_MS = 50.0      # average intent processing
INTENT_P95_MAX_MS = 100.0     # 95th percentile
INTENT_P99_MAX_MS = 200.0     # 99th percentile
POLICY_MAX_AVG_MS = 10.0      # policy check
VALIDATION_MAX_AVG_MS = 5.0   # input validation
AUDIT_MAX_AVG_MS = 2.0        # audit write


# ═══════════════════════════════════════════════════════════════
#  Block F.1 — Intent Processing Latency
# ═══════════════════════════════════════════════════════════════

class TestIntentLatency(unittest.TestCase):
    """F.1: Intent processing latency baseline."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_intent_average_latency(self):
        """200 intents → average < 50ms."""
        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        times = []

        for i in range(200):
            t0 = time.monotonic()
            bridge.from_text(f"покажи статус системы {i}", source="cli")
            elapsed = (time.monotonic() - t0) * 1000
            times.append(elapsed)

        avg = statistics.mean(times)
        p95 = sorted(times)[int(len(times) * 0.95)]
        p99 = sorted(times)[int(len(times) * 0.99)]

        print(f"\n  Intent latency (200 calls):")
        print(f"    avg={avg:.2f}ms  p95={p95:.2f}ms  p99={p99:.2f}ms")
        print(f"    min={min(times):.2f}ms  max={max(times):.2f}ms")

        self.assertLess(avg, INTENT_MAX_AVG_MS,
            f"Average intent latency {avg:.2f}ms > {INTENT_MAX_AVG_MS}ms")

    def test_intent_p95_latency(self):
        """200 intents → p95 < 100ms."""
        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        times = []

        for i in range(200):
            t0 = time.monotonic()
            bridge.from_text(f"статус сервиса {i}", source="cli")
            elapsed = (time.monotonic() - t0) * 1000
            times.append(elapsed)

        p95 = sorted(times)[int(len(times) * 0.95)]
        self.assertLess(p95, INTENT_P95_MAX_MS,
            f"p95 latency {p95:.2f}ms > {INTENT_P95_MAX_MS}ms")

    def test_action_processing_latency(self):
        """200 action intents → average < 50ms."""
        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        times = []

        for i in range(200):
            t0 = time.monotonic()
            bridge.from_action("svc_status", domain="service",
                               params={"name": f"svc_{i}"}, source="cli")
            elapsed = (time.monotonic() - t0) * 1000
            times.append(elapsed)

        avg = statistics.mean(times)
        print(f"\n  Action latency (200 calls): avg={avg:.2f}ms")
        self.assertLess(avg, INTENT_MAX_AVG_MS,
            f"Average action latency {avg:.2f}ms > {INTENT_MAX_AVG_MS}ms")


# ═══════════════════════════════════════════════════════════════
#  Block F.2 — Governance Overhead
# ═══════════════════════════════════════════════════════════════

class TestGovernanceOverhead(unittest.TestCase):
    """F.2: Policy + access resolver overhead."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_policy_check_latency(self):
        """500 policy checks → average < 10ms."""
        from lina.governance.policy_engine import get_policy_engine

        pe = get_policy_engine()
        times = []

        for i in range(500):
            t0 = time.monotonic()
            pe.check("svc_status", domain="service", risk_level="low")
            elapsed = (time.monotonic() - t0) * 1000
            times.append(elapsed)

        avg = statistics.mean(times)
        p95 = sorted(times)[int(len(times) * 0.95)]
        print(f"\n  Policy check (500 calls): avg={avg:.2f}ms  p95={p95:.2f}ms")
        self.assertLess(avg, POLICY_MAX_AVG_MS,
            f"Policy check avg {avg:.2f}ms > {POLICY_MAX_AVG_MS}ms")

    def test_access_resolver_latency(self):
        """500 access resolves → average < 10ms."""
        from lina.access.resolver import get_access_resolver

        ar = get_access_resolver()
        times = []

        # Create a minimal intent-like object
        class FakeIntent:
            def __init__(self):
                self.domain = "service"
                self.action = "svc_status"
                self.source = "cli"
                self.type = "SYSTEM_ACTION"
                self.id = "perf_test"

        intent = FakeIntent()
        for i in range(500):
            t0 = time.monotonic()
            ar.check(intent)
            elapsed = (time.monotonic() - t0) * 1000
            times.append(elapsed)

        avg = statistics.mean(times)
        print(f"\n  Access resolve (500 calls): avg={avg:.2f}ms")
        self.assertLess(avg, POLICY_MAX_AVG_MS,
            f"Access resolve avg {avg:.2f}ms > {POLICY_MAX_AVG_MS}ms")


# ═══════════════════════════════════════════════════════════════
#  Block F.3 — Audit Write Throughput
# ═══════════════════════════════════════════════════════════════

class TestAuditThroughput(unittest.TestCase):
    """F.3: Audit write throughput baseline."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_audit_write_latency(self):
        """1000 audit writes → average < 2ms."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()
        times = []

        for i in range(1000):
            t0 = time.monotonic()
            al.log(AuditRecord(
                event_type=AuditEvent.INTENT_CREATED,
                intent_id=f"perf_audit_{i}",
                intent_type="SYSTEM_ACTION",
                domain="service",
                action="svc_status",
                source="cli",
                decision="allow",
            ))
            elapsed = (time.monotonic() - t0) * 1000
            times.append(elapsed)

        avg = statistics.mean(times)
        p95 = sorted(times)[int(len(times) * 0.95)]
        print(f"\n  Audit write (1000 calls): avg={avg:.4f}ms  p95={p95:.4f}ms")
        self.assertLess(avg, AUDIT_MAX_AVG_MS,
            f"Audit write avg {avg:.4f}ms > {AUDIT_MAX_AVG_MS}ms")


# ═══════════════════════════════════════════════════════════════
#  Block F.4 — Input Validation Throughput
# ═══════════════════════════════════════════════════════════════

class TestValidationThroughput(unittest.TestCase):
    """F.4: Input validation throughput baseline."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_text_validation_latency(self):
        """1000 text validations → average < 5ms."""
        from lina.security.input_validator import get_input_validator

        iv = get_input_validator()
        times = []

        texts = [
            "покажи статус",
            "включи wifi",
            "обнови систему",
            "создай файл test.txt",
            "помоги мне",
        ]

        for i in range(1000):
            text = texts[i % len(texts)]
            t0 = time.monotonic()
            iv.validate_text(text)
            elapsed = (time.monotonic() - t0) * 1000
            times.append(elapsed)

        avg = statistics.mean(times)
        p95 = sorted(times)[int(len(times) * 0.95)]
        print(f"\n  Text validation (1000 calls): avg={avg:.4f}ms  p95={p95:.4f}ms")
        self.assertLess(avg, VALIDATION_MAX_AVG_MS,
            f"Text validation avg {avg:.4f}ms > {VALIDATION_MAX_AVG_MS}ms")

    def test_domain_validation_latency(self):
        """1000 domain validations → average < 5ms."""
        from lina.security.input_validator import get_input_validator

        iv = get_input_validator()
        times = []

        domains = ["service", "network", "disk", "package", "general"]

        for i in range(1000):
            domain = domains[i % len(domains)]
            t0 = time.monotonic()
            iv.validate_domain(domain)
            elapsed = (time.monotonic() - t0) * 1000
            times.append(elapsed)

        avg = statistics.mean(times)
        print(f"\n  Domain validation (1000 calls): avg={avg:.4f}ms")
        self.assertLess(avg, VALIDATION_MAX_AVG_MS,
            f"Domain validation avg {avg:.4f}ms > {VALIDATION_MAX_AVG_MS}ms")


# ═══════════════════════════════════════════════════════════════
#  Block F.5 — End-to-End Pipeline Throughput
# ═══════════════════════════════════════════════════════════════

class TestPipelineThroughput(unittest.TestCase):
    """F.5: Full pipeline throughput."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_sustained_throughput_100(self):
        """100 sequential end-to-end intents → all complete, avg < 50ms."""
        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        times = []

        t_start = time.monotonic()
        for i in range(100):
            t0 = time.monotonic()
            bridge.from_text(f"покажи статус {i}", source="cli")
            times.append((time.monotonic() - t0) * 1000)

        total_sec = time.monotonic() - t_start
        avg = statistics.mean(times)
        throughput = 100 / total_sec

        print(f"\n  Pipeline throughput (100 calls):")
        print(f"    total={total_sec:.2f}s  avg={avg:.2f}ms  throughput={throughput:.1f} ops/s")

        self.assertLess(avg, INTENT_MAX_AVG_MS,
            f"Pipeline avg {avg:.2f}ms > {INTENT_MAX_AVG_MS}ms")
        self.assertGreater(throughput, 10.0,
            f"Throughput {throughput:.1f} ops/s too low (need >10)")


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
        TestIntentLatency,
        TestGovernanceOverhead,
        TestAuditThroughput,
        TestValidationThroughput,
        TestPipelineThroughput,
    ]
    all_ok = True
    for s in suites:
        r = _run_suite(s)
        if not r.wasSuccessful():
            all_ok = False

    print(f"\n{'='*60}")
    print(f"Phase 7 Block F — Performance Baseline: {_pass}/{_total} passed, {_fail} failed")
    if all_ok:
        print("✅ PERFORMANCE BASELINE PASSED")
    else:
        print("❌ PERFORMANCE BASELINE FAILED — review latency thresholds")
    sys.exit(0 if all_ok else 1)
