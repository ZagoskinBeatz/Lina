# -*- coding: utf-8 -*-
"""
Lina Pipeline — Assistant Pipeline (v2 Orchestrator).

The MAIN entry point for the v2 RAG pipeline.

Full data flow:
  ┌──────────────────────────────────────────────────────────────────────┐
  │  User Query                                                         │
  │    │                                                                │
  │    ▼                                                                │
  │  0. Response Cache check ─────── hit? → return cached answer       │
  │    │ miss                                                           │
  │    ▼                                                                │
  │  1. Conversation State: resolve pronouns, get context              │
  │    │                                                                │
  │    ▼                                                                │
  │  2. Fact Store: cached facts? ── hit? → skip search, go to 8      │
  │    │ miss                                                           │
  │    ▼                                                                │
  │  3. Query Rewriter: 1 query → 3-5 optimised search queries        │
  │    │                                                                │
  │    ▼                                                                │
  │  4. Parallel Web Search (ThreadPoolExecutor)                       │
  │    │                                                                │
  │    ▼                                                                │
  │  5. Result Ranking (domain + keyword + freshness + diversity)      │
  │    │                                                                │
  │    ▼                                                                │
  │  6. Passage Extraction (download top pages → split)                │
  │    │                                                                │
  │    ▼                                                                │
  │  7. Embedding Ranking (semantic similarity to query)               │
  │    │                                                                │
  │    ▼                                                                │
  │  8. Fact Extraction → Aggregation → Verification                   │
  │    │                                                                │
  │    ▼                                                                │
  │  9. Context Compression (top-K facts → prompt)                     │
  │    │                                                                │
  │    ▼                                                                │
  │ 10. LLM Answer Generation (Fact Mode)                              │
  │    │                                                                │
  │    ▼                                                                │
  │ 11. Self-Verification (second LLM pass)                            │
  │    │ ◄──── if hallucination → regenerate (max 2 attempts)          │
  │    ▼                                                                │
  │ 12. Cache answer + update conversation state                       │
  │    │                                                                │
  │    ▼                                                                │
  │  Final Answer (PipelineAnswer)                                     │
  └──────────────────────────────────────────────────────────────────────┘

Design:
  - Fully synchronous public API (parallelism inside stages).
  - Every stage has try/except — pipeline never crashes.
  - PipelineTrace records timing for every stage.
  - Configurable via PipelineConfig.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional, Tuple

from lina.models.datatypes import (
    ConversationTurn,
    Fact,
    FactSet,
    IntentType,
    Passage,
    PipelineAnswer,
    PipelineTrace,
    QueryPlan,
    SearchResult,
)
from lina.pipeline.config import PipelineConfig, get_pipeline_config

logger = logging.getLogger("lina.pipeline.assistant")


# ═══════════════════════════════════════════════════
#  Fact-Mode LLM Prompt
# ═══════════════════════════════════════════════════

_FACTMODE_PROMPT_RU = """Ты — факт-ориентированный ассистент.
Отвечай ТОЛЬКО на основе предоставленных фактов.
Если факт не указан — скажи «Информация не найдена».
Не придумывай данные.  Не добавляй факты от себя.

=== ФАКТЫ ===
{facts}

=== ВОПРОС ===
{query}

Дай чёткий, структурированный ответ на русском языке.
Если данных достаточно — перечисли ключевые характеристики.
Укажи источники (сайты) в конце ответа."""

_FACTMODE_PROMPT_EN = """You are a fact-oriented assistant.
Answer ONLY based on the provided facts.
If a fact is not listed — say "Information not found".
Do not invent data.  Do not add facts from your own knowledge.

=== FACTS ===
{facts}

=== QUESTION ===
{query}

