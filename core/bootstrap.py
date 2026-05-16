"""
Lina — Bootstrap (безопасный запуск).

Обеспечивает:
  - faulthandler для отладки segfault
  - Обработка сигналов (SIGINT, SIGTERM)
  - Изоляция ошибок при импорте
  - Корректное завершение при любых сбоях

Используется как единая точка инициализации перед запуском CLI/REPL.
"""

import sys
import os
import signal
import logging
import faulthandler
from typing import Optional, Callable

logger = logging.getLogger("lina.core.bootstrap")


def enable_faulthandler() -> None:
    """
    Включает faulthandler для вывода traceback при segfault.

    Полезно при работе с llama-cpp-python (нативные бинарники).
    Вывод идёт в stderr, не засоряя stdout.
    """
    try:
        faulthandler.enable(file=sys.stderr)
        logger.debug("faulthandler enabled (stderr)")
    except Exception as e:
        logger.debug("faulthandler unavailable: %s", e)


def setup_signal_handlers(cleanup_fn: Optional[Callable] = None) -> None:
    """
    Настраивает обработчики сигналов для корректного завершения.

    Args:
        cleanup_fn: Функция очистки, вызываемая перед выходом.
                    Принимает 0 аргументов. Может быть None.
    """
    def _handler(signum: int, frame) -> None:
        """Обработчик SIGINT/SIGTERM."""
        sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
        logger.info("Received signal %s, shutting down", sig_name)

        # Безопасный вывод через stderr (не stdout!) при завершении
        try:
            sys.stderr.write("\n[Lina] Завершение работы...\n")
            sys.stderr.flush()
        except Exception:
            pass

        if cleanup_fn:
            try:
                cleanup_fn()
            except Exception as e:
                logger.error("Cleanup error: %s", e)

        sys.exit(0)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def safe_import(module_path: str, fallback=None):
    """
    Безопасный импорт модуля с обработкой ошибок.

    Если модуль не удаётся импортировать — возвращает fallback
    вместо выброса исключения.

    Args:
        module_path: Полный путь модуля (e.g. "lina.cv.scanner").
        fallback: Значение по умолчанию при ошибке импорта.

    Returns:
        Импортированный модуль или fallback.
    """
    try:
        return __import__(module_path, fromlist=[""])
    except ImportError as e:
        logger.warning("Optional module %s not available: %s", module_path, e)
        return fallback
    except Exception as e:
        logger.error("Failed to import %s: %s", module_path, e)
        return fallback


def bootstrap() -> None:
    """
    Полная инициализация среды Lina.

    Вызывает все подготовительные шаги:
      1. faulthandler
      2. sys.path (добавляет родительскую директорию)
      3. Логирование (базовая настройка)

    Должен быть вызван ДО любого другого import lina.*.
    """
    # 1. faulthandler — ловим segfault от llama-cpp
    enable_faulthandler()

    # 2. sys.path — гарантируем импорт lina.*
    # __file__ = lina/core/bootstrap.py → parent = lina/core/ → parent = lina/ → parent = проект
    # Нужно добавить именно PROJECT (не lina/), чтобы import lina.config работал
    lina_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(lina_dir)

    # Проверяем, что project_root — правильный (содержит lina/ как пакет)
    lina_init = os.path.join(project_root, "lina", "__init__.py")
    if os.path.isfile(lina_init) and project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 3. Логирование — НЕ добавляем console handler по умолчанию.
    # LinaLogger (system/logger.py) уже настраивает file handlers.
    # Console handler добавляется только при --verbose (в runtime.py).
    # Здесь только предотвращаем WARNING от basicConfig:
    root = logging.getLogger()
    if not root.handlers:
        # Пустой handler чтобы basicConfig не добавил свой StreamHandler
        root.addHandler(logging.NullHandler())

    logger.debug("Bootstrap complete (pid=%d)", os.getpid())
