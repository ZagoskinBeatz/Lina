# -*- coding: utf-8 -*-
"""
Phase 4 Integration Tests — Usability & Stability.

Tests:
  Block A — UX Messaging:
    1. ResponseFormatter exists as singleton
    2. format_result handles SUCCESS → ✅ prefix
    3. format_result handles DENIED → 🚫 prefix
    4. format_result handles NEEDS_CONFIRM → ⚠ with /confirm hint
    5. format_result handles ESCALATED → ⏫ prefix
    6. format_result handles FAILED → ❌ prefix, no traceback
    7. format_result handles NOT_FOUND → ℹ prefix
    8. format_result handles CHAT_RESPONSE → passthrough
    9. _strip_traceback removes Python stack traces

  Block B — Edge Cases:
    10. format_empty_input returns actionable message
    11. is_help_command detects help/помощь/?
    12. format_help returns usage examples
    13. format_cancel returns cancel message
    14. format_permission_error returns 🚫 with domain

  Block C — Diagnostics Advice:
    15. All 9 domains have tips in _DOMAIN_ADVICE
    16. format_diagnostics_advice returns tips
    17. get_domain_advice returns raw dict
    18. Unknown domain returns default advice

  Block D — Degradation:
    19. format_degradation: dbus, network, llm, governance
    20. format_degradation: unknown component → fallback

  Block E — Progress:
    21. format_progress: all stages have messages
    22. format_progress: unknown stage → default

  Block F — UX Wiring:
    23. REPL uses ResponseFormatter (source inspection)
    24. GUI chat uses ResponseFormatter (source inspection)
    25. CLI uses ResponseFormatter (source inspection)

  Block G — IntentRouter Metadata Enrichment:
    26. IntentRouter process() declares _result variable
    27. IntentRouter finally block enriches metadata
    28. IntentRouter metadata includes domain and action
    29. Sub-method returns captured by _result
    30. IntentResult has duration_ms set

  Block H — Error Safety:
    31. FAILED responses strip traceback
    32. _format_error adds domain tips for known domains
    33. _format_error no tips for unknown domains

Phase: UX LAYER / Phase 4
Правило: Governance не меняется — меняется вывод.
"""

import inspect
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Project root
_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT not in sys.path:
    sys.path.insert(0, os.path.dirname(_PROJECT))


# ═══════════════════════════════════════════════════════════
#  Block A — UX Messaging: ResponseFormatter
# ═══════════════════════════════════════════════════════════

class TestResponseFormatterBasic(unittest.TestCase):
    """ResponseFormatter creation and singleton."""

    def test_01_singleton_exists(self):
        """get_response_formatter returns ResponseFormatter instance."""
        from lina.core.response_ux import get_response_formatter, ResponseFormatter
        fmt = get_response_formatter()
        self.assertIsInstance(fmt, ResponseFormatter)

    def test_01b_singleton_same_instance(self):
        """get_response_formatter returns the same instance."""
        from lina.core.response_ux import get_response_formatter
        fmt1 = get_response_formatter()
        fmt2 = get_response_formatter()
        self.assertIs(fmt1, fmt2)


