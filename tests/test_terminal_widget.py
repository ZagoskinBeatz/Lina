"""
Tests — Embedded Terminal & Command Action Bar (install pipeline).

Tests:
  1. extract_executable_commands — regex parsing
  2. CommandActionBar — widget creation
  3. EmbeddedTerminal — widget creation
  4. Pipeline: install_app → output → extract → bar
"""

import unittest
from unittest.mock import patch, MagicMock


class TestExtractCommands(unittest.TestCase):
    """extract_executable_commands correctly parses bot responses."""

    def _extract(self, text):
        from lina.gui.terminal_widget import extract_executable_commands
        return extract_executable_commands(text)

    def test_pacman_command(self):
        """Extracts pacman install command."""
        text = """📦 Варианты установки «firefox»:

  1. [PACMAN] sudo pacman -S firefox (extra)
  2. [FLATPAK] flatpak install flathub org.mozilla.firefox
  3. [WEB] 🌐 https://duckduckgo.com/?q=firefox"""
        cmds = self._extract(text)
        self.assertIn("sudo pacman -S firefox", cmds)
        self.assertIn("flatpak install flathub org.mozilla.firefox", cmds)
        # WEB entry should NOT be extracted (no package manager command)
        for cmd in cmds:
            self.assertNotIn("duckduckgo", cmd)

    def test_apt_command(self):
        """Extracts apt install command."""
        text = "  1. [APT] sudo apt install vim (universe)"
        cmds = self._extract(text)
        self.assertEqual(cmds, ["sudo apt install vim"])

    def test_dnf_command(self):
        """Extracts dnf install command."""
        text = "  1. [DNF] sudo dnf install htop"
        cmds = self._extract(text)
        self.assertEqual(cmds, ["sudo dnf install htop"])

    def test_yay_aur_command(self):
        """Extracts AUR helper commands."""
        text = "  1. [YAY] yay -S google-chrome"
        cmds = self._extract(text)
        self.assertEqual(cmds, ["yay -S google-chrome"])

    def test_no_commands(self):
        """Returns empty list for non-install text."""
        text = "Привет! Как дела?"
        cmds = self._extract(text)
        self.assertEqual(cmds, [])

    def test_command_with_note(self):
        """Strips trailing notes."""
        text = "  1. [PACMAN] sudo pacman -S firefox (extra) — стабильная версия"
        cmds = self._extract(text)
        # Should strip both (extra) and the note
        self.assertEqual(cmds, ["sudo pacman -S firefox"])

    def test_flatpak_command(self):
        """Extracts flatpak install command."""
        text = "  2. [FLATPAK] flatpak install flathub com.spotify.Client"
        cmds = self._extract(text)
        self.assertEqual(cmds, ["flatpak install flathub com.spotify.Client"])

    def test_snap_command(self):
        """Extracts snap install command."""
        text = "  1. [SNAP] snap install discord"
        cmds = self._extract(text)
        self.assertEqual(cmds, ["snap install discord"])

    def test_multiple_commands(self):
        """Extracts multiple commands from response."""
        text = """  1. [PACMAN] sudo pacman -S vlc (extra)
  2. [FLATPAK] flatpak install flathub org.videolan.VLC"""
        cmds = self._extract(text)
        self.assertEqual(len(cmds), 2)
        self.assertEqual(cmds[0], "sudo pacman -S vlc")
        self.assertEqual(cmds[1], "flatpak install flathub org.videolan.VLC")


class TestCommandActionBar(unittest.TestCase):
    """CommandActionBar widget tests."""

    def test_class_creation(self):
        """CommandActionBar class created via factory."""
        try:
            from lina.gui.terminal_widget import create_command_action_bar_class
            CmdBar = create_command_action_bar_class()
            self.assertIsNotNone(CmdBar)
            self.assertTrue(callable(CmdBar))
        except ImportError:
            self.skipTest("Qt not available")

    def test_has_signals(self):
        """CommandActionBar has execute_requested and dismissed signals."""
        try:
            from lina.gui.terminal_widget import create_command_action_bar_class
            CmdBar = create_command_action_bar_class()
            self.assertTrue(hasattr(CmdBar, 'execute_requested'))
            self.assertTrue(hasattr(CmdBar, 'dismissed'))
        except ImportError:
            self.skipTest("Qt not available")


class TestEmbeddedTerminal(unittest.TestCase):
    """EmbeddedTerminal widget tests."""

    def test_class_creation(self):
        """EmbeddedTerminal class created via factory."""
        try:
            from lina.gui.terminal_widget import create_embedded_terminal_class
            Terminal = create_embedded_terminal_class()
            self.assertIsNotNone(Terminal)
            self.assertTrue(callable(Terminal))
        except ImportError:
            self.skipTest("Qt not available")

    def test_has_signals(self):
        """EmbeddedTerminal has command_finished and command_started signals."""
        try:
            from lina.gui.terminal_widget import create_embedded_terminal_class
            Terminal = create_embedded_terminal_class()
            self.assertTrue(hasattr(Terminal, 'command_finished'))
            self.assertTrue(hasattr(Terminal, 'command_started'))
        except ImportError:
            self.skipTest("Qt not available")


class TestANSIStripping(unittest.TestCase):
    """ANSI escape code stripping."""

    def test_strips_color_codes(self):
        from lina.gui.terminal_widget import _ANSI_RE
        text = "\x1b[32mSuccess\x1b[0m"
        clean = _ANSI_RE.sub("", text)
        self.assertEqual(clean, "Success")

    def test_strips_cursor_movement(self):
        from lina.gui.terminal_widget import _ANSI_RE
        text = "\x1b[2Ahello\x1b[K"
        clean = _ANSI_RE.sub("", text)
        self.assertEqual(clean, "hello")

    def test_strips_carriage_return(self):
        from lina.gui.terminal_widget import _ANSI_RE
        text = "line\r\ntest"
        clean = _ANSI_RE.sub("", text)
        self.assertEqual(clean, "line\ntest")

    def test_preserves_plain_text(self):
        from lina.gui.terminal_widget import _ANSI_RE
        text = "Normal log output: all good"
        clean = _ANSI_RE.sub("", text)
        self.assertEqual(clean, text)


class TestInstallToolOutput(unittest.TestCase):
    """_tool_install_app now uses method as label (not source)."""

    def test_output_format_has_method(self):
        """Verify [METHOD] format in output is extractable."""
        from lina.gui.terminal_widget import extract_executable_commands
        # Simulate the new output format: [METHOD] command (source)
        formatted = (
            "📦 Варианты установки «firefox»:\n\n"
            "  1. [PACMAN] sudo pacman -S firefox (extra)\n"
            "  2. [FLATPAK] flatpak install flathub org.mozilla.firefox\n"
            "  3. [WEB] 🌐 https://duckduckgo.com/?q=firefox"
        )
        cmds = extract_executable_commands(formatted)
        self.assertEqual(len(cmds), 2)
        self.assertEqual(cmds[0], "sudo pacman -S firefox")
        self.assertIn("flatpak install", cmds[1])


class TestMainWindowTerminalWiring(unittest.TestCase):
    """main_window has terminal widgets wired."""

    def test_imports_work(self):
        """terminal_widget module imports clean."""
        from lina.gui.terminal_widget import (
            extract_executable_commands,
            create_command_action_bar_class,
            create_embedded_terminal_class,
            _ANSI_RE,
            _INSTALL_CMD_RE,
        )


if __name__ == "__main__":
    unittest.main()
