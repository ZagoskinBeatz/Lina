# -*- coding: utf-8 -*-
"""
Lina Knowledge — Fact Verifier (v3).

v3 location: re-exports from core/fact_verifier.py and adds
v3 pipeline-aware helpers.
"""

from lina.core.fact_verifier import (
    FactVerifier,
    get_fact_verifier,
)
from lina.models.datatypes import FactSet


def verify_facts(fact_set: FactSet) -> FactSet:
    """Convenience function for v3 pipeline."""
    return get_fact_verifier().verify(fact_set)


__all__ = ["FactVerifier", "get_fact_verifier", "verify_facts"]
