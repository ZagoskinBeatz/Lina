# -*- coding: utf-8 -*-
"""
Lina Retrieval — Result Merger (v3).

Merges search results from multiple engines using
Reciprocal Rank Fusion (RRF).

RRF formula:
  score(d) = Σ  1 / (k + rank_i(d))
             i∈engines

Where k=60 (standard constant), rank_i is the position in engine i's results.

Pipeline:
  Dict[engine → results]  →  deduplicate  →  RRF  →  sorted List[SearchResult]
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Dict, List, Set
from urllib.parse import urlparse

from lina.models.datatypes import RetrievalResult, SearchResult

logger = logging.getLogger("lina.retrieval.result_merger")


class ResultMerger:
    """
    Merges results from multiple search engines using Reciprocal Rank Fusion.

    Usage:
        merger = ResultMerger()
        merged = merger.merge({"duckduckgo": [...], "wikipedia": [...]})
    """

    RRF_K = 60  # Standard RRF constant

    def merge(
        self,
        engine_results: Dict[str, List[SearchResult]],
        max_results: int = 20,
    ) -> RetrievalResult:
        """
        Merge and rank results from multiple engines.

        Args:
            engine_results: engine_name → list of SearchResult.
            max_results: Maximum results to return.

        Returns:
            RetrievalResult with merged, deduplicated, RRF-ranked results.
        """
        if not engine_results:
            return RetrievalResult()

        total_raw = sum(len(v) for v in engine_results.values())
        engines_used = [k for k, v in engine_results.items() if v]

        # Step 1: Normalize URLs and build RRF scores
        url_to_result: Dict[str, SearchResult] = {}
        rrf_scores: Dict[str, float] = defaultdict(float)

        for engine_name, results in engine_results.items():
            for rank, result in enumerate(results):
                norm_url = self._normalize_url(result.url)
                if not norm_url:
                    continue

                # RRF score contribution from this engine
                rrf_scores[norm_url] += 1.0 / (self.RRF_K + rank + 1)

                # Keep the result with the most information
                if norm_url not in url_to_result:
                    url_to_result[norm_url] = result
                else:
                    existing = url_to_result[norm_url]
                    # Prefer result with longer snippet
                    if len(result.snippet) > len(existing.snippet):
                        url_to_result[norm_url] = result

        # Step 2: Filter spam domains
        filtered_urls = [
            url for url in rrf_scores
            if not self._is_spam(url)
        ]

        # Step 3: Sort by RRF score
        filtered_urls.sort(key=lambda u: rrf_scores[u], reverse=True)

        # Step 4: Build final list with updated relevance
        merged: List[SearchResult] = []
        max_rrf = max(rrf_scores.values()) if rrf_scores else 1.0

        for norm_url in filtered_urls[:max_results]:
            result = url_to_result[norm_url]
            # Normalize RRF score to [0, 1]
            result.relevance = rrf_scores[norm_url] / max(max_rrf, 0.001)
            merged.append(result)

        total_deduped = len(merged)
        logger.info(
            "ResultMerger: %d raw → %d deduped (engines: %s)",
            total_raw, total_deduped, engines_used,
        )

        return RetrievalResult(
            results=merged,
            engines_used=engines_used,
            total_raw=total_raw,
            total_deduped=total_deduped,
        )

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize URL for deduplication."""
        if not url:
            return ""
        try:
            parsed = urlparse(url)
            # Remove www., trailing slashes, fragments, tracking params
            host = parsed.hostname or ""
            host = re.sub(r"^www\.", "", host)
            path = parsed.path.rstrip("/")
            return f"{host}{path}".lower()
        except Exception:
            return url.lower().strip()

    # Spam domains
    _SPAM = {
        "pinterest", "facebook", "instagram", "tiktok",
        "twitter", "x.com", "linkedin", "vk.com",
        "ok.ru", "youtube", "youtu.be",
    }

    @classmethod
    def _is_spam(cls, norm_url: str) -> bool:
        """Check if URL is from a known spam/social domain."""
        for spam in cls._SPAM:
            if spam in norm_url:
                return True
        return False


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_merger: ResultMerger | None = None

def get_result_merger() -> ResultMerger:
    global _merger
    if _merger is None:
        _merger = ResultMerger()
    return _merger
