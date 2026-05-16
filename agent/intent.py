# -*- coding: utf-8 -*-
"""
Lina Agent — Классификатор намерений (Intent Classifier).

Расширенная классификация запросов с оценкой сложности:
  1. Тип намерения (из core.runtime_state.IntentType)
  2. Уровень сложности (simple / moderate / complex)
  3. Оценка количества шагов
  4. Рекомендация: агент нужен или нет

Работает поверх core.context.ContextBuilder.detect_intent(),
добавляя complexity estimation.

Phase 10 — AI Runtime v2.
"""

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional

from lina.core.runtime_state import IntentType

logger = logging.getLogger("lina.agent.intent")


# ═══════════════════════════════════════════════════════════
#  Уровень сложности
# ═══════════════════════════════════════════════════════════

class ComplexityLevel(str, Enum):
    """Уровень сложности запроса."""
    SIMPLE = "simple"         # Одна команда / быстрый ответ
    MODERATE = "moderate"     # 2-3 шага, но предсказуемые
    COMPLEX = "complex"       # Многошаговая задача, нужен агент


# ═══════════════════════════════════════════════════════════
#  Результат классификации
# ═══════════════════════════════════════════════════════════

@dataclass
class IntentResult:
    """Результат классификации намерения.

    Attributes:
        intent: Тип намерения (IntentType).
        complexity: Уровень сложности.
        estimated_steps: Оценка количества шагов.
        needs_agent: Нужен ли автономный агент.
        confidence: Уверенность (0.0-1.0).
        keywords_matched: Совпавшие ключевые слова.
        reason: Объяснение решения.
    """
    intent: IntentType = IntentType.QUESTION
    complexity: ComplexityLevel = ComplexityLevel.SIMPLE
    estimated_steps: int = 1
    needs_agent: bool = False
    confidence: float = 0.8
    keywords_matched: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "intent": self.intent.value,
            "complexity": self.complexity.value,
            "estimated_steps": self.estimated_steps,
            "needs_agent": self.needs_agent,
            "confidence": round(self.confidence, 2),
            "keywords_matched": self.keywords_matched,
            "reason": self.reason,
        }


# ═══════════════════════════════════════════════════════════
#  Паттерны сложности
# ═══════════════════════════════════════════════════════════

# Паттерны, указывающие на сложные многошаговые задачи
_COMPLEX_PATTERNS = [
    re.compile(r"установи\s+и\s+настрой", re.IGNORECASE),
    re.compile(r"создай\s+проект\s+и", re.IGNORECASE),
    re.compile(r"настрой\s+(полностью|систему|сервер)", re.IGNORECASE),
    re.compile(r"(пошагово|step.by.step)", re.IGNORECASE),
    re.compile(r"полная\s+(установка|настройка|диагностика|миграция)", re.IGNORECASE),
    re.compile(r"автоматизир", re.IGNORECASE),
    re.compile(r"мигрируй|перенеси", re.IGNORECASE),
    re.compile(r"разверни\s+(приложение|сервис|кластер)", re.IGNORECASE),
]

# Паттерны, указывающие на умеренную сложность
_MODERATE_PATTERNS = [
    re.compile(r"проверь\s+и\s+(исправь|настрой|обнови)", re.IGNORECASE),
    re.compile(r"найди\s+и\s+(удали|замени|исправь)", re.IGNORECASE),
    re.compile(r"сравни\s+и\s+выбери", re.IGNORECASE),
    re.compile(r"обнови\s+и\s+перезапусти", re.IGNORECASE),
]

# Ключевые слова для сложных задач (подсчёт совпадений)
_COMPLEXITY_KEYWORDS = [
    "установить", "настроить", "проверить", "создать",
    "удалить", "обновить", "перезапустить", "мигрировать",
    "развернуть", "сконфигурировать", "протестировать",
    "оптимизировать", "диагностировать", "восстановить",
]


# ═══════════════════════════════════════════════════════════
#  AgentIntentClassifier
# ═══════════════════════════════════════════════════════════

