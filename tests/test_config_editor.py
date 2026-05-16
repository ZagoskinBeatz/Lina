# -*- coding: utf-8 -*-
"""
Tests — ConfigEditor (Block C6).

Tests for safe config reading, editing, backup/restore, and security:
  1. Format detection (INI, key=value, JSON, YAML, TOML, plain)
  2. read_config / get_value / list_values / search_key
  3. suggest_change — diff preview
  4. apply_change — with backup + confirmed flag
  5. backup / restore
  6. Security: whitelist, blacklist, path validation
  7. Edge cases: empty, missing, permission denied

Phase: Block C / System Modules
"""

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════
#  Block A — Import & Dataclasses
# ═══════════════════════════════════════════════════════════

class TestConfigEditorImport(unittest.TestCase):
    """ConfigEditor imports and dataclass creation."""

    def test_01_import(self):
        """ConfigEditor imports without errors."""
        from lina.system.config_editor import (
            ConfigEditor, ConfigFormat, ConfigValue, ConfigDiff,
            ConfigEditResult, ConfigParseResult,
        )
        self.assertIsNotNone(ConfigEditor)

    def test_02_create_instance(self):
        """ConfigEditor creates without errors."""
        from lina.system.config_editor import ConfigEditor
        editor = ConfigEditor()
        self.assertIsNotNone(editor)

    def test_03_config_format_enum(self):
        """ConfigFormat has expected values."""
        from lina.system.config_editor import ConfigFormat
        self.assertEqual(ConfigFormat.INI.value, "ini")
        self.assertEqual(ConfigFormat.JSON.value, "json")
        self.assertEqual(ConfigFormat.YAML.value, "yaml")
        self.assertEqual(ConfigFormat.KEYVALUE.value, "keyvalue")
        self.assertEqual(ConfigFormat.PLAIN.value, "plain")

    def test_04_config_value_dataclass(self):
        """ConfigValue fields are correct."""
        from lina.system.config_editor import ConfigValue
        v = ConfigValue(key="vm.swappiness", value="10", line_number=5)
        self.assertEqual(v.key, "vm.swappiness")
        self.assertEqual(v.value, "10")
        self.assertEqual(v.line_number, 5)

    def test_05_config_diff_dataclass(self):
        """ConfigDiff fields are correct."""
        from lina.system.config_editor import ConfigDiff
        d = ConfigDiff(path="/etc/test", key="foo", old_value="1", new_value="2")
        self.assertTrue(d.safe)

    def test_06_config_edit_result_dataclass(self):
        """ConfigEditResult fields are correct."""
        from lina.system.config_editor import ConfigEditResult
        r = ConfigEditResult(success=True, path="/etc/test", message="ok")
        self.assertTrue(r.success)


# ═══════════════════════════════════════════════════════════
#  Block B — Format Detection
# ═══════════════════════════════════════════════════════════

class TestFormatDetection(unittest.TestCase):
    """_detect_format() correctly identifies config formats."""

    def test_07_detect_ini_by_extension(self):
        """Detects .ini files as INI."""
        from lina.system.config_editor import _detect_format, ConfigFormat
        with tempfile.NamedTemporaryFile(suffix=".ini", mode="w", delete=False) as f:
            f.write("[section]\nkey=val\n")
            f.flush()
            try:
                self.assertEqual(_detect_format(f.name), ConfigFormat.INI)
            finally:
                os.unlink(f.name)

    def test_08_detect_json_by_extension(self):
        """Detects .json files as JSON."""
        from lina.system.config_editor import _detect_format, ConfigFormat
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write('{"key": "val"}\n')
            f.flush()
            try:
                self.assertEqual(_detect_format(f.name), ConfigFormat.JSON)
            finally:
                os.unlink(f.name)

    def test_09_detect_yaml_by_extension(self):
        """Detects .yaml/.yml files as YAML."""
        from lina.system.config_editor import _detect_format, ConfigFormat
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("key: value\n")
            f.flush()
            try:
                self.assertEqual(_detect_format(f.name), ConfigFormat.YAML)
            finally:
                os.unlink(f.name)

    def test_10_detect_conf_as_ini(self):
        """Detects .conf files as INI."""
        from lina.system.config_editor import _detect_format, ConfigFormat
        with tempfile.NamedTemporaryFile(suffix=".conf", mode="w", delete=False) as f:
            f.write("[section]\nkey=val\n")
            f.flush()
            try:
                self.assertEqual(_detect_format(f.name), ConfigFormat.INI)
            finally:
                os.unlink(f.name)

    def test_11_detect_json_by_content(self):
        """Detects JSON by { content start."""
        from lina.system.config_editor import _detect_format, ConfigFormat
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write('{"enabled": true}\n')
            f.flush()
            try:
                self.assertEqual(_detect_format(f.name), ConfigFormat.JSON)
            finally:
                os.unlink(f.name)