Give a clear, structured answer.
If enough data is available — list key specs.
Cite sources (websites) at the end."""


class AssistantPipeline:
    """
    Main v2 RAG pipeline orchestrator.

    Usage:
        pipeline = AssistantPipeline(llm_fn=engine.generate)
        answer = pipeline.run("Расскажи про Realme 10")
    """

    def __init__(
        self,
        llm_fn=None,
        config: PipelineConfig | None = None,
    ):
        """
        Args:
            llm_fn: Callable(prompt: str) → str.  LLM generation function.
            config: Pipeline configuration.  None → default.
        """
        self._llm_fn = llm_fn
        self._cfg = config or get_pipeline_config()
        self._init_components()

    def _init_components(self):
        """Lazy-import and initialise all pipeline components."""
        from lina.core.query_rewriter import get_query_rewriter
        from lina.core.search_pipeline import get_search_pipeline
        from lina.core.passage_extractor import get_passage_extractor
        from lina.core.embedding_ranker import get_embedding_ranker
        from lina.core.fact_extractor import get_fact_extractor
        from lina.core.fact_aggregator import get_fact_aggregator
        from lina.core.fact_verifier import get_fact_verifier
        from lina.core.result_ranker import get_result_ranker
        from lina.llm.self_verifier import SelfVerifier
        from lina.memory.conversation_state import get_conversation_state
        from lina.memory.fact_store import get_fact_store
        from lina.memory.cache import get_response_cache

        self._rewriter = get_query_rewriter()
        self._search = get_search_pipeline()
        self._result_ranker = get_result_ranker()
        self._passage_ext = get_passage_extractor()
        self._emb_ranker = get_embedding_ranker()
        self._fact_ext = get_fact_extractor()
        self._fact_agg = get_fact_aggregator()
        self._fact_ver = get_fact_verifier()
        self._self_ver = SelfVerifier(llm_fn=self._llm_fn)
        self._conv_state = get_conversation_state()
        self._fact_store = get_fact_store()
        self._resp_cache = get_response_cache()

    # ═══════════════════════════════════════════════════
    #  Main entry point
    # ═══════════════════════════════════════════════════

    def run(
        self,
        query: str,
        lang: str = "ru",
        use_cache: bool = True,
    ) -> PipelineAnswer:
        """
        Execute the full v2 RAG pipeline.

        Args:
            query:     User query (any language).
            lang:      Response language ("ru" or "en").
            use_cache: Check response cache.

        Returns:
            PipelineAnswer with text, facts, confidence, sources, etc.
        """
        t_start = time.time()
        trace = PipelineTrace()

        def _elapsed():
            """Helper: ms since t_start."""
            return (time.time() - t_start) * 1000

        # ── Stage 0: Response Cache ──
        if use_cache:
            cached = self._resp_cache.get(query.strip().lower())
            if cached is not None and isinstance(cached, PipelineAnswer):
                logger.info("Pipeline cache hit for: %s", query[:40])
                return cached
        t_stage = time.time()
        trace.record("cache_check", (time.time() - t_stage) * 1000)

        # ── Stage 1: Conversation State → resolve pronouns ──
        t_stage = time.time()
        resolved_query = self._conv_state.resolve_pronoun_subject(query)
        context_hint = self._conv_state.build_context_hint()
        trace.record("conversation_state", (time.time() - t_stage) * 1000)

        # ── Stage 2: Fact Store → check cached facts ──
        t_stage = time.time()
        subject = self._extract_subject(resolved_query)
        cached_facts = self._fact_store.get(subject) if subject else []
        # Require minimum quality to use cached facts (avoid stale 1-fact entries)
        if cached_facts and (len(cached_facts) < 3 or
                sum(f.confidence for f in cached_facts) / len(cached_facts) < 0.60):
            logger.info("[assistant] fact store low quality: %d facts for '%s', re-searching",
                        len(cached_facts), subject)
            cached_facts = []
        trace.record("fact_store_check", (time.time() - t_stage) * 1000)

        if cached_facts:
            logger.info("Fact store hit: %d facts for '%s'", len(cached_facts), subject)
            fact_set = FactSet(
                subject=subject,
                facts=cached_facts,
                total_sources=len(set(s for f in cached_facts for s in f.sources)),
                confidence=sum(f.confidence for f in cached_facts) / max(len(cached_facts), 1),
            )
            # Skip search, go directly to generation
            answer = self._generate_and_verify(
                resolved_query, fact_set, trace, lang,
            )
            self._finalize(query, answer, subject, fact_set, trace, t_start)
            return answer

        # ── Stage 3: Query Rewriting ──
        t_stage = time.time()
        try:
            plan = self._rewriter.rewrite(resolved_query)
            trace.record("query_rewrite", (time.time() - t_stage) * 1000)
            logger.info("Query plan: %d queries, entities=%s",
                       len(plan.queries), plan.detected_entities[:3])
        except Exception as e:
            logger.error("Query rewrite failed: %s", e)
            plan = QueryPlan(original=resolved_query, queries=[resolved_query])
            trace.record("query_rewrite", (time.time() - t_stage) * 1000)
            trace.errors.append(f"rewrite: {e}")

        # ── Stage 4: Parallel Web Search ──
        t_stage = time.time()
        try:
            _, raw_results = self._search.search(resolved_query)
            trace.search_results_count = len(raw_results)
            trace.record("web_search", (time.time() - t_stage) * 1000)
            logger.info("Web search: %d results", len(raw_results))
        except Exception as e:
            logger.error("Web search failed: %s", e)
            raw_results = []
            trace.record("web_search", (time.time() - t_stage) * 1000)
            trace.errors.append(f"search: {e}")

        if not raw_results:
            return self._no_results_answer(query, trace, t_start)

        # ── Stage 5: Result Ranking ──
        t_stage = time.time()
        try:
            ranked_results = self._result_ranker.rank(raw_results, resolved_query)
            trace.record("result_ranking", (time.time() - t_stage) * 1000)
        except Exception as e:
            logger.error("Result ranking failed: %s", e)
            ranked_results = raw_results
            trace.record("result_ranking", (time.time() - t_stage) * 1000)
            trace.errors.append(f"ranking: {e}")

        # ── Stage 6: Passage Extraction ──
        t_stage = time.time()
        try:
            passages = self._passage_ext.extract(ranked_results, resolved_query)
            trace.passages_count = len(passages)
            trace.record("passage_extraction", (time.time() - t_stage) * 1000)
            logger.info("Passages extracted: %d", len(passages))
        except Exception as e:
            logger.error("Passage extraction failed: %s", e)
            passages = []
            trace.record("passage_extraction", (time.time() - t_stage) * 1000)
            trace.errors.append(f"passages: {e}")

        if not passages:
            # Fallback: build facts from snippets only
            passages = self._snippets_to_passages(ranked_results)

        # ── Stage 7: Embedding Ranking ──
        t_stage = time.time()
        try:
            top_passages = self._emb_ranker.rank(
                passages,
                resolved_query,
                top_k=self._cfg.top_k_passages,
                min_similarity=self._cfg.min_similarity,
            )
            trace.record("embedding_ranking", (time.time() - t_stage) * 1000)
            logger.info("Top passages after embedding: %d", len(top_passages))
        except Exception as e:
            logger.error("Embedding ranking failed: %s", e)
            top_passages = passages[:self._cfg.top_k_passages]
            trace.record("embedding_ranking", (time.time() - t_stage) * 1000)
            trace.errors.append(f"embeddings: {e}")

        # ── Stage 8: Fact Extraction → Aggregation → Verification ──
        subject = subject or plan.detected_entities[0] if plan.detected_entities else resolved_query[:30]

        t_stage = time.time()
        try:
            raw_facts = self._fact_ext.extract_from_passages(top_passages, subject=subject)
            trace.facts_extracted = len(raw_facts)
            trace.record("fact_extraction", (time.time() - t_stage) * 1000)
        except Exception as e:
            logger.error("Fact extraction failed: %s", e)
            raw_facts = []
            trace.record("fact_extraction", (time.time() - t_stage) * 1000)
            trace.errors.append(f"fact_extract: {e}")

        t_stage = time.time()
        try:
            fact_set = self._fact_agg.aggregate(raw_facts, subject=subject)
            trace.record("fact_aggregation", (time.time() - t_stage) * 1000)
        except Exception as e:
            logger.error("Fact aggregation failed: %s", e)
            fact_set = FactSet(subject=subject)
            trace.record("fact_aggregation", (time.time() - t_stage) * 1000)
            trace.errors.append(f"fact_agg: {e}")

        t_stage = time.time()
        try:
            fact_set = self._fact_ver.verify(fact_set)
            trace.facts_verified = fact_set.verified_count
            trace.record("fact_verification", (time.time() - t_stage) * 1000)
        except Exception as e:
            logger.error("Fact verification failed: %s", e)
            trace.record("fact_verification", (time.time() - t_stage) * 1000)
            trace.errors.append(f"fact_verify: {e}")

        # Cache verified facts for future queries
        if fact_set.facts and subject:
            try:
                self._fact_store.put(subject, fact_set.facts)
                self._fact_store.save()
            except Exception:
                pass

        # ── Stages 9-11: Generation + Self-Verification ──
        answer = self._generate_and_verify(resolved_query, fact_set, trace, lang)

        # ── Stage 12: Finalize ──
        self._finalize(query, answer, subject, fact_set, trace, t_start)
        return answer

    # ═══════════════════════════════════════════════════
    #  Internal stages
    # ═══════════════════════════════════════════════════

    def _generate_and_verify(
        self,
        query: str,
        fact_set: FactSet,
        trace: PipelineTrace,
        lang: str,
    ) -> PipelineAnswer:
        """Stages 9-11: Context compression → LLM generation → Self-verification."""

        if not self._llm_fn:
            # No LLM available: return fact summary directly
            text = fact_set.format_for_llm_ru() if lang == "ru" else fact_set.format_for_llm()
            return PipelineAnswer(
                text=text or "Информация не найдена.",
                facts_used=len(fact_set.facts),
                confidence=fact_set.confidence * 0.7,
                sources=list(set(s for f in fact_set.facts for s in f.sources)),
                verified=False,
            )

        # ── Stage 9: Context Compression ──
        t_stage = time.time()
        facts_text = fact_set.format_for_llm_ru() if lang == "ru" else fact_set.format_for_llm()
        # Truncate to max context chars
        if len(facts_text) > self._cfg.max_context_chars:
            facts_text = facts_text[:self._cfg.max_context_chars] + "\n[...усечено...]"

        template = _FACTMODE_PROMPT_RU if lang == "ru" else _FACTMODE_PROMPT_EN
        prompt = template.format(facts=facts_text, query=query)
        trace.record("context_compression", (time.time() - t_stage) * 1000)

        # ── Stage 10 + 11: Generate + Verify (with retry) ──
        best_answer: PipelineAnswer | None = None
        max_attempts = self._cfg.max_regeneration_attempts + 1

        for attempt in range(1, max_attempts + 1):
            t_stage = time.time()
            try:
                raw_text = self._llm_fn(prompt)
                trace.record(f"llm_generation_{attempt}", (time.time() - t_stage) * 1000)
            except Exception as e:
                logger.error("LLM generation failed (attempt %d): %s", attempt, e)
                trace.errors.append(f"llm_{attempt}: {e}")
                continue

            # Build answer
            sources = list(set(s for f in fact_set.facts for s in f.sources))
            answer = PipelineAnswer(
                text=raw_text.strip(),
                facts_used=len(fact_set.facts),
                confidence=fact_set.confidence,
                sources=sources[:10],
                generation_attempts=attempt,
            )

            # Self-verification
            if self._cfg.enable_self_verification and attempt < max_attempts:
                t_sv = time.time()
                try:
                    ver_result = self._self_ver.verify(answer, fact_set, lang=lang)
                    trace.record(f"self_verify_{attempt}", (time.time() - t_sv) * 1000)

                    if not ver_result.has_issues:
                        answer.verified = True
                        answer.confidence = min(1.0, answer.confidence + 0.10)
                        return answer

                    # Issues found → store and retry
                    answer.hallucination_flags = (
                        ver_result.hallucinations + ver_result.mismatches
                    )
                    best_answer = answer
                    logger.warning(
                        "Self-verify attempt %d: %d issues, retrying…",
                        attempt, len(answer.hallucination_flags),
                    )
                    continue

                except Exception as e:
                    logger.error("Self-verification failed: %s", e)
                    trace.errors.append(f"self_verify_{attempt}: {e}")
                    answer.verified = False
                    return answer
            else:
                answer.verified = False
                return answer

        # All attempts exhausted → return the best one
        if best_answer is not None:
            best_answer.confidence = max(0.2, best_answer.confidence - 0.15)
            return best_answer

        return PipelineAnswer(
            text="Не удалось сгенерировать надёжный ответ.",
            confidence=0.1,
            verified=False,
        )

    def _finalize(
        self,
        original_query: str,
        answer: PipelineAnswer,
        subject: str,
        fact_set: FactSet,
        trace: PipelineTrace,
        t_start: float,
    ) -> None:
        """Store in caches, update conversation state, set elapsed_ms."""
        elapsed = (time.time() - t_start) * 1000
        answer.elapsed_ms = elapsed

        logger.info(
            "Pipeline complete: conf=%.2f, verified=%s, facts=%d, "
            "sources=%d, elapsed=%.0f ms, errors=%d",
            answer.confidence, answer.verified, answer.facts_used,
            len(answer.sources), elapsed, len(trace.errors),
        )

        # Cache response
        try:
            self._resp_cache.put(
                original_query.strip().lower(), answer, ttl=1800,
            )
        except Exception:
            pass

        # Update conversation state
        try:
            turn = ConversationTurn(
                query=original_query,
                answer=answer.text[:200],
                intent=IntentType.WEB_SEARCH,
                topic=subject,
                entities=[subject] if subject else [],
                facts=fact_set.facts[:10],
            )
            self._conv_state.add_turn(turn)
        except Exception:
            pass

    def _extract_subject(self, query: str) -> str:
        """Try to extract the main entity from the query."""
        try:
            from lina.core.entity_parser import get_entity_parser
            parser = get_entity_parser()
            parsed = parser.parse(query)
            if parsed.entities:
                # Return the first device/product entity
                for e in parsed.entities:
                    if hasattr(e, "value"):
                        return e.value
                    if hasattr(e, "name"):
                        return e.name
        except Exception:
            pass
        return ""

    def _no_results_answer(
        self,
        query: str,
        trace: PipelineTrace,
        t_start: float,
    ) -> PipelineAnswer:
        """Fallback when search returns nothing."""
        elapsed = (time.time() - t_start) * 1000
        return PipelineAnswer(
            text="К сожалению, по запросу ничего не найдено. "
                 "Попробуйте переформулировать вопрос.",
            facts_used=0,
            confidence=0.05,
            verified=False,
            elapsed_ms=elapsed,
        )

    @staticmethod
    def _snippets_to_passages(results: List[SearchResult]) -> List[Passage]:
        """Convert search result snippets to minimal passages."""
        passages = []
        for r in results[:10]:
            text = r.snippet or r.title
            if text and len(text) > 10:
                passages.append(Passage(
                    text=text,
                    source_url=r.url,
                    source_title=r.title,
                    score=r.relevance,
                ))
        return passages


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_pipeline: AssistantPipeline | None = None


def get_assistant_pipeline(llm_fn=None) -> AssistantPipeline:
    """
    Get or create the singleton AssistantPipeline.

    Args:
        llm_fn: LLM generation function.  Required on first call.
    """
    global _pipeline
    if _pipeline is None:
        _pipeline = AssistantPipeline(llm_fn=llm_fn)
    return _pipeline
