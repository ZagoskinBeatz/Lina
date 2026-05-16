"""
Lina Integration Test — JSON-репортёр.

Формирует подробный JSON-отчёт по результатам тестирования:
  - Метаданные: дата, версия, окружение
  - Результат каждого теста: вход, ответ, действие, статус, токены
  - Сводка: пройдено / провалено / ошибки / предупреждения
  - Статистика по категориям и моделям
"""

from __future__ import annotations

import json
import time
import platform
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from lina.integration_tests.framework import TestResult, TestStatus


class TestReporter:
    """
    Генерирует JSON-отчёт по результатам интеграционных тестов.

    Формат отчёта:
    {
        "metadata": { ... },
        "summary": { ... },
        "categories": { ... },
        "model_stats": { ... },
        "tests": [ ... ],
        "failed_tests": [ ... ],
        "warnings": [ ... ]
    }
    """

    def __init__(self, results: Optional[List[TestResult]] = None):
        self._results = results or []

    def set_results(self, results: List[TestResult]) -> None:
        """Устанавливает результаты для отчёта."""
        self._results = results

    def generate_report(
        self,
        output_path: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Генерирует полный JSON-отчёт.

        Args:
            output_path: Путь для сохранения (None → не сохранять на диск).
            dry_run: Пометка что тесты были в dry-run режиме.

        Returns:
            Словарь с полным отчётом.
        """
        report = {
            "metadata": self._build_metadata(dry_run),
            "summary": self._build_summary(),
            "categories": self._build_category_stats(),
            "model_stats": self._build_model_stats(),
            "tests": [r.to_dict() for r in self._results],
            "failed_tests": self._get_failed_tests(),
            "warnings": self._get_all_warnings(),
        }

        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)

        return report

    def print_report(self, report: Optional[Dict[str, Any]] = None) -> None:
        """Печатает сводку отчёта в консоль."""
        if report is None:
            report = self.generate_report()

        summary = report["summary"]
        cats = report["categories"]
        model_stats = report["model_stats"]

        print("\n╔══════════════════════════════════════════════════╗")
        print("║      Lina Integration Test — Отчёт            ║")
        print("╠══════════════════════════════════════════════════╣")
        print(f"║  Всего тестов:        {summary['total']:>5}                    ║")
        print(f"║  ✅ Пройдено:         {summary['passed']:>5}                    ║")
        print(f"║  ❌ Провалено:        {summary['failed']:>5}                    ║")
        print(f"║  💥 Ошибки:           {summary['errors']:>5}                    ║")
        print(f"║  ⏭  Пропущено:       {summary['skipped']:>5}                    ║")
        print(f"║  ⚠  Предупреждения:  {summary['warnings']:>5}                    ║")
        print(f"║  ⏱  Общее время:   {summary['total_time_seconds']:>7.1f}s                  ║")
        print("╠══════════════════════════════════════════════════╣")

        print("║  По категориям:                                 ║")
        for cat, stats in cats.items():
            p_rate = stats["pass_rate"]
            bar = "█" * int(p_rate / 5) + "░" * (20 - int(p_rate / 5))
            label = f"{cat[:14]:<14}"
            print(f"║  {label} {bar} {p_rate:>5.1f}%  ║")

        print("╠══════════════════════════════════════════════════╣")
        print("║  По моделям:                                    ║")
        for model, stats in model_stats.items():
            print(
                f"║  {model:<8}: {stats['count']:>3} тестов, "
                f"avg {stats['avg_time']:.1f}s, "
                f"avg_tokens {stats['avg_tokens']:>4}     ║"
            )

        print("╠══════════════════════════════════════════════════╣")

        if report["failed_tests"]:
            print("║  Провалившиеся тесты:                           ║")
            for ft in report["failed_tests"][:10]:
                name = ft["name"][:35]
                print(f"║    ❌ {name:<44}║")
            if len(report["failed_tests"]) > 10:
                print(
                    f"║    ... и ещё "
                    f"{len(report['failed_tests']) - 10}                             ║"
                )

        print("╚══════════════════════════════════════════════════╝")

    # ── Приватные ──

    def _build_metadata(self, dry_run: bool) -> Dict[str, Any]:
        """Метаданные отчёта."""
        import psutil

        return {
            "timestamp": datetime.now().isoformat(),
            "lina_version": "0.4.0",
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "hostname": platform.node(),
            "dry_run": dry_run,
            "cpu_count": psutil.cpu_count(),
            "ram_total_mb": round(psutil.virtual_memory().total / (1024 ** 2)),
        }

    def _build_summary(self) -> Dict[str, Any]:
        """Общая сводка."""
        total = len(self._results)
        passed = sum(1 for r in self._results if r.status == TestStatus.PASSED.value)
        failed = sum(1 for r in self._results if r.status == TestStatus.FAILED.value)
        errors = sum(1 for r in self._results if r.status == TestStatus.ERROR.value)
        skipped = sum(1 for r in self._results if r.status == TestStatus.SKIPPED.value)
        warnings = sum(1 for r in self._results if r.status == TestStatus.WARNING.value)
        total_time = sum(r.elapsed_seconds for r in self._results)
        total_tokens = sum(r.tokens_input + r.tokens_output for r in self._results)
        cache_hits = sum(1 for r in self._results if r.from_cache)

        return {
            "total": total,
            "passed": passed + warnings,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
            "warnings": warnings,
            "pass_rate": round((passed + warnings) / total * 100, 1) if total else 0,
            "total_time_seconds": round(total_time, 2),
            "total_tokens": total_tokens,
            "cache_hits": cache_hits,
        }

    def _build_category_stats(self) -> Dict[str, Dict[str, Any]]:
        """Статистика по категориям."""
        cats: Dict[str, list] = {}
        for r in self._results:
            cats.setdefault(r.category, []).append(r)

        result = {}
        for cat, results in sorted(cats.items()):
            total = len(results)
            passed = sum(
                1 for r in results
                if r.status in (TestStatus.PASSED.value, TestStatus.WARNING.value)
            )
            failed = sum(1 for r in results if r.status == TestStatus.FAILED.value)
            errors = sum(1 for r in results if r.status == TestStatus.ERROR.value)
            avg_time = sum(r.elapsed_seconds for r in results) / total if total else 0

            result[cat] = {
                "total": total,
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "pass_rate": round(passed / total * 100, 1) if total else 0,
                "avg_time_seconds": round(avg_time, 2),
            }

        return result

    def _build_model_stats(self) -> Dict[str, Dict[str, Any]]:
        """Статистика по моделям (full / auto)."""
        models: Dict[str, list] = {}
        for r in self._results:
            tier = r.model_tier or "unknown"
            models.setdefault(tier, []).append(r)

        result = {}
        for model, results in sorted(models.items()):
            total = len(results)
            avg_time = sum(r.elapsed_seconds for r in results) / total if total else 0
            avg_tokens = (
                sum(r.tokens_output for r in results) / total if total else 0
            )
            result[model] = {
                "count": total,
                "avg_time": round(avg_time, 2),
                "avg_tokens": round(avg_tokens),
            }

        return result

    def _get_failed_tests(self) -> List[Dict[str, Any]]:
        """Список провалившихся тестов (для быстрого обзора)."""
        return [
            {
                "test_id": r.test_id,
                "name": r.name,
                "category": r.category,
                "error": r.error_message,
                "input": r.input_text[:100],
                "response_preview": r.llm_response[:200],
            }
            for r in self._results
            if r.status in (TestStatus.FAILED.value, TestStatus.ERROR.value)
        ]

    def _get_all_warnings(self) -> List[Dict[str, str]]:
        """Все предупреждения из всех тестов."""
        warnings = []
        for r in self._results:
            for w in r.warnings:
                warnings.append({
                    "test_id": r.test_id,
                    "name": r.name,
                    "warning": w,
                })
        return warnings
