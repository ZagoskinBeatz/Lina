# -*- coding: utf-8 -*-
"""
Lina Planning Module — Многошаговое планирование задач.

Поддерживает создание, выполнение и оценку планов:
  - planner.py   — создание планов (LLM / шаблоны)
  - executor.py  — выполнение шагов
  - evaluator.py — оценка результатов и перепланирование
  - state.py     — состояние плана (state machine)

Phase 9 — Controlled Autonomous Runtime.
"""

__version__ = "0.8.0"
