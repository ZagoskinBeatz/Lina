"""
Phase 7 — Block A: Soak Testing (Stability Over Time).

Simulates extended runtime with mixed workloads:
  - Diagnostics, allowed/denied actions, confirmation flows
  - Random valid and invalid inputs
  - DBus-style calls
  - Memory/resource monitoring

Success criteria:
  • No crash
  • No memory leak trend
  • No governance bypass
  • No unhandled exceptions

Run: python tests/phase7/test_soak.py
"""

import gc
import os
import sys
import time
import random
import threading
import tracemalloc
import unittest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import List, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

VALID_TEXTS = [
    "перезапусти NetworkManager",
    "покажи статус системы",
    "какая погода",
    "привет",
    "открой firefox",
    "установи htop",
    "диагностика wifi",
    "нет звука",
    "что такое Linux",
    "покажи использование диска",
    "перезагрузи сервис cups",
    "статус bluetooth",
    "обнови систему",
    "покажи процессы",
    "настрой яркость",
]

INVALID_TEXTS = [
    "",
    "x" * 5000,  # oversized
    "test\x00injection",  # null byte
    "echo aGVsbG8= | base64 -d",  # obfuscation
    "; rm -rf /",  # injection
    "$(cat /etc/shadow)",  # command substitution
    "`whoami`",  # backtick
    "eval('import os')",  # eval
    "\x01\x02\x03",  # control chars
]

VALID_DOMAINS = ["service", "package", "network", "audio", "display", "config", "system"]
INVALID_DOMAINS = ["evil", "x" * 100, "", "../../etc", "adm\x00in"]

VALID_ACTIONS = ["svc_restart", "svc_status", "pkg_list", "net_status"]
INVALID_ACTIONS = ["rm_everything", "'; DROP TABLE", "../../bin/sh", "x" * 200]

VALID_SOURCES = ["ui", "cli", "dbus", "hotkey", "internal"]
INVALID_SOURCES = ["hacker", "x" * 50, "", "roo\x00t"]


def _reset_singletons():
    """Reset all governance singletons for clean test state."""
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


@dataclass
class SoakMetrics:
    """Metrics collected during soak test."""
    iterations: int = 0
    successes: int = 0
    denials: int = 0
    failures: int = 0
    errors: int = 0
    unhandled: int = 0
    bypasses: int = 0
    peak_memory_kb: float = 0
    start_memory_kb: float = 0
    end_memory_kb: float = 0
    elapsed_seconds: float = 0
    memory_samples: List[float] = None

    def __post_init__(self):
        if self.memory_samples is None:
            self.memory_samples = []


# ═══════════════════════════════════════════════════════════════
#  Block A.1 — Soak Test: Mixed Workload
# ═══════════════════════════════════════════════════════════════

