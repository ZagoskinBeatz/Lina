# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Web Extraction Pipeline.

Top-level orchestrator that wires all web extraction stages into a single
deterministic pipeline:

  SearchResults → PageProcessor → HybridRanker → SourceTrust → Top-K Passages

This is the public API for the web extraction subsystem. The RAG pipeline
(pipeline_v3) delegates all web content processing to this module.

Pipeline stages:
  1. Parallel page download + content extraction + chunking (PageProcessor)
  2. Passage quality filtering (min length, sentence count, density)
  3. Hybrid two-stage ranking: BM25 recall + embedding precision (HybridRanker)
  4. Source trust scoring and passage bonus (SourceTrustScorer)
  5. Top-K selection with diversity enforcement
  6. Context assembly for RAG (formatted text with provenance)

Design:
  - LLM is NEVER called — this pipeline produces text context for LLM
  - Every stage is independently testable
  - Graceful degradation at every level (no hard dependencies)
  - Compatible with existing pipeline_v3 Passage/SearchResult types

Architecture diagram:
  ┌────────────────────────────────────────────────────────────────┐
  │                   WebExtractionPipeline                        │
  │                                                                │
  │  SearchResult[]                                                │
  │       │                                                        │
  │       ▼                                                        │
  │  ┌──────────────┐  ThreadPoolExecutor (3 workers)             │
  │  │ PageProcessor │──→ fetch HTML ──→ extract content           │
  │  │  (parallel)   │──→ detect bots ──→ quality filter           │
  │  │               │──→ semantic chunk ──→ dedup                 │
  │  └──────┬───────┘                                             │
  │         │ Passage[]                                            │
  │         ▼                                                      │
  │  ┌──────────────┐                                             │
  │  │ HybridRanker │──→ BM25 candidate selection (Stage 1)       │
  │  │  (two-stage) │──→ Embedding re-ranking (Stage 2)           │
  │  │              │──→ Score fusion (α×BM25 + (1-α)×embed)      │
  │  └──────┬───────┘                                             │
  │         │ Passage[] (scored)                                   │
  │         ▼                                                      │
  │  ┌──────────────┐                                             │
  │  │ SourceTrust  │──→ Domain reputation bonus                   │
  │  │   Scorer     │──→ Trust-weighted score adjustment           │
  │  └──────┬───────┘                                             │
  │         │ Passage[] (trust-adjusted)                           │
  │         ▼                                                      │
  │  ┌──────────────┐                                             │
  │  │ Diversity &  │──→ Max passages per domain                   │
  │  │  Top-K       │──→ Final selection                           │
  │  └──────┬───────┘                                             │
  │         │                                                      │
  │         ▼                                                      │
  │  Top-K Passage[] → ready for Fact Extraction & LLM            │
  └────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from urllib.parse import urlparse

from lina.models.datatypes import SearchResult, Passage
from lina.web_extraction.page_processor import (
    PageProcessor, PageResult, ProcessingStats, get_page_processor,
)
from lina.web_extraction.hybrid_ranker import (
    HybridRanker, get_hybrid_ranker,
)
from lina.web_extraction.source_trust import (
    SourceTrustScorer, get_source_trust_scorer,
)

logger = logging.getLogger("lina.web_extraction.web_pipeline")


# ═══════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════

@dataclass
class WebExtractionConfig:
    """Configuration for the web extraction pipeline."""
    # Page processing
    max_pages: int = 5
    page_timeout_sec: float = 12.0
    download_workers: int = 3
    max_passages_per_page: int = 40

    # Ranking
    top_k_passages: int = 10
    min_passage_score: float = 0.12
    bm25_weight: float = 0.35
    embedding_weight: float = 0.65

    # Trust
    trust_bonus_weight: float = 0.10

    # Diversity
    max_passages_per_domain: int = 5

    # Snippet fallback
    min_snippet_words: int = 10


# ═══════════════════════════════════════════════════
#  Pipeline Result
# ═══════════════════════════════════════════════════

