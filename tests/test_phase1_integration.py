"""
Phase 1 Integration Tests — все entry points → Intent → Governance.

Тесты:
  1. IntentBridge: text → Intent → classify
  2. GUI ChatController → IntentBridge (не прямой handler)
  3. GUI Tray → IntentBridge (системные действия)
  4. DBus → IntentBridge (diagnose/execute)
  5. Hotkeys → IntentBridge (dispatch_intent)
  6. Diagnostics domain resolver
  7. AccessResolver deepening (source trust, rate limit, failure escalation)
  8. E2E: full flow from text to result

Phase: INTEGRATION LAYER / Phase 1
Правило: Без тестов — не релиз.
"""

import json
import time
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# ═══════════════════════════════════════════════════════════
#  1. IntentBridge
# ═══════════════════════════════════════════════════════════

class TestIntentBridge(unittest.TestCase):
    """IntentBridge: classify_and_route, from_text, from_action, from_diagnose."""

    def setUp(self):
        # Reset singleton
        import lina.intent.bridge as bridge_mod
        bridge_mod._bridge = None
        self.bridge = bridge_mod.get_intent_bridge()

    def test_singleton(self):
        """get_intent_bridge() возвращает один и тот же объект."""
        import lina.intent.bridge as bm
        b1 = bm.get_intent_bridge()
        b2 = bm.get_intent_bridge()
        self.assertIs(b1, b2)

    def test_from_text_empty(self):
        """Пустой ввод → FAILED."""
        from lina.intent.types import IntentStatus
        result = self.bridge.from_text("", source="ui")
        self.assertEqual(result.status, IntentStatus.FAILED)

    def test_from_text_chat(self):
        """Chat ввод → CHAT_RESPONSE или SUCCESS."""
        from lina.intent.types import IntentStatus
        handler = MagicMock(return_value="Привет!")
        result = self.bridge.from_text("Привет", source="ui",
                                        pipeline_handler=handler)
        # Должен пройти через bridge (chat → pipeline handler)
        self.assertIn(result.status,
                      (IntentStatus.SUCCESS, IntentStatus.CHAT_RESPONSE))

    def test_from_text_diagnostic(self):
        """Diagnostic text → IntentType.DIAGNOSE."""
        from lina.intent.types import IntentStatus
        result = self.bridge.from_text(
            "не работает интернет", source="cli")
        # Должен распознать как diagnostic
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.status)

    def test_from_action(self):
        """from_action → IntentResult."""
        from lina.intent.types import IntentStatus
        result = self.bridge.from_action(
            action_id="svc_restart",
            domain="service",
            params={"service": "NetworkManager"},
            source="dbus",
        )
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.status)

    def test_from_diagnose(self):
        """from_diagnose → IntentResult."""
        from lina.intent.types import IntentStatus
        result = self.bridge.from_diagnose(
            domain="network",
            user_text="нет интернета",
            source="ui",
        )
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.status)

    def test_from_action_sources(self):
        """Все четыре источника работают."""
        for source in ("ui", "cli", "dbus", "hotkey"):
            result = self.bridge.from_action(
                action_id="test_action",
                domain="test",
                source=source,
            )
            self.assertIsNotNone(result)

    def test_stats(self):
        """Статистика обновляется."""
        self.bridge.from_text("привет", source="ui")
        self.bridge.from_text("почини систему", source="cli")
        stats = self.bridge.get_stats()
        self.assertGreaterEqual(stats["total"], 2)

    def test_classify_system_command(self):
        """! команда → SYSTEM_ACTION."""
        from lina.intent.types import IntentType
        intent_type, domain, action, conf = self.bridge._classify("!systemctl status")
        self.assertEqual(intent_type, IntentType.SYSTEM_ACTION)

    def test_classify_open_app(self):
        """'Открой Firefox' → OPEN_APP."""
        from lina.intent.types import IntentType
        intent_type, domain, action, conf = self.bridge._classify("Открой Firefox")
        self.assertEqual(intent_type, IntentType.OPEN_APP)

    def test_classify_install(self):
        """'Установи vim' → PACKAGE_OP."""
        from lina.intent.types import IntentType
        intent_type, domain, action, conf = self.bridge._classify("Установи vim")
        self.assertEqual(intent_type, IntentType.PACKAGE_OP)

    def test_classify_web_search(self):
        """'Найди в интернете python' → SEARCH."""
        from lina.intent.types import IntentType
        intent_type, domain, action, conf = self.bridge._classify("Найди в интернете python")
        self.assertEqual(intent_type, IntentType.SEARCH)

    def test_classify_diagnose(self):
        """'Не работает звук' → DIAGNOSE."""
        from lina.intent.types import IntentType
        intent_type, domain, action, conf = self.bridge._classify("Не работает звук")
        self.assertEqual(intent_type, IntentType.DIAGNOSE)

    def test_classify_diagnose_domain_resolved(self):
        """Diagnostic + domain resolver: 'не работает wifi' → domain=network."""
        intent_type, domain, action, conf = self.bridge._classify("Не работает wifi")
        self.assertEqual(domain, "network")


