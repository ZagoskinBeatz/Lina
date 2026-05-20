# -*- coding: utf-8 -*-
"""
Lina Core — Human Response Layer.

Phase 19: Гарантирует, что пользователь НИКОГДА не увидит:
  - JSON intent / confidence
  - routing debug
  - внутренние системные сообщения
  - технические логи моделей

Каждый ответ проходит через этот слой перед отправкой пользователю.

Если ответ содержит internal leakage → вызывается full fallback
→ генерируется нормальный человеческий ответ.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional, Callable

logger = logging.getLogger("lina.core.human_response")


# ─── Паттерны internal leakage ─────────────────────────────────────────────────

# JSON-подобные структуры с intent/confidence
_LEAKAGE_PATTERNS = [
    # {"intent": "...", "confidence": ...}
    re.compile(r'\{\s*"intent"\s*:', re.IGNORECASE),
    re.compile(r'\{\s*"confidence"\s*:', re.IGNORECASE),
    # intent= или confidence= в начале строки
    re.compile(r'^\s*intent\s*[=:]\s*', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*confidence\s*[=:]\s*[\d.]', re.IGNORECASE | re.MULTILINE),
    # routing debug markers
    re.compile(r'^\s*Route:\s*intent\s*=', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*Intent:\s*\w+\s*\(confidence\s*=', re.IGNORECASE | re.MULTILINE),
    # model tier labels in response
    re.compile(r'\[(?:mini|full|🟢|🔵)\s*(?:mini|full)?\]', re.IGNORECASE),
    # internal cache marker
    re.compile(r'^\[кэш\]\s*', re.IGNORECASE),
    # Chain-of-Thought / meta-analysis leaks (Qwen и похожие модели любят
    # «думать вслух» в начале ответа на английском)
    re.compile(r'\bThinking\s+Process\s*:', re.IGNORECASE),
    re.compile(r'\b(?:Analyze|Analyzing)\s+the\s+(?:Request|Provided\s+Context|Question|Input)\b',
               re.IGNORECASE),
    re.compile(r'^\s*(?:My\s+Role|Constraints|My\s+Task|Step\s*\d+\s*:)\s*:',
               re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*\d+\.\s+(?:Analyze|Identify|Understand|Plan)\b',
               re.IGNORECASE | re.MULTILINE),
    # XML-подобные thinking-теги
    re.compile(r'<think(?:ing)?\b[^>]*>', re.IGNORECASE),
    # Утечка системного промпта / инструкций (модель повторяет свои правила)
    re.compile(r'АЛГОРИТМ\s*\(выполни\s+строго', re.IGNORECASE),
    re.compile(r'БЕЗОПАСНОСТЬ\s*[—\-]\s*НИКОГДА', re.IGNORECASE),
    re.compile(r'ПРОТОКОЛ\s+ВЫПОЛНЕНИЯ', re.IGNORECASE),
    re.compile(r'ВЕРИФИКАЦИЯ\s*\(обязательн', re.IGNORECASE),
    re.compile(r'Шаг\s+[ABC]\.\s+(?:Проверь|Подбери|Сформируй)', re.IGNORECASE),
]

# Технические префиксы для удаления
_TECH_PREFIXES = [
    re.compile(r'^\[кэш\]\s*', re.IGNORECASE),
    re.compile(r'^\[🟢\s*mini\]\s*', re.IGNORECASE),
    re.compile(r'^\[🔵\s*full\]\s*', re.IGNORECASE),
    re.compile(r'^### ASSISTANT\s*', re.IGNORECASE),
    re.compile(r'^### Lina:\s*', re.IGNORECASE),
    re.compile(r'^Lina:\s*', re.IGNORECASE),
]

# Безопасный fallback-ответ когда всё сломалось
SAFE_FALLBACK_RESPONSE = (
    "Извините, произошла внутренняя ошибка. "
    "Пожалуйста, попробуйте переформулировать запрос."
)

# Минимальная длина "полезного" ответа (1 = single digit/char OK, e.g. "4")
_MIN_USEFUL_LENGTH = 1

# Порог: ответ целиком похож на JSON-объект
_FULL_JSON_RE = re.compile(r'^\s*\{.*\}\s*$', re.DOTALL)


# ─── Утечки CoT — попытка вырезать вместо отбрасывания ─────────────────────────

# XML-подобные thinking-блоки (Qwen3, DeepSeek-R1 и пр.)
_THINK_BLOCK_RE = re.compile(
    r'<think(?:ing)?\b[^>]*>.*?</think(?:ing)?>',
    re.IGNORECASE | re.DOTALL,
)

# Несколько типичных «открывалок» CoT, после которых модель льёт
# свои размышления английским текстом. Срезаем всё до конца блока,
# а блок заканчивается обычно перед нормальным ответом.
_COT_OPENERS_RE = re.compile(
    r'(?:'
    r'\bThinking\s+Process\s*:'
    r'|^\s*(?:My\s+Role|Constraints|My\s+Task)\s*:'
    r'|^\s*\d+\.\s+Analyze\s+the\s+(?:Request|Provided\s+Context|Input|Question)\b'
    r')',
    re.IGNORECASE | re.MULTILINE,
)

# Маркеры, которые часто отделяют CoT от финального ответа.
_COT_END_MARKERS = [
    re.compile(r'^\s*(?:Final\s+(?:Answer|Response)|Ответ)\s*:\s*', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*---\s*$', re.MULTILINE),
    re.compile(r'\n\n(?=[А-ЯЁ])'),  # пустая строка → русское предложение
]


def _strip_cot(text: str) -> str:
    """Try to remove chain-of-thought blocks, keeping the actual answer.

    1. Удаляем `<think>...</think>` (нативные теги).
    2. Если найден маркер CoT-вступления, ищем границу и режем по ней.
    Возвращает очищенный текст. Если ничего не получилось — текст без изменений.
    """
    if not text:
        return text

    # 1. Native thinking tags
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    # ещё могут быть незакрытые <think> — отбрасываем всё до </think>
    cleaned = re.sub(
        r'<think(?:ing)?\b[^>]*>.*?(?:</think(?:ing)?>|$)',
        '', cleaned, flags=re.IGNORECASE | re.DOTALL,
    ).strip()

    # 2. CoT openers — режем до маркера-конца
    if _COT_OPENERS_RE.search(cleaned):
        for end_re in _COT_END_MARKERS:
            m = end_re.search(cleaned)
            if m:
                tail = cleaned[m.end():].strip()
                if len(tail) >= 20:
                    return tail
    return cleaned


# ─── Результат проверки ────────────────────────────────────────────────────────

@dataclass
class SanitizeResult:
    """Результат санитизации ответа.

    Attributes:
        text: Очищенный текст для пользователя.
        was_sanitized: Были ли применены изменения.
        leakage_detected: Обнаружена ли утечка internal data.
        fallback_used: Использован ли fallback.
        original_length: Длина исходного ответа.
        issues: Список обнаруженных проблем.
    """
    text: str = ""
    was_sanitized: bool = False
    leakage_detected: bool = False
    fallback_used: bool = False
    original_length: int = 0
    issues: list = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = []

    def to_dict(self) -> dict:
        return {
            "was_sanitized": self.was_sanitized,
            "leakage_detected": self.leakage_detected,
            "fallback_used": self.fallback_used,
            "original_length": self.original_length,
            "issues": self.issues,
        }


# ─── Human Response Layer ──────────────────────────────────────────────────────

class HumanResponseLayer:
    """Слой очистки ответов для пользователя.

    Гарантирует:
      1. Никакого JSON intent/confidence в ответе
      2. Никаких технических префиксов
      3. Никаких пустых или бессмысленных ответов
      4. Связный, вежливый, завершённый текст

    Если что-то не так → fallback_fn (full-модель) или safe response.
    """

    def __init__(self, fallback_fn: Optional[Callable] = None):
        """Инициализация.

        Args:
            fallback_fn: Функция fallback-генерации.
                Сигнатура: fn(query: str) → str
        """
        self._fallback_fn = fallback_fn
        self._stats = {
            "total": 0,
            "clean": 0,
            "sanitized": 0,
            "leakage_blocked": 0,
            "fallback_used": 0,
        }

    def sanitize(
        self,
        response: str,
        query: str = "",
    ) -> SanitizeResult:
        """Очищает ответ перед отправкой пользователю.

        Порядок проверок:
          1. Пустой/None → fallback
          2. Удалить технические префиксы
          3. Проверить на JSON leakage → fallback
          4. Убрать лишние пробелы/переводы строк
          5. Проверить минимальную длину

        Args:
            response: Сырой ответ от модели.
            query: Исходный запрос (для fallback-контекста).

        Returns:
            SanitizeResult с очищенным текстом.
        """
        self._stats["total"] += 1
        issues = []

        # ── 1. Пустой ответ ──
        if not response or not response.strip():
            issues.append("empty_response")
            text = self._do_fallback(query, issues)
            return SanitizeResult(
                text=text,
                was_sanitized=True,
                leakage_detected=False,
                fallback_used=True,
                original_length=0,
                issues=issues,
            )

        original_length = len(response)
        text = response

        # ── 2. Удалить технические префиксы ──
        for pattern in _TECH_PREFIXES:
            new_text = pattern.sub("", text)
            if new_text != text:
                issues.append(f"removed_prefix:{pattern.pattern[:30]}")
                text = new_text

        text = text.strip()

        # ── 2b. Срезать CoT-блоки (Qwen «Thinking Process», <think>, пр.) ──
        stripped_cot = _strip_cot(text)
        if stripped_cot != text and stripped_cot.strip():
            issues.append("cot_stripped")
            logger.debug(
                "CoT block stripped: %d → %d chars",
                len(text), len(stripped_cot),
            )
            text = stripped_cot.strip()

        # ── 3. Проверить на JSON leakage ──
        if self._is_leakage(text):
            issues.append("internal_leakage_detected")
            self._stats["leakage_blocked"] += 1
            logger.debug(
                "Leakage blocked: %s → fallback", text[:100]
            )
            text = self._do_fallback(query, issues)
            return SanitizeResult(
                text=text,
                was_sanitized=True,
                leakage_detected=True,
                fallback_used=True,
                original_length=original_length,
                issues=issues,
            )

        # ── 4. Убрать лишние пробелы ──
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        # ── 5. Минимальная длина ──
        if len(text) < _MIN_USEFUL_LENGTH:
            issues.append("too_short")
            text = self._do_fallback(query, issues)
            return SanitizeResult(
                text=text,
                was_sanitized=True,
                leakage_detected=False,
                fallback_used=True,
                original_length=original_length,
                issues=issues,
            )

        # ── Чистый ответ ──
        if issues:
            self._stats["sanitized"] += 1
            return SanitizeResult(
                text=text,
                was_sanitized=True,
                original_length=original_length,
                issues=issues,
            )

        self._stats["clean"] += 1
        return SanitizeResult(
            text=text,
            was_sanitized=False,
            original_length=original_length,
        )

    def _is_leakage(self, text: str) -> bool:
        """Проверяет, содержит ли ответ внутренние данные.

        Args:
            text: Текст для проверки.

        Returns:
            True если обнаружена утечка.
        """
        # Полностью JSON-объект?
        if _FULL_JSON_RE.match(text):
            # Проверяем, есть ли intent/confidence ключи
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    keys = {k.lower() for k in obj}
                    if "intent" in keys or "confidence" in keys:
                        return True
            except (json.JSONDecodeError, TypeError):
                pass

        # Строка начинается с { и содержит intent/confidence
        stripped = text.strip()
        if stripped.startswith("{"):
            if re.search(r'"intent"\s*:', stripped) or re.search(r'"confidence"\s*:', stripped):
                return True

        # Паттерны утечки
        for pattern in _LEAKAGE_PATTERNS:
            if pattern.search(text):
                return True

        return False

    def _do_fallback(self, query: str, issues: list) -> str:
        """Выполняет fallback-генерацию.

        Args:
            query: Исходный запрос.
            issues: Список проблем (добавляется запись).

        Returns:
            Fallback-ответ или safe response.
        """
        self._stats["fallback_used"] += 1

        if self._fallback_fn is not None:
            try:
                fallback = self._fallback_fn(query)
                if fallback and fallback.strip():
                    # Рекурсивная проверка: fallback тоже может быть мусором
                    if not self._is_leakage(fallback.strip()):
                        return fallback.strip()
                    issues.append("fallback_also_leaked")
            except Exception as e:
                logger.debug("Fallback generation failed: %s", e, exc_info=True)
                issues.append("fallback_error")

        return SAFE_FALLBACK_RESPONSE

    def get_stats(self) -> dict:
        """Статистика слоя."""
        return dict(self._stats)
