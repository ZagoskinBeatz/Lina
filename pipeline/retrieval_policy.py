# -*- coding: utf-8 -*-
"""
Lina Pipeline — Retrieval Policy.

Adaptive quality thresholds that sit between retrieval and generation.
Different query types need different standards:

  LINUX troubleshooting → strict (need 2+ facts, commands, solutions)
  GENERAL web search    → relaxed (1 fact is enough for specs, dates)
  ERROR mode            → may skip LLM entirely if KG answers directly

This module determines:
  - Minimum facts required for LLM generation
  - Minimum confidence for LLM generation
  - Whether a single-fact direct answer (no LLM) is acceptable
  - Whether re-search should be triggered

Design: deterministic, no LLM, driven by query intent + web_extraction mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("lina.pipeline.retrieval_policy")


# ═══════════════════════════════════════════════════
#  Retrieval Mode (maps from QueryMode + intent)
# ═══════════════════════════════════════════════════

class RetrievalMode(Enum):
    """How strictly to gate generation."""
    STRICT = "strict"     # Linux troubleshooting: need multiple facts
    STANDARD = "standard" # General web: 1 fact suffices
    DIRECT = "direct"     # Error KG answered: skip LLM entirely


# ═══════════════════════════════════════════════════
#  Policy Decision
# ═══════════════════════════════════════════════════

@dataclass(frozen=True)
class PolicyDecision:
    """What the retrieval policy decided for this query."""
    mode: RetrievalMode
    min_facts: int
    min_confidence: float
    allow_single_fact_passthrough: bool  # 1 fact → format without LLM
    skip_llm_generation: bool = False    # KG answered directly
    reason: str = ""


# ═══════════════════════════════════════════════════
#  Intent classification helpers
# ═══════════════════════════════════════════════════

# Intents that map to STRICT (Linux troubleshooting)
_LINUX_INTENTS = frozenset({
    "linux_error",
    "linux_solution",
    "system_command",
    "linux_troubleshooting",
    "linux_config",
    "linux_admin",
})

# Intents that are factual but can work with fewer facts
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

# Non-factual intents: always allow generation (creative, chat, etc.)
_NON_FACTUAL_INTENTS = frozenset({
    "chat",
    "greeting",
    "math",
    "translation",
    "summarization",
    "creative",
    "code",
    "explanation",
})


# ═══════════════════════════════════════════════════
#  Retrieval Policy
# ═══════════════════════════════════════════════════

class RetrievalPolicy:
    """
    Adaptive quality gate between retrieval and generation.

    Usage:
        policy = RetrievalPolicy()
        decision = policy.decide(intent="product_spec", query_mode="general")

        if facts_count < decision.min_facts:
            ...  # trigger re-search or refuse

        if decision.allow_single_fact_passthrough and facts_count == 1:
            ...  # format fact directly, skip LLM
    """

    def __init__(
        self,
        # STANDARD mode thresholds (general web)
        standard_min_facts: int = 1,
        standard_min_confidence: float = 0.15,
        # STRICT mode thresholds (Linux troubleshooting)
        strict_min_facts: int = 2,
        strict_min_confidence: float = 0.35,
    ):
        self._standard_min_facts = standard_min_facts
        self._standard_min_confidence = standard_min_confidence
        self._strict_min_facts = strict_min_facts
        self._strict_min_confidence = strict_min_confidence

    def decide(
        self,
        intent: str = "",
        query_mode: str = "general",
        answered_from_kg: bool = False,
    ) -> PolicyDecision:
        """
        Determine retrieval quality thresholds.

        Args:
            intent:           Query intent from QueryUnderstanding (e.g. "product_spec").
            query_mode:       Web extraction mode ("general", "linux", "error").
            answered_from_kg: Whether Error KG already has a direct answer.

        Returns:
            PolicyDecision with adaptive thresholds.
        """
        intent_lower = (intent or "").strip().lower()
        mode_lower = (query_mode or "general").strip().lower()

        # ── DIRECT: Error KG answered ──
        if answered_from_kg:
            return PolicyDecision(
                mode=RetrievalMode.DIRECT,
                min_facts=0,
                min_confidence=0.0,
                allow_single_fact_passthrough=True,
                skip_llm_generation=True,
                reason="error_kg_direct_answer",
            )

        # ── STRICT: Linux troubleshooting ──
        if mode_lower in ("linux", "error") or intent_lower in _LINUX_INTENTS:
            return PolicyDecision(
                mode=RetrievalMode.STRICT,
                min_facts=self._strict_min_facts,
                min_confidence=self._strict_min_confidence,
                allow_single_fact_passthrough=False,
                reason=f"linux_mode:{mode_lower}/intent:{intent_lower}",
            )

        # ── NON-FACTUAL: always pass ──
        if intent_lower in _NON_FACTUAL_INTENTS:
            return PolicyDecision(
                mode=RetrievalMode.STANDARD,
                min_facts=0,
                min_confidence=0.0,
                allow_single_fact_passthrough=True,
                reason=f"non_factual_intent:{intent_lower}",
            )

        # ── STANDARD: general factual web search ──
        return PolicyDecision(
            mode=RetrievalMode.STANDARD,
            min_facts=self._standard_min_facts,
            min_confidence=self._standard_min_confidence,
            allow_single_fact_passthrough=True,
            reason=f"standard_factual:{mode_lower}/intent:{intent_lower}",
        )

    def should_research(
        self,
        facts_count: int,
        confidence: float,
        has_hallucination_flags: bool,
        decision: PolicyDecision,
    ) -> bool:
        """
        Determine if re-search is warranted.

        Args:
            facts_count:             Number of extracted facts.
            confidence:              Answer confidence.
            has_hallucination_flags:  Whether self-check flagged hallucinations.
            decision:                PolicyDecision from decide().

        Returns:
            True if re-search should be triggered.
        """
        if decision.skip_llm_generation:
            return False  # KG answered directly, no need to research

        if has_hallucination_flags:
            return True

        if facts_count < decision.min_facts:
            return True

        if confidence < decision.min_confidence:
            return True

        return False


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_policy: RetrievalPolicy | None = None


def get_retrieval_policy() -> RetrievalPolicy:
    global _policy
    if _policy is None:
        _policy = RetrievalPolicy()
    return _policy


__all__ = [
    "RetrievalMode",
    "RetrievalPolicy",
    "PolicyDecision",
    "get_retrieval_policy",
]
