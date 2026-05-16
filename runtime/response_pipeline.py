"""
Lina Runtime — Response Pipeline.

Центральный pipeline обработки LLM-ответов.

Поток:
  Raw LLM output
  → extract_answer()     — обрезка до маркера ASSISTANT
  → detect_tool_call()   — обнаружение JSON tool-call
  → clean()              — OutputCleaner
  → validate()           — финальная валидация

Принцип: RAW LLM OUTPUT НИКОГДА НЕ ПОКАЗЫВАЕТСЯ ПОЛЬЗОВАТЕЛЮ.
"""

import re
import json
import logging
from typing import Optional
from dataclasses import dataclass, field

from lina.runtime.output_cleaner import OutputCleaner

logger = logging.getLogger("lina.runtime.response_pipeline")


@dataclass
class PipelineResult:
    """Результат обработки ответа."""
    text: str                          # Финальный текст для пользователя
    is_tool_call: bool = False         # Ответ содержит tool-call JSON
    tool_call: Optional[dict] = None   # Распарсенный tool-call
    was_cleaned: bool = False          # Были ли применены фильтры
    raw_length: int = 0                # Длина сырого ответа
    clean_length: int = 0              # Длина после очистки
    filters_applied: list = field(default_factory=list)  # Список применённых фильтров


# ── Валидные tool-call инструменты ─────────────────────────────────────────────

ALLOWED_TOOLS = frozenset({
    "mkdir", "touch", "rm", "mv", "cp",
    "ls", "cat", "find", "grep",
    "weather", "exchange", "ip_info",
    "web_search", "screenshot", "ocr",
    "run_command", "read_file", "write_file",
})

# JSON tool-call паттерн
_TOOL_JSON_PATTERN = re.compile(
    r'\{\s*"tool"\s*:\s*"[^"]+"\s*,\s*"args"\s*:\s*\{[^}]*\}\s*\}',
    re.DOTALL,
)


class ResponsePipeline:
    """
    Центральный pipeline обработки LLM-ответов.

    Использование:
        pipeline = ResponsePipeline()
        result = pipeline.process(raw_llm_output)
        if result.is_tool_call:
            # Исполнить через ToolExecutor
            ...
        else:
            print(result.text)
    """

    def __init__(self, cleaner: Optional[OutputCleaner] = None):
        self._cleaner = cleaner or OutputCleaner()

    def process(self, raw_output: str) -> PipelineResult:
        """
        Полная обработка LLM-ответа.

        Pipeline:
          1. extract_answer() — обрезка до маркера ASSISTANT
          2. detect_tool_call() — поиск JSON tool-call
          3. clean() — OutputCleaner
          4. Формирование PipelineResult

        Args:
            raw_output: Сырой текст от LLM.

        Returns:
            PipelineResult с чистым текстом и/или tool-call.
        """
        if not raw_output:
            return PipelineResult(text="", raw_length=0, clean_length=0)

        filters = []

        # 1. Извлекаем ответ после маркера ASSISTANT
        answer = self.extract_answer(raw_output)
        if answer != raw_output:
            filters.append("extract_answer")

        # 2. Проверяем на tool-call
        tool_call = self.detect_tool_call(answer)
        if tool_call:
            filters.append("tool_call_detected")
            return PipelineResult(
                text="",
                is_tool_call=True,
                tool_call=tool_call,
                was_cleaned=True,
                raw_length=len(raw_output),
                clean_length=0,
                filters_applied=filters,
            )

        # 3. Очистка через OutputCleaner
        cleaned = self._cleaner.clean(answer)
        if cleaned != answer:
            filters.append("output_cleaner")

        return PipelineResult(
            text=cleaned,
            is_tool_call=False,
            tool_call=None,
            was_cleaned=bool(filters),
            raw_length=len(raw_output),
            clean_length=len(cleaned),
            filters_applied=filters,
        )

    def extract_answer(self, text: str) -> str:
        """
        Извлекает текст после маркера ### ASSISTANT.

        Если LLM сгенерировал текст с маркерами промпта —
        берём только часть после последнего ### ASSISTANT.

        Args:
            text: Сырой LLM-ответ.

        Returns:
            Текст после маркера (или весь текст, если маркера нет).
        """
        # Ищем последний маркер ### ASSISTANT
        markers = ["### ASSISTANT", "### Lina:", "### ASSISTANT:"]
        last_pos = -1
        last_marker_len = 0

        for marker in markers:
            pos = text.rfind(marker)
            if pos > last_pos:
                last_pos = pos
                last_marker_len = len(marker)

        if last_pos >= 0:
            return text[last_pos + last_marker_len:].strip()

        return text.strip()

    def detect_tool_call(self, text: str) -> Optional[dict]:
        """
        Обнаруживает structured tool-call в ответе LLM.

        Формат:
            {"tool": "mkdir", "args": {"path": "/home/user/Projects"}}

        Валидация:
          - tool должен быть в ALLOWED_TOOLS
          - args должен быть dict
          - JSON должен быть валидным

        Args:
            text: Текст ответа LLM.

        Returns:
            Распарсенный tool-call dict или None.
        """
        match = _TOOL_JSON_PATTERN.search(text)
        if not match:
            return None

        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

        # Валидация структуры
        if not isinstance(data, dict):
            return None
        if "tool" not in data or "args" not in data:
            return None
        if not isinstance(data["tool"], str):
            return None
        if not isinstance(data["args"], dict):
            return None

        # Валидация tool name
        if data["tool"] not in ALLOWED_TOOLS:
            logger.warning("Unknown tool in LLM response: %s", data["tool"])
            return None

        logger.info("Tool call detected: %s(%s)", data["tool"], data["args"])
        return data
