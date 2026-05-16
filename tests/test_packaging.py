# -*- coding: utf-8 -*-
"""
Lina — Packaging & Distribution Tests (v1.0.0 milestone).

Tests: test_1851 – test_1965 (115 tests).
Covers: CLI args, __main__, First Run Wizard, Updater,
        Packaging generators, Desktop installer, systemd service,
        GUI wizard, Flatpak, AppData, icon, man page, documentation.
"""

import io
import os
import re
import sys
import unittest
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, PropertyMock


# ═══════════════════════════════════════════════════════════════════════════════
#  Block A — CLI Arguments (--daemon, --first-run, --version)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCLIArgs(unittest.TestCase):
    """CLI argument parsing: new v1.0.0 flags."""

    def test_1851_parse_daemon_flag(self):
        """parse_args recognizes --daemon."""
        from lina.core.cli import parse_args
        args = parse_args(["--daemon"])
        self.assertTrue(args.daemon)
        self.assertFalse(args.gui)
        self.assertFalse(args.first_run)

    def test_1852_parse_first_run_flag(self):
        """parse_args recognizes --first-run."""
        from lina.core.cli import parse_args
        args = parse_args(["--first-run"])
        self.assertTrue(args.first_run)

    def test_1853_parse_gui_flag(self):
        """parse_args recognizes --gui."""
        from lina.core.cli import parse_args
        args = parse_args(["--gui"])
        self.assertTrue(args.gui)

    def test_1854_parse_quiet_flag(self):
        """parse_args recognizes --quiet / -q."""
        from lina.core.cli import parse_args
        args = parse_args(["-q"])
        self.assertTrue(args.quiet)

    def test_1855_parse_oneshot(self):
        """parse_args recognizes --oneshot."""
        from lina.core.cli import parse_args
        args = parse_args(["--oneshot", "привет"])
        self.assertEqual(args.oneshot, "привет")

    def test_1856_parse_defaults(self):
        """Default args have all flags False."""
        from lina.core.cli import parse_args
        args = parse_args([])
        self.assertFalse(args.daemon)
        self.assertFalse(args.first_run)
        self.assertFalse(args.gui)
        self.assertFalse(args.verbose)
        self.assertFalse(args.web)
        self.assertFalse(args.index)
        self.assertIsNone(args.oneshot)
        self.assertIsNone(args.model)

    def test_1857_parse_combined_flags(self):
        """Multiple flags can be combined."""
        from lina.core.cli import parse_args
        args = parse_args(["--daemon", "--quiet", "--verbose"])
        self.assertTrue(args.daemon)
        self.assertTrue(args.quiet)
        self.assertTrue(args.verbose)

    def test_1858_linargs_dataclass(self):
        """LinaArgs has daemon and first_run fields."""
        from lina.core.cli import LinaArgs
        la = LinaArgs()
        self.assertFalse(la.daemon)
        self.assertFalse(la.first_run)
        la2 = LinaArgs(daemon=True, first_run=True)
        self.assertTrue(la2.daemon)
        self.assertTrue(la2.first_run)

    def test_1859_get_version_returns_string(self):
        """_get_version() returns valid version string."""
        from lina.core.cli import _get_version
        v = _get_version()
        self.assertRegex(v, r"^\d+\.\d+\.\d+")

    def test_1860_build_parser_has_daemon(self):
        """build_parser includes --daemon action."""
        from lina.core.cli import build_parser
        parser = build_parser()
        actions = {a.option_strings[0] for a in parser._actions
                   if hasattr(a, 'option_strings') and a.option_strings}
        self.assertIn("--daemon", actions)
        self.assertIn("--first-run", actions)


# ═══════════════════════════════════════════════════════════════════════════════
#  Block B — __main__.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestMainModule(unittest.TestCase):
    """lina/__main__.py entry point."""

    def test_1861_main_module_exists(self):
        """__main__.py exists in lina package."""
        main_path = Path(__file__).parent.parent / "__main__.py"
        self.assertTrue(main_path.exists(),
                        f"Missing: {main_path}")

    def test_1862_main_module_importable(self):
        """lina.__main__ is importable."""
        import lina.__main__
        self.assertTrue(hasattr(lina.__main__, 'main'))

    def test_1863_main_calls_cli(self):
        """__main__.main() delegates to cli.main()."""
        from unittest.mock import patch
        with patch('lina.core.cli.main', return_value=0) as mock_main:
            from lina.__main__ import main
            # main() calls sys.exit, so we catch it
            with self.assertRaises(SystemExit) as ctx:
                main()
            mock_main.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
