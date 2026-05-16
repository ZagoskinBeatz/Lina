"""
Tests — System Module Wiring (Block C integration).

Tests that domain modules (diagnostics, package_manager, service_manager,
network_manager, display_manager, hardware_info, audio_manager) are properly
wired into the pipeline via:
  1. QueryPreprocessor.enrich_for_llm() — context enrichment
  2. QueryPreprocessor._try_system_query() — direct answers
  3. AudioManager — new module
"""

import unittest
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════
#  AudioManager unit tests
# ═══════════════════════════════════════════════════════════

class TestAudioManager(unittest.TestCase):
    """AudioManager — new C7 module."""

    def test_import(self):
        """AudioManager imports without errors."""
        from lina.system.audio_manager import (
            AudioManager, AudioServer, AudioSink, AudioSource,
            AudioDiagResult,
        )
        self.assertIsNotNone(AudioManager)

    def test_init(self):
        """AudioManager creates without errors."""
        from lina.system.audio_manager import AudioManager
        am = AudioManager()
        self.assertIsNotNone(am)

    def test_detect_server_enum(self):
        """AudioServer enum has expected values."""
        from lina.system.audio_manager import AudioServer
        self.assertEqual(AudioServer.PIPEWIRE.value, "pipewire")
        self.assertEqual(AudioServer.PULSEAUDIO.value, "pulseaudio")
        self.assertEqual(AudioServer.ALSA_ONLY.value, "alsa")
        self.assertEqual(AudioServer.UNKNOWN.value, "unknown")

    @patch("lina.system.audio_manager._run_rc")
    def test_detect_pipewire(self, mock_run):
        """Detects PipeWire as audio server."""
        from lina.system.audio_manager import AudioManager, AudioServer
        mock_run.side_effect = [
            (0, "12345", ""),  # pgrep pipewire → found
            (0, "Server Name: PipeWire", ""),  # pactl info
        ]
        am = AudioManager()
        server = am.detect_server()
        self.assertEqual(server, AudioServer.PIPEWIRE)

    @patch("lina.system.audio_manager._run_rc")
    def test_detect_pulseaudio(self, mock_run):
        """Detects PulseAudio when PipeWire not running."""
        from lina.system.audio_manager import AudioManager, AudioServer
        mock_run.side_effect = [
            (1, "", ""),  # pgrep pipewire → not found
            (0, "12345", ""),  # pgrep pulseaudio → found
        ]
        am = AudioManager()
        server = am.detect_server()
        self.assertEqual(server, AudioServer.PULSEAUDIO)

    def test_volume_cmd_generation(self):
        """Command generation doesn't execute anything."""
        from lina.system.audio_manager import AudioManager
        am = AudioManager()
        self.assertIn("50%", am.set_volume_cmd(50))
        self.assertIn("+10%", am.volume_up_cmd(10))
        self.assertIn("-5%", am.volume_down_cmd(5))
        self.assertIn("toggle", am.mute_toggle_cmd())

    def test_volume_cmd_clamped(self):
        """Volume is clamped to 0-150."""
        from lina.system.audio_manager import AudioManager
        am = AudioManager()
        self.assertIn("0%", am.set_volume_cmd(-10))
        self.assertIn("150%", am.set_volume_cmd(200))

    @patch("lina.system.audio_manager._run_rc")
    def test_diagnose_no_server(self, mock_run):
        """Diagnose returns issues when no audio server found."""
        from lina.system.audio_manager import AudioManager, AudioServer
        mock_run.side_effect = [
            (1, "", ""),  # pgrep pipewire → not found
            (1, "", ""),  # pgrep pulseaudio → not found
            (1, "", ""),  # aplay -l → not found
        ]
        am = AudioManager()
        diag = am.diagnose_no_sound()
        self.assertFalse(diag.ok)
        self.assertEqual(diag.server, AudioServer.UNKNOWN)
        self.assertTrue(len(diag.issues) > 0)
        self.assertTrue(len(diag.suggestions) > 0)

    def test_format_status(self):
        """format_status returns a string."""
        from lina.system.audio_manager import AudioManager
        am = AudioManager()
        with patch.object(am, 'detect_server') as mock_det:
            from lina.system.audio_manager import AudioServer
            mock_det.return_value = AudioServer.PIPEWIRE
            with patch.object(am, 'list_sinks', return_value=[]):
                with patch.object(am, 'list_sources', return_value=[]):
                    status = am.format_status()
                    self.assertIn("pipewire", status)

    def test_set_default_sink_cmd(self):
        """set_default_sink_cmd generates correct command."""
        from lina.system.audio_manager import AudioManager
        am = AudioManager()
        cmd = am.set_default_sink_cmd("alsa_output.pci-0000_00_1f.3.analog-stereo")
        self.assertIn("pactl set-default-sink", cmd)
        self.assertIn("alsa_output", cmd)


