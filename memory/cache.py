# -*- coding: utf-8 -*-
"""
Lina Memory — General-Purpose Cache (v2 Pipeline).

Thread-safe LRU cache with TTL.
Used for:
  - Caching full pipeline answers (query → PipelineAnswer)
  - Caching embedding results
  - Any key-value storage with automatic expiration

Design: pure Python, no external dependencies, O(1) get/put.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger("lina.memory.cache")


class LRUCache:
    """
    Thread-safe LRU cache with per-entry TTL.

    Usage:
        cache = LRUCache(max_size=1000, default_ttl=600)
        cache.put("key", value)
        hit = cache.get("key")  # → value or None
    """

    def __init__(
        self,
        max_size: int = 1000,
        default_ttl: float = 600.0,
    ):
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._data: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get value by key.  Returns None if missing or expired."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.is_expired:
                del self._data[key]
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._data.move_to_end(key)
            self._hits += 1
            return entry.value

    def put(self, key: str, value: Any, ttl: float | None = None) -> None:
        """Store a value.  Evicts LRU entry if at capacity."""
        t = ttl if ttl is not None else self._default_ttl
        with self._lock:
            if key in self._data:
                del self._data[key]
            self._data[key] = _CacheEntry(value=value, ttl=t)
            self._data.move_to_end(key)
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)

    def has(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            if entry.is_expired:
                del self._data[key]
                return False
            return True

    def remove(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._data)

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def stats(self) -> dict:
        return {
            "size": self.size,
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self.hit_rate, 3),
        }

    def cleanup_expired(self) -> int:
        """Remove all expired entries.  Returns count removed."""
        removed = 0
        with self._lock:
            expired_keys = [
                k for k, v in self._data.items() if v.is_expired
            ]
            for k in expired_keys:
                del self._data[k]
                removed += 1
        return removed


class _CacheEntry:
    __slots__ = ("value", "created_at", "ttl")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.created_at = time.time()
        self.ttl = ttl

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl


# ═══════════════════════════════════════════════════
#  Singleton (response cache for pipeline answers)
# ═══════════════════════════════════════════════════

_response_cache: LRUCache | None = None


def get_response_cache() -> LRUCache:
    global _response_cache
    if _response_cache is None:
        _response_cache = LRUCache(max_size=500, default_ttl=1800.0)
    return _response_cache