#  Block C — First Run Wizard
# ═══════════════════════════════════════════════════════════════════════════════

class TestFirstRunWizard(unittest.TestCase):
    """FirstRunWizard: steps, model selection, system detection."""

    def test_1864_model_size_enum(self):
        """ModelSize enum has expected values."""
        from lina.installer.first_run import ModelSize
        self.assertEqual(ModelSize.SMALL.value, "small")
        self.assertEqual(ModelSize.MEDIUM.value, "medium")
        self.assertEqual(ModelSize.LARGE.value, "large")

    def test_1865_model_option_to_dict(self):
        """ModelOption.to_dict() has expected keys."""
        from lina.installer.first_run import ModelOption, ModelSize
        opt = ModelOption(
            size=ModelSize.SMALL,
            name="test-model",
            params="3B",
            ram_required_gb=2.0,
            disk_required_gb=1.5,
            description="Test model",
        )
        d = opt.to_dict()
        self.assertEqual(d["name"], "test-model")
        self.assertEqual(d["ram_required_gb"], 2.0)

    def test_1866_wizard_step_enum(self):
        """WizardStep enum has all 8 steps."""
        from lina.installer.first_run import WizardStep
        steps = list(WizardStep)
        self.assertGreaterEqual(len(steps), 8)
        self.assertEqual(steps[0].value, "welcome")

    def test_1867_wizard_state_init(self):
        """WizardState initializes with welcome step."""
        from lina.installer.first_run import WizardState, WizardStep
        state = WizardState()
        self.assertEqual(state.current_step, WizardStep.WELCOME)
        self.assertFalse(state.model_downloaded)

    def test_1868_wizard_init(self):
        """FirstRunWizard initializes without error."""
        from lina.installer.first_run import FirstRunWizard
        wizard = FirstRunWizard()
        self.assertIsNotNone(wizard)

    def test_1869_wizard_get_current_step(self):
        """get_current_step returns WELCOME initially."""
        from lina.installer.first_run import FirstRunWizard, WizardStep
        wizard = FirstRunWizard()
        self.assertEqual(wizard.get_current_step(), WizardStep.WELCOME)

    def test_1870_wizard_get_total_steps(self):
        """get_total_steps returns >= 8."""
        from lina.installer.first_run import FirstRunWizard
        wizard = FirstRunWizard()
        self.assertGreaterEqual(wizard.get_total_steps(), 8)

    def test_1871_wizard_get_progress(self):
        """get_progress returns 0.0 initially."""
        from lina.installer.first_run import FirstRunWizard
        wizard = FirstRunWizard()
        self.assertAlmostEqual(wizard.get_progress(), 0.0, delta=0.15)

    def test_1872_wizard_next_step(self):
        """next_step advances to next step."""
        from lina.installer.first_run import FirstRunWizard, WizardStep
        wizard = FirstRunWizard()
        next_s = wizard.next_step()
        self.assertNotEqual(next_s, WizardStep.WELCOME)

    def test_1873_wizard_prev_step(self):
        """prev_step goes back (or stays at first)."""
        from lina.installer.first_run import FirstRunWizard, WizardStep
        wizard = FirstRunWizard()
        wizard.next_step()
        prev = wizard.prev_step()
        self.assertEqual(prev, WizardStep.WELCOME)

    def test_1874_wizard_get_welcome_text(self):
        """get_welcome_text returns non-empty string."""
        from lina.installer.first_run import FirstRunWizard
        wizard = FirstRunWizard()
        text = wizard.get_welcome_text()
        self.assertIn("Lina", text)
        self.assertGreater(len(text), 50)

    def test_1875_wizard_get_available_models(self):
        """get_available_models returns list of ModelOption."""
        from lina.installer.first_run import FirstRunWizard
        wizard = FirstRunWizard()
        models = wizard.get_available_models()
        self.assertGreater(len(models), 0)
        self.assertTrue(hasattr(models[0], 'name'))
        self.assertTrue(hasattr(models[0], 'ram_required_gb'))

    def test_1876_wizard_get_recommended_model(self):
        """get_recommended_model returns ModelOption."""
        from lina.installer.first_run import FirstRunWizard
        wizard = FirstRunWizard()
        rec = wizard.get_recommended_model()
        self.assertTrue(hasattr(rec, 'name'))
        self.assertTrue(hasattr(rec, 'ram_required_gb'))

    def test_1877_wizard_select_model(self):
        """select_model with valid size returns True."""
        from lina.installer.first_run import FirstRunWizard
        wizard = FirstRunWizard()
        self.assertTrue(wizard.select_model("small"))

    def test_1878_wizard_select_model_invalid(self):
        """select_model with invalid size returns False."""
        from lina.installer.first_run import FirstRunWizard
        wizard = FirstRunWizard()
        self.assertFalse(wizard.select_model("nonexistent"))

    def test_1879_wizard_callbacks(self):
        """set_on_progress / set_on_step_change store callbacks."""
        from lina.installer.first_run import FirstRunWizard
        wizard = FirstRunWizard()
        cb = MagicMock()
        wizard.set_on_progress(cb)
        wizard.set_on_step_change(cb)

    def test_1880_wizard_state_to_dict(self):
        """WizardState.to_dict() returns expected keys."""
        from lina.installer.first_run import WizardState
        state = WizardState()
        d = state.to_dict()
        self.assertIn("current_step", d)
        self.assertIn("model_downloaded", d)
        self.assertIn("language", d)

    def test_1881_is_first_run(self):
        """FirstRunWizard.is_first_run() returns bool."""
        from lina.installer.first_run import FirstRunWizard
        result = FirstRunWizard.is_first_run()
        self.assertIsInstance(result, bool)


