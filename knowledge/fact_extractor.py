# -*- coding: utf-8 -*-
"""
Lina Knowledge — Fact Extractor (v3).

v3 location: re-exports from core/fact_extractor.py and adds
v3 pipeline-aware helpers.
"""

from lina.core.fact_extractor import (
    FactExtractor,
    get_fact_extractor,
)
from lina.models.datatypes import Fact, Passage

from typing import List


def extract_facts(passages: List[Passage], subject: str = "") -> List[Fact]:
    """Convenience function for v3 pipeline."""
    return get_fact_extractor().extract_from_passages(passages, subject=subject)


__all__ = ["FactExtractor", "get_fact_extractor", "extract_facts"]
