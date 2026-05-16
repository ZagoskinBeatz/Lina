"""
Lina — Система управления токенным бюджетом (Token Budget Manager).

Предотвращает превышение контекстного окна ДО генерации,
а не ловит ошибку "Requested tokens exceed context window" уже после.

Архитектура:
  ┌────────────────────────────────────────────────────┐
  │ TokenBudget                                        │
  │                                                    │
  │  [estimate_tokens] → подсчёт токенов строки        │
  │  [calculate_budget] → доступный бюджет генерации   │
  │  [trim_to_fit] → авто-урезание до лимита           │
  │  [TrimStrategy] → стратегии урезания               │
  │                                                    │
  │  Приоритет урезания (от первого к последнему):      │
  │    1. История (trim_history)                        │
  │    2. RAG-контекст (trim_rag)                       │
  │    3. Рантайм-блок (trim_runtime)                   │
  │    4. Safe mode (fallback до минимума)              │
  └────────────────────────────────────────────────────┘

Используется единственная модель (full).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any

logger = logging.getLogger("lina.token_budget")


# ─── Константы ─────────────────────────────────────────────────────────────────

# Средний коэффициент: 1 токен ≈ 2.2 символов для русского текста BPE.
# Ранее 3.5, но реальные замеры LLaMA tokenizer → ~2.2 chars/tok.
CHARS_PER_TOKEN_RU = 2.2

# Запас безопасности (токены) — зазор для stop-токенов, BOS/EOS и пр.
SAFETY_MARGIN_DEFAULT = 64

# Минимальный бюджет на генерацию (токены). Если осталось меньше —
# переходим в safe mode.
MIN_GENERATION_BUDGET = 32

# Порог предупреждения — если использовано >90% окна контекста
WARNING_THRESHOLD = 0.90


# ─── Стратегии урезания ────────────────────────────────────────────────────────

class TrimStrategy(Enum):
    """Стратегия урезания промпта для вписывания в контекст."""
    NONE = "none"                # Ничего не урезаем
    TRIM_HISTORY = "history"     # Убрать старые сообщения
    TRIM_RAG = "rag"             # Урезать RAG-контекст
    TRIM_RUNTIME = "runtime"     # Убрать рантайм-блок
    SAFE_MODE = "safe_mode"      # Минимальный промпт (только запрос)


# ─── Результат подсчёта бюджета ────────────────────────────────────────────────

@dataclass
class BudgetReport:
    """
    Отчёт о токенном бюджете для одного запроса.

    Используется для логирования, мониторинга и принятия решений
    о необходимости урезания.
    """
    # Входные параметры
    model_tier: str = "full"              # tier модели
    context_window: int = 4096            # n_ctx модели (токены)
    max_tokens: int = 256                 # max_tokens для генерации
    safety_margin: int = SAFETY_MARGIN_DEFAULT

    # Подсчитанные токены
    system_prompt_tokens: int = 0         # Системный промпт
    runtime_tokens: int = 0              # Рантайм-секция
    rag_context_tokens: int = 0          # RAG-контекст
    query_tokens: int = 0               # Запрос пользователя
    overhead_tokens: int = 0            # Маркеры (### Система:, ### Lina: и пр.)

    # Результаты
    total_input_tokens: int = 0          # Сумма входных токенов
    available_for_generation: int = 0    # Доступно для генерации
    utilization: float = 0.0             # Процент заполнения контекста (0.0-1.0)
    fits: bool = True                    # Помещается ли в контекст
    strategies_applied: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def is_warning(self) -> bool:
        """Превышен ли порог предупреждения (>90%)."""
        return self.utilization > WARNING_THRESHOLD

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация для JSON-отчётов."""
        return {
            "model_tier": self.model_tier,
            "context_window": self.context_window,
            "max_tokens": self.max_tokens,
            "system_prompt_tokens": self.system_prompt_tokens,
            "runtime_tokens": self.runtime_tokens,
            "rag_context_tokens": self.rag_context_tokens,
            "query_tokens": self.query_tokens,
            "overhead_tokens": self.overhead_tokens,
            "total_input_tokens": self.total_input_tokens,
            "available_for_generation": self.available_for_generation,
            "utilization": round(self.utilization, 3),
            "fits": self.fits,
            "strategies_applied": self.strategies_applied,
            "warnings": self.warnings,
        }


# ─── Основной класс ───────────────────────────────────────────────────────────

