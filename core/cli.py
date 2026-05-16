"""
Lina — CLI модуль (разбор аргументов командной строки).

Отделяет парсинг аргументов от логики запуска.
Все аргументы возвращаются как структурированный объект,
без побочных эффектов (без print, без sys.exit).

Phase 2: CLI routed through IntentBridge → governance pipeline.
"""

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger("lina.core.cli")


def _get_version() -> str:
    """Возвращает текущую версию из lina.__init__."""
    try:
        from lina import __version__
        return __version__
    except ImportError:
        return "0.9.0"


@dataclass
class LinaArgs:
    """
    Структура аргументов командной строки Lina.

    Все поля имеют значения по умолчанию.
    Используется вместо argparse.Namespace для типобезопасности.
    """
    verbose: bool = False
    index: bool = False
    model: Optional[str] = None
    knowledge_dir: Optional[str] = None
    web: bool = False
    port: int = 8585
    notify: bool = False
    preinstall: bool = False
    cv: bool = False
    oneshot: Optional[str] = None  # Одноразовый запрос (без REPL)
    quiet: bool = False            # Тихий режим (минимум вывода)
    trace: bool = False            # Trace mode — вывод JSON-трейса каждого запроса
    # ── Phase 16 flags ──
    trace_json: bool = False       # Extended JSON trace (observability pro)
    chaos: Optional[str] = None    # Chaos profile name (disabled|dev_chaos|staging_chaos)
    adaptive_routing: bool = False # Enable adaptive routing
    agent_v3: bool = False         # Enable agent evolution v3
    secure_shell: bool = False     # Enable OS-aware secure shell
    profile: Optional[str] = None  # Runtime profile override
    gui: bool = False              # Launch Qt desktop GUI
    daemon: bool = False           # Daemon mode (systemd / socket)
    first_run: bool = False        # Run First Run Wizard
    clear_cache: bool = False      # Clear all caches and exit


