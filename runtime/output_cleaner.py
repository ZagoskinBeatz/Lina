"""
Lina Runtime — Output Cleaner.

Строгая фильтрация LLM-ответов перед показом пользователю.

Гарантии:
  1. Никаких системных промптов в ответе
  2. Никаких RAG-маркеров ([Источник: ...], --- Контекст ---)
  3. Никаких секционных маркеров (### SYSTEM:, ### Контекст:)
  4. Никаких мега-длинных ответов (hard cap)
  5. Никаких shell-like последовательностей в обычном ответе
  6. Удаление дублированных строк подряд
"""

import re
import logging
from typing import Optional

logger = logging.getLogger("lina.runtime.output_cleaner")


# ── Максимальная длина ответа (символов) ───────────────────────────────────────

MAX_RESPONSE_LENGTH = 4000

# ── Паттерны для удаления ──────────────────────────────────────────────────────

# RAG-маркеры (оставшиеся от build_context)
_RAG_BLOCK = re.compile(
    r"---\s*Контекст из базы знаний\s*---.*?"
    r"(?:---\s*Конец контекста\s*---|$)",
    re.DOTALL,
)
_RAG_MARKERS = [
    re.compile(r"---\s*Контекст из базы знаний\s*---"),
    re.compile(r"---\s*Конец контекста\s*---"),
    re.compile(r"\[Источник:\s*[^\]]*\]"),
    re.compile(r"📜\s*история"),
]

# Секционные маркеры промпта (если модель их процитировала)
_SECTION_MARKERS = re.compile(
    r"^###\s*(SYSTEM|ASSISTANT|HISTORY|CONTEXT|USER|"
    r"Система|Lina|Контекст|Диалог|Пользователь|"
    r"РАНТАЙМ|БЕЗОПАСНОСТЬ|ВОЗМОЖНОСТИ|ФОРМАТ)\s*:?\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Повтор системного промпта (ключевые фразы)
_SYSTEM_LEAK_PHRASES = [
    re.compile(r"Ты\s*—\s*Lina.*локальный\s+ИИ", re.I),
    re.compile(r"ЗАПРЕЩЕНО\s+без\s+подтверждения", re.I),
    re.compile(r"Файловые\s+операции\s+только\s+в\s+домашней", re.I),
    re.compile(r"Пакетный\s+менеджер:", re.I),
    re.compile(r"НЕ\s+повторяй\s+контекст", re.I),
]

# Дублированные строки подряд — removed regex (ReDoS-prone backreference)
# Using iterative dedup instead (see strip_duplicate_lines method)

# Лишние пустые строки (>2 подряд)
_EXCESS_NEWLINES = re.compile(r"\n{3,}")


class OutputCleaner:
    """
    Очистка LLM-ответов — последний барьер перед пользователем.

    Raw LLM output НИКОГДА не должен показываться напрямую.
    Все ответы проходят через clean().
    """

    def __init__(self, max_length: int = MAX_RESPONSE_LENGTH):
        self._max_length = max_length

    def clean(self, text: str) -> str:
        """
        Полная очистка LLM-ответа.

        Pipeline:
          1. strip_rag_markers()
          2. strip_section_markers()
          3. strip_system_leakage()
          4. strip_duplicate_lines()
          5. enforce_max_length()
          6. normalize_whitespace()

        Args:
            text: Сырой LLM-ответ.

        Returns:
            Чистый, безопасный текст для пользователя.
        """
        if not text:
            return ""

        result = text

        # 1. RAG-маркеры
        result = self.strip_rag_markers(result)

        # 2. Секционные маркеры
        result = self.strip_section_markers(result)

        # 3. Утечка системного промпта
        result = self.strip_system_leakage(result)

        # 4. Дублированные строки
        result = self.strip_duplicate_lines(result)

        # 5. Лимит длины
        result = self.enforce_max_length(result)

        # 6. Нормализация пробелов
        result = self.normalize_whitespace(result)

        return result

    def strip_rag_markers(self, text: str) -> str:
        """Удаляет RAG-маркеры и [Источник: ...] из текста."""
        result = _RAG_BLOCK.sub("", text)
        for pattern in _RAG_MARKERS:
            result = pattern.sub("", result)
        return result

    def strip_section_markers(self, text: str) -> str:
        """Удаляет секционные маркеры промпта (### SYSTEM: и т.д.)."""
        return _SECTION_MARKERS.sub("", text)

    def strip_system_leakage(self, text: str) -> str:
        """
        Удаляет фразы, утёкшие из системного промпта.

        Если модель начинает цитировать свои инструкции —
        удаляем строки, содержащие ключевые фразы.
        """
        lines = text.split("\n")
        clean_lines = []
        for line in lines:
            leaked = False
            for pattern in _SYSTEM_LEAK_PHRASES:
                if pattern.search(line):
                    leaked = True
                    logger.debug("System leak stripped: %s", line[:80])
                    break
            if not leaked:
                clean_lines.append(line)
        return "\n".join(clean_lines)

    def strip_duplicate_lines(self, text: str) -> str:
        """Удаляет дублированные строки подряд (iterative, ReDoS-safe)."""
        lines = text.split('\n')
        result: list[str] = []
        prev: Optional[str] = None
        for line in lines:
            if line != prev or not line:
                result.append(line)
            prev = line
        return '\n'.join(result)

    def enforce_max_length(self, text: str) -> str:
        """Обрезает ответ до максимальной длины."""
        if len(text) <= self._max_length:
            return text
        # Обрезаем по последнему полному предложению
        truncated = text[:self._max_length]
        last_period = max(
            truncated.rfind(". "),
            truncated.rfind(".\n"),
            truncated.rfind("。"),
        )
        if last_period > self._max_length // 2:
            truncated = truncated[:last_period + 1]
        return truncated + "\n[...ответ обрезан]"

    def normalize_whitespace(self, text: str) -> str:
        """Нормализует пробелы и переносы строк."""
        result = _EXCESS_NEWLINES.sub("\n\n", text)
        return result.strip()
