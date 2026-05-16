# -*- coding: utf-8 -*-
"""
Lina Core — Search Pipeline (v2).

Orchestrates: QueryRewriter → Parallel Web Search → Result Ranking.

Takes a single user query and returns ranked, deduplicated SearchResults
from multiple engines searched with multiple query variants.

Design: uses existing WebSearchEngine for actual HTTP, adds
multi-query parallelism on top.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from lina.models.datatypes import SearchResult, QueryPlan
from lina.core.query_rewriter import get_query_rewriter
from lina.core.result_ranker import get_result_ranker

logger = logging.getLogger("lina.core.search_pipeline")


class SearchPipeline:
    """
    Multi-query, multi-engine search with ranking.

    Usage:
        sp = SearchPipeline()
        results = sp.search("какой процессор у realme 10")
    """

    def __init__(
        self,
        max_results: int = 15,
        parallel_workers: int = 3,
        timeout: float = 15.0,
    ):
        self._max_results = max_results
        self._workers = parallel_workers
        self._timeout = timeout

    def search(self, query: str) -> tuple[QueryPlan, List[SearchResult]]:
        """
        Execute the full search pipeline.

        Args:
            query: Raw user query.

        Returns:
            (QueryPlan, ranked_results): the plan + sorted results.
        """
        t0 = time.time()

        # Step 1: Rewrite query
        plan = get_query_rewriter().rewrite(query)
        logger.info(
            "SearchPipeline: %d queries generated from %r",
            len(plan.queries), query[:50],
        )

        # Step 2: Parallel search across query variants
        all_results: List[SearchResult] = []

        try:
            from lina.core.web_search_engine import get_web_search_engine
            engine = get_web_search_engine()
        except ImportError:
            logger.error("WebSearchEngine not available")
            return plan, []

        def _search_variant(sq: str) -> List[SearchResult]:
            """Search one query variant."""
            try:
                resp = engine.search(sq)
                if resp.success and resp.results:
                    return [
                        SearchResult(
                            title=r.title,
                            url=r.url,
                            snippet=r.snippet,
                            relevance=r.relevance,
                            source_engine=resp.source or "",
                        )
                        for r in resp.results
                    ]
            except Exception as e:
                logger.debug("Search variant %r failed: %s", sq[:40], e)
            return []

        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = {
                pool.submit(_search_variant, q): q
                for q in plan.queries
            }
            for future in as_completed(futures, timeout=self._timeout + 5):
                try:
                    results = future.result(timeout=2)
                    all_results.extend(results)
                except Exception as e:
                    logger.debug("Search future error: %s", e)

        # Step 3: Rank and deduplicate
        ranked = get_result_ranker().rank(all_results, query)

        elapsed = (time.time() - t0) * 1000
        logger.info(
            "SearchPipeline: %d raw → %d ranked in %.0f ms",
            len(all_results), len(ranked), elapsed,
        )

        return plan, ranked[:self._max_results]


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_pipeline: SearchPipeline | None = None


def get_search_pipeline() -> SearchPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = SearchPipeline()
    return _pipeline
