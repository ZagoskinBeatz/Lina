# -*- coding: utf-8 -*-
"""
Lina CI — Запуск тестов (Test Runner).

Автоматизация запуска тестов:
    1. Unit-тесты (pytest tests/)
  2. Интеграционные тесты (integration_tests/_full_run.py)
  3. Сбор результатов (passed/failed/total/time)
  4. Таймауты и graceful shutdown

Текущий runner для pytest + legacy integration scripts.
"""

import logging
import subprocess
import time
import re
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger("lina.ci.runner")


# ═══════════════════════════════════════════════════════════
#  Модели данных
# ═══════════════════════════════════════════════════════════

@dataclass
class TestResult:
    """Результат одного теста.

    Attributes:
        name: Имя теста.
        passed: Прошёл ли.
        duration: Длительность (секунды).
        output: Вывод теста.
        error: Ошибка (если failed).
    """
    name: str = ""
    passed: bool = True
    duration: float = 0.0
    output: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "name": self.name,
            "passed": self.passed,
            "duration_s": round(self.duration, 3),
            "error": self.error[:200] if self.error else "",
        }


@dataclass
class TestSuiteResult:
    """Результат тестового набора.

    Attributes:
        suite_name: Имя набора.
        total: Всего тестов.
        passed: Прошло.
        failed: Провалено.
        duration: Общая длительность.
        tests: Результаты отдельных тестов.
        returncode: Код возврата.
        output: Полный вывод.
        success: Все тесты прошли.
    """
    suite_name: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    duration: float = 0.0
    tests: List[TestResult] = field(default_factory=list)
    returncode: int = 0
    output: str = ""
    success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "suite_name": self.suite_name,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "duration_s": round(self.duration, 1),
            "success": self.success,
            "returncode": self.returncode,
        }


# ═══════════════════════════════════════════════════════════
#  Парсеры результатов
# ═══════════════════════════════════════════════════════════

def _parse_pytest_output(output: str) -> Dict[str, int]:
    """Парсит краткий вывод pytest.

    Поддерживает строки вида:
      1182 passed, 13 skipped in 30.42s
      8 failed, 2278 passed, 13 skipped in 198.30s
    """
    result = {"passed": 0, "failed": 0, "total": 0}

    m_passed = re.search(r"(\d+)\s+passed", output)
    m_failed = re.search(r"(\d+)\s+failed", output)
    m_errors = re.search(r"(\d+)\s+errors?", output)

    if m_passed:
        result["passed"] = int(m_passed.group(1))
    if m_failed:
        result["failed"] += int(m_failed.group(1))
    if m_errors:
        result["failed"] += int(m_errors.group(1))

    result["total"] = result["passed"] + result["failed"]

    return result


def _parse_integration_output(output: str) -> Dict[str, int]:
    """Парсит вывод integration test runner.

    Ищет строки вида:
      PASSED: 45/45

    Args:
        output: Вывод теста.

    Returns:
        dict с passed, failed, total.
    """
    result = {"passed": 0, "failed": 0, "total": 0}

    m = re.search(r"PASSED:\s*(\d+)/(\d+)", output)
    if m:
        result["passed"] = int(m.group(1))
        result["total"] = int(m.group(2))
        result["failed"] = result["total"] - result["passed"]

    return result


# ═══════════════════════════════════════════════════════════
#  TestRunner
# ═══════════════════════════════════════════════════════════

