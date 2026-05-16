# -*- coding: utf-8 -*-
"""
Lina CI — Генератор отчётов (CI Reporter).

Генерация отчётов CI в форматах:
  1. JSON — для автоматизации
  2. CLI — для терминала
  3. Markdown — для GitHub / документации

Phase 10 — AI Runtime v2.
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

from lina.ci.runner import TestSuiteResult

logger = logging.getLogger("lina.ci.reporter")


class CIReporter:
    """Генератор CI-отчётов.

    Собирает результаты тестов и метрики,
    генерирует отчёты в разных форматах.

    Attributes:
        project_name: Имя проекта.
        version: Версия.
        _results: Результаты тестов.
        _metrics: Дополнительные метрики.
    """

    def __init__(
        self,
        project_name: str = "Lina",
        version: str = "0.5.0",
    ):
        """Инициализация.

        Args:
            project_name: Имя проекта.
            version: Текущая версия.
        """
        self.project_name = project_name
        self.version = version
        self._results: List[TestSuiteResult] = []
        self._metrics: Dict[str, Any] = {}

    def add_results(
        self,
        results: List[TestSuiteResult],
    ) -> None:
        """Добавляет результаты тестов.

        Args:
            results: Список TestSuiteResult.
        """
        self._results.extend(results)

    def add_metrics(self, metrics: Dict[str, Any]) -> None:
        """Добавляет метрики.

        Args:
            metrics: Словарь метрик.
        """
        self._metrics.update(metrics)

    # ───────────────────────────────────────────────────────
    #  JSON отчёт
    # ───────────────────────────────────────────────────────

    def generate_json_report(
        self,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Генерирует JSON-отчёт.

        Args:
            output_path: Путь для сохранения (None = только dict).

        Returns:
            Словарь с отчётом.
        """
        total_passed = sum(r.passed for r in self._results)
        total_failed = sum(r.failed for r in self._results)
        total_tests = sum(r.total for r in self._results)
        total_time = sum(r.duration for r in self._results)

        report = {
            "project": self.project_name,
            "version": self.version,
            "timestamp": time.time(),
            "summary": {
                "total_suites": len(self._results),
                "total_tests": total_tests,
                "passed": total_passed,
                "failed": total_failed,
                "duration_s": round(total_time, 1),
                "success": total_failed == 0,
            },
            "suites": [r.to_dict() for r in self._results],
            "metrics": self._metrics,
        }

        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info("CI report saved to %s", output_path)

        return report

    # ───────────────────────────────────────────────────────
    #  Markdown отчёт
    # ───────────────────────────────────────────────────────

    def generate_markdown_report(self) -> str:
        """Генерирует Markdown-отчёт.

        Returns:
            Строка в формате Markdown.
        """
        total_passed = sum(r.passed for r in self._results)
        total_failed = sum(r.failed for r in self._results)
        total_tests = sum(r.total for r in self._results)
        total_time = sum(r.duration for r in self._results)
        all_ok = total_failed == 0

        badge = "✅ PASSED" if all_ok else "❌ FAILED"

        lines = [
            f"# {self.project_name} CI Report",
            "",
            f"**Version:** {self.version}",
            f"**Status:** {badge}",
            f"**Tests:** {total_passed}/{total_tests}",
            f"**Duration:** {total_time:.1f}s",
            "",
            "## Test Suites",
            "",
            "| Suite | Passed | Failed | Total | Duration | Status |",
            "|-------|--------|--------|-------|----------|--------|",
        ]

        for r in self._results:
            status = "✅" if r.success else "❌"
            lines.append(
                f"| {r.suite_name} | {r.passed} | {r.failed} | "
                f"{r.total} | {r.duration:.1f}s | {status} |"
            )

        if self._metrics:
            lines.extend([
                "",
                "## Metrics",
                "",
            ])
            for key, value in self._metrics.items():
                lines.append(f"- **{key}:** {value}")

        return "\n".join(lines)

    # ───────────────────────────────────────────────────────
    #  CLI отчёт
    # ───────────────────────────────────────────────────────

    def format_cli_report(self) -> str:
        """Форматированный отчёт для CLI.

        Returns:
            Строка для терминала.
        """
        total_passed = sum(r.passed for r in self._results)
        total_failed = sum(r.failed for r in self._results)
        total_tests = sum(r.total for r in self._results)
        total_time = sum(r.duration for r in self._results)
        all_ok = total_failed == 0

        badge = "✅ ALL PASSED" if all_ok else "❌ FAILURES DETECTED"

        lines = [
            "╔═══════════════════════════════════════════╗",
            f"║  {self.project_name} CI Report v{self.version}",
            "╚═══════════════════════════════════════════╝",
            "",
            f"  Status: {badge}",
            f"  Tests:  {total_passed}/{total_tests}",
            f"  Failed: {total_failed}",
            f"  Time:   {total_time:.1f}s",
            "",
        ]

        for r in self._results:
            icon = "✅" if r.success else "❌"
            lines.append(
                f"  {icon} {r.suite_name}: "
                f"{r.passed}/{r.total} ({r.duration:.1f}s)"
            )

        return "\n".join(lines)
