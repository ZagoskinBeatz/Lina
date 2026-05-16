# -*- coding: utf-8 -*-
"""
Lina Core — Passage Extractor (v2 Pipeline).

Downloads top-ranked web pages, extracts clean text,
and splits them into passage-level chunks for embedding ranking.

Pipeline position:  SearchResults → [download] → [clean] → [split] → Passages

Design: parallelises page downloads via ThreadPoolExecutor.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from lina.models.datatypes import SearchResult, Passage
from lina.utils.html_cleaner import clean_html, extract_main_content, extract_title
from lina.utils.text_splitter import split_into_passages
from lina.processing.html_cleaner import is_bot_protection_page
from lina.utils.http import http_get

logger = logging.getLogger("lina.core.passage_extractor")

# ── Constants ──
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
    "Gecko/20100101 Firefox/120.0"
)


class PassageExtractor:
    """
    Downloads web pages and extracts passage-level text chunks.

    Usage:
        ext = PassageExtractor()
        passages = ext.extract(ranked_results[:5], query="realme 10 processor")
    """

    def __init__(
        self,
        max_pages: int = 5,
        timeout: float = 12.0,
        max_passages_per_page: int = 30,
        min_passage_words: int = 15,
        max_passage_words: int = 200,
        workers: int = 3,
    ):
        self._max_pages = max_pages
        self._timeout = timeout
        self._max_ppp = max_passages_per_page
        self._min_pw = min_passage_words
        self._max_pw = max_passage_words
        self._workers = workers

    def extract(
        self,
        results: List[SearchResult],
        query: str = "",
    ) -> List[Passage]:
        """
        Download top pages and split into passages.

        Args:
            results: Ranked search results (best first).
            query:   Original query (for logging).

        Returns:
            List of Passage objects from all downloaded pages.
        """
        top = results[:self._max_pages]
        if not top:
            return []

        t0 = time.time()
        all_passages: List[Passage] = []

        # Parallel page download
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = {
                pool.submit(self._download_and_split, r): r
                for r in top
            }
            for future in as_completed(futures, timeout=self._timeout + 10):
                try:
                    passages = future.result(timeout=self._timeout + 2)
                    all_passages.extend(passages)
                except Exception as e:
                    url = futures[future].url
                    logger.debug("Page download/split failed %s: %s",
                                 url[:60], e)

        elapsed = (time.time() - t0) * 1000
        logger.info(
            "PassageExtractor: %d pages → %d passages in %.0f ms",
            len(top), len(all_passages), elapsed,
        )
        return all_passages

    def _download_and_split(self, result: SearchResult) -> List[Passage]:
        """Download one page and split into passages."""
        html = self._fetch_html(result.url)
        if not html:
            return []

        # Extract main article content (not menus/footers)
        text = extract_main_content(html)
        if not text or len(text) < 50:
            text = clean_html(html)

        if not text or len(text) < 50:
            return []

        # Reject bot-protection / blocked pages
        if is_bot_protection_page(text):
            logger.debug("Bot-protection page detected, skipping: %s", result.url[:60])
            return []

        # Also update SearchResult.content for downstream use
        result.content = text

        # Split into passages
        raw_passages = split_into_passages(
            text,
            min_words=self._min_pw,
            max_words=self._max_pw,
        )

        title = result.title or extract_title(html)
        passages = []
        for i, p_text in enumerate(raw_passages[:self._max_ppp]):
            passages.append(Passage(
                text=p_text,
                source_url=result.url,
                source_title=title,
            ))

        return passages

    def _fetch_html(self, url: str) -> str:
        """Fetch page HTML using urllib (pure-Python, handles encoding)."""
        try:
            raw = http_get(
                url,
                timeout=int(self._timeout),
                user_agent=_USER_AGENT,
                raw=True,
            )
            if not raw:
                return ""

            # Try UTF-8 first, then fallback encodings
            for enc in ("utf-8", "windows-1251", "koi8-r", "latin-1"):
                try:
                    return raw.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw.decode("utf-8", errors="replace")

        except Exception as e:
            logger.debug("HTTP fetch failed for %s: %s", url[:60], e)
            return ""


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_extractor: PassageExtractor | None = None


def get_passage_extractor() -> PassageExtractor:
    global _extractor
    if _extractor is None:
        _extractor = PassageExtractor()
    return _extractor