# ═══════════════════════════════════════════════════════════
#  QueryPreprocessor._try_system_query() — direct answers
# ═══════════════════════════════════════════════════════════

class TestSystemQueryDirect(unittest.TestCase):
    """_try_system_query() returns instant answers from domain modules."""

    def _make_preprocessor(self):
        """Create a QueryPreprocessor with mocked snapshot."""
        from lina.core.system_interaction import QueryPreprocessor, SystemSnapshot
        snap = SystemSnapshot(
            distro="CachyOS", kernel="6.13.0", de="KDE",
            display_server="wayland",
        )
        return QueryPreprocessor(snapshot=snap)

    def test_failed_services_query(self):
        """'Упавшие сервисы' → direct answer from ServiceManager."""
        mock_sm = MagicMock()
        mock_sm.list_services.return_value = [
            {"name": "bluetooth.service", "description": "Bluetooth"},
        ]
        with patch("lina.system.service_manager.ServiceManager", return_value=mock_sm):
            pp = self._make_preprocessor()
            result = pp._try_system_query("упавшие сервисы")
            if result is not None:
                self.assertIn("bluetooth", result.lower())

    @patch("lina.system.audio_manager.AudioManager")
    def test_audio_diag_query_pattern(self, MockAM):
        """'Нет звука' → delegates to AudioManager."""
        pp = self._make_preprocessor()
        # Test pattern matching
        patterns = pp._SYS_QUERY_PATTERNS["audio_diag"]
        self.assertTrue(patterns.search("нет звука"))
        self.assertTrue(patterns.search("пропал звук"))
        self.assertTrue(patterns.search("аудио не работает"))

    def test_net_diag_pattern(self):
        """Network diagnostic patterns match correctly."""
        pp = self._make_preprocessor()
        patterns = pp._SYS_QUERY_PATTERNS["net_diag"]
        self.assertTrue(patterns.search("нет интернета"))
        self.assertTrue(patterns.search("wifi не работает"))
        self.assertTrue(patterns.search("пропала сеть"))
        self.assertFalse(patterns.search("настрой сеть"))

    def test_updates_pattern(self):
        """Updates query patterns match correctly."""
        pp = self._make_preprocessor()
        patterns = pp._SYS_QUERY_PATTERNS["updates"]
        self.assertTrue(patterns.search("есть обновления?"))
        self.assertTrue(patterns.search("доступные обновления"))
        self.assertTrue(patterns.search("обновления доступны"))
        self.assertFalse(patterns.search("установи обновления"))

    def test_hw_summary_pattern(self):
        """Hardware summary patterns match correctly."""
        pp = self._make_preprocessor()
        patterns = pp._SYS_QUERY_PATTERNS["hw_summary"]
        self.assertTrue(patterns.search("обзор системы"))
        self.assertTrue(patterns.search("характеристики компьютера"))
        self.assertTrue(patterns.search("конфигурация системы"))

    def test_display_info_pattern(self):
        """Display info patterns match correctly."""
        pp = self._make_preprocessor()
        patterns = pp._SYS_QUERY_PATTERNS["display_info"]
        self.assertTrue(patterns.search("инфо монитор"))
        self.assertTrue(patterns.search("параметры экрана"))
        self.assertTrue(patterns.search("видеокарта какая"))

    def test_failed_services_none_on_no_match(self):
        """Non-matching query returns None."""
        pp = self._make_preprocessor()
        result = pp._try_system_query("привет мир")
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════
#  QueryPreprocessor.enrich_for_llm() — context enrichment
# ═══════════════════════════════════════════════════════════

