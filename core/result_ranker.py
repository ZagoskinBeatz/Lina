# -*- coding: utf-8 -*-
"""
Lina Core — Result Ranker (v2 Pipeline).

Scores and re-ranks web search results using a multi-signal formula:
  1. Keyword match (query terms present in title/snippet)
  2. Domain reputation (gsmarena > random blog)
  3. Content freshness (newer articles score higher for product queries)
  4. Source diversity bonus (multiple domains > one domain repeated)

Design: stateless scorer, no network calls, deterministic.
"""

from __future__ import annotations

import re
import time
import logging
from typing import List, Dict, Set
from collections import Counter

from lina.models.datatypes import SearchResult

logger = logging.getLogger("lina.core.result_ranker")


# ═══════════════════════════════════════════════════
#  Domain Reputation Scores
# ═══════════════════════════════════════════════════

_DOMAIN_SCORES: Dict[str, float] = {
    # Tech specs — gold standard
    "gsmarena.com": 0.95,
    "notebookcheck.net": 0.90,
    "kimovil.com": 0.85,
    "nanoreview.net": 0.85,
    "versus.com": 0.80,
    "devicespecifications.com": 0.80,
    "phonearena.com": 0.80,
    "tom's hardware": 0.80,
    "anandtech.com": 0.80,
    "techpowerup.com": 0.80,
    # Encyclopedia / wiki
    "wikipedia.org": 0.85,
    "en.wikipedia.org": 0.85,
    "ru.wikipedia.org": 0.85,
    # News / reviews
    "theverge.com": 0.75,
    "arstechnica.com": 0.75,
    "ixbt.com": 0.75,
    "4pda.to": 0.70,
    "habr.com": 0.70,
    "3dnews.ru": 0.70,
    # General OK
    "reddit.com": 0.55,
    "quora.com": 0.50,
    "youtube.com": 0.40,
    # Low quality
    "aliexpress.com": 0.20,
    "amazon.com": 0.30,
    "ebay.com": 0.20,
}

# Patterns for low-quality / spam domains
_SPAM_PATTERNS = re.compile(
    r"(pinterest\.|facebook\.|instagram\.|tiktok\.|twitter\.|x\.com"
    r"|linkedin\.|play\.google\.|apps\.apple\.)",
    re.I,
)

# Year pattern for freshness detection
_YEAR_RE = re.compile(r"\b(20[12]\d)\b")


class ResultRanker:
    """
    Multi-signal search result ranker.

    Ranking formula:
      score = 0.40 × keyword_match
            + 0.30 × domain_reputation
            + 0.15 × freshness
            + 0.15 × diversity_bonus
    """

    W_KEYWORD = 0.40
    W_DOMAIN = 0.30
    W_FRESH = 0.15
    W_DIVERSITY = 0.15

    def rank(
        self,
        results: List[SearchResult],
        query: str,
    ) -> List[SearchResult]:
        """
        Score and sort results by composite relevance.

        Args:
            results:  Raw search results (may have duplicates).
            query:    Original user query.

        Returns:
            Sorted list (best first), duplicates removed.
        """
        if not results:
            return []

        # Deduplicate by URL
        seen_urls: Set[str] = set()
        unique: List[SearchResult] = []
        for r in results:
            norm = r.url.rstrip("/").lower()
            if norm not in seen_urls and not _SPAM_PATTERNS.search(r.url):
                seen_urls.add(norm)
                unique.append(r)

        query_terms = self._tokenize(query)
        domain_counts: Counter = Counter()

        # Score each result
        for r in unique:
            kw_score = self._keyword_score(r, query_terms)
            dom_score = self._domain_score(r)
            fresh_score = self._freshness_score(r)

            domain = self._extract_domain(r.url)
            domain_counts[domain] += 1

            r.domain_score = dom_score
            r.relevance = (
                self.W_KEYWORD * kw_score
                + self.W_DOMAIN * dom_score
                + self.W_FRESH * fresh_score
            )

        # Diversity bonus: penalise repeated domains
        for r in unique:
            domain = self._extract_domain(r.url)
            count = domain_counts[domain]
            diversity = 1.0 if count == 1 else max(0.3, 1.0 / count)
            r.relevance += self.W_DIVERSITY * diversity

        # Sort descending
        unique.sort(key=lambda x: x.relevance, reverse=True)

        return unique

    # ── Scorers ──

    def _keyword_score(self, result: SearchResult, query_terms: Set[str]) -> float:
        """Fraction of query terms found in title + snippet."""
        if not query_terms:
            return 0.5

        text = (result.title + " " + result.snippet).lower()
        found = sum(1 for t in query_terms if t in text)
        return found / len(query_terms)

    def _domain_score(self, result: SearchResult) -> float:
        """Reputation score of the domain."""
        domain = self._extract_domain(result.url)
        for known, score in _DOMAIN_SCORES.items():
            if known in domain:
                return score
        # Unknown domain: default moderate score
        return 0.45

    def _freshness_score(self, result: SearchResult) -> float:
        """Bonus for recent content (based on year mentions in snippet)."""
        text = result.title + " " + result.snippet
        years = _YEAR_RE.findall(text)
        if not years:
            return 0.4  # Unknown age — neutral

        latest = max(int(y) for y in years)
        current_year = 2026
        age = current_year - latest
        if age <= 0:
            return 1.0
        if age == 1:
            return 0.8
        if age <= 3:
            return 0.5
        return 0.2

    # ── Helpers ──

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        """Tokenize query into meaningful terms (lowercase, ≥2 chars)."""
        words = re.findall(r'\w{2,}', text.lower())
        # Remove common stop words
        stops = {"что", "как", "где", "для", "это", "the", "and", "for", "are"}
        return {w for w in words if w not in stops}

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract domain from URL."""
        m = re.search(r'https?://(?:www\.)?([^/]+)', url)
        return m.group(1).lower() if m else url.lower()


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_ranker: ResultRanker | None = None


def get_result_ranker() -> ResultRanker:
    global _ranker
    if _ranker is None:
        _ranker = ResultRanker()
    return _ranker
