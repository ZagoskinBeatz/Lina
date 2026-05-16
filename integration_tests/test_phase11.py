#!/usr/bin/env python3
"""
Lina Phase 11 — Integration Tests.

Тестирует полную интеграцию модулей Phase 11:
  - Output Isolation: TTY / PIPE / CI режимы
  - Bootstrap → CLI → Runtime → REPL pipeline
  - Fish shell совместимость
  - Безопасность: no eval, no exec, no raw print
  - LLMEngine с SafePrinter
"""

import sys
import os
import io

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

passed = 0
failed = 0
total = 0


def test(name, func):
    global passed, failed, total
    total += 1
    try:
        result = func()
        if result is not False:
            print(f"  ✅ {total:03d}. {name}")
            passed += 1
        else:
            print(f"  ❌ {total:03d}. {name}: returned False")
            failed += 1
    except Exception as e:
        print(f"  ❌ {total:03d}. {name}: {e}")
        failed += 1


test.__test__ = False



if __name__ == "__main__":
    print("=" * 60)
    print("  Phase 11 — Integration Tests")
    print("=" * 60)


    # ── 1. Full Pipeline: bootstrap → CLI → output ──
    print("\n── 1. Full Pipeline ──")


    def test_full_pipeline_bootstrap():
        """bootstrap + CLI + output mode detection works end-to-end."""
        from lina.core.bootstrap import bootstrap
        from lina.core.cli import parse_args
        from lina.core.output import detect_output_mode, OutputMode

        bootstrap()
        args = parse_args(["--cv", "--quiet"])
        mode = detect_output_mode()

        assert args.cv is True
        assert args.quiet is True
        assert isinstance(mode, OutputMode)
        return True


    def test_apply_config_integration():
        """apply_config modifies global config correctly."""
        from lina.core.cli import LinaArgs
        from lina.core.runtime import apply_config
        from lina.config import config

        old_cv = config.cv.enabled
        old_web = config.web.enabled

        args = LinaArgs(cv=True, web=True, port=9999)
        apply_config(args)

        assert config.cv.enabled is True
        assert config.web.enabled is True
        assert config.web.port == 9999

        # Restore
        config.cv.enabled = old_cv
        config.web.enabled = old_web
        config.web.port = 8585
        return True


    def test_startup_info_pipe_mode():
        """Startup info in PIPE mode has no emoji."""
        from lina.core.output import SafePrinter, OutputMode
        from lina.core.runtime import print_startup_info
        from lina.shell.commander import Commander

        buf = io.StringIO()
        printer = SafePrinter(mode=OutputMode.PIPE, stream=buf)
        commander = Commander()

        print_startup_info(printer, commander)
        output = buf.getvalue()

        # Output must NOT contain problematic emoji
        assert "🟢" not in output, "PIPE output must not contain 🟢"
        assert "🔵" not in output, "PIPE output must not contain 🔵"
        assert "⚠" not in output, "PIPE output must not contain ⚠"

        # But must contain ASCII replacements
        assert "[OK]" in output or "[INFO]" in output or "LLM" in output
        return True


    def test_startup_info_tty_mode():
        """Startup info in TTY mode has emoji."""
        from lina.core.output import SafePrinter, OutputMode
        from lina.core.runtime import print_startup_info
        from lina.shell.commander import Commander

        buf = io.StringIO()
        printer = SafePrinter(mode=OutputMode.TTY, stream=buf)
        commander = Commander()

        print_startup_info(printer, commander)
        output = buf.getvalue()

        # TTY output should have emoji
        assert "💻" in output or "📁" in output or "🟢" in output
        return True


    def test_startup_info_ci_mode():
        """Startup info in CI mode suppresses banner."""
        from lina.core.output import SafePrinter, OutputMode
        from lina.core.runtime import print_startup_info, BANNER
        from lina.shell.commander import Commander

        buf = io.StringIO()
        printer = SafePrinter(mode=OutputMode.CI, stream=buf)
        commander = Commander()

        # Banner should be suppressed
        printer.banner(BANNER)
        banner_output = buf.getvalue()
        assert banner_output == "", "Banner should be suppressed in CI mode"

        # But status info should still appear (sanitized)
        print_startup_info(printer, commander)
        output = buf.getvalue()
        assert len(output) > 0, "Status info should still appear in CI mode"
        return True


    test("full pipeline bootstrap", test_full_pipeline_bootstrap)
    test("apply_config integration", test_apply_config_integration)
    test("startup info PIPE mode", test_startup_info_pipe_mode)
    test("startup info TTY mode", test_startup_info_tty_mode)
    test("startup info CI mode", test_startup_info_ci_mode)


    # ── 2. REPL Integration ──
    print("\n── 2. REPL Integration ──")


    def test_repl_with_pipe_printer():
        """REPL session uses PIPE printer correctly."""
        from lina.core.repl import REPLSession
        from lina.core.output import SafePrinter, OutputMode
        from lina.shell.commander import Commander

        buf = io.StringIO()
        printer = SafePrinter(mode=OutputMode.PIPE, stream=buf)
        commander = Commander()
        session = REPLSession(commander, printer)

        # Oneshot — should process and output without emoji
        result = session.run_oneshot("/версия")
        output = buf.getvalue()

        # Check no problematic emoji leaked
        for emoji in ["🟢", "🔵", "⚠", "❌", "✅", "⏳"]:
            assert emoji not in output, f"PIPE mode leaked emoji: {emoji}"

        return True


    def test_repl_prompt_safe():
        """REPL prompt is safe in PIPE mode (no emoji)."""
        from lina.core.output import SafePrinter, OutputMode

        pipe_printer = SafePrinter(mode=OutputMode.PIPE)
        prompt = pipe_printer.prompt_text("Lina")
        assert "🟢" not in prompt
        assert "Lina" in prompt

        tty_printer = SafePrinter(mode=OutputMode.TTY)
        prompt = tty_printer.prompt_text("Lina")
        assert "🟢" in prompt
        assert "Lina" in prompt
        return True


    def test_repl_shutdown():
        """REPL session shutdown cleans up without errors."""
        from lina.core.repl import REPLSession
        from lina.core.output import SafePrinter, OutputMode
        from lina.shell.commander import Commander

        buf = io.StringIO()
        printer = SafePrinter(mode=OutputMode.PIPE, stream=buf)
        commander = Commander()
        session = REPLSession(commander, printer)

        session._shutdown(message="Test shutdown")
        assert session.is_running is False
        assert "shutdown" in buf.getvalue().lower() or "Test" in buf.getvalue()
        return True


    test("REPL with PIPE printer", test_repl_with_pipe_printer)
    test("REPL prompt safe", test_repl_prompt_safe)
    test("REPL shutdown", test_repl_shutdown)


    # ── 3. LLMEngine + SafePrinter ──
    print("\n── 3. LLMEngine Integration ──")


    def test_llm_engine_has_safe_print():
        """LLMEngine has _print method using SafePrinter."""
        from lina.llm.engine import LLMEngine
        engine = LLMEngine()
        assert hasattr(engine, '_print')
        assert callable(engine._print)
        return True


    def test_llm_engine_print_uses_printer():
        """LLMEngine._print() delegates to SafePrinter."""
        from lina.llm.engine import LLMEngine
        from lina.core.output import reset_printer, OutputMode

        buf = io.StringIO()
        printer = reset_printer(OutputMode.PIPE)
        printer._stream = buf

        engine = LLMEngine()
        engine._print("🟢 test message")
        output = buf.getvalue()

        assert "🟢" not in output, "SafePrinter should replace 🟢"
        assert "[OK]" in output
        assert "test message" in output

        # Restore default printer
        reset_printer()
        return True


    def test_llm_engine_format_status():
        """LLMEngine.format_status() returns valid status string."""
        from lina.llm.engine import LLMEngine
        engine = LLMEngine()
        status = engine.format_status()
        assert "LLM Engine" in status
        assert "llama-cpp" in status.lower() or "llama" in status.lower()
        return True


    test("LLMEngine has _print", test_llm_engine_has_safe_print)
    test("LLMEngine _print uses printer", test_llm_engine_print_uses_printer)
    test("LLMEngine format_status", test_llm_engine_format_status)


    # ── 4. Fish Shell Safety ──
    print("\n── 4. Fish Shell Safety ──")


    def test_output_no_emoji_at_line_start_pipe():
        """In PIPE mode, no line starts with emoji that fish could interpret."""
        from lina.core.output import SafePrinter, OutputMode
        from lina.core.runtime import print_startup_info
        from lina.shell.commander import Commander

        buf = io.StringIO()
        printer = SafePrinter(mode=OutputMode.PIPE, stream=buf)
        commander = Commander()

        print_startup_info(printer, commander)
        output = buf.getvalue()

        # Check each line — no emoji at start
        dangerous_emoji = ["🟢", "🔵", "⚠", "❌", "✅", "⏳", "♻", "🔄", "💻", "📁"]
        for line in output.split("\n"):
            stripped = line.strip()
            for emoji in dangerous_emoji:
                assert not stripped.startswith(emoji), \
                    f"Line starts with emoji '{emoji}': {stripped[:40]}"

        return True


    def test_env_lina_output_mode():
        """LINA_OUTPUT_MODE environment variable controls mode."""
        from lina.core.output import detect_output_mode, OutputMode
        old = os.environ.get("LINA_OUTPUT_MODE")

        for mode_name, expected in [
            ("TTY", OutputMode.TTY),
            ("PIPE", OutputMode.PIPE),
            ("CI", OutputMode.CI),
        ]:
            os.environ["LINA_OUTPUT_MODE"] = mode_name
            detected = detect_output_mode()
            assert detected == expected, \
                f"LINA_OUTPUT_MODE={mode_name} should be {expected}, got {detected}"

        # Restore
        if old is None:
            os.environ.pop("LINA_OUTPUT_MODE", None)
        else:
            os.environ["LINA_OUTPUT_MODE"] = old
        return True


    def test_sanitize_all_problematic_emoji():
        """All emoji that fish complains about are handled."""
        from lina.core.output import sanitize_text, OutputMode

        # These are the exact emoji that fish reports as 'Unknown command'
        problematic = ["🟢", "⚠", "❌", "✅", "⏳", "🔄"]
        for emoji in problematic:
            result = sanitize_text(f"{emoji} some text", OutputMode.PIPE)
            assert emoji not in result, f"Emoji {emoji} not sanitized"
        return True


    def test_complete_output_isolation():
        """Full startup flow produces no emoji in PIPE mode."""
        from lina.core.output import SafePrinter, OutputMode
        from lina.core.runtime import print_startup_info, print_optional_info
        from lina.core.cli import LinaArgs
        from lina.shell.commander import Commander

        buf = io.StringIO()
        printer = SafePrinter(mode=OutputMode.PIPE, stream=buf)
        commander = Commander()
        args = LinaArgs()

        printer.banner("TEST BANNER")
        print_startup_info(printer, commander)
        print_optional_info(printer, commander, args)
        printer.separator()

        output = buf.getvalue()

        # Comprehensive check — no raw emoji in output
        import re
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map
            "\U0001F1E0-\U0001F1FF"  # flags
            "\U00002702-\U000027B0"  # dingbats
            "\U0000FE00-\U0000FE0F"  # variation selectors
            "\u2600-\u26FF"          # misc symbols
            "\u2700-\u27BF"          # dingbats
            "]+",
            flags=re.UNICODE
        )

        matches = emoji_pattern.findall(output)
        assert len(matches) == 0, f"Emoji found in PIPE output: {matches}"

        return True


    test("no emoji at line start PIPE", test_output_no_emoji_at_line_start_pipe)
    test("LINA_OUTPUT_MODE env", test_env_lina_output_mode)
    test("sanitize problematic emoji", test_sanitize_all_problematic_emoji)
    test("complete output isolation", test_complete_output_isolation)


    # ── 5. Signal Handling ──
    print("\n── 5. Signal Handling ──")


    def test_signal_handlers_setup():
        """Signal handlers can be set up without error."""
        import signal
        from lina.core.bootstrap import setup_signal_handlers

        cleanup_called = []
        setup_signal_handlers(cleanup_fn=lambda: cleanup_called.append(True))

        handler = signal.getsignal(signal.SIGINT)
        assert handler is not signal.default_int_handler
        assert callable(handler)
        return True


    def test_faulthandler_enabled():
        """faulthandler is enabled after bootstrap."""
        import faulthandler
        from lina.core.bootstrap import bootstrap

        bootstrap()
        assert faulthandler.is_enabled()
        return True


    test("signal handlers setup", test_signal_handlers_setup)
    test("faulthandler enabled", test_faulthandler_enabled)


    # ═══ Итог ═══
    print("\n" + "=" * 60)
    print(f"  Phase 11 Integration: {passed}/{total} тестов пройдено")
    if failed:
        print(f"  ПРОВАЛЕНО: {failed}")
    else:
        print("  ВСЕ ТЕСТЫ ПРОЙДЕНЫ! ✨")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