def build_parser() -> argparse.ArgumentParser:
    """
    Создаёт парсер аргументов командной строки.

    Returns:
        Настроенный ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        description=f"Lina — ИИ-ассистент v{_get_version()}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python lina.py                    # Интерактивный REPL\n"
            "  python lina.py --cv               # С Computer Vision\n"
            "  python lina.py --oneshot 'привет' # Одноразовый запрос\n"
            "  python lina.py --quiet             # Без баннера и emoji\n"
        ),
    )
    parser.add_argument("--version", action="version",
                        version=f"Lina v{_get_version()}")

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Включить подробный вывод",
    )
    parser.add_argument(
        "--index", "-i",
        action="store_true",
        help="Проиндексировать базу знаний при старте",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Путь к GGUF модели",
    )
    parser.add_argument(
        "--knowledge-dir",
        type=str,
        default=None,
        help="Путь к директории с документами для RAG",
    )
    parser.add_argument(
        "--web", "-w",
        action="store_true",
        help="Запустить веб-интерфейс (REST API + UI)",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8585,
        help="Порт веб-интерфейса (по умолчанию: 8585)",
    )
    parser.add_argument(
        "--notify", "-n",
        action="store_true",
        help="Включить desktop-уведомления (KDE/Wayland)",
    )
    parser.add_argument(
        "--preinstall",
        action="store_true",
        help="Включить режим предустановки Linux (Live USB помощник)",
    )
    parser.add_argument(
        "--cv",
        action="store_true",
        help="Включить модуль Computer Vision (скриншоты, OCR, детекция GUI)",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Включить trace mode — JSON-трейс каждого запроса (роутинг, риск, латенси, агент)",
    )
    parser.add_argument(
        "--oneshot",
        type=str,
        default=None,
        metavar="QUERY",
        help="Одноразовый запрос (без интерактивного REPL)",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Тихий режим — без баннера и emoji (для pipe/fish)",
    )

    # ── Phase 16 flags ──
    parser.add_argument(
        "--trace-json",
        action="store_true",
        help="Extended JSON trace с observability pro (spans, flamegraph, error taxonomy)",
    )
    parser.add_argument(
        "--chaos",
        type=str,
        default=None,
        metavar="PROFILE",
        choices=["disabled", "dev_chaos", "staging_chaos"],
        help="Chaos engineering profile (disabled|dev_chaos|staging_chaos)",
    )
    parser.add_argument(
        "--adaptive-routing",
        action="store_true",
        help="Включить adaptive routing (complexity-based tier selection)",
    )
    parser.add_argument(
        "--agent-v3",
        action="store_true",
        help="Включить Agent Evolution v3 (DAG planner, memory refiner, self-evaluator)",
    )
    parser.add_argument(
        "--secure-shell",
        action="store_true",
        help="Включить OS-aware secure shell (SafeShell + FileGuard + EnvironmentGuard)",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        metavar="NAME",
        choices=["dev", "prod", "secure", "lightweight", "cli"],
        help="Runtime profile override (dev|prod|secure|lightweight|cli)",
    )

    # ── Phase 8: GUI ──
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Запустить десктопный Qt GUI (требуется PyQt6 или PySide6)",
    )

    # ── v1.0.0: Packaging ──
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Запуск в режиме демона (systemd service / D-Bus socket)",
    )
    parser.add_argument(
        "--first-run",
        action="store_true",
        help="Запуск мастера первого запуска (выбор модели, настройка)",
    )

    # ── Maintenance ──
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Очистить все кэши (fact_store, response_cache, in-memory) и выйти",
    )

    return parser


def parse_args(argv: Optional[List[str]] = None) -> LinaArgs:
    """
    Парсит аргументы командной строки.

    Args:
        argv: Список аргументов (None = sys.argv[1:]).

    Returns:
        LinaArgs — структурированные аргументы.
    """
    parser = build_parser()
    ns = parser.parse_args(argv)

    return LinaArgs(
        verbose=ns.verbose,
        index=ns.index,
        model=ns.model,
        knowledge_dir=ns.knowledge_dir,
        web=ns.web,
        port=ns.port,
        notify=ns.notify,
        preinstall=ns.preinstall,
        cv=ns.cv,
        oneshot=ns.oneshot,
        quiet=ns.quiet,
        trace=ns.trace,
        trace_json=ns.trace_json,
        chaos=ns.chaos,
        adaptive_routing=ns.adaptive_routing,
        agent_v3=ns.agent_v3,
        secure_shell=ns.secure_shell,
        profile=ns.profile,
        gui=ns.gui,
        daemon=ns.daemon,
        first_run=ns.first_run,
        clear_cache=ns.clear_cache,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Entry point — вызывается из pyproject.toml console_scripts
# ═══════════════════════════════════════════════════════════════════════════

BANNER = r"""
██╗     ██╗███╗   ██╗ █████╗
██║     ██║████╗  ██║██╔══██╗
██║     ██║██╔██╗ ██║███████║
██║     ██║██║╚██╗██║██╔══██║
███████╗██║██║ ╚████║██║  ██║
╚══════╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝
  Локальный ИИ-помощник для Linux  v1.0.0
"""


def _setup_logging(verbose: bool = False) -> None:
    """Настройка логирования."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # bootstrap.py adds NullHandler to root, making basicConfig a no-op.
    # Force a real StreamHandler for parser + pipeline log visibility.
    root = logging.getLogger()
    root.setLevel(level)
    has_stream = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.NullHandler)
        for h in root.handlers
    )
    if not has_stream:
        _console = logging.StreamHandler()
        _console.setLevel(level)
        _console.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        root.addHandler(_console)


