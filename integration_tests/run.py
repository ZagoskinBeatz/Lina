#!/usr/bin/env python3
"""
Lina v0.4.0 — Интеграционный тест-раннер.

Запускает полный набор интеграционных тестов с живой LLaMA моделью
в изолированной песочнице. Генерирует JSON-отчёт.

Использование:
  # Все тесты
  python lina/integration_tests/run.py

  # Только smoke-тесты (mini, быстрые)
  python lina/integration_tests/run.py --tags smoke

  # Только категория safety
  python lina/integration_tests/run.py --category safety

  # Dry-run (не выполнять системные действия)
  python lina/integration_tests/run.py --dry-run

  # Сохранить отчёт в файл
  python lina/integration_tests/run.py --output report.json

  # Только mini-модель
  python lina/integration_tests/run.py --tags mini

  # Тихий режим
  python lina/integration_tests/run.py --quiet

Флаги:
  --dry-run          Не выполнять действия (только генерация LLM)
  --category CAT     Фильтр по категории (llm_basic, safety, cv, ...)
  --tags TAG,TAG     Фильтр по тегам через запятую
  --output FILE      Путь к JSON-отчёту
  --quiet            Минимальный вывод
  --list             Показать все тест-кейсы без запуска
"""

from __future__ import annotations

import argparse
import sys
import os

# Добавляем корень проекта в sys.path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def parse_args() -> argparse.Namespace:
    """Аргументы командной строки."""
    parser = argparse.ArgumentParser(
        description="Lina v0.4.0 — Интеграционный тест-раннер"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Не выполнять системные действия",
    )
    parser.add_argument(
        "--category", type=str, default=None,
        help="Фильтр по категории (llm_basic, safety, cv, macro, ...)",
    )
    parser.add_argument(
        "--tags", type=str, default=None,
        help="Фильтр по тегам через запятую (smoke, mini, full, ...)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Путь к JSON-отчёту (по умолчанию: logs/integration_report.json)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Минимальный вывод",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Показать все тест-кейсы без запуска",
    )
    return parser.parse_args()


def list_tests() -> None:
    """Показывает все доступные тест-кейсы."""
    from lina.integration_tests.test_cases import collect_all_tests

    tests = collect_all_tests()
    print(f"\nДоступно тест-кейсов: {len(tests)}\n")

    current_cat = None
    for tc in tests:
        if tc.category.value != current_cat:
            current_cat = tc.category.value
            print(f"\n── {current_cat.upper()} ──")

        tags_str = ", ".join(tc.tags) if tc.tags else ""
        skip_mark = " [SKIP]" if tc.skip else ""
        print(
            f"  {tc.test_id:<18} {tc.name:<40} "
            f"[{tc.complexity.value}] {tags_str}{skip_mark}"
        )

    # Статистика по категориям
    from collections import Counter
    cat_counts = Counter(tc.category.value for tc in tests)
    print(f"\n{'─' * 60}")
    print("По категориям:")
    for cat, count in sorted(cat_counts.items()):
        print(f"  {cat:<20} {count:>3} тестов")
    print(f"{'─' * 60}")
    print(f"Всего: {len(tests)} тестов")


def main() -> int:
    """Главная функция."""
    args = parse_args()

    if args.list:
        list_tests()
        return 0

    # Импорты
    from lina.integration_tests.framework import (
        IntegrationRunner, TestCategory,
    )
    from lina.integration_tests.test_cases import collect_all_tests
    from lina.integration_tests.sandbox_env import SandboxEnvironment
    from lina.integration_tests.reporter import TestReporter

    # Фильтр по категории
    category_filter = None
    if args.category:
        try:
            category_filter = [TestCategory(args.category)]
        except ValueError:
            print(f"❌ Неизвестная категория: {args.category}")
            print(f"   Доступные: {[c.value for c in TestCategory]}")
            return 1

    # Фильтр по тегам
    tag_filter = None
    if args.tags:
        tag_filter = [t.strip() for t in args.tags.split(",")]

    # Собираем тесты
    all_tests = collect_all_tests()
    verbose = not args.quiet

    if verbose:
        print(f"\n🔬 Lina Integration Test Runner")
        print(f"   Тестов в коллекции: {len(all_tests)}")
        if args.dry_run:
            print("   Режим: DRY-RUN")
        if category_filter:
            print(f"   Категория: {args.category}")
        if tag_filter:
            print(f"   Теги: {tag_filter}")

    # Sandbox
    with SandboxEnvironment() as sandbox:
        if verbose:
            print(f"   Песочница: {sandbox.root}")

        # Раннер
        runner = IntegrationRunner(
            sandbox_env=sandbox,
            dry_run=args.dry_run,
            verbose=verbose,
        )

        # Запуск
        results = runner.run_all(
            test_cases=all_tests,
            categories=category_filter,
            tags=tag_filter,
        )

        # Отчёт
        reporter = TestReporter(results)

        # Путь к отчёту
        output_path = args.output
        if output_path is None:
            from lina.config import LOGS_DIR
            output_path = str(LOGS_DIR / "integration_report.json")

        report = reporter.generate_report(
            output_path=output_path,
            dry_run=args.dry_run,
        )

        if verbose:
            reporter.print_report(report)
            print(f"\n📄 JSON-отчёт сохранён: {output_path}")

        # Код возврата: 0 если все прошли, 1 если есть провалы
        return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