class AgentIntentClassifier:
    """Классификатор намерений для агентного слоя.

    Расширяет базовый detect_intent() из ContextBuilder:
      1. Определяет IntentType (базовый)
      2. Оценивает сложность (simple/moderate/complex)
      3. Оценивает количество шагов
      4. Решает, нужен ли автономный агент

    Attributes:
        complexity_threshold: Min ключевых слов для complex.
        _stats: Статистика классификации.
    """

    def __init__(self, complexity_threshold: int = 2):
        """Инициализация.

        Args:
            complexity_threshold: Минимум совпавших ключевых слов
                                  для уровня COMPLEX.
        """
        self.complexity_threshold = complexity_threshold
        self._stats = {
            "total": 0,
            "simple": 0,
            "moderate": 0,
            "complex": 0,
            "agent_required": 0,
        }

    def classify(self, raw_input: str) -> IntentResult:
        """Классифицирует запрос пользователя.

        Args:
            raw_input: Ввод пользователя.

        Returns:
            IntentResult с полной классификацией.
        """
        self._stats["total"] += 1
        text = raw_input.strip()
        result = IntentResult()

        # 1. Базовый intent через паттерны (из core.context)
        result.intent = self._detect_base_intent(text)

        # 2. Анализ сложности
        result = self._estimate_complexity(text, result)

        # 3. Решение: нужен ли агент?
        result.needs_agent = (
            result.complexity == ComplexityLevel.COMPLEX
            or result.intent == IntentType.PLANNING
        )

        if result.needs_agent:
            self._stats["agent_required"] += 1

        # Статистика
        self._stats[result.complexity.value] += 1

        logger.debug(
            "Intent: %s, complexity=%s, steps=%d, agent=%s",
            result.intent.value, result.complexity.value,
            result.estimated_steps, result.needs_agent,
        )

        return result

    def _detect_base_intent(self, text: str) -> IntentType:
        """Определяет базовый тип intent.

        Использует тот же алгоритм что и core.context.ContextBuilder.

        Args:
            text: Текст запроса.

        Returns:
            IntentType.
        """
        if text.startswith("/"):
            return IntentType.META
        if text.startswith("!"):
            return IntentType.COMMAND
        if re.search(r"\u2192|->|=>|;\s*(затем|потом)", text, re.IGNORECASE):
            return IntentType.CHAIN
        if re.search(r"скриншот|screenshot|ocr|экран", text, re.IGNORECASE):
            return IntentType.CV
        if re.match(r"^(найди|поиск|ищи|search|find)\s+", text, re.IGNORECASE):
            return IntentType.RAG_QUERY
        if re.search(
            r"установи\s+и\s+настрой|создай\s+проект|настрой\s+систему"
            r"|пошагов|step.by.step|план\s+действий"
            r"|полная\s+(установка|настройка|диагностика)",
            text, re.IGNORECASE,
        ):
            return IntentType.PLANNING

        return IntentType.QUESTION

    def _estimate_complexity(
        self,
        text: str,
        result: IntentResult,
    ) -> IntentResult:
        """Оценивает сложность запроса.

        Метод:
          1. Проверяем complex patterns → COMPLEX
          2. Проверяем moderate patterns → MODERATE
          3. Считаем ключевые слова → complexity
          4. Длина текста как фактор

        Args:
            text: Текст запроса.
            result: Текущий IntentResult (мутируется).

        Returns:
            Обновлённый IntentResult.
        """
        keywords_found = []
        text_lower = text.lower()

        # Проверка complex patterns
        for pattern in _COMPLEX_PATTERNS:
            match = pattern.search(text)
            if match:
                result.complexity = ComplexityLevel.COMPLEX
                result.estimated_steps = 5
                result.confidence = 0.9
                result.reason = f"Complex pattern: {match.group()}"
                return result

        # Проверка moderate patterns
        for pattern in _MODERATE_PATTERNS:
            match = pattern.search(text)
            if match:
                result.complexity = ComplexityLevel.MODERATE
                result.estimated_steps = 3
                result.confidence = 0.85
                result.reason = f"Moderate pattern: {match.group()}"
                return result

        # Подсчёт ключевых слов
        for kw in _COMPLEXITY_KEYWORDS:
            if kw in text_lower:
                keywords_found.append(kw)

        result.keywords_matched = keywords_found

        if len(keywords_found) >= self.complexity_threshold:
            result.complexity = ComplexityLevel.COMPLEX
            result.estimated_steps = max(3, len(keywords_found) + 1)
            result.confidence = min(0.5 + len(keywords_found) * 0.1, 0.95)
            result.reason = f"Keywords: {', '.join(keywords_found)}"
        elif len(keywords_found) >= 1:
            result.complexity = ComplexityLevel.MODERATE
            result.estimated_steps = 2
            result.confidence = 0.7
            result.reason = f"Keyword: {keywords_found[0]}"
        else:
            # Длинный текст может быть сложной задачей
            if len(text) > 200:
                result.complexity = ComplexityLevel.MODERATE
                result.estimated_steps = 2
                result.confidence = 0.6
                result.reason = f"Long input ({len(text)} chars)"
            else:
                result.complexity = ComplexityLevel.SIMPLE
                result.estimated_steps = 1
                result.confidence = 0.8
                result.reason = "Simple query"

        return result

    # ───────────────────────────────────────────────────────
    #  Статистика
    # ───────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику классификации."""
        return dict(self._stats)

    def format_stats(self) -> str:
        """Форматированная статистика."""
        s = self._stats
        lines = [
            f"Intent Classifier:",
            f"  Total: {s['total']}",
            f"  Simple: {s['simple']}, Moderate: {s['moderate']}, "
            f"Complex: {s['complex']}",
            f"  Agent required: {s['agent_required']}",
        ]
        return "\n".join(lines)