def _create_pipeline():
    """Создать MainPipeline и подключить LLM + RAG + Tool executors с SystemInteraction."""
    from lina.core.main_pipeline import MainPipeline
    from lina.core.system_interaction import (
        collect_system_snapshot,
        format_snapshot_for_prompt,
        QueryPreprocessor,
        extract_commands,
        ActionExecutor,
    )

    pipeline = MainPipeline()

    # ── Собрать реальный snapshot системы ──
    logger.info("Collecting system snapshot...")
    snapshot = collect_system_snapshot()
    system_context = format_snapshot_for_prompt(snapshot)
    preprocessor = QueryPreprocessor(snapshot)
    action_executor = ActionExecutor(interactive=True)
    logger.info("System: %s, kernel %s, DE: %s",
                snapshot.distro, snapshot.kernel, snapshot.de or "none")

    # ── Подключить LLMEngine с системным контекстом ──
    llm_engine = None
    try:
        from lina.llm.engine import LLMEngine
        llm_engine = LLMEngine()

        # ── IntentRouter for CLI intent-based command guard ──
        _cli_intent_router = None
        try:
            from lina.core.intent_router import IntentRouter
            _cli_intent_router = IntentRouter()
        except Exception as e:
            logger.debug("CLI IntentRouter init: %s", e)

        # Intents where command execution is allowed (same as GUI)
        _EXEC_INTENTS = {
            "system_command", "install_application", "system_diagnostic",
            "tool_explicit",
        }

        def llm_executor(ctx):
            """Callback: LLM генерация с реальным системным контекстом."""
            query = ctx.user_input

            # 0. Determine intent for command execution guard
            intent = "chat"
            if _cli_intent_router:
                try:
                    decision = _cli_intent_router.route(query)
                    intent = decision.intent.value if hasattr(decision.intent, 'value') else str(decision.intent)
                except Exception:
                    pass

            # 1. Попробовать прямой ответ без LLM (быстрые запросы)
            direct = preprocessor.try_direct_answer(query)
            if direct is not None:
                return direct

            # 2. Собрать контекст для LLM
            enrichment = preprocessor.enrich_for_llm(query)
            full_context = system_context + "\n" + enrichment
            rag_context = getattr(ctx, "rag_context", "") or ""
            if rag_context:
                full_context += "\n\n" + rag_context

            history = getattr(ctx, "history", None)

            # 3. Вызвать LLM с реальным контекстом
            response = llm_engine.generate(
                query, context=full_context, history=history,
                intent=intent,
            )

            # 4. Извлечь и выполнить команды из ответа LLM
            #    ONLY for system-related intents (same guard as GUI)
            if intent not in _EXEC_INTENTS:
                # Strip ```bash blocks from response for non-system intents
                response = re.sub(
                    r'```(?:bash|sh|shell|console|zsh|fish)?[\s]*\n?.*?```',
                    '', response, flags=re.DOTALL | re.IGNORECASE,
                ).strip() or response
                return response

            commands = extract_commands(response)
            if commands:
                # Filter out echo/printf that just repeat the text answer
                commands = [
                    c for c in commands
                    if not re.match(
                        r'^(echo|printf)\s+["\']|'
                        r'^(google|search|browse|bing|yandex|firefox|chrome)\s',
                        c.command, re.I,
                    )
                ]
            if commands:
                exec_results = action_executor.execute_many(commands)
                exec_output = []
                for res in exec_results:
                    if res.skipped:
                        exec_output.append(res.reason)
                    elif res.success:
                        if res.stdout:
                            exec_output.append(f"$ {res.command}\n{res.stdout}")
                        else:
                            exec_output.append(f"✅ {res.command}")
                    else:
                        exec_output.append(
                            f"❌ {res.command}\n{res.stderr or res.reason}"
                        )

                if exec_output:
                    response += "\n\n📋 Результат выполнения:\n" + "\n".join(exec_output)

            return response

        pipeline.set_llm_executor(llm_executor)
        logger.info("LLM engine connected (model: %s)",
                     llm_engine.llm_config.full.model_path)
    except Exception as e:
        logger.warning("LLM engine not available: %s — fallback answers only", e)

    # ── Подключить RAG ──
    try:
        from lina.rag.retriever import KnowledgeRetriever
        retriever = KnowledgeRetriever()

        def rag_executor(ctx):
            """Callback: RAG по базе знаний (hybrid BM25 + re-ranking)."""
            results = retriever.search(ctx.user_input, top_k=3)
            if results:
                return "\n\n".join(
                    r.get("text", str(r)) if isinstance(r, dict) else str(r)
                    for r in results
                )
            return ""

        pipeline.set_rag_executor(rag_executor)
        logger.info("RAG KnowledgeRetriever connected (hybrid search)")
    except Exception as e:
        logger.warning("RAG retriever not available: %s", e)

    # ── Подключить Tool executor (Commander) ──
    try:
        from lina.shell.commander import Commander
        commander = Commander()
        commander.set_pipeline(pipeline)  # Unified: Commander→MainPipeline delegation

        def tool_executor(ctx):
            """Callback: выполнение команд через Commander."""
            return commander.process(ctx.user_input)

        pipeline.set_tool_executor(tool_executor)
        logger.info("Tool executor (Commander) connected")
    except Exception as e:
        logger.warning("Commander not available: %s", e)

    # ── Подключить Diagnostic executor ──
    try:
        from lina.diagnostics.integration import diagnose as diag_fn

        def diag_executor(ctx):
            """Callback: диагностика через деревья + LLM fallback."""
            result = diag_fn(ctx.user_input)
            if result.get("matched") and result.get("formatted"):
                return result["formatted"]
            elif result.get("needs_llm") and result.get("llm_prompt"):
                # Enrich ctx for LLM with diagnostic context
                ctx.user_input = result["llm_prompt"]
                return ""  # signal to fallback to LLM
            return ""

        pipeline.set_diag_executor(diag_executor)
        logger.info("Diagnostic executor connected (21 trees)")
    except Exception as e:
        logger.warning("Diagnostic executor not available: %s", e)

    # ── Подключить Web Search executor ──
    try:
        from lina.core.web_search_engine import get_web_search_engine
        from lina.core.web_search_session import get_web_search_session

        web_engine = get_web_search_engine()
        web_session = get_web_search_session()

        def web_executor(ctx):
            """Callback: веб-поиск + парсер + session + fact pipeline.

            Использует:
              - WebSearchSession для отслеживания посещённых URL и контекста
              - WebSearchEngine с BS4 парсером (из Parcer/search_cli)
              - lina.parser для извлечения текста (readability-lxml + BS4)
              - lina.parser.web_llm для mini-LLM суммаризации
              - FactPipeline для anti-hallucination guard
            """
            from lina.parser.web_llm import detect_language
            # Определяем язык запроса
            web_session.language = detect_language(ctx.user_input)

            resp = web_engine.search(ctx.user_input)
            if not resp.success:
                if resp.error:
                    return f"⚠️ Веб-поиск: {resp.error}"
                return ""
            if not resp.summary:
                return ""

            # Отметить просмотренные URL в сессии
            if resp.results:
                web_session.mark_urls([r.url for r in resp.results])

            # ── Fact Pipeline: извлечение + верификация + guard ──
            summary = resp.summary
            try:
                from lina.core.fact_pipeline import get_fact_pipeline
                from lina.pipeline.generation_gate import get_generation_gate

                fp = get_fact_pipeline()
                gate = get_generation_gate()
                results = resp.results or []
                fact_set = fp.process(
                    web_summary=summary,
                    results=results,
                    subject=ctx.user_input,
                )
                decision = gate.evaluate("web_search", fact_set)
                if not decision.allow_generation:
                    return decision.refusal_text

                if fact_set and fact_set.facts:
                    cleaned, removed = fp.check_answer(summary, fact_set)
                    if removed:
                        logger.info(
                            "CLI anti-hallucination: removed %d claims from web answer",
                            len(removed),
                        )
                    summary = cleaned or summary
            except Exception as e:
                logger.debug("CLI fact pipeline skipped: %s", e)

            # Обновить сессию веб-поиска
            web_session.add_user(ctx.user_input)
            web_session.add_assistant(summary)
            web_session.last_summary = summary
            if not web_session.current_topic:
                web_session.current_topic = ctx.user_input

            return summary

        pipeline.set_web_executor(web_executor)
        logger.info(
            "Web search executor connected "
            "(DDG BS4 + Brave + SearXNG + Wikipedia + Parser + FactPipeline)"
        )
    except Exception as e:
        logger.warning("Web search executor not available: %s", e)

    # Attach preprocessor for use outside pipeline
    pipeline._lina_preprocessor = preprocessor
    pipeline._lina_snapshot = snapshot

    return pipeline


