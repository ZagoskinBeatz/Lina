# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Parallel Page Processor.

Downloads and processes web pages concurrently with streaming results.

Architecture:
  - ThreadPoolExecutor for parallel HTTP downloads
  - Each page processed immediately on download (no waiting)
  - Pipeline: fetch → detect type → extract → chunk → filter
  - Fault-tolerant: individual page failures don't block others
  - Quality filtering: rejects bot pages, empty pages, low-density pages

Flow:
  SearchResult[] ──→ ParallelDownload ──→ ContentExtract ──→ Chunk ──→ Filter
                      ↓ (per page)        ↓ (immediate)       ↓         ↓
                   raw HTML            ExtractionResult   Passage[]  Passage[]

No LLM calls. Fully deterministic.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Callable

from lina.models.datatypes import SearchResult, Passage
from lina.web_extraction.content_extractor import (
    ContentExtractor, ExtractionResult, get_content_extractor,
)
from lina.web_extraction.semantic_chunker import (
    SemanticChunker, get_semantic_chunker,
)
from lina.web_extraction.source_trust import (
    SourceTrustScorer, get_source_trust_scorer,
)

logger = logging.getLogger("lina.web_extraction.page_processor")

# ── Constants ──
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)
_ACCEPT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
}

# Minimum viable content thresholds
_MIN_TEXT_LENGTH = 50           # chars
_MIN_WORD_COUNT = 30            # words
_MIN_SENTENCES = 2              # sentences
_MIN_CONTENT_QUALITY = 0.15     # quality score [0-1]

# Deduplication thresholds
_DEDUP_JACCARD_THRESHOLD = 0.70  # Pages with >70% text overlap are duplicates


# ═══════════════════════════════════════════════════
#  Data types
# ═══════════════════════════════════════════════════

@dataclass
class PageResult:
    """Result of processing one web page."""
    url: str
    title: str = ""
    passages: List[Passage] = field(default_factory=list)
    extraction: ExtractionResult | None = None
    domain_trust: float = 0.0
    elapsed_ms: float = 0.0
    error: str = ""

    @property
    def is_success(self) -> bool:
        return bool(self.passages) and not self.error

    @property
    def passage_count(self) -> int:
        return len(self.passages)


@dataclass
class ProcessingStats:
    """Aggregate statistics for a batch processing run."""
    pages_attempted: int = 0
    pages_succeeded: int = 0
    pages_failed: int = 0
    pages_bot_blocked: int = 0
    pages_low_quality: int = 0
    pages_duplicate: int = 0
    total_passages: int = 0
    total_elapsed_ms: float = 0.0

    def summary(self) -> str:
        return (
            f"Pages: {self.pages_succeeded}/{self.pages_attempted} OK "
            f"({self.pages_failed} fail, {self.pages_bot_blocked} bot, "
            f"{self.pages_low_quality} low-q, {self.pages_duplicate} dup) "
            f"→ {self.total_passages} passages in {self.total_elapsed_ms:.0f}ms"
        )


# ═══════════════════════════════════════════════════
#  Page Processor
# ═══════════════════════════════════════════════════

