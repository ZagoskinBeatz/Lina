"""
Lina CV — OCR Engine (Распознавание текста на экране).

Функции:
- Распознавание текста из скриншотов
- Обнаружение ошибок и предупреждений на экране
- Извлечение прогресс-баров (текстовых)
- Поиск определённых слов/фраз на экране

Зависимости (опциональные):
- pytesseract: OCR (обёртка над Tesseract)
- Pillow (PIL): загрузка изображений

Если зависимости недоступны — fallback-режим с информативными сообщениями.
"""

import os
import re
from typing import Optional, Dict, Any, List

from lina.system.logger import logger

# ── Опциональные импорты ──

_HAS_TESSERACT = False
_HAS_PIL = False

try:
    import pytesseract
    _HAS_TESSERACT = True
except ImportError:
    pass

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    pass


# Паттерны для обнаружения ошибок и предупреждений
ERROR_PATTERNS = [
    re.compile(r"(?i)error[:\s]"),
    re.compile(r"(?i)ошибка[:\s]"),
    re.compile(r"(?i)failed[:\s]"),
    re.compile(r"(?i)failure[:\s]"),
    re.compile(r"(?i)не\s*удалось"),
    re.compile(r"(?i)critical[:\s]"),
    re.compile(r"(?i)exception[:\s]"),
    re.compile(r"(?i)fatal[:\s]"),
    re.compile(r"(?i)crash"),
    re.compile(r"(?i)segfault"),
    re.compile(r"(?i)permission\s+denied"),
    re.compile(r"(?i)access\s+denied"),
    re.compile(r"(?i)not\s+found"),
    re.compile(r"(?i)не\s+найден"),
]

WARNING_PATTERNS = [
    re.compile(r"(?i)warning[:\s]"),
    re.compile(r"(?i)предупреждение[:\s]"),
    re.compile(r"(?i)внимание[:\s]"),
    re.compile(r"(?i)deprecated"),
    re.compile(r"(?i)caution"),
    re.compile(r"(?i)notice[:\s]"),
]

PROGRESS_PATTERNS = [
    re.compile(r"(\d{1,3})\s*%"),           # "45%"
    re.compile(r"(\d+)\s*/\s*(\d+)"),        # "5/10"
    re.compile(r"\[([#=]+)\s*\]"),           # "[####    ]"
    re.compile(r"(?:progress|прогресс)[:\s]*(\d+)", re.IGNORECASE),
]


