"""
Lina — Runtime (главный модуль запуска).

Объединяет: bootstrap, CLI, output isolation, REPL.
Единственный модуль, который знает о всех компонентах запуска.

Поток запуска:
  1. bootstrap() — faulthandler, sys.path
  2. parse_args() — разбор CLI
  3. apply_config() — применение аргументов к config
  4. detect_output_mode() → SafePrinter
  5. print_startup_info() — баннер, статус
  6. REPLSession.run() или run_oneshot()

Phase 15: CLI полностью подключён к RuntimeAPI v2.
Session ID, trace mode, sandbox confirmation.
"""

import os
import sys
import uuid
import logging
from typing import Optional

from lina.core.bootstrap import bootstrap, setup_signal_handlers
from lina.core.cli import LinaArgs, parse_args
from lina.core.output import (
    OutputMode, SafePrinter, detect_output_mode, get_printer, reset_printer,
)
from lina.core.repl import REPLSession
from lina.config import config, KNOWLEDGE_DIR
from lina.system.logger import logger as lina_logger

logger = logging.getLogger("lina.core.runtime")


# ── ASCII-баннер ──────────────────────────────────────────────────────────────

BANNER = r"""
██╗     ██╗███╗   ██╗ █████╗
██║     ██║████╗  ██║██╔══██╗
██║     ██║██╔██╗ ██║███████║
██║     ██║██║╚██╗██║██╔══██║
███████╗██║██║ ╚████║██║  ██║
╚══════╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝

  ИИ-ассистент v0.5.0 — Гибрид + RAG + Web + Tools + Preinstall + CV
  Введите /help для справки, /выход для выхода.
"""

VERSION = "0.5.0"


def apply_config(args: LinaArgs) -> None:
    """
    Применяет аргументы CLI к глобальной конфигурации.

    Args:
        args: Разобранные аргументы командной строки.
    """
    if args.verbose:
        config.verbose = True

    if args.model:
        config.llm.full.model_path = args.model

    if args.web:
        config.web.enabled = True
        config.web.port = args.port

    if args.notify:
        config.notify.enabled = True

    if args.preinstall:
        config.preinstall.enabled = True

    if args.cv:
        config.cv.enabled = True


def print_startup_info(printer: SafePrinter, commander) -> None:
    """
    Выводит информацию при старте (баннер, статус системы).

    Использует SafePrinter для безопасного вывода.
    В CI-режиме — баннер подавляется.

    Args:
        printer: SafePrinter для вывода.
        commander: Commander с инициализированными компонентами.
    """
    # Баннер (подавляется в CI)
    printer.banner(BANNER)

    lina_logger.info("Lina starting up")

    # Информация о системе
    try:
        mem = commander.monitor.get_memory_usage()
        printer.status("💻", f"RAM: {mem.get('available_mb', '?')} MB свободно "
                       f"из {mem.get('total_mb', '?')} MB")
    except Exception:
        printer.status("💻", "RAM: информация недоступна")
    printer.status("📁", f"База знаний: {KNOWLEDGE_DIR}")
    printer.status("�", f"LLM: {config.llm.full.model_path}")

    # Проверяем модель
    full_ok = os.path.exists(config.llm.full.model_path)

    if not full_ok:
        printer.status("⚠", "Модель не найдена!")
        printer.status("ℹ", "Lina будет работать без LLM (встроенные команды + RAG).")
        printer.status("ℹ", "Для скачивания модели: python download_model.py")

    # Phase 28: MainPipeline is the sole pipeline
    printer.status("🔒", "MainPipeline: ACTIVE (unified security pipeline)")
    printer.status("🆔", f"Session: {commander.session_id}")
    if commander.trace_enabled:
        printer.status("📊", "Trace mode: ON")


def print_optional_info(printer: SafePrinter, commander, args: LinaArgs) -> None:
    """
    Выводит информацию об опциональных модулях (CV, preinstall, web).

    Args:
        printer: SafePrinter для вывода.
        commander: Commander с инициализированными компонентами.
        args: Аргументы CLI.
    """
    # Индексация
    if args.index:
        knowledge_path = args.knowledge_dir or str(KNOWLEDGE_DIR)
        printer.status("📚", f"Индексация документов из {knowledge_path}...")
        result = commander.indexer.index_documents(knowledge_path)
        printer.status("ℹ", result["message"])

    # Предустановочный режим
    if config.preinstall.enabled:
        printer.status("🐧", "Режим предустановки Linux: АКТИВЕН")
        printer.status("ℹ", "Команды: обзор системы, анализ разделов, проверка готовности")
        printer.status("ℹ", "Макросы: system_overview, partition_assist, pre_install_check")
        if config.preinstall.auto_scan:
            printer.status("⏳", "Автосканирование оборудования...")
            overview = commander.hw_scanner.system_overview()
            printer.print(f"\n{overview}")

    # Computer Vision
    if config.cv.enabled:
        cv_caps = commander.screen_scanner.get_capabilities()
        printer.print("")
        printer.status("👁", "Computer Vision: АКТИВЕН")
        ocr_ok = commander.ocr_engine.available
        det_ok = commander.gui_detector.available
        printer.status("ℹ",
                       f"Скриншоты: {'доступны' if cv_caps['screenshot'] else 'mss не установлен'}")
        printer.status("ℹ",
                       f"OCR: {'доступен' if ocr_ok else 'pytesseract не установлен'}")
        printer.status("ℹ",
                       f"Детекция: {'доступна' if det_ok else 'opencv не установлен'}")
        printer.status("ℹ",
                       "Команды: скриншот экрана, распознай текст, найди ошибки на экране")