# ═══════════════════════════════════════════════════════════
#  Block C — Reading Configs
# ═══════════════════════════════════════════════════════════

class TestReadConfig(unittest.TestCase):
    """ConfigEditor.read_config() parses different formats."""

    def _editor(self):
        from lina.system.config_editor import ConfigEditor
        return ConfigEditor()

    def _make_temp(self, content, suffix=".conf", dir_path=None):
        """Create temporary config file in allowed dir."""
        # Use ~/.config to be within ALLOWED_READ_DIRS
        if dir_path is None:
            dir_path = os.path.expanduser("~/.config/lina_test")
        os.makedirs(dir_path, exist_ok=True)
        path = os.path.join(dir_path, f"test{suffix}")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_12_read_ini_config(self):
        """Read INI config with sections."""
        ed = self._editor()
        path = self._make_temp("[options]\nColor\nCheckSpace\n\n[core]\nSigLevel = Required\n")
        try:
            result = ed.read_config(path)
            self.assertTrue(result.readable)
            self.assertIn("options", result.sections)
        finally:
            os.unlink(path)

    def test_13_read_keyvalue_config(self):
        """Read key=value config."""
        ed = self._editor()
        content = "vm.swappiness=10\nnet.ipv4.ip_forward=1\n"
        path = self._make_temp(content, suffix=".txt")
        try:
            result = ed.read_config(path)
            self.assertTrue(result.readable)
            self.assertTrue(len(result.values) >= 2)
        finally:
            os.unlink(path)

    def test_14_read_json_config(self):
        """Read JSON config."""
        ed = self._editor()
        data = {"server": {"port": 8080, "host": "localhost"}}
        path = self._make_temp(json.dumps(data, indent=2), suffix=".json")
        try:
            result = ed.read_config(path)
            self.assertTrue(result.readable)
            self.assertTrue(any(v.key == "server.port" for v in result.values))
        finally:
            os.unlink(path)

    def test_15_read_yaml_config(self):
        """Read YAML config."""
        ed = self._editor()
        content = "server:\n  port: 8080\n  host: localhost\n"
        path = self._make_temp(content, suffix=".yaml")
        try:
            result = ed.read_config(path)
            self.assertTrue(result.readable)
            self.assertTrue(len(result.values) >= 2)
        finally:
            os.unlink(path)

    def test_16_get_value(self):
        """get_value returns correct value."""
        ed = self._editor()
        content = "vm.swappiness=10\nvm.vfs_cache_pressure=50\n"
        path = self._make_temp(content, suffix=".txt")
        try:
            val = ed.get_value(path, "vm.swappiness")
            self.assertEqual(val, "10")
        finally:
            os.unlink(path)

    def test_17_get_value_missing_returns_none(self):
        """get_value returns None for missing key."""
        ed = self._editor()
        content = "key1=val1\n"
        path = self._make_temp(content, suffix=".txt")
        try:
            val = ed.get_value(path, "nonexistent")
            self.assertIsNone(val)
        finally:
            os.unlink(path)

    def test_18_list_values(self):
        """list_values returns all parsed values."""
        ed = self._editor()
        content = "a=1\nb=2\nc=3\n"
        path = self._make_temp(content, suffix=".txt")
        try:
            vals = ed.list_values(path)
            keys = [v.key for v in vals]
            self.assertIn("a", keys)
            self.assertIn("b", keys)
            self.assertIn("c", keys)
        finally:
            os.unlink(path)

    def test_19_search_key(self):
        """search_key finds keys matching regex."""
        ed = self._editor()
        content = "vm.swappiness=10\nvm.vfs_cache=50\nnet.ipv4=1\n"
        path = self._make_temp(content, suffix=".txt")
        try:
            results = ed.search_key(path, r"^vm\.")
            self.assertEqual(len(results), 2)
        finally:
            os.unlink(path)

    def test_20_list_sections_ini(self):
        """list_sections returns INI sections."""
        ed = self._editor()
        content = "[core]\nkey=val\n\n[extra]\nother=1\n"
        path = self._make_temp(content)
        try:
            sections = ed.list_sections(path)
            self.assertIn("core", sections)
            self.assertIn("extra", sections)
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════
#  Block D — Suggest Change (Diff Preview)
# ═══════════════════════════════════════════════════════════