class TestRunner:
    """Запуск тестовых наборов Lina.

    Запускает:
    - Unit tests (pytest tests/)
      - Integration tests (_full_run.py)

    Через subprocess с таймаутами.

    Attributes:
        project_root: Корень проекта.
        python_path: Путь к Python.
        unit_timeout: Таймаут для unit тестов (секунды).
        integration_timeout: Таймаут для интеграционных (секунды).
        _results: Результаты всех запусков.
    """

    def __init__(
        self,
        project_root: Optional[str] = None,
        python_path: str = "python",
        unit_timeout: int = 300,
        integration_timeout: int = 1200,
    ):
        """Инициализация.

        Args:
            project_root: Корень проекта (None = автоопределение).
            python_path: Путь к Python.
            unit_timeout: Таймаут unit-тестов.
            integration_timeout: Таймаут интеграционных.
        """
        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path(__file__).resolve().parent.parent

        self.python_path = python_path
        self.unit_timeout = unit_timeout
        self.integration_timeout = integration_timeout
        self._results: List[TestSuiteResult] = []

    # ───────────────────────────────────────────────────────
    #  Запуск unit-тестов
    # ───────────────────────────────────────────────────────

    def run_unit_tests(self) -> TestSuiteResult:
        """Запускает unit-тесты через pytest.

        Returns:
            TestSuiteResult с результатами.
        """
        result = self._run_command(
            [self.python_path, "-m", "pytest", "tests", "-q"],
            "unit",
            self.unit_timeout,
        )

        # Парсим результат
        parsed = _parse_pytest_output(result.output)
        result.passed = parsed["passed"]
        result.failed = parsed["failed"]
        result.total = parsed["total"]
        result.success = result.failed == 0 and result.returncode == 0

        self._results.append(result)
        return result

    # ───────────────────────────────────────────────────────
    #  Запуск интеграционных тестов
    # ───────────────────────────────────────────────────────

    def run_integration_tests(self) -> TestSuiteResult:
        """Запускает интеграционные тесты (_full_run.py).

        Returns:
            TestSuiteResult с результатами.
        """
        test_file = (
            self.project_root / "integration_tests" / "_full_run.py"
        )
        if not test_file.exists():
            return TestSuiteResult(
                suite_name="integration",
                output=f"File not found: {test_file}",
                success=False,
                returncode=-1,
            )

        result = self._run_test_script(
            str(test_file),
            "integration",
            self.integration_timeout,
        )

        # Парсим результат
        parsed = _parse_integration_output(result.output)
        result.passed = parsed["passed"]
        result.failed = parsed["failed"]
        result.total = parsed["total"]
        result.success = result.failed == 0 and result.returncode == 0

        self._results.append(result)
        return result

    # ───────────────────────────────────────────────────────
    #  Запуск всех тестов
    # ───────────────────────────────────────────────────────

    def run_all(self) -> List[TestSuiteResult]:
        """Запускает все тесты (unit + integration).

        Returns:
            Список TestSuiteResult.
        """
        self._results.clear()
        results = []

        logger.info("Running unit tests...")
        results.append(self.run_unit_tests())

        logger.info("Running integration tests...")
        results.append(self.run_integration_tests())

        return results

    # ───────────────────────────────────────────────────────
    #  Внутренний запуск
    # ───────────────────────────────────────────────────────

    def _run_command(
        self,
        command: List[str],
        suite_name: str,
        timeout: int,
    ) -> TestSuiteResult:
        """Запускает команду через subprocess.

        Args:
            command: Команда запуска.
            suite_name: Имя набора.
            timeout: Таймаут (секунды).

        Returns:
            TestSuiteResult.
        """
        start = time.time()

        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.project_root),
                env={**os.environ, "PYTHONPATH": str(self.project_root.parent)},
            )

            elapsed = time.time() - start
            output = proc.stdout + "\n" + proc.stderr

            return TestSuiteResult(
                suite_name=suite_name,
                duration=elapsed,
                returncode=proc.returncode,
                output=output,
                success=proc.returncode == 0,
            )

        except subprocess.TimeoutExpired:
            elapsed = time.time() - start
            return TestSuiteResult(
                suite_name=suite_name,
                duration=elapsed,
                returncode=-1,
                output=f"TIMEOUT after {timeout}s",
                success=False,
            )

        except Exception as e:
            elapsed = time.time() - start
            return TestSuiteResult(
                suite_name=suite_name,
                duration=elapsed,
                returncode=-1,
                output=f"ERROR: {e}",
                success=False,
            )

    # ───────────────────────────────────────────────────────
    #  Сводка
    # ───────────────────────────────────────────────────────

    def get_summary(self) -> Dict[str, Any]:
        """Сводка по всем запускам.

        Returns:
            Словарь со сводкой.
        """
        total_passed = sum(r.passed for r in self._results)
        total_failed = sum(r.failed for r in self._results)
        total_tests = sum(r.total for r in self._results)
        total_time = sum(r.duration for r in self._results)
        all_success = all(r.success for r in self._results)

        return {
            "total_suites": len(self._results),
            "total_tests": total_tests,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "total_duration_s": round(total_time, 1),
            "all_success": all_success,
            "suites": [r.to_dict() for r in self._results],
        }

    def format_summary(self) -> str:
        """Форматированная сводка для CLI."""
        s = self.get_summary()
        status = "✅ ALL PASSED" if s["all_success"] else "❌ FAILURES"
        lines = [
            "═══ CI Test Summary ═══",
            f"Status: {status}",
            f"Tests: {s['total_passed']}/{s['total_tests']}",
            f"Failed: {s['total_failed']}",
            f"Duration: {s['total_duration_s']:.1f}s",
            "",
        ]
        for suite in s["suites"]:
            status_icon = "✅" if suite["success"] else "❌"
            lines.append(
                f"  {status_icon} {suite['suite_name']}: "
                f"{suite['passed']}/{suite['total']} "
                f"({suite['duration_s']:.1f}s)"
            )
        return "\n".join(lines)
