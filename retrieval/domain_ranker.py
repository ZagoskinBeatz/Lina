# -*- coding: utf-8 -*-
"""
Lina Retrieval — Domain Ranker (v3).

Re-ranks search results by:
  1. Domain reputation score (0–1)
  2. Keyword match density
  3. Freshness (year mentions)
  4. Source diversity bonus

This is applied AFTER result merging, before passage extraction.
"""

from __future__ import annotations

import re
import logging
import time
from typing import Dict, List, Set
from urllib.parse import urlparse

from lina.models.datatypes import SearchResult

logger = logging.getLogger("lina.retrieval.domain_ranker")


# ═══════════════════════════════════════════════════
#  Domain Reputation Database
# ═══════════════════════════════════════════════════

_DOMAIN_SCORES: Dict[str, float] = {
    # Tech — high quality
    "gsmarena.com": 0.95,
    "notebookcheck.net": 0.93,
    "anandtech.com": 0.92,
    "techpowerup.com": 0.90,
    "phonearena.com": 0.88,
    "kimovil.com": 0.87,
    "nanoreview.net": 0.85,
    "devicespecifications.com": 0.83,
    "versus.com": 0.82,
    "91mobiles.com": 0.80,

    # General knowledge
    "en.wikipedia.org": 0.88,
    "ru.wikipedia.org": 0.88,
    "stackoverflow.com": 0.85,
    "arxiv.org": 0.90,
    "docs.python.org": 0.90,

    # Tech media
    "theverge.com": 0.78,
    "arstechnica.com": 0.82,
    "tomshardware.com": 0.80,
    "techradar.com": 0.75,
    "tomsguide.com": 0.74,
    "pcmag.com": 0.75,
    "cnet.com": 0.72,
    "wired.com": 0.73,

    # RU tech
    "4pda.to": 0.75,
    "4pda.ru": 0.75,
    "ixbt.com": 0.73,
    "3dnews.ru": 0.72,
    "overclockers.ru": 0.68,
    "habr.com": 0.78,

    # Community
    "reddit.com": 0.60,
    "xda-developers.com": 0.70,

    # E-commerce (lower for specs)
    "amazon.com": 0.40,
    "aliexpress.com": 0.25,
    "ozon.ru": 0.35,
    "wildberries.ru": 0.30,
    "dns-shop.ru": 0.50,
    "citilink.ru": 0.50,
    "mvideo.ru": 0.45,
}


class DomainRanker:
    """
    Re-ranks results using multi-signal scoring.

    Score formula:
      final = 0.35 × domain_rep + 0.30 × keyword_match
            + 0.15 × freshness + 0.10 × diversity + 0.10 × position_bonus
    """

    W_DOMAIN = 0.35
    W_KEYWORD = 0.30
    W_FRESH = 0.15
    W_DIVERSITY = 0.10
    W_POSITION = 0.10

    def rank(
        self,
        results: List[SearchResult],
        query: str,
        max_results: int = 15,
    ) -> List[SearchResult]:
        """
        Re-rank results by multi-signal score.

        Updates each result's .relevance and .domain_score, returns sorted list.
        """
        if not results:
            return []

        query_terms = set(re.findall(r"\w{2,}", query.lower()))
        seen_domains: Set[str] = set()

        for i, r in enumerate(results):
            domain = self._extract_domain(r.url)

            # Signal 1: Domain reputation
            dom_score = _DOMAIN_SCORES.get(domain, 0.40)
            r.domain_score = dom_score

            # Signal 2: Keyword density in title + snippet
            text = f"{r.title} {r.snippet}".lower()
            text_terms = set(re.findall(r"\w{2,}", text))
            overlap = len(query_terms & text_terms)
            kw_score = min(overlap / max(len(query_terms), 1), 1.0)

            # Signal 3: Freshness
            fresh_score = self._freshness_score(text)

            # Signal 4: Diversity (new domain = bonus)
            div_score = 1.0 if domain not in seen_domains else 0.3
            seen_domains.add(domain)

            # Signal 5: Position bonus (early results slightly favored)
            pos_score = max(0, 1.0 - i * 0.05)

            # Combined score
            score = (
                self.W_DOMAIN * dom_score
                + self.W_KEYWORD * kw_score
                + self.W_FRESH * fresh_score
                + self.W_DIVERSITY * div_score
                + self.W_POSITION * pos_score
            )
            r.relevance = round(score, 4)

        # Sort by score descending
        results.sort(key=lambda r: r.relevance, reverse=True)

        logger.info(
            "DomainRanker: %d results ranked (top=%.3f, bottom=%.3f)",
            len(results[:max_results]),
            results[0].relevance if results else 0,
            results[min(max_results - 1, len(results) - 1)].relevance if results else 0,
        )
        return results[:max_results]

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            host = urlparse(url).hostname or ""
            return re.sub(r"^www\.", "", host).lower()
        except Exception:
            return ""

    @staticmethod
    def _freshness_score(text: str) -> float:
        """Score based on year mentions in text."""
        current_year = 2026
        years = re.findall(r"20[12]\d", text)
        if not years:
            return 0.4
        newest = max(int(y) for y in years)
        age = current_year - newest
        if age <= 0:
            return 1.0
        if age == 1:
            return 0.8
        if age == 2:
            return 0.5
        return 0.2


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_ranker: DomainRanker | None = None

def get_domain_ranker() -> DomainRanker:
    global _ranker
    if _ranker is None:
        _ranker = DomainRanker()
    return _ranker