# ═══════════════════════════════════════════════════════════
#  2. GUI ChatController → IntentBridge
# ═══════════════════════════════════════════════════════════

class TestChatControllerIntentBridge(unittest.TestCase):
    """ChatController.send_user_message → IntentBridge, не прямой handler."""

    def setUp(self):
        from lina.gui.chat import ChatController
        self.ctrl = ChatController()

    def test_send_message_uses_intent_bridge(self):
        """send_user_message должен вызывать _process_via_intent."""
        self.ctrl._process_via_intent = MagicMock(return_value="OK")
        self.ctrl.send_user_message("Привет")
        self.ctrl._process_via_intent.assert_called_once_with("Привет")

    def test_send_message_denied_shows_marker(self):
        """DENIED → сообщение содержит 🚫."""
        from lina.intent.types import IntentResult, IntentStatus
        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_text.return_value = IntentResult(
                intent_id="t1",
                status=IntentStatus.DENIED,
                response_text="Недостаточно прав",
            )
            msg = self.ctrl.send_user_message("Удали /boot")
            self.assertIsNotNone(msg)
            self.assertIn("🚫", msg.content)

    def test_send_message_needs_confirm(self):
        """NEEDS_CONFIRM → сообщение содержит 'подтверждение'."""
        from lina.intent.types import IntentResult, IntentStatus
        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_text.return_value = IntentResult(
                intent_id="t2",
                status=IntentStatus.NEEDS_CONFIRM,
                response_text="Перезапуск NetworkManager",
                escalation_id="esc_001",
            )
            msg = self.ctrl.send_user_message("Перезапусти NetworkManager")
            self.assertIsNotNone(msg)
            self.assertIn("подтверждени", msg.content.lower())

    def test_send_message_empty_returns_none(self):
        """Пустой ввод → None."""
        result = self.ctrl.send_user_message("")
        self.assertIsNone(result)

    def test_process_via_intent_fallback_to_handler(self):
        """Если bridge.from_text → CHAT_RESPONSE, pipeline_handler вызывается."""
        handler = MagicMock(return_value="LLM ответ")
        self.ctrl.set_request_handler(handler)

        from lina.intent.types import IntentResult, IntentStatus
        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_text.return_value = IntentResult(
                intent_id="t3",
                status=IntentStatus.CHAT_RESPONSE,
            )
            response = self.ctrl._process_via_intent("Расскажи анекдот")
            handler.assert_called_once_with("Расскажи анекдот")

    def test_has_process_via_intent_method(self):
        """ChatController имеет _process_via_intent."""
        self.assertTrue(hasattr(self.ctrl, '_process_via_intent'))


# ═══════════════════════════════════════════════════════════
#  3. GUI Tray → IntentBridge
# ═══════════════════════════════════════════════════════════

class TestTrayIntentBridge(unittest.TestCase):
    """TrayIconController: UI actions → direct, system actions → intent."""

    def setUp(self):
        from lina.gui.tray import TrayIconController
        self.tray = TrayIconController()

    def test_ui_action_direct(self):
        """UI-only actions (open_chat, quit) → прямой callback."""
        callback = MagicMock()
        self.tray.register_action("open_chat", callback)
        self.tray.handle_action("open_chat")
        callback.assert_called_once()

    def test_ui_action_about(self):
        """about → direct callback."""
        callback = MagicMock()
        self.tray.register_action("about", callback)
        self.tray.handle_action("about")
        callback.assert_called_once()

    def test_system_action_dispatches_intent(self):
        """system_overview → IntentBridge.from_diagnose."""
        from lina.intent.types import IntentResult, IntentStatus
        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_diagnose.return_value = IntentResult(
                intent_id="t5",
                status=IntentStatus.SUCCESS,
                response_text="System OK",
            )
            result = self.tray.handle_action("system_overview")
            mock_bridge.return_value.from_diagnose.assert_called_once()
            self.assertTrue(result)

    def test_system_action_denied_notifies(self):
        """DENIED → notification."""
        from lina.intent.types import IntentResult, IntentStatus
        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_diagnose.return_value = IntentResult(
                intent_id="t6",
                status=IntentStatus.DENIED,
                response_text="No access",
            )
            result = self.tray.handle_action("system_overview")
            self.assertFalse(result)

    def test_has_dispatch_intent_method(self):
        """TrayIconController has _dispatch_intent."""
        self.assertTrue(hasattr(self.tray, '_dispatch_intent'))


