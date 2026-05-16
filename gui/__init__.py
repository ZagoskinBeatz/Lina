"""
Lina GUI — графический интерфейс (PyQt6 / PySide6).

Модули:
  - tray.py      — значок в системном трее
  - chat.py      — popup-окно чата
  - settings.py  — окно настроек
  - desktop.py   — генерация .desktop файлов
  - theme.py     — темы и стили
"""

from typing import Optional

_QT_BACKEND: Optional[str] = None
_QT_AVAILABLE: bool = False


def _detect_qt_backend() -> tuple:
    """Определяет доступный Qt-бэкенд: PyQt6 → PySide6 → None."""
    try:
        import PyQt6.QtWidgets  # noqa: F401
        return "PyQt6", True
    except ImportError:
        pass
    try:
        import PySide6.QtWidgets  # noqa: F401
        return "PySide6", True
    except ImportError:
        pass
    return None, False


_QT_BACKEND, _QT_AVAILABLE = _detect_qt_backend()


def is_gui_available() -> bool:
    """Доступен ли Qt для GUI."""
    return _QT_AVAILABLE


def get_qt_backend() -> Optional[str]:
    """Возвращает имя Qt-бэкенда: 'PyQt6', 'PySide6' или None."""
    return _QT_BACKEND


def get_qt_modules():
    """Возвращает кортеж (QtWidgets, QtCore, QtGui) для текущего бэкенда.

    Raises ImportError если Qt недоступен.
    """
    if _QT_BACKEND == "PyQt6":
        from PyQt6 import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui
    elif _QT_BACKEND == "PySide6":
        from PySide6 import QtWidgets, QtCore, QtGui
        return QtWidgets, QtCore, QtGui
    else:
        raise ImportError("Ни PyQt6, ни PySide6 не установлены. "
                          "Установите: pip install PyQt6")