def start_web_server(printer: SafePrinter, commander):
    """
    Запускает веб-сервер если включён в конфигурации.

    Args:
        printer: SafePrinter для вывода.
        commander: Commander.

    Returns:
        web_server или None.
    """
    if not config.web.enabled:
        return None

    try:
        from lina.interface.web import LinaWebServer
        web_server = LinaWebServer(commander, port=config.web.port)
        web_server.start()
        printer.status("🌐", f"Веб-интерфейс: http://localhost:{config.web.port}")
        return web_server
    except Exception as e:
        logger.error("Web server start failed: %s", e, exc_info=True)
        printer.status("⚠", "Не удалось запустить веб-интерфейс")
        return None


def start_notifications(printer: SafePrinter, commander) -> None:
    """
    Включает desktop-уведомления если запрошены.

    Args:
        printer: SafePrinter для вывода.
        commander: Commander.
    """
    if not config.notify.enabled:
        return

    commander.notifier.info("Lina запущен", "Система готова к работе")
    printer.status("🔔", "Desktop-уведомления: включены")


def start_watchdog(commander) -> None:
    """
    Запускает watchdog мониторинга ресурсов.

    Args:
        commander: Commander.
    """
    def _on_overload(reasons):
        msg = "Перегрузка: " + ", ".join(reasons)
        lina_logger.warning(msg)
        if config.notify.enabled and config.notify.on_overload:
            commander.notifier.resource_warning(msg)

    commander.monitor.start_watchdog(interval=30, on_overload=_on_overload)


def run(argv=None) -> int:
    """
    Главная функция запуска Lina.

    Полный цикл:
      bootstrap → CLI → config → output → startup → REPL/oneshot

    Args:
        argv: Аргументы CLI (None = sys.argv).

    Returns:
        Exit code (0 = OK, 1 = ошибка).
    """
    try:
        # 1. Bootstrap — faulthandler, sys.path
        bootstrap()

        # 2. CLI — разбор аргументов
        args = parse_args(argv)

        # 3. Config — применяем аргументы
        apply_config(args)

        # 3.5. --gui: запуск Qt десктопного GUI (до Commander/REPL)
        if getattr(args, 'gui', False):
            try:
                from lina.gui.app import run_gui
                return run_gui()
            except ImportError as e:
                sys.stderr.write(f"❌ Qt не найден: {e}\nУстановите: pip install PyQt6\n")
                return 1

        # 3.6. Logging — console output только при --verbose
        if args.verbose:
            _setup_console_logging(logging.DEBUG)

        # 4. Output — определяем режим
        if args.quiet:
            printer = reset_printer(OutputMode.PIPE)
        else:
            printer = reset_printer()

        # 5. Commander — основной движок (Phase 15: с session, trace, confirm)
        from lina.shell.commander import Commander

        # Генерируем persistent session_id для этого запуска
        session_id = uuid.uuid4().hex[:16]

        # Sandbox confirmation callback для CLI
        def _cli_confirm(desc: str) -> bool:
            """Запрашивает подтверждение у пользователя для опасных операций."""
            try:
                answer = input(f"\n{desc}\n  Подтвердить? (y/n): ").strip().lower()
                return answer in ("y", "yes", "да", "д")
            except (EOFError, KeyboardInterrupt):
                return False

        commander = Commander(
            session_id=session_id,
            trace_enabled=args.trace,
            confirm_fn=_cli_confirm,
        )

        # 6. Signal handlers — корректное завершение (web_server captured via list)
        _ws_ref = [None]
        setup_signal_handlers(
            cleanup_fn=lambda: _cleanup(commander, _ws_ref[0])
        )

        # 7. Startup info — баннер, статус
        print_startup_info(printer, commander)
        print_optional_info(printer, commander, args)

        # 8. Веб-сервер, уведомления, watchdog
        web_server = start_web_server(printer, commander)
        _ws_ref[0] = web_server  # Now cleanup will stop it too
        start_notifications(printer, commander)
        start_watchdog(commander)

        # 9. Разделитель перед REPL
        printer.print("")
        printer.separator()

        # 10. Запуск — Phase 3: pass commander.process as pipeline_handler
        #     for CHAT_RESPONSE/NOT_FOUND fallback in governance routing
        session = REPLSession(
            commander, printer, web_server,
            pipeline_handler=commander.process,
        )

        if args.oneshot:
            # Одноразовый запрос — без REPL
            session.run_oneshot(args.oneshot)
        else:
            # Интерактивный REPL
            session.run()

        return 0

    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0

    except Exception as e:
        # Безопасный вывод ошибки в stderr
        sys.stderr.write("\n[Lina] Критическая ошибка. Подробности в логе.\n")
        sys.stderr.flush()
        logger.critical("Fatal error: %s", e, exc_info=True)
        return 1


def _setup_console_logging(level: int = logging.DEBUG) -> None:
    """Добавляет console handler к root logger (только для --verbose)."""
    root = logging.getLogger()
    # Проверяем, что StreamHandler ещё не добавлен
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            if not isinstance(h, logging.NullHandler):
                return  # Уже есть console handler
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    ))
    root.addHandler(handler)
    root.setLevel(level)


def _cleanup(commander, web_server=None) -> None:
    """Функция очистки при завершении."""
    try:
        if web_server is not None:
            web_server.stop()
    except Exception:
        pass
    try:
        if commander.llm.is_loaded:
            commander.llm.unload()
    except Exception:
        pass
    try:
        commander.monitor.stop_watchdog()
    except Exception:
        pass
