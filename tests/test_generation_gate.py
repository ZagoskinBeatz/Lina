# -*- coding: utf-8 -*-
"""Tests for unified factual generation gate."""

from lina.models.datatypes import Fact, FactSet
from lina.pipeline.generation_gate import GenerationGate


def _fact(verified: bool = False, source_count: int = 1, confidence: float = 0.7):
    return Fact(
        subject="Realme 10",
        predicate="processor",
        object_value="Helio G99",
        sources=["https://example.com/specs"],
        source_count=source_count,
        confidence=confidence,
        verified=verified,
    )


def test_blocks_factual_without_verified_facts():
    gate = GenerationGate(min_verified_facts=1, min_confidence=0.40)
    fact_set = FactSet(subject="Realme 10", facts=[_fact(verified=False, source_count=1)], confidence=0.9)

    decision = gate.evaluate("web_search", fact_set)

    assert decision.allow_generation is False
    assert decision.reason == "verified_facts_below_threshold"


def test_blocks_factual_with_low_confidence_even_if_verified():
    gate = GenerationGate(min_verified_facts=1, min_confidence=0.40)
    fact_set = FactSet(subject="Realme 10", facts=[_fact(verified=True, source_count=2)], confidence=0.2)

    decision = gate.evaluate("web_search", fact_set)

    assert decision.allow_generation is False
    assert decision.reason == "confidence_below_threshold"


def test_allows_factual_with_verified_and_confidence():
    gate = GenerationGate(min_verified_facts=1, min_confidence=0.40)
    fact_set = FactSet(subject="Realme 10", facts=[_fact(verified=True, source_count=2)], confidence=0.8)

    decision = gate.evaluate("web_search", fact_set)

    assert decision.allow_generation is True


def test_allows_non_factual_without_fact_set():
    gate = GenerationGate()
    decision = gate.evaluate("chat", None)

    assert decision.allow_generation is True
    assert decision.reason == "non_factual_intent"


def test_counts_verified_by_source_count_fallback():
    gate = GenerationGate(min_verified_facts=1)
    fact_set = FactSet(subject="Realme 10", facts=[_fact(verified=False, source_count=2)], confidence=0.9)

    decision = gate.evaluate("web_search", fact_set)

    assert decision.allow_generation is True
