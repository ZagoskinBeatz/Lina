# -*- coding: utf-8 -*-
"""
Lina Metrics — Агрегированный профайлер (Runtime Profiler).

Объединяет все метрики в единый отчёт:
  - Latency (задержки операций)
  - TokenMetrics (использование токенов)
  - Safety (отклонения и блокировки)
  - Planning (итерации и перепланирования)

Экспорт в JSON и CLI.

Phase 9 — Controlled Autonomous Runtime.
"""

import json
import os
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from lina.metrics.latency import LatencyTracker
from lina.metrics.token_metrics import TokenMetricsCollector

logger = logging.getLogger("lina.metrics.profiler")


class RuntimeProfiler:
    """Агрегированный профайлер runtime-метрик.

    Объединяет LatencyTracker и TokenMetricsCollector
    с дополнительными счётчиками (safety, planning).

    Attributes:
        latency: Трекер задержек.
        tokens: Коллектор токенов.
        _counters: Общие счётчики.
        _start_time: Время запуска профайлера.
    """

    def __init__(
        self,
        max_latency_records: int = 10000,
        max_token_records: int = 5000,
    ):
        """Инициализация профайлера.

        Args:
            max_latency_records: Максимум записей latency.
            max_token_records: Максимум записей токенов.
        """
        self.latency = LatencyTracker(max_records=max_latency_records)
        self.tokens = TokenMetricsCollector(max_records=max_token_records)
        self._start_time = time.time()

        # Общие счётчики
        self._counters: Dict[str, int] = {
            "total_requests": 0,
            "safety_rejections": 0,
            "plan_iterations": 0,
            "plan_replans": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "errors": 0,
            # Phase 10 — новые счётчики
            "agent_steps": 0,
            "agent_plans": 0,
            "agent_replans": 0,
            "inference_requests": 0,
            "dedup_hits": 0,
            "model_switches": 0,
        }

    # ───────────────────────────────────────────────────────
    #  Записи метрик
    # ───────────────────────────────────────────────────────

    def record_request(self) -> None:
        """Записывает факт запроса."""
        self._counters["total_requests"] += 1

    def record_safety_rejection(self) -> None:
        """Записывает отклонение Safety Layer."""
        self._counters["safety_rejections"] += 1

    def record_plan_iteration(self) -> None:
        """Записывает итерацию планирования."""
        self._counters["plan_iterations"] += 1

    def record_plan_replan(self) -> None:
        """Записывает перепланирование."""
        self._counters["plan_replans"] += 1

    def record_cache_hit(self) -> None:
        """Записывает попадание в кэш."""
        self._counters["cache_hits"] += 1

    def record_cache_miss(self) -> None:
        """Записывает промах кэша."""
        self._counters["cache_misses"] += 1

    def record_error(self) -> None:
        """Записывает ошибку."""
        self._counters["errors"] += 1

    def record_agent_step(self) -> None:
        """Записывает шаг агента (Phase 10)."""
        self._counters["agent_steps"] += 1

    def record_agent_plan(self) -> None:
        """Записывает создание плана агентом (Phase 10)."""
        self._counters["agent_plans"] += 1

    def record_agent_replan(self) -> None:
        """Записывает перепланирование агентом (Phase 10)."""
        self._counters["agent_replans"] += 1

    def record_inference_request(self) -> None:
        """Записывает запрос к inference (Phase 10)."""
        self._counters["inference_requests"] += 1

    def record_dedup_hit(self) -> None:
        """Записывает дедупликацию запроса (Phase 10)."""
        self._counters["dedup_hits"] += 1

    def record_model_switch(self) -> None:
        """Записывает переключение модели (Phase 10)."""
        self._counters["model_switches"] += 1

    def increment(self, counter_name: str, value: int = 1) -> None:
        """Инкрементирует произвольный счётчик.

        Args:
            counter_name: Название счётчика.
            value: Значение для инкремента.
        """
        self._counters[counter_name] = (
            self._counters.get(counter_name, 0) + value
        )

    # ───────────────────────────────────────────────────────
    #  Агрегированный отчёт
    # ───────────────────────────────────────────────────────

    def get_report(self) -> Dict[str, Any]:
        """Генерирует полный отчёт по всем метрикам.

        Returns:
            Словарь со всеми метриками, готовый для JSON.
        """
        uptime = time.time() - self._start_time

        # Латенция
        latency_stats = {}
        for op, stats in self.latency.get_stats().items():
            latency_stats[op] = stats.to_dict()

        # Токены
        token_summary = self.tokens.get_summary().to_dict()

        return {
            "timestamp": time.time(),
            "uptime_seconds": round(uptime, 1),
            "counters": dict(self._counters),
            "latency": latency_stats,
            "tokens": token_summary,
            "model_switch_count": self.tokens.model_switch_count,
        }

    # ───────────────────────────────────────────────────────
    #  Экспорт
    # ───────────────────────────────────────────────────────

    def export_json(
        self,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Экспортирует метрики в JSON-файл.

        Args:
            output_path: Путь к файлу (None → только dict).

        Returns:
            Отчёт в виде словаря.
        """
        report = self.get_report()

        if output_path:
            path = Path(output_path)
            if ".." in path.parts:
                raise ValueError("Path traversal is not allowed in output_path")
            path = path.resolve()
            path.parent.mkdir(parents=True, exist_ok=True)

            tmp = path.with_suffix('.tmp')
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            os.replace(str(tmp), str(path))

            logger.info("Metrics exported to %s", output_path)

        return report

    # ───────────────────────────────────────────────────────
    #  Форматирование
    # ───────────────────────────────────────────────────────

    def format_report(self) -> str:
        """Форматированный текстовый отчёт.

        Returns:
            Многострочная строка для CLI.
        """
        report = self.get_report()
        uptime = report["uptime_seconds"]
        c = report["counters"]

        lines = [
            "╔════════════════════════════════════╗",
            "║       Runtime Metrics Report       ║",
            "╚════════════════════════════════════╝",
            "",
            f"⏱  Uptime: {uptime:.0f}s",
            f"📝 Запросов: {c.get('total_requests', 0)}",
            f"🛡  Safety rejections: {c.get('safety_rejections', 0)}",
            f"📋 Plan iterations: {c.get('plan_iterations', 0)}",
            f"🔄 Plan replans: {c.get('plan_replans', 0)}",
            f"💾 Cache: {c.get('cache_hits', 0)} hits / "
            f"{c.get('cache_misses', 0)} misses",
            f"❌ Errors: {c.get('errors', 0)}",
            "",
        ]

        # Латенция
        lines.append(self.latency.format_stats())
        lines.append("")

        # Токены
        lines.append(self.tokens.format_summary())

        return "\n".join(lines)

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    @property
    def uptime(self) -> float:
        """Время работы профайлера (секунды)."""
        return time.time() - self._start_time

    def get_counters(self) -> Dict[str, int]:
        """Возвращает копию счётчиков.

        Returns:
            Словарь со счётчиками.
        """
        return dict(self._counters)

    def clear(self) -> None:
        """Полная очистка всех метрик."""
        self.latency.clear()
        self.tokens.clear()
        for key in self._counters:
            self._counters[key] = 0
        self._start_time = time.time()

    def format_status(self) -> str:
        """Краткий статус для Commander.

        Returns:
            Однострочный статус.
        """
        c = self._counters
        summary = self.tokens.get_summary()
        return (
            f"📊 Metrics: {c.get('total_requests', 0)} req, "
            f"{summary.total_input_tokens + summary.total_output_tokens} tok, "
            f"{c.get('safety_rejections', 0)} blocked, "
            f"uptime={self.uptime:.0f}s"
        )
