# -*- coding: utf-8 -*-
"""
Lina Safety Module — Self-Evaluation Safety Layer.

Модуль безопасности для проверки действий перед выполнением.
Включает:
  - models.py  — модели данных (SafetyVerdict, RiskLevel, паттерны)
  - validator.py — валидатор безопасности (паттерны + LLM)
  - policy.py — движок политик (блокировка по правилам)

Phase 9 — Controlled Autonomous Runtime.
"""

__version__ = "0.8.0"