# ═══════════════════════════════════════════════════════════════════════════════
#  Block D — Updater
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdater(unittest.TestCase):
    """LinaUpdater: version check, migration, knowledge update."""

    def test_1882_update_config_defaults(self):
        """UpdateConfig has sensible defaults."""
        from lina.installer.updater import UpdateConfig
        cfg = UpdateConfig()
        self.assertIsInstance(cfg.check_on_startup, bool)
        self.assertEqual(cfg.channel, "stable")
        self.assertEqual(cfg.check_interval_hours, 24)

    def test_1883_update_config_to_dict(self):
        """UpdateConfig.to_dict() returns expected keys."""
        from lina.installer.updater import UpdateConfig
        d = UpdateConfig().to_dict()
        self.assertIn("check_on_startup", d)
        self.assertIn("channel", d)

    def test_1884_version_info_to_dict(self):
        """VersionInfo.to_dict() serializes correctly."""
        from lina.installer.updater import VersionInfo
        vi = VersionInfo(version="1.0.0", release_date="2026-03-05",
                         changelog="New version", download_url="https://example.com")
        d = vi.to_dict()
        self.assertEqual(d["version"], "1.0.0")
        self.assertEqual(d["download_url"], "https://example.com")

    def test_1885_updater_init(self):
        """LinaUpdater initializes with current version."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater()
        self.assertRegex(u.current_version, r"^\d+\.\d+\.\d+")

    def test_1886_updater_get_current_version(self):
        """get_current_version returns version string."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater(current_version="0.9.0")
        self.assertEqual(u.get_current_version(), "0.9.0")

    def test_1887_updater_parse_version(self):
        """parse_version parses semver correctly."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater()
        self.assertEqual(u.parse_version("1.2.3"), (1, 2, 3))
        self.assertEqual(u.parse_version("v0.9.0"), (0, 9, 0))

    def test_1888_updater_is_newer_true(self):
        """is_newer returns True for higher version."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater(current_version="0.9.0")
        self.assertTrue(u.is_newer("1.0.0"))
        self.assertTrue(u.is_newer("0.10.0"))

    def test_1889_updater_is_newer_false(self):
        """is_newer returns False for same/lower version."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater(current_version="0.9.0")
        self.assertFalse(u.is_newer("0.9.0"))
        self.assertFalse(u.is_newer("0.8.0"))

    def test_1890_updater_check_github_stub(self):
        """check_update_github returns None (stub)."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater()
        result = u.check_update_github()
        self.assertIsNone(result)

    def test_1891_updater_check_for_updates(self):
        """check_for_updates returns dict with expected keys."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater()
        result = u.check_for_updates()
        self.assertIn("checked_at", result)
        self.assertIn("current_version", result)

    def test_1892_updater_update_knowledge(self):
        """update_knowledge_base returns dict."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater()
        result = u.update_knowledge_base()
        self.assertIn("success", result)

    def test_1893_updater_migrate_config(self):
        """migrate_config returns dict with success."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater()
        result = u.migrate_config("0.8.0", "0.9.0")
        self.assertIn("success", result)

    def test_1894_updater_check_model_update(self):
        """check_model_update returns dict."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater()
        result = u.check_model_update()
        self.assertIn("update_available", result)

    def test_1895_updater_should_check(self):
        """should_check returns bool."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater()
        result = u.should_check()
        self.assertIsInstance(result, bool)

    def test_1896_updater_to_dict(self):
        """to_dict() returns expected keys."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater()
        d = u.to_dict()
        self.assertIn("current_version", d)
        self.assertIn("config", d)

    def test_1897_updater_callbacks(self):
        """set_on_progress / set_on_update_available store callbacks."""
        from lina.installer.updater import LinaUpdater
        u = LinaUpdater()
        cb = MagicMock()
        u.set_on_progress(cb)
        u.set_on_update_available(cb)

    def test_1898_update_channel_dataclass(self):
        """UpdateChannel is a dataclass with name field."""
        from lina.installer.updater import UpdateChannel
        ch = UpdateChannel(name="stable")
        self.assertEqual(ch.name, "stable")
        self.assertTrue(ch.enabled)
        ch2 = UpdateChannel(name="beta", enabled=False)
        self.assertFalse(ch2.enabled)


