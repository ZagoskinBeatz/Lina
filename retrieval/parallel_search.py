# -*- coding: utf-8 -*-
"""
Lina Retrieval — Parallel Multi-Engine Search (v3).

Searches multiple engines in parallel:
  1. DuckDuckGo / Bing (via existing WebSearchEngine)
  2. Wikipedia API (structured knowledge)
  3. Brave Search API (if BRAVE_API_KEY set)
  4. SearXNG (if SEARXNG_URL set)

Architecture:
  ThreadPoolExecutor → concurrent searches
  Results → List[SearchResult] per engine

No merging here — that's result_merger.py's job.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from lina.models.datatypes import SearchResult
from lina.utils.http import http_get

logger = logging.getLogger("lina.retrieval.parallel_search")


class SearchEngine:
    """
    Abstract interface for a search engine.

    Each engine returns List[SearchResult] for a query.
    """

    name: str = "base"

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        raise NotImplementedError


# ═══════════════════════════════════════════════════
#  Engine: WebSearchEngine wrapper (DuckDuckGo/Bing)
# ═══════════════════════════════════════════════════

class DuckDuckGoEngine(SearchEngine):
    """Lightweight DDG search — returns only search results (no page download/LLM)."""

    name = "duckduckgo"

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        try:
            from lina.core.web_search_engine import WebSearchEngine
            engine = WebSearchEngine()
            # Call _search_ddg_bs4 directly for speed (avoids full pipeline)
            raw_results = engine._search_ddg_bs4(query)
            results = []
            for r in raw_results[:max_results]:
                results.append(SearchResult(
                    title=getattr(r, 'title', ''),
                    url=getattr(r, 'url', '') or getattr(r, 'href', ''),
                    snippet=getattr(r, 'snippet', '') or getattr(r, 'body', ''),
                    relevance=getattr(r, 'relevance', 0.5),
                    source_engine=self.name,
                ))
            return results
        except Exception as e:
            logger.error("DuckDuckGo search failed: %s", e)
            return []


# ═══════════════════════════════════════════════════
#  Engine: Ecosia (Bing-powered, works through SOCKS proxy)
# ═══════════════════════════════════════════════════

class EcosiaEngine(SearchEngine):
    """Lightweight Ecosia search — Bing-powered, reliable through SOCKS proxy."""

    name = "ecosia"

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        try:
            from lina.core.web_search_engine import WebSearchEngine
            engine = WebSearchEngine()
            raw_results = engine._search_ecosia(query)
            results = []
            for r in raw_results[:max_results]:
                results.append(SearchResult(
                    title=getattr(r, 'title', ''),
                    url=getattr(r, 'url', '') or getattr(r, 'href', ''),
                    snippet=getattr(r, 'snippet', '') or getattr(r, 'body', ''),
                    relevance=getattr(r, 'relevance', 0.5),
                    source_engine=self.name,
                ))
            return results
        except Exception as e:
            logger.error("Ecosia search failed: %s", e)
            return []


# ═══════════════════════════════════════════════════
#  Engine: Wikipedia API
# ═══════════════════════════════════════════════════

class WikipediaEngine(SearchEngine):
    """Search Wikipedia via its REST API for background knowledge."""

    name = "wikipedia"

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        try:
            # Use Wikipedia search API
            encoded = quote_plus(query)
            url = (
                f"https://en.wikipedia.org/w/api.php?"
                f"action=query&list=search&srsearch={encoded}"
                f"&srlimit={max_results}&format=json"
            )
            raw = http_get(url, timeout=8)
            if not raw:
                return []

            data = json.loads(raw)
            results = []
            for item in data.get("query", {}).get("search", []):
                title = item.get("title", "")
                snippet = _strip_html(item.get("snippet", ""))
                page_url = f"https://en.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"
                results.append(SearchResult(
                    title=title,
                    url=page_url,
                    snippet=snippet,
                    relevance=0.6,
                    source_engine=self.name,
                    domain_score=0.85,
                ))
            return results
        except Exception as e:
            logger.error("Wikipedia search failed: %s", e)
            return []


# ═══════════════════════════════════════════════════
#  Engine: Brave Search API
# ═══════════════════════════════════════════════════

class BraveEngine(SearchEngine):
    """Search via Brave Search API (requires BRAVE_API_KEY)."""

    name = "brave"

    def __init__(self):
        self._api_key = os.environ.get("BRAVE_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        if not self._api_key:
            return []
        try:
            from lina.utils.http import http_get
            encoded = quote_plus(query)
            url = f"https://api.search.brave.com/res/v1/web/search?q={encoded}&count={max_results}"
            body = http_get(
                url, timeout=10,
                headers={
                    "X-Subscription-Token": self._api_key,
                    "Accept": "application/json",
                },
            )
            if not body:
                return []

            data = json.loads(body)
            results = []
            for item in data.get("web", {}).get("results", []):
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                    relevance=0.5,
                    source_engine=self.name,
                ))
            return results
        except Exception as e:
            logger.error("Brave search failed: %s", e)
            return []


# ═══════════════════════════════════════════════════
#  Engine: SearXNG
# ═══════════════════════════════════════════════════

class SearXNGEngine(SearchEngine):
    """Search via SearXNG instance (requires SEARXNG_URL)."""

    name = "searxng"

    def __init__(self):
        self._url = os.environ.get("SEARXNG_URL", "").rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self._url)

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        if not self._url:
            return []
        try:
            encoded = quote_plus(query)
            url = f"{self._url}/search?q={encoded}&format=json&language=auto"
            raw = http_get(url, timeout=10)
            if not raw:
                return []

            data = json.loads(raw)
            results = []
            for item in data.get("results", [])[:max_results]:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    relevance=0.5,
                    source_engine=self.name,
                ))
            return results
        except Exception as e:
            logger.error("SearXNG search failed: %s", e)
            return []


# ═══════════════════════════════════════════════════
#  Parallel Search Orchestrator
# ═══════════════════════════════════════════════════

class ParallelSearch:
    """
    Runs multiple search engines in parallel.

    Usage:
        ps = ParallelSearch()
        results = ps.search(["realme 10 specs", "realme 10 processor"])
        # results: Dict[str, List[SearchResult]]  (engine_name → results)
    """

    def __init__(self, max_workers: int = 4, timeout: float = 15.0):
        self._max_workers = max_workers
        self._timeout = timeout
        self._engines: List[SearchEngine] = self._init_engines()

    def _init_engines(self) -> List[SearchEngine]:
        """Initialize available engines."""
        engines: list[SearchEngine] = []

        # Always available — Ecosia first (most reliable through SOCKS proxy)
        engines.append(EcosiaEngine())
        engines.append(DuckDuckGoEngine())
        engines.append(WikipediaEngine())

        # Conditional
        brave = BraveEngine()
        if brave.available:
            engines.append(brave)
            logger.info("Brave Search engine enabled")

        searxng = SearXNGEngine()
        if searxng.available:
            engines.append(searxng)
            logger.info("SearXNG engine enabled")

        logger.info("ParallelSearch: %d engines active", len(engines))
        return engines

    @property
    def engine_names(self) -> List[str]:
        return [e.name for e in self._engines]

    def search(
        self,
        queries: List[str],
        max_results_per_engine: int = 10,
    ) -> Dict[str, List[SearchResult]]:
        """
        Search all engines with all queries in parallel.

        Args:
            queries: List of search queries (from QueryRewriter).
            max_results_per_engine: Max results per engine.

        Returns:
            Dict: engine_name → aggregated results across all queries.
        """
        if not queries:
            return {}

        t0 = time.time()
        all_results: Dict[str, List[SearchResult]] = {e.name: [] for e in self._engines}

        # Build task list: (engine, query) pairs
        tasks = []
        for engine in self._engines:
            # Ecosia + DuckDuckGo get all queries; others get primary only
            if engine.name in ("duckduckgo", "ecosia"):
                for q in queries[:3]:  # cap at 3 to avoid rate-limiting
                    tasks.append((engine, q, max_results_per_engine))
            else:
                # Wikipedia, Brave, SearXNG: only primary query
                tasks.append((engine, queries[0], max_results_per_engine))

        # Execute in parallel
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {}
            for engine, query, max_r in tasks:
                f = pool.submit(self._safe_search, engine, query, max_r)
                futures[f] = engine.name

            for future in as_completed(futures, timeout=self._timeout):
                engine_name = futures[future]
                try:
                    results = future.result(timeout=2)
                    all_results[engine_name].extend(results)
                except Exception as e:
                    logger.warning("Engine %s failed: %s", engine_name, e)

        elapsed = (time.time() - t0) * 1000
        total = sum(len(v) for v in all_results.values())
        logger.info(
            "ParallelSearch: %d engines, %d queries → %d results in %.0f ms",
            len(self._engines), len(queries), total, elapsed,
        )
        return all_results

    @staticmethod
    def _safe_search(
        engine: SearchEngine, query: str, max_results: int,
    ) -> List[SearchResult]:
        """Safe wrapper around engine.search()."""
        try:
            return engine.search(query, max_results)
        except Exception as e:
            logger.error("Engine %s error: %s", engine.name, e)
            return []


# ── Helpers ──

def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text)


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_search: ParallelSearch | None = None

def get_parallel_search() -> ParallelSearch:
    global _search
    if _search is None:
        _search = ParallelSearch()
    return _search