@dataclass
class WebExtractionResult:
    """Complete result of the web extraction pipeline."""
    passages: List[Passage] = field(default_factory=list)
    page_results: List[PageResult] = field(default_factory=list)
    total_pages_attempted: int = 0
    total_pages_succeeded: int = 0
    total_raw_passages: int = 0
    total_ranked_passages: int = 0
    domains_used: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    used_snippet_fallback: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.passages)

    @property
    def top_passage(self) -> Optional[Passage]:
        return self.passages[0] if self.passages else None

    def format_context_for_rag(self, max_passages: int = 5) -> str:
        """
        Format top passages as structured context for RAG prompt.

        Output format:
            [SOURCE 1: title (domain)]
            passage text...

            [SOURCE 2: title (domain)]
            passage text...
        """
        if not self.passages:
            return ""

        parts: List[str] = []
        seen_urls: set = set()

        for i, p in enumerate(self.passages[:max_passages], 1):
            # Deduplicate by URL for cleaner context
            if p.source_url in seen_urls:
                continue
            seen_urls.add(p.source_url)

            domain = self._extract_domain(p.source_url)
            title = p.source_title or domain
            header = f"[SOURCE {i}: {title} ({domain})]"
            parts.append(f"{header}\n{p.text}")

        return "\n\n".join(parts)

    def format_sources(self) -> str:
        """Format source list for citation."""
        sources: List[str] = []
        seen: set = set()
        for p in self.passages:
            if p.source_url and p.source_url not in seen:
                seen.add(p.source_url)
                domain = self._extract_domain(p.source_url)
                title = p.source_title or domain
                sources.append(f"• {title}: {p.source_url}")
        return "\n".join(sources)

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.hostname or url
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return url


# ═══════════════════════════════════════════════════
#  Web Extraction Pipeline
# ═══════════════════════════════════════════════════