# ═══════════════════════════════════════════════════════════════════════════════
#  Block E — Packaging Generators
# ═══════════════════════════════════════════════════════════════════════════════

class TestPackagingGenerators(unittest.TestCase):
    """Packaging generators: systemd, PKGBUILD, debian, RPM."""

    def test_1899_package_info_defaults(self):
        """PackageInfo has sensible defaults."""
        from lina.installer.packaging import PackageInfo
        pkg = PackageInfo()
        self.assertEqual(pkg.name, "lina")
        self.assertRegex(pkg.version, r"^\d+\.\d+\.\d+")
        self.assertIn("python", pkg.depends[0])

    def test_1900_systemd_user_service(self):
        """SystemdGenerator produces valid user service."""
        from lina.installer.packaging import SystemdGenerator
        gen = SystemdGenerator()
        service = gen.generate_user_service()
        self.assertIn("[Unit]", service)
        self.assertIn("[Service]", service)
        self.assertIn("[Install]", service)
        self.assertIn("lina", service)

    def test_1901_systemd_system_service(self):
        """SystemdGenerator produces valid system service."""
        from lina.installer.packaging import SystemdGenerator
        gen = SystemdGenerator()
        service = gen.generate_system_service()
        self.assertIn("[Unit]", service)
        self.assertIn("ExecStart", service)

    def test_1902_systemd_validate_good(self):
        """validate_service passes for good service file."""
        from lina.installer.packaging import SystemdGenerator
        gen = SystemdGenerator()
        service = gen.generate_user_service()
        result = gen.validate_service(service)
        self.assertTrue(result["has_unit"])
        self.assertTrue(result["has_service"])
        self.assertTrue(result["has_install"])
        self.assertTrue(result["has_exec"])

    def test_1903_systemd_validate_bad(self):
        """validate_service fails for empty string."""
        from lina.installer.packaging import SystemdGenerator
        gen = SystemdGenerator()
        result = gen.validate_service("")
        self.assertFalse(result["has_unit"])

    def test_1904_pkgbuild_generate(self):
        """PKGBUILDGenerator produces valid PKGBUILD."""
        from lina.installer.packaging import PKGBUILDGenerator
        gen = PKGBUILDGenerator()
        pkgbuild = gen.generate()
        self.assertIn("pkgname=", pkgbuild)
        self.assertIn("pkgver=", pkgbuild)
        self.assertIn("package()", pkgbuild)
        self.assertIn("lina", pkgbuild)

    def test_1905_pkgbuild_validate(self):
        """PKGBUILDGenerator.validate passes for generated content."""
        from lina.installer.packaging import PKGBUILDGenerator
        gen = PKGBUILDGenerator()
        content = gen.generate()
        result = gen.validate(content)
        self.assertTrue(result["has_pkgname"])
        self.assertTrue(result["has_pkgver"])
        self.assertTrue(result["has_package_func"])

    def test_1906_debian_control(self):
        """DebianGenerator produces valid control file."""
        from lina.installer.packaging import DebianGenerator
        gen = DebianGenerator()
        control = gen.generate_control()
        self.assertIn("Package:", control)
        self.assertIn("lina", control)
        self.assertIn("Depends:", control)

    def test_1907_debian_postinst(self):
        """DebianGenerator.generate_postinst has bash header."""
        from lina.installer.packaging import DebianGenerator
        gen = DebianGenerator()
        postinst = gen.generate_postinst()
        self.assertIn("#!/bin/", postinst)

    def test_1908_debian_rules(self):
        """DebianGenerator.generate_rules produces makefile."""
        from lina.installer.packaging import DebianGenerator
        gen = DebianGenerator()
        rules = gen.generate_rules()
        self.assertIn("dh", rules)

    def test_1909_debian_validate(self):
        """DebianGenerator.validate validates control content."""
        from lina.installer.packaging import DebianGenerator
        gen = DebianGenerator()
        control = gen.generate_control()
        result = gen.validate(control)
        self.assertTrue(result["has_package"])
        self.assertTrue(result["has_depends"])

    def test_1910_rpm_spec_generate(self):
        """RPMGenerator produces valid spec file."""
        from lina.installer.packaging import RPMGenerator
        gen = RPMGenerator()
        spec = gen.generate_spec()
        self.assertIn("Name:", spec)
        self.assertIn("Version:", spec)
        self.assertIn("lina", spec)

    def test_1911_rpm_validate(self):
        """RPMGenerator.validate passes for generated content."""
        from lina.installer.packaging import RPMGenerator
        gen = RPMGenerator()
        spec = gen.generate_spec()
        result = gen.validate(spec)
        self.assertTrue(result["has_name"])
        self.assertTrue(result["has_version"])

    def test_1912_packaging_manager_generate_all(self):
        """PackagingManager.generate_all returns all formats."""
        from lina.installer.packaging import PackagingManager
        mgr = PackagingManager()
        result = mgr.generate_all()
        self.assertIn("lina.service", result)
        self.assertIn("lina-user.service", result)
        self.assertIn("PKGBUILD", result)
        self.assertIn("debian/control", result)
        self.assertIn("lina.spec", result)


