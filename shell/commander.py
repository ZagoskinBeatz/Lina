"""
Lina — Командный процессор (Commander).

Парсит команды пользователя, определяет тип действия,
решает, нужна ли LLM или достаточно встроенной команды.

Интегрирует: цепочки, макросы, историю, уведомления,
инструменты, обучение, логирование.
"""

import json
import os
import re
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Optional, Tuple, List

from lina.config import config
from lina.system.files import FileManager
from lina.system.executor import CommandExecutor
from lina.system.monitor import SystemMonitor
from lina.system.logger import logger
from lina.system.sandbox import SubprocessSandbox
from lina.rag.indexer import KnowledgeIndexer
from lina.rag.searcher import KnowledgeSearcher
from lina.rag.history import CommandHistory
from lina.shell.chains import CommandChain, ChainExecutor, MacroManager
from lina.llm.engine import LLMEngine
from lina.interface.notify import DesktopNotifier
from lina.tools.browser import WebTool
from lina.tools.ide import IDETool
from lina.tools.api import APIClient
from lina.learning.collector import KnowledgeCollector
from lina.learning.analyzer import LogAnalyzer
from lina.preinstall.hardware import HardwareScanner
from lina.preinstall.network import NetworkScanner
from lina.preinstall.guide import InstallGuide
from lina.cv.scanner import ScreenScanner
from lina.cv.ocr import OCREngine
from lina.cv.detector import GUIDetector


# System Interaction — утилиты для системного контекста
from lina.core.system_interaction import (
    collect_system_snapshot,
    format_snapshot_for_prompt,
    QueryPreprocessor,
    extract_commands,
    ActionExecutor,
)

# Mini LLM — быстрая модель с function-calling (замена hardcoded fast-path)
from lina.llm.mini_engine import MiniLLMEngine


class CommandType:
    """Типы обработки команд."""
    BUILTIN = "builtin"         # Встроенная команда (без LLM)
    SYSTEM = "system"           # Системная команда (subprocess)
    RAG_QUERY = "rag_query"     # Поиск по базе знаний
    LLM_QUERY = "llm_query"    # Запрос к LLM
    LLM_RAG = "llm_rag"        # LLM + контекст из RAG
    META = "meta"               # Мета-команда Lina


# ── Встроенные ключевые слова для быстрого распознавания ──

BUILTIN_PATTERNS = {
    # Файлы и директории
    r"(?:покажи|список|показать)\s+(?:файл|файлы|содержимое)\s+(.+)": "list_files",
    r"(?:дерево|структура)\s+(?:каталога|директории|папки)?\s*(.*)": "dir_tree",
    r"(?:прочитай|прочти|открой|покажи содержимое)\s+(?:файл[а]?)\s+(.+)": "read_file",
    r"(?:найди|поиск|искать)\s+(?:файл[ыа]?)\s+(.+)": "search_files",

    # Система
    r"(?:статус|состояние)\s+(?:систем[ыа]|ресурс[ыов])": "system_status",
    r"(?:процессы|топ процесс[ыов])": "top_processes",
    r"(?:диагональ|разрешени\w+|размер)\s+(?:(?:у\s+меня|моего)\s+)?(?:экран\w*|монитор\w*|дисплей\w*)": "display_info",
    r"как(?:ой|ая|ое)\s+(?:у\s+меня\s+)?(?:диагональ|размер|разрешени\w+)\s+(?:экран\w*|монитор\w*|дисплей\w*)": "display_info",
    r"как(?:ой|ая|ое)\s+(?:у\s+меня\s+)?(?:экран|монитор|дисплей)\b": "display_info",

    # RAG / База знаний
    r"(?:индексир|переиндекс|обнови базу|индекс)": "index_knowledge",
    r"(?:статус|инфо)\s+(?:баз[ыа]|знан)": "rag_stats",
    r"(?:очисти|очистить|удалить)\s+(?:баз[уы]|знан)": "rag_clear",
    r"(?:поиск|найди|ищи)\s+(?:в\s+)(?!инт|инет|веб|сет[ьи]|internet|web)(?:базе?\s+)?(?:знани[йях])?\s*[:\-]?\s*(.+)": "rag_search",

    # LLM
    r"(?:загрузи|подключи)\s+(?:модел[ьи]|llm)": "llm_load",
    r"(?:запусти|подключи|включи|загрузи|активируй)\s+(?:старш|больш|тяжёл|тяжел|полн)\w*\s+(?:модел[ьи]|llm|нейросет\w*|брат\w*|верси\w*|нейронк\w*)": "llm_load_full",
    r"(?:выгрузи|отключи)\s+(?:модел[ьи]|llm)": "llm_unload",
    r"(?:статус|инфо)\s+(?:модел[ьи]|llm)": "llm_status",
    r"(?:очисти|очистить)\s+(?:кэш|cache)": "cache_clear",

    # Цепочки / макросы
    r"(?:макрос|macro)\s+(?:список|list)": "macro_list",
    r"(?:макрос|macro)\s+(?:запусти|run|выполни)\s+(.+)": "macro_run",
    r"(?:макрос|macro)\s+(?:сохрани|save)\s+(.+)": "macro_save",
    r"(?:макрос|macro)\s+(?:удали|delete)\s+(.+)": "macro_delete",

    # История
    r"(?:истори[яю]|history)(?:\s+(\d+))?": "history_show",

    # Инструменты
    r"(?:поиск|найди|ищи|загугли|поищи)\s+(?:в\s+)?(?:инт\w*|инет\w*|веб\w*|сет[ьи]\w*|internet|web)\s*[:\-]?\s*(.+)": "web_search",
    r"(?:загугли|погугли)\s+(.+)": "web_search",
    r"(?:погода|weather)\s*(.*)": "weather",
    r"(?:курс|валют[ыа]|exchange)\s*(.*)": "exchange_rate",
    r"(?:инструменты|tools)\s*(?:разработки)?": "dev_tools",
    r"(?:линт|lint|проверь код)\s+(.+)": "lint_file",
    r"(?:git\s+статус|git\s+status)": "git_status",
    r"(?:git\s+лог|git\s+log)": "git_log",

    # Установка приложений (imperative forms only — "как установить" → web_search)
    r"(?:установи|поставь|инсталлируй)\s+(?:мне\s+)?(?:мессенджер\w*|браузер\w*|редактор\w*|плеер\w*|клиент\w*|утилит\w*|приложени\w*|программ\w*)?\s*(.+)": "install_app",
    r"(?<!как )(?<!где )(?:установить|поставить|инсталлировать)\s+(?:мессенджер\w*|браузер\w*|редактор\w*|плеер\w*|клиент\w*|утилит\w*|приложени\w*|программ\w*)?\s*(.+)": "install_app",
    r"(?:install|setup)\s+(.+)": "install_app",

    # Мониторинг / логи
    r"(?:расширенный\s+)?(?:статус|монитор)\s+(?:llm|лл[мм])": "llm_monitor",
    r"(?:аудит|audit|лог[и]?)\s*(?:последни[еих])?\s*(\d*)": "audit_log",
    r"(?:отчёт|отчет|report)\s*(?:анализ[а]?)?": "analysis_report",

    # Обучение
    r"(?:статистика|стат)\s+(?:обучени[яе]|знани[йя])": "learning_stats",
    r"(?:экспорт|export)\s+(?:знани[йя]|knowledge)": "learning_export",

    # Предустановка Linux
    r"(?:обзор|overview)\s+(?:систем[ыа]|hardware|железа)": "preinstall_overview",
    r"(?:анализ|analyze|помощь)\s+(?:раздел[ыов]|partition|диск[иов])": "preinstall_partitions",
    r"(?:сетевая|network)\s+(?:диагностик[аи]|setup|настройк[аи])": "preinstall_network",
    r"(?:рекомендаци[ия]|suggest)\s+(?:пакет[ыов]|packages?)\s*(.*)": "preinstall_packages",
    r"(?:пакеты|packages)\s+(.+)": "preinstall_packages",
    r"(?:проверка|check)\s+(?:готовност[ьи]|readiness)": "preinstall_check",
    r"(?:гид|guide|инструкция)\s+(?:установк[иа]|install)": "preinstall_guide",
    r"(?:faq|чзв)\s+(?:установк[иа]|install)": "preinstall_faq",
    r"(?:тюнинг|tune|настройк[аи])\s+(?:после\s+)?(?:установк[иа]|post.?install)": "preinstall_tune",
    r"(?:режим\s+)?(?:предустановк[аи]|preinstall)\s*(?:статус|status)?": "preinstall_status",

    # Computer Vision
    r"(?:скриншот|screenshot)\s*(?:экран[аы]?)?": "cv_screenshot",
    r"(?:распознай|распознать|ocr)\s+(?:текст|text)(?:\s+(.+))?": "cv_ocr",
    r"(?:найди|поиск)\s+(?:ошибки|ошибок)\s+(?:на\s+)?(?:экран[еу]?)": "cv_find_errors",
    r"(?:найди|поиск)\s+(?:прогресс|progress)": "cv_find_progress",
    r"(?:анализ|analyze)\s+(?:gui|гуи|интерфейс[аы]?)\s*(?:элемент[ыов]*)?": "cv_detect_gui",
    r"(?:статус|status)\s+(?:cv|зрени[яе])": "cv_status",
    r"(?:список|покажи)\s+(?:скриншот[ыов]*)": "cv_list_screenshots",
    r"(?:анализ|analyze)\s+(?:скриншот[аы]?|screenshot)\s*(.*)": "cv_analyze",

    # FAQ (generic — после preinstall_faq, чтобы "faq установки" не перехватывался)
    r"(?:faq|чзв|частые\s+вопросы)$": "show_faq",

    # Interactive diagnostics (v0.8.0)
    r"(?:диагностик[аи]|diagnos(?:e|tics?))\s+(.+)": "diagnose",
    r"(?:продиагностируй|проверь\s+проблему)\s+(.+)": "diagnose",
    r"(?:список|покажи)\s+(?:диагностик[иу]|diagnos(?:tics|es))": "diagnose_list",
}

