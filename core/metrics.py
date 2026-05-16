"""
Lina — Расширенные метрики (X4).

Мониторинг и статистика:
  - Среднее время ответа
  - Процент RAG vs LLM vs дерева
  - Частые вопросы
  - Потребление ресурсов
  - Хранение в памяти (SQLite опционально)
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger("lina.core.metrics")


# ─── Типы ответа ─────────────────────────────────────────────────────────────

class ResponseSource:
    RAG = "rag"               # Ответ из базы знаний
    LLM = "llm"               # Прямая генерация LLM
    DIAGNOSTIC = "diagnostic"  # Диагностическое дерево
    SYSTEM = "system"          # Системная команда
    CACHE = "cache"            # Из кэша
    UNKNOWN = "unknown"


# ─── Запись метрики ───────────────────────────────────────────────────────────

@dataclass
class MetricEntry:
    """Одна запись метрики."""
    timestamp: str
    query: str = ""
    source: str = ResponseSource.UNKNOWN
    response_time_ms: float = 0
    tokens_in: int = 0
    tokens_out: int = 0
    success: bool = True
    error: str = ""

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "query": self.query[:100],  # обрезаем для хранения
            "source": self.source,
            "response_time_ms": self.response_time_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "success": self.success,
            "error": self.error,
        }


@dataclass
class ResourceSnapshot:
    """Снимок ресурсов."""
    timestamp: str
    ram_mb: float = 0
    cpu_percent: float = 0
    model_loaded: bool = False
    uptime_seconds: float = 0

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "ram_mb": self.ram_mb,
            "cpu_percent": self.cpu_percent,
            "model_loaded": self.model_loaded,
            "uptime_seconds": self.uptime_seconds,
        }


# ─── LinaMetrics ───────────────────────────────────────────────────────────

class LinaMetrics:
    """Расширенный сборщик метрик Lina.

    Собирает:
      - Время ответа
      - Источники ответов (RAG/LLM/дерево)
      - Частые вопросы
      - Потребление ресурсов
      - Статистику ошибок
    """

    MAX_HISTORY = 10000  # Максимум записей в памяти
    _MAX_COUNTER_ENTRIES = 10_000  # Макс уникальных ключей в Counter

    def __init__(self):
        self._entries: deque = deque(maxlen=self.MAX_HISTORY)
        self._resources: deque = deque(maxlen=1000)
        self._query_counter: Counter = Counter()
        self._source_counter: Counter = Counter()
        self._error_counter: Counter = Counter()
        self._start_time = time.time()
        self._active_timer: Optional[float] = None
        self._active_query: str = ""
        logger.info("LinaMetrics создан")

    # ── Таймер запроса ──

    def start_query(self, query: str = "") -> None:
        """Начинает замер времени запроса."""
        self._active_timer = time.time()
        self._active_query = query

    def end_query(self, source: str = ResponseSource.UNKNOWN,
                  success: bool = True, error: str = "",
                  tokens_in: int = 0, tokens_out: int = 0) -> MetricEntry:
        """Завершает замер и записывает метрику."""
        elapsed_ms = 0
        if self._active_timer:
            elapsed_ms = (time.time() - self._active_timer) * 1000

        entry = MetricEntry(
            timestamp=datetime.now().isoformat(),
            query=self._active_query,
            source=source,
            response_time_ms=round(elapsed_ms, 2),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            success=success,
            error=error,
        )

        self._record(entry)
        self._active_timer = None
        self._active_query = ""
        return entry

    def record_query(self, query: str, source: str,
                     response_time_ms: float, success: bool = True,
                     error: str = "", tokens_in: int = 0,
                     tokens_out: int = 0) -> MetricEntry:
        """Записывает метрику напрямую (без таймера)."""
        entry = MetricEntry(
            timestamp=datetime.now().isoformat(),
            query=query,
            source=source,
            response_time_ms=round(response_time_ms, 2),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            success=success,
            error=error,
        )
        self._record(entry)
        return entry

    def _record(self, entry: MetricEntry) -> None:
        """Внутренняя запись метрики."""
        self._entries.append(entry)

        self._source_counter[entry.source] += 1
        if entry.query:
            # Нормализуем: lowercase, strip
            normalized = entry.query.lower().strip()
            self._query_counter[normalized] += 1
            if len(self._query_counter) > self._MAX_COUNTER_ENTRIES:
                self._query_counter = Counter(
                    dict(self._query_counter.most_common(self._MAX_COUNTER_ENTRIES // 2))
                )
        if entry.error:
            self._error_counter[entry.error] += 1
            if len(self._error_counter) > self._MAX_COUNTER_ENTRIES:
                self._error_counter = Counter(
                    dict(self._error_counter.most_common(self._MAX_COUNTER_ENTRIES // 2))
                )

    # ── Ресурсы ──

    def record_resource_snapshot(self, ram_mb: float = 0,
                                  cpu_percent: float = 0,
                                  model_loaded: bool = False) -> ResourceSnapshot:
        """Записывает снимок ресурсов."""
        snap = ResourceSnapshot(
            timestamp=datetime.now().isoformat(),
            ram_mb=ram_mb,
            cpu_percent=cpu_percent,
            model_loaded=model_loaded,
            uptime_seconds=time.time() - self._start_time,
        )
        self._resources.append(snap)
        return snap

    def get_current_resources(self) -> Dict:
        """Текущее потребление ресурсов (через /proc)."""
        result = {
            "ram_mb": 0,
            "cpu_percent": 0,
            "uptime_seconds": round(time.time() - self._start_time, 1),
        }
        try:
            import os
            # RSS из /proc/self/status
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        result["ram_mb"] = round(kb / 1024, 1)
                        break
        except Exception:
            pass
        return result

    # ── Статистика ──

    def get_average_response_time(self) -> float:
        """Среднее время ответа в мс."""
        if not self._entries:
            return 0
        total = sum(e.response_time_ms for e in self._entries)
        return round(total / len(self._entries), 2)

    def get_response_time_percentile(self, percentile: float = 95) -> float:
        """P-й перцентиль времени ответа."""
        if not self._entries:
            return 0
        times = sorted(e.response_time_ms for e in self._entries)
        idx = int(len(times) * percentile / 100)
        idx = min(idx, len(times) - 1)
        return times[idx]

    def get_source_distribution(self) -> Dict[str, int]:
        """Распределение источников ответов."""
        return dict(self._source_counter)

    def get_source_percentages(self) -> Dict[str, float]:
        """Распределение источников в процентах."""
        total = sum(self._source_counter.values())
        if total == 0:
            return {}
        return {k: round(v / total * 100, 1)
                for k, v in self._source_counter.items()}

    def get_top_queries(self, n: int = 10) -> List[tuple]:
        """Самые частые вопросы."""
        return self._query_counter.most_common(n)

    def get_error_rate(self) -> float:
        """Процент ошибок."""
        if not self._entries:
            return 0
        errors = sum(1 for e in self._entries if not e.success)
        return round(errors / len(self._entries) * 100, 2)

    def get_total_queries(self) -> int:
        return len(self._entries)

    def get_success_count(self) -> int:
        return sum(1 for e in self._entries if e.success)

    def get_error_count(self) -> int:
        return sum(1 for e in self._entries if not e.success)

    def get_top_errors(self, n: int = 5) -> List[tuple]:
        return self._error_counter.most_common(n)

    def get_noanswer_rate(self) -> float:
        """Процент 'не знаю' ответов (source=unknown)."""
        if not self._entries:
            return 0
        unknown = sum(1 for e in self._entries
                      if e.source == ResponseSource.UNKNOWN)
        return round(unknown / len(self._entries) * 100, 2)

    # ── Сводка ──

    def get_summary(self) -> Dict[str, Any]:
        """Полная сводка метрик."""
        return {
            "total_queries": self.get_total_queries(),
            "success_count": self.get_success_count(),
            "error_count": self.get_error_count(),
            "error_rate_pct": self.get_error_rate(),
            "noanswer_rate_pct": self.get_noanswer_rate(),
            "avg_response_time_ms": self.get_average_response_time(),
            "p95_response_time_ms": self.get_response_time_percentile(95),
            "source_distribution": self.get_source_distribution(),
            "source_percentages": self.get_source_percentages(),
            "top_queries": self.get_top_queries(5),
            "top_errors": self.get_top_errors(3),
            "resources": self.get_current_resources(),
            "uptime_seconds": round(time.time() - self._start_time, 1),
        }

    def get_summary_text(self) -> str:
        """Текстовая сводка."""
        s = self.get_summary()
        lines = [
            f"📊 Метрики Lina",
            f"  Запросов: {s['total_queries']}",
            f"  Успешных: {s['success_count']}",
            f"  Ошибок: {s['error_count']} ({s['error_rate_pct']}%)",
            f"  Среднее время: {s['avg_response_time_ms']} мс",
            f"  P95 время: {s['p95_response_time_ms']} мс",
        ]
        if s["source_percentages"]:
            lines.append("  Источники:")
            for src, pct in s["source_percentages"].items():
                lines.append(f"    {src}: {pct}%")
        return "\n".join(lines)

    # ── Очистка ──

    def clear(self) -> None:
        """Очищает все метрики."""
        self._entries.clear()
        self._resources.clear()
        self._query_counter.clear()
        self._source_counter.clear()
        self._error_counter.clear()
        self._start_time = time.time()

    def to_dict(self) -> Dict:
        return self.get_summary()


# ─── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[LinaMetrics] = None


def get_metrics() -> LinaMetrics:
    global _instance
    if _instance is None:
        _instance = LinaMetrics()
    return _instance


def reset_metrics() -> None:
    global _instance
    _instance = None
