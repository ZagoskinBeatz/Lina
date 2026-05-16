"""
Lina — CV Runtime Manager (Headless Strategy).

Управляет стратегией работы Computer Vision в различных окружениях:
  - Desktop (X11/Wayland) → полный функционал
  - Headless (нет дисплея) → mock-режим с структурированными ответами
  - CI (--cv-mock) → принудительный mock

Архитектура:
  ┌──────────────────────────────────────────────────┐
  │ CVRuntime                                        │
  │                                                  │
  │  [detect_display] → X11 / Wayland / headless     │
  │  [get_mode] → full / mock / disabled             │
  │  [mock_screenshot] → структурированный ответ     │
  │  [mock_ocr] → fallback текст                     │
  │  [mock_detector] → пустые результаты             │
  └──────────────────────────────────────────────────┘

Не вызывает warning как ошибку. В mock-mode возвращает
корректные структурированные данные.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any, List

logger = logging.getLogger("lina.cv_runtime")


# ─── Режимы CV ─────────────────────────────────────────────────────────────────

class CVMode(Enum):
    """Режим работы CV-модуля."""
    FULL = "full"          # Полный функционал (X11/Wayland)
    MOCK = "mock"          # Mock-режим (headless/CI)
    DISABLED = "disabled"  # CV полностью отключён


class DisplayType(Enum):
    """Тип дисплейного сервера."""
    X11 = "x11"
    WAYLAND = "wayland"
    HEADLESS = "headless"
    UNKNOWN = "unknown"


# ─── Результат детекции дисплея ────────────────────────────────────────────────

@dataclass
class DisplayInfo:
    """Информация о дисплейном окружении."""
    display_type: DisplayType
    display_var: str = ""           # $DISPLAY
    wayland_var: str = ""           # $WAYLAND_DISPLAY
    xdg_session: str = ""           # $XDG_SESSION_TYPE
    has_display: bool = False
    details: str = ""


# ─── Основной класс ───────────────────────────────────────────────────────────

class CVRuntime:
    """
    Менеджер рантайма Computer Vision.

    Определяет окружение и выбирает стратегию:
      - X11/Wayland → полный CV
      - Headless → mock-ответы
      - --cv-mock → принудительный mock

    Usage:
        cv = CVRuntime()
        mode = cv.get_mode()

        if mode == CVMode.MOCK:
            result = cv.mock_screenshot()  # структурированный ответ
        elif mode == CVMode.FULL:
            result = scanner.take_screenshot()  # реальный экран
    """

    def __init__(self, force_mock: bool = False):
        """
        Args:
            force_mock: Принудительный mock-режим (для CI / --cv-mock).
        """
        self._force_mock = force_mock
        self._display_info: Optional[DisplayInfo] = None
        self._mode: Optional[CVMode] = None

    # ── Детекция дисплея ──

    def detect_display(self) -> DisplayInfo:
        """
        Определяет тип дисплейного сервера.

        Проверяет в порядке приоритета:
          1. $WAYLAND_DISPLAY → Wayland
          2. $DISPLAY → X11
          3. $XDG_SESSION_TYPE → wayland/x11/tty
          4. Fallback → headless

        Returns:
            DisplayInfo с результатом детекции.
        """
        if self._display_info is not None:
            return self._display_info

        display = os.environ.get("DISPLAY", "")
        wayland = os.environ.get("WAYLAND_DISPLAY", "")
        xdg = os.environ.get("XDG_SESSION_TYPE", "")

        info = DisplayInfo(
            display_type=DisplayType.HEADLESS,
            display_var=display,
            wayland_var=wayland,
            xdg_session=xdg,
        )

        # Wayland
        if wayland:
            info.display_type = DisplayType.WAYLAND
            info.has_display = True
            info.details = f"Wayland ({wayland})"
        # X11
        elif display:
            info.display_type = DisplayType.X11
            info.has_display = True
            info.details = f"X11 ({display})"
        # XDG fallback
        elif xdg in ("wayland", "x11"):
            info.display_type = (
                DisplayType.WAYLAND if xdg == "wayland"
                else DisplayType.X11
            )
            info.has_display = True
            info.details = f"XDG: {xdg}"
        else:
            info.display_type = DisplayType.HEADLESS
            info.has_display = False
            info.details = "Нет дисплея (headless / tty)"

        self._display_info = info
        return info

    # ── Режим работы ──

    def get_mode(self, cv_enabled: bool = True) -> CVMode:
        """
        Определяет режим работы CV.

        Args:
            cv_enabled: Включён ли CV в конфиге.

        Returns:
            CVMode: FULL, MOCK или DISABLED.
        """
        if self._mode is not None:
            return self._mode

        # CV отключён в конфиге
        if not cv_enabled:
            self._mode = CVMode.DISABLED
            return self._mode

        # Принудительный mock (CI / --cv-mock)
        if self._force_mock:
            self._mode = CVMode.MOCK
            logger.info("CV: mock-режим (принудительно)")
            return self._mode

        # Детектируем дисплей
        info = self.detect_display()
        if info.has_display:
            self._mode = CVMode.FULL
            logger.info(f"CV: полный режим ({info.details})")
        else:
            self._mode = CVMode.MOCK
            logger.info(f"CV: mock-режим ({info.details})")

        return self._mode

    def reset(self) -> None:
        """Сбрасывает кэш детекции (для тестов)."""
        self._display_info = None
        self._mode = None

    # ── Mock-ответы ──

    def mock_screenshot(self) -> Dict[str, Any]:
        """
        Mock-ответ для скриншота в headless-окружении.

        Returns:
            Структурированный ответ, совместимый с ScreenScanner.
        """
        return {
            "ok": False,
            "mock": True,
            "reason": "headless",
            "message": (
                "CV работает в mock-режиме (нет дисплея). "
                "Для полного функционала запустите в графическом окружении."
            ),
            "monitors": [],
            "screenshot_path": None,
        }

    def mock_ocr(self, image_path: str = "") -> Dict[str, Any]:
        """
        Mock-ответ для OCR в headless-окружении.

        Args:
            image_path: Путь к изображению (игнорируется в mock).

        Returns:
            Структурированный ответ, совместимый с OCREngine.
        """
        return {
            "ok": False,
            "mock": True,
            "reason": "headless",
            "text": "",
            "errors": [],
            "warnings": [],
            "progress": [],
            "message": "OCR недоступен: нет дисплея (headless-режим).",
        }

    def mock_detector(self) -> Dict[str, Any]:
        """
        Mock-ответ для детектора GUI в headless-окружении.

        Returns:
            Структурированный ответ, совместимый с GUIDetector.
        """
        return {
            "ok": False,
            "mock": True,
            "reason": "headless",
            "elements": [],
            "dialogs": [],
            "structure": {},
            "message": "GUI-детекция недоступна: нет дисплея (headless-режим).",
        }

    def mock_list_screenshots(self) -> Dict[str, Any]:
        """
        Mock-ответ для списка скриншотов.

        Returns:
            Структурированный ответ.
        """
        return {
            "ok": True,
            "mock": True,
            "reason": "headless",
            "screenshots": [],
            "total": 0,
            "message": "Скриншоты недоступны: нет дисплея (headless-режим).",
        }

    # ── Статус ──

    def get_status(self) -> Dict[str, Any]:
        """
        Полный статус CV рантайма.

        Returns:
            Dict с информацией о режиме, дисплее, возможностях.
        """
        info = self.detect_display()
        mode = self.get_mode()

        return {
            "mode": mode.value,
            "display_type": info.display_type.value,
            "has_display": info.has_display,
            "display_details": info.details,
            "force_mock": self._force_mock,
            "capabilities": {
                "screenshot": mode == CVMode.FULL,
                "ocr": mode == CVMode.FULL,
                "gui_detection": mode == CVMode.FULL,
                "mock_available": mode == CVMode.MOCK,
            },
        }

    def format_status(self) -> str:
        """Форматированный статус для вывода."""
        s = self.get_status()
        mode_icon = {
            "full": "🟢",
            "mock": "🟡",
            "disabled": "⬜",
        }.get(s["mode"], "❓")

        lines = [
            f"  {mode_icon} CV режим: {s['mode']}",
            f"  📺 Дисплей: {s['display_details']}",
        ]

        if s["mode"] == "mock":
            lines.append("  ℹ Mock-режим: структурированные ответы без реального экрана")

        return "\n".join(lines)