class TestSuggestChange(unittest.TestCase):
    """suggest_change() generates diff without writing."""

    def _editor(self):
        from lina.system.config_editor import ConfigEditor
        return ConfigEditor()

    def _make_temp(self, content, suffix=".txt"):
        dir_path = os.path.expanduser("~/.config/lina_test")
        os.makedirs(dir_path, exist_ok=True)
        path = os.path.join(dir_path, f"test_suggest{suffix}")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_21_suggest_generates_diff(self):
        """suggest_change returns diff for key=value."""
        ed = self._editor()
        content = "vm.swappiness=60\nvm.vfs_cache_pressure=50\n"
        path = self._make_temp(content)
        try:
            diff = ed.suggest_change(path, "vm.swappiness", "10")
            self.assertTrue(diff.safe)
            self.assertEqual(diff.old_value, "60")
            self.assertEqual(diff.new_value, "10")
            self.assertIn("-", diff.unified_diff)
            self.assertIn("+", diff.unified_diff)
        finally:
            os.unlink(path)

    def test_22_suggest_does_not_modify(self):
        """suggest_change does NOT modify the file."""
        ed = self._editor()
        content = "key=old_value\nother=123\n"
        path = self._make_temp(content)
        try:
            ed.suggest_change(path, "key", "new_value")
            with open(path) as f:
                self.assertEqual(f.read(), content)
        finally:
            os.unlink(path)

    def test_23_suggest_blacklisted_blocked(self):
        """suggest_change blocks blacklisted files."""
        ed = self._editor()
        diff = ed.suggest_change("/etc/shadow", "key", "val")
        self.assertFalse(diff.safe)
        self.assertIn("чёрн", diff.reason.lower())


# ═══════════════════════════════════════════════════════════
#  Block E — Apply Change
# ═══════════════════════════════════════════════════════════

class TestApplyChange(unittest.TestCase):
    """apply_change() with backup and confirmation."""

    def _editor(self):
        from lina.system.config_editor import ConfigEditor
        return ConfigEditor()

    def _make_temp(self, content, suffix=".txt"):
        dir_path = os.path.expanduser("~/.config/lina_test")
        os.makedirs(dir_path, exist_ok=True)
        path = os.path.join(dir_path, f"test_apply{suffix}")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_24_apply_without_confirmation_returns_command(self):
        """apply_change without confirmed=True returns command (dry run)."""
        ed = self._editor()
        content = "key=old\nother=val\n"
        path = self._make_temp(content)
        try:
            result = ed.apply_change(path, "key", "new")
            self.assertFalse(result.success)
            self.assertIn("подтвержд", result.message.lower())
        finally:
            os.unlink(path)

    def test_25_apply_with_confirmation_writes(self):
        """apply_change with confirmed=True writes the file."""
        ed = self._editor()
        content = "key=old\nother=val\n"
        path = self._make_temp(content)
        try:
            result = ed.apply_change(path, "key", "new", confirmed=True)
            self.assertTrue(result.success)
            with open(path) as f:
                new_content = f.read()
            self.assertIn("key=new", new_content)
        finally:
            os.unlink(path)

    def test_26_apply_creates_backup(self):
        """apply_change creates backup before writing."""
        ed = self._editor()
        content = "key=old\nother=val\n"
        path = self._make_temp(content)
        try:
            result = ed.apply_change(path, "key", "new", confirmed=True)
            self.assertTrue(result.success)
            self.assertTrue(result.backup_path)
            self.assertTrue(os.path.isfile(result.backup_path))
            # Cleanup backup
            os.unlink(result.backup_path)
        finally:
            os.unlink(path)

    def test_27_apply_blacklisted_blocked(self):
        """apply_change blocks blacklisted files."""
        ed = self._editor()
        result = ed.apply_change("/etc/shadow", "key", "val", confirmed=True)
        self.assertFalse(result.success)
        self.assertIn("чёрн", result.message.lower())


# ═══════════════════════════════════════════════════════════
#  Block F — Backup / Restore
# ═══════════════════════════════════════════════════════════

