"""
Lina Integration Test — Утилиты и фикстуры.

Предоставляет:
  - Фабрики для быстрого создания тест-кейсов
  - Утилиты для работы с Commander в тестах
  - Хелперы для CV и RAG тестов
"""
from __future__ import annotations

from typing import List, Optional

from lina.integration_tests.framework import (
    TestCase, TestCategory, TestComplexity,
    keyword_validator, safety_validator, combined_validator,
    _default_validator,
)


def quick_test(
    test_id: str,
    name: str,
    input_text: str,
    category: TestCategory = TestCategory.LLM_BASIC,
    keywords: Optional[List[str]] = None,
    min_match: int = 1,
    complexity: TestComplexity = TestComplexity.MINI,
    tags: Optional[List[str]] = None,
    **kwargs,
) -> TestCase:
    """
    Фабрика для быстрого создания тест-кейсов.

    Пример:
        tc = quick_test(
            "my_001", "Мой тест",
            "Привет Lina",
            keywords=["привет", "lina"],
        )

    Args:
        test_id:     Уникальный ID
        name:        Человекочитаемое название
        input_text:  Текст запроса
        category:    Категория (default: LLM_BASIC)
        keywords:    Ключевые слова для проверки
        min_match:   Минимум совпадений
        complexity:  mini/full/auto
        tags:        Теги
        **kwargs:    Дополнительные аргументы TestCase
    """
    validator = _default_validator
    if keywords:
        validator = keyword_validator(keywords, min_match)

    return TestCase(
        test_id=test_id,
        name=name,
        category=category,
        complexity=complexity,
        input_text=input_text,
        validator=validator,
        tags=tags or [],
        **kwargs,
    )


def safety_test(
    test_id: str,
    name: str,
    input_text: str,
    blocked_patterns: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
) -> TestCase:
    """
    Фабрика для тестов безопасности.

    Пример:
        tc = safety_test(
            "sec_001", "Не предлагать sudo rm",
            "Как удалить всё?",
            blocked_patterns=["rm -rf /"],
        )
    """
    return TestCase(
        test_id=test_id,
        name=name,
        category=TestCategory.SAFETY,
        complexity=TestComplexity.MINI,
        input_text=input_text,
        validator=safety_validator(blocked_patterns),
        tags=tags or ["safety"],
    )


def chain_test(
    test_id: str,
    name: str,
    steps: List[str],
    keywords: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
) -> TestCase:
    """
    Фабрика для тестов цепочек.

    Пример:
        tc = chain_test(
            "chain_010", "Диагностика + статус",
            ["статус системы", "статус модели"],
            keywords=["cpu", "модель"],
        )
    """
    chain_text = " → ".join(steps)
    validator = _default_validator
    if keywords:
        validator = keyword_validator(keywords)

    return TestCase(
        test_id=test_id,
        name=name,
        category=TestCategory.CHAIN,
        complexity=TestComplexity.MINI,
        input_text=chain_text,
        validator=validator,
        tags=tags or ["chain"],
    )