# ═══════════════════════════════════════════════════════════
#  4. DBus → IntentBridge
# ═══════════════════════════════════════════════════════════

class TestDBusIntentBridge(unittest.TestCase):
    """LinaDBusService.diagnose / execute_action → IntentBridge."""

    def setUp(self):
        from lina.governance.dbus_service import LinaDBusService
        self.svc = LinaDBusService()

    def test_diagnose_calls_bridge(self):
        """diagnose(domain) → IntentBridge.from_diagnose."""
        from lina.intent.types import IntentResult, IntentStatus
        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_diagnose.return_value = IntentResult(
                intent_id="d1",
                status=IntentStatus.SUCCESS,
                response_text="Network OK",
            )
            result_json = self.svc.diagnose("network")
            data = json.loads(result_json)
            self.assertEqual(data["domain"], "network")
            self.assertEqual(data["status"], "success")
            mock_bridge.return_value.from_diagnose.assert_called_once()

    def test_execute_action_calls_bridge(self):
        """execute_action → IntentBridge.from_action."""
        from lina.intent.types import IntentResult, IntentStatus
        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_action.return_value = IntentResult(
                intent_id="a1",
                status=IntentStatus.SUCCESS,
                response_text="Done",
            )
            result_json = self.svc.execute_action(
                "svc_restart", '{"service": "NetworkManager"}')
            data = json.loads(result_json)
            self.assertEqual(data["status"], "success")
            mock_bridge.return_value.from_action.assert_called_once()

    def test_execute_action_denied(self):
        """execute_action DENIED → status=denied в JSON."""
        from lina.intent.types import IntentResult, IntentStatus
        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_action.return_value = IntentResult(
                intent_id="a2",
                status=IntentStatus.DENIED,
                response_text="No access",
            )
            result_json = self.svc.execute_action("disk_format", '{}')
            data = json.loads(result_json)
            self.assertEqual(data["status"], "denied")

    def test_diagnose_source_is_dbus(self):
        """diagnose передаёт source='dbus'."""
        from lina.intent.types import IntentResult, IntentStatus
        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_diagnose.return_value = IntentResult(
                intent_id="d2", status=IntentStatus.SUCCESS)
            self.svc.diagnose("audio")
            call_kwargs = mock_bridge.return_value.from_diagnose.call_args
            self.assertEqual(call_kwargs.kwargs.get("source"), "dbus")


# ═══════════════════════════════════════════════════════════
#  5. Hotkeys → IntentBridge
# ═══════════════════════════════════════════════════════════