class TestBackupRestore(unittest.TestCase):
    """backup() and restore() operations."""

    def _editor(self):
        from lina.system.config_editor import ConfigEditor
        return ConfigEditor()

    def _make_temp(self, content):
        dir_path = os.path.expanduser("~/.config/lina_test")
        os.makedirs(dir_path, exist_ok=True)
        path = os.path.join(dir_path, "test_backup.txt")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_28_backup_creates_file(self):
        """backup() creates a .lina-bak file."""
        ed = self._editor()
        path = self._make_temp("original content")
        try:
            backup = ed.backup(path)
            self.assertTrue(backup)
            self.assertTrue(os.path.isfile(backup))
            self.assertTrue(backup.endswith(".lina-bak"))
            # Verify content matches
            with open(backup) as f:
                self.assertEqual(f.read(), "original content")
            os.unlink(backup)
        finally:
            os.unlink(path)

    def test_29_backup_nonexistent_returns_empty(self):
        """backup() returns '' for nonexistent file."""
        ed = self._editor()
        result = ed.backup("/tmp/nonexistent_lina_test_12345.conf")
        self.assertEqual(result, "")

    def test_30_restore_works(self):
        """restore() reverts file to backup content."""
        ed = self._editor()
        path = self._make_temp("original")
        try:
            backup = ed.backup(path)
            # Modify the file
            with open(path, "w") as f:
                f.write("modified")
            # Restore
            result = ed.restore(path, backup)
            self.assertTrue(result.success)
            with open(path) as f:
                self.assertEqual(f.read(), "original")
            os.unlink(backup)
        finally:
            os.unlink(path)

    def test_31_list_backups(self):
        """list_backups() shows existing backups."""
        ed = self._editor()
        path = self._make_temp("content")
        try:
            backup = ed.backup(path)
            backups = ed.list_backups(path)
            self.assertTrue(len(backups) >= 1)
            self.assertTrue(any(b["path"] == backup for b in backups))
            os.unlink(backup)
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════
#  Block G — Security: Whitelist / Blacklist
# ═══════════════════════════════════════════════════════════

class TestSecurity(unittest.TestCase):
    """Security: path validation, whitelist, blacklist."""

    def test_32_read_outside_whitelist_blocked(self):
        """read_config blocks paths outside ALLOWED_READ_DIRS."""
        from lina.system.config_editor import ConfigEditor
        ed = ConfigEditor()
        result = ed.read_config("/opt/secret/config.ini")
        self.assertFalse(result.readable)
        self.assertIn("вне разреш", result.error.lower())

    def test_33_blacklisted_shadow(self):
        """Blacklist: /etc/shadow is blocked for writing."""
        from lina.system.config_editor import _is_blacklisted
        self.assertTrue(_is_blacklisted("/etc/shadow"))

    def test_34_blacklisted_passwd(self):
        """Blacklist: /etc/passwd is blocked for writing."""
        from lina.system.config_editor import _is_blacklisted
        self.assertTrue(_is_blacklisted("/etc/passwd"))

    def test_35_blacklisted_sudoers(self):
        """Blacklist: /etc/sudoers is blocked for writing."""
        from lina.system.config_editor import _is_blacklisted
        self.assertTrue(_is_blacklisted("/etc/sudoers"))

    def test_36_not_blacklisted_pacman(self):
        """Non-blacklisted: /etc/pacman.conf is allowed."""
        from lina.system.config_editor import _is_blacklisted
        self.assertFalse(_is_blacklisted("/etc/pacman.conf"))

    def test_37_allowed_read_etc(self):
        """Whitelist: /etc/ is allowed for reading."""
        from lina.system.config_editor import _is_path_allowed, ALLOWED_READ_DIRS
        self.assertTrue(_is_path_allowed("/etc/pacman.conf", ALLOWED_READ_DIRS))

    def test_38_allowed_read_home_config(self):
        """Whitelist: ~/.config/ is allowed for reading."""
        from lina.system.config_editor import _is_path_allowed, ALLOWED_READ_DIRS
        home_conf = os.path.expanduser("~/.config/test.ini")
        self.assertTrue(_is_path_allowed(home_conf, ALLOWED_READ_DIRS))

    def test_39_not_allowed_read_random_dir(self):
        """Not allowed: /var/secret is not in read whitelist."""
        from lina.system.config_editor import _is_path_allowed, ALLOWED_READ_DIRS
        self.assertFalse(_is_path_allowed("/var/secret/data", ALLOWED_READ_DIRS))


# ═══════════════════════════════════════════════════════════
#  Block H — Content Modification Helpers
# ═══════════════════════════════════════════════════════════