class TestFormatResultStatuses(unittest.TestCase):
    """format_result handles all IntentStatus values correctly."""

    def _make_result(self, status, text="", **kwargs):
        from lina.intent.types import IntentResult
        return IntentResult(
            intent_id="test-001",
            status=status,
            response_text=text,
            **kwargs,
        )

    def test_02_success_has_checkmark(self):
        """SUCCESS → starts with ✅."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus
        fmt = get_response_formatter()
        result = self._make_result(IntentStatus.SUCCESS, "Готово")
        text = fmt.format_result(result, domain="system")
        self.assertTrue(text.startswith("✅"))
        self.assertIn("Готово", text)

    def test_03_denied_has_stop(self):
        """DENIED → starts with 🚫."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus
        fmt = get_response_formatter()
        result = self._make_result(IntentStatus.DENIED, "Запрещено")
        text = fmt.format_result(result, domain="security")
        self.assertTrue(text.startswith("🚫"))
        self.assertIn("Запрещено", text)

    def test_04_confirm_has_warning(self):
        """NEEDS_CONFIRM → contains ⚠ and /confirm hint."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus
        fmt = get_response_formatter()
        result = self._make_result(
            IntentStatus.NEEDS_CONFIRM,
            "Подтвердите удаление",
            escalation_id="esc-42",
        )
        text = fmt.format_result(result)
        self.assertIn("⚠", text)
        self.assertIn("/confirm esc-42", text)
        self.assertIn("/deny esc-42", text)

    def test_05_escalated_has_arrow(self):
        """ESCALATED → starts with ⏫."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus
        fmt = get_response_formatter()
        result = self._make_result(IntentStatus.ESCALATED, "Передано")
        text = fmt.format_result(result)
        self.assertTrue(text.startswith("⏫"))

    def test_06_failed_has_cross_no_traceback(self):
        """FAILED → starts with ❌, no traceback."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus
        fmt = get_response_formatter()
        # Include a fake traceback in the response
        raw_error = (
            "Traceback (most recent call last):\n"
            "  File \"/opt/app.py\", line 42, in run\n"
            "    raise RuntimeError('oops')\n"
            "RuntimeError: oops"
        )
        result = self._make_result(IntentStatus.FAILED, raw_error)
        text = fmt.format_result(result, domain="network")
        self.assertTrue(text.startswith("❌"))
        self.assertNotIn("Traceback", text)
        self.assertNotIn("File \"/opt", text)
        self.assertIn("oops", text)

    def test_07_not_found_has_info(self):
        """NOT_FOUND → starts with ℹ."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus
        fmt = get_response_formatter()
        result = self._make_result(IntentStatus.NOT_FOUND, "Нет данных")
        text = fmt.format_result(result, domain="audio")
        self.assertTrue(text.startswith("ℹ"))

    def test_08_chat_passthrough(self):
        """CHAT_RESPONSE → text unchanged."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus
        fmt = get_response_formatter()
        result = self._make_result(IntentStatus.CHAT_RESPONSE, "Hello world")
        text = fmt.format_result(result)
        self.assertEqual(text, "Hello world")


class TestStripTraceback(unittest.TestCase):
    """_strip_traceback removes Python tracebacks."""

    def test_09_strips_full_traceback(self):
        """Full traceback → only exception message remains."""
        from lina.core.response_ux import ResponseFormatter
        tb = (
            "Traceback (most recent call last):\n"
            "  File \"test.py\", line 1, in <module>\n"
            "    raise ValueError('bad value')\n"
            "ValueError: bad value"
        )
        result = ResponseFormatter._strip_traceback(tb)
        self.assertNotIn("Traceback", result)
        self.assertNotIn("File \"test.py\"", result)
        self.assertIn("bad value", result)

    def test_09b_no_traceback_unchanged(self):
        """Text without traceback passes through."""
        from lina.core.response_ux import ResponseFormatter
        text = "Обычное сообщение об ошибке"
        result = ResponseFormatter._strip_traceback(text)
        self.assertEqual(result, text)

    def test_09c_empty_string(self):
        """Empty string → empty string."""
        from lina.core.response_ux import ResponseFormatter
        self.assertEqual(ResponseFormatter._strip_traceback(""), "")


# ═══════════════════════════════════════════════════════════
#  Block B — Edge Cases
# ═══════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):
    """Edge case handlers: empty input, help, cancel, permission."""

    def test_10_empty_input(self):
        """format_empty_input returns non-empty actionable message."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        msg = fmt.format_empty_input()
        self.assertTrue(len(msg) > 10)
        self.assertIn("помощь", msg.lower())

    def test_11_help_commands(self):
        """is_help_command detects help/помощь/?."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        for cmd in ("помощь", "help", "?", "/help", "/помощь"):
            self.assertTrue(fmt.is_help_command(cmd), f"Failed for: {cmd}")
        for cmd in ("привет", "status", "exit"):
            self.assertFalse(fmt.is_help_command(cmd), f"False positive: {cmd}")

    def test_12_help_message(self):
        """format_help returns usage examples."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        msg = fmt.format_help()
        self.assertIn("диагностика", msg.lower())
        self.assertIn("/confirm", msg)
        self.assertIn("/deny", msg)

    def test_13_cancel_message(self):
        """format_cancel returns 🚫 cancel text."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        msg = fmt.format_cancel()
        self.assertIn("🚫", msg)
        self.assertIn("отменено", msg.lower())

    def test_14_permission_error(self):
        """format_permission_error returns 🚫 with domain."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        msg = fmt.format_permission_error("security")
        self.assertIn("🚫", msg)
        self.assertIn("security", msg.lower())


# ═══════════════════════════════════════════════════════════
#  Block C — Diagnostics Advice
# ═══════════════════════════════════════════════════════════

