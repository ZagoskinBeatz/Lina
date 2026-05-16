# -*- coding: utf-8 -*-
"""
Lina Inference — Детерминистический кэш (Inference Cache).

Расширенный кэш для inference ответов:
  1. Hash-based ключи (query + context → MD5)
  2. Thread-safe (threading.Lock)
  3. TTL с авто-очисткой
  4. Размер ограничен (LRU-подобный eviction)
  5. Статистика (hit/miss/eviction)
  6. Совместим с ResponseCache из LLMEngine

Работает поверх существующего ResponseCache — не заменяет,
а предоставляет дополнительный thread-safe слой.

Phase 10 — AI Runtime v2.
"""

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field, replace as _dc_replace
from typing import Dict, Any, Optional

logger = logging.getLogger("lina.inference.cache")


# ═══════════════════════════════════════════════════════════
#  Статистика кэша
# ═══════════════════════════════════════════════════════════

@dataclass
class CacheStats:
    """Статистика работы кэша.

    Attributes:
        hits: Попадания в кэш.
        misses: Промахи.
        evictions: Вытеснения старых записей.
        current_size: Текущий размер (записей).
        max_size: Максимальный размер.
        hit_rate: Процент попаданий.
    """
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    current_size: int = 0
    max_size: int = 200
    hit_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        total = self.hits + self.misses
        self.hit_rate = self.hits / total if total > 0 else 0.0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "current_size": self.current_size,
            "max_size": self.max_size,
            "hit_rate_pct": round(self.hit_rate * 100, 1),
            "total_lookups": total,
        }


# ═══════════════════════════════════════════════════════════
#  Запись кэша
# ═══════════════════════════════════════════════════════════

@dataclass
class CacheEntry:
    """Одна запись в кэше.

    Attributes:
        key: MD5-ключ.
        query: Исходный запрос.
        response: Ответ LLM.
        tier: Модель.
        timestamp: Время создания.
        access_count: Количество обращений.
        last_access: Время последнего обращения.
    """
    key: str
    query: str
    response: str
    tier: str = "full"
    timestamp: float = field(default_factory=time.time)
    access_count: int = 0
    last_access: float = field(default_factory=time.time)

    def is_expired(self, ttl: float) -> bool:
        """Проверяет, истекло ли время жизни.

        Args:
            ttl: Время жизни в секундах.

        Returns:
            True если запись устарела.
        """
        return (time.time() - self.timestamp) > ttl


# ═══════════════════════════════════════════════════════════
#  InferenceCache
# ═══════════════════════════════════════════════════════════

