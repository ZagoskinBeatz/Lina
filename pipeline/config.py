# -*- coding: utf-8 -*-
"""
Lina Pipeline — Configuration.

All tunable parameters for the v2 assistant pipeline.
Centralised here to avoid magic numbers scattered across modules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PipelineConfig:
    """Tunable knobs for the v2 pipeline."""

    # ── Query Rewriter ──
    max_search_queries: int = 5            # Max queries generated from one user query
    min_search_queries: int = 3            # Min queries generated

    # ── Web Search ──
    max_search_results: int = 15           # Max results across all engines
    search_timeout_sec: float = 15.0       # Per-engine timeout
    search_parallel_workers: int = 3       # ThreadPool workers for parallel search

    # ── Page Download ──
    max_pages_to_download: int = 5         # Top-N pages to fully download
    page_download_timeout_sec: float = 12.0
    max_page_chars: int = 80_000           # Truncate pages beyond this

    # ── Passage Extraction ──
    min_passage_words: int = 15
    max_passage_words: int = 200
    max_passages_per_page: int = 30
    overlap_sentences: int = 1

    # ── Embedding Ranking ──
    top_k_passages: int = 10               # Best passages after ranking
    embedding_model: str = "all-MiniLM-L6-v2"  # sentence-transformers model
    min_similarity: float = 0.20           # Discard passages below this

    # ── Fact Extraction ──
    max_facts_per_passage: int = 10
    max_total_facts: int = 50

    # ── Fact Aggregation ──
    min_confidence_for_use: float = 0.40   # Facts below this are discarded
    multi_source_boost: float = 0.20       # Bonus for 2+ source confirmation

    # ── LLM Generation ──
    max_context_facts: int = 15            # Facts injected into LLM prompt
    max_context_chars: int = 4000          # Hard limit on context size
    generation_temperature: float = 0.3    # Low temp = more factual

    # ── Self Verification ──
    enable_self_verification: bool = True
    max_regeneration_attempts: int = 1     # Max retries on hallucination

    # ── Response Validation ──
    min_answer_length: int = 20            # Chars
    max_answer_length: int = 3000
    min_facts_used: int = 1                # At least 1 fact must be used
    min_confidence_threshold: float = 0.40

    # ── Conversation Memory ──
    max_conversation_turns: int = 10
    topic_decay_turns: int = 5             # Forget topic after N turns

    # ── Fact Store (cache) ──
    fact_ttl_seconds: int = 3600           # 1 hour TTL for cached facts
    max_cached_entities: int = 200


# ═══════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════

_config: PipelineConfig | None = None


def get_pipeline_config() -> PipelineConfig:
    """Get (or create) the pipeline configuration singleton."""
    global _config
    if _config is None:
        _config = PipelineConfig()
    return _config