META_COMMANDS = {
    "/help": "help",
    "/выход": "exit",
    "/exit": "exit",
    "/quit": "exit",
    "/статус": "status",
    "/status": "status",
    "/clear": "clear_screen",
    "/очистить": "clear_screen",
    "/версия": "version",
    "/version": "version",
    "/verbose": "toggle_verbose",
    "/web": "toggle_web",
    "/notify": "toggle_notify",
    "/history": "history_show",
    "/история": "history_show",
    "/макросы": "macro_list",
    "/macros": "macro_list",
    "/audit": "audit_log",
    "/аудит": "audit_log",
    "/tools": "dev_tools",
    "/инструменты": "dev_tools",
    "/report": "analysis_report",
    "/отчёт": "analysis_report",
    "/preinstall": "preinstall_status",
    "/предустановка": "preinstall_status",
    "/cv": "cv_status",
    "/зрение": "cv_status",
    "/скриншот": "cv_screenshot",
    "/diagnose": "diagnose",
    "/диагностика": "diagnose",
    "/диаг": "diagnose",
}


class Commander:
    """
    Главный командный процессор Lina.

    Принимает ввод пользователя, анализирует, решает какой модуль
    использовать, и возвращает ответ.
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        trace_enabled: bool = False,
        confirm_fn: Optional[Callable[[str], bool]] = None,
    ):
        self.file_manager = FileManager()
        self.executor = CommandExecutor()
        self.monitor = SystemMonitor()
        self.indexer = KnowledgeIndexer()
        self.searcher = KnowledgeSearcher()
        self.llm = LLMEngine()
        self.sandbox = SubprocessSandbox()
        self.history = CommandHistory()
        self.macro_manager = MacroManager()
        self.notifier = DesktopNotifier()
        self.web_tool = WebTool()
        self.ide_tool = IDETool()
        self.api_client = APIClient()
        self.collector = KnowledgeCollector()
        self.analyzer = LogAnalyzer()
        self.hw_scanner = HardwareScanner()
        self.net_scanner = NetworkScanner()
        self.install_guide = InstallGuide()
        self.screen_scanner = ScreenScanner()
        self.ocr_engine = OCREngine()
        self.gui_detector = GUIDetector()
        self._chain_executor = None  # Lazy init

        # ── MainPipeline reference (set from outside via set_pipeline) ──
        self._pipeline_ref = None
        self._in_pipeline_delegation = False

        # ── Session isolation (Phase 15) ──
        self._session_id = session_id or uuid.uuid4().hex[:16]
        self._trace_enabled = trace_enabled
        self._confirm_fn = confirm_fn

        # Legacy buffer (для обратной совместимости с _record)
        self._conversation_buffer: deque = deque(maxlen=5)

        # ── System Context — собираем реальные данные системы ──
        try:
            self._sys_snapshot = collect_system_snapshot()
            self._sys_preprocessor = QueryPreprocessor(self._sys_snapshot)
            self._sys_executor = ActionExecutor(interactive=True)

            # Формируем системный контекст для обеих моделей
            from lina.config import config as lina_config
            sys_context = format_snapshot_for_prompt(self._sys_snapshot)
            try:
                from lina.core.application_resolver import get_resolver
                resolver = get_resolver()
                apps = resolver.find_installed_apps()
                if apps:
                    app_list = ", ".join(a.display_name for a in apps[:40])
                    sys_context += f"Установленные приложения ({len(apps)}): {app_list}\n"
            except Exception as e:
                logger.debug("App resolver init failed: %s", e)
            # Inject в instance-level enriched prompt (NOT global config)
            self._enriched_system_prompt = lina_config.llm.system_prompt + "\n" + sys_context
        except Exception as e:
            logger.warning("System context init failed: %s", e)
            self._sys_snapshot = None
            self._sys_preprocessor = None
            self._sys_executor = None
            sys_context = ""

        # ── Mini LLM — быстрая модель с function-calling ──
        try:
            self._mini_llm = MiniLLMEngine(system_context=sys_context)
            self._mini_llm.load()
            logger.info("MiniLLM: function-calling engine active")
        except Exception as e:
            logger.warning("MiniLLM init failed: %s — will use full LLM for everything", e)
            self._mini_llm = None

    @property
    def session_id(self) -> str:
        """Текущий session ID."""
        return self._session_id

    @property
    def trace_enabled(self) -> bool:
        """Включён ли trace mode."""
        return self._trace_enabled

    @trace_enabled.setter
    def trace_enabled(self, value: bool) -> None:
        self._trace_enabled = value

    def _llm_handler(self, prompt: str, tier: str, **kwargs) -> str:
        """LLM handler adapter with system context enrichment."""
        context = ""
        if self._sys_preprocessor:
            context = self._sys_preprocessor.enrich_for_llm(prompt)
        history = list(self._conversation_buffer) if self._conversation_buffer else None
        return self.llm.generate(prompt, context=context, tier=tier, session_id=self._session_id, history=history)

    # Backward-compatible alias (tests inspect this name)
    _v2_llm_handler = _llm_handler

    @property
    def chain_executor(self) -> ChainExecutor:
        if self._chain_executor is None:
            self._chain_executor = ChainExecutor(self.process)
        return self._chain_executor

    def set_pipeline(self, pipeline) -> None:
        """Inject MainPipeline reference (called from cli.py / gui wiring)."""
        self._pipeline_ref = pipeline

    def process(self, user_input: str) -> str:
        """
        Обрабатывает ввод пользователя и возвращает ответ.

        Логика:
        1. Мета-команды (/help, /exit) → мгновенно.
        2. Системные команды (!) → sandbox subprocess.
        3. Цепочки команд (→, ->) → последовательная обработка.
        4. Макрос-команды → разворачиваются и выполняются.
        5. Встроенные команды → без LLM.
        6. Всё остальное → LLM + RAG контекст.

        Все действия логируются и записываются в историю.
        """
        text = user_input.strip()
        if not text:
            return ""

        start_time = time.time()

        # 1. Мета-команды
        if text.lower() in META_COMMANDS:
            response = self._handle_meta(META_COMMANDS[text.lower()])
            self._record(text, response, start_time)
            return response

        # 1.5. /system * → делегируем в MainPipeline.SystemControl
        if text.lower().startswith("/system"):
            response = self._handle_system_control(text)
            self._record(text, response, start_time)
            return response

        # 2. Системная команда (начинается с !)
        # Phase 5: !commands MUST route through governance — no direct sandbox
        if text.startswith("!"):
            response = self._handle_system_command_governed(text[1:].strip())
            self._record(text, response, start_time)
            return response

        # 3. Цепочки команд (разделители: →, ->, =>, ; затем, ; потом)
        chain = CommandChain.parse(text)
        if chain and len(chain.steps) > 1:
            response = self._handle_chain(chain)
            self._record(text, response, start_time)
            return response

        # 4. Макрос-команды (проверяем, есть ли макрос с таким именем)
        macro_name = text.lower().strip()
        macro = self.macro_manager.get(macro_name)
        if macro:
            response = self._handle_macro(macro)
            self._record(text, response, start_time)
            return response

        # 5. Встроенные команды (по паттернам)
        builtin_result = self._match_builtin(text)
        if builtin_result:
            action, args = builtin_result
            response = self._handle_builtin(action, args)
            self._record(text, response, start_time)
            return response

        # 5.5. Mini LLM — быстрая ИИ-обработка с function-calling
        #      Понимает любую формулировку: приветствия, яркость, громкость,
        #      приложения, системные вопросы. Эскалация в full LLM при необходимости.
        #
        # НО: если предыдущий запрос был web_search и текущий — follow-up,
        # mini LLM не знает контекста поиска → пропускаем в full pipeline.
        _skip_mini = False
        if self._pipeline_ref and hasattr(self._pipeline_ref, '_step_memory'):
            _prev = self._pipeline_ref._step_memory.get_previous()
            if (_prev and _prev.intent == "web_search"
                    and self._pipeline_ref._is_followup_query(text)):
                _skip_mini = True
                logger.info(
                    "Follow-up after web_search — skipping mini LLM: '%s'",
                    text[:60])

        # Skip mini LLM for web_search queries — mini LLM hallucinates on
        # "характеристики X" (returns brightness/volume commands instead).
        # These MUST go through the full pipeline → WebSearchEngine.
        if not _skip_mini:
            try:
                from lina.core.intent_router import IntentRouter as _IR
                _ir = _IR()
                _decision = _ir.route(text)
                if _decision.intent.value == "web_search":
                    _skip_mini = True
                    logger.info(
                        "Web search query — skipping mini LLM: '%s'",
                        text[:60])
            except Exception:
                pass

        if self._mini_llm and self._mini_llm.is_loaded and not _skip_mini:
            try:
                mini_response, needs_full = self._mini_llm.process(text)
                if not needs_full and mini_response:
                    self._conversation_buffer.append((text, mini_response))
                    self._record(text, mini_response, start_time)
                    return mini_response
                if needs_full:
                    # Проверяем: "старшая/большая модель" = загрузить full LLM (не отправлять как запрос)
                    _low = text.lower()
                    _model_switch = any(w in _low for w in ("старш", "больш", "тяжёл", "полн"))
                    _model_kw = any(w in _low for w in ("модел", "llm", "нейросет", "брат", "верси", "нейронк"))
                    if _model_switch and _model_kw:
                        # Пользователь хочет переключиться на полную модель
                        if self.llm.is_loaded:
                            response = "✅ Полная модель уже загружена. Задавайте вопрос."
                        else:
                            success = self.llm.load("full")
                            response = "✅ Полная модель загружена. Задавайте вопрос." if success else "❌ Не удалось загрузить полную модель."
                        self._record(text, response, start_time)
                        return response
                    # Обычная эскалация → отправляем в full LLM
            except Exception as e:
                logger.debug("Mini LLM error: %s — falling through to full LLM", e)

        # 6. Full LLM + RAG (тяжёлая модель для сложных задач)
        response = self._handle_llm_query(text)
        self._record(text, response, start_time)
        return response

    def _record(self, command: str, response: str, start_time: float) -> None:
        """Записывает команду в историю, лог и коллектор знаний."""
        elapsed = time.time() - start_time

        # История
        success = not response.startswith("❌")
        self.history.add(command, response[:500], success=success)

        # Аудит-лог
        logger.audit_command(command, len(response), elapsed)

        # Коллектор знаний
        if config.learning.collect_fragments:
            self.collector.record_interaction(command, response)

    def _match_builtin(self, text: str) -> Optional[Tuple[str, str]]:
        """Сопоставляет текст со встроенными паттернами."""
        text_lower = text.lower()
        for pattern, action in BUILTIN_PATTERNS.items():
            match = re.search(pattern, text_lower)
            if match:
                if match.lastindex:
                    # Извлекаем аргумент из ОРИГИНАЛЬНОГО текста (сохраняем регистр)
                    start, end = match.start(1), match.end(1)
                    args = text[start:end]
                else:
                    args = ""
                return action, args.strip()
        return None

    # ── Обработчики мета-команд ──

    def _handle_meta(self, action: str) -> str:
        """Обрабатывает мета-команды."""
        if action == "help":
            return self._get_help()
        elif action == "exit":
            return "__EXIT__"
        elif action == "status":
            return self._get_full_status()
        elif action == "clear_screen":
            import sys as _sys
            _sys.stdout.write("\033c")
            _sys.stdout.flush()
            return ""
        elif action == "version":
            from lina import __version__
            return f"Lina v{__version__} — Гибрид + RAG + Цепочки + Web + Инструменты + Предустановка + CV"
        elif action == "toggle_verbose":
            config.verbose = not config.verbose
            state = "включен" if config.verbose else "выключен"
            return f"Подробный режим: {state}"
        elif action == "toggle_web":
            config.web.enabled = not config.web.enabled
            state = "включен" if config.web.enabled else "выключен"
            return f"Веб-интерфейс: {state}"
        elif action == "toggle_notify":
            config.notify.enabled = not config.notify.enabled
            state = "включены" if config.notify.enabled else "выключены"
            return f"Уведомления: {state}"
        elif action == "history_show":
            return self.history.format_recent(20)
        elif action == "macro_list":
            return self._handle_builtin("macro_list", "")
        elif action == "audit_log":
            return self._handle_builtin("audit_log", "")
        elif action == "dev_tools":
            return self._handle_builtin("dev_tools", "")
        elif action == "analysis_report":
            return self._handle_builtin("analysis_report", "")
        elif action == "preinstall_status":
            return self._handle_builtin("preinstall_status", "")
        elif action == "cv_status":
            return self._handle_builtin("cv_status", "")
        elif action == "cv_screenshot":
            return self._handle_builtin("cv_screenshot", "")
        elif action == "diagnose":
            return self._handle_builtin("diagnose", "")
        return ""

    def _handle_system_control(self, text: str) -> str:
        """Делегирует /system * команды в MainPipeline.SystemControl.

        Если MainPipeline ещё не подключен — возвращает ошибку.
        """
        if (self._pipeline_ref
                and hasattr(self._pipeline_ref, '_system_control')):
            result = self._pipeline_ref._system_control.handle(text)
            return result if result else "⚠️ Пустой ответ от SystemControl."
        return ("⚠️ MainPipeline не подключен — /system команды недоступны.\n"
                "Используйте /статус для базовой информации.")

    # ── Обработчики встроенных команд ──

    def _handle_builtin(self, action: str, args: str) -> str:
        """Обрабатывает встроенные команды."""
        try:
            if action == "list_files":
                path = args or "."
                items = self.file_manager.list_directory(path)
                return self._format_file_list(items)

            elif action == "dir_tree":
                path = args or "."
                return self.file_manager.get_directory_tree(path)

            elif action == "read_file":
                return self.file_manager.read_file(args, max_lines=100)

            elif action == "search_files":
                parts = args.split(" в ", 1)
                if len(parts) == 2:
                    pattern, directory = parts
                else:
                    pattern, directory = args, "."
                files = self.file_manager.search_files(directory, f"*{pattern}*")
                if files:
                    return "\n".join(f"  📄 {f}" for f in files[:20])
                return f"Файлы по запросу '{args}' не найдены."

            elif action == "system_status":
                return self.monitor.format_status()

            elif action == "top_processes":
                procs = self.monitor.get_top_processes(10)
                lines = ["Топ процессов по RAM:"]
                for p in procs:
                    lines.append(
                        f"  {p['pid']:>6}  {p['name']:<25} "
                        f"{p['memory_mb']:>8.1f} MB  CPU: {p['cpu_percent']:.1f}%"
                    )
                return "\n".join(lines)

            elif action == "display_info":
                return self._get_display_info()

            elif action == "index_knowledge":
                result = self.indexer.index_documents()
                return result["message"]

            elif action == "rag_stats":
                stats = self.indexer.get_stats()
                return (
                    f"База знаний:\n"
                    f"  Коллекция: {stats.get('collection', '?')}\n"
                    f"  Чанков: {stats.get('total_chunks', 0)}\n"
                    f"  Хранилище: {stats.get('persist_dir', '?')}"
                )

            elif action == "rag_clear":
                result = self.indexer.clear()
                return result["message"]

            elif action == "rag_search":
                results = self.searcher.search(args)
                if not results:
                    return f"Ничего не найдено по запросу: '{args}'"
                lines = [f"Результаты поиска по '{args}':"]
                for r in results:
                    lines.append(
                        f"\n  📎 {r['filename']} (релевантность: {r['score']:.2f})\n"
                        f"  {r['text'][:300]}..."
                    )
                return "\n".join(lines)

            elif action == "llm_load":
                success = self.llm.load("full")
                return "✅ Модель загружена." if success else "❌ Не удалось загрузить модель."

            elif action == "llm_load_full":
                success = self.llm.load("full")
                return "✅ Полная модель загружена." if success else "❌ Не удалось загрузить полную модель."

            elif action == "llm_unload":
                self.llm.unload()
                return "✅ Модель выгружена."

            elif action == "llm_status":
                return self.llm.format_status()

            elif action == "cache_clear":
                self.llm.clear_cache()
                return "✅ Кэш очищен."

            # ── Цепочки / макросы ──

            elif action == "macro_list":
                return self.macro_manager.format_list()

            elif action == "macro_run":
                macro = self.macro_manager.get(args)
                if not macro:
                    return f"Макрос '{args}' не найден."
                return self._handle_macro(macro)

            elif action == "macro_save":
                # Формат: макрос сохрани <имя> <шаг1> → <шаг2>
                parts = args.split(None, 1)
                if len(parts) < 2:
                    return "Формат: макрос сохрани <имя> <шаг1> → <шаг2>"
                name, commands_str = parts
                chain = CommandChain.parse(commands_str)
                if not chain or len(chain.steps) < 2:
                    return "Нужно минимум 2 шага, разделённые → или ->"
                self.macro_manager.save_macro(name, chain)
                return f"✅ Макрос '{name}' сохранён ({len(chain.steps)} шагов)"

            elif action == "macro_delete":
                ok = self.macro_manager.delete_macro(args)
                return f"✅ Макрос '{args}' удалён." if ok else f"Макрос '{args}' не найден."

            # ── История ──

            elif action == "history_show":
                n = int(args) if args and args.isdigit() else 20
                return self.history.format_recent(n)

            # ── Инструменты ──

            elif action == "install_app":
                # Прямой вызов install_app через ApplicationResolver
                app_name = args.strip()
                if not app_name:
                    return "❌ Не указано название приложения для установки."
                try:
                    from lina.core.application_resolver import get_resolver
                    resolver = get_resolver()
                    suggestions = resolver.suggest_installation(app_name)
                    if not suggestions:
                        return (
                            f"⚠️ Приложение «{app_name}» не найдено ни в одном "
                            f"репозитории. Попробуйте поискать в интернете: "
                            f"«загугли {app_name} linux установка»"
                        )
                    real = [s for s in suggestions if s.method != "web"]
                    web = [s for s in suggestions if s.method == "web"]
                    lines = []
                    if real:
                        lines.append(f"📦 Варианты установки «{app_name}»:\n")
                        for i, s in enumerate(real, 1):
                            method = s.method.upper()
                            cmd = s.command or ""
                            note = f" — {s.note}" if s.note else ""
                            src = f" ({s.source})" if s.source else ""
                            if cmd:
                                lines.append(f"  {i}. [{method}] {cmd}{src}{note}")
                            elif s.url:
                                lines.append(f"  {i}. [{method}] 🌐 {s.url}{note}")
                    else:
                        lines.append(
                            f"⚠️ «{app_name}» не найден в системных "
                            f"репозиториях (pacman, AUR, flatpak, snap)."
                        )
                    for s in web:
                        if s.note:
                            lines.append(f"\n🌐 Из интернета:\n  {s.note}")
                        if s.url:
                            lines.append(f"  🔗 {s.url}")
                    return "\n".join(lines)
                except Exception as e:
                    return f"❌ Ошибка поиска установки: {e}"

            elif action == "web_search":
                results = self.web_tool.search_duckduckgo(args)
                if not results:
                    return f"Ничего не найдено по запросу: '{args}'"
                lines = [f"🔍 Результаты поиска: '{args}'"]
                for r in results:
                    lines.append(f"\n  🔗 {r['title']}")
                    lines.append(f"     {r['url']}")
                    if r.get("snippet"):
                        lines.append(f"     {r['snippet'][:200]}")
                return "\n".join(lines)

            elif action == "weather":
                city = args.strip() or "Moscow"
                return self.api_client.get_weather(city)

            elif action == "exchange_rate":
                parts = args.strip().split() if args.strip() else []
                base = parts[0].upper() if len(parts) > 0 else "USD"
                target = parts[1].upper() if len(parts) > 1 else "RUB"
                return self.api_client.get_exchange_rate(base, target)

            elif action == "dev_tools":
                return self.ide_tool.format_tools_report()

            elif action == "lint_file":
                result = self.ide_tool.lint_python(args)
                if not result.get("success"):
                    return f"❌ {result.get('error', 'Ошибка линтинга')}"
                lines = ["📝 Результаты линтинга:"]
                for tool_name, data in result.get("results", {}).items():
                    if data.get("clean"):
                        lines.append(f"  ✅ {tool_name}: чисто")
                    elif data.get("issues"):
                        lines.append(f"  ⚠ {tool_name}:")
                        for issue in data["issues"][:10]:
                            lines.append(f"    {issue}")
                return "\n".join(lines)

            elif action == "git_status":
                result = self.ide_tool.git_status()
                if not result.get("success"):
                    return f"❌ {result.get('error', 'Не git-репозиторий')}"
                return result.get("output") or "Рабочая директория чистая."

            elif action == "git_log":
                result = self.ide_tool.git_log()
                if not result.get("success"):
                    return f"❌ {result.get('error', 'Ошибка')}"
                return "\n".join(result.get("commits", []))

            # ── Мониторинг / логи ──

            elif action == "llm_monitor":
                return self.monitor.format_extended_status()

            elif action == "audit_log":
                n = int(args) if args and args.isdigit() else 20
                entries = logger.get_recent_audit(n)
                if not entries:
                    return "Аудит-лог пуст."
                lines = [f"📋 Последние {len(entries)} записей аудита:"]
                for e in entries:
                    act = e.get("action", "?")
                    t = e.get("time", "?")
                    cmd = e.get("command", "")[:60]
                    ok = "✅" if e.get("success", True) else "❌"
                    lines.append(f"  {ok} [{t}] {act}: {cmd}")
                return "\n".join(lines)

            elif action == "analysis_report":
                return self.analyzer.generate_report()

            # ── Обучение ──

            elif action == "learning_stats":
                stats = self.collector.get_stats()
                return (
                    f"📊 Статистика обучения:\n"
                    f"  Фрагментов: {stats['total_fragments']}\n"
                    f"  Уникальных вопросов: {stats['unique_questions']}\n"
                    f"  Среднее качество: {stats['avg_quality']}\n"
                    f"  FAQ: {stats['faq_count']}\n"
                    f"  Готово к экспорту: {stats['exportable']}"
                )

            elif action == "learning_export":
                count = self.collector.export_to_knowledge()
                if count:
                    return f"✅ Экспортировано {count} фрагментов в knowledge/auto_learned/"
                return "Нет фрагментов достаточного качества для экспорта."

            elif action == "show_faq":
                faq = self.collector.get_faq()
                if not faq:
                    return "FAQ пока пуст (нужно минимум 3 повторения вопроса)."
                lines = ["❓ Часто задаваемые вопросы:"]
                for i, f in enumerate(faq[:10], 1):
                    lines.append(f"\n  {i}. {f['question']} ({f['frequency']}x)")
                    lines.append(f"     {f['answer'][:200]}...")
                return "\n".join(lines)

            # ── Предустановка Linux ──

            elif action == "preinstall_overview":
                return self.hw_scanner.system_overview()

            elif action == "preinstall_partitions":
                return self.hw_scanner.partition_assist()

            elif action == "preinstall_network":
                return self.net_scanner.network_setup()

            elif action == "preinstall_packages":
                return self.install_guide.package_suggestions(args)

            elif action == "preinstall_check":
                return self.hw_scanner.pre_install_check()

            elif action == "preinstall_guide":
                return self.install_guide.installation_guide()

            elif action == "preinstall_faq":
                return self.install_guide.auto_faq_update()

            elif action == "preinstall_tune":
                return self.install_guide.post_install_tune()

            elif action == "preinstall_status":
                return self._get_preinstall_status()

            # ── Computer Vision ──

            elif action == "cv_screenshot":
                result = self.screen_scanner.take_screenshot()
                if result["success"]:
                    return (
                        f"📸 Скриншот сохранён:\n"
                        f"  Файл: {result['path']}\n"
                        f"  Размер: {result['width']}x{result['height']}\n"
                        f"  Вес: {result['size_kb']} KB"
                    )
                return f"❌ Ошибка скриншота: {result.get('error', 'неизвестно')}"

            elif action == "cv_ocr":
                # Если передан путь — используем его, иначе делаем скриншот
                image_path = args
                if not image_path:
                    shot = self.screen_scanner.take_screenshot()
                    if not shot["success"]:
                        return f"❌ Не удалось сделать скриншот: {shot.get('error')}"
                    image_path = shot["path"]
                return self.ocr_engine.format_analysis(image_path)

            elif action == "cv_find_errors":
                # Делаем скриншот + OCR + ошибки
                shot = self.screen_scanner.take_screenshot()
                if not shot["success"]:
                    return f"❌ Не удалось сделать скриншот: {shot.get('error')}"
                ocr_result = self.ocr_engine.recognize_text(shot["path"])
                if not ocr_result["success"]:
                    return f"❌ OCR не удался: {ocr_result.get('error')}"
                errors = self.ocr_engine.find_errors()
                lines = [f"🔍 Поиск ошибок на экране ({shot['path']}):"]
                lines.append(f"  📝 Распознано: {ocr_result['line_count']} строк")
                lines.append(f"  {errors['summary']}")
                for err in errors["errors"][:10]:
                    lines.append(f"    ❌ [строка {err['line']}]: {err['text'][:80]}")
                for warn in errors["warnings"][:10]:
                    lines.append(f"    ⚠ [строка {warn['line']}]: {warn['text'][:80]}")
                return "\n".join(lines)

            elif action == "cv_find_progress":
                shot = self.screen_scanner.take_screenshot()
                if not shot["success"]:
                    return f"❌ Не удалось сделать скриншот: {shot.get('error')}"
                ocr_result = self.ocr_engine.recognize_text(shot["path"])
                if not ocr_result["success"]:
                    return f"❌ OCR не удался: {ocr_result.get('error')}"
                progress = self.ocr_engine.find_progress()
                lines = ["📊 Поиск прогресса на экране:"]
                if progress["found"]:
                    lines.append(f"  Общий прогресс: ~{progress['estimated_percent']}%")
                    for item in progress["items"][:5]:
                        if item["type"] == "percent":
                            lines.append(f"  ▶ {item['value']}% — {item['text'][:60]}")
                        elif item["type"] == "counter":
                            lines.append(f"  ▶ {item['current']}/{item['total']} — {item['text'][:60]}")
                else:
                    lines.append("  Индикаторы прогресса не найдены.")
                return "\n".join(lines)

            elif action == "cv_detect_gui":
                shot = self.screen_scanner.take_screenshot()
                if not shot["success"]:
                    return f"❌ Не удалось сделать скриншот: {shot.get('error')}"
                return self.gui_detector.format_detection_report(shot["path"])

            elif action == "cv_status":
                return self.screen_scanner.format_status()

            elif action == "cv_list_screenshots":
                screenshots = self.screen_scanner.list_screenshots()
                if not screenshots:
                    return "Нет сохранённых скриншотов."
                lines = [f"📸 Скриншоты ({len(screenshots)}):"]
                for s in screenshots[:15]:
                    lines.append(f"  📄 {s['name']}  ({s['size_kb']} KB)  {s['modified']}")
                return "\n".join(lines)

            elif action == "cv_analyze":
                image_path = args
                if not image_path:
                    screenshots = self.screen_scanner.list_screenshots(1)
                    if screenshots:
                        image_path = screenshots[0]["path"]
                    else:
                        return "Нет скриншотов. Сначала: скриншот экрана"
                # Полный анализ: OCR + GUI
                lines = []
                ocr_report = self.ocr_engine.format_analysis(image_path)
                lines.append(ocr_report)
                gui_report = self.gui_detector.format_detection_report(image_path)
                lines.append(gui_report)
                return "\n\n".join(lines)

            # ── Interactive Diagnostics (v0.8.0) ──

            elif action == "diagnose":
                from lina.diagnostics.session import DiagnosticSession
                session = DiagnosticSession()
                if not args:
                    return session.list_available()
                report = session.start(args)
                return session.format_session()

            elif action == "diagnose_list":
                from lina.diagnostics.session import DiagnosticSession
                session = DiagnosticSession()
                return session.list_available()

        except Exception as e:
            logger.exception("Commander._handle_builtin error for action '%s'", action)
            return "❌ Внутренняя ошибка при выполнении команды."

        return f"⚠ Неизвестная встроенная команда: {action}"

    # ── Display info ──

    def _get_display_info(self) -> str:
        """Get display/monitor info using available tools."""
        import subprocess, math, glob
        lines = ["🖥️ Информация о дисплее:\n"]

        # 1. Get resolution/mode from kscreen-doctor (Wayland KDE)
        resolution = ""
        for cmd in [
            ["kscreen-doctor", "--outputs"],
            ["wlr-randr"],
            ["xrandr", "--current"],
        ]:
            try:
                r = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout.strip():
                    for line in r.stdout.strip().split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        # Extract current mode (marked with *)
                        import re as _re_disp
                        m = _re_disp.search(r'(\d{3,5}x\d{3,5})@[\d.]+\*', line)
                        if m:
                            resolution = m.group(1)
                        lines.append(f"  {line}")
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        # 2. Get physical size from EDID → calculate diagonal
        for edid_path in sorted(glob.glob("/sys/class/drm/*/edid")):
            try:
                with open(edid_path, "rb") as f:
                    edid = f.read(128)
                if len(edid) < 23:
                    continue
                w_cm = edid[21]
                h_cm = edid[22]
                if w_cm == 0 or h_cm == 0:
                    continue
                diag_cm = math.sqrt(w_cm ** 2 + h_cm ** 2)
                diag_in = diag_cm / 2.54
                lines.append(f"\n  📐 Физический размер: {w_cm} × {h_cm} см")
                lines.append(f"  📐 Диагональ: {diag_in:.1f}\" ({diag_cm:.1f} см)")
                if resolution:
                    lines.append(f"  📐 Разрешение: {resolution}")
                break
            except Exception:
                continue

        if len(lines) <= 1:
            return "Не удалось определить параметры дисплея. Попробуйте: xrandr --current"
        return "\n".join(lines)

    # ── Системные команды ──

    def _handle_system_command(self, command: str) -> str:
        """Legacy direct sandbox execution — DEPRECATED Phase 5.
        All system commands must now go through _handle_system_command_governed.
        Kept for internal/programmatic use only where governance was already checked.
        """
        logger.warning("Commander._handle_system_command: direct sandbox call "
                       "(should use governed path)")
        if not command:
            return "Укажите команду после '!'. Пример: !ls -la"

        result = self.sandbox.execute(command)

        output = ""
        if result["stdout"]:
            output += result["stdout"]
        if result["stderr"]:
            if output:
                output += "\n"
            output += f"[stderr] {result['stderr']}"

        if not result["success"] and not output:
            output = f"Команда завершилась с кодом: {result['returncode']}"

        return output

    def _handle_system_command_governed(self, command: str) -> str:
        """Phase 5: System commands routed through governance pipeline."""
        if not command:
            return "Укажите команду после '!'. Пример: !ls -la"

        try:
            from lina.intent.bridge import get_intent_bridge
            from lina.intent.types import IntentStatus

            bridge = get_intent_bridge()
            result = bridge.from_action(
                action_id="shell_execute",
                domain="system",
                params={"command": command},
                source="cli",
            )

            if result.status == IntentStatus.SUCCESS:
                return result.response_text or "✅ Выполнено."
            elif result.status == IntentStatus.DENIED:
                return f"🚫 Команда заблокирована governance: {result.response_text}"
            elif result.status == IntentStatus.NEEDS_CONFIRM:
                esc_id = result.escalation_id or ""
                return (
                    f"⚠ Требуется подтверждение для: !{command}\n"
                    f"/confirm {esc_id} — подтвердить\n"
                    f"/deny {esc_id} — отклонить"
                )
            else:
                return result.response_text or "Не удалось выполнить команду."

        except Exception as e:
            logger.error("Commander: governed system command failed: %s", e)
            return "❌ Ошибка выполнения команды. Попробуйте ещё раз."

    # ── LLM + RAG запросы ──

    def _handle_chain(self, chain: CommandChain) -> str:
        """Обрабатывает цепочку команд."""
        logger.info(f"Chain: {len(chain.steps)} steps")

        results = []
        for i, step in enumerate(chain.steps, 1):
            step_response = self.process(step.command)
            results.append(f"[Шаг {i}] {step.command}\n{step_response}")

        if config.notify.enabled and config.notify.on_chain_complete:
            self.notifier.chain_complete(len(chain.steps), True)

        return "\n\n".join(results)

    def _handle_macro(self, macro: "CommandChain") -> str:
        """Выполняет макрос (CommandChain)."""
        steps = macro.steps
        if not steps:
            return "Макрос пуст."

        logger.info(f"Macro: {macro.name} ({len(steps)} steps)")

        results = []
        for i, step in enumerate(steps, 1):
            step_response = self.process(step.command)
            results.append(f"[{i}/{len(steps)}] {step.command}\n{step_response}")

        return "\n\n".join(results)

    def _handle_llm_query(self, query: str) -> str:
        """
        Обрабатывает запрос через MainPipeline (unified path).

        Phase 27: ВСЕ LLM-запросы проходят через MainPipeline.process_request(),
        который включает security pre-check (step_00), intent classification,
        PipelineV3 для web_search, LLM generation, post-processing, guard.

        Eliminates split-brain: ранее _handle_llm_query_v3 обходила security.
        """
        # ── Recursion guard — prevent MainPipeline→tool_executor→Commander→MainPipeline loop ──
        if getattr(self, '_in_pipeline_delegation', False):
            # Called from MainPipeline's tool_executor — no builtin matched,
            # return empty to let MainPipeline use its own LLM executor.
            return ""

        self._in_pipeline_delegation = True
        try:
            from lina.core.main_pipeline import MainPipeline

            if not hasattr(self, '_pipeline_ref') or self._pipeline_ref is None:
                self._pipeline_ref = MainPipeline()
                # Wire minimal executors for standalone Commander usage
                self._pipeline_ref.set_llm_executor(
                    lambda ctx: self._llm_handler(ctx.user_input, "full")
                )

            result = self._pipeline_ref.process_request(
                query, session_id=self._session_id
            )

            text = result.text or ""

            # Update conversation buffer
            if text:
                self._conversation_buffer.append((query, text))

            return text
        except Exception as exc:
            logger.error("Commander→MainPipeline delegation failed: %s", exc)
            # Last-resort fallback: direct LLM query
            try:
                return self._llm_handler(query, "full")
            except Exception:
                return "⚠ Произошла внутренняя ошибка."
        finally:
            self._in_pipeline_delegation = False

    # ── v3 Pipeline handler (deprecated — unified through MainPipeline) ──

    def _handle_llm_query_v3(self, query: str) -> str:
        """Deprecated: PipelineV3 now runs inside MainPipeline step_07."""
        return self._handle_llm_query(query)

    def _handle_llm_query_legacy(self, query: str) -> str:
        """Deprecated: legacy path now unified through MainPipeline."""
        return self._handle_llm_query(query)

    # ── Форматирование ──

    def _format_file_list(self, items: list) -> str:
        """Форматирует список файлов."""
        if not items:
            return "Директория пуста."

        lines = []
        for item in items:
            if item["type"] == "dir":
                lines.append(f"  📁 {item['name']}/")
            else:
                size = item.get("size_human", "")
                lines.append(f"  📄 {item['name']}  ({size})")

        return f"Найдено {len(items)} элементов:\n" + "\n".join(lines)

    def _get_help(self) -> str:
        """Возвращает справку по командам."""
        return """
