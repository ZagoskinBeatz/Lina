"""
Lina CV — Детектор GUI элементов (GUIDetector).

Функции:
- Определение окон и диалогов
- Поиск кнопок и элементов интерфейса
- Обнаружение прогресс-баров
- Распознавание диалогов ошибок
- Анализ общей структуры GUI

Зависимости (опциональные):
- opencv-python (cv2): компьютерное зрение
- Pillow (PIL): обработка изображений
- numpy: работа с массивами пикселей

Если зависимости недоступны — fallback-режим.
"""

import os
from typing import Optional, Dict, Any, List, Tuple

from lina.system.logger import logger

# ── Опциональные импорты ──

_HAS_CV2 = False
_HAS_PIL = False
_HAS_NUMPY = False

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    pass

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    pass

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    pass


# Шаблоны GUI элементов: цветовые диапазоны для обнаружения
# (HSV: hue, saturation, value)
GUI_COLOR_RANGES = {
    "error_red": {
        "lower": (0, 100, 100),
        "upper": (10, 255, 255),
        "description": "Красные элементы (ошибки, закрытие)",
    },
    "success_green": {
        "lower": (40, 100, 100),
        "upper": (80, 255, 255),
        "description": "Зелёные элементы (успех, подтверждение)",
    },
    "warning_yellow": {
        "lower": (20, 100, 100),
        "upper": (35, 255, 255),
        "description": "Жёлтые элементы (предупреждения)",
    },
    "button_blue": {
        "lower": (100, 100, 100),
        "upper": (130, 255, 255),
        "description": "Синие элементы (кнопки, ссылки)",
    },
}