class TokenBudget:
    """
    Менеджер токенного бюджета.

    Подсчитывает токены ДО генерации и при необходимости
    урезает промпт, чтобы гарантированно вписаться в контекст.

    Usage:
        budget = TokenBudget()
        report = budget.calculate(
            model_tier="full",
            context_window=4096,
            max_tokens=256,
            system_prompt=sys_prompt,
            query="Привет!",
        )
        if not report.fits:
            prompt = budget.trim_to_fit(prompt, report)
    """

    def __init__(self, chars_per_token: float = CHARS_PER_TOKEN_RU):
        self._chars_per_token = chars_per_token

    # ── Подсчёт токенов ──

    def estimate_tokens(self, text: str) -> int:
        """
        Оценивает количество токенов в тексте.

        Использует эвристику: 1 токен ≈ 3.5 символов для русского текста.
        Для точного подсчёта нужен tokenizer модели, но эвристика
        достаточна для бюджетирования (с запасом).

        Args:
            text: Текст для оценки.

        Returns:
            Оценочное количество токенов.
        """
        if not text:
            return 0
        # +10% запас на специальные токены (BPE-фрагменты кириллицы)
        raw = len(text) / self._chars_per_token
        return int(raw * 1.1)

    # ── Расчёт бюджета ──

    def calculate(
        self,
        model_tier: str,
        context_window: int,
        max_tokens: int,
        system_prompt: str,
        query: str,
        rag_context: str = "",
        runtime_section: str = "",
        safety_margin: int = SAFETY_MARGIN_DEFAULT,
    ) -> BudgetReport:
        """
        Рассчитывает токенный бюджет для запроса.

        Формула доступного бюджета:
            available = context_window - input_tokens - max_tokens - safety_margin

        Args:
            model_tier: tier модели
            context_window: Размер контекстного окна (n_ctx)
            max_tokens: Максимум токенов для генерации
            system_prompt: Текст системного промпта
            query: Текст запроса пользователя
            rag_context: RAG-контекст (опционально)
            runtime_section: Рантайм-блок (опционально)
            safety_margin: Запас безопасности (токены)

        Returns:
            BudgetReport с полным расчётом.
        """
        report = BudgetReport(
            model_tier=model_tier,
            context_window=context_window,
            max_tokens=max_tokens,
            safety_margin=safety_margin,
        )

        # Подсчитываем токены каждого блока
        report.system_prompt_tokens = self.estimate_tokens(system_prompt)
        report.runtime_tokens = self.estimate_tokens(runtime_section)
        report.rag_context_tokens = self.estimate_tokens(rag_context)
        report.query_tokens = self.estimate_tokens(query)

        # Overhead: маркеры секций (### Система:, ### Контекст:, и пр.)
        # Примерно 5 токенов на маркер × 4 секции = 20 токенов
        report.overhead_tokens = 20

        # Суммарный вход
        report.total_input_tokens = (
            report.system_prompt_tokens
            + report.runtime_tokens
            + report.rag_context_tokens
            + report.query_tokens
            + report.overhead_tokens
        )

        # Доступный бюджет для генерации
        report.available_for_generation = (
            context_window
            - report.total_input_tokens
            - max_tokens
            - safety_margin
        )

        # Утилизация (0.0-1.0)
        used = report.total_input_tokens + max_tokens + safety_margin
        report.utilization = used / context_window if context_window > 0 else 1.0

        # Помещается ли
        report.fits = report.available_for_generation >= 0

        # Предупреждения
        if not report.fits:
            deficit = abs(report.available_for_generation)
            report.warnings.append(
                f"Превышение на {deficit} токенов "
                f"(вход: {report.total_input_tokens}, "
                f"окно: {context_window}, "
                f"генерация: {max_tokens})"
            )
        elif report.is_warning:
            report.warnings.append(
                f"Высокая утилизация контекста: {report.utilization:.0%} "
                f"(вход: {report.total_input_tokens}/{context_window})"
            )

        return report

    # ── Авто-урезание ──

    def auto_trim(
        self,
        model_tier: str,
        context_window: int,
        max_tokens: int,
        system_prompt: str,
        query: str,
        rag_context: str = "",
        runtime_section: str = "",
        safety_margin: int = SAFETY_MARGIN_DEFAULT,
        compact_prompt: str = "",
    ) -> Dict[str, Any]:
        """
        Авто-урезание промпта для вписывания в контекст.

        Применяет стратегии в порядке приоритета:
          1. trim_runtime    — убрать рантайм-блок
          2. trim_rag        — урезать RAG-контекст
          3. switch_prompt   — переключить на компактный промпт
          4. safe_mode       — только запрос + минимальный промпт

        Args:
            Все аргументы calculate() + compact_prompt для fallback.

        Returns:
            Dict с ключами:
              - system_prompt: (возможно, урезанный)
              - rag_context: (возможно, урезанный)
              - runtime_section: (возможно, пустой)
              - report: BudgetReport
              - trimmed: bool (были ли урезания)
        """
        result = {
            "system_prompt": system_prompt,
            "rag_context": rag_context,
            "runtime_section": runtime_section,
            "trimmed": False,
            "report": None,
        }

        # Первый расчёт — проверяем, нужно ли урезать
        report = self.calculate(
            model_tier=model_tier,
            context_window=context_window,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            query=query,
            rag_context=rag_context,
            runtime_section=runtime_section,
            safety_margin=safety_margin,
        )

        if report.fits and not report.is_warning:
            result["report"] = report
            return result

        # ── Стратегия 1: Убираем рантайм-блок ──
        if runtime_section and not report.fits:
            runtime_section = ""
            result["runtime_section"] = ""
            result["trimmed"] = True
            report = self.calculate(
                model_tier=model_tier,
                context_window=context_window,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
                query=query,
                rag_context=rag_context,
                runtime_section="",
                safety_margin=safety_margin,
            )
            report.strategies_applied.append(TrimStrategy.TRIM_RUNTIME.value)
            if report.fits:
                result["report"] = report
                return result

        # ── Стратегия 2: Урезаем RAG-контекст ──
        if rag_context and not report.fits:
            # Урезаем RAG до 50% → 25% → 0%
            for ratio in (0.5, 0.25, 0.0):
                if ratio == 0.0:
                    trimmed_rag = ""
                else:
                    trim_len = int(len(rag_context) * ratio)
                    trimmed_rag = rag_context[:trim_len].rsplit('\n', 1)[0]

                report = self.calculate(
                    model_tier=model_tier,
                    context_window=context_window,
                    max_tokens=max_tokens,
                    system_prompt=system_prompt,
                    query=query,
                    rag_context=trimmed_rag,
                    runtime_section=result["runtime_section"],
                    safety_margin=safety_margin,
                )
                report.strategies_applied.append(TrimStrategy.TRIM_RAG.value)
                result["rag_context"] = trimmed_rag
                result["trimmed"] = True
                if report.fits:
                    result["report"] = report
                    return result

        # ── Стратегия 3: Переключаем на компактный промпт ──
        if compact_prompt and system_prompt != compact_prompt:
            report = self.calculate(
                model_tier=model_tier,
                context_window=context_window,
                max_tokens=max_tokens,
                system_prompt=compact_prompt,
                query=query,
                rag_context=result["rag_context"],
                runtime_section="",
                safety_margin=safety_margin,
            )
            report.strategies_applied.append("switch_compact_prompt")
            result["system_prompt"] = compact_prompt
            result["runtime_section"] = ""
            result["trimmed"] = True
            if report.fits:
                result["report"] = report
                return result

        # ── Стратегия 4: Safe mode — минимальный промпт ──
        safe_prompt = "Ты — Lina. Отвечай кратко на русском."
        report = self.calculate(
            model_tier=model_tier,
            context_window=context_window,
            max_tokens=min(max_tokens, 128),  # Урезаем и генерацию
            system_prompt=safe_prompt,
            query=query,
            rag_context="",
            runtime_section="",
            safety_margin=safety_margin,
        )
        report.strategies_applied.append(TrimStrategy.SAFE_MODE.value)
        report.warnings.append("Safe mode: минимальный промпт, без RAG/runtime")
        result["system_prompt"] = safe_prompt
        result["rag_context"] = ""
        result["runtime_section"] = ""
        result["trimmed"] = True
        result["report"] = report

        return result

    # ── Утилиты ──

    def fits_in_context(
        self,
        text: str,
        context_window: int,
        max_tokens: int,
        safety_margin: int = SAFETY_MARGIN_DEFAULT,
    ) -> bool:
        """
        Быстрая проверка: помещается ли текст в контекст.

        Args:
            text: Полный промпт.
            context_window: Размер контекстного окна.
            max_tokens: Зарезервировано для генерации.
            safety_margin: Запас безопасности.

        Returns:
            True если помещается.
        """
        tokens = self.estimate_tokens(text)
        return (tokens + max_tokens + safety_margin) <= context_window

    @staticmethod
    def get_model_limits(tier: str = "full") -> Dict[str, Any]:
        """
        Возвращает рекомендации по лимитам для модели.

        Args:
            tier: tier модели (всегда "full")

        Returns:
            Dict с рекомендуемыми параметрами.
        """
        return {
            "max_rag_chars": 3000,       # Расширенный RAG
            "max_rag_chunks": 5,         # До 5 чанков
            "include_runtime": True,     # С рантайм-блоком
            "max_history": 5,            # Последние 5 сообщений
            "verbose_allowed": True,     # Подробный режим доступен
            "reasoning_depth": "deep",   # Глубокий анализ
        }