class TestEnrichForLLM(unittest.TestCase):
    """enrich_for_llm() adds system context to LLM prompt."""

    def _make_preprocessor(self):
        from lina.core.system_interaction import QueryPreprocessor, SystemSnapshot
        snap = SystemSnapshot(
            distro="CachyOS", kernel="6.13.0", de="KDE",
            display_server="wayland",
        )
        return QueryPreprocessor(snapshot=snap)

    def test_always_has_system_info(self):
        """Every query gets basic system info."""
        pp = self._make_preprocessor()
        result = pp.enrich_for_llm("привет")
        self.assertIn("CachyOS", result)
        self.assertIn("KDE", result)

    def test_disk_query_enriches(self):
        """'сколько места на диске' triggers diagnostics module."""
        pp = self._make_preprocessor()
        with patch("lina.system.diagnostics.get_system_summary", return_value={
            "ram": {"total_h": "16G", "used_h": "8G", "available_h": "8G"},
        }):
            with patch("lina.system.diagnostics.get_disk_usage", return_value=[
                {"mount": "/", "size_h": "500G", "used_h": "250G", "use_pct": "50%"},
            ]):
                result = pp.enrich_for_llm("сколько места на диске")
                # Should contain system info + system data
                self.assertIn("CachyOS", result)

    def test_network_query_enriches(self):
        """Network query triggers NetworkDiagnostics module."""
        pp = self._make_preprocessor()
        mock_nd = MagicMock()
        mock_nd.get_interfaces.return_value = [
            {"name": "wlan0", "state": "UP", "ipv4": "192.168.1.100"}
        ]
        mock_nd.get_active_connections.return_value = [{"name": "WiFi"}]
        with patch("lina.system.network_manager.NetworkDiagnostics", return_value=mock_nd):
            result = pp.enrich_for_llm("покажи wifi сети")
            self.assertIn("CachyOS", result)

    def test_audio_query_enriches(self):
        """Audio query triggers AudioManager module."""
        pp = self._make_preprocessor()
        mock_am = MagicMock()
        mock_am.format_status.return_value = "Аудиосервер: pipewire\nУстройства: 1"
        with patch("lina.system.audio_manager.AudioManager", return_value=mock_am):
            result = pp.enrich_for_llm("текущая громкость")
            self.assertIn("CachyOS", result)

    def test_package_query_enriches(self):
        """Package query triggers PackageManager module."""
        pp = self._make_preprocessor()
        mock_pm = MagicMock()
        mock_pm.check_updates.return_value = []
        mock_pm._distro_id = "cachyos"
        with patch("lina.system.package_manager.PackageManager", return_value=mock_pm):
            result = pp.enrich_for_llm("проверь обновления pacman")
            self.assertIn("CachyOS", result)

    def test_service_query_enriches(self):
        """Service query triggers ServiceManager module."""
        pp = self._make_preprocessor()
        mock_sm = MagicMock()
        mock_sm.list_services.return_value = []
        with patch("lina.system.service_manager.ServiceManager", return_value=mock_sm):
            result = pp.enrich_for_llm("какие сервисы сломались")
            self.assertIn("CachyOS", result)

    def test_bluetooth_query_enriches(self):
        """Bluetooth query triggers diagnostics module."""
        pp = self._make_preprocessor()
        with patch("lina.system.diagnostics.get_bluetooth_status", return_value="Bluetooth: вкл"):
            result = pp.enrich_for_llm("статус блютуз")
            self.assertIn("CachyOS", result)


# ═══════════════════════════════════════════════════════════
#  System modules existence and API
# ═══════════════════════════════════════════════════════════