class GUIDetector:
    """
    Детектор GUI-элементов на скриншотах.

    Поддерживает:
    - Обнаружение прямоугольных областей (окна, кнопки)
    - Анализ цветовых зон (красный=ошибка, зелёный=успех)
    - Поиск прогресс-баров
    - Определение диалогов ошибок
    - Общая структура экрана
    """

    def __init__(self):
        self._cache: Dict[str, Any] = {}

    @property
    def available(self) -> bool:
        """Доступен ли детектор."""
        return _HAS_CV2 or _HAS_PIL

    def get_capabilities(self) -> Dict[str, bool]:
        """Возвращает доступные возможности."""
        return {
            "contour_detection": _HAS_CV2,
            "color_analysis": _HAS_CV2 and _HAS_NUMPY,
            "image_load": _HAS_PIL or _HAS_CV2,
            "numpy": _HAS_NUMPY,
        }

    def detect_elements(self, image_path: str) -> Dict[str, Any]:
        """
        Обнаруживает GUI элементы на скриншоте.

        Args:
            image_path: Путь к изображению.

        Returns:
            dict с windows, buttons, dialogs, progress_bars.
        """
        if not os.path.exists(image_path):
            return {"success": False, "error": f"Файл не найден: {image_path}"}

        result: Dict[str, Any] = {
            "success": True,
            "path": image_path,
            "windows": [],
            "buttons": [],
            "dialogs": [],
            "progress_bars": [],
            "color_zones": [],
        }

        if _HAS_CV2 and _HAS_NUMPY:
            try:
                img = cv2.imread(image_path)
                if img is None:
                    return {"success": False, "error": "Не удалось загрузить изображение"}

                h, w = img.shape[:2]
                result["image_size"] = {"width": w, "height": h}

                # Анализ контуров — поиск прямоугольных областей
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                edges = cv2.Canny(gray, 50, 150)
                contours, _ = cv2.findContours(
                    edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )

                # Фильтруем контуры по размеру
                min_area = w * h * 0.001  # минимум 0.1% экрана
                max_area = w * h * 0.8    # максимум 80% экрана

                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area < min_area or area > max_area:
                        continue

                    x, y, cw, ch = cv2.boundingRect(contour)
                    aspect = cw / ch if ch > 0 else 0

                    element = {
                        "x": x, "y": y,
                        "width": cw, "height": ch,
                        "area": int(area),
                    }

                    # Классификация по пропорциям и размеру
                    if cw > w * 0.3 and ch > h * 0.3:
                        element["type"] = "window"
                        result["windows"].append(element)
                    elif 2.0 < aspect < 8.0 and ch < 60:
                        element["type"] = "button"
                        result["buttons"].append(element)
                    elif aspect > 5.0 and ch < 40:
                        element["type"] = "progress_bar"
                        result["progress_bars"].append(element)

                # Цветовой анализ
                hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
                for zone_name, zone_range in GUI_COLOR_RANGES.items():
                    lower = np.array(zone_range["lower"])
                    upper = np.array(zone_range["upper"])
                    mask = cv2.inRange(hsv, lower, upper)
                    pixel_count = cv2.countNonZero(mask)
                    percentage = round(pixel_count / (w * h) * 100, 2)

                    if percentage > 0.1:  # Значимое присутствие цвета
                        result["color_zones"].append({
                            "zone": zone_name,
                            "description": zone_range["description"],
                            "percentage": percentage,
                            "pixel_count": pixel_count,
                        })

                # Сортировка по значимости
                result["color_zones"].sort(key=lambda z: z["percentage"], reverse=True)

                logger.info(
                    f"CV detector: {len(result['windows'])} windows, "
                    f"{len(result['buttons'])} buttons, "
                    f"{len(result['color_zones'])} color zones"
                )

            except Exception as e:
                logger.error(f"CV detector error: {e}")
                result["success"] = False
                result["error"] = "Ошибка обработки изображения."

        elif _HAS_PIL:
            # Fallback: базовый анализ через Pillow
            try:
                img = Image.open(image_path)
                result["image_size"] = {"width": img.width, "height": img.height}
                result["note"] = "OpenCV не установлен. Расширенная детекция недоступна."

                # Базовый цветовой анализ
                if img.mode in ("RGB", "RGBA"):
                    pixels = list(img.getdata())
                    if pixels:
                        r_avg = sum(p[0] for p in pixels) / len(pixels)
                        g_avg = sum(p[1] for p in pixels) / len(pixels)
                        b_avg = sum(p[2] for p in pixels) / len(pixels)
                        result["avg_color"] = {
                            "r": round(r_avg),
                            "g": round(g_avg),
                            "b": round(b_avg),
                        }
                img.close()
            except Exception as e:
                result["success"] = False
                result["error"] = "Ошибка обработки изображения."
        else:
            result["success"] = False
            result["error"] = "OpenCV и Pillow не установлены."

        return result

    def detect_error_dialogs(self, image_path: str) -> Dict[str, Any]:
        """
        Специализированный поиск диалогов ошибок.

        Ищет: красные зоны, предупреждающие цвета, характерные
        пропорции модальных окон.

        Returns:
            dict с is_error, confidence, details.
        """
        if not os.path.exists(image_path):
            return {
                "success": False,
                "is_error": False,
                "confidence": 0,
                "error": f"Файл не найден: {image_path}",
            }

        result = {
            "success": True,
            "is_error": False,
            "confidence": 0.0,
            "reasons": [],
        }

        elements = self.detect_elements(image_path)
        if not elements.get("success"):
            result["success"] = False
            result["error"] = elements.get("error", "Ошибка детекции")
            return result

        score = 0.0

        # Проверяем наличие красных зон
        for zone in elements.get("color_zones", []):
            if zone["zone"] == "error_red" and zone["percentage"] > 0.5:
                score += 0.3
                result["reasons"].append(
                    f"Обнаружены красные элементы ({zone['percentage']}%)"
                )

        # Проверяем наличие маленьких модальных окон
        for win in elements.get("windows", []):
            img_size = elements.get("image_size", {})
            if img_size:
                w_ratio = win["width"] / img_size.get("width", 1)
                h_ratio = win["height"] / img_size.get("height", 1)
                if 0.2 < w_ratio < 0.6 and 0.1 < h_ratio < 0.5:
                    score += 0.2
                    result["reasons"].append("Обнаружено модальное окно")
                    break

        # Проверяем наличие кнопок (OK, Cancel)
        if elements.get("buttons"):
            btn_count = len(elements["buttons"])
            if 1 <= btn_count <= 3:
                score += 0.15
                result["reasons"].append(f"Обнаружено {btn_count} кнопок")

        result["confidence"] = round(min(score, 1.0), 2)
        result["is_error"] = score >= 0.4

        return result

    def analyze_screen_structure(self, image_path: str) -> Dict[str, Any]:
        """
        Анализирует общую структуру экрана.

        Returns:
            dict с зонами экрана и их типами.
        """
        if not os.path.exists(image_path):
            return {"success": False, "error": f"Файл не найден: {image_path}"}

        elements = self.detect_elements(image_path)
        if not elements.get("success"):
            return elements

        img_size = elements.get("image_size", {})
        w = img_size.get("width", 1920)
        h = img_size.get("height", 1080)

        # Определяем зоны экрана
        zones = {
            "taskbar": {"detected": False, "position": "unknown"},
            "sidebar": {"detected": False, "position": "unknown"},
            "main_content": {"area_percent": 100},
            "dialogs": {"count": 0},
        }

        # Ищем панель задач (узкая полоса внизу или вверху)
        for win in elements.get("windows", []):
            if win["height"] < h * 0.08 and win["width"] > w * 0.5:
                if win["y"] > h * 0.85:
                    zones["taskbar"] = {"detected": True, "position": "bottom"}
                elif win["y"] < h * 0.05:
                    zones["taskbar"] = {"detected": True, "position": "top"}

        # Ищем боковую панель
        for win in elements.get("windows", []):
            if win["width"] < w * 0.15 and win["height"] > h * 0.5:
                if win["x"] < w * 0.05:
                    zones["sidebar"] = {"detected": True, "position": "left"}
                elif win["x"] > w * 0.85:
                    zones["sidebar"] = {"detected": True, "position": "right"}

        zones["dialogs"]["count"] = len(elements.get("windows", []))

        return {
            "success": True,
            "image_size": img_size,
            "zones": zones,
            "elements_summary": {
                "windows": len(elements.get("windows", [])),
                "buttons": len(elements.get("buttons", [])),
                "progress_bars": len(elements.get("progress_bars", [])),
                "color_zones": len(elements.get("color_zones", [])),
            },
        }

    def format_detection_report(self, image_path: str) -> str:
        """
        Полный отчёт по обнаруженным GUI элементам.

        Returns:
            Форматированный отчёт.
        """
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║       🎯 Анализ GUI элементов                    ║")
        lines.append("╠══════════════════════════════════════════════════╣")

        elements = self.detect_elements(image_path)
        if not elements.get("success"):
            lines.append(f"║  ❌ {elements.get('error', 'Ошибка')}")
            lines.append("╚══════════════════════════════════════════════════╝")
            return "\n".join(lines)

        img_size = elements.get("image_size", {})
        lines.append(f"║  🖥 Размер: {img_size.get('width', '?')}x{img_size.get('height', '?')}")

        # Окна
        windows = elements.get("windows", [])
        lines.append(f"║  🪟 Окон: {len(windows)}")
        for w in windows[:5]:
            lines.append(f"║    ▪ {w['width']}x{w['height']} at ({w['x']},{w['y']})")

        # Кнопки
        buttons = elements.get("buttons", [])
        lines.append(f"║  🔘 Кнопок: {len(buttons)}")

        # Прогресс-бары
        progress = elements.get("progress_bars", [])
        lines.append(f"║  📊 Прогресс-баров: {len(progress)}")

        # Цветовые зоны
        zones = elements.get("color_zones", [])
        if zones:
            lines.append("║  🎨 Цветовые зоны:")
            for z in zones[:4]:
                lines.append(f"║    ▪ {z['description']}: {z['percentage']}%")

        # Ошибки
        err = self.detect_error_dialogs(image_path)
        if err.get("is_error"):
            lines.append(f"║  ⚠ Возможен диалог ошибки (уверенность: {err['confidence']:.0%})")
            for r in err.get("reasons", []):
                lines.append(f"║    ▪ {r}")

        if elements.get("note"):
            lines.append(f"║  ℹ {elements['note']}")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    def clear_cache(self) -> None:
        """Очищает кэш."""
        self._cache = {}
