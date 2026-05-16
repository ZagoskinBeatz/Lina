# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Web Extraction Pipeline v2 (Dual-Mode).

Top-level orchestrator with query-adaptive routing:

  ┌──────────────────────────────────────────────────────────────────┐
  │                   WebExtractionPipeline (v2)                     │
  │                                                                  │
  │  User Query                                                      │
  │       │                                                          │
  │       ▼                                                          │
  │  ┌──────────────────┐                                           │
  │  │ QueryClassifier  │──→ GENERAL / LINUX / ERROR                │
  │  └──────┬───────────┘                                           │
  │         │                                                        │
  │    ┌────┴────────────────────────┐                              │
  │    │                             │                              │
  │  GENERAL                    LINUX / ERROR                       │
  │    │                             │                              │
  │    │                    ┌────────┴──────────┐                   │
  │    │                    │ ErrorKnowledgeGraph│                   │
  │    │                    │ lookup (ERROR only)│                   │
  │    │                    └────────┬──────────┘                   │
  │    │                     ┌───────┴─────────┐                    │
  │    │                  found?             not found              │
  │    │                  + high conf         or low conf           │
  │    │                     │                    │                  │
  │    │                  Return                  │                  │
  │    │                  directly                │                  │
  │    │                                          │                  │
  │    ▼                                          ▼                  │
  │  ┌──────────────┐                    ┌──────────────┐          │
  │  │ PageProcessor │                    │ PageProcessor │          │
  │  │  (standard)   │                    │  + Linux ext  │          │
  │  └──────┬───────┘                    └──────┬───────┘          │
  │         ▼                                    ▼                   │
  │  ┌──────────────┐                    ┌──────────────┐          │
  │  │ HybridRanker │                    │ HybridRanker │          │
  │  │  (standard)  │                    │  (Linux mode)│          │
  │  └──────┬───────┘                    └──────┬───────┘          │
  │         │                             ┌─────┴──────────┐       │
  │         │                             │ SolutionDetect  │       │
  │         │                             │ CommandExtract  │       │
  │         │                             │ ErrorDetect     │       │
  │         │                             │ KG Learn        │       │
  │         │                             └─────┬──────────┘       │
  │         ▼                                    ▼                   │
  │  ┌──────────────────────────────────────────────┐              │
  │  │ SourceTrust → Diversity → Top-K → Format     │              │
  │  └──────────────────────┬───────────────────────┘              │
  │                         ▼                                       │
  │     WebExtractionResult (passages + solutions + commands)       │
  └──────────────────────────────────────────────────────────────────┘

Pipeline stages (unified):
  1. Query classification → mode selection
  2. (ERROR mode) Error Knowledge Graph lookup
  3. Parallel page download + content extraction + chunking
  4. Hybrid ranking (standard or Linux-boosted)
  5. Source trust scoring
  6. (LINUX/ERROR) Solution detection + command extraction + error detection
  7. (ERROR) Error Knowledge Graph learning
  8. Top-K selection with diversity enforcement
  9. Context assembly for RAG

Design:
  - LLM is NEVER called
  - Single unified pipeline with two internal modes
  - Every stage independently testable
  - Backward compatible with v1 API
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from urllib.parse import urlparse

