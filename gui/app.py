"""
Lina GUI — Application Entry Point.

Block K: Initializes QApplication, backend services, MainWindow,
         tray icon, and starts the event loop.

Block H: Wires TrayIconController to MainWindow (show/hide/quit).

Usage:
    python -m lina.gui.app
    lina --gui
"""

from __future__ import annotations

import sys
import logging
import re as _re
import threading
import signal
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("lina.gui.app")

# ── Precompiled regex for datetime fast-path ──
_RE_DATETIME_QUICK = _re.compile(
    r"(который\s+час|сколько\s+времени|как(ое|ой|ая)\s+"
    r"(сейчас\s+)?(дата|число|день)|^текущее\s+время"
    r"|^\s*(time|date)\s*$|сколько\s+сейчас\s+часов)",
    _re.IGNORECASE,
)

# ── Precompiled regex for system-adjacent queries ──
_RE_SYS_MENTION = _re.compile(
    r'(расскажи|покажи|скажи|выведи|информац|сведен)\w*\s*.{0,12}'
    r'\b(систем|компьютер|желез|машин|процессор|памят|оператив|диск|ядр)\w*'
    r'|\b(моя|мой|моё|моей)\s+(систем|компьютер|\S*машин)\w*'
    r'|\bчто\s+за\s+(систем|компьютер|pc)\w*'
    # Installed software queries → need system context
    r'|как(?:ие|ой|ая|ое)\s+.{0,20}установлен'
    r'|(?:есть|стоит)\s+(?:ли\s+)?(?:у\s+меня\s+)?\S+\s+установлен'
    r'|список\s+(?:установленных|приложений|программ|пакетов)'
    # Gaming compatibility → system specs needed
    r'|(?:можно|могу)\s+(?:ли\s+)?(?:.{0,30})?поиграть'
    r'|(?:потянет|пойд[её]т|запустится)\s+(?:ли\s+)?',
    _re.IGNORECASE,
)


def run_gui(argv: list | None = None) -> int:
    """Launch the Lina desktop GUI.

    Returns:
        Exit code (0 = normal, 1 = error).
    """
    # ── Early Qt availability check ──
    try:
        from lina.gui import get_qt_modules, is_gui_available
        if not is_gui_available():
            print("Ошибка: Qt не найден. Установите PyQt6 или PySide6.")
            print("  pip install PyQt6")
            return 1
        QtWidgets, QtCore, QtGui = get_qt_modules()
    except ImportError as e:
        print(f"Ошибка импорта GUI: {e}")
        print("Установите зависимости: pip install PyQt6")
        return 1

    # ── Setup logging ──
    # NOTE: bootstrap.py adds NullHandler to root, so basicConfig is a no-op.
    # Force a StreamHandler for console visibility of parser + pipeline logs.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    # Ensure root has a real StreamHandler (not just NullHandler)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    has_stream = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.NullHandler)
        for h in root_logger.handlers
    )
    if not has_stream:
        _console = logging.StreamHandler()
        _console.setLevel(logging.INFO)
        _console.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        ))
        root_logger.addHandler(_console)

    # ── Create QApplication ──
    app = QtWidgets.QApplication(argv or sys.argv)
    app.setApplicationName("Lina AI Assistant")
    app.setApplicationVersion("0.7.0")
    app.setOrganizationName("Lina")

    # Allow Ctrl+C in terminal
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # ── Initialize backend services ──
    from lina.gui.chat import ChatController
    from lina.gui.settings import get_settings
    from lina.gui.tray import TrayIconController, TrayConfig

    settings = get_settings()
    controller = ChatController()

    # Set up request handler (pipeline) if available
    _setup_pipeline_handler(controller)

    # ── Create tray controller (Block H) ──
    tray_controller = TrayIconController(TrayConfig())

    # ── Create main window ──
    from lina.gui.main_window import create_main_window
    window = create_main_window(
        controller=controller,
        settings=settings,
        tray_controller=tray_controller,
    )

    # ── Wire tray actions (Block H) ──
    tray_controller.register_actions({
        "open_chat": window.toggle_visibility,
        "open_settings": lambda: _open_settings(window),
        "about": lambda: window._show_about(),
        "quit": lambda: _quit_app(window, app),
    })

    # ── Create Qt tray icon ──
    tray_icon = None
    if settings.gui.show_tray_icon:
        try:
            from lina.gui.tray import create_qt_tray
            tray_icon, _ = create_qt_tray(tray_controller, app)
            logger.info("Tray icon created")
        except Exception as e:
            logger.warning("Tray icon creation failed: %s", e)

    # ── Show window ──
    if settings.gui.start_minimized and tray_icon:
        logger.info("Запуск свёрнуто в трей")
    else:
        window.show()

    # ── Register global hotkeys (non-blocking) ──
    try:
        from lina.governance.hotkey_manager import HotkeyManager
        hotkey_mgr = HotkeyManager()
        hotkey_mgr.detect_desktop()
        results = hotkey_mgr.register_all()
        registered = [k for k, v in results.items() if v]
        if registered:
            logger.info("Global hotkeys registered: %s", registered)
        else:
            logger.debug("No global hotkeys registered (non-critical)")
    except Exception as e:
        logger.debug("Hotkey registration skipped: %s", e)

    # ── D-Bus service (expose ToggleWindow + Query) ──
    _dbus_watcher = None
    try:
        from lina.gui.dbus_bridge import start_dbus_listener
        _dbus_watcher = start_dbus_listener(window, controller)
        if _dbus_watcher:
            logger.info("D-Bus listener started (org.lina.Assistant)")
    except Exception as e:
        logger.debug("D-Bus listener skipped: %s", e)

    # ── Voice pipeline (STT → text → pipeline → TTS) ──
    _voice_worker = None
    try:
        from lina.voice import get_voice_status
        vstatus = get_voice_status()
        if vstatus.get("stt_available") or vstatus.get("tts_available"):
            window.input_bar.set_voice_available(True)
            logger.info("Voice available: STT=%s (%s), TTS=%s (%s)",
                        vstatus["stt_available"], vstatus.get("stt_backend"),
                        vstatus["tts_available"], vstatus.get("tts_backend"))

            _stt_instance = None
            _tts_instance = None
            _voice_initiated = False  # v0.9.0: Track voice-originated msgs

            def _get_stt():
                nonlocal _stt_instance
                if _stt_instance is None:
                    from lina.voice.stt import SpeechToText
                    _stt_instance = SpeechToText()
                return _stt_instance

            def _get_tts():
                nonlocal _tts_instance
                if _tts_instance is None:
                    from lina.voice.tts import TextToSpeech
                    _tts_instance = TextToSpeech()
                return _tts_instance

            def _on_voice_requested():
                """Start voice recording in worker thread."""
                nonlocal _voice_worker
                stt = _get_stt()
                if not stt or not stt.is_available():
                    window.status_bar.set_mode("error")
                    return
                from lina.gui.workers import create_voice_worker_class
                VoiceWorker = create_voice_worker_class()
                _voice_worker = VoiceWorker(stt, listen_seconds=10.0)
                _voice_worker.text_recognized.connect(_on_voice_text)
                _voice_worker.error.connect(_on_voice_error)
                _voice_worker.recording_started.connect(
                    lambda: window.status_bar.set_mode("listening"))
                _voice_worker.start()

            def _on_voice_stop():
                """Stop voice recording."""
                nonlocal _voice_worker
                if _voice_worker:
                    _voice_worker.cancel()
                    _voice_worker = None
                window.input_bar.set_voice_recording(False)
                window.status_bar.set_mode("ready")

            def _on_voice_text(text):
                """Recognized text → send to pipeline (same as typed text)."""
                nonlocal _voice_worker, _voice_initiated
                _voice_worker = None
                _voice_initiated = True  # v0.9.0: mark as voice-originated
                window.input_bar.set_voice_recording(False)
                window.status_bar.set_mode("ready")
                # Insert into text field and send
                window.input_bar.text_edit.setPlainText(text)
                window.input_bar.send_requested.emit(text)
                window.input_bar.text_edit.clear()

            def _on_voice_error(msg):
                nonlocal _voice_worker
                _voice_worker = None
                window.input_bar.set_voice_recording(False)
                window.status_bar.set_mode("error")
                logger.warning("Voice error: %s", msg)

            window.input_bar.voice_requested.connect(_on_voice_requested)
            window.input_bar.voice_stop_requested.connect(_on_voice_stop)

            # v0.9.0: TTS response — озвучиваем ответ для voice-запросов
            def _tts_response_cb(response_text: str):
                nonlocal _voice_initiated
                if _voice_initiated:
                    _voice_initiated = False
                    tts = _get_tts()
                    if tts and tts.is_available():
                        tts.speak_async(response_text)

            controller.set_on_tts_response(_tts_response_cb)
        else:
            logger.debug("Voice not available (no STT/TTS backends)")
    except Exception as e:
        logger.debug("Voice init skipped: %s", e)

    # ── Status poller ──
    try:
        from lina.gui.workers import create_status_poller_class
        StatusPoller = create_status_poller_class()
        poller = StatusPoller(interval_ms=10000)
        def _on_status_updated(s):
            ram_text = f"RAM: {s.get('ram_mb', 0):.0f} MB"
            if hasattr(window.status_bar, "set_metrics"):
                window.status_bar.set_metrics(ram_text)
            else:
                window.status_bar.set_info(ram_text)

        poller.status_updated.connect(_on_status_updated)
        poller.start()
    except Exception as e:
        logger.debug("Status poller not started: %s", e)

    logger.info("Lina GUI запущен")

    # Freeze config after full GUI initialization
    from lina.config import config as _cfg_freeze
    _cfg_freeze.freeze()

    return app.exec()


