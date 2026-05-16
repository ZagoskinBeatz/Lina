# -*- coding: utf-8 -*-
"""
Lina Knowledge — Fact Store (v3).

v3 location: re-exports from memory/fact_store.py.
In v3, the fact store logically belongs to the knowledge layer.
"""

from lina.memory.fact_store import (
    FactStore,
    get_fact_store,
)

__all__ = ["FactStore", "get_fact_store"]