from lina.models.datatypes import SearchResult, Passage
from lina.web_extraction.query_classifier import (
    QueryClassifier, QueryMode, QueryClassification,
    get_query_classifier,
)
from lina.web_extraction.page_processor import (
    PageProcessor, PageResult, get_page_processor,
)
from lina.web_extraction.hybrid_ranker import (
    HybridRanker, get_hybrid_ranker,
)
from lina.web_extraction.source_trust import (
    SourceTrustScorer, get_source_trust_scorer,
)
from lina.web_extraction.linux_commands import (
    LinuxCommandExtractor, LinuxCommand, get_linux_command_extractor,
)
from lina.web_extraction.solution_detector import (
    SolutionDetector, SolutionBlock, ErrorDetector, DetectedError,
    get_solution_detector, get_error_detector,
)
from lina.web_extraction.error_knowledge_graph import (
    ErrorKnowledgeGraph, KnownSolution, LookupResult,
    get_error_knowledge_graph,
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

    # Linux mode — extra domain trust bonuses
    linux_trust_bonus_domains: Dict[str, float] = field(default_factory=lambda: {
        "wiki.archlinux.org": 0.15,
        "wiki.debian.org": 0.12,
        "help.ubuntu.com": 0.12,
        "wiki.gentoo.org": 0.12,
        "docs.fedoraproject.org": 0.12,
        "man7.org": 0.10,
        "stackoverflow.com": 0.08,
        "superuser.com": 0.08,
        "askubuntu.com": 0.10,
        "unix.stackexchange.com": 0.10,
        "serverfault.com": 0.08,
        "linuxquestions.org": 0.06,
        "bbs.archlinux.org": 0.06,
        "forums.debian.net": 0.06,
    })

    # Error Knowledge Graph
    use_error_kg: bool = True
    min_kg_confidence_for_direct: float = 0.65
    learn_from_web: bool = True


# ═══════════════════════════════════════════════════
#  Pipeline Result (extended)
# ═══════════════════════════════════════════════════

@dataclass
class WebExtractionResult:
    """Complete result of the web extraction pipeline."""
    # Core
    passages: List[Passage] = field(default_factory=list)
    page_results: List[PageResult] = field(default_factory=list)
    total_pages_attempted: int = 0
    total_pages_succeeded: int = 0
    total_raw_passages: int = 0
    total_ranked_passages: int = 0
    domains_used: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    used_snippet_fallback: bool = False

    # Query classification
    query_mode: QueryMode = QueryMode.GENERAL
    query_classification: Optional[QueryClassification] = None

    # Linux-specific results
    solutions: List[SolutionBlock] = field(default_factory=list)
    commands: List[LinuxCommand] = field(default_factory=list)
    detected_errors: List[DetectedError] = field(default_factory=list)
    kg_lookup: Optional[LookupResult] = None
    answered_from_kg: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.passages)

    @property
    def is_linux_mode(self) -> bool:
        return self.query_mode in (QueryMode.LINUX, QueryMode.ERROR)

    @property
    def has_solutions(self) -> bool:
        return bool(self.solutions)

    @property
    def has_commands(self) -> bool:
        return bool(self.commands)

    @property
    def top_passage(self) -> Optional[Passage]:
        return self.passages[0] if self.passages else None

    @property
    def top_solution(self) -> Optional[SolutionBlock]:
        return self.solutions[0] if self.solutions else None

    def format_context_for_rag(self, max_passages: int = 5) -> str:
        """
        Format top passages as structured context for RAG prompt.

        In Linux mode, prepends solution blocks and commands.
        """
        parts: List[str] = []

        # ── KG solutions ──
        if self.answered_from_kg and self.kg_lookup and self.kg_lookup.entry:
            entry = self.kg_lookup.entry
            kg_parts: List[str] = [f"[KNOWN ERROR: {entry.description}]"]
            if entry.causes:
                kg_parts.append("Possible causes:")
                for cause in entry.causes[:5]:
                    kg_parts.append(f"  • {cause}")
            best = entry.best_solution
            if best:
                kg_parts.append(f"\nRecommended solution (confidence: {best.confidence:.0%}):")
                kg_parts.append(best.description)
                if best.commands:
                    kg_parts.append("Commands:")
                    for cmd in best.commands:
                        kg_parts.append(f"  $ {cmd}")
                if best.steps:
                    kg_parts.append("Steps:")
                    for i, step in enumerate(best.steps, 1):
                        kg_parts.append(f"  {i}. {step}")
            parts.append("\n".join(kg_parts))

        # ── Detected solutions ──
        if self.solutions and not self.answered_from_kg:
            for i, sol in enumerate(self.solutions[:3], 1):
                sol_parts: List[str] = [f"[SOLUTION {i}]"]
                if sol.problem:
                    sol_parts.append(f"Problem: {sol.problem[:200]}")
                sol_parts.append(f"Solution: {sol.solution[:500]}")
                if sol.commands:
                    sol_parts.append("Commands:")
                    for cmd in sol.commands[:10]:
                        sol_parts.append(f"  $ {cmd}")
                parts.append("\n".join(sol_parts))

        # ── Extracted commands summary ──
        if self.commands and not self.answered_from_kg:
            cmd_texts = [c.normalized for c in self.commands[:15]]
            if cmd_texts:
                parts.append("[EXTRACTED COMMANDS]\n" + "\n".join(f"  $ {c}" for c in cmd_texts))

        # ── Standard passage context ──
        seen_urls: set = set()
        source_idx = 1
        for p in self.passages[:max_passages]:
            if p.source_url in seen_urls:
                continue
            seen_urls.add(p.source_url)
            domain = _extract_domain(p.source_url)
            title = p.source_title or domain
            header = f"[SOURCE {source_idx}: {title} ({domain})]"
            parts.append(f"{header}\n{p.text}")
            source_idx += 1

        return "\n\n".join(parts)

    def format_sources(self) -> str:
        """Format source list for citation."""
        sources: List[str] = []
        seen: set = set()
        for p in self.passages:
            if p.source_url and p.source_url not in seen:
                seen.add(p.source_url)
                domain = _extract_domain(p.source_url)
                title = p.source_title or domain
                sources.append(f"• {title}: {p.source_url}")
        return "\n".join(sources)


