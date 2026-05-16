"""
KBEntry — базовая структура записи Knowledge Base.

Phase: GOVERNANCE LAYER / Knowledge Base
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class KBEntry:
    """Одна запись в Knowledge Base."""
    id: str
    domain: str
    tags: List[str] = field(default_factory=list)
    symptom: str = ""                      # Описание симптома
    symptom_ru: str = ""
    diagnosis: str = ""                    # Описание диагноза
    diagnosis_ru: str = ""
    solution_steps: List[str] = field(default_factory=list)  # Шаги решения (текстом)
    actions: List[str] = field(default_factory=list)         # action_id из ActionRegistry
    action_params: List[Dict[str, str]] = field(default_factory=list)
    confidence: float = 0.8
    success_rate: float = 0.0
    total_attempts: int = 0
    total_successes: int = 0
    risk_level: str = "low"
    requires_reboot: bool = False
    verified: bool = False                 # Проверено вручную
    source: str = "local"                  # local | user | inferred
    created: float = 0.0
    updated: float = 0.0
    fingerprints: List[str] = field(default_factory=list)  # Привязанные fingerprints
    related_entries: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.created:
            self.created = time.time()
        if not self.updated:
            self.updated = self.created

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def record_attempt(self, success: bool) -> None:
        """Записать результат попытки."""
        self.total_attempts += 1
        if success:
            self.total_successes += 1
        if self.total_attempts > 0:
            self.success_rate = self.total_successes / self.total_attempts
        self.updated = time.time()

    def summary_ru(self) -> str:
        """Краткое описание."""
        symptom = self.symptom_ru or self.symptom
        diag = self.diagnosis_ru or self.diagnosis
        return f"[{self.domain}] {symptom} → {diag} (conf={self.confidence:.0%})"


@dataclass
class KBSearchResult:
    """Результат поиска в KB."""
    entry: KBEntry
    score: float
    match_type: str = "exact"  # exact, tags, fuzzy
    source: str = "local"      # local, user

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry.id,
            "domain": self.entry.domain,
            "score": round(self.score, 3),
            "match_type": self.match_type,
            "source": self.source,
            "symptom_ru": self.entry.symptom_ru or self.entry.symptom,
            "actions": self.entry.actions,
            "confidence": self.entry.confidence,
        }
