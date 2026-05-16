# -*- coding: utf-8 -*-
"""
Lina Memory — Fact Store (v2 Pipeline).

Persistent fact cache backed by a JSON file.  Stores verified facts
with TTL so that recently-fetched facts can be reused without
re-searching.

Features:
  - Lookup facts by entity name.
  - Auto-expire stale facts (configurable TTL).
  - Thread-safe read/write.
  - Periodic auto-save.

File format: JSON dict keyed by normalised entity name.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from lina.models.datatypes import Fact

logger = logging.getLogger("lina.memory.fact_store")


class FactStore:
    """
    Persistent fact cache with TTL.

    Storage format (on disk):
    {
      "realme 10": {
        "updated_at": 1718000000,
        "facts": [
          {
            "subject": "Realme 10",
            "predicate": "processor",
            "object_value": "MediaTek Helio G99",
            "sources": ["https://..."],
            "source_count": 2,
            "confidence": 0.85,
            "verified": true
          },
          ...
        ]
      },
      ...
    }
    """

    def __init__(
        self,
        cache_dir: str | Path = "",
        ttl_seconds: int = 3600,
        max_entities: int = 200,
    ):
        if not cache_dir:
            cache_dir = Path(__file__).parent.parent / "cache"
        self._path = Path(cache_dir) / "fact_store.json"
        self._ttl = ttl_seconds
        self._max_entities = max_entities
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = {}
        self._dirty = False
        self._load()

    # ── Public API ──

    def get(self, entity: str) -> List[Fact]:
        """
        Get cached facts for an entity.

        Returns empty list if not found, expired, or all facts are garbage.
        """
        key = self._norm(entity)
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return []
            if time.time() - entry.get("updated_at", 0) > self._ttl:
                del self._data[key]
                self._dirty = True
                return []
            facts = self._deserialize_facts(entry.get("facts", []))
            # Filter out garbage that may have been cached before validation was added
            clean = [f for f in facts if self._is_valid_fact(f)]
            if len(clean) < len(facts):
                logger.debug("FactStore: filtered %d garbage facts for '%s'",
                             len(facts) - len(clean), entity)
                if not clean:
                    # All garbage — remove entry
                    del self._data[key]
                    self._dirty = True
                    return []
            return clean

    def put(self, entity: str, facts: List[Fact]) -> None:
        """
        Cache facts for an entity.

        Filters out bot-protection / garbage facts before storing.
        """
        clean = [f for f in facts if self._is_valid_fact(f)]
        if not clean:
            logger.debug("FactStore: all facts for '%s' rejected as garbage", entity)
            return
        key = self._norm(entity)
        with self._lock:
            self._data[key] = {
                "updated_at": time.time(),
                "facts": self._serialize_facts(clean),
            }
            self._dirty = True
            self._evict_if_needed()

    @staticmethod
    def _is_valid_fact(f: Fact) -> bool:
        """Reject facts that come from bot-protection / error pages."""
        pred = (f.predicate or "").lower().strip()
        val = (f.object_value or "").lower().strip()
        # Bot-protection markers
        _GARBAGE_PREDICATES = {
            "ray id", "cloudflare ray id", "your ip", "ip address",
            "cloudflare", "captcha", "page not found", "error code",
            "access denied", "403 forbidden", "404 not found",
            "security check", "ddos protection", "click to reveal",
        }
        if pred in _GARBAGE_PREDICATES:
            return False
        _GARBAGE_VALUES = {
            "cloudflare", "click to reveal", "access denied",
            "please verify", "just a moment", "page not found",
        }
        if val in _GARBAGE_VALUES:
            return False
        # Cloudflare Ray ID hex pattern
        if "ray" in pred and re.match(r'^[0-9a-f]{10,}$', val):
            return False
        return True

    def has(self, entity: str) -> bool:
        """Check if entity has non-expired cached facts."""
        key = self._norm(entity)
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            return time.time() - entry.get("updated_at", 0) < self._ttl

    def remove(self, entity: str) -> None:
        """Remove entity from cache."""
        key = self._norm(entity)
        with self._lock:
            self._data.pop(key, None)
            self._dirty = True

    def clear(self) -> None:
        """Clear entire cache."""
        with self._lock:
            self._data.clear()
            self._dirty = True

    def save(self) -> None:
        """Save to disk if dirty."""
        with self._lock:
            if not self._dirty:
                return
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                self._dirty = False
                logger.debug("FactStore saved (%d entities)", len(self._data))
            except Exception as e:
                logger.error("FactStore save failed: %s", e)

    @property
    def entity_count(self) -> int:
        with self._lock:
            return len(self._data)

    # ── Internal ──

    def _load(self) -> None:
        """Load from disk."""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info("FactStore loaded (%d entities) from %s",
                           len(self._data), self._path)
            except Exception as e:
                logger.error("FactStore load failed: %s", e)
                self._data = {}
        else:
            self._data = {}

    def _evict_if_needed(self) -> None:
        """Remove oldest entries if over max_entities."""
        if len(self._data) <= self._max_entities:
            return
        # Sort by updated_at, remove oldest
        items = sorted(
            self._data.items(),
            key=lambda kv: kv[1].get("updated_at", 0),
        )
        remove_count = len(self._data) - self._max_entities
        for key, _ in items[:remove_count]:
            del self._data[key]
        logger.info("FactStore evicted %d old entries", remove_count)

    @staticmethod
    def _norm(entity: str) -> str:
        return entity.lower().strip()

    @staticmethod
    def _serialize_facts(facts: List[Fact]) -> List[dict]:
        return [
            {
                "subject": f.subject,
                "predicate": f.predicate,
                "object_value": f.object_value,
                "sources": f.sources,
                "source_count": f.source_count,
                "confidence": f.confidence,
                "verified": f.verified,
            }
            for f in facts
        ]

    @staticmethod
    def _deserialize_facts(entries: List[dict]) -> List[Fact]:
        facts = []
        for e in entries:
            try:
                facts.append(Fact(
                    subject=e.get("subject", ""),
                    predicate=e.get("predicate", ""),
                    object_value=e.get("object_value", ""),
                    sources=e.get("sources", []),
                    source_count=e.get("source_count", 1),
                    confidence=e.get("confidence", 0.5),
                    verified=e.get("verified", False),
                ))
            except Exception:
                continue
        return facts


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_store: FactStore | None = None


def get_fact_store() -> FactStore:
    global _store
    if _store is None:
        _store = FactStore()
    return _store