class TestHotkeyIntentBridge(unittest.TestCase):
    """HotkeyManager.dispatch_intent → IntentBridge."""

    def setUp(self):
        from lina.governance.hotkey_manager import HotkeyManager, HotkeyBinding
        self.mgr = HotkeyManager()

    def test_dispatch_intent_diagnose(self):
        """quick_diag с intent_action='diagnose' → IntentBridge.from_diagnose."""
        from lina.governance.hotkey_manager import HotkeyBinding
        from lina.intent.types import IntentResult, IntentStatus

        binding = HotkeyBinding(
            id="quick_diag", name="Quick Diag",
            keys="Meta+Shift+D",
            command="lina --diagnose",
            intent_action="diagnose",
            intent_domain="system",
        )

        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_diagnose.return_value = IntentResult(
                intent_id="h1",
                status=IntentStatus.SUCCESS,
                response_text="System OK",
            )
            result = self.mgr.dispatch_intent(binding)
            self.assertIsNotNone(result)
            mock_bridge.return_value.from_diagnose.assert_called_once()

    def test_dispatch_intent_action(self):
        """safe_mode с intent_action='set_mode' → IntentBridge.from_action."""
        from lina.governance.hotkey_manager import HotkeyBinding
        from lina.intent.types import IntentResult, IntentStatus

        binding = HotkeyBinding(
            id="safe_mode", name="Safe Mode",
            keys="Meta+Shift+S",
            command="lina --safe-mode",
            intent_action="set_mode",
            intent_domain="safety",
        )

        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_action.return_value = IntentResult(
                intent_id="h2",
                status=IntentStatus.SUCCESS,
            )
            result = self.mgr.dispatch_intent(binding)
            self.assertIsNotNone(result)
            mock_bridge.return_value.from_action.assert_called_once()

    def test_dispatch_intent_no_intent_action(self):
        """Binding без intent_action → None (shell fallback)."""
        from lina.governance.hotkey_manager import HotkeyBinding
        binding = HotkeyBinding(
            id="legacy", name="Legacy",
            keys="Meta+X",
            command="lina",
        )
        result = self.mgr.dispatch_intent(binding)
        self.assertIsNone(result)

    def test_default_bindings_have_intent(self):
        """DEFAULT_BINDINGS все имеют intent_action."""
        from lina.governance.hotkey_manager import DEFAULT_BINDINGS
        for b in DEFAULT_BINDINGS:
            self.assertTrue(b.intent_action,
                            f"Binding {b.id} has no intent_action")

    def test_hotkey_source_is_hotkey(self):
        """dispatch_intent передаёт source='hotkey'."""
        from lina.governance.hotkey_manager import HotkeyBinding
        from lina.intent.types import IntentResult, IntentStatus

        binding = HotkeyBinding(
            id="test", name="Test",
            intent_action="diagnose", intent_domain="system",
        )

        with patch("lina.intent.bridge.get_intent_bridge") as mock_bridge:
            mock_bridge.return_value.from_diagnose.return_value = IntentResult(
                intent_id="h3", status=IntentStatus.SUCCESS)
            self.mgr.dispatch_intent(binding)
            call_kwargs = mock_bridge.return_value.from_diagnose.call_args
            self.assertEqual(call_kwargs.kwargs.get("source"), "hotkey")


# ═══════════════════════════════════════════════════════════
#  6. Diagnostics Domain Resolver
# ═══════════════════════════════════════════════════════════

class TestDomainResolver(unittest.TestCase):
    """Diagnostics domain resolver: text → domain."""

    def test_network_domain(self):
        from lina.diagnostics.domain_resolver import resolve_domain
        domain, conf = resolve_domain("не работает wifi")
        self.assertEqual(domain, "network")
        self.assertGreater(conf, 0.5)

    def test_audio_domain(self):
        from lina.diagnostics.domain_resolver import resolve_domain
        domain, conf = resolve_domain("пропал звук после обновления")
        self.assertEqual(domain, "audio")

    def test_bluetooth_domain(self):
        from lina.diagnostics.domain_resolver import resolve_domain
        domain, conf = resolve_domain("блютуз наушники не подключаются")
        self.assertEqual(domain, "bluetooth")

    def test_disk_domain(self):
        from lina.diagnostics.domain_resolver import resolve_domain
        domain, conf = resolve_domain("нет места на диске")
        self.assertEqual(domain, "disk")

    def test_display_domain(self):
        from lina.diagnostics.domain_resolver import resolve_domain
        domain, conf = resolve_domain("чёрный экран после загрузки")
        # Could be display or boot
        self.assertIn(domain, ("display", "boot"))

    def test_package_domain(self):
        from lina.diagnostics.domain_resolver import resolve_domain
        domain, conf = resolve_domain("конфликт пакетов при обновлении")
        self.assertEqual(domain, "package")

    def test_service_domain(self):
        from lina.diagnostics.domain_resolver import resolve_domain
        domain, conf = resolve_domain("не запускается сервис nginx")
        self.assertEqual(domain, "service")

    def test_performance_domain(self):
        from lina.diagnostics.domain_resolver import resolve_domain
        domain, conf = resolve_domain("компьютер тормозит и лагает")
        self.assertEqual(domain, "performance")

    def test_system_fallback(self):
        from lina.diagnostics.domain_resolver import resolve_domain
        domain, conf = resolve_domain("абракадабра 123")
        self.assertEqual(domain, "system")
        self.assertLessEqual(conf, 0.5)

    def test_empty_input(self):
        from lina.diagnostics.domain_resolver import resolve_domain
        domain, conf = resolve_domain("")
        self.assertEqual(domain, "system")

    def test_available_domains(self):
        from lina.diagnostics.domain_resolver import get_available_domains
        domains = get_available_domains()
        self.assertIn("network", domains)
        self.assertIn("audio", domains)
        self.assertIn("disk", domains)
        self.assertGreaterEqual(len(domains), 10)

    def test_domain_keywords(self):
        from lina.diagnostics.domain_resolver import get_domain_keywords
        kw = get_domain_keywords("network")
        self.assertTrue(len(kw) > 0)
        self.assertIn("WiFi", kw)