╔═══════════════════════════════════════════════════════════════╗
║                    Lina v0.4.0 — Справка                   ║
╠═══════════════════════════════════════════════════════════════╣
║                                                               ║
║  📁 Файлы и директории:                                      ║
║    "покажи файлы <путь>"      — список файлов                ║
║    "дерево каталога <путь>"   — дерево директорий             ║
║    "прочитай файл <путь>"     — содержимое файла              ║
║    "найди файлы <шаблон>"     — поиск файлов                  ║
║                                                               ║
║  💻 Системные команды:                                        ║
║    !<команда>                 — shell-команда (sandbox)       ║
║    "статус системы"           — CPU, RAM, swap                ║
║    "процессы"                 — топ процессов по RAM          ║
║                                                               ║
║  📚 База знаний (RAG — BM25 + n-gram):                       ║
║    "индексируй"              — индексировать документы        ║
║    "статус базы знаний"      — информация о базе             ║
║    "поиск в базе: <запрос>"  — поиск по базе знаний          ║
║    "история [N]"             — последние N команд             ║
║                                                               ║
║  🤖 LLM (гибрид: мини + полная):                              ║
║    "загрузи мини модель"     — загрузить мини LLM             ║
║    "загрузи полную модель"   — загрузить полную LLM           ║
║    "выгрузи модель"          — выгрузить LLM                  ║
║    "статус модели"           — информация о LLM               ║
║                                                               ║
║  🔗 Цепочки и макросы:                                        ║
║    "шаг1 → шаг2 → шаг3"     — цепочка команд                ║
║    "макрос список"           — список макросов                ║
║    "макрос сохрани <имя> ..."— сохранить макрос               ║
║    "макрос запусти <имя>"    — выполнить макрос               ║
║                                                               ║
║  🌐 Инструменты:                                              ║
║    "поиск в интернете: ..."  — веб-поиск (DuckDuckGo)        ║
║    "погода [город]"          — текущая погода                  ║
║    "курс USD RUB"            — курс валют                     ║
║    "инструменты"             — доступные dev-инструменты      ║
║    "линт <файл>"             — проверить Python-код           ║
║    "git статус"              — git status                     ║
║                                                               ║
║  📊 Мониторинг:                                               ║
║    "статус llm"              — мониторинг LLM-процессов       ║
║    "аудит [N]"               — последние N записей аудита     ║
║    /отчёт                    — полный отчёт анализа           ║
║                                                               ║
║  🧠 Обучение:                                                 ║
║    "статистика обучения"     — статистика коллектора          ║
║    "экспорт знаний"          — экспорт в knowledge/           ║
║    "faq"                     — часто задаваемые вопросы       ║
║                                                               ║
║  🐧 Предустановка Linux:                                      ║
║    "обзор системы"           — CPU, GPU, RAM, диски, UEFI    ║
║    "анализ разделов"         — разделы + рекомендации         ║
║    "сетевая диагностика"     — интерфейсы, подключение       ║
║    "рекомендации пакетов"    — пакеты по профилю             ║
║    "пакеты <профиль>"        — конкретный профиль пакетов    ║
║    "проверка готовности"     — проверка перед установкой      ║
║    "гид установки"           — пошаговая инструкция          ║
║    "faq установки"           — FAQ по установке Linux        ║
║    "тюнинг после установки"  — настройка после установки     ║
║    /предустановка            — статус режима предустановки    ║
║                                                               ║
║  👁 Computer Vision (CV):                                      ║
║    "скриншот экрана"         — захват скриншота               ║
║    "распознай текст"         — OCR скриншота                  ║
║    "найди ошибки на экране"  — поиск ошибок (OCR + анализ)   ║
║    "найди прогресс"          — поиск индикаторов прогресса   ║
║    "анализ gui элементов"    — детекция GUI (кнопки, окна)   ║
║    "список скриншотов"       — сохранённые скриншоты         ║
║    "анализ скриншота [путь]" — полный OCR + GUI анализ       ║
║    /cv                       — статус CV-модуля              ║
║                                                               ║
║  🩺 Диагностика (v0.8.0):                                    ║
║    "диагностика нет интернета" — интерактивная диагностика   ║
║    "список диагностик"         — все доступные деревья       ║
║    /diagnose  /диагностика     — запустить диагностику       ║
║                                                               ║
║  ⚙ Мета-команды:                                             ║
║    /help     /статус    /версия    /verbose                   ║
║    /web      /notify    /история   /макросы                   ║
║    /аудит    /отчёт     /инструменты  /выход                  ║
║    /предустановка  /cv   /скриншот  /диаг                     ║
║                                                               ║
║  🔧 Pipeline (/system <подкоманда>):                          ║
║    /system status      — полный статус pipeline               ║
║    /system router      — интент-роутер                        ║
║    /system guard       — production guard                     ║
║    /system trace       — execution traces                     ║
║    /system budget      — контекстный бюджет                   ║
║    /system drift       — state drift                          ║
║    /system consistency — consistency engine                    ║
║    /system intentlock  — intent lock                          ║
║    /system performance — производительность                   ║
║                                                               ║
║  💬 Всё остальное → запрос к LLM (с контекстом из RAG)       ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
"""

    def _get_preinstall_status(self) -> str:
        """Возвращает статус предустановочного режима."""
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║      🐧 Предустановочный режим Linux             ║")
        lines.append("╠══════════════════════════════════════════════════╣")
        lines.append(f"║  Режим: {'ВКЛ' if config.preinstall.enabled else 'ВЫКЛ'}")

        # CPU
        cpu = self.hw_scanner.get_cpu_info()
        lines.append(f"║  CPU: {cpu['model'][:45]}")
        lines.append(f"║  RAM: {self.hw_scanner.get_ram_info()['total_mb']} MB")

        # Загрузка
        boot = self.hw_scanner.get_boot_mode()
        lines.append(f"║  Загрузка: {boot['mode']}")

        # Диски
        disks = self.hw_scanner.get_disk_info()
        disk_count = len([d for d in disks if d["type"] == "disk"])
        lines.append(f"║  Дисков: {disk_count}")

        # Сеть
        ifaces = self.net_scanner.get_interfaces()
        up_count = len([i for i in ifaces if i["state"] == "UP"])
        lines.append(f"║  Сеть: {up_count}/{len(ifaces)} интерфейсов активно")

        # FAQ
        guide_stats = self.install_guide.get_stats()
        lines.append(f"║  FAQ: {guide_stats['faq_count']} записей")
        lines.append(f"║  Профилей пакетов: {guide_stats['profiles_count']}")

        lines.append("║")
        lines.append("║  Команды: обзор системы, анализ разделов,")
        lines.append("║           сетевая диагностика, проверка готовности,")
        lines.append("║           гид установки, пакеты <профиль>,")
        lines.append("║           faq установки, тюнинг после установки")
        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    def _get_full_status(self) -> str:
        """Возвращает полный статус всех подсистем."""
        parts = []

        # Система
        parts.append(self.monitor.format_extended_status())

        # LLM
        parts.append(self.llm.format_status())

        # RAG
        try:
            stats = self.indexer.get_stats()
            parts.append(
                f"📚 База знаний: {stats.get('total_chunks', 0)} чанков"
            )
        except Exception:
            parts.append("📚 База знаний: недоступна")

        # История
        hist_stats = self.history.get_stats()
        parts.append(
            f"📜 История: {hist_stats.get('total', 0)} команд "
            f"(успешных: {hist_stats.get('successful', 0)})"
        )

        # Макросы
        macros = self.macro_manager.list_macros()
        parts.append(f"🔗 Макросов: {len(macros)}")

        # Обучение
        learn_stats = self.collector.get_stats()
        parts.append(
            f"🧠 Знания: {learn_stats['total_fragments']} фрагментов "
            f"(качество: {learn_stats['avg_quality']})"
        )

        # Аудит
        audit_stats = logger.get_stats()
        parts.append(f"📋 Аудит: {audit_stats.get('total_actions', 0)} записей")

        # Предустановка
        guide_stats = self.install_guide.get_stats()
        parts.append(
            f"🐧 Предустановка: {'вкл' if config.preinstall.enabled else 'выкл'} "
            f"(FAQ: {guide_stats['faq_count']}, профилей: {guide_stats['profiles_count']})"
        )

        # Computer Vision
        cv_caps = self.screen_scanner.get_capabilities()
        cv_status_parts = []
        if cv_caps["screenshot"]:
            cv_status_parts.append("скриншоты")
        if cv_caps["image_processing"]:
            cv_status_parts.append("обработка")
        if self.ocr_engine.available:
            cv_status_parts.append("OCR")
        if self.gui_detector.available:
            cv_status_parts.append("детекция")
        cv_info = ", ".join(cv_status_parts) if cv_status_parts else "не установлены зависимости"
        parts.append(
            f"👁 CV: {'вкл' if config.cv.enabled else 'выкл'} ({cv_info})"
        )

        # Конфигурация
        parts.append(
            f"\n⚙ Конфигурация:\n"
            f"  Модель: {config.llm.full.model_path}\n"
            f"  Лимит RAM: {config.resources.max_ram_mb} MB\n"
            f"  Web: {'вкл' if config.web.enabled else 'выкл'}\n"
            f"  Notify: {'вкл' if config.notify.enabled else 'выкл'}\n"
            f"  Кэш: {'вкл' if config.cache.enabled else 'выкл'}\n"
            f"  Обучение: {'вкл' if config.learning.collect_fragments else 'выкл'}\n"
            f"  Предустановка: {'вкл' if config.preinstall.enabled else 'выкл'}\n"
            f"  CV: {'вкл' if config.cv.enabled else 'выкл'}\n"
            f"  Verbose: {'вкл' if config.verbose else 'выкл'}"
        )

        return "\n".join(parts)
