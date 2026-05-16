#!/usr/bin/env python3
"""
Lina — ИИ-ассистент v0.5.0.

Главный файл запуска. Делегирует всю логику в core.runtime.

Поддерживает два режима:
  1. Безопасный (по умолчанию): через core.runtime.run()
     — faulthandler, output isolation, fish-совместимость
  2. Legacy fallback: если core.runtime недоступен — использует
     встроенную main() для обратной совместимости.

Запуск:
    python lina.py
    python lina.py --verbose
    python lina.py --index       # Индексация при старте
    python lina.py --web         # Запустить веб-интерфейс
    python lina.py --notify      # Включить уведомления
    python lina.py --preinstall  # Режим предустановки Linux
    python lina.py --cv          # Включить Computer Vision
    python lina.py --gui         # Запустить десктопный Qt GUI
    python lina.py --oneshot 'запрос'  # Одноразовый запрос
    python lina.py --quiet       # Без баннера и emoji (для pipe/fish)
"""

import sys
import os
import argparse
import signal

# ── Автодетекция venv ──
# Если запущены системным Python, но рядом есть .venv — перезапускаем.
def _maybe_reexec_in_venv():
    """Перезапускает процесс через venv Python, если доступен."""
    # Уже в venv?
    if sys.prefix != sys.base_prefix:
        return
    # Ищем .venv рядом с lina/
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_python = os.path.join(project_root, ".venv", "bin", "python")
    if os.path.isfile(venv_python) and os.access(venv_python, os.X_OK):
        # Перезапускаем через venv Python
        os.execv(venv_python, [venv_python] + sys.argv)

_maybe_reexec_in_venv()

# Добавляем родительскую директорию в path для корректного импорта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lina.config import config, KNOWLEDGE_DIR
from lina.shell.commander import Commander
from lina.system.logger import logger


# ── ASCII-баннер ──

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

PROMPT = "\n🟢 Lina> "


def parse_args():
    """Парсит аргументы командной строки."""
    parser = argparse.ArgumentParser(
        description="Lina — ИИ-ассистент v0.4.0"
    )
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
        "--full-model",
        type=str,
        default=None,
        help="Путь к полной GGUF модели (7-13B+)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Путь к GGUF модели (используется как мини)",
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
        "--llm-debug",
        action="store_true",
        help="Диагностика LLM: печать prompt, токены, бюджет перед генерацией",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Запустить десктопный Qt GUI (требуется PyQt6 или PySide6)",
    )
    return parser.parse_args()


def setup_signal_handlers():
    """Настраивает обработчики сигналов для корректного завершения."""
    def handle_interrupt(signum, frame):
        print("\n\n👋 Lina завершает работу...")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_interrupt)
    signal.signal(signal.SIGTERM, handle_interrupt)