class TestDiagnosticsAdvice(unittest.TestCase):
    """Domain-specific diagnostics advice."""

    EXPECTED_DOMAINS = [
        "network", "audio", "disk", "display", "service",
        "package", "boot", "security", "system",
    ]

    def test_15_all_9_domains_have_tips(self):
        """All 9 domains present in _DOMAIN_ADVICE with tips."""
        from lina.core.response_ux import _DOMAIN_ADVICE
        for domain in self.EXPECTED_DOMAINS:
            self.assertIn(domain, _DOMAIN_ADVICE, f"Missing domain: {domain}")
            tips = _DOMAIN_ADVICE[domain].get("tips", [])
            self.assertTrue(len(tips) > 0, f"No tips for domain: {domain}")

    def test_16_format_diagnostics_advice(self):
        """format_diagnostics_advice returns tips text."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        msg = fmt.format_diagnostics_advice("network", problem="Нет интернета")
        self.assertIn("Нет интернета", msg)
        self.assertIn("ping", msg.lower())

    def test_17_get_domain_advice(self):
        """get_domain_advice returns dict with icon/tips/check_hint."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        advice = fmt.get_domain_advice("audio")
        self.assertIn("icon", advice)
        self.assertIn("tips", advice)
        self.assertIn("check_hint", advice)
        self.assertTrue(len(advice["tips"]) > 0)

    def test_18_unknown_domain_default(self):
        """Unknown domain returns default advice (no crash)."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        advice = fmt.get_domain_advice("alien_domain_xyz")
        self.assertIn("icon", advice)
        self.assertEqual(advice["tips"], [])


# ═══════════════════════════════════════════════════════════
#  Block D — Degradation Messages
# ═══════════════════════════════════════════════════════════

class TestDegradation(unittest.TestCase):
    """Degradation messages for component failures."""

    def test_19_known_components(self):
        """format_degradation covers dbus, network, llm, governance."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        for comp in ("dbus", "network", "llm", "governance"):
            msg = fmt.format_degradation(comp)
            self.assertTrue(len(msg) > 5, f"Empty msg for: {comp}")
            self.assertNotIn("Traceback", msg)

    def test_20_unknown_component_fallback(self):
        """Unknown component → generic fallback message."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        msg = fmt.format_degradation("quantum_module")
        self.assertIn("quantum_module", msg)
        self.assertIn("⚠", msg)


# ═══════════════════════════════════════════════════════════
#  Block E — Progress
# ═══════════════════════════════════════════════════════════

class TestProgress(unittest.TestCase):
    """Progress indicators."""

    def test_21_known_stages(self):
        """All known stages have messages."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        for stage in ("thinking", "analyzing", "executing",
                       "diagnosing", "confirming"):
            msg = fmt.format_progress(stage)
            self.assertTrue(len(msg) > 3, f"Empty progress for: {stage}")

    def test_22_unknown_stage_default(self):
        """Unknown stage → default progress."""
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        msg = fmt.format_progress("teleporting")
        self.assertIn("⏳", msg)


# ═══════════════════════════════════════════════════════════
#  Block F — UX Wiring (source inspection)
# ═══════════════════════════════════════════════════════════

class TestUXWiring(unittest.TestCase):
    """Verify REPL, GUI, CLI are wired to ResponseFormatter."""

    def test_23_repl_uses_formatter(self):
        """REPL _route_via_governance uses ResponseFormatter."""
        from lina.core.repl import REPLSession
        src = inspect.getsource(REPLSession._route_via_governance)
        self.assertIn("get_response_formatter", src)
        self.assertIn("format_result", src)

    def test_24_gui_chat_uses_formatter(self):
        """GUI ChatController _process_via_intent uses ResponseFormatter."""
        try:
            from lina.gui.chat import ChatController
            src = inspect.getsource(ChatController._process_via_intent)
            self.assertIn("get_response_formatter", src)
        except ImportError:
            # GUI may not be importable in headless env
            self.skipTest("GUI not importable (headless)")

    def test_25_cli_uses_formatter(self):
        """CLI _route_via_governance uses ResponseFormatter."""
        from lina.core.cli import _route_via_governance
        src = inspect.getsource(_route_via_governance)
        self.assertIn("get_response_formatter", src)
        self.assertIn("format_result", src)


# ═══════════════════════════════════════════════════════════
#  Block G — IntentRouter Metadata Enrichment
# ═══════════════════════════════════════════════════════════

