"""
FuzzyMatcher — нечёткий поиск сигнатур и стратегий.

Алгоритмы:
  1. Jaccard similarity (по тегам)
  2. Levenshtein distance (по тексту ошибки)
  3. N-gram similarity (по нормализованному тексту)
  4. Composite score = weighted combination

Используется когда точное совпадение в KB не найдено.

Phase: GOVERNANCE LAYER / Module 6
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ─── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class FuzzyMatch:
    """Результат нечёткого совпадения."""
    target_id: str
    score: float
    jaccard: float = 0.0
    levenshtein: float = 0.0
    ngram: float = 0.0
    tag_overlap: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "score": round(self.score, 3),
            "jaccard": round(self.jaccard, 3),
            "levenshtein": round(self.levenshtein, 3),
            "ngram": round(self.ngram, 3),
            "tag_overlap": self.tag_overlap,
        }


# ─── Pure functions ──────────────────────────────────────────────────────────

def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard similarity coefficient: |A∩B| / |A∪B|."""
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def levenshtein_distance(s1: str, s2: str) -> int:
    """Levenshtein edit distance."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if not s2:
        return len(s1)

    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            insert = prev[j + 1] + 1
            delete = curr[j] + 1
            replace = prev[j] + (0 if c1 == c2 else 1)
            curr.append(min(insert, delete, replace))
        prev = curr
    return prev[-1]


def levenshtein_similarity(s1: str, s2: str) -> float:
    """Нормализованное Levenshtein сходство (0..1)."""
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    dist = levenshtein_distance(s1, s2)
    return 1.0 - (dist / max_len)


def ngram_similarity(s1: str, s2: str, n: int = 3) -> float:
    """Сходство по N-граммам."""
    if len(s1) < n or len(s2) < n:
        return 0.0
    ngrams1 = set(s1[i:i+n] for i in range(len(s1) - n + 1))
    ngrams2 = set(s2[i:i+n] for i in range(len(s2) - n + 1))
    return jaccard_similarity(ngrams1, ngrams2)


def normalize_for_comparison(text: str) -> str:
    """Нормализовать текст для сравнения."""
    text = text.lower()
    text = re.sub(r"\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}", "", text)
    text = re.sub(r"\b\d+\.\d+\.\d+\.\d+\b", "", text)
    text = re.sub(r"\b[0-9a-f]{8,}\b", "", text)
    text = re.sub(r"[^a-z\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ─── FuzzyMatcher ────────────────────────────────────────────────────────────

class FuzzyMatcher:
    """
    Нечёткий поиск по тегам и тексту.

    Пример:
        matcher = get_fuzzy_matcher()
        matcher.add_entry("net_dns_fix", tags=["dns", "network"], text="DNS resolution failed")
        results = matcher.search(tags=["dns", "timeout"], text="name resolution failed")
    """

    def __init__(self, *,
                 jaccard_weight: float = 0.4,
                 levenshtein_weight: float = 0.3,
                 ngram_weight: float = 0.3,
                 threshold: float = 0.25) -> None:
        self._entries: Dict[str, _Entry] = {}
        self._w_jaccard = jaccard_weight
        self._w_levenshtein = levenshtein_weight
        self._w_ngram = ngram_weight
        self._threshold = threshold

    # ── Index ────────────────────────────────────────────

    def add_entry(self, entry_id: str, *,
                  tags: Optional[List[str]] = None,
                  text: str = "",
                  domain: str = "") -> None:
        """Добавить запись в индекс."""
        self._entries[entry_id] = _Entry(
            id=entry_id,
            tags=set(tags or []),
            text=normalize_for_comparison(text),
            domain=domain,
        )

    def remove_entry(self, entry_id: str) -> bool:
        """Удалить запись."""
        if entry_id in self._entries:
            del self._entries[entry_id]
            return True
        return False

    def clear(self) -> None:
        """Очистить индекс."""
        self._entries.clear()

    # ── Search ───────────────────────────────────────────

    def search(self, *,
               tags: Optional[List[str]] = None,
               text: str = "",
               domain: str = "",
               limit: int = 10,
               threshold: Optional[float] = None) -> List[FuzzyMatch]:
        """
        Нечёткий поиск.

        Args:
            tags: теги для Jaccard comparison
            text: текст для Levenshtein + N-gram
            domain: фильтр по домену (если задан)
            limit: максимум результатов
            threshold: порог (ниже — отбрасывать)
        """
        thresh = threshold if threshold is not None else self._threshold
        query_tags = set(tags or [])
        query_text = normalize_for_comparison(text)

        results: List[FuzzyMatch] = []

        for entry in self._entries.values():
            # Domain filter
            if domain and entry.domain and entry.domain != domain:
                continue

            # Jaccard (tags)
            j_score = jaccard_similarity(query_tags, entry.tags) if query_tags else 0.0

            # Levenshtein (text)
            l_score = 0.0
            if query_text and entry.text:
                # For very long text, truncate to reasonable length
                t1 = query_text[:200]
                t2 = entry.text[:200]
                l_score = levenshtein_similarity(t1, t2)

            # N-gram (text)
            n_score = 0.0
            if query_text and entry.text:
                n_score = ngram_similarity(query_text[:300], entry.text[:300])

            # Composite — normalize weights for available signals
            w_j = self._w_jaccard if query_tags else 0.0
            w_l = self._w_levenshtein if (query_text and entry.text) else 0.0
            w_n = self._w_ngram if (query_text and entry.text) else 0.0
            total_w = w_j + w_l + w_n
            if total_w == 0:
                continue
            score = (
                j_score * w_j
                + l_score * w_l
                + n_score * w_n
            ) / total_w

            if score >= thresh:
                overlap = sorted(query_tags & entry.tags)
                results.append(FuzzyMatch(
                    target_id=entry.id,
                    score=score,
                    jaccard=j_score,
                    levenshtein=l_score,
                    ngram=n_score,
                    tag_overlap=overlap,
                ))

        results.sort(key=lambda m: -m.score)
        return results[:limit]

    def best_match(self, *,
                   tags: Optional[List[str]] = None,
                   text: str = "",
                   domain: str = "") -> Optional[FuzzyMatch]:
        """Лучшее совпадение (или None)."""
        results = self.search(tags=tags, text=text, domain=domain, limit=1)
        return results[0] if results else None

    # ── Stats ────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Статистика."""
        domains: Dict[str, int] = {}
        for e in self._entries.values():
            domains[e.domain] = domains.get(e.domain, 0) + 1
        return {
            "total_entries": len(self._entries),
            "domains": domains,
            "weights": {
                "jaccard": self._w_jaccard,
                "levenshtein": self._w_levenshtein,
                "ngram": self._w_ngram,
            },
            "threshold": self._threshold,
        }


# ─── Internal ────────────────────────────────────────────────────────────────

@dataclass
class _Entry:
    """Внутренний элемент индекса."""
    id: str
    tags: Set[str] = field(default_factory=set)
    text: str = ""
    domain: str = ""


# ─── Singleton ─────────────────────────────────────────────────────────────────

_matcher: Optional[FuzzyMatcher] = None

def get_fuzzy_matcher() -> FuzzyMatcher:
    """Получить единственный экземпляр FuzzyMatcher."""
    global _matcher
    if _matcher is None:
        _matcher = FuzzyMatcher()
    return _matcher
