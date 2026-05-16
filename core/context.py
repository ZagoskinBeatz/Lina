# -*- coding: utf-8 -*-
"""
Lina Core — Сборщик контекста (Context Builder).

Собирает полный контекст для запроса:
  - RAG-контекст (из базы знаний)
  - Runtime-контекст (CPU, RAM, модель)
  - History-контекст (из истории команд)
  - Intent detection (определение типа запроса)

Phase 9 — Controlled Autonomous Runtime.
"""

import re
import logging
import warnings
from typing import Optional, Dict, Any, List, Callable

from lina.core.runtime_state import IntentType, RequestContext

logger = logging.getLogger("lina.core.context")


# ═══════════════════════════════════════════════════════════
#  Паттерны для определения намерений
# ═══════════════════════════════════════════════════════════

# Паттерны мета-команд
_META_PATTERN = re.compile(r"^/\w+", re.IGNORECASE)

# Паттерны системных команд
_COMMAND_PATTERN = re.compile(r"^!", re.IGNORECASE)

# Паттерны цепочек
_CHAIN_PATTERN = re.compile(r"\u2192|->|=>|;\s*(затем|потом)", re.IGNORECASE)

# Паттерны CV
_CV_PATTERNS = re.compile(
    r"скриншот|screenshot|ocr|экран|detect|распозна",
    re.IGNORECASE,
)

# Паттерны планирования (многошаговые задачи)
_PLANNING_PATTERNS = re.compile(
    r"установи\s+и\s+настрой|"
    r"создай\s+проект|"
    r"настрой\s+систему|"
    r"пошагов|step.by.step|"
    r"план\s+действий|"
    r"полная\s+(установка|настройка|диагностика)",
    re.IGNORECASE,
)

# Паттерны RAG запросов
_RAG_PATTERNS = re.compile(
    r"^(найди|поиск|ищи|search|find)\s+",
    re.IGNORECASE,
)


class ContextBuilder:
    """Сборщик контекста для конвейера.

    Собирает и подготавливает контекст для обработки запроса:
      1. Определяет намерение (intent)
      2. Собирает RAG-контекст
      3. Собирает runtime-контекст
      4. Обогащает RequestContext

    Attributes:
        rag_fn: Функция поиска по RAG (query → context).
        runtime_fn: Функция получения runtime info.
        max_rag_chars: Максимум символов RAG-контекста.
    """

    def __init__(
        self,
        rag_fn: Optional[Callable[[str], str]] = None,
        runtime_fn: Optional[Callable[[], str]] = None,
        max_rag_chars: int = 2000,
    ):
        """Инициализация сборщика.

        Args:
            rag_fn: Функция RAG-поиска (query → context string).
            runtime_fn: Функция runtime info (→ str).
            max_rag_chars: Максимум символов RAG.
        """
        self.rag_fn = rag_fn
        self.runtime_fn = runtime_fn
        self.max_rag_chars = max_rag_chars

    # ───────────────────────────────────────────────────────
    #  Определение намерения
    # ───────────────────────────────────────────────────────

    def _detect_intent_internal(self, raw_input: str) -> IntentType:
        """Internal intent detection without deprecation warning."""
        text = raw_input.strip()

        if _META_PATTERN.match(text):
            return IntentType.META
        if _COMMAND_PATTERN.match(text):
            return IntentType.COMMAND
        if _CHAIN_PATTERN.search(text):
            return IntentType.CHAIN
        if _CV_PATTERNS.search(text):
            return IntentType.CV
        if _RAG_PATTERNS.match(text):
            return IntentType.RAG_QUERY
        if _PLANNING_PATTERNS.search(text):
            return IntentType.PLANNING
        return IntentType.QUESTION

    def detect_intent(self, raw_input: str) -> IntentType:
        """Определяет намерение пользователя.

        .. deprecated::
            Используйте IntentRouter.route() вместо этого метода.
            Оставлен для обратной совместимости.

        Args:
            raw_input: Ввод пользователя.

        Returns:
            IntentType.
        """
        warnings.warn(
            "ContextBuilder.detect_intent() is deprecated. "
            "Use IntentRouter.route() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._detect_intent_internal(raw_input)

    # ───────────────────────────────────────────────────────
    #  Сборка контекста
    # ───────────────────────────────────────────────────────

    def build(self, ctx: RequestContext) -> RequestContext:
        """Собирает полный контекст для запроса.

        Обогащает RequestContext:
          - intent (если не задан)
          - rag_context (для вопросов)
          - runtime_context (если доступен)

        Args:
            ctx: Контекст запроса (мутируется).

        Returns:
            Обогащённый RequestContext.
        """
        # Определяем intent
        if ctx.intent is None:
            ctx.intent = self._detect_intent_internal(ctx.raw_input)

        # RAG-контекст (для вопросов и RAG-запросов)
        if ctx.intent in (IntentType.QUESTION, IntentType.RAG_QUERY,
                          IntentType.PLANNING):
            ctx.rag_context = self._get_rag_context(ctx.raw_input)

        # Runtime-контекст
        ctx.runtime_context = self._get_runtime_context()

        return ctx

    def _get_rag_context(self, query: str) -> str:
        """Получает RAG-контекст.

        Args:
            query: Запрос для поиска.

        Returns:
            Строка с контекстом (может быть пустой).
        """
        if self.rag_fn is None:
            return ""

        try:
            context = self.rag_fn(query)
            if context and len(context) > self.max_rag_chars:
                context = context[:self.max_rag_chars]
            return context or ""
        except Exception as e:
            logger.warning("RAG context failed: %s", e)
            return ""

    def _get_runtime_context(self) -> str:
        """Получает runtime-контекст.

        Returns:
            Строка с runtime info (может быть пустой).
        """
        if self.runtime_fn is None:
            return ""

        try:
            return self.runtime_fn() or ""
        except Exception as e:
            logger.warning("Runtime context failed: %s", e)
            return ""

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    def detect_intent_batch(
        self,
        inputs: List[str],
    ) -> List[IntentType]:
        """Определяет намерения для списка вводов.

        Args:
            inputs: Список вводов пользователя.

        Returns:
            Список IntentType.
        """
        return [self._detect_intent_internal(inp) for inp in inputs]