# ═══════════════════════════════════════════════════
#  Web Extraction Pipeline v2 (Dual-Mode)
# ═══════════════════════════════════════════════════

class WebExtractionPipeline:
    """
    Production-grade web content extraction pipeline with dual-mode routing.

    Mode GENERAL: Standard web search → extract → rank → top-K.
    Mode LINUX:   Linux-boosted ranking + solution/command extraction.
    Mode ERROR:   Error KG lookup first → web search if needed → learn.

    Usage:
        pipeline = WebExtractionPipeline()
        result = pipeline.run(search_results, query="sudo apt install nginx fails")

        if result.is_linux_mode:
            print(f"Solutions: {len(result.solutions)}")
            print(f"Commands: {len(result.commands)}")
            if result.answered_from_kg:
                print("Answered from Error Knowledge Graph")

        context = result.format_context_for_rag(max_passages=5)
    """

    def __init__(
        self,
        config: WebExtractionConfig | None = None,
        page_processor: PageProcessor | None = None,
        ranker: HybridRanker | None = None,
        trust_scorer: SourceTrustScorer | None = None,
        classifier: QueryClassifier | None = None,
        command_extractor: LinuxCommandExtractor | None = None,
        solution_detector: SolutionDetector | None = None,
        error_detector: ErrorDetector | None = None,
        error_kg: ErrorKnowledgeGraph | None = None,
    ):
        self._cfg = config or WebExtractionConfig()
        self._processor = page_processor or get_page_processor()
        self._ranker = ranker or get_hybrid_ranker()
        self._trust = trust_scorer or get_source_trust_scorer()
        self._classifier = classifier or get_query_classifier()
        self._cmd_extractor = command_extractor or get_linux_command_extractor()
        self._sol_detector = solution_detector or get_solution_detector()
        self._err_detector = error_detector or get_error_detector()
        self._error_kg = error_kg or (get_error_knowledge_graph() if self._cfg.use_error_kg else None)

    def run(
        self,
        results: List[SearchResult],
        query: str,
        top_k: int | None = None,
    ) -> WebExtractionResult:
        """
        Run the complete web extraction pipeline.

        Args:
            results: Ranked search results from search engines.
            query: User's original query.
            top_k: Override for number of passages to return.

        Returns:
            WebExtractionResult with ranked passages, solutions, commands.
        """
        top_k = top_k or self._cfg.top_k_passages
        t0 = time.time()

        if not results and not query:
            return WebExtractionResult()

        # ═══════════════════════════════════════
        #  Stage 1: Query Classification
        # ═══════════════════════════════════════
        classification = self._classifier.classify(query)
        mode = classification.mode

        result = WebExtractionResult(
            query_mode=mode,
            query_classification=classification,
        )

        logger.info(
            "WebPipeline: mode=%s conf=%.2f kw=%d cmd=%d err=%d",
            mode.value, classification.confidence,
            len(classification.linux_keywords),
            len(classification.linux_commands),
            len(classification.error_strings),
        )

        # ═══════════════════════════════════════
        #  Stage 2: Error Knowledge Graph Lookup
        # ═══════════════════════════════════════
        if mode == QueryMode.ERROR and self._error_kg:
            result = self._try_kg_lookup(query, classification, result, top_k)
            if result.answered_from_kg:
                result.elapsed_ms = (time.time() - t0) * 1000
                logger.info("WebPipeline: answered from Error KG in %.0f ms", result.elapsed_ms)
                return result

        # ═══════════════════════════════════════
        #  Stage 3: Page processing (parallel)
        # ═══════════════════════════════════════
        if not results:
            result.elapsed_ms = (time.time() - t0) * 1000
            return result

        page_results = self._processor.process(
            results[:self._cfg.max_pages],
            query=query,
        )

        all_passages: List[Passage] = []
        for pr in page_results:
            if pr.is_success:
                all_passages.extend(pr.passages)

        total_raw = len(all_passages)

        # Snippet fallback
        if len(all_passages) < 3:
            snippet_passages = self._snippets_to_passages(results)
            all_passages.extend(snippet_passages)
            result.used_snippet_fallback = bool(snippet_passages)

        if not all_passages:
            result.page_results = page_results
            result.total_pages_attempted = len(page_results)
            result.elapsed_ms = (time.time() - t0) * 1000
            return result

        # ═══════════════════════════════════════
        #  Stage 4: Ranking (mode-adaptive)
        # ═══════════════════════════════════════
        if mode in (QueryMode.LINUX, QueryMode.ERROR):
            ranked = self._ranker.rank_linux(
                all_passages,
                query=query,
                top_k=top_k * 3,
                min_score=self._cfg.min_passage_score,
                error_strings=classification.error_strings,
            )
        else:
            ranked = self._ranker.rank(
                all_passages,
                query=query,
                top_k=top_k * 3,
                min_score=self._cfg.min_passage_score,
            )

        # ═══════════════════════════════════════
        #  Stage 5: Source trust
        # ═══════════════════════════════════════
        self._apply_trust(ranked, mode)
        ranked.sort(key=lambda p: p.score, reverse=True)

        # ═══════════════════════════════════════
        #  Stage 6: Linux extraction (LINUX/ERROR)
        # ═══════════════════════════════════════
        if mode in (QueryMode.LINUX, QueryMode.ERROR):
            self._extract_linux_data(ranked, query, result)

        # ═══════════════════════════════════════
        #  Stage 7: Error KG learning
        # ═══════════════════════════════════════
        if mode == QueryMode.ERROR and self._error_kg and self._cfg.learn_from_web:
            self._learn_to_kg(result, query)

        # ═══════════════════════════════════════
        #  Stage 8: Diversity + Top-K
        # ═══════════════════════════════════════
        diversified = self._enforce_diversity(
            ranked,
            max_per_domain=self._cfg.max_passages_per_domain,
            top_k=top_k,
        )

        # ── Build result ──
        result.passages = diversified
        result.page_results = page_results
        result.total_pages_attempted = len(page_results)
        result.total_pages_succeeded = sum(1 for pr in page_results if pr.is_success)
        result.total_raw_passages = total_raw
        result.total_ranked_passages = len(diversified)
        result.domains_used = list(set(
            _extract_domain(p.source_url) for p in diversified if p.source_url
        ))
        result.elapsed_ms = (time.time() - t0) * 1000

        logger.info(
            "WebPipeline [%s]: %d results → %d pages → %d raw → %d ranked "
            "→ %d final in %.0f ms (sol=%d cmd=%d err=%d)",
            mode.value, len(results), len(page_results), total_raw,
            len(ranked), len(diversified), result.elapsed_ms,
            len(result.solutions), len(result.commands),
            len(result.detected_errors),
        )

        return result

    # ═══════════════════════════════════════════════
    #  Stage implementations
    # ═══════════════════════════════════════════════

    def _try_kg_lookup(
        self,
        query: str,
        classification: QueryClassification,
        result: WebExtractionResult,
        top_k: int,
    ) -> WebExtractionResult:
        """Stage 2: Try to answer from Error Knowledge Graph."""
        try:
            for err_str in classification.error_strings:
                lookup = self._error_kg.lookup(err_str)
                if lookup.found:
                    result.kg_lookup = lookup
                    if lookup.can_answer_directly:
                        result.answered_from_kg = True
                        result.passages = self._kg_to_passages(lookup, top_k)
                        logger.info(
                            "KG hit: '%s' quality=%.2f direct=%s",
                            err_str[:60], lookup.match_quality, lookup.can_answer_directly,
                        )
                        return result

            if not result.kg_lookup:
                lookup = self._error_kg.lookup(query)
                if lookup.found:
                    result.kg_lookup = lookup
                    if lookup.can_answer_directly:
                        result.answered_from_kg = True
                        result.passages = self._kg_to_passages(lookup, top_k)
                        return result

        except Exception as e:
            logger.warning("KG lookup failed: %s", e)

        return result

    def _kg_to_passages(self, lookup: LookupResult, top_k: int) -> List[Passage]:
        """Convert Error KG solutions to Passage objects."""
        passages: List[Passage] = []
        if not lookup.entry:
            return passages

        entry = lookup.entry
        for sol in sorted(entry.solutions, key=lambda s: s.confidence, reverse=True)[:top_k]:
            text_parts = [sol.description]
            if sol.steps:
                text_parts.append("Steps:")
                for i, step in enumerate(sol.steps, 1):
                    text_parts.append(f"  {i}. {step}")
            if sol.commands:
                text_parts.append("Commands:")
                for cmd in sol.commands:
                    text_parts.append(f"  $ {cmd}")

            passages.append(Passage(
                text="\n".join(text_parts),
                source_url=sol.sources[0] if sol.sources else "",
                source_title=f"Known solution: {entry.description}",
                score=sol.confidence,
            ))

        return passages

    def _apply_trust(self, passages: List[Passage], mode: QueryMode):
        """Stage 5: Apply trust scoring with optional Linux domain bonuses."""
        for p in passages:
            if not p.source_url:
                continue

            bonus = self._trust.passage_trust_bonus(p.source_url)
            bonus *= self._cfg.trust_bonus_weight

            if mode in (QueryMode.LINUX, QueryMode.ERROR):
                domain = _extract_domain(p.source_url)
                linux_bonus = self._cfg.linux_trust_bonus_domains.get(domain, 0.0)
                bonus += linux_bonus

            p.score = min(p.score + bonus, 1.0)

    def _extract_linux_data(
        self,
        passages: List[Passage],
        query: str,
        result: WebExtractionResult,
    ):
        """Stage 6: Extract solutions, commands, errors from passages."""
        try:
            # Error detection
            for p in passages[:15]:
                errors = self._err_detector.detect(p.text)
                for err in errors:
                    if err.normalized not in {e.normalized for e in result.detected_errors}:
                        result.detected_errors.append(err)

            # Solution detection
            result.solutions = self._sol_detector.detect_in_passages(passages[:15])

            # Command extraction
            result.commands = self._cmd_extractor.extract_from_passages(passages[:15])

            logger.info(
                "Linux extraction: %d errors, %d solutions, %d commands",
                len(result.detected_errors), len(result.solutions),
                len(result.commands),
            )
        except Exception as e:
            logger.warning("Linux extraction failed: %s", e)

    def _learn_to_kg(self, result: WebExtractionResult, query: str):
        """Stage 7: Learn new solutions into Error Knowledge Graph."""
        if not result.solutions or not result.detected_errors:
            return

        try:
            for err in result.detected_errors[:3]:
                for sol in result.solutions[:3]:
                    if sol.confidence < 0.40:
                        continue
                    new_sol = KnownSolution(
                        description=sol.solution[:200],
                        commands=sol.commands[:10],
                        steps=sol.steps[:10],
                        confidence=sol.confidence * 0.5,
                        sources=[sol.source_url] if sol.source_url else [],
                        times_confirmed=1,
                    )
                    self._error_kg.learn(
                        error_key=err.normalized,
                        solution=new_sol,
                        error_type=err.error_type,
                    )
            logger.info("KG learning: added for %d errors", len(result.detected_errors))
        except Exception as e:
            logger.warning("KG learning failed: %s", e)

    # ═══════════════════════════════════════════════
    #  Convenience methods (v1 compatible)
    # ═══════════════════════════════════════════════

    def extract_passages(
        self,
        results: List[SearchResult],
        query: str,
        top_k: int = 10,
    ) -> List[Passage]:
        """Drop-in for old PassageExtractor + EmbeddingRanker."""
        result = self.run(results, query, top_k=top_k)
        return result.passages

    def extract_and_format(
        self,
        results: List[SearchResult],
        query: str,
        max_passages: int = 5,
    ) -> str:
        """Run pipeline and return formatted RAG context string."""
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
        """Limit passages from any single domain."""
        domain_counts: Dict[str, int] = {}
        diversified: List[Passage] = []

        for p in passages:
            domain = _extract_domain(p.source_url) if p.source_url else "unknown"
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

    def _snippets_to_passages(self, results: List[SearchResult]) -> List[Passage]:
        """Convert search result snippets to passages (fallback)."""
        passages: List[Passage] = []
        for r in results:
            if r.snippet and len(r.snippet.split()) >= self._cfg.min_snippet_words:
                passages.append(Passage(
                    text=r.snippet,
                    source_url=r.url,
                    source_title=r.title,
                    score=0.3,
                ))
        return passages


# ═══════════════════════════════════════════════════
#  Helper
# ═══════════════════════════════════════════════════

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
