# -*- coding: utf-8 -*-
"""
Lina Pipeline — Unified factual generation gate.

Single policy authority for anti-hallucination gating:
- factual intents must not generate factual text without verified facts;
- low-confidence factual evidence must be refused;
- non-factual intents pass through.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


_FACTUAL_INTENTS = frozenset({
    "product_spec",
    "hardware",
    "price",
    "comparison",
    "benchmark",
    "review",
    "release_date",
    "version",
    "general_info",
    "web_search",
    "factual",
})


@dataclass(frozen=True)
class GateDecision:
    allow_generation: bool
    reason: str = ""
    refusal_text: str = ""


class IGenerationGate(Protocol):
    def is_factual(self, intent: str) -> bool: ...
    def evaluate(self, intent: str, fact_set: Optional[Any]) -> GateDecision: ...


class GenerationGate:
    """Unified gate for factual generation safety."""

    def __init__(
        self,
        min_verified_facts: int = 1,
        min_confidence: float = 0.40,
    ):
        self._min_verified_facts = max(1, int(min_verified_facts))
        self._min_confidence = float(min_confidence)

    def is_factual(self, intent: str) -> bool:
        return (intent or "").strip().lower() in _FACTUAL_INTENTS

    def evaluate(self, intent: str, fact_set: Optional[Any]) -> GateDecision:
        """Evaluate whether generation is allowed for the given intent/fact set."""
        if not self.is_factual(intent):
            return GateDecision(True, reason="non_factual_intent")

        if fact_set is None:
            return GateDecision(
                False,
                reason="missing_fact_set",
                refusal_text=self.refusal("missing_fact_set"),
            )

        verified_facts = self._count_verified_facts(fact_set)
        if verified_facts < self._min_verified_facts:
            return GateDecision(
                False,
                reason="verified_facts_below_threshold",
                refusal_text=self.refusal("verified_facts_below_threshold"),
            )

        confidence = float(getattr(fact_set, "confidence", 0.0) or 0.0)
        if confidence < self._min_confidence:
            return GateDecision(
                False,
                reason="confidence_below_threshold",
                refusal_text=self.refusal("confidence_below_threshold"),
            )

        return GateDecision(True, reason="factual_gate_pass")

    def refusal(self, reason: str) -> str:
        if reason == "confidence_below_threshold":
            return (
                "Недостаточно надёжных подтверждённых данных для фактического ответа. "
                "Попробуйте уточнить запрос или повторить позже."
            )
        return (
            "Не найдено верифицированных фактов для фактического ответа. "
            "Попробуйте уточнить запрос или повторить позже."
        )

    @staticmethod
    def _count_verified_facts(fact_set: Any) -> int:
        verified_count = getattr(fact_set, "verified_count", None)
        facts = getattr(fact_set, "facts", None) or []
        heuristic_count = 0
        for fact in facts:
            if bool(getattr(fact, "verified", False)):
                heuristic_count += 1
                continue
            source_count = int(getattr(fact, "source_count", 0) or 0)
            if source_count >= 2:
                heuristic_count += 1

        if isinstance(verified_count, int):
            return max(verified_count, heuristic_count)

        return heuristic_count


_gate: GenerationGate | None = None


def get_generation_gate() -> GenerationGate:
    global _gate
    if _gate is None:
        _gate = GenerationGate()
    return _gate


__all__ = [
    "GateDecision",
    "IGenerationGate",
    "GenerationGate",
    "get_generation_gate",
]