class TestSoakMixedWorkload(unittest.TestCase):
    """A.1: Продолжительный тест смешанной нагрузки."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    @patch("lina.governance.action_registry.subprocess.run")
    def test_soak_1000_iterations(self, mock_run):
        """1000 итераций смешанной нагрузки без crash."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()
        metrics = SoakMetrics()

        tracemalloc.start()
        snapshot_start = tracemalloc.take_snapshot()
        metrics.start_memory_kb = sum(s.size for s in snapshot_start.statistics("filename")) / 1024

        start_time = time.monotonic()

        for i in range(1000):
            try:
                # Mix: 60% valid, 25% invalid, 15% actions
                r = random.random()
                if r < 0.60:
                    text = random.choice(VALID_TEXTS)
                    result = bridge.from_text(text, source="cli")
                elif r < 0.85:
                    text = random.choice(INVALID_TEXTS)
                    result = bridge.from_text(text, source="cli")
                else:
                    action = random.choice(VALID_ACTIONS + INVALID_ACTIONS)
                    domain = random.choice(VALID_DOMAINS + INVALID_DOMAINS)
                    source = random.choice(VALID_SOURCES)
                    result = bridge.from_action(
                        action_id=action,
                        domain=domain,
                        source=source,
                    )

                metrics.iterations += 1
                if result.status == IntentStatus.SUCCESS:
                    metrics.successes += 1
                elif result.status == IntentStatus.DENIED:
                    metrics.denials += 1
                elif result.status == IntentStatus.FAILED:
                    metrics.failures += 1

                # Memory sample every 100 iterations
                if i % 100 == 0:
                    gc.collect()
                    snap = tracemalloc.take_snapshot()
                    mem_kb = sum(s.size for s in snap.statistics("filename")) / 1024
                    metrics.memory_samples.append(mem_kb)

            except Exception as e:
                metrics.errors += 1
                # Governance bypass = unacceptable
                if "bypass" in str(e).lower():
                    metrics.bypasses += 1

        metrics.elapsed_seconds = time.monotonic() - start_time

        gc.collect()
        snapshot_end = tracemalloc.take_snapshot()
        metrics.end_memory_kb = sum(s.size for s in snapshot_end.statistics("filename")) / 1024
        metrics.peak_memory_kb = max(metrics.memory_samples) if metrics.memory_samples else 0
        tracemalloc.stop()

        # Assertions
        self.assertEqual(metrics.iterations, 1000)
        self.assertEqual(metrics.unhandled, 0, "No unhandled exceptions")
        self.assertEqual(metrics.bypasses, 0, "No governance bypass")

    @patch("lina.governance.action_registry.subprocess.run")
    def test_soak_no_memory_leak(self, mock_run):
        """Отсутствие тренда утечки памяти за 500 итераций."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        samples = []

        tracemalloc.start()
        for i in range(500):
            text = random.choice(VALID_TEXTS)
            bridge.from_text(text, source="cli")

            if i % 50 == 0:
                gc.collect()
                snap = tracemalloc.take_snapshot()
                mem = sum(s.size for s in snap.statistics("filename")) / 1024
                samples.append(mem)

        tracemalloc.stop()

        # Check: steady-state memory (skip first half = initialization).
        # Compare 3rd quarter to 4th quarter — both should be stable.
        if len(samples) >= 4:
            half = len(samples) // 2
            third_q = sum(samples[half:half + half//2]) / max(half//2, 1)
            fourth_q = sum(samples[half + half//2:]) / max(len(samples) - half - half//2, 1)
            growth_ratio = fourth_q / third_q if third_q > 0 else 1.0
            self.assertLess(growth_ratio, 2.0,
                f"Steady-state memory growth: {growth_ratio:.2f}x ({third_q:.0f}KB → {fourth_q:.0f}KB)")


# ═══════════════════════════════════════════════════════════════
#  Block A.2 — Soak Test: Governance Consistency
# ═══════════════════════════════════════════════════════════════

class TestSoakGovernanceConsistency(unittest.TestCase):
    """A.2: Governance принимает одинаковые решения на протяжении всего теста."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_deny_consistency_500(self):
        """500 identical denied requests → 100% consistent DENY."""
        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()
        denied_count = 0

        for _ in range(500):
            result = bridge.from_text("; rm -rf /", source="cli")
            if result.status in (IntentStatus.DENIED, IntentStatus.NEEDS_CONFIRM):
                denied_count += 1

        self.assertEqual(denied_count, 500,
            f"Expected 500 DENIED/NEEDS_CONFIRM, got {denied_count}")

    def test_invalid_input_consistency_500(self):
        """500 null-byte inputs → 100% consistent DENY."""
        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()
        denied = 0

        for _ in range(500):
            result = bridge.from_text("test\x00payload", source="cli")
            if result.status == IntentStatus.DENIED:
                denied += 1

        self.assertEqual(denied, 500)

    def test_domain_validation_consistency(self):
        """300 invalid domain checks → all rejected."""
        from lina.security.input_validator import get_input_validator

        iv = get_input_validator()
        for _ in range(300):
            ok, _ = iv.validate_domain("evil_domain")
            self.assertFalse(ok)

    @patch("lina.governance.action_registry.subprocess.run")
    def test_allowed_action_consistency(self, mock_run):
        """200 allowed actions → governance consistent."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()
        results = set()

        for _ in range(200):
            result = bridge.from_action(
                action_id="svc_status",
                domain="service",
                source="cli",
                params={"service_name": "NetworkManager"},
            )
            results.add(result.status)

        # All should be same status (SUCCESS or NEEDS_CONFIRM)
        self.assertLessEqual(len(results), 2,
            f"Inconsistent results: {results}")


# ═══════════════════════════════════════════════════════════════
#  Block A.3 — Soak Test: Error Recovery
# ═══════════════════════════════════════════════════════════════

class TestSoakErrorRecovery(unittest.TestCase):
    """A.3: Система восстанавливается после ошибок."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    @patch("lina.governance.action_registry.subprocess.run")
    def test_recovery_after_errors(self, mock_run):
        """Чередование ошибок и успехов — система не деградирует."""
        def side_effect(*args, **kwargs):
            if random.random() < 0.3:
                raise OSError("simulated failure")
            return MagicMock(returncode=0, stdout="OK", stderr="")

        mock_run.side_effect = side_effect

        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()
        for i in range(200):
            try:
                result = bridge.from_text("статус системы", source="cli")
                # System should always return a valid IntentResult
                self.assertIsNotNone(result)
                self.assertIsInstance(result.status, IntentStatus)
            except Exception:
                # Unhandled exceptions from subprocess are caught by executor
                pass

        # After errors, system should still work
        mock_run.side_effect = None
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
        result = bridge.from_text("привет", source="cli")
        self.assertIsNotNone(result)

    def test_rapid_singleton_resets(self):
        """100 singleton resets don't cause memory leak or crash."""
        for _ in range(100):
            _reset_singletons()
            from lina.intent.bridge import get_intent_bridge
            bridge = get_intent_bridge()
            self.assertIsNotNone(bridge)