def main():
    """Главная функция запуска Lina CLI."""
    args = parse_args()

    # Применяем аргументы к конфигурации
    if args.verbose:
        config.verbose = True

    if args.model:
        config.llm.full.model_path = args.model

    if args.full_model:
        config.llm.full.model_path = args.full_model

    if args.web:
        config.web.enabled = True
        config.web.port = args.port

    if args.notify:
        config.notify.enabled = True

    if args.preinstall:
        config.preinstall.enabled = True

    if args.cv:
        config.cv.enabled = True

    # Phase 21: --llm-debug
    llm_debug = getattr(args, 'llm_debug', False)
    if llm_debug:
        os.environ["LLM_DEBUG"] = "1"

    # --gui: запуск Qt десктопного GUI
    if getattr(args, 'gui', False):
        try:
            from lina.gui.app import run_gui
            sys.exit(run_gui())
        except ImportError as e:
            print(f"❌ Qt не найден: {e}")
            print("Установите: pip install PyQt6")
            sys.exit(1)

    # Устанавливаем обработчики сигналов
    setup_signal_handlers()

    # Выводим баннер
    print(BANNER)

    logger.info("Lina starting up")

    # Информация о системе
    commander = Commander()

    # Краткая информация при старте
    mem = commander.monitor.get_memory_usage()
    print(f"  💻 RAM: {mem.get('available_mb', '?')} MB свободно из {mem.get('total_mb', '?')} MB")
    print(f"  📁 База знаний: {KNOWLEDGE_DIR}")
    print(f"  � LLM: {config.llm.full.model_path}")

    # Проверяем наличие модели
    full_ok = os.path.exists(config.llm.full.model_path)

    if not full_ok:
        print(
            f"\n  ⚠ Модель не найдена: {config.llm.full.model_path}"
            f"\n  ℹ Lina будет работать без LLM (встроенные команды + RAG)."
            f"\n  ℹ Для скачивания модели: python download_model.py"
        )

    # Индексация при старте (если запрошено)
    if args.index:
        knowledge_path = args.knowledge_dir or str(KNOWLEDGE_DIR)
        print(f"\n📚 Индексация документов из {knowledge_path}...")
        result = commander.indexer.index_documents(knowledge_path)
        print(f"   {result['message']}")

    # Предустановочный режим
    if config.preinstall.enabled:
        print(f"\n  🐧 Режим предустановки Linux: АКТИВЕН")
        print(f"  ℹ Команды: обзор системы, анализ разделов, проверка готовности")
        print(f"  ℹ Макросы: system_overview, partition_assist, pre_install_check")
        if config.preinstall.auto_scan:
            print(f"  ⏳ Автосканирование оборудования...")
            overview = commander.hw_scanner.system_overview()
            print(f"\n{overview}")

    # Computer Vision
    if config.cv.enabled:
        cv_caps = commander.screen_scanner.get_capabilities()
        print(f"\n  👁 Computer Vision: АКТИВЕН")
        print(f"  ℹ Скриншоты: {'✅' if cv_caps['screenshot'] else '❌ mss не установлен'}")
        print(f"  ℹ OCR: {'✅' if commander.ocr_engine.available else '❌ pytesseract не установлен'}")
        print(f"  ℹ Детекция: {'✅' if commander.gui_detector.available else '❌ opencv не установлен'}")
        print(f"  ℹ Команды: скриншот экрана, распознай текст, найди ошибки на экране")

    # Веб-интерфейс
    web_server = None
    if config.web.enabled:
        try:
            from lina.interface.web import LinaWebServer
            web_server = LinaWebServer(commander, port=config.web.port)
            web_server.start()
            print(f"\n  🌐 Веб-интерфейс: http://localhost:{config.web.port}")
        except Exception as e:
            print(f"\n  ⚠ Не удалось запустить веб-интерфейс: {e}")

    # Уведомления
    if config.notify.enabled:
        commander.notifier.info("Lina запущен", "Система готова к работе")
        print(f"  🔔 Desktop-уведомления: включены")

    # Watchdog мониторинга
    def _on_overload(reasons):
        msg = "Перегрузка: " + ", ".join(reasons)
        logger.warning(msg)
        if config.notify.enabled and config.notify.on_overload:
            commander.notifier.resource_warning(msg)

    commander.monitor.start_watchdog(interval=30, on_overload=_on_overload)

    # ── Phase 3: Governance routing helper ──
    def _legacy_route_governance(text: str) -> str:
        """Route through IntentBridge. Fallback to commander only if governance unavailable."""
        try:
            from lina.intent.bridge import get_intent_bridge
            from lina.intent.types import IntentStatus

            bridge = get_intent_bridge()
            result = bridge.from_text(
                text, source="cli", pipeline_handler=commander.process)

            if result.status == IntentStatus.NEEDS_CONFIRM:
                esc_id = result.escalation_id
                print(f"\n⚠ Требуется подтверждение:\n{result.response_text}")
                try:
                    answer = input(
                        f"  /confirm {esc_id} или /deny {esc_id}: ").strip().lower()
                    if answer in ("y", "yes", "да", "д",
                                  f"/confirm {esc_id}", "/confirm"):
                        return "✅ Подтверждено."
                    return "🚫 Отклонено пользователем."
                except (EOFError, KeyboardInterrupt):
                    return "🚫 Отклонено."

            if result.status == IntentStatus.DENIED:
                return f"🚫 Отказано: {result.response_text}"
            if result.status == IntentStatus.FAILED:
                return f"❌ {result.response_text}"
            if result.response_text:
                return result.response_text
            # Chat/query fallback
            if result.status in (IntentStatus.CHAT_RESPONSE,
                                 IntentStatus.NOT_FOUND):
                return commander.process(text) or ""
            return result.response_text or ""
        except ImportError:
            logger.warning("IntentBridge unavailable in legacy main, fallback")
            return commander.process(text) or ""

    # ── Главный цикл ──
    print("\n" + "─" * 55)

    _EXIT_CMDS = {"выход", "exit", "quit", "q", "/выход", "/exit"}

    while True:
        try:
            user_input = input(PROMPT).strip()

            if not user_input:
                # Проверяем автовыгрузку по таймауту при простое
                commander.llm.check_idle_unload()
                continue

            # Exit commands — check before governance
            if user_input.lower() in _EXIT_CMDS:
                print("\n👋 До встречи! Lina завершает работу.")
                logger.info("Lina shutting down")
                if commander.llm.is_loaded:
                    commander.llm.unload()
                commander.monitor.stop_watchdog()
                if web_server:
                    web_server.stop()
                break

            # Сбрасываем таймер auto-unload при ЛЮБОЙ активности пользователя
            if commander.llm.is_loaded:
                commander.llm._active.touch()

            # ── Phase 3: Route through governance ──
            response = _legacy_route_governance(user_input)

            # Проверяем автовыгрузку после каждого запроса
            commander.llm.check_idle_unload()

            # Legacy __EXIT__ compat
            if response == "__EXIT__":
                print("\n👋 До встречи! Lina завершает работу.")
                logger.info("Lina shutting down")
                if commander.llm.is_loaded:
                    commander.llm.unload()
                commander.monitor.stop_watchdog()
                if web_server:
                    web_server.stop()
                break

            # Выводим ответ
            if response:
                print(f"\n{response}")

        except EOFError:
            print("\n\n👋 До встречи!")
            break
        except KeyboardInterrupt:
            print("\n\n👋 Прервано. До встречи!")
            break
        except Exception as e:
            print(f"\n❌ Непредвиденная ошибка: {e}")
            logger.error(f"Unhandled exception: {e}")
            if config.verbose:
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    # Безопасный запуск через core.runtime (Phase 11)
    # Если core.runtime недоступен — fallback на legacy main()
    try:
        from lina.core.runtime import run
        sys.exit(run())
    except ImportError:
        # Legacy fallback — core.runtime не установлен
        main()