class PageProcessor:
    """
    Parallel web page downloader and processor.

    Downloads pages concurrently, processes each immediately on arrival,
    and produces ranked passages ready for the RAG pipeline.

    Each page goes through:
      1. HTTP fetch with encoding detection
      2. Content-type validation (reject non-HTML)
      3. DOM-based content extraction with density analysis
      4. Bot/error page detection
      5. Quality filtering (min words, sentences, quality score)
      6. Semantic chunking into passages
      7. Cross-page deduplication

    Usage:
        processor = PageProcessor()
        results = processor.process(search_results[:5])
        all_passages = processor.extract_passages(search_results[:5])
    """

    def __init__(
        self,
        max_pages: int = 5,
        timeout_sec: float = 12.0,
        workers: int = 3,
        max_passages_per_page: int = 40,
        min_page_quality: float = _MIN_CONTENT_QUALITY,
        extractor: ContentExtractor | None = None,
        chunker: SemanticChunker | None = None,
        trust_scorer: SourceTrustScorer | None = None,
    ):
        """
        Args:
            max_pages: Maximum pages to download per query.
            timeout_sec: HTTP request timeout per page.
            workers: ThreadPoolExecutor worker count.
            max_passages_per_page: Max chunks from one page.
            min_page_quality: Minimum content quality to accept.
            extractor: Content extractor (default: singleton).
            chunker: Semantic chunker (default: singleton).
            trust_scorer: Source trust scorer (default: singleton).
        """
        self._max_pages = max_pages
        self._timeout = timeout_sec
        self._workers = workers
        self._max_ppp = max_passages_per_page
        self._min_quality = min_page_quality
        self._extractor = extractor or get_content_extractor()
        self._chunker = chunker or get_semantic_chunker()
        self._trust = trust_scorer or get_source_trust_scorer()

    def process(
        self,
        results: List[SearchResult],
        query: str = "",
    ) -> List[PageResult]:
        """
        Download and process pages, returning per-page results.

        Args:
            results: Ranked search results (best first).
            query: Original query (for logging).

        Returns:
            List of PageResult objects (one per attempted page).
        """
        top = results[:self._max_pages]
        if not top:
            return []

        t0 = time.time()
        page_results: List[PageResult] = []
        seen_content_hashes: List[set] = []  # For dedup

        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures: Dict[Future, SearchResult] = {
                pool.submit(self._process_one, r): r
                for r in top
            }

            for future in as_completed(futures, timeout=self._timeout + 15):
                result_sr = futures[future]
                try:
                    page_result = future.result(timeout=self._timeout + 5)

                    # Deduplication check
                    if page_result.is_success:
                        content_words = self._content_fingerprint(page_result)
                        is_dup = any(
                            self._jaccard(content_words, existing) > _DEDUP_JACCARD_THRESHOLD
                            for existing in seen_content_hashes
                        )
                        if is_dup:
                            page_result.error = "duplicate_content"
                            page_result.passages = []
                            logger.debug(
                                "Duplicate content: %s", result_sr.url[:60],
                            )
                        else:
                            seen_content_hashes.append(content_words)

                    page_results.append(page_result)

                except Exception as e:
                    page_results.append(PageResult(
                        url=result_sr.url,
                        title=result_sr.title,
                        error=str(e),
                    ))

        elapsed = (time.time() - t0) * 1000
        stats = self._compute_stats(page_results, elapsed)
        logger.info("PageProcessor: %s", stats.summary())

        return page_results

    def extract_passages(
        self,
        results: List[SearchResult],
        query: str = "",
    ) -> List[Passage]:
        """
        Convenience: download, process, and return flat list of passages.

        Compatible with existing pipeline_v3 interface (drop-in replacement
        for PassageExtractor.extract).
        """
        page_results = self.process(results, query=query)

        all_passages: List[Passage] = []
        for pr in page_results:
            if pr.is_success:
                # Apply trust bonus to passage metadata
                for p in pr.passages:
                    p.source_url = pr.url
                    p.source_title = pr.title
                all_passages.extend(pr.passages)

        return all_passages

    # ═══════════════════════════════════════════════
    #  Single page processing
    # ═══════════════════════════════════════════════

    def _process_one(self, result: SearchResult) -> PageResult:
        """
        Download and process a single web page.

        Pipeline: fetch → extract → validate → chunk → return.
        """
        t0 = time.time()
        url = result.url

        # ── Step 1: Fetch HTML ──
        html = self._fetch_html(url)
        if not html:
            return PageResult(url=url, title=result.title, error="fetch_failed")

        # ── Step 2: Content-type check ──
        if self._is_non_html(html):
            return PageResult(url=url, title=result.title, error="non_html_content")

        # ── Step 3: Extract content ──
        extraction = self._extractor.extract(html)

        # ── Step 4: Bot / error page detection ──
        if extraction.is_bot_page:
            elapsed = (time.time() - t0) * 1000
            return PageResult(
                url=url, title=result.title,
                extraction=extraction,
                error="bot_protection",
                elapsed_ms=elapsed,
            )

        # ── Step 5: Quality filtering ──
        if not extraction.is_usable or extraction.content_quality < self._min_quality:
            elapsed = (time.time() - t0) * 1000
            return PageResult(
                url=url, title=extraction.title or result.title,
                extraction=extraction,
                error="low_quality",
                elapsed_ms=elapsed,
            )

        # Additional quality checks
        text = extraction.main_text
        if not self._passes_quality_check(text):
            elapsed = (time.time() - t0) * 1000
            return PageResult(
                url=url, title=extraction.title or result.title,
                extraction=extraction,
                error="quality_check_failed",
                elapsed_ms=elapsed,
            )

        # ── Step 6: Semantic chunking ──
        title = extraction.title or result.title
        passages = self._chunker.chunk_to_passages(
            text, source_url=url, source_title=title,
        )

        # Limit passages per page
        passages = passages[:self._max_ppp]

        # Also update SearchResult.content for downstream use
        result.content = text

        # ── Step 7: Domain trust ──
        domain_trust = self._trust.score_url(url)

        elapsed = (time.time() - t0) * 1000
        return PageResult(
            url=url,
            title=title,
            passages=passages,
            extraction=extraction,
            domain_trust=domain_trust,
            elapsed_ms=elapsed,
        )

    # ═══════════════════════════════════════════════
    #  HTTP layer
    # ═══════════════════════════════════════════════

    def _fetch_html(self, url: str) -> str:
        """Fetch raw HTML with encoding detection."""
        from lina.utils.http import http_get

        try:
            raw = http_get(
                url,
                timeout=int(self._timeout),
                user_agent=_USER_AGENT,
                headers=_ACCEPT_HEADERS,
                raw=True,
            )
            if not raw:
                return ""

            # Encoding detection chain
            for enc in ("utf-8", "windows-1251", "koi8-r", "latin-1"):
                try:
                    return raw.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw.decode("utf-8", errors="replace")

        except Exception as e:
            logger.debug("HTTP fetch failed %s: %s", url[:60], e)
            return ""

    # ═══════════════════════════════════════════════
    #  Validation helpers
    # ═══════════════════════════════════════════════

    def _is_non_html(self, content: str) -> bool:
        """Check if content is not HTML (e.g., PDF, JSON, image)."""
        start = content[:200].strip().lower()
        # PDF
        if start.startswith("%pdf"):
            return True
        # JSON
        if start.startswith("{") or start.startswith("["):
            # Could be JSON API response
            if '"results"' in start or '"error"' in start:
                return True
        # Binary
        if "\x00" in content[:500]:
            return True
        return False

    def _passes_quality_check(self, text: str) -> bool:
        """
        Additional quality checks beyond ExtractionResult.is_usable.

        Rejects:
          - Very short text (< MIN_WORD_COUNT words)
          - Too few sentences (< MIN_SENTENCES)
          - Low information density (mostly numbers/codes)
        """
        if not text:
            return False

        words = text.split()
        wc = len(words)
        if wc < _MIN_WORD_COUNT:
            return False

        # Sentence count
        import re
        sentences = len(re.findall(r'[.!?…]\s', text)) + 1
        if sentences < _MIN_SENTENCES:
            return False

        # Information density: ratio of "real words" (not numbers/codes)
        alpha_words = sum(1 for w in words if any(c.isalpha() for c in w))
        if wc > 0 and alpha_words / wc < 0.3:
            return False

        return True

    # ═══════════════════════════════════════════════
    #  Deduplication
    # ═══════════════════════════════════════════════

    def _content_fingerprint(self, page_result: PageResult) -> set:
        """Create a word-level fingerprint for content dedup."""
        all_text = " ".join(p.text for p in page_result.passages)
        words = set(all_text.lower().split())
        return words

    def _jaccard(self, set_a: set, set_b: set) -> float:
        """Jaccard similarity between two sets."""
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / max(union, 1)

    # ═══════════════════════════════════════════════
    #  Statistics
    # ═══════════════════════════════════════════════

    def _compute_stats(
        self, results: List[PageResult], total_ms: float,
    ) -> ProcessingStats:
        """Compute aggregate processing statistics."""
        stats = ProcessingStats(
            pages_attempted=len(results),
            total_elapsed_ms=total_ms,
        )
        for r in results:
            if r.is_success:
                stats.pages_succeeded += 1
                stats.total_passages += r.passage_count
            elif r.error == "bot_protection":
                stats.pages_bot_blocked += 1
                stats.pages_failed += 1
            elif r.error in ("low_quality", "quality_check_failed"):
                stats.pages_low_quality += 1
                stats.pages_failed += 1
            elif r.error == "duplicate_content":
                stats.pages_duplicate += 1
            else:
                stats.pages_failed += 1
        return stats


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_processor: PageProcessor | None = None


def get_page_processor() -> PageProcessor:
    global _processor
    if _processor is None:
        _processor = PageProcessor()
    return _processor
