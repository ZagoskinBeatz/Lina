"""
Knowledge Base — двухуровневая база знаний Lina.

Архитектура:
  - LocalKB:  встроенная, read-only, обновляется с релизами
  - UserKB:   пользовательская, read-write, обучается на success/fail

Формат записи:
  KBEntry {
    id, domain, tags, symptom, diagnosis, solution_steps,
    actions[], confidence, success_rate, verified, source
  }

Поиск:
  1. Точный — по fingerprint/id
  2. По тегам — Jaccard similarity
  3. Нечёткий — через FuzzyMatcher

Phase: GOVERNANCE LAYER / Knowledge Base
"""

from __future__ import annotations

from .local_kb import LocalKB, get_local_kb
from .user_kb import UserKB, get_user_kb
from .kb_entry import KBEntry, KBSearchResult

__all__ = [
    "LocalKB", "get_local_kb",
    "UserKB", "get_user_kb",
    "KBEntry", "KBSearchResult",
]