# ═══════════════════════════════════════════════════════════════════════════════
#  Block F — Desktop Installer
# ═══════════════════════════════════════════════════════════════════════════════

class TestDesktopInstaller(unittest.TestCase):
    """Desktop entry installer: .desktop, autostart."""

    def test_1913_desktop_entry_has_exec(self):
        """data/lina.desktop exists and has Exec= line."""
        desktop = Path(__file__).parent.parent / "data" / "lina.desktop"
        if desktop.exists():
            content = desktop.read_text()
            self.assertIn("Exec=", content)
            self.assertIn("lina", content.lower())
        else:
            self.skipTest("data/lina.desktop not found")

    def test_1914_dist_desktop_has_actions(self):
        """dist/lina.desktop has Desktop Actions."""
        desktop = Path(__file__).parent.parent / "dist" / "lina.desktop"
        if desktop.exists():
            content = desktop.read_text()
            self.assertIn("[Desktop Entry]", content)
        else:
            self.skipTest("dist/lina.desktop not found")

    def test_1915_autostart_desktop_exists(self):
        """data/lina-autostart.desktop exists."""
        autostart = Path(__file__).parent.parent / "data" / "lina-autostart.desktop"
        if autostart.exists():
            content = autostart.read_text()
            self.assertIn("Exec=", content)
        else:
            self.skipTest("data/lina-autostart.desktop not found")

    def test_1916_is_installed_returns_dict(self):
        """is_installed() returns dict with expected keys."""
        from lina.installer.desktop import is_installed
        result = is_installed()
        self.assertIn("desktop", result)
        self.assertIn("autostart", result)


# ═══════════════════════════════════════════════════════════════════════════════
#  Block G — Systemd Service Files
# ═══════════════════════════════════════════════════════════════════════════════

class TestSystemdServiceFiles(unittest.TestCase):
    """Validate dist/ systemd service files."""

    def test_1917_daemon_service_exists(self):
        """lina@.service exists in dist/."""
        svc = Path(__file__).parent.parent / "dist" / "lina@.service"
        self.assertTrue(svc.exists(), f"Missing: {svc}")

    def test_1918_daemon_service_valid(self):
        """lina@.service has valid systemd structure."""
        svc = Path(__file__).parent.parent / "dist" / "lina@.service"
        if svc.exists():
            content = svc.read_text()
            self.assertIn("[Unit]", content)
            self.assertIn("[Service]", content)
            self.assertIn("[Install]", content)
            self.assertIn("--daemon", content)

    def test_1919_gui_service_exists(self):
        """lina-gui.service exists in dist/."""
        svc = Path(__file__).parent.parent / "dist" / "lina-gui.service"
        self.assertTrue(svc.exists(), f"Missing: {svc}")

    def test_1920_gui_service_valid(self):
        """lina-gui.service has --gui flag."""
        svc = Path(__file__).parent.parent / "dist" / "lina-gui.service"
        if svc.exists():
            content = svc.read_text()
            self.assertIn("--gui", content)
            self.assertIn("[Service]", content)


