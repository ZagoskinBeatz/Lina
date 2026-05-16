# -*- coding: utf-8 -*-
"""
Lina Metrics — Трекер задержек (Latency Tracker).

Замеряет время выполнения операций:
  - LLM generation
  - RAG search
  - Safety validation
  - Planning steps
  - Общие команды

Поддерживает:
  - Контекстный менеджер (with tracker.measure("op"))
  - Ручной start/stop
  - Статистику (min, max, avg, p95, p99)

Phase 9 — Controlled Autonomous Runtime.
"""

import time
import logging
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger("lina.metrics.latency")


# ═══════════════════════════════════════════════════════════
#  Модели данных
# ═══════════════════════════════════════════════════════════

@dataclass
class LatencyRecord:
    """Одна запись о задержке.

    Attributes:
        operation: Название операции.
        duration: Длительность (секунды).
        timestamp: Время завершения.
        metadata: Дополнительные данные.
    """
    operation: str
    duration: float
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "operation": self.operation,
            "duration_ms": round(self.duration * 1000, 2),
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class LatencyStats:
    """Статистика задержек для одной операции.

    Attributes:
        operation: Название операции.
        count: Количество замеров.
        total: Суммарное время.
        min_ms: Минимальная задержка (мс).
        max_ms: Максимальная задержка (мс).
        avg_ms: Средняя задержка (мс).
        p95_ms: 95-й перцентиль (мс).
        p99_ms: 99-й перцентиль (мс).
    """
    operation: str
    count: int = 0
    total: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    avg_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "operation": self.operation,
            "count": self.count,
            "total_ms": round(self.total * 1000, 2),
            "min_ms": round(self.min_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "avg_ms": round(self.avg_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "p99_ms": round(self.p99_ms, 2),
        }


# ═══════════════════════════════════════════════════════════
#  LatencyTracker
# ═══════════════════════════════════════════════════════════

class LatencyTracker:
    """Трекер задержек операций.

    Использование:
        tracker = LatencyTracker()

        # Контекстный менеджер
        with tracker.measure("llm_generate"):
            result = llm.generate(query)

        # Ручной замер
        tracker.start("rag_search")
        results = rag.search(query)
        tracker.stop("rag_search")

        # Статистика
        stats = tracker.get_stats("llm_generate")

    Attributes:
        max_records: Максимальное количество записей.
        _records: Список всех записей.
        _active: Активные замеры (start без stop).
    """

    def __init__(self, max_records: int = 10000):
        """Инициализация трекера.

        Args:
            max_records: Максимальное количество записей.
        """
        self.max_records = max_records
        self._records: deque = deque(maxlen=max_records)
        self._active: Dict[str, float] = {}

    # ───────────────────────────────────────────────────────
    #  Контекстный менеджер
    # ───────────────────────────────────────────────────────

    @contextmanager
    def measure(
        self,
        operation: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Контекстный менеджер для замера задержки.

        Args:
            operation: Название операции.
            metadata: Дополнительные данные.

        Yields:
            None — код выполняется внутри with.

        Example:
            with tracker.measure("llm_generate", {"tier": "full"}):
                result = llm.generate(query)
        """
        start = time.time()
        try:
            yield
        finally:
            duration = time.time() - start
            self._record(operation, duration, metadata)

    # ───────────────────────────────────────────────────────
    #  Ручной старт/стоп
    # ───────────────────────────────────────────────────────

    def start(self, operation: str) -> None:
        """Начинает замер операции.

        Args:
            operation: Название операции.
        """
        self._active[operation] = time.time()

    def stop(
        self,
        operation: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Завершает замер операции.

        Args:
            operation: Название операции.
            metadata: Дополнительные данные.

        Returns:
            Длительность в секундах.

        Raises:
            ValueError: Если замер не был начат.
        """
        start = self._active.pop(operation, None)
        if start is None:
            raise ValueError(
                f"No active measurement for '{operation}'"
            )

        duration = time.time() - start
        self._record(operation, duration, metadata)
        return duration

    # ───────────────────────────────────────────────────────
    #  Запись
    # ───────────────────────────────────────────────────────

    def record(
        self,
        operation: str,
        duration: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Записывает готовый замер (внешний источник).

        Args:
            operation: Название операции.
            duration: Длительность (секунды).
            metadata: Дополнительные данные.
        """
        self._record(operation, duration, metadata)

    def _record(
        self,
        operation: str,
        duration: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Внутренняя запись замера.

        Args:
            operation: Название операции.
            duration: Длительность (секунды).
            metadata: Дополнительные данные.
        """
        record = LatencyRecord(
            operation=operation,
            duration=duration,
            metadata=metadata or {},
        )
        self._records.append(record)

        logger.debug(
            "Latency: %s = %.1fms",
            operation, duration * 1000
        )

    # ───────────────────────────────────────────────────────
    #  Статистика
    # ───────────────────────────────────────────────────────

    def get_stats(
        self,
        operation: Optional[str] = None,
    ) -> Dict[str, LatencyStats]:
        """Возвращает статистику задержек.

        Args:
            operation: Конкретная операция (None → все).

        Returns:
            Словарь {operation: LatencyStats}.
        """
        # Группируем записи по операциям
        groups: Dict[str, List[float]] = {}
        for rec in self._records:
            if operation and rec.operation != operation:
                continue
            if rec.operation not in groups:
                groups[rec.operation] = []
            groups[rec.operation].append(rec.duration)

        # Вычисляем статистику
        result = {}
        for op, durations in groups.items():
            result[op] = self._compute_stats(op, durations)

        return result

    @staticmethod
    def _compute_stats(
        operation: str,
        durations: List[float],
    ) -> LatencyStats:
        """Вычисляет статистику для списка длительностей.

        Args:
            operation: Название операции.
            durations: Список длительностей (секунды).

        Returns:
            LatencyStats.
        """
        if not durations:
            return LatencyStats(operation=operation)

        sorted_d = sorted(durations)
        count = len(sorted_d)
        total = sum(sorted_d)

        # Перцентили
        p95_idx = min(int(count * 0.95), count - 1)
        p99_idx = min(int(count * 0.99), count - 1)

        return LatencyStats(
            operation=operation,
            count=count,
            total=total,
            min_ms=sorted_d[0] * 1000,
            max_ms=sorted_d[-1] * 1000,
            avg_ms=(total / count) * 1000,
            p95_ms=sorted_d[p95_idx] * 1000,
            p99_ms=sorted_d[p99_idx] * 1000,
        )

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    def get_records(
        self,
        operation: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Возвращает последние записи.

        Args:
            operation: Фильтр по операции (None → все).
            limit: Максимальное количество.

        Returns:
            Список сериализованных записей.
        """
        filtered = list(self._records)
        if operation:
            filtered = [r for r in filtered if r.operation == operation]
        return [r.to_dict() for r in filtered[-limit:]]

    @property
    def total_records(self) -> int:
        """Общее количество записей."""
        return len(self._records)

    @property
    def operations(self) -> List[str]:
        """Список уникальных операций."""
        return list(set(r.operation for r in self._records))

    def clear(self) -> None:
        """Очищает все записи и активные замеры."""
        self._records.clear()
        self._active.clear()

    def format_stats(self) -> str:
        """Форматированная статистика.

        Returns:
            Многострочная строка.
        """
        stats = self.get_stats()
        if not stats:
            return "⏱  Latency: нет данных"

        lines = ["⏱  Latency Stats:"]
        for op, s in sorted(stats.items()):
            lines.append(
                f"   {op}: count={s.count} "
                f"avg={s.avg_ms:.1f}ms "
                f"p95={s.p95_ms:.1f}ms "
                f"max={s.max_ms:.1f}ms"
            )
        return "\n".join(lines)