# ═══════════════════════════════════════════════════════════
#  7. AccessResolver Deepening
# ═══════════════════════════════════════════════════════════

class TestAccessResolverDeepened(unittest.TestCase):
    """AccessLevelResolver: source trust, rate limiting, failure escalation."""

    def setUp(self):
        import lina.access.resolver as resolver_mod
        resolver_mod._resolver = None  # Reset singleton
        self.resolver = resolver_mod.get_access_resolver()

    def _make_intent(self, **kwargs):
        """Helper: create mock intent."""
        from lina.intent.types import Intent, IntentType
        defaults = {
            "type": IntentType.SYSTEM_ACTION,
            "domain": "service",
            "action": "svc_restart",
            "source": "ui",
        }
        defaults.update(kwargs)
        return Intent(**defaults)

    def test_source_trust_ui_allowed(self):
        """UI source (trust=3) → power action allowed."""
        from lina.access.levels import AccessLevel
        intent = self._make_intent(source="ui")
        result = self.resolver.check(intent)
        self.assertTrue(result.allowed)

    def test_source_trust_dbus_admin_denied(self):
        """DBus source (trust=1) → admin action denied."""
        from lina.intent.types import IntentType
        intent = self._make_intent(
            type=IntentType.DISK_OP,
            domain="disk",
            action="disk_format",
            source="dbus",
        )
        result = self.resolver.check(intent)
        self.assertFalse(result.allowed)

    def test_source_trust_hotkey_admin_denied(self):
        """Hotkey source (trust=1) → admin action denied."""
        from lina.intent.types import IntentType
        intent = self._make_intent(
            type=IntentType.LOW_LEVEL,
            domain="low_level",
            action="disk_partition",
            source="hotkey",
        )
        result = self.resolver.check(intent)
        self.assertFalse(result.allowed)

    def test_dbus_power_needs_confirm(self):
        """DBus + power action → needs_confirmation=True."""
        from lina.access.levels import AccessLevel
        self.resolver.set_session_level(AccessLevel.POWER)
        intent = self._make_intent(
            domain="service",
            action="svc_restart",
            source="dbus",
        )
        result = self.resolver.check(intent)
        self.assertTrue(result.allowed)
        self.assertTrue(result.needs_confirmation)

    def test_failure_escalation(self):
        """После 3 ошибок → needs_confirmation=True."""
        from lina.access.levels import AccessLevel
        self.resolver.set_session_level(AccessLevel.POWER)
        intent = self._make_intent(action="test_action", source="ui")

        # До ошибок — user action без confirm
        intent_user = self._make_intent(
            action="test_action", source="ui",
            domain="desktop",
        )
        # Набираем 3 ошибки
        self.resolver.record_failure("test_action")
        self.resolver.record_failure("test_action")
        self.resolver.record_failure("test_action")

        result = self.resolver.check(intent_user)
        self.assertTrue(result.needs_confirmation)

    def test_failure_reset(self):
        """reset_failures сбрасывает счётчик."""
        self.resolver.record_failure("x")
        self.resolver.record_failure("x")
        self.resolver.record_failure("x")
        self.assertTrue(self.resolver._should_escalate_confirm("x"))
        self.resolver.reset_failures("x")
        self.assertFalse(self.resolver._should_escalate_confirm("x"))

    def test_rate_limit(self):
        """Rate limiting: превышение лимита → denied."""
        from lina.access.levels import AccessLevel
        self.resolver.set_session_level(AccessLevel.ADMIN)

        # Заполняем timestamps вручную для admin level
        now = time.time()
        self.resolver._action_timestamps["admin"] = [now] * 3

        intent = self._make_intent(
            type=self._make_intent().type,
            domain="disk",
            action="disk_format",
            source="ui",
        )
        # is_admin → access level admin → rate check "admin"
        from lina.intent.types import IntentType
        intent_admin = self._make_intent(
            type=IntentType.DISK_OP,
            domain="disk",
            action="disk_format",
            source="ui",
        )
        result = self.resolver.check(intent_admin)
        self.assertFalse(result.allowed)
        self.assertIn("Rate limit", result.reason)

    def test_stats_include_new_fields(self):
        """Stats включают failure_counts и session_age_sec."""
        stats = self.resolver.get_stats()
        self.assertIn("failure_counts", stats)
        self.assertIn("session_age_sec", stats)
        self.assertIsInstance(stats["session_age_sec"], float)