class OCREngine:
    """
    OCR движок — распознавание текста с экрана.

    Поддерживает:
    - Распознавание текста из изображений
    - Обнаружение ошибок/предупреждений
    - Извлечение прогресса (проценты, счётчики)
    - Мультиязычность (rus+eng)
    """

    def __init__(self, lang: str = "rus+eng"):
        self.lang = lang
        self._last_text = ""

    @property
    def available(self) -> bool:
        """Доступен ли OCR."""
        return _HAS_TESSERACT and _HAS_PIL

    def get_capabilities(self) -> Dict[str, bool]:
        """Возвращает доступные возможности."""
        return {
            "ocr": _HAS_TESSERACT,
            "image_load": _HAS_PIL,
            "tesseract_installed": self._check_tesseract(),
        }

    def _check_tesseract(self) -> bool:
        """Проверяет установлен ли Tesseract."""
        if not _HAS_TESSERACT:
            return False
        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def recognize_text(self, image_path: str) -> Dict[str, Any]:
        """
        Распознаёт текст на изображении.

        Args:
            image_path: Путь к изображению.

        Returns:
            dict с ключами: success, text, lines, word_count, error.
        """
        if not os.path.exists(image_path):
            return {
                "success": False,
                "error": f"Файл не найден: {image_path}",
                "text": "",
                "lines": [],
            }

        if not _HAS_PIL:
            return {
                "success": False,
                "error": "Pillow не установлен. Установите: pip install Pillow",
                "text": "",
                "lines": [],
            }

        if not _HAS_TESSERACT:
            return {
                "success": False,
                "error": "pytesseract не установлен. Установите: pip install pytesseract",
                "text": "",
                "lines": [],
            }

        try:
            img = Image.open(image_path)
            text = pytesseract.image_to_string(img, lang=self.lang)
            img.close()

            lines = [l.strip() for l in text.split("\n") if l.strip()]
            words = text.split()

            self._last_text = text

            logger.info(f"CV OCR: {len(lines)} lines, {len(words)} words from {image_path}")

            return {
                "success": True,
                "text": text,
                "lines": lines,
                "word_count": len(words),
                "line_count": len(lines),
                "lang": self.lang,
            }

        except Exception as e:
            logger.error(f"CV OCR error: {e}")
            return {
                "success": False,
                "error": "Ошибка распознавания текста.",
                "text": "",
                "lines": [],
            }

    def find_errors(self, text: Optional[str] = None) -> Dict[str, Any]:
        """
        Ищет ошибки и предупреждения в тексте.

        Args:
            text: Текст для анализа (или последний распознанный).

        Returns:
            dict с errors, warnings, и summary.
        """
        if text is None:
            text = self._last_text

        if not text:
            return {
                "errors": [],
                "warnings": [],
                "has_errors": False,
                "has_warnings": False,
                "summary": "Нет текста для анализа.",
            }

        lines = text.split("\n")
        errors = []
        warnings = []

        for i, line in enumerate(lines, 1):
            line_stripped = line.strip()
            if not line_stripped:
                continue

            for pattern in ERROR_PATTERNS:
                if pattern.search(line_stripped):
                    errors.append({
                        "line": i,
                        "text": line_stripped[:200],
                        "type": "error",
                    })
                    break

            for pattern in WARNING_PATTERNS:
                if pattern.search(line_stripped):
                    warnings.append({
                        "line": i,
                        "text": line_stripped[:200],
                        "type": "warning",
                    })
                    break

        has_errors = len(errors) > 0
        has_warnings = len(warnings) > 0

        summary_parts = []
        if has_errors:
            summary_parts.append(f"❌ {len(errors)} ошибок")
        if has_warnings:
            summary_parts.append(f"⚠ {len(warnings)} предупреждений")
        if not summary_parts:
            summary_parts.append("✅ Ошибок не найдено")

        return {
            "errors": errors,
            "warnings": warnings,
            "has_errors": has_errors,
            "has_warnings": has_warnings,
            "summary": ", ".join(summary_parts),
        }

    def find_progress(self, text: Optional[str] = None) -> Dict[str, Any]:
        """
        Ищет индикаторы прогресса в тексте.

        Args:
            text: Текст для анализа (или последний распознанный).

        Returns:
            dict с progress_items и estimated_percent.
        """
        if text is None:
            text = self._last_text

        if not text:
            return {
                "found": False,
                "items": [],
                "estimated_percent": None,
            }

        items = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Процент
            m = re.search(r"(\d{1,3})\s*%", line)
            if m:
                pct = int(m.group(1))
                if 0 <= pct <= 100:
                    items.append({
                        "type": "percent",
                        "value": pct,
                        "text": line[:100],
                    })

            # Счётчик X/Y
            m = re.search(r"(\d+)\s*/\s*(\d+)", line)
            if m:
                current = int(m.group(1))
                total = int(m.group(2))
                if total > 0:
                    pct = round(current / total * 100, 1)
                    items.append({
                        "type": "counter",
                        "current": current,
                        "total": total,
                        "percent": pct,
                        "text": line[:100],
                    })

        estimated = None
        if items:
            percents = []
            for item in items:
                if item["type"] == "percent":
                    percents.append(item["value"])
                elif item["type"] == "counter":
                    percents.append(item["percent"])
            if percents:
                estimated = round(sum(percents) / len(percents), 1)

        return {
            "found": len(items) > 0,
            "items": items,
            "estimated_percent": estimated,
        }

    def search_text(
        self,
        query: str,
        text: Optional[str] = None,
        case_sensitive: bool = False,
    ) -> Dict[str, Any]:
        """
        Ищет строку в распознанном тексте.

        Args:
            query: Искомая строка.
            text: Текст для поиска (или последний распознанный).
            case_sensitive: Учитывать регистр.

        Returns:
            dict с matches.
        """
        if text is None:
            text = self._last_text

        if not text:
            return {"found": False, "matches": [], "count": 0}

        lines = text.split("\n")
        matches = []

        for i, line in enumerate(lines, 1):
            if case_sensitive:
                if query in line:
                    matches.append({"line": i, "text": line.strip()[:200]})
            else:
                if query.lower() in line.lower():
                    matches.append({"line": i, "text": line.strip()[:200]})

        return {
            "found": len(matches) > 0,
            "matches": matches,
            "count": len(matches),
            "query": query,
        }

    def format_analysis(self, image_path: str) -> str:
        """
        Полный анализ изображения: OCR + ошибки + прогресс.

        Returns:
            Форматированный отчёт.
        """
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║         🔍 OCR Анализ экрана                     ║")
        lines.append("╠══════════════════════════════════════════════════╣")

        # OCR
        ocr_result = self.recognize_text(image_path)
        if not ocr_result["success"]:
            lines.append(f"║  ❌ OCR: {ocr_result['error']}")
            lines.append("╚══════════════════════════════════════════════════╝")
            return "\n".join(lines)

        lines.append(f"║  📝 Распознано: {ocr_result['line_count']} строк, {ocr_result['word_count']} слов")

        # Ошибки
        err_result = self.find_errors()
        lines.append(f"║  {err_result['summary']}")
        for err in err_result["errors"][:5]:
            lines.append(f"║    ❌ [{err['line']}]: {err['text'][:60]}")
        for warn in err_result["warnings"][:5]:
            lines.append(f"║    ⚠ [{warn['line']}]: {warn['text'][:60]}")

        # Прогресс
        prog_result = self.find_progress()
        if prog_result["found"]:
            lines.append(f"║  📊 Прогресс: {prog_result['estimated_percent']}%")
            for item in prog_result["items"][:3]:
                if item["type"] == "percent":
                    lines.append(f"║    ▶ {item['value']}% — {item['text'][:50]}")
                elif item["type"] == "counter":
                    lines.append(f"║    ▶ {item['current']}/{item['total']} — {item['text'][:50]}")

        # Превью текста
        if ocr_result["lines"]:
            lines.append("║  ── Начало текста ──")
            for tl in ocr_result["lines"][:5]:
                lines.append(f"║    {tl[:60]}")
            if len(ocr_result["lines"]) > 5:
                lines.append(f"║    ... ещё {len(ocr_result['lines']) - 5} строк")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    def get_last_text(self) -> str:
        """Возвращает последний распознанный текст."""
        return self._last_text
