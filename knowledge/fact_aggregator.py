# -*- coding: utf-8 -*-
"""
Lina Knowledge — Fact Aggregator (v3).

v3 location: re-exports from core/fact_aggregator.py and adds
v3 pipeline-aware helpers.
"""

from lina.core.fact_aggregator import (
    FactAggregator,
    get_fact_aggregator,
)
from lina.models.datatypes import Fact, FactSet

from typing import List


def aggregate_facts(facts: List[Fact], subject: str = "") -> FactSet:
    """Convenience function for v3 pipeline."""
    return get_fact_aggregator().aggregate(facts, subject=subject)


__all__ = ["FactAggregator", "get_fact_aggregator", "aggregate_facts"]
