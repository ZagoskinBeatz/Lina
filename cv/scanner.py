"""
Lina CV — Захват и анализ экрана (ScreenScanner).

Функции:
- Захват скриншотов экрана (полный экран или область)
- Базовый анализ изображения (размер, яркость, контраст)
- Сохранение скриншотов для дальнейшего анализа
- Определение активных окон

Зависимости (опциональные):
- mss: захват экрана
- Pillow (PIL): обработка изображений

Если зависимости недоступны — работает в fallback-режиме,
возвращая информативные сообщения.
"""

import os
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from lina.config import config, BASE_DIR
from lina.system.logger import logger

# ── Опциональные импорты ──

_HAS_MSS = False
_HAS_PIL = False

try:
    import mss
    _HAS_MSS = True
except ImportError:
    pass

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    pass


SCREENSHOTS_DIR = BASE_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


class ScreenScanner:
    """
    Захват и анализ экрана.

    Поддерживает:
    - Полный экран и регион
    - Сохранение PNG
    - Базовый анализ (размер, средняя яркость)
    - Информация о мониторах
    """

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._screenshot_count = 0

    @property
    def available(self) -> bool:
        """Доступен ли захват экрана."""
        return _HAS_MSS or _HAS_PIL

    def get_capabilities(self) -> Dict[str, bool]:
        """Возвращает доступные возможности."""
        return {
            "screenshot": _HAS_MSS,
            "image_processing": _HAS_PIL,
            "monitor_info": _HAS_MSS,
        }

    def get_monitors(self) -> list:
        """Возвращает список мониторов."""
        if "monitors" in self._cache:
            return self._cache["monitors"]

        monitors = []
        if _HAS_MSS:
            try:
                with mss.mss() as sct:
                    for i, m in enumerate(sct.monitors[1:], 1):  # skip combined
                        monitors.append({
                            "id": i,
                            "left": m["left"],
                            "top": m["top"],
                            "width": m["width"],
                            "height": m["height"],
                        })
            except Exception as e:
                logger.warning(f"CV: ошибка получения мониторов: {e}")
        else:
            # Fallback: попробуем xrandr
            try:
                import subprocess
                result = subprocess.run(
                    ["xrandr", "--query"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    import re
                    for m in re.finditer(
                        r'(\S+)\s+connected\s+(?:primary\s+)?(\d+)x(\d+)\+(\d+)\+(\d+)',
                        result.stdout
                    ):
                        monitors.append({
                            "id": len(monitors) + 1,
                            "name": m.group(1),
                            "width": int(m.group(2)),
                            "height": int(m.group(3)),
                            "left": int(m.group(4)),
                            "top": int(m.group(5)),
                        })
            except Exception:
                pass

        if not monitors:
            monitors.append({
                "id": 1,
                "width": 1920,
                "height": 1080,
                "left": 0,
                "top": 0,
                "note": "estimated (no display info)",
            })

        self._cache["monitors"] = monitors
        return monitors

    def take_screenshot(
        self,
        region: Optional[Tuple[int, int, int, int]] = None,
        save: bool = True,
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Делает скриншот экрана.

        Args:
            region: (left, top, width, height) — область, или None для полного экрана.
            save: Сохранять ли файл.
            filename: Имя файла (автогенерация если None).

        Returns:
            dict с ключами: success, path, width, height, size_kb, error.
        """
        if not _HAS_MSS:
            return {
                "success": False,
                "error": "mss не установлен. Установите: pip install mss",
                "path": None,
                "width": 0,
                "height": 0,
            }

        try:
            with mss.mss() as sct:
                if region:
                    monitor = {
                        "left": region[0],
                        "top": region[1],
                        "width": region[2],
                        "height": region[3],
                    }
                else:
                    monitor = sct.monitors[1]  # Первый реальный монитор

                screenshot = sct.grab(monitor)

                if not filename:
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    self._screenshot_count += 1
                    filename = f"screen_{ts}_{self._screenshot_count}.png"

                # Защита от path traversal — оставляем только имя файла
                safe_name = Path(filename).name
                if not safe_name:
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    safe_name = f"screen_{ts}.png"
                path = str(SCREENSHOTS_DIR / safe_name)

                if save:
                    # Сохраняем через mss
                    mss.tools.to_png(screenshot.rgb, screenshot.size, output=path)

                width = screenshot.width
                height = screenshot.height
                size_kb = 0
                if save and os.path.exists(path):
                    size_kb = os.path.getsize(path) / 1024

                logger.info(f"CV: screenshot {width}x{height} -> {path}")

                return {
                    "success": True,
                    "path": path,
                    "width": width,
                    "height": height,
                    "size_kb": round(size_kb, 1),
                    "timestamp": time.time(),
                }

        except Exception as e:
            logger.error(f"CV: ошибка скриншота: {e}")
            return {
                "success": False,
                "error": "Ошибка при создании скриншота.",
                "path": None,
                "width": 0,
                "height": 0,
            }

    def analyze_screenshot(self, image_path: str) -> Dict[str, Any]:
        """
        Анализирует скриншот: размер, яркость, контраст.

        Args:
            image_path: Путь к изображению.

        Returns:
            dict с информацией об изображении.
        """
        if not os.path.exists(image_path):
            return {"success": False, "error": f"Файл не найден: {image_path}"}

        result: Dict[str, Any] = {
            "success": True,
            "path": image_path,
            "size_kb": round(os.path.getsize(image_path) / 1024, 1),
        }

        if _HAS_PIL:
            try:
                img = Image.open(image_path)
                result["width"] = img.width
                result["height"] = img.height
                result["mode"] = img.mode
                result["format"] = img.format or "unknown"

                # Средняя яркость
                if img.mode in ("RGB", "RGBA"):
                    gray = img.convert("L")
                    pixels = list(gray.getdata())
                    if pixels:
                        avg_brightness = sum(pixels) / len(pixels)
                        result["avg_brightness"] = round(avg_brightness, 1)
                        result["is_dark"] = avg_brightness < 80
                        result["is_bright"] = avg_brightness > 180
                img.close()
            except Exception as e:
                result["analysis_error"] = "Ошибка анализа изображения."
        else:
            result["note"] = "Pillow не установлен (расширенный анализ недоступен)"

        return result

    def list_screenshots(self, limit: int = 20) -> list:
        """Возвращает список сохранённых скриншотов."""
        files = sorted(
            SCREENSHOTS_DIR.glob("*.png"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:limit]
        return [
            {
                "name": f.name,
                "path": str(f),
                "size_kb": round(f.stat().st_size / 1024, 1),
                "modified": time.ctime(f.stat().st_mtime),
            }
            for f in files
        ]

    def format_status(self) -> str:
        """Форматирует статус CV-модуля."""
        caps = self.get_capabilities()
        monitors = self.get_monitors()
        screenshots = self.list_screenshots(5)

        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║         👁 Computer Vision — Статус              ║")
        lines.append("╠══════════════════════════════════════════════════╣")
        lines.append(f"║  Скриншоты: {'✅' if caps['screenshot'] else '❌ (mss не установлен)'}")
        lines.append(f"║  Обработка: {'✅' if caps['image_processing'] else '❌ (Pillow не установлен)'}")
        lines.append(f"║  Мониторов: {len(monitors)}")

        for m in monitors:
            lines.append(f"║    🖥 #{m['id']}: {m.get('width', '?')}x{m.get('height', '?')}")

        if screenshots:
            lines.append("║  Последние скриншоты:")
            for s in screenshots[:3]:
                lines.append(f"║    📸 {s['name']} ({s['size_kb']} KB)")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    def clear_cache(self) -> None:
        """Очищает кэш."""
        self._cache = {}