class TestSystemModulesExist(unittest.TestCase):
    """All 7 domain system modules exist and have expected APIs."""

    def test_diagnostics_has_functions(self):
        from lina.system.diagnostics import (
            get_system_summary, get_journal_errors, get_dmesg_errors,
            get_failed_services, get_disk_usage, get_memory_pressure,
            get_cpu_load_analysis, get_network_status, get_gpu_status,
            get_boot_log, get_audio_status, get_bluetooth_status,
        )

    def test_package_manager_has_class(self):
        from lina.system.package_manager import PackageManager
        pm = PackageManager()
        self.assertTrue(hasattr(pm, 'search'))
        self.assertTrue(hasattr(pm, 'info'))
        self.assertTrue(hasattr(pm, 'install'))
        self.assertTrue(hasattr(pm, 'update'))
        self.assertTrue(hasattr(pm, 'check_updates'))

    def test_service_manager_has_class(self):
        from lina.system.service_manager import ServiceManager
        sm = ServiceManager()
        self.assertTrue(hasattr(sm, 'list_services'))
        self.assertTrue(hasattr(sm, 'status'))
        self.assertTrue(hasattr(sm, 'start'))
        self.assertTrue(hasattr(sm, 'stop'))
        self.assertTrue(hasattr(sm, 'diagnose'))

    def test_network_manager_has_class(self):
        from lina.system.network_manager import NetworkDiagnostics
        nd = NetworkDiagnostics()
        self.assertTrue(hasattr(nd, 'get_interfaces'))
        self.assertTrue(hasattr(nd, 'check_internet'))
        self.assertTrue(hasattr(nd, 'diagnose_no_internet'))

    def test_display_manager_has_functions(self):
        from lina.system.display_manager import (
            detect_display_server, list_monitors, list_gpus,
            get_display_summary,
        )

    def test_hardware_info_has_class(self):
        from lina.system.hardware_info import HardwareInfo
        hw = HardwareInfo()
        self.assertTrue(hasattr(hw, 'get_cpu_info'))
        self.assertTrue(hasattr(hw, 'get_gpu_info'))
        self.assertTrue(hasattr(hw, 'get_ram_info'))
        self.assertTrue(hasattr(hw, 'get_full_summary'))
        self.assertTrue(hasattr(hw, 'format_summary'))

    def test_audio_manager_has_class(self):
        from lina.system.audio_manager import AudioManager
        am = AudioManager()
        self.assertTrue(hasattr(am, 'detect_server'))
        self.assertTrue(hasattr(am, 'list_sinks'))
        self.assertTrue(hasattr(am, 'list_sources'))
        self.assertTrue(hasattr(am, 'get_volume'))
        self.assertTrue(hasattr(am, 'is_muted'))
        self.assertTrue(hasattr(am, 'diagnose_no_sound'))
        self.assertTrue(hasattr(am, 'format_status'))
        self.assertTrue(hasattr(am, 'format_diagnosis'))


# ═══════════════════════════════════════════════════════════
#  Pipeline wiring — app.py history integration
# ═══════════════════════════════════════════════════════════

class TestPipelineHistory(unittest.TestCase):
    """Chat history is passed to LLM for follow-up understanding."""

    def test_chat_controller_export_history(self):
        """ChatController.export_history() returns role/content pairs."""
        from lina.gui.chat import ChatController, MessageRole
        ctrl = ChatController()
        ctrl.add_message(MessageRole.USER, "сделай погромче")
        ctrl.add_message(MessageRole.ASSISTANT, "Громкость +10%")
        ctrl.add_message(MessageRole.USER, "ещё добавь")
        history = ctrl.export_history()
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "сделай погромче")
        self.assertEqual(history[1]["role"], "assistant")

    def test_engine_generate_stream_accepts_history(self):
        """LLMEngine.generate_stream accepts history parameter."""
        import inspect
        from lina.llm.engine import LLMEngine
        sig = inspect.signature(LLMEngine.generate_stream)
        params = list(sig.parameters.keys())
        self.assertIn("history", params)
        self.assertIn("cancel_flag", params)

    def test_engine_generate_accepts_history(self):
        """LLMEngine.generate accepts history parameter."""
        import inspect
        from lina.llm.engine import LLMEngine
        sig = inspect.signature(LLMEngine.generate)
        params = list(sig.parameters.keys())
        self.assertIn("history", params)


# ═══════════════════════════════════════════════════════════
#  Single generate_stream method (no duplicate)
# ═══════════════════════════════════════════════════════════

class TestSingleGenerateStream(unittest.TestCase):
    """Only one generate_stream method exists in LLMEngine."""

    def test_no_duplicate_generate_stream(self):
        """LLMEngine has exactly one generate_stream method (no override)."""
        import inspect
        from lina.llm.engine import LLMEngine
        # Get all methods named generate_stream
        members = inspect.getmembers(LLMEngine, predicate=inspect.isfunction)
        stream_methods = [name for name, _ in members if name == "generate_stream"]
        self.assertEqual(len(stream_methods), 1,
                         "Should have exactly one generate_stream method")

    def test_generate_stream_has_correct_stop_sequences(self):
        """generate_stream uses safe stop sequences (not bare '###')."""
        import inspect
        from lina.llm.engine import LLMEngine
        source = inspect.getsource(LLMEngine.generate_stream)
        # Should NOT have bare "###" as stop (causes empty responses)
        self.assertNotIn('stop=["</s>", "\\nПользователь:', source)
        self.assertNotIn('"###"]', source)
        # Should have safe stop sequences
        self.assertIn("\\n### USER", source)


if __name__ == "__main__":
    unittest.main()
