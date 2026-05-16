# -*- coding: utf-8 -*-
"""
Lina CI — Continuous Integration & Testing.

Подмодули:
  - runner   : Запуск тестов и сбор результатов
  - reporter : Генерация отчётов (JSON, CLI)

Phase 10 — AI Runtime v2.
"""

__version__ = "0.8.0"

from lina.ci.runner import TestRunner, TestSuiteResult
from lina.ci.reporter import CIReporter

__all__ = [
    "TestRunner", "TestSuiteResult", "CIReporter",
]