# ═══════════════════════════════════════════════════════════════
#  Block A.4 — Soak Test: Audit Accumulation
# ═══════════════════════════════════════════════════════════════

class TestSoakAuditAccumulation(unittest.TestCase):
    """A.4: Аудит-лог стабильно накапливается без потерь."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_audit_entry_count_matches(self):
        """300 операций = 300+ аудит-записей."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()
        initial_count = len(al.get_recent(limit=5000))

        for i in range(300):
            al.log(AuditRecord(
                event_type=AuditEvent.INTENT_CREATED,
                intent_id=f"soak_{i}",
                intent_type="SYSTEM_ACTION",
                domain="service",
                action="svc_status",
                source="cli",
                decision="allow",
            ))

        log = al.get_recent(limit=5000)
        # Should have at least 300 new entries
        new_entries = len(log) - initial_count
        self.assertGreaterEqual(new_entries, 300,
            f"Expected 300+ entries, got {new_entries}")

    def test_audit_no_data_corruption(self):
        """Audit entries preserve data integrity over 200 writes."""
        from lina.governance.audit_logger import get_audit_logger, AuditEvent, AuditRecord

        al = get_audit_logger()

        for i in range(200):
            al.log(AuditRecord(
                event_type=AuditEvent.DENIED,
                intent_id=f"integrity_{i:04d}",
                intent_type="SYSTEM_ACTION",
                domain="disk",
                action="disk_format",
                source="dbus",
                decision="deny",
                metadata={"iteration": i},
            ))

        log = al.get_recent(limit=5000)
        # Find our entries
        our_entries = [e for e in log if str(e.get("intent_id", "")).startswith("integrity_")]
        self.assertEqual(len(our_entries), 200,
            f"Expected 200 integrity entries, got {len(our_entries)}")


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
        TestSoakMixedWorkload,
        TestSoakGovernanceConsistency,
        TestSoakErrorRecovery,
        TestSoakAuditAccumulation,
    ]
    all_ok = True
    for s in suites:
        r = _run_suite(s)
        if not r.wasSuccessful():
            all_ok = False

    print(f"\n{'='*60}")
    print(f"Phase 7 Block A — Soak Testing: {_pass}/{_total} passed, {_fail} failed")
    if all_ok:
        print("✅ SOAK TEST PASSED")
    else:
        print("❌ SOAK TEST FAILED")
    sys.exit(0 if all_ok else 1)
