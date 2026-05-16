# -*- coding: utf-8 -*-
"""
Lina Metrics — Метрики токенов (Token Metrics Collector).

Собирает и агрегирует метрики использования токенов:
  - input_tokens   — токены во входе
  - output_tokens  — токены на выходе
  - context_usage  — процент использования контекстного окна
  - tokens_per_sec — скорость генерации

Поддерживает историю и экспорт.

Phase 9 — Controlled Autonomous Runtime.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger("lina.metrics.token_metrics")


# ═══════════════════════════════════════════════════════════
#  Модели данных
# ═══════════════════════════════════════════════════════════

@dataclass
class TokenRecord:
    """Одна запись об использовании токенов.

    Attributes:
        model_tier: Тип модели.
        input_tokens: Токенов на входе.
        output_tokens: Токенов на выходе.
        context_window: Размер контекстного окна.
        context_usage: Процент использования (0.0-1.0).
        generation_time: Время генерации (секунды).
        tokens_per_sec: Скорость генерации (токенов/сек).
        timestamp: Время записи.
        metadata: Дополнительные данные.
    """
    model_tier: str = "full"
    input_tokens: int = 0
    output_tokens: int = 0
    context_window: int = 2048
    context_usage: float = 0.0
    generation_time: float = 0.0
    tokens_per_sec: float = 0.0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "model_tier": self.model_tier,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "context_window": self.context_window,
            "context_usage_pct": round(self.context_usage * 100, 1),
            "generation_time_s": round(self.generation_time, 3),
            "tokens_per_sec": round(self.tokens_per_sec, 1),
            "timestamp": self.timestamp,
        }


@dataclass
class TokenSummary:
    """Агрегированная сводка по токенам.

    Attributes:
        total_requests: Общее количество запросов.
        total_input_tokens: Суммарные входные токены.
        total_output_tokens: Суммарные выходные токены.
        avg_input_tokens: Средние входные.
        avg_output_tokens: Средние выходные.
        avg_context_usage: Средний процент использования.
        avg_tokens_per_sec: Средняя скорость.
        max_context_usage: Максимальный процент использования.
        model_usage: Счётчик по моделям.
        warnings: Количество предупреждений (>90%).
    """
    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    avg_input_tokens: float = 0.0
    avg_output_tokens: float = 0.0
    avg_context_usage: float = 0.0
    avg_tokens_per_sec: float = 0.0
    max_context_usage: float = 0.0
    model_usage: Dict[str, int] = field(default_factory=dict)
    warnings: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "total_requests": self.total_requests,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": (
                self.total_input_tokens + self.total_output_tokens
            ),
            "avg_input_tokens": round(self.avg_input_tokens, 1),
            "avg_output_tokens": round(self.avg_output_tokens, 1),
            "avg_context_usage_pct": round(
                self.avg_context_usage * 100, 1
            ),
            "avg_tokens_per_sec": round(self.avg_tokens_per_sec, 1),
            "max_context_usage_pct": round(
                self.max_context_usage * 100, 1
            ),
            "model_usage": self.model_usage,
            "context_warnings": self.warnings,
        }


# ═══════════════════════════════════════════════════════════
#  TokenMetricsCollector
# ═══════════════════════════════════════════════════════════

# Порог для предупреждения о высоком использовании контекста
CONTEXT_WARNING_THRESHOLD = 0.90


class TokenMetricsCollector:
    """Коллектор метрик использования токенов.

    Собирает данные о каждом запросе к LLM
    и предоставляет агрегированные метрики.

    Attributes:
        max_records: Максимальное количество записей.
        warning_threshold: Порог для предупреждений.
        _records: Список записей.
        _model_switch_count: Счётчик переключений модели.
        _last_model: Последняя использованная модель.
    """

    def __init__(
        self,
        max_records: int = 5000,
        warning_threshold: float = CONTEXT_WARNING_THRESHOLD,
    ):
        """Инициализация коллектора.

        Args:
            max_records: Максимальное количество записей.
            warning_threshold: Порог для предупреждений (0.0-1.0).
        """
        self.max_records = max_records
        self.warning_threshold = warning_threshold
        self._records: List[TokenRecord] = []
        self._model_switch_count: int = 0
        self._last_model: Optional[str] = None

    # ───────────────────────────────────────────────────────
    #  Запись метрик
    # ───────────────────────────────────────────────────────

    def record(
        self,
        model_tier: str,
        input_tokens: int,
        output_tokens: int,
        context_window: int,
        generation_time: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TokenRecord:
        """Записывает метрики одного запроса.

        Args:
            model_tier: Тип модели.
            input_tokens: Токенов на входе.
            output_tokens: Токенов на выходе.
            context_window: Размер контекстного окна.
            generation_time: Время генерации (секунды).
            metadata: Дополнительные данные.

        Returns:
            Созданная TokenRecord.
        """
        # Вычисляем производные метрики
        context_usage = (
            input_tokens / context_window
            if context_window > 0
            else 0.0
        )

        tokens_per_sec = (
            output_tokens / generation_time
            if generation_time > 0
            else 0.0
        )

        record = TokenRecord(
            model_tier=model_tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_window=context_window,
            context_usage=context_usage,
            generation_time=generation_time,
            tokens_per_sec=tokens_per_sec,
            metadata=metadata or {},
        )

        self._records.append(record)

        # Трекинг переключений модели
        if self._last_model is not None and model_tier != self._last_model:
            self._model_switch_count += 1
        self._last_model = model_tier

        # Предупреждение о высоком использовании
        if context_usage >= self.warning_threshold:
            logger.warning(
                "⚠ High context usage: %.1f%% (%d/%d tokens) "
                "model=%s",
                context_usage * 100,
                input_tokens,
                context_window,
                model_tier,
            )

        # Ограничиваем размер
        if len(self._records) > self.max_records:
            self._records = self._records[-self.max_records:]

        return record

    # ───────────────────────────────────────────────────────
    #  Агрегация
    # ───────────────────────────────────────────────────────

    def get_summary(self) -> TokenSummary:
        """Возвращает агрегированную сводку.

        Returns:
            TokenSummary со всеми метриками.
        """
        if not self._records:
            return TokenSummary()

        total = len(self._records)
        total_input = sum(r.input_tokens for r in self._records)
        total_output = sum(r.output_tokens for r in self._records)

        # Средние значения
        avg_in = total_input / total
        avg_out = total_output / total
        avg_ctx = sum(r.context_usage for r in self._records) / total
        max_ctx = max(r.context_usage for r in self._records)

        # Средняя скорость (только ненулевые)
        speeds = [r.tokens_per_sec for r in self._records if r.tokens_per_sec > 0]
        avg_speed = sum(speeds) / len(speeds) if speeds else 0.0

        # Подсчёт моделей
        model_usage: Dict[str, int] = {}
        for r in self._records:
            model_usage[r.model_tier] = (
                model_usage.get(r.model_tier, 0) + 1
            )

        # Предупреждения
        warnings = sum(
            1 for r in self._records
            if r.context_usage >= self.warning_threshold
        )

        return TokenSummary(
            total_requests=total,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            avg_input_tokens=avg_in,
            avg_output_tokens=avg_out,
            avg_context_usage=avg_ctx,
            avg_tokens_per_sec=avg_speed,
            max_context_usage=max_ctx,
            model_usage=model_usage,
            warnings=warnings,
        )

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    @property
    def model_switch_count(self) -> int:
        """Количество переключений между моделями."""
        return self._model_switch_count

    @property
    def total_records(self) -> int:
        """Общее количество записей."""
        return len(self._records)

    def get_records(
        self,
        model_tier: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Возвращает последние записи.

        Args:
            model_tier: Фильтр по модели (None → все).
            limit: Максимальное количество.

        Returns:
            Список сериализованных записей.
        """
        filtered = self._records
        if model_tier:
            filtered = [
                r for r in filtered
                if r.model_tier == model_tier
            ]
        return [r.to_dict() for r in filtered[-limit:]]

    def clear(self) -> None:
        """Очищает все записи."""
        self._records.clear()
        self._model_switch_count = 0
        self._last_model = None

    def format_summary(self) -> str:
        """Форматированная сводка.

        Returns:
            Строка со сводкой метрик.
        """
        s = self.get_summary()
        if s.total_requests == 0:
            return "📊 Tokens: нет данных"

        lines = [
            "📊 Token Metrics:",
            f"   Запросов: {s.total_requests}",
            f"   Токены: {s.total_input_tokens} in / "
            f"{s.total_output_tokens} out",
            f"   Средний вход: {s.avg_input_tokens:.0f} токенов",
            f"   Средний выход: {s.avg_output_tokens:.0f} токенов",
            f"   Контекст: avg={s.avg_context_usage*100:.1f}% "
            f"max={s.max_context_usage*100:.1f}%",
            f"   Скорость: {s.avg_tokens_per_sec:.1f} tok/s",
            f"   Переключений модели: {self._model_switch_count}",
        ]

        if s.warnings > 0:
            lines.append(
                f"   ⚠ Предупреждений (ctx>90%): {s.warnings}"
            )

        if s.model_usage:
            usage = ", ".join(
                f"{k}={v}" for k, v in s.model_usage.items()
            )
            lines.append(f"   Модели: {usage}")

        return "\n".join(lines)
