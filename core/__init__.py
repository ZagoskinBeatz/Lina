# -*- coding: utf-8 -*-
"""
Lina Core Module — Unified Core Pipeline + Output Isolation.

Единый слой обработки запросов:
  - pipeline.py      — основной конвейер
  - context.py       — сборка контекста
  - model_router.py  — маршрутизация к модели
  - runtime_state.py — общее состояние runtime

Phase 11 — Output Isolation + Fish Shell Compatibility:
  - output.py        — безопасный вывод (TTY/PIPE/CI)
  - bootstrap.py     — безопасный запуск (faulthandler, signals)
  - cli.py           — разбор аргументов CLI
  - repl.py          — интерактивный REPL
  - runtime.py       — главный модуль запуска
"""

__version__ = "0.8.0"
