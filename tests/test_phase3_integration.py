"""
Phase 3 Integration Tests — MVP stabilization, governance enforcement.

Tests:
  1. REPL governance routing (no direct commander.process bypass)
  2. Legacy lina.py governance routing
  3. PolicyEngine: "desktop" + "system" domains allowed
  4. PolicyEngine: empty domain normalizes to "general"
  5. GUI /confirm and /deny command parsing
  6. GUI chat: no ImportError bypass
  7. Tray: no ImportError bypass
  8. Hotkey commands route through governance (cli module)
  9. IntentRouter: _process_diagnose audit logging
  10. IntentRouter: NEEDS_CONFIRM audit in _process_action
  11. IntentRouter: NOT_FOUND fallback audit
  12. IntentRouter: chat passthrough audit
  13. IntentRouter: telemetry tracks actual success/failure
  14. PolicyEngine: all critical domains present
  15. REPLSession: exit commands handled before governance

Phase: MVP STABILIZATION / Phase 3
Правило: Governance enforcement — zero bypass paths.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock


# ═══════════════════════════════════════════════════════════
#  1. REPL — Governance Routing
# ═══════════════════════════════════════════════════════════

class TestREPLGovernanceRouting(unittest.TestCase):
    """REPL must route through IntentBridge, never direct commander.process()."""

    def test_repl_has_governance_method(self):
        """REPLSession has _route_via_governance method."""
        from lina.core.repl import REPLSession
        self.assertTrue(hasattr(REPLSession, '_route_via_governance'))

    def test_repl_accepts_pipeline_handler(self):
        """REPLSession.__init__ accepts pipeline_handler kwarg."""
        from lina.core.repl import REPLSession
        import inspect
        sig = inspect.signature(REPLSession.__init__)
        self.assertIn('pipeline_handler', sig.parameters)

    def test_repl_run_does_not_call_commander_process_directly(self):
        """REPLSession.run() uses _route_via_governance, not commander.process()."""
        import inspect
        from lina.core.repl import REPLSession
        source = inspect.getsource(REPLSession.run)
        # Should NOT contain direct commander.process call in the main loop
        # But SHOULD contain _route_via_governance
        self.assertIn('_route_via_governance', source)
        # The only commander.process reference should NOT be in the main input loop
        # We verify _route_via_governance is the call, not commander.process
        lines = source.split('\n')
        main_loop_lines = [
            l for l in lines
            if 'user_input' in l and 'commander.process' in l
        ]
        self.assertEqual(len(main_loop_lines), 0,
                         "run() must not call commander.process(user_input)")

    def test_repl_oneshot_uses_governance(self):
        """run_oneshot() calls _route_via_governance."""
        import inspect
        from lina.core.repl import REPLSession
        source = inspect.getsource(REPLSession.run_oneshot)
        self.assertIn('_route_via_governance', source)
        self.assertNotIn('commander.process', source)

    def test_repl_exit_commands(self):
        """REPLSession has _EXIT_COMMANDS for pre-governance exit."""
        from lina.core.repl import REPLSession
        self.assertTrue(hasattr(REPLSession, '_EXIT_COMMANDS'))
        exits = REPLSession._EXIT_COMMANDS
        for cmd in ('выход', 'exit', 'quit', 'q'):
            self.assertIn(cmd, exits,
                          f"'{cmd}' must be in _EXIT_COMMANDS")

    def test_repl_route_governance_importerror_degrades(self):
        """_route_via_governance degrades gracefully when IntentBridge missing."""
        from lina.core.repl import REPLSession

        mock_commander = MagicMock()
        mock_commander.process.return_value = "fallback response"
        session = REPLSession(mock_commander)

        with patch.dict('sys.modules', {'lina.intent.bridge': None}):
            # Should not crash, should degrade
            try:
                result = session._route_via_governance("test query")
                # Either returns response or degrades to commander
                self.assertIsInstance(result, str)
            except ImportError:
                # Acceptable — degradation path
                pass


# ═══════════════════════════════════════════════════════════
#  2. Legacy lina.py — Governance Routing
# ═══════════════════════════════════════════════════════════

class TestLegacyGovernanceRouting(unittest.TestCase):
    """Legacy lina.py main() must no longer call commander.process() directly."""

    def test_legacy_main_has_governance_routing(self):
        """lina.py main() uses _legacy_route_governance."""
        import inspect
        from lina import lina as lina_mod
        source = inspect.getsource(lina_mod.main)
        self.assertIn('_legacy_route_governance', source)

    def test_legacy_main_no_direct_commander_process_in_loop(self):
        """lina.py main loop does not call commander.process() directly."""
        import inspect
        from lina import lina as lina_mod
        source = inspect.getsource(lina_mod.main)
        # Find lines in while True loop that directly call commander.process
        # The function _legacy_route_governance wraps it — direct calls forbidden
        lines = source.split('\n')
        in_loop = False
        direct_calls = []
        for line in lines:
            stripped = line.strip()
            if 'while True' in stripped:
                in_loop = True
            if in_loop and 'commander.process(user_input)' in stripped:
                # Only count if NOT inside the _legacy_route_governance function def
                if 'def _legacy_route_governance' not in stripped:
                    direct_calls.append(stripped)
        self.assertEqual(len(direct_calls), 0,
                         f"Direct commander.process in main loop: {direct_calls}")


# ═══════════════════════════════════════════════════════════
#  3. PolicyEngine — Domain Coverage
# ═══════════════════════════════════════════════════════════

class TestPolicyDomainCoverage(unittest.TestCase):
    """PolicyEngine must allow desktop, system, safety, general domains."""

    def setUp(self):
        from lina.governance.policy_engine import PolicyEngine, PolicyConfig
        # Use explicit default PolicyConfig to avoid TOML file interference
        self.engine = PolicyEngine(config=PolicyConfig())

    def test_desktop_domain_allowed(self):
        """'desktop' domain must be in allowed_domains."""
        from lina.governance.policy_engine import PolicyDecision
        result = self.engine.check("open_app", domain="desktop", risk_level="low")
        self.assertNotEqual(result.decision, PolicyDecision.DENY,
                            f"desktop domain denied: {result.reason}")

    def test_system_domain_allowed(self):
        """'system' domain must be in allowed_domains."""
        from lina.governance.policy_engine import PolicyDecision
        result = self.engine.check("diagnose", domain="system", risk_level="low")
        self.assertNotEqual(result.decision, PolicyDecision.DENY,
                            f"system domain denied: {result.reason}")

    def test_safety_domain_allowed(self):
        """'safety' domain must be in allowed_domains."""
        from lina.governance.policy_engine import PolicyDecision
        result = self.engine.check("set_mode", domain="safety", risk_level="low")
        self.assertNotEqual(result.decision, PolicyDecision.DENY,
                            f"safety domain denied: {result.reason}")

    def test_general_domain_allowed(self):
        """'general' domain must be in allowed_domains."""
        from lina.governance.policy_engine import PolicyDecision
        result = self.engine.check("some_action", domain="general", risk_level="low")
        self.assertNotEqual(result.decision, PolicyDecision.DENY,
                            f"general domain denied: {result.reason}")

    def test_empty_domain_normalizes_to_general(self):
        """Empty domain normalizes to 'general', not bypass."""
        from lina.governance.policy_engine import PolicyDecision
        result = self.engine.check("some_action", domain="", risk_level="low")
        # Should NOT bypass — should be checked as "general"
        self.assertNotEqual(result.decision, PolicyDecision.DENY,
                            "Empty domain should normalize to 'general'")

    def test_all_critical_domains_present(self):
        """All domains used by the system are in allowed_domains."""
        from lina.governance.policy_engine import PolicyConfig
        config = PolicyConfig()
        required = {
            "service", "package", "network", "disk", "config",
            "user", "boot", "display", "audio", "security",
            "installer", "desktop", "system", "safety", "general",
        }
        missing = required - set(config.allowed_domains)
        self.assertEqual(missing, set(),
                         f"Missing domains in allowed_domains: {missing}")

    def test_unknown_domain_denied(self):
        """Unknown domain is denied by policy."""
        from lina.governance.policy_engine import PolicyDecision
        result = self.engine.check("action", domain="totally_unknown_xyz",
                                   risk_level="low")
        self.assertEqual(result.decision, PolicyDecision.DENY)


# ═══════════════════════════════════════════════════════════
#  4. GUI /confirm /deny Parsing
# ═══════════════════════════════════════════════════════════

class TestGUIConfirmDenyParsing(unittest.TestCase):
    """GUI chat must intercept /confirm and /deny commands."""

    def setUp(self):
        from lina.gui.chat import ChatController
        self.ctrl = ChatController()

    def test_handle_confirm_deny_exists(self):
        """ChatController has _handle_confirm_deny method."""
        self.assertTrue(hasattr(self.ctrl, '_handle_confirm_deny'))

    def test_confirm_command_parsed(self):
        """/confirm <id> is parsed correctly."""
        with patch('lina.governance.confirmation.get_confirmation_handler') as mock_get:
            mock_handler = MagicMock()
            mock_handler.resolve.return_value = True
            mock_get.return_value = mock_handler
            result = self.ctrl._handle_confirm_deny("/confirm abc123")
            self.assertIsNotNone(result)
            self.assertIn("Подтверждено", result)

    def test_deny_command_parsed(self):
        """/deny <id> is parsed correctly."""
        with patch('lina.governance.confirmation.get_confirmation_handler') as mock_get:
            mock_handler = MagicMock()
            mock_handler.resolve.return_value = True
            mock_get.return_value = mock_handler
            result = self.ctrl._handle_confirm_deny("/deny abc123")
            self.assertIsNotNone(result)
            self.assertIn("отклонена", result.lower())

    def test_non_command_returns_none(self):
        """Regular text returns None (not a /confirm or /deny)."""
        result = self.ctrl._handle_confirm_deny("hello world")
        self.assertIsNone(result)

    def test_partial_command_returns_none(self):
        """/confirm without ID returns None."""
        result = self.ctrl._handle_confirm_deny("/confirm")
        self.assertIsNone(result)

    def test_send_user_message_intercepts_confirm(self):
        """send_user_message() calls _handle_confirm_deny before intent routing."""
        import inspect
        from lina.gui.chat import ChatController
        source = inspect.getsource(ChatController.send_user_message)
        self.assertIn('_handle_confirm_deny', source)


# ═══════════════════════════════════════════════════════════
#  5. GUI/Tray — No Bypass Fallbacks
# ═══════════════════════════════════════════════════════════

class TestNoBypassFallbacks(unittest.TestCase):
    """GUI and Tray must NOT fall back to direct execution on ImportError."""

    def test_chat_no_import_error_bypass(self):
        """_process_via_intent ImportError does not call _request_handler."""
        import inspect
        from lina.gui.chat import ChatController
        source = inspect.getsource(ChatController._process_via_intent)
        # Find ImportError handler
        lines = source.split('\n')
        in_import_except = False
        bypass_found = False
        for line in lines:
            if 'except ImportError' in line:
                in_import_except = True
            elif in_import_except:
                if '_request_handler' in line:
                    bypass_found = True
                    break
                if 'except' in line or 'return' in line:
                    in_import_except = False
        self.assertFalse(bypass_found,
                         "ImportError handler must NOT call _request_handler")

    def test_tray_no_import_error_bypass(self):
        """_dispatch_intent ImportError does not call callback()."""
        import inspect
        from lina.gui.tray import TrayIconController
        source = inspect.getsource(TrayIconController._dispatch_intent)
        lines = source.split('\n')
        in_import_except = False
        bypass_found = False
        for line in lines:
            if 'except ImportError' in line:
                in_import_except = True
            elif in_import_except:
                if 'callback()' in line:
                    bypass_found = True
                    break
                if 'except' in line or 'return' in line:
                    in_import_except = False
        self.assertFalse(bypass_found,
                         "ImportError handler must NOT call callback()")


# ═══════════════════════════════════════════════════════════
#  6. Hotkey Commands — Governance Path
# ═══════════════════════════════════════════════════════════

class TestHotkeyGovernanceCommands(unittest.TestCase):
    """Hotkey default bindings must route through governance (cli or dbus)."""

    def test_default_bindings_use_governance_routing(self):
        """DEFAULT_BINDINGS commands use lina.core.cli or org.lina.Assistant D-Bus."""
        from lina.governance.hotkey_manager import DEFAULT_BINDINGS
        for binding in DEFAULT_BINDINGS:
            has_cli = 'lina.core.cli' in binding.command
            has_gui = 'lina.gui.app' in binding.command
            has_dbus = 'org.lina.Assistant' in binding.command
            self.assertTrue(
                has_cli or has_gui or has_dbus,
                f"Binding '{binding.id}' command '{binding.command}' "
                f"must route through governance (cli, gui, or dbus)"
            )

    def test_no_direct_lina_py_commands(self):
        """No binding uses 'lina --diagnose' or 'lina --safe-mode'."""
        from lina.governance.hotkey_manager import DEFAULT_BINDINGS
        for binding in DEFAULT_BINDINGS:
            self.assertNotIn('lina --diagnose', binding.command)
            self.assertNotIn('lina --safe-mode', binding.command)


# ═══════════════════════════════════════════════════════════
#  7. IntentRouter — Audit Coverage
# ═══════════════════════════════════════════════════════════

class TestIntentRouterAuditCoverage(unittest.TestCase):
    """IntentRouter must audit ALL decision paths."""

    def setUp(self):
        import lina.intent.router as r_mod
        r_mod._router = None
        self.router = r_mod.IntentRouter()

    def test_chat_passthrough_audited(self):
        """Chat passthrough path emits audit log."""
        import inspect
        from lina.intent.router import IntentRouter
        source = inspect.getsource(IntentRouter.process)
        # Find the chat passthrough section
        self.assertIn('chat_passthrough', source,
                       "Chat passthrough must be audited")

    def test_not_found_audited(self):
        """NOT_FOUND fallback emits audit log."""
        import inspect
        from lina.intent.router import IntentRouter
        source = inspect.getsource(IntentRouter.process)
        self.assertIn('action_not_found', source,
                       "NOT_FOUND must be audited")

    def test_diagnose_success_audited(self):
        """_process_diagnose success emits audit log."""
        import inspect
        from lina.intent.router import IntentRouter
        source = inspect.getsource(IntentRouter._process_diagnose)
        self.assertIn('log_execution', source,
                       "_process_diagnose must call log_execution")

    def test_diagnose_not_found_audited(self):
        """_process_diagnose NOT_FOUND emits audit log."""
        import inspect
        from lina.intent.router import IntentRouter
        source = inspect.getsource(IntentRouter._process_diagnose)
        # Should have at least 2 log calls (success + not_found)
        log_calls = source.count('log_execution')
        self.assertGreaterEqual(log_calls, 2,
                                 "_process_diagnose must audit both success and not_found")

    def test_action_needs_confirm_audited(self):
        """_process_action NEEDS_CONFIRM emits audit log."""
        import inspect
        from lina.intent.router import IntentRouter
        source = inspect.getsource(IntentRouter._process_action)
        self.assertIn('action_needs_confirm', source,
                       "_process_action NEEDS_CONFIRM must be audited")


# ═══════════════════════════════════════════════════════════
#  8. Telemetry — Accurate Success Tracking
# ═══════════════════════════════════════════════════════════

class TestTelemetryAccuracy(unittest.TestCase):
    """Telemetry must track actual outcome, not always success=True."""

    def test_telemetry_tracks_variable_success(self):
        """Finally block uses _outcome_success, not hardcoded True."""
        import inspect
        from lina.intent.router import IntentRouter
        source = inspect.getsource(IntentRouter.process)
        # Find the finally block
        lines = source.split('\n')
        in_finally = False
        for line in lines:
            if 'finally:' in line:
                in_finally = True
            if in_finally and 'success=' in line:
                self.assertIn('_outcome_success', line,
                              f"Telemetry must use _outcome_success, not hardcoded: {line.strip()}")
                break
        else:
            self.fail("Could not find success= in finally block")

    def test_outcome_success_initialized(self):
        """_outcome_success is initialized before try block."""
        import inspect
        from lina.intent.router import IntentRouter
        source = inspect.getsource(IntentRouter.process)
        self.assertIn('_outcome_success = True', source)

    def test_outcome_success_set_false_on_exception(self):
        """_outcome_success set to False in except block."""
        import inspect
        from lina.intent.router import IntentRouter
        source = inspect.getsource(IntentRouter.process)
        self.assertIn('_outcome_success = False', source)


# ═══════════════════════════════════════════════════════════
#  9. Runtime → REPLSession wiring
# ═══════════════════════════════════════════════════════════

class TestRuntimeREPLWiring(unittest.TestCase):
    """core/runtime.py must pass pipeline_handler to REPLSession."""

    def test_runtime_passes_pipeline_handler(self):
        """run() passes pipeline_handler= to REPLSession."""
        import inspect
        from lina.core import runtime as rt_mod
        source = inspect.getsource(rt_mod.run)
        self.assertIn('pipeline_handler=', source,
                       "runtime.run() must pass pipeline_handler to REPLSession")


# ═══════════════════════════════════════════════════════════
#  10. End-to-end: Governance blocks unknown domain
# ═══════════════════════════════════════════════════════════

class TestGovernanceE2E(unittest.TestCase):
    """End-to-end: policy engine blocks actions in unknown domains."""

    def test_policy_blocks_fake_domain(self):
        """Action in fake domain is denied."""
        from lina.governance.policy_engine import PolicyEngine, PolicyDecision
        engine = PolicyEngine()
        result = engine.check("evil_action", domain="evil_domain",
                              risk_level="high")
        self.assertEqual(result.decision, PolicyDecision.DENY)

    def test_policy_allows_network_domain(self):
        """Action in 'network' domain passes policy (low risk)."""
        from lina.governance.policy_engine import PolicyEngine, PolicyDecision
        engine = PolicyEngine()
        result = engine.check("network_check", domain="network",
                              risk_level="low")
        self.assertEqual(result.decision, PolicyDecision.ALLOW)

    def test_policy_blocks_critical_risk(self):
        """Critical risk is always blocked."""
        from lina.governance.policy_engine import PolicyEngine, PolicyDecision
        engine = PolicyEngine()
        result = engine.check("safe_action", domain="network",
                              risk_level="critical")
        self.assertEqual(result.decision, PolicyDecision.DENY)


# ═══════════════════════════════════════════════════════════
#  Runner
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