# ── Governance routing ───────────────────────────────────────────────────────

def _route_via_governance(text: str, pipeline, source: str = "cli") -> str:
    """
    Route user input through IntentBridge → governance pipeline.

    Phase 2: CLI NEVER calls pipeline.process_request() directly.
    Phase 4: ResponseFormatter for human-friendly output.

    Returns:
        Human-readable response text.
    """
    from lina.intent.types import IntentStatus

    # Phase 4: Help command
    try:
        from lina.core.response_ux import get_response_formatter
        fmt = get_response_formatter()
        if fmt.is_help_command(text):
            return fmt.format_help()
    except ImportError:
        pass

    # Fast path: direct answers (greetings, basic system queries)
    preprocessor = getattr(pipeline, '_lina_preprocessor', None)
    if preprocessor:
        try:
            direct = preprocessor.try_direct_answer(text)
            if direct is not None:
                return str(direct)
        except Exception as e:
            logger.debug("Fast path failed: %s — falling through to governance", e)

    # Route through IntentBridge (governance pipeline)
    try:
        from lina.intent.bridge import get_intent_bridge
        from lina.core.response_ux import get_response_formatter

        fmt = get_response_formatter()
        bridge = get_intent_bridge()

        pipeline_handler = getattr(pipeline, 'process_request', None)
        result = bridge.from_text(text, source=source,
                                  pipeline_handler=pipeline_handler)

        # Handle NEEDS_CONFIRM — interactive CLI prompt
        if result.status == IntentStatus.NEEDS_CONFIRM:
            try:
                from lina.governance.confirmation import get_confirmation_handler
                handler = get_confirmation_handler()
                handler.set_cli_mode()
                from lina.intent.types import Intent, IntentType
                intent = Intent(
                    type=IntentType.SYSTEM_ACTION,
                    action=getattr(result, 'policy_decision', ''),
                    source=source,
                    user_text=text,
                )
                result = handler.handle(result, intent=intent)
            except Exception as e:
                logger.debug("Confirmation handling failed: %s", e, exc_info=True)
                return "⚠ Требуется подтверждение, но обработчик недоступен"

        # Phase 4: Format through UX layer
        domain = getattr(result, 'metadata', {}).get('domain', '') if hasattr(result, 'metadata') else ''
        action = getattr(result, 'metadata', {}).get('action', '') if hasattr(result, 'metadata') else ''
        formatted = fmt.format_result(result, domain=domain, action=action)
        if formatted:
            return formatted

        # Fallback: raw text extraction
        if hasattr(result, 'response_text') and result.response_text:
            return result.response_text
        if hasattr(result, 'text'):
            return str(result.text)
        return str(result)

    except ImportError as e:
        logger.error("IntentBridge not available — request denied (fail-closed): %s", e)
        try:
            from lina.core.response_ux import get_response_formatter
            return get_response_formatter().format_degradation("governance")
        except Exception:
            return "⚠ Governance pipeline недоступен. Запрос отклонён."