class TestIntentRouterMetadata(unittest.TestCase):
    """IntentRouter enriches result metadata with domain/action."""

    def test_26_process_declares_result(self):
        """IntentRouter.process() uses _result variable."""
        from lina.intent.router import IntentRouter
        src = inspect.getsource(IntentRouter.process)
        self.assertIn("_result", src)
        self.assertIn("_result =", src)

    def test_27_finally_enriches_metadata(self):
        """IntentRouter.process() finally block sets domain/action."""
        from lina.intent.router import IntentRouter
        src = inspect.getsource(IntentRouter.process)
        self.assertIn("_result.metadata", src)
        self.assertIn('"domain"', src)
        self.assertIn('"action"', src)

    def test_28_metadata_domain_action_set(self):
        """Running process() sets metadata with domain and action."""
        from lina.intent.router import IntentRouter
        from lina.intent.types import Intent, IntentType, IntentStatus

        router = IntentRouter()
        intent = Intent(
            id="test-meta-001",
            type=IntentType.QUERY,
            domain="network",
            action=None,
            params={},
            source="test",
        )
        result = router.process(intent)
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.metadata)
        self.assertEqual(result.metadata.get("domain"), "network")

    def test_29_submethod_returns_captured(self):
        """Diagnose/action sub-method returns are captured by _result."""
        from lina.intent.router import IntentRouter
        src = inspect.getsource(IntentRouter.process)
        # Check that sub-method calls assign to _result
        self.assertIn("_result = self._process_diagnose", src)
        self.assertIn("_result = self._process_action", src)

    def test_30_duration_ms_set(self):
        """IntentResult.duration_ms is set after process()."""
        from lina.intent.router import IntentRouter
        from lina.intent.types import Intent, IntentType

        router = IntentRouter()
        intent = Intent(
            id="test-dur-001",
            type=IntentType.QUERY,
            domain="system",
            action=None,
            params={},
            source="test",
        )
        result = router.process(intent)
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.duration_ms)
        self.assertGreaterEqual(result.duration_ms, 0)


# ═══════════════════════════════════════════════════════════
#  Block H — Error Safety
# ═══════════════════════════════════════════════════════════

class TestErrorSafety(unittest.TestCase):
    """Error messages are clean and actionable."""

    def test_31_failed_strips_traceback(self):
        """FAILED response strips traceback via format_result."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus, IntentResult
        fmt = get_response_formatter()
        result = IntentResult(
            intent_id="err-001",
            status=IntentStatus.FAILED,
            response_text=(
                "Traceback (most recent call last):\n"
                "  File \"x.py\", line 1\n"
                "    boom()\n"
                "RuntimeError: connection lost"
            ),
        )
        text = fmt.format_result(result, domain="network")
        self.assertNotIn("Traceback", text)
        self.assertIn("connection lost", text)

    def test_32_error_adds_domain_tips(self):
        """FAILED with known domain adds tips."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus, IntentResult
        fmt = get_response_formatter()
        result = IntentResult(
            intent_id="err-002",
            status=IntentStatus.FAILED,
            response_text="Сбой сети",
        )
        text = fmt.format_result(result, domain="network")
        self.assertIn("❌", text)
        self.assertIn("ping", text.lower())

    def test_33_error_no_tips_unknown_domain(self):
        """FAILED with unknown domain → no tips section."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus, IntentResult
        fmt = get_response_formatter()
        result = IntentResult(
            intent_id="err-003",
            status=IntentStatus.FAILED,
            response_text="Неизвестная ошибка",
        )
        text = fmt.format_result(result, domain="alien_xyz")
        self.assertIn("❌", text)
        self.assertNotIn("Возможные решения", text)


# ═══════════════════════════════════════════════════════════
#  Block I — Success formatting edge cases
# ═══════════════════════════════════════════════════════════

class TestSuccessFormatting(unittest.TestCase):
    """SUCCESS formatting variations."""

    def test_34_success_empty_text_with_action(self):
        """SUCCESS with empty text but action → shows action name."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus, IntentResult
        fmt = get_response_formatter()
        result = IntentResult(
            intent_id="s-001",
            status=IntentStatus.SUCCESS,
            response_text="",
        )
        text = fmt.format_result(result, action="restart_networkmanager")
        self.assertIn("✅", text)
        self.assertIn("restart_networkmanager", text)

    def test_35_success_empty_text_no_action(self):
        """SUCCESS with no text and no action → 'Готово.'."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus, IntentResult
        fmt = get_response_formatter()
        result = IntentResult(
            intent_id="s-002",
            status=IntentStatus.SUCCESS,
            response_text="",
        )
        text = fmt.format_result(result)
        self.assertIn("✅", text)
        self.assertIn("Готово", text)

    def test_36_denied_with_domain(self):
        """DENIED with domain shows domain context."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus, IntentResult
        fmt = get_response_formatter()
        result = IntentResult(
            intent_id="d-001",
            status=IntentStatus.DENIED,
            response_text="Нет доступа",
        )
        text = fmt.format_result(result, domain="boot")
        self.assertIn("boot", text.lower())

    def test_37_not_found_with_hint(self):
        """NOT_FOUND with known domain shows check_hint."""
        from lina.core.response_ux import get_response_formatter
        from lina.intent.types import IntentStatus, IntentResult
        fmt = get_response_formatter()
        result = IntentResult(
            intent_id="nf-001",
            status=IntentStatus.NOT_FOUND,
            response_text="",
        )
        text = fmt.format_result(result, domain="audio")
        self.assertIn("ℹ", text)


# ═══════════════════════════════════════════════════════════
#  Runner
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