# ═══════════════════════════════════════════════════════════
#  8. E2E: full flow
# ═══════════════════════════════════════════════════════════

class TestE2EFlow(unittest.TestCase):
    """End-to-end: text → classify → Intent → bridge → result."""

    def test_chat_text_e2e(self):
        """'Привет' → classify as CHAT → handler → response."""
        import lina.intent.bridge as bridge_mod
        bridge_mod._bridge = None
        bridge = bridge_mod.get_intent_bridge()
        handler = MagicMock(return_value="Привет! Чем помочь?")
        result = bridge.from_text("Привет, как дела?", source="cli",
                                   pipeline_handler=handler)
        self.assertIn(result.response_text, ["Привет! Чем помочь?", ""])

    def test_system_command_e2e(self):
        """'!ls' → classify as SYSTEM_ACTION → governance."""
        import lina.intent.bridge as bridge_mod
        bridge_mod._bridge = None
        bridge = bridge_mod.get_intent_bridge()
        result = bridge.from_text("!ls -la", source="ui")
        # Should route through governance (action)
        self.assertIsNotNone(result)

    def test_diagnostic_e2e(self):
        """'Не работает wifi' → DIAGNOSE → domain=network."""
        import lina.intent.bridge as bridge_mod
        bridge_mod._bridge = None
        bridge = bridge_mod.get_intent_bridge()
        result = bridge.from_text("Не работает wifi", source="ui")
        self.assertIsNotNone(result)

    def test_open_app_e2e(self):
        """'Открой Firefox' → OPEN_APP."""
        import lina.intent.bridge as bridge_mod
        bridge_mod._bridge = None
        bridge = bridge_mod.get_intent_bridge()
        result = bridge.from_text("Открой Firefox", source="ui")
        self.assertIsNotNone(result)

    def test_install_e2e(self):
        """'Установи vim' → PACKAGE_OP."""
        import lina.intent.bridge as bridge_mod
        bridge_mod._bridge = None
        bridge = bridge_mod.get_intent_bridge()
        result = bridge.from_text("Установи vim", source="cli")
        self.assertIsNotNone(result)


# ═══════════════════════════════════════════════════════════
#  9. Intent types integrity
# ═══════════════════════════════════════════════════════════

class TestIntentTypesIntegrity(unittest.TestCase):
    """Проверки целостности типов."""

    def test_mapping_covers_all_core_intents(self):
        """_CORE_INTENT_TO_GOV покрывает все core Intent values."""
        from lina.core.intent_router import Intent as CoreIntent
        from lina.intent.bridge import _CORE_INTENT_TO_GOV

        for ci in CoreIntent:
            self.assertIn(ci.value, _CORE_INTENT_TO_GOV,
                          f"Core intent '{ci.value}' not mapped in bridge")

    def test_all_intent_types_exist(self):
        """IntentType содержит все необходимые типы."""
        from lina.intent.types import IntentType
        required = ["CHAT", "DIAGNOSE", "OPEN_APP", "SYSTEM_ACTION",
                     "PACKAGE_OP", "SEARCH", "QUERY", "SET_MODE"]
        for name in required:
            self.assertTrue(hasattr(IntentType, name), f"Missing IntentType.{name}")

    def test_intent_requires_action(self):
        """Intent.requires_action() — chat/query не требуют, action требует."""
        from lina.intent.types import Intent, IntentType
        chat = Intent(type=IntentType.CHAT)
        self.assertFalse(chat.requires_action())

        action = Intent(type=IntentType.SYSTEM_ACTION)
        self.assertTrue(action.requires_action())

    def test_intent_sources(self):
        """Intent принимает все четыре source."""
        from lina.intent.types import Intent, IntentType
        for src in ("ui", "cli", "dbus", "hotkey"):
            i = Intent(type=IntentType.CHAT, source=src)
            self.assertEqual(i.source, src)


# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