def _clear_all_caches() -> int:
    """Очистить все кэши Lina (fact_store, response_cache, knowledge и т.д.).

    Returns:
        Exit code (0 = success).
    """
    from pathlib import Path
    import json

    cache_dir = Path(__file__).parent.parent / "cache"
    cleared: list[str] = []
    errors: list[str] = []

    # ── Disk caches ──
    _CACHE_FILES = [
        "fact_store.json",
        "response_cache.json",
        "command_history.json",
        "knowledge_fragments.json",
        "knowledge_manifest.json",
    ]
    for fname in _CACHE_FILES:
        fpath = cache_dir / fname
        if fpath.exists():
            try:
                fpath.write_text("{}", encoding="utf-8")
                cleared.append(fname)
            except Exception as e:
                errors.append(f"{fname}: {e}")

    # ── ChromaDB / vector index ──
    chroma_dir = Path(__file__).parent.parent / "chroma_db"
    for idx_file in ("tfidf_index.json", "vector_index.json"):
        fpath = chroma_dir / idx_file
        if fpath.exists():
            try:
                fpath.write_text("{}", encoding="utf-8")
                cleared.append(f"chroma_db/{idx_file}")
            except Exception as e:
                errors.append(f"chroma_db/{idx_file}: {e}")

    # ── In-memory caches (FactStore, ResponseCache) ──
    try:
        from lina.memory.fact_store import FactStore
        fs = FactStore(cache_dir=cache_dir)
        fs.clear()
        fs.save()
    except Exception:
        pass  # already cleared via disk

    try:
        from lina.memory.cache import get_response_cache
        rc = get_response_cache()
        rc.clear()
    except Exception:
        pass

    # ── Report ──
    if cleared:
        print(f"✅ Очищено: {', '.join(cleared)}")
    if errors:
        for err in errors:
            print(f"❌ Ошибка: {err}")
    if not cleared and not errors:
        print("ℹ️  Кэш-файлы не найдены")

    print("\n🔄 Перезапустите Lina для полного сброса in-memory кэшей.")
    return 1 if errors else 0