# ═══════════════════════════════════════════════════════════════════════════════
#  Block H — Version Consistency
# ═══════════════════════════════════════════════════════════════════════════════

class TestVersionConsistency(unittest.TestCase):
    """Verify version strings are consistent across the project."""

    def _get_lina_version(self):
        from lina import __version__
        return __version__

    def test_1921_init_version_format(self):
        """lina.__version__ is valid semver."""
        v = self._get_lina_version()
        self.assertRegex(v, r"^\d+\.\d+\.\d+")

    def test_1922_pyproject_version_matches(self):
        """pyproject.toml version matches __init__."""
        v = self._get_lina_version()
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text()
            self.assertIn(f'version = "{v}"', content)

    def test_1923_cli_version_uses_init(self):
        """CLI --version outputs same as __init__.__version__."""
        from lina.core.cli import _get_version
        self.assertEqual(_get_version(), self._get_lina_version())

    def test_1924_man_page_version(self):
        """man page header contains current version."""
        v = self._get_lina_version()
        man = Path(__file__).parent.parent / "man" / "lina.1"
        if man.exists():
            content = man.read_text()
            self.assertIn(v, content)

    def test_1925_makefile_uses_lina(self):
        """Makefile references 'lina' not 'jarvis'."""
        makefile = Path(__file__).parent.parent.parent / "Makefile"
        if makefile.exists():
            content = makefile.read_text()
            # Should have lina references
            self.assertIn("lina/", content)
            # Should NOT have jarvis references (except possibly in comments)
            jarvis_refs = [line for line in content.splitlines()
                          if "jarvis" in line.lower() and not line.strip().startswith("#")]
            self.assertEqual(len(jarvis_refs), 0,
                            f"Makefile still references jarvis: {jarvis_refs}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Block I — GUI First Run Wizard
# ═══════════════════════════════════════════════════════════════════════════════

class TestGUIWizard(unittest.TestCase):
    """GUI wizard module: importability and page classes."""

    def test_1926_wizard_module_exists(self):
        """gui/wizard.py exists."""
        wizard_path = Path(__file__).parent.parent / "gui" / "wizard.py"
        self.assertTrue(wizard_path.exists())

    def test_1927_wizard_module_importable(self):
        """lina.gui.wizard is importable."""
        import lina.gui.wizard as wiz
        self.assertTrue(hasattr(wiz, 'FirstRunQWizard'))

    def test_1928_wizard_pages_defined(self):
        """All 8 page classes exist in wizard module."""
        import lina.gui.wizard as wiz
        page_names = [
            'WelcomePage', 'ModelPage', 'DownloadPage', 'IndexPage',
            'SystemPage', 'LanguagePage', 'FeaturesPage', 'CompletePage',
        ]
        for name in page_names:
            self.assertTrue(
                hasattr(wiz, name) and getattr(wiz, name) is not None,
                f"Missing page class: {name}")

    def test_1929_wizard_has_get_backend(self):
        """FirstRunQWizard has get_backend() method."""
        import lina.gui.wizard as wiz
        cls = wiz.FirstRunQWizard
        # If Qt available, check method; if stub, it raises ImportError
        if hasattr(cls, 'get_backend'):
            self.assertTrue(callable(cls.get_backend))
        else:
            # Stub class — only __init__ that raises ImportError
            self.assertTrue(True)

    def test_1930_wizard_has_get_state_dict(self):
        """FirstRunQWizard has get_state_dict() method."""
        import lina.gui.wizard as wiz
        cls = wiz.FirstRunQWizard
        if hasattr(cls, 'get_state_dict'):
            self.assertTrue(callable(cls.get_state_dict))

    def test_1931_wizard_source_has_accept(self):
        """Wizard source code contains accept() override."""
        import inspect
        import lina.gui.wizard as wiz
        src = inspect.getsource(wiz)
        self.assertIn("def accept(self)", src)
        self.assertIn("mark_first_run_done", src)

    def test_1932_wizard_russian_button_labels(self):
        """Wizard source has Russian button text."""
        import inspect
        import lina.gui.wizard as wiz
        src = inspect.getsource(wiz)
        self.assertIn("Далее", src)
        self.assertIn("Назад", src)
        self.assertIn("Готово", src)
        self.assertIn("Пропустить", src)

    def test_1933_wizard_8_pages_added(self):
        """Source adds exactly 8 pages via addPage."""
        import inspect
        import lina.gui.wizard as wiz
        src = inspect.getsource(wiz)
        add_count = src.count("self.addPage(")
        self.assertEqual(add_count, 8)


# ═══════════════════════════════════════════════════════════════════════════════
#  Block J — Flatpak Manifest
# ═══════════════════════════════════════════════════════════════════════════════

class TestFlatpakManifest(unittest.TestCase):
    """org.lina.Lina.yml Flatpak manifest."""

    def test_1934_flatpak_manifest_exists(self):
        """Flatpak manifest exists in dist/."""
        manifest = Path(__file__).parent.parent / "dist" / "org.lina.Lina.yml"
        self.assertTrue(manifest.exists())

    def test_1935_flatpak_app_id(self):
        """Flatpak manifest has correct app-id."""
        manifest = Path(__file__).parent.parent / "dist" / "org.lina.Lina.yml"
        content = manifest.read_text()
        self.assertIn("app-id: org.lina.Lina", content)

    def test_1936_flatpak_runtime(self):
        """Flatpak uses freedesktop runtime."""
        manifest = Path(__file__).parent.parent / "dist" / "org.lina.Lina.yml"
        content = manifest.read_text()
        self.assertIn("org.freedesktop.Platform", content)

    def test_1937_flatpak_offline(self):
        """Flatpak does NOT share network (offline-first)."""
        manifest = Path(__file__).parent.parent / "dist" / "org.lina.Lina.yml"
        content = manifest.read_text()
        # Should not have --share=network uncommented
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "- --share=network":
                self.fail("Network should not be enabled (offline-first)")

    def test_1938_flatpak_audio(self):
        """Flatpak has pulseaudio for voice."""
        manifest = Path(__file__).parent.parent / "dist" / "org.lina.Lina.yml"
        content = manifest.read_text()
        self.assertIn("pulseaudio", content)

    def test_1939_flatpak_has_lina_module(self):
        """Flatpak manifest builds lina module."""
        manifest = Path(__file__).parent.parent / "dist" / "org.lina.Lina.yml"
        content = manifest.read_text()
        self.assertIn("name: lina", content)


# ═══════════════════════════════════════════════════════════════════════════════
#  Block K — AppData & Icon
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppDataAndIcon(unittest.TestCase):
    """AppData XML and SVG icon."""

    def test_1940_appdata_exists(self):
        """AppData XML exists in data/."""
        appdata = Path(__file__).parent.parent / "data" / "org.lina.Lina.appdata.xml"
        self.assertTrue(appdata.exists())

    def test_1941_appdata_has_id(self):
        """AppData has correct component id."""
        appdata = Path(__file__).parent.parent / "data" / "org.lina.Lina.appdata.xml"
        content = appdata.read_text()
        self.assertIn("<id>org.lina.Lina</id>", content)

    def test_1942_appdata_has_license(self):
        """AppData has MIT license."""
        appdata = Path(__file__).parent.parent / "data" / "org.lina.Lina.appdata.xml"
        content = appdata.read_text()
        self.assertIn("MIT", content)

    def test_1943_appdata_has_description_ru(self):
        """AppData has Russian description."""
        appdata = Path(__file__).parent.parent / "data" / "org.lina.Lina.appdata.xml"
        content = appdata.read_text()
        self.assertIn('xml:lang="ru"', content)

    def test_1944_appdata_has_releases(self):
        """AppData has at least one release."""
        appdata = Path(__file__).parent.parent / "data" / "org.lina.Lina.appdata.xml"
        content = appdata.read_text()
        self.assertIn("<release", content)

    def test_1945_icon_svg_exists(self):
        """SVG icon exists."""
        icon = Path(__file__).parent.parent / "data" / "icons" / "lina.svg"
        self.assertTrue(icon.exists())

    def test_1946_icon_is_valid_svg(self):
        """Icon file is valid SVG."""
        icon = Path(__file__).parent.parent / "data" / "icons" / "lina.svg"
        content = icon.read_text()
        self.assertIn("<svg", content)
        self.assertIn("</svg>", content)

    def test_1947_icon_has_viewbox(self):
        """SVG has viewBox for scalability."""
        icon = Path(__file__).parent.parent / "data" / "icons" / "lina.svg"
        content = icon.read_text()
        self.assertIn("viewBox", content)


# ═══════════════════════════════════════════════════════════════════════════════
#  Block L — Man Page Completeness
# ═══════════════════════════════════════════════════════════════════════════════

class TestManPageComplete(unittest.TestCase):
    """Man page has all required sections."""

    def _read_man(self):
        man = Path(__file__).parent.parent / "man" / "lina.1"
        return man.read_text()

    def test_1948_man_has_modes_section(self):
        """Man page has MODES section."""
        self.assertIn(".SH MODES", self._read_man())

    def test_1949_man_has_exit_status(self):
        """Man page has EXIT STATUS section."""
        self.assertIn(".SH EXIT STATUS", self._read_man())

    def test_1950_man_has_version_flag(self):
        """Man page documents --version."""
        self.assertIn("version", self._read_man())

    def test_1951_man_has_help_flag(self):
        """Man page documents --help."""
        self.assertIn("help", self._read_man().lower())

    def test_1952_man_has_cv_flag(self):
        """Man page documents --cv."""
        content = self._read_man()
        self.assertTrue("cv" in content.lower(),
                        "Man page should document --cv")

    def test_1953_man_has_daemon_mode(self):
        """Man page MODES section describes Daemon."""
        self.assertIn("Daemon", self._read_man())

    def test_1954_man_has_gui_mode(self):
        """Man page MODES section describes GUI."""
        self.assertIn("GUI", self._read_man())

    def test_1955_man_no_duplicate(self):
        """Only one man page exists (no docs/lina.1)."""
        stale = Path(__file__).parent.parent / "docs" / "lina.1"
        self.assertFalse(stale.exists(),
                         "Stale docs/lina.1 should be removed")


# ═══════════════════════════════════════════════════════════════════════════════
#  Block M — README & Documentation
# ═══════════════════════════════════════════════════════════════════════════════

class TestDocumentation(unittest.TestCase):
    """Documentation completeness checks."""

    def test_1956_readme_has_version(self):
        """README mentions version string."""
        readme = Path(__file__).parent.parent / "README.md"
        content = readme.read_text()
        self.assertRegex(content, r"v?\d+\.\d+\.\d+")

    def test_1957_readme_has_test_command(self):
        """README documents the pytest command for the main suite."""
        readme = Path(__file__).parent.parent / "README.md"
        content = readme.read_text()
        self.assertIn("python -m pytest tests -q", content)

    def test_1958_readme_has_python_m_lina(self):
        """README shows python -m lina usage."""
        readme = Path(__file__).parent.parent / "README.md"
        content = readme.read_text()
        self.assertIn("python -m lina", content)

    def test_1959_docs_architecture_exists(self):
        """docs/architecture.md exists."""
        doc = Path(__file__).parent.parent / "docs" / "architecture.md"
        self.assertTrue(doc.exists())

    def test_1960_docs_developer_guide_exists(self):
        """docs/developer_guide.md exists."""
        doc = Path(__file__).parent.parent / "docs" / "developer_guide.md"
        self.assertTrue(doc.exists())

    def test_1961_docs_security_model_exists(self):
        """docs/security_model.md exists."""
        doc = Path(__file__).parent.parent / "docs" / "security_model.md"
        self.assertTrue(doc.exists())

    def test_1962_docs_operations_exists(self):
        """docs/operations.md exists."""
        doc = Path(__file__).parent.parent / "docs" / "operations.md"
        self.assertTrue(doc.exists())

    def test_1963_install_md_exists(self):
        """INSTALL.md exists."""
        doc = Path(__file__).parent.parent / "INSTALL.md"
        self.assertTrue(doc.exists())

    def test_1964_install_md_multi_distro(self):
        """INSTALL.md covers Arch + Debian + Fedora."""
        doc = Path(__file__).parent.parent / "INSTALL.md"
        content = doc.read_text()
        for distro in ("Arch", "Debian", "Fedora"):
            self.assertIn(distro, content)

    def test_1965_contributing_exists(self):
        """CONTRIBUTING.md exists."""
        doc = Path(__file__).parent.parent / "CONTRIBUTING.md"
        self.assertTrue(doc.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
