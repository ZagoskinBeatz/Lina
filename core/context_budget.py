# -*- coding: utf-8 -*-
"""
Lina — Context Budget Manager (Phase 20.2).

Гарантирует:  prompt_tokens + max_tokens <= n_ctx  в 100% случаев.
Никаких overflow. Никаких "Requested tokens exceed context window".

Алгоритм:
  1. Собрать полный prompt (system + history + rag + user)
  2. Посчитать токены через tokenizer
  3. Если overflow:
     a. Обрезать rag_context (до лимита, затем до 0)
     b. Обрезать историю (старые сообщения)
  4. Пересобрать prompt
  5. Вычислить final_max_tokens = min(max_tokens, n_ctx - prompt_tokens - 32)
  6. Если available <= 64 → обрезать историю до 0
  7. Вернуть (final_prompt, final_max_tokens)

Приоритет блоков (от наивысшего):
  1. system_prompt  — никогда не удаляется
  2. user_input     — никогда не удаляется
  3. history        — обрезается с начала (старые первыми)
  4. rag_context    — обрезается первым
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Tuple

logger = logging.getLogger("lina.core.context_budget")


# ─── Лимиты по умолчанию ──────────────────────────────────────────────────────

MAX_HISTORY_TOKENS = 1500
MAX_RAG_TOKENS = 1000
SYSTEM_PROMPT_LIMIT = 600

# Резерв для stop-токенов, BOS/EOS, chat-template маркеров
# Phase 20.4: увеличен с 32 до 64 — llama добавляет BOS + служебные
SAFETY_MARGIN = 64

# Если available <= этого значения → обрезать историю до минимума
MIN_GENERATION_THRESHOLD = 64

# Минимум полезных токенов для генерации (ниже — ответ бессмысленный)
MIN_USEFUL_TOKENS = 16

# Эвристика: 1 токен ≈ 2.2 символов (для русского текста с BPE)
# Ранее было 3.5, но реальные замеры LLaMA tokenizer показали ~2.2
CHARS_PER_TOKEN = 2.2


# ─── Результат ─────────────────────────────────────────────────────────────────

@dataclass
class BudgetResult:
    """Результат работы ContextBudgetManager.build_prompt()."""
    prompt: str = ""
    max_tokens: int = 0
    prompt_tokens: int = 0
    total_budget: int = 0           # prompt_tokens + max_tokens
    n_ctx: int = 0
    history_trimmed: bool = False
    rag_trimmed: bool = False
    budget_constrained: bool = False  # True when max_tokens < MIN_USEFUL_TOKENS
    history_entries_kept: int = 0
    rag_tokens_original: int = 0
    rag_tokens_final: int = 0

    @property
    def fits(self) -> bool:
        return self.total_budget <= self.n_ctx

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "max_tokens": self.max_tokens,
            "total_budget": self.total_budget,
            "n_ctx": self.n_ctx,
            "fits": self.fits,
            "budget_constrained": self.budget_constrained,
            "history_trimmed": self.history_trimmed,
            "rag_trimmed": self.rag_trimmed,
            "history_entries_kept": self.history_entries_kept,
        }


# ─── Хелпер-токенизатор (fallback) ────────────────────────────────────────────

class HeuristicTokenizer:
    """Fallback-токенизатор на основе эвристики символов/токен."""

    def tokenize(self, data: bytes) -> list:
        """Возвращает pseudo-list длиной ≈ количество токенов."""
        if not data:
            return []
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        n = int(len(text) / CHARS_PER_TOKEN * 1.1)  # +10% запас
        return range(max(n, 1))  # O(1) memory vs [0]*n


# ─── Основной класс ───────────────────────────────────────────────────────────

class ContextBudgetManager:
    """Менеджер контекстного бюджета (Phase 20.3).

    Гарантирует: prompt_tokens + max_tokens <= n_ctx.
    Всегда. Без исключений.

    Использует llm.tokenize() из llama-cpp-python для точного
    подсчёта токенов. Если llm=None — эвристика.

    Args:
        llm: Объект llama-cpp с методом tokenize(bytes) → list[int].
             Если None — используется HeuristicTokenizer.
        n_ctx: Размер контекстного окна модели (в токенах).
    """

    def __init__(
        self,
        llm=None,
        n_ctx: int = 4096,
        max_history_tokens: int = MAX_HISTORY_TOKENS,
        max_rag_tokens: int = MAX_RAG_TOKENS,
        system_prompt_limit: int = SYSTEM_PROMPT_LIMIT,
    ):
        if llm is not None and not hasattr(llm, "tokenize"):
            raise RuntimeError(
                "llm object does not support tokenize(). "
                "Requires llama-cpp-python >= 0.2.x"
            )
        self.llm = llm or HeuristicTokenizer()
        self.n_ctx = n_ctx
        self.max_history_tokens = max_history_tokens
        self.max_rag_tokens = max_rag_tokens
        self.system_prompt_limit = system_prompt_limit

    # ── Подсчёт токенов ──

    def count(self, text: str) -> int:
        """Подсчитывает количество токенов в тексте.

        Использует llm.tokenize() для точного подсчёта.

        Args:
            text: Текст для подсчёта.

        Returns:
            Количество токенов.
        """
        if not text:
            return 0
        tokens = self.llm.tokenize(text.encode("utf-8"))
        return len(tokens)

    # ── Сборка промпта ──

    def build_prompt(
        self,
        system_prompt: str,
        history: Optional[List[str]] = None,
        rag_context: Optional[str] = None,
        user_input: str = "",
        max_tokens: int = 256,
    ) -> Tuple[str, int]:
        """Собирает prompt с гарантией: prompt_tokens + max_tokens <= n_ctx.

        Делегирует к build_prompt_detailed() и возвращает упрощённый результат.

        Args:
            system_prompt: Системный промпт.
            history: Список сообщений ["user: ...", "assistant: ...", ...].
            rag_context: RAG-контекст (может быть None).
            user_input: Запрос пользователя.
            max_tokens: Желаемый max_tokens для генерации.

        Returns:
            Tuple[final_prompt, final_max_tokens]
            Гарантия: count(final_prompt) + final_max_tokens <= n_ctx
        """
        result = self.build_prompt_detailed(
            system_prompt=system_prompt,
            history=history,
            rag_context=rag_context,
            user_input=user_input,
            max_tokens=max_tokens,
        )
        return result.prompt, result.max_tokens

    # ── Вспомогательные методы ──

    def _assemble(
        self,
        system_prompt: str,
        history: List[str],
        rag_context: str,
        user_input: str,
    ) -> str:
        """Собирает промпт из блоков."""
        parts = [f"### SYSTEM\n{system_prompt}"]

        if history:
            parts.append("### HISTORY\n" + "\n".join(history))

        if rag_context:
            parts.append(f"### CONTEXT\n{rag_context}")

        parts.append(f"### USER\n{user_input}")
        parts.append("### ASSISTANT")

        return "\n\n".join(parts)

    def _trim_text_to_tokens(self, text: str, max_tokens: int) -> str:
        """Обрезает текст до указанного количества токенов."""
        if not text:
            return ""
        tokens = self.count(text)
        if tokens <= max_tokens:
            return text

        # Бинарный поиск по длине текста
        ratio = max_tokens / tokens
        cut = int(len(text) * ratio)
        trimmed = text[:cut]

        # Обрезаем по последнему переносу строки для чистоты
        last_nl = trimmed.rfind('\n')
        if last_nl > len(trimmed) // 2:
            trimmed = trimmed[:last_nl]

        # Проверяем и корректируем
        while self.count(trimmed) > max_tokens and len(trimmed) > 10:
            trimmed = trimmed[:int(len(trimmed) * 0.9)]
            last_nl = trimmed.rfind('\n')
            if last_nl > len(trimmed) // 2:
                trimmed = trimmed[:last_nl]

        return trimmed.strip()

    def _trim_text_by_ratio(self, text: str, ratio: float) -> str:
        """Обрезает текст до указанной доли."""
        if ratio <= 0.0 or not text:
            return ""
        cut = int(len(text) * ratio)
        trimmed = text[:cut]
        last_nl = trimmed.rfind('\n')
        if last_nl > len(trimmed) // 2:
            trimmed = trimmed[:last_nl]
        return trimmed.strip()

    def _trim_history(
        self, history: List[str], max_tokens: int
    ) -> List[str]:
        """Обрезает историю с начала (старые сообщения первыми)."""
        if not history:
            return []

        # Считаем суммарные токены
        total = sum(self.count(h) for h in history)
        if total <= max_tokens:
            return history

        # Удаляем с начала (самые старые)
        result = list(history)
        while result and total > max_tokens:
            removed = result.pop(0)
            total -= self.count(removed)

        return result

    def build_prompt_detailed(
        self,
        system_prompt: str,
        history: Optional[List[str]] = None,
        rag_context: Optional[str] = None,
        user_input: str = "",
        max_tokens: int = 256,
    ) -> BudgetResult:
        """Как build_prompt, но возвращает полный BudgetResult."""
        history = list(history or [])
        rag_context = rag_context or ""

        result = BudgetResult(n_ctx=self.n_ctx)
        result.rag_tokens_original = self.count(rag_context)

        rag_context = self._trim_text_to_tokens(rag_context, self.max_rag_tokens)
        history = self._trim_history(history, self.max_history_tokens)

        prompt = self._assemble(system_prompt, history, rag_context, user_input)
        prompt_tokens = self.count(prompt)

        budget_limit = self.n_ctx - max_tokens - SAFETY_MARGIN

        if prompt_tokens > budget_limit:
            if rag_context:
                result.rag_trimmed = True
                for ratio in (0.5, 0.25, 0.0):
                    trimmed_rag = self._trim_text_by_ratio(rag_context, ratio)
                    prompt = self._assemble(
                        system_prompt, history, trimmed_rag, user_input
                    )
                    prompt_tokens = self.count(prompt)
                    if prompt_tokens <= budget_limit:
                        rag_context = trimmed_rag
                        break
                else:
                    rag_context = ""
                    prompt = self._assemble(
                        system_prompt, history, "", user_input
                    )
                    prompt_tokens = self.count(prompt)

        if prompt_tokens > budget_limit:
            result.history_trimmed = True
            while history and prompt_tokens > budget_limit:
                history.pop(0)
                prompt = self._assemble(
                    system_prompt, history, rag_context, user_input
                )
                prompt_tokens = self.count(prompt)

        available = self.n_ctx - prompt_tokens - SAFETY_MARGIN
        final_max_tokens = min(max_tokens, max(available, 1))

        if available <= MIN_GENERATION_THRESHOLD and history:
            result.history_trimmed = True
            history = []
            prompt = self._assemble(
                system_prompt, [], rag_context, user_input
            )
            prompt_tokens = self.count(prompt)
            available = self.n_ctx - prompt_tokens - SAFETY_MARGIN
            final_max_tokens = min(max_tokens, max(available, 1))

        # Если prompt всё ещё > n_ctx → обрезать system_prompt
        if prompt_tokens + SAFETY_MARGIN >= self.n_ctx:
            bare_prompt = self._assemble("", [], "", user_input)
            bare_tokens = self.count(bare_prompt)
            sys_budget = self.n_ctx - bare_tokens - SAFETY_MARGIN - 1
            if sys_budget < 1:
                sys_budget = 1
            system_prompt = self._trim_text_to_tokens(system_prompt, sys_budget)
            prompt = self._assemble(system_prompt, [], rag_context, user_input)
            prompt_tokens = self.count(prompt)
            available = self.n_ctx - prompt_tokens - SAFETY_MARGIN
            final_max_tokens = max(available, 1)

        # Если user_input > n_ctx → обрезать user_input (last resort)
        if prompt_tokens + SAFETY_MARGIN >= self.n_ctx:
            overhead_prompt = self._assemble("", [], "", "")
            overhead_tokens = self.count(overhead_prompt)
            user_budget = self.n_ctx - overhead_tokens - SAFETY_MARGIN - 1
            if user_budget < 1:
                user_budget = 1
            user_input = self._trim_text_to_tokens(user_input, user_budget)
            system_prompt = ""
            rag_context = ""
            prompt = self._assemble(system_prompt, [], rag_context, user_input)
            prompt_tokens = self.count(prompt)
            available = self.n_ctx - prompt_tokens - SAFETY_MARGIN
            final_max_tokens = max(available, 1)

        # ── Phase 20.4: HARD ENFORCEMENT (detailed) ──
        prompt_tokens = self.count(prompt)
        available = self.n_ctx - prompt_tokens

        if available <= 0:
            raise RuntimeError(
                f"Prompt exceeds context window BEFORE generation: "
                f"prompt_tokens={prompt_tokens}, n_ctx={self.n_ctx}"
            )

        allowed_max_tokens = available - SAFETY_MARGIN
        if allowed_max_tokens < 1:
            allowed_max_tokens = 1
        final_max_tokens = min(final_max_tokens, allowed_max_tokens)

        result.prompt = prompt
        result.prompt_tokens = prompt_tokens
        result.max_tokens = final_max_tokens
        result.total_budget = prompt_tokens + final_max_tokens
        result.history_entries_kept = len(history)
        result.rag_tokens_final = self.count(rag_context)

        if final_max_tokens < MIN_USEFUL_TOKENS:
            result.budget_constrained = True
            logger.warning(
                "Budget critically constrained: max_tokens=%d (< %d minimum useful)",
                final_max_tokens, MIN_USEFUL_TOKENS,
            )

        if result.total_budget > self.n_ctx:
            raise RuntimeError(
                f"INVARIANT VIOLATED (detailed): {result.total_budget} > {self.n_ctx}"
            )

        return result