def _setup_pipeline_handler(controller):
    """Connect ChatController to the FULL processing pipeline.

    ALL requests go through LLM with real system context + RAG + diagnostics.
    LLM output with ```bash``` blocks is auto-executed.
    Model auto-loads on first request if not in memory.

    Pipeline:
      1. Collect system snapshot (distro, kernel, DE, utils)
      2. RAG: search knowledge base for relevant context
      3. Diagnostics: run diagnostic tree if problem detected
      4. Web search: detect web queries → search → LLM summarization
      5. Enrich query with system context for LLM
      6. LLM generates response (with ```bash``` commands)
      7. Extract commands from LLM response → auto-execute
      8. Return response + execution results
    """
    from lina.gui.settings import get_settings

    settings = get_settings()

    # ── Lazy-initialized components ──
    _engine = None
    _snapshot = None
    _system_context = None
    _preprocessor = None
    _action_executor = None
    _retriever = None
    _diag_ready = None  # tri-state: None=not tried, True=ok, False=failed
    _web_search = None
    _app_resolver = None
    _intent_router = None  # cached IntentRouter
    _last_intent = [""]     # last resolved intent (mutable for closure)
    _fact_pipeline = None   # FactPipeline instance
    _conversation_state = None  # ConversationState instance
    _last_fact_set = [None]    # last FactSet for anti-hallucination guard
    _init_lock = threading.Lock()  # guards all lazy-init singletons
    _engine_lock = threading.Lock()  # guards engine.generate / direct llama calls

    def _get_engine():
        """Lazy-init LLMEngine singleton (thread-safe)."""
        nonlocal _engine
        if _engine is None:
            with _init_lock:
                if _engine is None:  # double-check under lock
                    try:
                        from lina.llm.engine import LLMEngine
                        _engine = LLMEngine()
                    except Exception as e:
                        logger.warning("LLMEngine init failed: %s", e)
        return _engine

    _INIT_FAILED = object()  # sentinel for failed init

    def _get_system_context():
        """Lazy-init system snapshot and preprocessor (thread-safe)."""
        nonlocal _snapshot, _system_context, _preprocessor, _action_executor
        if _snapshot is _INIT_FAILED:
            return None, None, None  # already failed, don't retry
        if _snapshot is None:
            with _init_lock:
                if _snapshot is _INIT_FAILED:
                    return None, None, None
                if _snapshot is not None:
                    return _system_context, _preprocessor, _action_executor
                try:
                    from lina.core.system_interaction import (
                        collect_system_snapshot,
                        format_snapshot_for_prompt,
                        QueryPreprocessor,
                        ActionExecutor,
                    )
                    _snapshot = collect_system_snapshot()
                    _system_context = format_snapshot_for_prompt(_snapshot)
                    _preprocessor = QueryPreprocessor(_snapshot)
                    _action_executor = ActionExecutor(interactive=False)
                    logger.info(
                        "System context: %s, kernel %s, DE: %s",
                        _snapshot.distro, _snapshot.kernel,
                        _snapshot.de or "none",
                    )
                except Exception as e:
                    logger.warning("System context init failed: %s", e)
                    _snapshot = _INIT_FAILED  # prevent re-init on next call
                    return None, None, None
        return _system_context, _preprocessor, _action_executor

    def _get_retriever():
        """Lazy-init RAG KnowledgeRetriever."""
        nonlocal _retriever
        if _retriever is None:
            with _init_lock:
                if _retriever is None:
                    try:
                        from lina.rag.retriever import KnowledgeRetriever
                        _retriever = KnowledgeRetriever()
                        logger.info("RAG KnowledgeRetriever initialized")
                    except Exception as e:
                        logger.warning("RAG retriever init failed: %s", e)
                        _retriever = _INIT_FAILED  # prevent re-init
        return _retriever if _retriever is not _INIT_FAILED else None

    def _try_diagnostics(text: str) -> Optional[str]:
        """Try diagnostic trees. Returns formatted report or None."""
        nonlocal _diag_ready
        if _diag_ready is False:
            return None  # import already failed, don't retry
        try:
            from lina.diagnostics.integration import diagnose
            _diag_ready = True  # mark import succeeded
            result = diagnose(text)
            if result.get("matched") and result.get("formatted"):
                return result["formatted"]
        except ImportError:
            _diag_ready = False  # permanent failure
        except Exception as e:
            logger.debug("Diagnostics error: %s", e)
        return None

    def _get_web_search():
        """Lazy-init WebSearchEngine."""
        nonlocal _web_search
        if _web_search is None:
            try:
                from lina.core.web_search_engine import get_web_search_engine
                _web_search = get_web_search_engine()
                logger.info("WebSearchEngine initialized (DDG + SearXNG + Wikipedia)")
            except Exception as e:
                logger.warning("WebSearchEngine init failed: %s", e)
                _web_search = False
        return _web_search if _web_search is not False else None

    def _get_fact_pipeline():
        """Lazy-init FactPipeline."""
        nonlocal _fact_pipeline
        if _fact_pipeline is None:
            try:
                from lina.core.fact_pipeline import get_fact_pipeline
                _fact_pipeline = get_fact_pipeline()
            except Exception as e:
                logger.debug("FactPipeline init skipped: %s", e)
        return _fact_pipeline

    def _get_conversation_state():
        """Lazy-init ConversationState."""
        nonlocal _conversation_state
        if _conversation_state is None:
            try:
                from lina.core.query_optimizer import ConversationState
                _conversation_state = ConversationState()
            except Exception as e:
                logger.debug("ConversationState init skipped: %s", e)
        return _conversation_state

    # ── Terminal error extraction for web search ──
    # Patterns that identify shell prompt / noise lines (NOT errors)
    _RE_PS1_LINE = _re.compile(
        r"^(?:"
        r"~[/\\]|"                                   # ~/path
        r"[/\\](?:home|root|usr|var|tmp|opt)[/\\]|"  # /home/user/...
        r"\w+@\w+[:\$#%>]|"                          # user@host:~$
        r"[❯\$#%>]\s|"                               # prompt chars
        r"\d{1,2}:\d{2}\b|"                           # timestamps 19:05
        r"✓|"                                        # check marks
        r"L?total\s+used\s+free|"                    # free/top headers
        r"Mem:|Swap:|"                               # memory lines
        r"\[sudo\]\s|"                               # [sudo] password
        r"password\s+for\s|"                          # password for user
        r"пароль\s+для\s"                            # русский пароль
        r")",
        _re.IGNORECASE | _re.MULTILINE,
    )
    # Patterns that identify error/diagnostic lines
    _RE_ERROR_LINE = _re.compile(
        r"(?:"
        r"ошибк[аиу]|"
        r"не\s+найден[аоы]?|"
        r"command\s+not\s+found|"
        r"no\s+such\s+file|"
        r"permission\s+denied|"
        r"отказано\s+в\s+доступе|"
        r"error[:\s]|"
        r"failed|"
        r"не\s+удалось|"
        r"segmentation\s+fault|"
        r"cannot\s+|"
        r"unable\s+to\s+|"
        r"невозможно\s+|"
        r"fatal[:\s]|"
        r"panic[:\s]|"
        r"exception[:\s]|"
        r"traceback|"
        r"errno\s+\d+"
        r")",
        _re.IGNORECASE,
    )
    # Meta-question words the user wraps around terminal output
    _RE_META_QUESTION = _re.compile(
        r"^(?:найди|поищи|загугли|нагугли|выясни|узнай)\s+"
        r"(?:в\s+(?:интернете|инете|сети|нете|гугл\w*)\s*)?"
        r"(?:информацию?\s*)?(?:по|о|об|про|что\s+(?:по|за|с)\s*)?"
        r"(?:этой|этому|этим|данной|данному|такой)?\s*"
        r"(?:ошибк[еиу]?|ошибке|проблем[еыу]?|вопрос[еу]?)?\s*",
        _re.IGNORECASE,
    )

    def _extract_search_from_terminal(text: str) -> str:
        """Extract a clean search query from user text that may contain
        pasted terminal output (PS1 prompts, timestamps, memory info, etc.).

        Input:
            'Найди в интернете что по этой ошибке
             ~/Документы/Parcer/search_cli
             ❯ sudo apt-get telegram
             [sudo] пароль для zbeatz:
             sudo: apt-get: команда не найдена'

        Output: 'sudo apt-get команда не найдена linux'
        """
        lines = text.strip().splitlines()
        if len(lines) < 2:
            return text  # no multiline terminal output

        # 1. Separate meta-question from terminal dump
        meta_lines = []
        content_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # First non-empty lines that look like natural language = meta
            if not content_lines and not _RE_PS1_LINE.search(stripped):
                # Check if line is a question, not a command
                if _re.match(r'^[А-Яа-яA-Za-z]', stripped) and \
                   not _re.match(r'^[a-z]+:', stripped, _re.I):
                    # Looks like a sentence (starts with letter, no colon prefix)
                    meta_lines.append(stripped)
                    continue
            content_lines.append(stripped)

        if not content_lines:
            return text  # no terminal content detected

        # 2. Find error lines in content
        error_lines = []
        command_lines = []
        for line in content_lines:
            # Skip pure noise: PS1 prompts, memory, passwords
            if _RE_PS1_LINE.search(line):
                # But keep if it also contains an error
                if _RE_ERROR_LINE.search(line):
                    # Extract just the error part after prompt
                    cleaned = _re.sub(r'^\[sudo\]\s*', '', line).strip()
                    cleaned = _re.sub(r'^.*[❯\$#%>]\s*', '', cleaned).strip()
                    if cleaned:
                        error_lines.append(cleaned)
                continue

            # Error lines
            if _RE_ERROR_LINE.search(line):
                error_lines.append(line)
                continue

            # Command lines (e.g. "sudo apt-get telegram")
            if _re.match(r'^(?:sudo\s+)?(?:apt|apt-get|dnf|pacman|pip|npm|'
                         r'docker|systemctl|git|wget|curl|make|cmake|cargo|'
                         r'flatpak|snap|zypper|emerge)\b', line, _re.I):
                command_lines.append(line)

        # 3. Build search query
        if error_lines:
            # Use the most informative error line
            error = max(error_lines, key=len)
            # Strip common prefix noise
            error = _re.sub(r'^(?:E|W|I):\s+', '', error)
            query = error
        elif command_lines:
            # No explicit error but user pasted a command
            query = command_lines[0]
        else:
            # Fallback: use all non-PS1 content lines
            useful = [l for l in content_lines if not _RE_PS1_LINE.search(l)]
            query = " ".join(useful[:3])

        if not query.strip():
            return text

        # 4. Append "linux" if not present for better search results
        q_lower = query.lower()
        if not any(w in q_lower for w in ("linux", "ubuntu", "debian",
                                           "fedora", "arch", "manjaro",
                                           "mint", "centos")):
            query = query.rstrip(".!") + " linux"

        logger.info("Terminal error → search query: '%s'", query[:80])
        return query

    def _try_web_search(text: str, intent: str) -> tuple:
        """Execute web search if intent is web-related.

        Returns (intent, summary):
          - ("weather_query", "🌤️ ...") — direct answer, skip LLM
          - ("web", "Курс USD/RUB: ...") — direct answer, skip LLM
          - ("web_search", "...results...") — context for LLM to summarize
          - (None, None) — no web intent detected

        Side effect: populates _last_fact_set[0] for anti-hallucination guard.
        """
        if intent not in ("web_search", "weather_query", "web"):
            return None, None
        try:
            ws = _get_web_search()
            if not ws:
                return None, None

            # Strip [Контекст: ...] prefix injected by _enrich_followup
            # and rebuild a web-searchable query with the topic
            search_text = text
            ctx_match = _re.match(r"^\[Контекст:\s*([^\]]+)\]\s*(.+)", text, _re.DOTALL)
            if ctx_match:
                topic = ctx_match.group(1).strip()
                question = ctx_match.group(2).strip()
                search_text = f"{topic} {question}"

            # ── Extract error from terminal output ──
            # If user pasted terminal output (PS1 prompts, command output),
            # extract the actual error message for web search.
            if "\n" in search_text or _RE_PS1_LINE.search(search_text):
                search_text = _extract_search_from_terminal(search_text)
            else:
                # Single-line: strip meta-question prefix
                cleaned = _RE_META_QUESTION.sub("", search_text).strip()
                if cleaned and len(cleaned) >= 5:
                    search_text = cleaned

            # ── Gaming compatibility: rewrite query to search for system requirements ──
            _gaming_m = _re.search(
                r'(?:поиграть|играть|запустить)\s+(?:в\s+)?(.+)',
                search_text, _re.IGNORECASE,
            )
            if not _gaming_m:
                _gaming_m = _re.search(
                    r'(?:потянет|пойд[её]т|запустится)\s+(?:ли\s+)?(.+)',
                    search_text, _re.IGNORECASE,
                )
            if _gaming_m:
                _game_name = _gaming_m.group(1).strip()
                # Strip trailing noise like "на моём компьютере"
                _game_name = _re.sub(
                    r'\s+(?:на\s+мо[её][йм]|у\s+меня|на\s+(?:комп|пк|систем|ноутбук))\w*.*$',
                    '', _game_name, flags=_re.IGNORECASE,
                ).strip()
                if _game_name and len(_game_name) >= 2:
                    search_text = f"{_game_name} системные требования"
                    logger.info("Gaming query rewrite: %s", search_text)

            logger.info("Web search detected (intent=%s): %s", intent, search_text[:80])
            resp = ws.search(search_text)
            if resp.success and resp.summary:
                # ── Direct specs output: if SpecExtractor produced results,
                # bypass ALL LLM processing (FactPipeline + main LLM).
                # This prevents hallucinations completely. ──
                if (("📱" in resp.summary or "💻" in resp.summary or "🎮" in resp.summary)
                        and ("**Дисплей**" in resp.summary
                             or "**Графический процессор**" in resp.summary)):
                    logger.info(
                        "SpecExtractor direct output detected — bypassing LLM"
                    )
                    return intent, "[DIRECT_FACTS]" + resp.summary

                # ── Fact Pipeline: extract + verify facts ──
                if intent == "web_search":
                    try:
                        fp = _get_fact_pipeline()
                        from lina.pipeline.generation_gate import get_generation_gate
                        if fp:
                            gate = get_generation_gate()
                            # Извлечь subject из EntityParser
                            subject = ""
                            try:
                                from lina.core.entity_parser import get_entity_parser
                                parsed = get_entity_parser().parse(search_text)
                                subject = parsed.device or parsed.brand or ""
                            except Exception:
                                pass
                            if not subject:
                                from lina.core.main_pipeline import MainPipeline
                                subject = MainPipeline._extract_topic(search_text)

                            fact_set = fp.process(
                                web_summary=resp.summary,
                                results=resp.results,
                                subject=subject,
                            )
                            _last_fact_set[0] = fact_set

                            decision = gate.evaluate("web_search", fact_set)
                            if not decision.allow_generation:
                                logger.info(
                                    "FactPipeline gate blocked (%s), "
                                    "falling back to raw summary mode",
                                    decision.reason,
                                )
                                # Если запрос про "характеристики" и 0 фактов — жёсткий отказ,
                                # НО только если веб-поиск вернул пустые данные.
                                # Если есть результаты поиска — пусть LLM обработает.
                                n_facts = len(fact_set.facts) if fact_set.facts else 0
                                _has_page_text = bool(
                                    resp.summary
                                    and len(resp.summary) > 200
                                    and any(m in resp.summary for m in
                                            ["Подробности", "[src-", "📄"])
                                )
                                if (n_facts == 0
                                        and _RE_SPECS_QUERY.search(search_text)
                                        and not _has_page_text):
                                    logger.info(
                                        "FactPipeline: 0 facts + specs query + "
                                        "no page text → hard refusal",
                                    )
                                    return intent, (
                                        "[Результаты веб-поиска не найдены]\n"
                                        "Не удалось найти достоверные характеристики для "
                                        f"«{subject or search_text}». "
                                        "Возможно, устройство ещё не анонсировано или недостаточно "
                                        "данных в открытых источниках."
                                    )
                                # Fall back to raw summary: the LLM context
                                # builder adds anti-hallucination instructions
                                # ("use ONLY these results") automatically.
                                return intent, resp.summary

                            # Если есть верифицированные факты — вернуть structured context
                            if fact_set.facts and fact_set.confidence >= 0.40:
                                # Запрос про "характеристики" → прямой вывод фактов, без LLM
                                if _RE_SPECS_QUERY.search(search_text):
                                    logger.info(
                                        "FactPipeline: %d facts, conf=%.2f, "
                                        "SPECS MODE → direct output (no LLM)",
                                        len(fact_set.facts), fact_set.confidence,
                                    )
                                    direct = fact_set.format_for_user()
                                    return intent, "[DIRECT_FACTS]" + direct
                                # How-to запрос: НЕ идти в FACT MODE с его строгими
                                # «отвечай ТОЛЬКО по фактам» правилами. Возвращаем
                                # raw summary — далее HOWTO MODE переварит его
                                # с дистро-fact-card и знаниями о пакетных менеджерах.
                                if _RE_HOWTO_QUERY.search(search_text):
                                    logger.info(
                                        "FactPipeline: %d facts, but HOW-TO query — "
                                        "skipping FACT MODE, falling back to raw summary",
                                        len(fact_set.facts),
                                    )
                                    return intent, resp.summary
                                logger.info(
                                    "FactPipeline: %d facts, conf=%.2f, using FACT MODE",
                                    len(fact_set.facts), fact_set.confidence,
                                )
                                return intent, fact_set.format_for_llm_ru()

                            # ── Confidence too low: degrade to raw summary mode ──
                            # The LLM context builder wraps raw summaries with
                            # strict "use ONLY these results" instructions.
                            logger.info(
                                "FactPipeline: confidence=%.2f, %d facts — "
                                "falling back to raw summary mode",
                                fact_set.confidence,
                                len(fact_set.facts) if fact_set.facts else 0,
                            )
                            return intent, resp.summary
                    except Exception as e:
                        logger.warning(
                            "FactPipeline error (falling back to raw summary): %s",
                            e, exc_info=True,
                        )
                        # Fall back to raw summary instead of hard refusal
                        return intent, resp.summary

                # ── Non-web_search intents (weather, web) get raw summary ──
                # Only web_search requires fact verification.
                # weather_query and web intents are direct-answer (no LLM).
                return intent, resp.summary
            elif resp.error:
                logger.debug("Web search failed: %s", resp.error)
        except Exception as e:
            logger.debug("Web search error: %s", e)
        return None, None

    def _try_install_search(text: str, intent: str, decision) -> Optional[str]:
        """For install_application intent: search real packages via ApplicationResolver.

        Returns formatted installation suggestions or None.
        """
        if intent != "install_application":
            return None
        try:
            app_name = ""
            if decision and hasattr(decision, 'metadata') and decision.metadata:
                app_name = decision.metadata.get("app_name", "")
            if not app_name:
                return None

            nonlocal _app_resolver
            if _app_resolver is None:
                with _init_lock:
                    if _app_resolver is None:
                        from lina.core.application_resolver import ApplicationResolver
                        _app_resolver = ApplicationResolver()

            suggestions = _app_resolver.suggest_installation(app_name)
            if not suggestions:
                return None

            parts = [f"[Результат поиска пакетов для «{app_name}»]"]
            for s in suggestions:
                if s.method == "web":
                    continue  # skip web fallback
                parts.append(
                    f"  • {s.method}: {s.command}"
                    + (f" ({s.package_name})" if s.package_name else "")
                )

            if len(parts) <= 1:
                parts.append(f"  Пакет «{app_name}» не найден в pacman/flatpak/snap")
                parts.append(f"  Попробуй поискать в AUR: yay -Ss {app_name}")

            return "\n".join(parts)
        except Exception as e:
            logger.debug("Install search error: %s", e)
        return None

    # Кеш для повторных how-to запросов на одно и то же имя
    _pkg_lookup_cache: Dict[str, str] = {}

    def _extract_install_target(text: str) -> str:
        """Extract the application name from a how-to install query.

        «как установить telegram на мою систему» → «telegram»
        «установи telegram» → «telegram»
        «установи мне telegram» → «telegram»
        «подскажи, как поставить gimp» → «gimp»

        Returns lowercase, trimmed name. Empty string if nothing found.
        """
        # Снимаем приветствия и вступления
        t = _re.sub(r"^\s*(?:привет[!,.]?\s*)?(?:подскажи|объясни|расскажи)?[,.\s]*",
                    "", text, flags=_re.IGNORECASE).strip()
        # Pattern 1: «как установить/поставить/… X»
        m = _re.search(
            r"\b(?:как|how\s+to)\s+(?:установить|поставить|инсталлировать|"
            r"настроить|обновить|удалить|запустить|собрать|"
            r"install|setup|configure|update|remove)\s+(.+?)"
            r"(?:\s+(?:на|в|через|using|on)\s+|[?.!]|$)",
            t, _re.IGNORECASE,
        )
        if not m:
            # Pattern 2: императив «установи/поставь/инсталлируй [мне/себе] X»
            m = _re.search(
                r"\b(?:установи|поставь|инсталлируй|скачай\s+и\s+установи)"
                r"(?:\s+(?:мне|себе|нам|пожалуйста|плиз|плз))*"
                r"\s+(.+?)"
                r"(?:\s+(?:на|в|через)\s+|[?.!]|$)",
                t, _re.IGNORECASE,
            )
        if not m:
            return ""
        target = m.group(1).strip().lower()
        # Снимаем ведущие «мне/себе/пожалуйста» если просочились через pattern 1
        target = _re.sub(
            r"^(?:мне|себе|нам|пожалуйста|плиз|плз|пж)\s+",
            "", target, flags=_re.IGNORECASE,
        ).strip()
        # Снимаем хвостовые слова-паразиты
        target = _re.sub(
            r"\s*(?:мою|свою|вашу|нашу)?\s*(?:систему|пк|компьютер|ноутбук|"
            r"машину|линукс|linux|тачку)\s*$",
            "", target, flags=_re.IGNORECASE,
        ).strip()
        # Защита от мусора
        if len(target) > 60 or len(target) < 2:
            return ""
        return target

    def _maybe_install_target(text: str) -> Optional[str]:
        """Если запрос — об установке, вернёт имя приложения. Иначе None.

        Используется для запуска install_workflow в обход LLM.
        Условия:
          • в тексте есть install-маркер (`_RE_INSTALL_QUERY`),
          • удалось извлечь target длиной >= 2 (`_extract_install_target`).

        Возвращает lowercase trimmed name или None.
        """
        if not _RE_INSTALL_QUERY.search(text or ""):
            return None
        target = _extract_install_target(text)
        if not target or len(target) < 2:
            return None
        return target

    def _local_package_lookup(text: str) -> str:
        """Search local repositories for the install target and return
        a compact list of real packages.

        Возвращает текст вроде:
            telegram-desktop 5.5.4-1: Official Telegram Desktop client
            64gram-desktop 5.5.4-1: Unofficial Telegram Desktop fork
        Или пустую строку если ничего не нашлось / репозиторий недоступен.
        """
        target = _extract_install_target(text)
        if not target:
            return ""

        # Кеш внутри сессии — pacman -Ss дешёвый, но повторять незачем.
        cached = _pkg_lookup_cache.get(target)
        if cached is not None:
            return cached

        try:
            from lina.system.package_manager import PackageManager
            pm = PackageManager()
            results = pm.search(target, limit=8)
        except Exception as e:
            logger.debug("PackageManager search failed: %s", e)
            _pkg_lookup_cache[target] = ""
            return ""

        if not results:
            _pkg_lookup_cache[target] = ""
            return ""

        lines = []
        for r in results[:6]:
            name = r.get("name", "")
            version = r.get("version", "")
            desc = (r.get("description") or "").strip()
            if len(desc) > 100:
                desc = desc[:97] + "…"
            line = f"  • {name}"
            if version:
                line += f" {version}"
            if desc:
                line += f": {desc}"
            lines.append(line)

        formatted = "\n".join(lines)
        _pkg_lookup_cache[target] = formatted
        logger.info(
            "Local package lookup: '%s' → %d results", target, len(results),
        )
        return formatted

    def _ensure_loaded() -> bool:
        """Загружает модель, если она не в памяти."""
        engine = _get_engine()
        if engine is None:
            return False
        if engine.is_loaded:
            return True
        logger.info("LLM модель не загружена — загружаю on-demand...")
        return engine.load("full")

    # ── Паттерны follow-up вопросов ──

    # Запрос про характеристики / спецификации (generic, не шаблон!)
    _RE_SPECS_QUERY = _re.compile(
        r"\b(?:характеристик\w*|спецификац\w*|параметр\w*"
        r"|specs|specifications|\u0442ех\.?\s*характеристик\w*)\b",
        _re.I,
    )

    # How-to / install / configure: запросы, где модель ДОЛЖНА использовать
    # свои знания о Linux в дополнение к веб-источникам. Здесь главная
    # ценность — не «найденные характеристики», а синтез: из обрывков
    # инструкций + знания пакетных менеджеров получить рабочие команды.
    _RE_HOWTO_QUERY = _re.compile(
        r"\b(?:как|как\s+(?:установить|поставить|настроить|собрать|"
        r"скачать|удалить|обновить|запустить|включить|выключить|"
        r"подключить|починить|исправить|сделать|использовать)|"
        r"подскажи\s+как|объясни\s+как|"
        r"how\s+to|install|setup|configure|"
        r"установка|настройка|инструкция|руководство|гайд|туториал)\b",
        _re.I,
    )

    # Запросы именно про УСТАНОВКУ — для них запускаем install_workflow.
    # Подмножество HOWTO + явные install-команды.
    _RE_INSTALL_QUERY = _re.compile(
        r"\b(?:"
        r"установ(?:и|ить|ишь)|"
        r"поставь|поставить|"
        r"инсталлируй|инсталлировать|"
        r"скачай\s+и\s+установ|"
        r"как\s+(?:мне\s+)?(?:установить|поставить|инсталлировать)|"
        r"подскажи\s*,?\s*как\s+(?:мне\s+)?(?:установить|поставить)|"
        r"how\s+(?:do\s+i\s+|to\s+)?install"
        r")\b",
        _re.I,
    )
    _FOLLOWUP_PATS = [
        _re.compile("^а\\s+(сколько|как|что|какой|какая|какое|какие|где|когда)\\b", _re.I),
        _re.compile("^(и\\s+ещё|ещё|также|а\\s+ещё)\\b", _re.I),
        _re.compile("^(а\\s+)?что\\s+(насчёт|на\\s*счёт|по\\s+поводу)\\b", _re.I),
        _re.compile("\\b(у\\s+него|у\\s+неё|его|её|их|этого|этой|этих|там)\\b", _re.I),
        # "Подробнее", "Более подробно", "Расскажи подробнее", "Детальнее", "Больше информации"
        _re.compile(
            r"(?:^|\b)(?:подробн\w*|поподробн\w*|детальн\w*|"
            r"расскажи\s+подробн\w*|расскажи\s+детальн\w*|"
            r"больше\s+информаци|более\s+подроб|"
            r"раскрой\s+тем\w*|разверни|\u0434ополни)",
            _re.I,
        ),
    ]

    def _is_web_followup(text: str) -> bool:
        """Проверяет, является ли запрос follow-up после web_search."""
        if _last_intent[0] != "web_search":
            return False
        q = text.strip()
        for pat in _FOLLOWUP_PATS:
            if pat.search(q):
                return True
        return False

    def _build_context(text: str, is_followup: bool = False, force_chat: bool = False):
        """Build full context: system + RAG + diagnostics + web search + install.

        Returns (context, executor, web_intent, web_result, intent).
        - web_intent: "weather_query"/"web" = direct answer, "web_search" = LLM context
        - web_result: summary string or None
        - intent: str — primary intent from IntentRouter
        - is_followup: caller already determined this is a follow-up to web_search
        - force_chat: skip intent router (auto-repair, internal LLM-only turn)
        """
        # Auto-repair turns are an internal LLM-only path: force chat intent,
        # bypass router (it might mis-route them to web_search/system_command).
        is_repair_turn = force_chat or text.startswith("[LINA-REPAIR]")
        if text.startswith("[LINA-REPAIR]"):
            text = text[len("[LINA-REPAIR]"):].lstrip()

        # Compute intent ONCE for the entire pipeline
        intent = "chat"
        decision = None
        try:
            nonlocal _intent_router
            if _intent_router is None:
                with _init_lock:
                    if _intent_router is None:
                        from lina.core.intent_router import IntentRouter
                        _intent_router = IntentRouter()
            decision = _intent_router.route(text)
            intent = decision.intent.value if hasattr(decision.intent, 'value') else str(decision.intent)
        except Exception as e:
            logger.debug("IntentRouter error: %s", e)

        # Auto-repair turns force chat — even if router thought "web_search"
        if is_repair_turn:
            intent = "chat"
            decision = None
            logger.debug("Auto-repair turn: forced intent=chat")

        # ── Follow-up detection: после web_search перенаправляем в web_search ──
        # Use the flag from caller (who checked original un-enriched text),
        # because _enrich_followup may add [Контекст:...] prefix that
        # breaks ^-anchored follow-up patterns.
        # ВАЖНО: для repair-turn'ов follow-up НЕ применяем — иначе ошибка
        # терминала уйдёт в web_search вместо локального LLM-исправления.
        _is_followup = is_followup and not is_repair_turn
        if _is_followup:
            if intent != "web_search":
                logger.info("GUI follow-up detected: %s → web_search (prev was web_search)", intent)
            intent = "web_search"
            # Извлечь полную тему из предыдущего запроса в истории
            try:
                history = _get_history(max_pairs=1)
                if history:
                    prev_user = history[-1][0]
                    from lina.core.main_pipeline import MainPipeline
                    topic = MainPipeline._extract_topic(prev_user)
                    if topic:
                        # Убрать [Контекст:...] если есть, заменить на полный топик
                        clean_q = _re.sub(r"^\[Контекст:\s*[^\]]+\]\s*", "", text).strip()
                        # Strip conversational noise from follow-up:
                        # "А что по процессору?" → "процессор"
                        clean_q = _re.sub(
                            r"^(?:а\s+)?(?:что\s+)?(?:по|насчёт|на\s*счёт|про|о|об)\s+",
                            "", clean_q, flags=_re.IGNORECASE,
                        ).strip()
                        clean_q = _re.sub(
                            r"^(?:а\s+)?(?:какой|какая|какое|какие|сколько|где|как)\s+",
                            "", clean_q, flags=_re.IGNORECASE,
                        ).strip()
                        # Remove trailing question mark
                        clean_q = clean_q.rstrip("?").strip()
                        text = f"{topic} {clean_q}"
                        logger.info("Follow-up web query: '%s'", text[:80])
            except Exception as e:
                logger.debug("Follow-up topic extraction error: %s", e)

        diag_report = _try_diagnostics(text)

        # Web search only if intent is web-related
        web_intent, web_result = _try_web_search(text, intent)

        # Install search only if intent matches
        install_info = None
        if intent == "install_application" and not web_result:
            install_info = _try_install_search(text, intent, decision)

        # ── Intent-aware context building ──
        # For chat/knowledge queries: skip system snapshot (CPU, RAM, etc.)
        # to give the small LLM more room for actual answer generation.
        # System snapshot is only useful for system_command/diagnostic intents.
        # EXCEPTION: if user asks about "their system" in chat intent, inject snapshot.
        _SYSTEM_INTENTS = {
            "system_command", "install_application", "system_diagnostic",
            "open_application", "tool_explicit",
        }
        sys_ctx, preprocessor, executor = _get_system_context()

        # Detect system-adjacent queries even when classified as "chat"
        # NEVER inject system context for web_search — prevents CPU/RAM leak
        # EXCEPTION: gaming compatibility queries need BOTH system specs AND web results
        _SYS_MENTION = _RE_SYS_MENTION
        _is_gaming_query = (
            decision and hasattr(decision, 'metadata') and decision.metadata
            and decision.metadata.get("gaming_query")
        )
        _is_sys_query = (
            intent in _SYSTEM_INTENTS
            or _is_gaming_query
            or (intent != "web_search" and bool(_SYS_MENTION.search(text)))
        )

        if _is_sys_query:
            full_context = sys_ctx or ""
            if preprocessor:
                enrichment = preprocessor.enrich_for_llm(text)
                if enrichment:
                    full_context = full_context + "\n" + enrichment
        else:
            # For chat/knowledge — no system snapshot, keep executor for safety
            full_context = ""

        # ── Proactive grounding для how-to запросов ─────────────────
        # Если пользователь спрашивает «как установить/настроить X» —
        # модель ОБЯЗАНА знать дистрибутив и пакетный менеджер ДО ответа,
        # иначе она угадывает (sudo apt-get на Arch — типичная ошибка).
        # Поэтому в начало контекста жёстко прибиваем мини-карточку
        # «что у пользователя» — без полного снимка, чтобы не утекло в ответ.
        # Это работает даже для intent=web_search.
        is_howto_query = bool(_RE_HOWTO_QUERY.search(text))
        if is_howto_query:
            try:
                from lina.utils.distro import get_cached_distro
                d = get_cached_distro()
                if d and getattr(d, "is_known", False):
                    pkg = d.package_manager or "?"
                    pretty = getattr(d, "pretty_name", None) or d.name or "Linux"
                    fact_card = (
                        f"[ИЗВЕСТНЫЕ ФАКТЫ О СИСТЕМЕ ПОЛЬЗОВАТЕЛЯ]\n"
                        f"Дистрибутив: {pretty}\n"
                        f"Пакетный менеджер: {pkg}\n"
                        f"Это обязательный контекст. Команды ДОЛЖНЫ "
                        f"использовать {pkg}. Команды чужих менеджеров "
                        f"(не {pkg}) — НЕ ПИСАТЬ, они не сработают.\n"
                        f"---\n"
                    )
                    full_context = fact_card + (full_context or "")
            except Exception as e:
                logger.debug("Howto distro fact card skipped: %s", e)

        # ── Реальные пакеты из локальных репозиториев ─────────────
        # Если how-to запрос на установку — ищем имя приложения и делаем
        # `pacman -Ss <name>`. Подмешиваем результаты в контекст как
        # «реально доступные пакеты». Без этого модель фантазирует имена
        # пакетов («pipx install --system claude-code», «git-claude-code»).
        if is_howto_query:
            try:
                pkg_results = _local_package_lookup(text)
                if pkg_results:
                    full_context = (
                        f"[ПАКЕТЫ В РЕПОЗИТОРИЯХ ВАШЕЙ СИСТЕМЫ]\n"
                        f"{pkg_results}\n"
                        f"Используй ТОЛЬКО эти точные имена пакетов в "
                        f"командах установки. НЕ выдумывай и НЕ изменяй "
                        f"имена.\n"
                        f"---\n"
                        + (full_context or "")
                    )
            except Exception as e:
                logger.debug("Local package lookup skipped: %s", e)

        # Skip RAG for web_search — KB contains Linux docs, not product specs.
        # Injecting irrelevant RAG causes LLM to hallucinate (e.g. Raspberry Pi
        # content mixed with phone specs).
        if intent != "web_search":
            retriever = _get_retriever()
            if retriever:
                try:
                    rag_context = retriever.build_context(
                        query=text, max_context_length=800,
                    )
                    if rag_context:
                        full_context = full_context + "\n" + rag_context
                except Exception as e:
                    logger.debug("RAG search error: %s", e)

        if diag_report:
            full_context = (
                full_context
                + "\n\n[Результат автодиагностики]\n"
                + diag_report
            )

        # web_search → inject into LLM context for summarization
        # weather_query/web → direct answer, no LLM context needed
        if web_result and web_intent == "web_search":
            # Search failed — inject refusal instruction directly
            if "[Результаты веб-поиска не найдены]" in web_result:
                full_context = full_context + "\n\n" + web_result
            # Если fact pipeline вернул структурированные факты —
            # используем их напрямую (FACT MODE prompt активируется автоматически)
            elif "[ПРОВЕРЕННЫЕ ФАКТЫ:" in web_result:
                full_context = full_context + "\n\n" + web_result
            else:
                # Raw summary: очищаем от эмодзи и raw-ссылок перед инъекцией
                clean = web_result
                for emoji in ("🔍", "🔗", "📄"):
                    clean = clean.replace(emoji, "")
                # Убираем голые URL-строки (http...)
                clean = _re.sub(
                    r'^\s*https?://\S+\s*$', '', clean, flags=_re.MULTILINE,
                )
                # ── Hybrid mode для how-to / install / configure ─────
                # Здесь МОЖНО (и нужно) использовать собственные знания
                # модели о Linux — пакетные менеджеры, типовые шаги.
                # Веб-источники служат подсказкой про конкретный софт,
                # а команды модель собирает сама. Это снимает кейс типа
                # «Kiro IDE» — даже если сам туториал не нашёлся, модель
                # знает как ставить .tar.gz / AppImage / AUR-пакет.
                is_howto = bool(_RE_HOWTO_QUERY.search(text))
                if is_howto:
                    full_context = (
                        full_context
                        + "\n\n[ИНСТРУКЦИЯ ДЛЯ HOW-TO ЗАПРОСА]\n"
                        + "Ниже — обрывки из веб-поиска. Используй их как "
                        + "ПОДСКАЗКУ про конкретный продукт (название, формат "
                        + "поставки, ссылка на репозиторий, AUR-пакет).\n"
                        + "Команды установки/настройки СОБИРАЙ САМ "
                        + "из своих знаний о Linux — пакетный менеджер "
                        + "уже определён в секции СИСТЕМА.\n"
                        + "Если из веб-поиска понятно что софт ставится "
                        + "из tar.gz / AppImage / .deb / AUR — дай рабочие "
                        + "команды для текущего дистрибутива.\n"
                        + "Если из веб-поиска вообще непонятно что это "
                        + "за софт — честно скажи и предложи "
                        + "общий способ (поиск в репозитории, AUR, GitHub).\n"
                        + "НЕ копируй URL и названия сайтов из подсказки.\n"
                        + "Команды оборачивай в ```bash блоки.\n"
                        + "---\n"
                        + clean.strip()
                    )
                else:
                    full_context = (
                        full_context
                        + "\n\n[Результаты веб-поиска — используй ТОЛЬКО эти данные для ответа. "
                        + "Перескажи найденные факты СВОИМИ словами, кратко и по делу. "
                        + "НЕ копируй ссылки. НЕ показывай URL. НЕ показывай названия сайтов. "
                        + "НЕ ВЫДУМЫВАЙ характеристики, числа, модели, цены, даты. "
                        + "Если в результатах НЕТ конкретных данных про устройство — "
                        + "ответь: 'К сожалению, мне не удалось найти достоверную информацию.' "
                        + "НЕ пиши 'процессор неизвестен', 'экран неизвестен' — это выдумка. "
                        + "Если данных мало — перечисли что нашёл, не додумывай.]\n"
                        + clean.strip()
                    )

        # install_application → real package search results for LLM
        if install_info:
            full_context = (
                full_context
                + "\n\n[Реальные результаты поиска пакетов — "
                + "используй ТОЛЬКО эти данные для команд установки. "
                + "НЕ выдумывай несуществующие пакеты.]\n"
                + install_info
            )

        # Gaming compatibility: inject comparison task for LLM
        if _is_gaming_query and web_result and sys_ctx:
            full_context += (
                "\n\n[ЗАДАЧА: Сравни системные требования игры из веб-поиска "
                "с характеристиками компьютера пользователя из секции СИСТЕМА. "
                "Дай КОНКРЕТНЫЙ вердикт: потянет или нет, на каких настройках "
                "(низкие/средние/высокие/ультра). Укажи узкие места (если есть). "
                "НЕ выдумывай модель компьютера.]"
            )

        return full_context, executor, web_intent, web_result, intent, decision

    def _save_last_intent(intent: str, query: str = "") -> None:
        """Сохранить последний intent для follow-up detection."""
        _last_intent[0] = intent

        # Update ConversationState (multi-turn)
        cs = _get_conversation_state()
        if cs:
            try:
                # Извлечь тему и сущности для стека
                topic = ""
                entities = []
                try:
                    from lina.core.entity_parser import get_entity_parser
                    parsed = get_entity_parser().parse(query)
                    topic = parsed.device or parsed.brand or ""
                    entities = [e.value for e in parsed.entities[:5]]
                except Exception:
                    pass
                if not topic and query:
                    from lina.core.main_pipeline import MainPipeline
                    topic = MainPipeline._extract_topic(query)
                cs.push(intent=intent, query=query, topic=topic, entities=entities)
            except Exception as e:
                logger.debug("ConversationState push error: %s", e)

    def _execute_commands(response: str, executor, intent: str = "") -> str:
        """Extract bash commands from LLM response and execute them.

        ONLY executes for system-related intents. For chat/knowledge
        queries, bash blocks are stripped to prevent hallucinated commands.
        """
        if not executor:
            return response

        # Intents where command execution is allowed
        _EXEC_INTENTS = {
            "system_command", "install_application", "system_diagnostic",
            "tool_explicit",
        }
        # Repair-turn'ы и явные how-to install не должны терять bash-блоки:
        # пользователь нажмёт «Выполнить» в CommandActionBar.
        _is_repair_response = bool(_re.search(
            r"(?:```bash|```sh|sudo\s+(?:pacman|apt|dnf|zypper|yay|paru))",
            response,
        )) and not (intent and intent in _EXEC_INTENTS)
        if intent and intent not in _EXEC_INTENTS and not _is_repair_response:
            # Strip ```bash blocks from response for non-system intents
            # so user doesn't see garbage commands
            cleaned = _re.sub(
                r'```(?:bash|sh|shell|console|zsh|fish)?[\s]*\n?.*?```',
                '', response, flags=_re.DOTALL | _re.IGNORECASE,
            ).strip()
            # Remove trailing empty lines
            cleaned = _re.sub(r'\n{3,}', '\n\n', cleaned)
            if cleaned:
                return cleaned
            # LLM produced ONLY bash blocks with no text — return generic msg
            logger.warning("LLM produced only bash blocks for chat intent, dropping")
            return response  # gui shows the raw response, user at least sees something

        try:
            from lina.core.system_interaction import extract_commands
            commands = extract_commands(response)
            # Filter out echo/printf that just repeat the text answer
            # (LLM sometimes wraps its answer as `echo "answer"`)
            commands = [
                c for c in commands
                if not _re.match(
                    r'^(echo|printf)\s+["\']|'     # echo "text answer"
                    r'^(google|search|browse|bing|yandex|firefox|chrome)\s',
                    c.command, _re.I,
                )
            ]
            if commands:
                exec_results = executor.execute_many(commands)
                exec_output = []
                for res in exec_results:
                    if res.skipped:
                        exec_output.append(res.reason)
                    elif res.success:
                        if res.stdout:
                            exec_output.append(
                                f"$ {res.command}\n{res.stdout}")
                        else:
                            exec_output.append(f"✅ {res.command}")
                    else:
                        exec_output.append(
                            f"❌ {res.command}\n"
                            f"{res.stderr or res.reason}")

                if exec_output:
                    response += (
                        "\n\n📋 Результат выполнения:\n"
                        + "\n".join(exec_output)
                    )
        except Exception as e:
            logger.warning("Command extraction/execution error: %s", e)
        return response

    def _get_history(max_pairs: int = 3) -> list:
        """Extract recent chat history as [(user_msg, assistant_msg), ...] pairs.

        Gives LLM context about previous conversation turns
        so it can handle follow-ups like 'ещё добавь' correctly.
        """
        try:
            messages = controller.export_history()
            pairs = []
            i = 0
            while i < len(messages) - 1:
                if messages[i]["role"] == "user":
                    user_msg = messages[i]["content"]
                    assistant_msg = ""
                    if i + 1 < len(messages) and messages[i + 1]["role"] == "assistant":
                        assistant_msg = messages[i + 1]["content"]
                        i += 2
                    else:
                        i += 1
                    # Skip placeholder / error messages
                    if "⏳ Думаю" in assistant_msg or "❌ Ошибка" in assistant_msg:
                        continue
                    pairs.append((user_msg, assistant_msg))
                else:
                    i += 1
            # Return only the last N pairs to fit in context
            return pairs[-max_pairs:]
        except Exception:
            return []

    def _is_llm_deflecting(response: str) -> bool:
        """Detect if LLM suggests googling or deflects to Linux topic."""
        deflection_patterns = [
            r'(используй|попробуй|можешь|можно)\s.{0,20}(поисков|google|гугл|яндекс)',
            r'(загугл|погугл|найди\s+в\s+(гугл|интернет|сети))',
            r'(рекомендую|предлагаю|советую)\s.{0,15}(поиск|найти|google)',
            r'поисков(ик|ую\s+систему)',
            r'google\s*[:.]',
            r'можно\s+(найти|узнать)\s+(в\s+интернете|в\s+сети|через\s+поиск)',
            # Deflection to Linux topic when question is not about Linux
            r'лин.{0,3}x?\s*—?\s*это\s+(открыт|операционн|бесплатн)',
            r'не\s+(имеет|относит).{0,30}(linux|линукс|arch|пакет)',
            r'не\s+связан\w*\s+с\s+(linux|линукс)',
        ]
        text_lower = response.lower()
        return any(_re.search(p, text_lower) for p in deflection_patterns)

    def _is_vague_answer(response: str, query: str) -> bool:
        """Detect if LLM gave a vague non-answer that lacks specific facts.

        Catches patterns like 'это компания по производству авто' without
        saying WHO owns it, WHEN it was founded, etc.
        """
        r = response.strip().lower()
        # Too short = likely doesn't contain real info
        if len(r) < 15:
            return True
        # Detect known vague filler patterns
        vague_patterns = [
            # "это компания, занимающаяся..." without specifics
            r'^\S+\s+(является|—\s*это)\s+компани',
            # "не могу ответить", "не могу найти"
            r'не\s+могу\s+(ответить|найти|определить)',
            # "не располагаю информацией" / "нет информации"
            r'(не\s+располагаю|нет\s+информации|не\s+знаю\s+точно)',
        ]
        # Only trigger for knowledge-type queries ("chey", "komu", "kto")
        q = query.lower()
        is_factual_query = _re.search(
            r'(чь[еёийм]|\bкому\b|\bкто\b|\bкако\w+\s+концерн|\bпринадлеж'
            r'|\bкак(?:ие|ой|ая|ое)\s+(?:параметр|характеристик|спецификаци)\w*'
            r'|\bхарактеристик\w*\s+\S+)', q
        )
        if not is_factual_query:
            return False
        return any(_re.search(p, r) for p in vague_patterns)

    def _is_generic_hallucination(response: str) -> bool:
        """Детектор маркетингового бреда и повторов без конкретных данных.

        Ловит ДВА типа галлюцинаций:
        1. Маркетинговый бред без чисел + много общих фраз
        2. Дегенеративные повторы: одна фраза повторяется 3+ раз
           (напр. "Диапазон глубины: 1200 мкм; Диапазон глубины дизайн: 1200 мкм")

        Returns True if the response looks like hallucination.
        """
        r = response.strip()
        if not r:
            return True

        # ── Check 1: Repetitive degeneration ──
        # LLM looping: same 3+ word phrase appears 3+ times
        words = r.lower().split()
        if len(words) >= 12:
            # Extract 4-grams and count repetitions
            ngram_counts: dict = {}
            for i in range(len(words) - 3):
                ng = " ".join(words[i:i + 4])
                ngram_counts[ng] = ngram_counts.get(ng, 0) + 1
            max_repeat = max(ngram_counts.values()) if ngram_counts else 0
            if max_repeat >= 3:
                logger.info(
                    "Hallucination detector: repetitive degeneration "
                    "(4-gram repeated %d times)", max_repeat,
                )
                return True

            # Also check for semi-colon-separated repeated key:value pairs
            # e.g. "ЧПУ: 90-герц; Диапазон: 1200; ЧПУ: 90-герц; Диапазон: 1200"
            kv_pattern = _re.findall(r'([А-Яа-яA-Za-z]{3,}[\w\s]{0,20}:\s*[\d.,]+)', r)
            if len(kv_pattern) >= 4:
                from collections import Counter as _Counter
                kv_counts = _Counter(kv_pattern)
                if kv_counts.most_common(1)[0][1] >= 3:
                    logger.info(
                        "Hallucination detector: repeated key:value pairs (%d dups)",
                        kv_counts.most_common(1)[0][1],
                    )
                    return True

        # ── Check 2: Generic marketing text (original) ──
        # Есть числа (ГБ, МГц, МАч, дюймов, Вт etc.) → не галлюцинация
        has_numbers = bool(_re.search(
            r'\d+\s*(?:гб|gb|мач|mah|mhz|мгц|ghz|ггц|вт|w|дюйм|тб|tb|mp|мп|мм|mm|грамм|bit|бит|px|cuda)',
            r, _re.I,
        ))
        if has_numbers:
            return False

        # Количество generic-фраз (маркетинговый мусор)
        _GENERIC_PHRASES = [
            r'высок\w*\s+производительност',
            r'поддерж\w*\s+технологи',
            r'улучшенн\w*\s+рендеринг',
            r'повышенн\w*\s+энергоэффективност',
            r'обеспечива\w*\s+.{0,20}производительност',
            r'больш\w*\s+производительност',
            r'надёжн\w*\s+.{0,15}производительност',
            r'современн\w*\s+технолог\w*',
            r'предлага\w*\s+.{0,20}производительност',
            r'ускорен\w*\s+рендеринг',
            r'улучшен\w*\s+(?:аудио|видео|график)',
            r'различн\w*\s+графическ\w*\s+приложен',
            r'монограф\w*',  # complete nonsense word
        ]
        r_lower = r.lower()
        generic_count = sum(1 for p in _GENERIC_PHRASES if _re.search(p, r_lower))
        # Если ≥ 3 маркетинговых фраз без единого числа → галлюцинация
        return generic_count >= 3

    def _clean_web_for_context(summary: str) -> str:
        """Strip emojis and URLs from web summary for LLM context."""
        clean = summary
        for emoji in ("\U0001f50d", "\U0001f517", "\U0001f4c4"):
            clean = clean.replace(emoji, "")
        clean = _re.sub(r'^\s*https?://\S+\s*$', '', clean, flags=_re.MULTILINE)
        return clean.strip()

    def _web_search_fallback(text: str, full_context: str):
        """Web search fallback when LLM deflects. Returns enhanced context or None."""
        ws = _get_web_search()
        if not ws:
            return None
        logger.info("LLM deflected → web search fallback: %s", text[:60])
        web_resp = ws.search(text)
        if web_resp.success and web_resp.summary:
            clean = _clean_web_for_context(web_resp.summary)
            enhanced = (
                full_context
                + "\n\n[Результаты веб-поиска — ответь КРАТКО своими словами. "
                + "НЕ копируй ссылки. НЕ показывай URL. "
                + "НЕ выдумывай факты. НЕ предлагай гуглить.]\n"
                + clean
            )
            return enhanced
        return None

    # ── Install-intent helpers ──

    _GARBAGE_INSTALL_WORDS = frozenset({
        "и", "а", "но", "или", "для", "в", "на", "из", "с", "по",
        "через", "потом", "тоже", "ещё", "еще", "показывай", "покажи",
        "логи", "лог", "мне", "консоль", "консоли", "используя",
        "все", "всё", "его", "её", "их", "только",
    })

    # ── Truncation detection helper ──

    _SENTENCE_ENDINGS = frozenset('.!?»")\']…—;:')

    def _fix_truncated_response(response: str) -> str:
        """Detect mid-sentence cutoff and append ellipsis.

        When the mini model runs out of tokens, the response ends
        abruptly like 'Если вы' — detect this and add '…' to signal
        the user that the answer was truncated.
        """
        if not response:
            return response
        text = response.rstrip()
        if not text:
            return response
        # Check if response ends with a code block (```), don't touch
        if text.endswith("```"):
            return response
        last = text[-1]
        if last not in _SENTENCE_ENDINGS and last not in '0123456789%°' and not text.endswith("```"):
            logger.info("Response appears truncated (ends with '%s'), appending ellipsis", last)
            return text + "…"
        return response

    def _is_garbage_install_target(app_name: str) -> bool:
        """Return True if captured install target is not a real app name.

        E.g. 'и показывай мне логи' — not an app name.
        """
        if not app_name:
            return True
        words = app_name.lower().split()
        # If first word is a Russian stop-word, it's garbage
        if words and words[0] in _GARBAGE_INSTALL_WORDS:
            return True
        # If ALL words are stop/noise words
        real_words = [w for w in words if w not in _GARBAGE_INSTALL_WORDS]
        if not real_words:
            return True
        return False

    def _find_install_app_in_history() -> Optional[str]:
        """Check recent history for an install-related query and extract app name."""
        try:
            history = _get_history(max_pairs=3)
            if not history:
                return None
            # Import install patterns locally
            install_pats = [
                _re.compile(r"установ\w+\s+(?:мессенджер\w*\s+|клиент\w*\s+|браузер\w*\s+|редактор\w*\s+|плеер\w*\s+)?(\S+)", _re.I),
                _re.compile(r"(?:install|setup)\s+(\S+)", _re.I),
                _re.compile(r"как\s+\w*\s*установить\s+(?:мессенджер\w*\s+|клиент\w*\s+)?(\S+)", _re.I),
            ]
            for user_msg, _assistant_msg in reversed(history):
                for pat in install_pats:
                    m = pat.search(user_msg)
                    if m:
                        candidate = m.group(1).strip().lower()
                        if candidate and candidate not in _GARBAGE_INSTALL_WORDS and len(candidate) >= 2:
                            logger.info("Found install app in history: '%s'", candidate)
                            return candidate
        except Exception:
            pass
        return None

    def _enrich_followup(text: str) -> str:
        """Enrich follow-up queries by injecting subject from history.

        When user says «А какому концерну принадлежит» after asking about Bugatti,
        extract 'Bugatti' from previous exchange and prepend it:
        → «[Контекст: Bugatti] А какому концерну принадлежит»

        This helps the LLM connect the follow-up to the original topic,
        especially for small models with limited context understanding.
        """
        q = text.strip()
        # Only enrich short follow-ups with anaphora
        if len(q) > 80:
            return text  # Long query is self-contained

        # Check if this looks like a follow-up (pronouns, anaphora, "подробнее")
        if not _re.search(
            r'^а\s+|'
            r'\b(она|он|оно|они|ему|ей|его|её|их|ней|нему|ним|них)\b|'
            r'\b(у\s+нег[ао]|у\s+не[ей]|у\s+них)\b|'
            r'\b(этот|этой|этого|этих|эту|этим|этому)\b|'
            r'\b(чей|чья|чьё|чьи|чьей|чьим|чьем|чьему)\b|'
            r'\b(какому|какой|какое|какие|каким)\b.*\b(принадлеж|концерн|корпорац|бренд|марк)|'
            r'(?:подробн|поподробн|детальн|больше\s+информац|более\s+подробн|дополни)|'
            r'^(кому|чему)\b',
            q, _re.I,
        ):
            return text  # Not a follow-up

        # If the query already contains its own named entity subject,
        # don't inject historical context — the query is self-contained.
        own_entities = _re.findall(
            r'\b([A-ZА-ЯЁ][a-zа-яё]{2,}(?:\s+[A-ZА-ЯЁ][a-zа-яё]+)*)\b'
            r'|\b([A-Z]{2,6})\b',
            q,
        )
        _skip_words = {"Скажи", "Расскажи", "Найди", "Покажи", "Открой",
                       "Привет", "Пожалуйста", "Что", "Как", "Кто", "Где",
                       "Когда", "Почему", "Какой", "Какая", "Какие",
                       "Lina", "Linux", "Это", "Это"}
        own_subjects = [
            (c[0] or c[1]) for c in own_entities
            if (c[0] or c[1]) and (c[0] or c[1]) not in _skip_words
        ]
        if own_subjects:
            # Query already has a named entity (e.g., "Чей автомобиль Changan")
            # — it's self-contained, no enrichment needed
            return text

        # Extract subject from last history exchange
        try:
            history = _get_history(max_pairs=2)
            if not history:
                return text

            last_user, last_assistant = history[-1]
            candidates = _re.findall(
                r'\b([A-ZА-ЯЁ][a-zа-яё]{2,}(?:\s+[A-ZА-ЯЁ][a-zа-яё]+)*)\b'
                r'|\b([A-Z]{2,6})\b',
                last_user,
            )
            candidates = [c[0] or c[1] for c in candidates if c[0] or c[1]]
            if not candidates:
                found = _re.findall(
                    r'\b([A-ZА-ЯЁ][a-zа-яё]{2,})\b|\b([A-Z]{2,6})\b',
                    last_assistant[:200],
                )
                candidates = [c[0] or c[1] for c in found if c[0] or c[1]]
            if not candidates:
                return text

            subject = None
            for c in candidates:
                if c not in _skip_words:
                    subject = c
                    break
            if not subject:
                return text

            enriched = f"[Контекст: {subject}] {text}"
            logger.info("Follow-up enriched: '%s' → '%s'", text[:40], enriched[:60])
            return enriched
        except Exception:
            return text

    def _handler(text: str) -> str:
        """Full LLM pipeline: preprocessor → diagnostics → RAG → web → classify → LLM → execute."""

        # Strip [LINA-REPAIR] sentinel so it never reaches the model.
        is_repair = text.startswith("[LINA-REPAIR]")
        if is_repair:
            text = text[len("[LINA-REPAIR]"):].lstrip()

        # ── Install workflow: автономная установка пакета ────────────
        # Запросы вида «установи telegram», «как установить gimp» —
        # отдаём GUI sentinel, main_window запустит InstallWorkflow.
        # Workflow сам всё сделает: поиск пакета, sudo pacman -S,
        # проверка, авто-фикс ошибок, верификация бинаря.
        if not is_repair:
            install_target = _maybe_install_target(text)
            if install_target:
                logger.info("Install workflow trigger: target='%s'", install_target)
                return f"[LINA-INSTALL]{install_target}"

        # ── Fast-path: direct answers without LLM ──
        # Skip fast-path for:
        #   1) follow-up to web_search (e.g. "А какой процессор" after specs query)
        #   2) auto-repair turns (force LLM path)
        _skip_fast = _is_web_followup(text) or is_repair
        if not _skip_fast:
            try:
                _, pp, _ = _get_system_context()
                if pp:
                    direct = pp.try_direct_answer(text)
                    if direct:
                        logger.info("Direct answer (non-streaming): %s…", direct[:60])
                        return direct
            except Exception as e:
                logger.debug("QueryPreprocessor fast-path error: %s", e)

        engine = _get_engine()
        if not engine:
            return ("⚠ Не удалось создать LLM-движок.\n"
                    "Проверьте установку llama-cpp-python.")

        # Enrich follow-up queries with subject from history
        enriched_text = _enrich_followup(text)

        full_context, executor, web_intent, web_result, intent, decision = _build_context(enriched_text, is_followup=_skip_fast, force_chat=is_repair)
        _save_last_intent(intent, query=text)

        # ── Direct web results (weather/currency) — skip LLM ──
        # web_search goes through LLM for summarization (context is clean:
        # no RAG, no system snapshot — only web results).
        if web_result and web_intent in ("weather_query", "web"):
            logger.info("Direct web result (%s), skipping LLM", web_intent)
            return web_result

        # ── SPECS MODE: facts extracted → direct output, NO LLM ──
        if web_result and isinstance(web_result, str) and web_result.startswith("[DIRECT_FACTS]"):
            logger.info("SPECS MODE: returning extracted facts directly (no LLM)")
            return web_result[len("[DIRECT_FACTS]"):]

        # ── Web search with NO results — template response, skip LLM ──
        # Small local LLMs inevitably hallucinate specs from thin air.
        # Honest refusal is more reliable than fabricated data.
        if intent == "web_search" and not web_result:
            from lina.core.main_pipeline import MainPipeline
            # For terminal error queries, extract the error — not the entire dump
            if "\n" in text or _RE_PS1_LINE.search(text):
                topic = _extract_search_from_terminal(text).rstrip(" linux")
            else:
                topic = MainPipeline._extract_topic(text)
            if topic:
                return (
                    f"К сожалению, мне не удалось найти информацию "
                    f"о «{topic}» в интернете. Попробуйте:"
                    f"\n  • уточнить запрос (например: «{topic} характеристики»)"
                    f"\n  • проверить подключение к сети"
                    f"\n  • повторить запрос чуть позже"
                )
            return (
                "К сожалению, не удалось найти информацию в интернете. "
                "Попробуйте уточнить запрос или повторить позже."
            )

        # ── Datetime fast-path — instant answer without LLM ──
        if _RE_DATETIME_QUICK.search(text):
            from lina.core.tools import ToolRegistry
            dt_result = ToolRegistry._tool_datetime(text)
            if dt_result.success:
                return dt_result.output

        # ── Open app fast-path — use ApplicationResolver, skip LLM ──
        if intent == "open_application":
            app_name = ""
            if decision and hasattr(decision, 'metadata') and decision.metadata:
                app_name = decision.metadata.get("app_name", "")
            if app_name:
                try:
                    from lina.core.tools import ToolRegistry
                    _reg = ToolRegistry()
                    result = _reg._tool_open_app(app_name)
                    return result.output
                except Exception as e:
                    logger.warning("open_app fast-path error: %s", e)

        # ── Install app fast-path — real package search, skip LLM hallucinations ──
        if intent == "install_application":
            app_name = ""
            if decision and hasattr(decision, 'metadata') and decision.metadata:
                app_name = decision.metadata.get("app_name", "")
            # If captured app_name is garbage (sentence fragment), check history
            if not app_name or _is_garbage_install_target(app_name):
                hist_app = _find_install_app_in_history()
                if hist_app:
                    app_name = hist_app
                    # Update decision metadata so _try_install_search uses corrected name
                    if decision and hasattr(decision, 'metadata') and decision.metadata:
                        decision.metadata["app_name"] = hist_app
            if app_name:
                install_result = _try_install_search(enriched_text, intent, decision)
                if install_result:
                    logger.info("Install fast-path: skipping LLM, returning package search")
                    return _execute_commands(install_result, executor, intent)

        history = _get_history()

        # ── Call LLM ──
        try:
            with _engine_lock:
                response = engine.generate(
                    query=text, context=full_context, history=history,
                    intent=intent)
        except Exception as e:
            logger.warning("LLM error: %s — retrying with reload", e)
            try:
                with _engine_lock:
                    engine.unload()
                    engine.load("full")
                    response = engine.generate(
                        query=text, context=full_context, history=history,
                        intent=intent)
            except Exception as e2:
                logger.error("LLM retry failed: %s", e2)
                return f"⚠ Ошибка LLM: {e2}"

        # ── Detect deflection OR vague answer → web search fallback ──
        # ANTI-HALLUCINATION: NEVER re-search for web_search intent.
        # If the LLM says "I don't know" after receiving verified facts
        # (or a refusal marker), that IS the correct answer.
        # Re-searching and re-generating is what causes fabricated specs.
        # Также НЕ запускаем web-fallback на коротких follow-up'ах вроде
        # «Давай ещё раз» — короткий пользовательский ввод не имеет смысла
        # искать в интернете.
        _is_short_followup = len(text.strip()) < 25
        if intent != "web_search" and not _is_short_followup and (
            _is_llm_deflecting(response) or _is_vague_answer(response, text)
        ):
            enhanced = _web_search_fallback(text, full_context)
            if enhanced:
                try:
                    with _engine_lock:
                        response = engine.generate(
                            query=text, context=enhanced, history=history,
                            intent=intent)
                except Exception as e:
                    logger.warning("Web fallback re-gen failed: %s", e)
                    if enhanced and isinstance(enhanced, str):
                        response = enhanced

        # ── Anti-Hallucination Guard: verify LLM claims against facts ──
        if intent == "web_search" and _last_fact_set[0] and _last_fact_set[0].facts:
            try:
                # Детекция generic-галлюцинации: если LLM выдала маркетинговый
                # бред без чисел — заменяем на форматированные факты
                if _is_generic_hallucination(response):
                    logger.warning(
                        "Anti-hallucination: generic marketing text detected, "
                        "replacing with extracted facts (%d facts)",
                        len(_last_fact_set[0].facts),
                    )
                    response = _last_fact_set[0].format_for_user()
                else:
                    fp = _get_fact_pipeline()
                    if fp:
                        cleaned, removed = fp.check_answer(response, _last_fact_set[0])
                        if removed:
                            logger.info(
                                "Anti-hallucination: removed %d unsupported claims",
                                len(removed),
                            )
                            response = cleaned

                    # Add confidence warning if low
                    from lina.core.fact_pipeline import ConfidenceScorer
                    warning = ConfidenceScorer.format_warning(
                        _last_fact_set[0].confidence
                    )
                    if warning:
                        response = response.rstrip() + "\n\n" + warning
            except Exception as e:
                logger.debug("Anti-hallucination guard error: %s", e)

        # ── Empty / garbage response → retry with minimal context ──
        # ANTI-HALLUCINATION: for web_search, empty response is CORRECT
        # when no facts were found.  Do NOT retry with context="" —
        # that forces the LLM to fabricate from parametric memory.
        clean_resp = response.strip() if response else ""
        if not clean_resp or len(clean_resp) < 5:
            if intent == "web_search":
                # Empty response for web_search = correct refusal.
                # Return a clean user-facing message.
                response = (
                    "К сожалению, не удалось найти достоверную информацию "
                    "в интернете. Попробуйте уточнить запрос или повторить позже."
                )
            else:
                logger.warning("Empty/garbage LLM response, retrying with minimal context")
                try:
                    with _engine_lock:
                        response = engine.generate(
                            query=text, context="", history=history,
                            intent=intent)
                except Exception:
                    pass
            # If retry also empty, return user-friendly error
            retry_resp = response.strip() if response else ""
            if not retry_resp or len(retry_resp) < 5:
                return "⚠ Не удалось получить ответ. Попробуйте переформулировать вопрос."

        # ── Truncation detection: if response ends mid-sentence, append ellipsis ──
        response = _fix_truncated_response(response)

        # GUI-режим: НЕ выполняем bash-блоки автоматически. Пользователь
        # сам нажмёт «Выполнить» в CommandActionBar после прочтения ответа.
        # Авто-выполнение через ActionExecutor с interactive=False ломает
        # sudo-команды (просит пароль, которого никто не введёт) и обходит
        # подтверждение пользователя.
        return response

    def _stream_handler(text: str, cancel_flag: list):
        """Streaming LLM pipeline: preprocessor → yields tokens one by one."""

        # Strip [LINA-REPAIR] sentinel so it never reaches the model.
        is_repair = text.startswith("[LINA-REPAIR]")
        if is_repair:
            text = text[len("[LINA-REPAIR]"):].lstrip()

        # ── Install workflow trigger ────────────────────────────────
        if not is_repair:
            install_target = _maybe_install_target(text)
            if install_target:
                logger.info("Install workflow trigger (stream): '%s'", install_target)
                yield f"[LINA-INSTALL]{install_target}"
                return

        # ── Fast-path: direct answers without LLM ──
        # Skip fast-path for:
        #   1) follow-up to web_search (e.g. "А какой процессор...")
        #   2) auto-repair turns (force LLM path)
        _skip_fast = _is_web_followup(text) or is_repair
        if not _skip_fast:
            try:
                _, pp, _ = _get_system_context()
                if pp:
                    direct = pp.try_direct_answer(text)
                    if direct:
                        logger.info("Direct answer (streaming): %s…", direct[:60])
                        yield direct
                        return
            except Exception as e:
                logger.debug("QueryPreprocessor fast-path error: %s", e)

        engine = _get_engine()
        if not engine:
            yield "⚠ Не удалось создать LLM-движок."
            return

        # Enrich follow-up queries with subject from history
        enriched_text = _enrich_followup(text)

        full_context, executor, web_intent, web_result, intent, decision = _build_context(enriched_text, is_followup=_skip_fast, force_chat=is_repair)
        _save_last_intent(intent, query=text)

        # ── Direct web results (weather/currency) — skip LLM ──
        if web_result and web_intent in ("weather_query", "web"):
            logger.info("Direct web result (%s), skipping LLM streaming", web_intent)
            yield web_result
            return

        # ── SPECS MODE: facts extracted → direct output, NO LLM ──
        if web_result and isinstance(web_result, str) and web_result.startswith("[DIRECT_FACTS]"):
            logger.info("SPECS MODE (streaming): returning extracted facts directly (no LLM)")
            yield web_result[len("[DIRECT_FACTS]"):]
            return

        # ── Web search with NO results — template response, skip LLM ──
        if intent == "web_search" and not web_result:
            from lina.core.main_pipeline import MainPipeline
            if "\n" in text or _RE_PS1_LINE.search(text):
                topic = _extract_search_from_terminal(text).rstrip(" linux")
            else:
                topic = MainPipeline._extract_topic(text)
            if topic:
                yield (
                    f"К сожалению, мне не удалось найти информацию "
                    f"о «{topic}» в интернете. Попробуйте:"
                    f"\n  • уточнить запрос (например: «{topic} характеристики»)"
                    f"\n  • проверить подключение к сети"
                    f"\n  • повторить запрос чуть позже"
                )
            else:
                yield (
                    "К сожалению, не удалось найти информацию в интернете. "
                    "Попробуйте уточнить запрос или повторить позже."
                )
            return

        # ── Datetime fast-path — instant answer without LLM ──
        if _RE_DATETIME_QUICK.search(text):
            from lina.core.tools import ToolRegistry
            dt_result = ToolRegistry._tool_datetime(text)
            if dt_result.success:
                yield dt_result.output
                return

        # ── Open app fast-path — use ApplicationResolver, skip LLM ──
        if intent == "open_application":
            app_name = ""
            if decision and hasattr(decision, 'metadata') and decision.metadata:
                app_name = decision.metadata.get("app_name", "")
            if app_name:
                try:
                    from lina.core.tools import ToolRegistry
                    _reg = ToolRegistry()
                    result = _reg._tool_open_app(app_name)
                    yield result.output
                    return
                except Exception as e:
                    logger.warning("open_app fast-path error: %s", e)

        # ── Install app fast-path — real package search, skip LLM hallucinations ──
        if intent == "install_application":
            app_name = ""
            if decision and hasattr(decision, 'metadata') and decision.metadata:
                app_name = decision.metadata.get("app_name", "")
            if not app_name or _is_garbage_install_target(app_name):
                hist_app = _find_install_app_in_history()
                if hist_app:
                    app_name = hist_app
                    if decision and hasattr(decision, 'metadata') and decision.metadata:
                        decision.metadata["app_name"] = hist_app
            if app_name:
                install_result = _try_install_search(enriched_text, intent, decision)
                if install_result:
                    logger.info("Install fast-path (streaming): skipping LLM")
                    yield _execute_commands(install_result, executor, intent)
                    return

        history = _get_history()

        # Stream tokens directly — engine already buffers first 15 for garbage detection.
        # Track what was actually yielded (not raw tokens) for post-stream processing.
        yielded_parts = []
        try:
            with _engine_lock:
                for token in engine.generate_stream(
                    query=text, context=full_context,
                    cancel_flag=cancel_flag, history=history,
                    intent=intent,
                ):
                    yielded_parts.append(token)
                    yield token
        except Exception as e:
            # Retry with model reload
            logger.warning("Streaming LLM error, reloading model: %s", e)
            yielded_parts.clear()
            try:
                with _engine_lock:
                    engine.unload()
                    engine.load("full")
                yield "\n---\n⟳ Повторная генерация...\n"
                with _engine_lock:
                    for token in engine.generate_stream(
                        query=text, context=full_context,
                        cancel_flag=cancel_flag, history=history,
                        intent=intent,
                    ):
                        yielded_parts.append(token)
                        yield token
            except Exception as e2:
                logger.error("Streaming retry failed after reload: %s", e2)
                yield f"\n❌ Ошибка: {e2}"
                return

        full_response = "".join(yielded_parts).strip()

        # ── Post-stream cleanup: detect leaked system info (log only, no duplicate output) ──
        from lina.llm.engine import LLMEngine
        cleaned = LLMEngine._clean_answer(full_response)
        if cleaned and len(cleaned) > 5 and cleaned != full_response:
            logger.warning("Post-stream cleanup: removed %d chars of leaked data",
                          len(full_response) - len(cleaned))
            full_response = cleaned

        # ── Empty / garbage → retry with minimal context ──
        if not full_response or len(full_response) < 5:
            logger.warning("Empty streaming response, retrying with minimal context")
            try:
                for token in engine.generate_stream(
                    query=text, context="",
                    cancel_flag=cancel_flag, history=history,
                    intent=intent,
                ):
                    yield token
            except Exception as e:
                logger.error("Streaming retry with minimal context failed: %s", e)
                yield f"\n⚠ Повторная генерация не удалась: {e}"
            return

        # ── Anti-Hallucination Guard для streaming web_search ──
        # Если факты были собраны из веба, но модель выдала generic-ответ
        # «зависит от модели камеры…» — заменяем на структурированные факты.
        # Без этого пользователь видит вымышленные данные вместо реальных.
        if (intent == "web_search" and _last_fact_set[0]
                and _last_fact_set[0].facts):
            try:
                if _is_generic_hallucination(full_response):
                    logger.warning(
                        "Stream anti-hallucination: generic text detected, "
                        "replacing with %d extracted facts",
                        len(_last_fact_set[0].facts),
                    )
                    facts_response = _last_fact_set[0].format_for_user()
                    # Полностью заменяем поток на форматированные факты:
                    # стираем то что уже выдали, отдаём факты.
                    yield "\n\n---\n📋 Уточнённые данные из веб-источников:\n"
                    yield facts_response
                    return
            except Exception as e:
                logger.debug("Stream anti-hallucination guard error: %s", e)

        # ── Detect deflection OR vague answer → web search fallback ──
        # Skip on short follow-ups (e.g. «Давай ещё раз»).
        _is_short_followup = len(text.strip()) < 25
        if not _is_short_followup and (
            _is_llm_deflecting(full_response) or _is_vague_answer(full_response, text)
        ):
            enhanced = _web_search_fallback(text, full_context)
            if enhanced:
                yield "\n\n---\n\U0001f50d Уточняю...\n"
                try:
                    for token in engine.generate_stream(
                        query=text, context=enhanced,
                        cancel_flag=cancel_flag, history=history,
                        intent=intent,
                    ):
                        yield token
                except Exception as e:
                    logger.warning("Web fallback stream failed: %s", e)
                return  # Skip command execution for deflection

        # Execute commands
        # GUI-режим: НЕ выполняем bash автоматически. Пользователь нажмёт
        # «Выполнить» в CommandActionBar. Иначе sudo-команды улетают в
        # interactive=False ActionExecutor и пропускаются.
        # exec_response = _execute_commands(full_response, executor, intent)
        # if exec_response != full_response:
        #     extra = exec_response[len(full_response):]
        #     if extra:
        #         yield extra

    controller.set_request_handler(_handler)
    controller.set_stream_handler(_stream_handler)
    logger.info("Full pipeline: LLM + RAG + diagnostics + streaming + auto-execute")

    # ── Preload model in background for fast first response ──
    def _preload_model():
        """Preload mini model in background thread so first query is instant."""
        try:
            engine = _get_engine()
            if engine and not engine.is_loaded:
                logger.info("Предзагрузка LLM-модели в фоне...")
                engine.load("full")
                logger.info("Модель предзагружена — первый ответ будет быстрым")
        except Exception as e:
            logger.debug("Предзагрузка не удалась: %s", e)

    preload_thread = threading.Thread(target=_preload_model, daemon=True)
    preload_thread.start()


def _open_settings(window):
    """Open the full settings dialog."""
    try:
        if hasattr(window, "_open_settings_dialog"):
            window._open_settings_dialog()
            return
        from lina.gui.settings_dialog import create_settings_dialog
        dialog = create_settings_dialog(parent=window)
        dialog.exec()
    except Exception as e:
        logger.error("Settings dialog error: %s", e)


def _quit_app(window, app):
    """Clean shutdown."""
    window._on_quit()
    app.quit()


# ── Entry point ──

def main():
    """CLI entry point."""
    sys.exit(run_gui())


if __name__ == "__main__":
    main()