class TestContentModification(unittest.TestCase):
    """Internal helpers for content modification."""

    def _editor(self):
        from lina.system.config_editor import ConfigEditor
        return ConfigEditor()

    def test_40_modify_keyvalue(self):
        """_modify_keyvalue replaces value correctly."""
        ed = self._editor()
        content = "a=1\nb=2\nc=3\n"
        result = ed._modify_keyvalue(content, "b", "99")
        self.assertIn("b=99", result)
        self.assertIn("a=1", result)
        self.assertIn("c=3", result)

    def test_41_modify_keyvalue_adds_missing(self):
        """_modify_keyvalue adds key if not found."""
        ed = self._editor()
        content = "a=1\n"
        result = ed._modify_keyvalue(content, "new_key", "val")
        self.assertIn("new_key=val", result)

    def test_42_modify_json(self):
        """_modify_json changes nested JSON value."""
        ed = self._editor()
        content = json.dumps({"server": {"port": 8080}}, indent=2)
        result = ed._modify_json(content, "server.port", "9090")
        data = json.loads(result)
        self.assertEqual(data["server"]["port"], 9090)

    def test_43_modify_ini_section(self):
        """_modify_ini changes value in specific section."""
        ed = self._editor()
        content = "[core]\nSigLevel = Required\n\n[extra]\nSigLevel = Optional\n"
        result = ed._modify_ini(content, "SigLevel", "Never", section="extra")
        # extra section should be changed
        self.assertIn("SigLevel = Never", result)
        # core section should remain
        self.assertIn("SigLevel = Required", result)

    def test_44_type_value_bool(self):
        """_type_value converts boolean strings."""
        from lina.system.config_editor import ConfigEditor
        self.assertTrue(ConfigEditor._type_value("true"))
        self.assertFalse(ConfigEditor._type_value("false"))

    def test_45_type_value_int(self):
        """_type_value converts integer strings."""
        from lina.system.config_editor import ConfigEditor
        self.assertEqual(ConfigEditor._type_value("42"), 42)

    def test_46_type_value_string(self):
        """_type_value keeps non-numeric strings as str."""
        from lina.system.config_editor import ConfigEditor
        self.assertEqual(ConfigEditor._type_value("hello"), "hello")


# ═══════════════════════════════════════════════════════════
#  Block I — describe_config / format_diff_for_user
# ═══════════════════════════════════════════════════════════

class TestLLMHelpers(unittest.TestCase):
    """describe_config() and format_diff_for_user() for LLM output."""

    def _editor(self):
        from lina.system.config_editor import ConfigEditor
        return ConfigEditor()

    def _make_temp(self, content, suffix=".txt"):
        dir_path = os.path.expanduser("~/.config/lina_test")
        os.makedirs(dir_path, exist_ok=True)
        path = os.path.join(dir_path, f"test_describe{suffix}")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_47_describe_config(self):
        """describe_config returns human-readable text."""
        ed = self._editor()
        content = "a=1\nb=2\n"
        path = self._make_temp(content)
        try:
            desc = ed.describe_config(path)
            self.assertIn("Конфигурация", desc)
            self.assertIn("Параметров", desc)
        finally:
            os.unlink(path)

    def test_48_format_diff_for_user_safe(self):
        """format_diff_for_user shows diff for safe change."""
        from lina.system.config_editor import ConfigDiff
        ed = self._editor()
        diff = ConfigDiff(
            path="/etc/test.conf",
            key="vm.swappiness",
            old_value="60",
            new_value="10",
            unified_diff="--- a\n+++ b\n-60\n+10",
            safe=True,
        )
        text = ed.format_diff_for_user(diff)
        self.assertIn("vm.swappiness", text)
        self.assertIn("60", text)
        self.assertIn("10", text)

    def test_49_format_diff_blocked(self):
        """format_diff_for_user shows warning for blocked change."""
        from lina.system.config_editor import ConfigDiff
        ed = self._editor()
        diff = ConfigDiff(safe=False, reason="Blacklisted file")
        text = ed.format_diff_for_user(diff)
        self.assertIn("⚠️", text)

    def test_50_needs_sudo_detection(self):
        """_needs_sudo correctly identifies privileged paths."""
        from lina.system.config_editor import _needs_sudo
        # /etc files typically need sudo
        if os.path.exists("/etc/hostname"):
            self.assertTrue(_needs_sudo("/etc/hostname"))
        # User's own files should not need sudo
        tmp = os.path.expanduser("~/.config/lina_test/testfile")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        Path(tmp).write_text("test")
        try:
            self.assertFalse(_needs_sudo(tmp))
        finally:
            os.unlink(tmp)


# ═══════════════════════════════════════════════════════════
#  Cleanup
# ═══════════════════════════════════════════════════════════

def tearDownModule():
    """Cleanup test temp directory."""
    import shutil
    test_dir = os.path.expanduser("~/.config/lina_test")
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