def main(argv: Optional[List[str]] = None) -> int:
    """
    Главная точка входа Lina CLI.

    Вызывается из console_scripts (pip install) или напрямую.
    Разбирает аргументы и запускает соответствующий режим.

    Returns:
        Exit code (0 = success).
    """
    args = parse_args(argv)
    _setup_logging(args.verbose)

    # --clear-cache: очистить кэши и выйти
    if args.clear_cache:
        return _clear_all_caches()

    # --oneshot: выполнить один запрос и выйти
    if args.oneshot:
        print(BANNER)
        logger.info("Oneshot mode: %s", args.oneshot)
        try:
            pipeline = _create_pipeline()
            result_text = _route_via_governance(
                args.oneshot, pipeline, source="cli")
            print(result_text)
        except Exception as e:
            logger.error("Pipeline error: %s", e, exc_info=True)
            print("❌ Произошла внутренняя ошибка")
            return 1
        return 0

    # --preinstall: первый запуск
    if args.preinstall or args.first_run:
        try:
            from lina.installer.first_run import FirstRunWizard
            wizard = FirstRunWizard()
            wizard.run()
        except Exception as e:
            logger.error("First run wizard error: %s", e, exc_info=True)
            print("❌ Ошибка мастера установки")
            return 1
        return 0

    # --index: переиндексация базы знаний
    if args.index:
        try:
            from lina.rag.indexer_v2 import KnowledgeIndexer
            indexer = KnowledgeIndexer()
            kdir = args.knowledge_dir or "knowledge"
            count = indexer.index_directory(kdir)
            print(f"✅ Проиндексировано документов: {count}")
        except Exception as e:
            logger.error("Indexing error: %s", e, exc_info=True)
            print("❌ Ошибка индексации")
            return 1
        return 0

    # --gui: запуск Qt десктопного GUI
    if args.gui:
        try:
            from lina.gui.app import run_gui
            return run_gui()
        except ImportError as e:
            logger.error("GUI launch failed: %s", e, exc_info=True)
            print("❌ Qt не найден. Установите: pip install PyQt6")
            return 1
        except Exception as e:
            logger.error("GUI error: %s", e, exc_info=True)
            print("❌ Ошибка GUI")
            return 1

    # --daemon: фоновый режим (systemd / D-Bus socket)
    if args.daemon:
        logger.info("Daemon mode started")
        try:
            from lina.installer.first_run import is_first_run
            if is_first_run():
                logger.warning("First run not completed — run: lina --first-run")
                print("⚠ Запустите: lina --first-run для первоначальной настройки")
                return 1

            pipeline = _create_pipeline()
            if not args.quiet:
                logger.info("Lina daemon ready (PID=%d)", os.getpid())

            # Daemon loop — wait for signals
            import signal
            running = True

            def _handle_signal(sig, frame):
                nonlocal running
                running = False
                logger.info("Received signal %s, shutting down", sig)

            signal.signal(signal.SIGTERM, _handle_signal)
            signal.signal(signal.SIGINT, _handle_signal)

            while running:
                import time
                time.sleep(1)

            logger.info("Daemon stopped")
        except Exception as e:
            logger.error("Daemon error: %s", e, exc_info=True)
            print("❌ Ошибка демона")
            return 1
        return 0

    # --web: запуск веб-сервера (placeholder)
    if args.web:
        print(BANNER)
        print(f"🌐 Веб-интерфейс на порту {args.port} (не реализован в v1.0)")
        return 0

    # Интерактивный режим (REPL)
    print(BANNER)

    if not args.quiet:
        print("  Введите вопрос о Linux или 'выход' для выхода.")
        print("  Используйте --help для списка опций.\n")

    try:
        pipeline = _create_pipeline()
    except Exception as e:
        logger.error("Failed to initialize pipeline: %s", e, exc_info=True)
        print("❌ Ошибка инициализации")
        print("   Попробуйте: lina --preinstall")
        return 1

    # Freeze config after startup — accidental mutations will log warnings
    from lina.config import config as _cfg
    _cfg.freeze()

    while True:
        try:
            user_input = input("lina> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 До свидания!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("выход", "exit", "quit", "q"):
            print("👋 До свидания!")
            break

        try:
            result_text = _route_via_governance(
                user_input, pipeline, source="cli")
            print(f"\n{result_text}\n")
        except Exception as e:
            logger.error("Error processing query: %s", e, exc_info=True)
            print("❌ Произошла внутренняя ошибка\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