class InferenceCache:
    """Thread-safe детерминистический кэш ответов.

    Предоставляет:
      - get(query, context) → Optional[str]
      - put(query, response, context, tier)
      - clear()
      - stats → CacheStats

    Использование:
        cache = InferenceCache(max_size=200, ttl=3600)

        # Проверка кэша перед inference
        cached = cache.get("Что такое Linux?")
        if cached is not None:
            return cached

        # Сохранение после inference
        cache.put("Что такое Linux?", response, tier="full")

    Attributes:
        max_size: Максимальный размер.
        ttl: Время жизни записей (секунды).
        _store: Словарь ключ → CacheEntry.
        _lock: Мьютекс для thread-safety.
        _stats: Статистика.
    """

    def __init__(
        self,
        max_size: int = 200,
        ttl: float = 3600.0,
    ):
        """Инициализация кэша.

        Args:
            max_size: Максимальное количество записей.
            ttl: Время жизни записи (секунды).
        """
        self.max_size = max_size
        self.ttl = ttl
        self._store: Dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._stats = CacheStats(max_size=max_size)

    # ───────────────────────────────────────────────────────
    #  Ключ кэша
    # ───────────────────────────────────────────────────────

    @staticmethod
    def _make_key(query: str, context: str = "") -> str:
        """Создаёт SHA256-ключ из запроса и контекста.

        Args:
            query: Текст запроса.
            context: Контекст (RAG, runtime).

        Returns:
            SHA256-хеш (64 символа).
        """
        combined = f"{query.strip().lower()}|{context.strip()}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    # ───────────────────────────────────────────────────────
    #  Чтение
    # ───────────────────────────────────────────────────────

    def get(self, query: str, context: str = "") -> Optional[str]:
        """Ищет ответ в кэше.

        Args:
            query: Текст запроса.
            context: Контекст.

        Returns:
            Кэшированный ответ или None.
        """
        key = self._make_key(query, context)

        with self._lock:
            entry = self._store.get(key)

            if entry is None:
                self._stats.misses += 1
                return None

            # Проверяем TTL
            if entry.is_expired(self.ttl):
                del self._store[key]
                self._stats.misses += 1
                self._stats.current_size = len(self._store)
                return None

            # Hit!
            entry.access_count += 1
            entry.last_access = time.time()
            self._stats.hits += 1
            return entry.response

    # ───────────────────────────────────────────────────────
    #  Запись
    # ───────────────────────────────────────────────────────

    def put(
        self,
        query: str,
        response: str,
        context: str = "",
        tier: str = "full",
    ) -> None:
        """Сохраняет ответ в кэш.

        Args:
            query: Текст запроса.
            response: Ответ LLM.
            context: Контекст.
            tier: Модель.
        """
        key = self._make_key(query, context)

        with self._lock:
            # Eviction если превышен размер
            self._evict_if_needed()

            self._store[key] = CacheEntry(
                key=key,
                query=query,
                response=response,
                tier=tier,
            )
            self._stats.current_size = len(self._store)

    # ───────────────────────────────────────────────────────
    #  Вытеснение
    # ───────────────────────────────────────────────────────

    def _evict_if_needed(self) -> None:
        """Вытесняет старые записи если кэш переполнен.

        Стратегия: LRU (Least Recently Used).
        Вызывается под _lock.
        """
        if len(self._store) < self.max_size:
            return

        # Сначала удаляем expired
        expired_keys = [
            k for k, v in self._store.items()
            if v.is_expired(self.ttl)
        ]
        for k in expired_keys:
            del self._store[k]
            self._stats.evictions += 1

        # Если всё ещё переполнен — LRU
        while len(self._store) >= self.max_size:
            lru_key = min(
                self._store,
                key=lambda k: self._store[k].last_access,
            )
            del self._store[lru_key]
            self._stats.evictions += 1

        self._stats.current_size = len(self._store)

    # ───────────────────────────────────────────────────────
    #  Очистка
    # ───────────────────────────────────────────────────────

    def clear(self) -> None:
        """Полная очистка кэша."""
        with self._lock:
            self._store.clear()
            self._stats.current_size = 0

    def cleanup_expired(self) -> int:
        """Удаляет все просроченные записи.

        Returns:
            Количество удалённых записей.
        """
        with self._lock:
            expired = [
                k for k, v in self._store.items()
                if v.is_expired(self.ttl)
            ]
            for k in expired:
                del self._store[k]
            self._stats.current_size = len(self._store)
            self._stats.evictions += len(expired)
            return len(expired)

    # ───────────────────────────────────────────────────────
    #  Статистика
    # ───────────────────────────────────────────────────────

    @property
    def stats(self) -> CacheStats:
        """Текущая статистика кэша (возвращает копию)."""
        with self._lock:
            self._stats.current_size = len(self._store)
            return _dc_replace(self._stats)

    def get_stats_dict(self) -> Dict[str, Any]:
        """Статистика в виде словаря."""
        return self.stats.to_dict()

    def format_stats(self) -> str:
        """Форматированная статистика для CLI."""
        s = self.stats.to_dict()
        lines = [
            f"Inference Cache:",
            f"  Size: {s['current_size']}/{s['max_size']}",
            f"  Hit rate: {s['hit_rate_pct']:.1f}%",
            f"  Total lookups: {s['total_lookups']}",
            f"  Hits: {s['hits']}, Misses: {s['misses']}",
            f"  Evictions: {s['evictions']}",
        ]
        return "\n".join(lines)