class WebExtractionPipeline:
    """
    Production-grade web content extraction pipeline for RAG.

    Orchestrates: page processing → ranking → trust scoring → top-K selection.

    This replaces the old PassageExtractor + EmbeddingRanker combination
    with a unified, more capable pipeline.

    Usage:
        pipeline = WebExtractionPipeline()
        result = pipeline.run(search_results, query="processor Realme 10")

        # Get formatted context for LLM
        context = result.format_context_for_rag(max_passages=5)

        # Or get raw passages for fact extraction
        top_passages = result.passages[:5]

    Migration from old system:
        # Old way:
        passages = passage_extractor.extract(results, query)
        ranked = embedding_ranker.rank(passages, query, top_k=10)

        # New way (drop-in compatible):
        result = web_pipeline.run(results, query)
        ranked = result.passages  # Already ranked
    """

    def __init__(
        self,
        config: WebExtractionConfig | None = None,
        page_processor: PageProcessor | None = None,
        ranker: HybridRanker | None = None,
        trust_scorer: SourceTrustScorer | None = None,
    ):
        self._cfg = config or WebExtractionConfig()
        self._processor = page_processor or get_page_processor()
        self._ranker = ranker or get_hybrid_ranker()
        self._trust = trust_scorer or get_source_trust_scorer()

    def run(
        self,
        results: List[SearchResult],
        query: str,
        top_k: int | None = None,
    ) -> WebExtractionResult:
        """
        Run the complete web extraction pipeline.

        Args:
            results: Ranked search results from the search engines.
            query: User's original query.
            top_k: Override for number of passages to return.

        Returns:
            WebExtractionResult with ranked passages and metadata.
        """
        top_k = top_k or self._cfg.top_k_passages
        t0 = time.time()

        if not results:
            return WebExtractionResult()

        # ── Stage 1: Parallel page download, extraction, chunking ──
        page_results = self._processor.process(
            results[:self._cfg.max_pages],
            query=query,
        )

        # Collect all passages
        all_passages: List[Passage] = []
        for pr in page_results:
            if pr.is_success:
                all_passages.extend(pr.passages)

        total_raw = len(all_passages)

        # ── Fallback: snippet-based passages if downloads failed ──
        if len(all_passages) < 3:
            snippet_passages = self._snippets_to_passages(results)
            all_passages.extend(snippet_passages)
            used_fallback = bool(snippet_passages)
        else:
            used_fallback = False

        if not all_passages:
            elapsed = (time.time() - t0) * 1000
            return WebExtractionResult(
                page_results=page_results,
                total_pages_attempted=len(page_results),
                elapsed_ms=elapsed,
            )

        # ── Stage 2: Hybrid ranking (BM25 + embeddings) ──
        ranked = self._ranker.rank(
            all_passages,
            query=query,
            top_k=top_k * 3,  # Get more for diversity filtering
            min_score=self._cfg.min_passage_score,
        )

        # ── Stage 3: Source trust adjustment ──
        self._trust.score_passages(
            ranked,
            trust_bonus_weight=self._cfg.trust_bonus_weight,
        )
        ranked.sort(key=lambda p: p.score, reverse=True)

        # ── Stage 4: Diversity enforcement ──
        diversified = self._enforce_diversity(
            ranked,
            max_per_domain=self._cfg.max_passages_per_domain,
            top_k=top_k,
        )

        # ── Build result ──
        domains = list(set(
            self._extract_domain(p.source_url)
            for p in diversified if p.source_url
        ))

        pages_succeeded = sum(1 for pr in page_results if pr.is_success)

        elapsed = (time.time() - t0) * 1000
        logger.info(
            "WebExtractionPipeline: %d results → %d pages → %d raw passages "
            "→ %d ranked → %d final in %.0f ms",
            len(results), len(page_results), total_raw,
            len(ranked), len(diversified), elapsed,
        )

        return WebExtractionResult(
            passages=diversified,
            page_results=page_results,
            total_pages_attempted=len(page_results),
            total_pages_succeeded=pages_succeeded,
            total_raw_passages=total_raw,
            total_ranked_passages=len(diversified),
            domains_used=domains,
            elapsed_ms=elapsed,
            used_snippet_fallback=used_fallback,
        )

    # ═══════════════════════════════════════════════
    #  Convenience methods
    # ═══════════════════════════════════════════════

    def extract_passages(
        self,
        results: List[SearchResult],
        query: str,
        top_k: int = 10,
    ) -> List[Passage]:
        """
        Convenience: run pipeline and return only passages.

        Drop-in replacement for old PassageExtractor + EmbeddingRanker.
        """
        result = self.run(results, query, top_k=top_k)
        return result.passages

    def extract_and_format(
        self,
        results: List[SearchResult],
        query: str,
        max_passages: int = 5,
    ) -> str:
        """
        Convenience: run pipeline and return formatted RAG context string.
        """
        result = self.run(results, query, top_k=max_passages)
        return result.format_context_for_rag(max_passages=max_passages)

    # ═══════════════════════════════════════════════
    #  Diversity enforcement
    # ═══════════════════════════════════════════════

    def _enforce_diversity(
        self,
        passages: List[Passage],
        max_per_domain: int = 5,
        top_k: int = 10,
    ) -> List[Passage]:
        """
        Enforce source diversity in final selection.

        Limits passages from any single domain to max_per_domain,
        ensuring the LLM sees perspectives from multiple sources.
        """
        domain_counts: Dict[str, int] = {}
        diversified: List[Passage] = []

        for p in passages:
            domain = self._extract_domain(p.source_url) if p.source_url else "unknown"

            count = domain_counts.get(domain, 0)
            if count >= max_per_domain:
                continue

            domain_counts[domain] = count + 1
            diversified.append(p)

            if len(diversified) >= top_k:
                break

        return diversified

    # ═══════════════════════════════════════════════
    #  Snippet fallback
    # ═══════════════════════════════════════════════

    def _snippets_to_passages(
        self,
        results: List[SearchResult],
    ) -> List[Passage]:
        """
        Convert search result snippets to passages (fallback).

        Used when page downloads largely fail but we have snippet text.
        """
        passages: List[Passage] = []
        for r in results:
            if r.snippet and len(r.snippet.split()) >= self._cfg.min_snippet_words:
                passages.append(Passage(
                    text=r.snippet,
                    source_url=r.url,
                    source_title=r.title,
                    score=0.3,  # Lower confidence for snippet-only
                ))
        return passages

    # ═══════════════════════════════════════════════
    #  Helpers
    # ═══════════════════════════════════════════════

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.hostname or url
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return url


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_pipeline: WebExtractionPipeline | None = None


def get_web_extraction_pipeline() -> WebExtractionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = WebExtractionPipeline()
    return _pipeline
