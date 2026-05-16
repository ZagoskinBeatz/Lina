# -*- coding: utf-8 -*-
"""
Lina Pipeline v3 — Full RAG Orchestrator.

Architecture v3: Clean layer-based pipeline with multi-engine parallel search,
Reciprocal Rank Fusion, deep query understanding, and response validation
with automatic re-search.

Principle: LLM works ONLY with verified facts, never with raw web text.

Stages:
   0. Response cache check
   1. Query understanding (intent + entities + attributes + language)
   2. Conversation state (pronoun resolution, context hints)
   3. Fact store check (cached verified facts)
   4. Query rewriting (3-5 optimised search variants)
   5. Parallel multi-engine search (DDG + Brave + SearXNG + Wikipedia)
   6. Result merger (Reciprocal Rank Fusion + dedup)
   7. Domain-based re-ranking (reputation, freshness, diversity)
   8. Page download + HTML cleaning + passage splitting
   9. Semantic embedding ranking
  10. Fact extraction
  11. Fact aggregation
  12. Fact verification
  13. Context compression + LLM generation (fact mode)
  14. Self-check (hallucination detection)
  15. Response validation → re-search trigger if quality too low
  16. Cache + conversation state update
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional


class V3BypassSignal(Exception):
    """Raised when v3 pipeline detects an intent it cannot handle.

    system_command, math, etc. must be processed by the shell layer
    (Commander builtins, MiniLLM, etc.), not by the RAG pipeline.
    The caller should catch this and delegate to legacy handling.
    """
    def __init__(self, query: str, intent: str):
        self.query = query
        self.intent = intent
        super().__init__(f"v3 bypass: intent={intent}")


from lina.models.datatypes import (
    ConversationTurn,
    Fact,
    FactSet,
    IntentType,
    Passage,
    PipelineAnswer,
    PipelineTrace,
    QueryPlan,
    QueryUnderstanding,
    RetrievalResult,
    SearchResult,
)
from lina.pipeline.config import PipelineConfig, get_pipeline_config
from lina.pipeline.generation_gate import get_generation_gate

logger = logging.getLogger(__name__)


class PipelineV3:
    """
    Lina v3 RAG pipeline — multi-engine, fact-checked assistant.

    Usage:
        pipeline = PipelineV3(llm_fn=engine.generate)
        answer = pipeline.run("Какой процессор у Realme 10?")
    """

    # ── Quality thresholds for re-search trigger ──
    _MIN_FACTS_FOR_GOOD_ANSWER = 2
    _MIN_CONFIDENCE_FOR_GOOD_ANSWER = 0.35
    _MAX_RESEARCH_ATTEMPTS = 2

    def __init__(
        self,
        llm_fn=None,
        config: PipelineConfig | None = None,
    ):
        self._llm_fn = llm_fn
        self._cfg = config or get_pipeline_config()
        self._generation_gate = get_generation_gate()
        self._init_components()

    def _init_components(self):
        """Lazy-import and initialise all v3 pipeline components."""
        # ── Core ──
        from lina.core.query_understanding import get_query_understanding
        from lina.core.query_rewriter import get_query_rewriter

        # ── Retrieval ──
        from lina.retrieval.parallel_search import get_parallel_search
        from lina.retrieval.result_merger import get_result_merger
        from lina.retrieval.domain_ranker import get_domain_ranker

        # ── Processing ──
        from lina.processing.passage_splitter import get_passage_splitter
        from lina.processing.html_cleaner import clean_page

        # ── Embeddings ──
        from lina.embeddings.semantic_ranker import get_semantic_ranker

        # ── Web Extraction v4 (new deterministic pipeline) ──
        from lina.web_extraction.web_pipeline import get_web_extraction_pipeline

        # ── Retrieval Policy (adaptive quality thresholds) ──
        from lina.pipeline.retrieval_policy import get_retrieval_policy

        # ── Knowledge ──
        from lina.knowledge.fact_extractor import get_fact_extractor
        from lina.knowledge.fact_aggregator import get_fact_aggregator
        from lina.knowledge.fact_verifier import get_fact_verifier
        from lina.knowledge.fact_store import get_fact_store

        # ── LLM ──
        from lina.llm.fact_prompt import (
            build_generation_prompt,
            build_verification_prompt,
        )
        from lina.llm.self_check import SelfVerifier

        # ── Memory ──
        from lina.memory.conversation_state import get_conversation_state
        from lina.memory.cache import get_response_cache

        # ── Page download (from existing v2) ──
        from lina.core.passage_extractor import get_passage_extractor

        self._qu = get_query_understanding()
        self._rewriter = get_query_rewriter()
        self._parallel_search = get_parallel_search()
        self._merger = get_result_merger()
        self._domain_ranker = get_domain_ranker()
        self._splitter = get_passage_splitter()
        self._clean_page = clean_page
        self._semantic_ranker = get_semantic_ranker()
        self._fact_ext = get_fact_extractor()
        self._fact_agg = get_fact_aggregator()
        self._fact_ver = get_fact_verifier()
        self._fact_store = get_fact_store()
        self._build_gen_prompt = build_generation_prompt
        self._build_ver_prompt = build_verification_prompt
        self._self_ver = SelfVerifier(llm_fn=self._llm_fn)
        self._conv_state = get_conversation_state()
        self._resp_cache = get_response_cache()
        self._page_downloader = get_passage_extractor()
        self._web_pipeline = get_web_extraction_pipeline()
        self._retrieval_policy = get_retrieval_policy()

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
        Execute the full v3 RAG pipeline.

        Args:
            query:     User query (RU or EN).
            lang:      Response language ("ru" or "en").
            use_cache: Check response cache first.

        Returns:
            PipelineAnswer with text, facts, confidence, sources, trace.
        """
        t_start = time.time()
        trace = PipelineTrace()

        def _ms():
            return (time.time() - t_start) * 1000

        # ── Stage 0: Response Cache ──────────────────────────────────────────
        if use_cache:
            cached = self._resp_cache.get(query.strip().lower())
            if cached is not None and isinstance(cached, PipelineAnswer):
                logger.info("[v3] cache hit: %s", query[:40])
                return cached
        trace.record("cache_check", _ms())

        # ── Stage 1: Query Understanding ─────────────────────────────────────
        t_stage = time.time()
        understanding = self._qu.analyze(query)
        trace.record("query_understanding", (time.time() - t_stage) * 1000)
        logger.info(
            "[v3] understanding: intent=%s, entities=%s, lang=%s, web=%s",
            understanding.intent,
            understanding.entities[:3],
            understanding.language,
            understanding.need_web_search,
        )

        # Override lang from understanding
        if understanding.language:
            lang = understanding.language

        # If no web search needed (system command, math, chat), short-circuit
        if not understanding.need_web_search:
            return self._no_search_answer(query, understanding, trace, t_start)

        # ── Stage 2: Conversation State ──────────────────────────────────────
        t_stage = time.time()
        resolved_query = self._conv_state.resolve_pronoun_subject(query)
        context_hint = self._conv_state.build_context_hint()
        trace.record("conversation_state", (time.time() - t_stage) * 1000)

        # ── Stage 3: Fact Store (cached facts) ───────────────────────────────
        t_stage = time.time()
        subject = understanding.primary_entity() or self._extract_subject(resolved_query)
        cached_facts = self._fact_store.get(subject) if subject else []
        trace.record("fact_store_check", (time.time() - t_stage) * 1000)

        # Accept cached facts only if they're high enough quality.
        # A stale entry with 1 low-confidence fact shouldn't block a
        # fresh web search that would produce much better results.
        _MIN_CACHED_FACTS = 3
        _MIN_CACHED_CONF = 0.60
        if cached_facts and len(cached_facts) >= _MIN_CACHED_FACTS:
            avg_conf = sum(f.confidence for f in cached_facts) / len(cached_facts)
            if avg_conf >= _MIN_CACHED_CONF:
                logger.info("[v3] fact store hit: %d facts (conf=%.2f) for '%s'",
                            len(cached_facts), avg_conf, subject)
                fact_set = FactSet(
                    subject=subject,
                    facts=cached_facts,
                    total_sources=len(set(s for f in cached_facts for s in f.sources)),
                    confidence=avg_conf,
                )
                answer = self._generate_and_verify(
                    resolved_query, fact_set, understanding, trace, lang,
                )
                self._finalize(query, answer, subject, fact_set, trace, t_start)
                return answer
            else:
                logger.info("[v3] fact store stale: %d facts conf=%.2f < %.2f for '%s', re-searching",
                            len(cached_facts), avg_conf, _MIN_CACHED_CONF, subject)
        elif cached_facts:
            logger.info("[v3] fact store insufficient: %d facts < %d for '%s', re-searching",
                        len(cached_facts), _MIN_CACHED_FACTS, subject)

        # ── Stage 4: Query Rewriting ─────────────────────────────────────────
        t_stage = time.time()
        try:
            plan = self._rewriter.rewrite(resolved_query)
            trace.record("query_rewrite", (time.time() - t_stage) * 1000)
        except Exception as e:
            logger.error("[v3] query rewrite failed: %s", e)
            plan = QueryPlan(original=resolved_query, queries=[resolved_query])
            trace.record("query_rewrite", (time.time() - t_stage) * 1000)
            trace.errors.append(f"rewrite: {e}")

        # ── Full search-to-answer pipeline (may retry on low quality) ────────
        answer = self._search_and_answer(
            query=resolved_query,
            plan=plan,
            understanding=understanding,
            subject=subject,
            trace=trace,
            lang=lang,
            attempt=1,
        )

        fact_set_ref = getattr(answer, "_fact_set_ref", FactSet(subject=subject))
        self._finalize(query, answer, subject, fact_set_ref, trace, t_start)
        return answer

    # ═══════════════════════════════════════════════════
    #  Search → Answer pipeline (with re-search trigger)
    # ═══════════════════════════════════════════════════

    def _search_and_answer(
        self,
        query: str,
        plan: QueryPlan,
        understanding: QueryUnderstanding,
        subject: str,
        trace: PipelineTrace,
        lang: str,
        attempt: int = 1,
    ) -> PipelineAnswer:
        """Execute search → merge → rank → extract → generate → validate."""

        tag = f"[v3:attempt{attempt}]"

        # ── Stage 5: Parallel Multi-Engine Search ────────────────────────────
        t_stage = time.time()
        try:
            engine_results = self._parallel_search.search(
                queries=plan.queries,
                max_results_per_engine=self._cfg.max_search_results,
            )
            flat_results = []
            for results_list in engine_results.values():
                flat_results.extend(results_list)
            trace.search_results_count = len(flat_results)
            trace.record(f"parallel_search_{attempt}", (time.time() - t_stage) * 1000)
            logger.info("%s search: %d raw results from %d engines",
                        tag, len(flat_results), len(engine_results))
        except Exception as e:
            logger.error("%s parallel search failed: %s", tag, e)
            flat_results = []
            trace.record(f"parallel_search_{attempt}", (time.time() - t_stage) * 1000)
            trace.errors.append(f"search_{attempt}: {e}")

        if not flat_results:
            answer = self._no_results_answer(query, trace, understanding)
            answer._fact_set_ref = FactSet(subject=subject)
            return answer

        # ── Stage 6: Result Merger (RRF + dedup) ─────────────────────────────
        t_stage = time.time()
        try:
            retrieval = self._merger.merge(engine_results)
            merged = retrieval.results
            trace.record(f"result_merger_{attempt}", (time.time() - t_stage) * 1000)
            logger.info("%s merged: %d → %d deduped", tag,
                        retrieval.total_raw, retrieval.total_deduped)
        except Exception as e:
            logger.error("%s merge failed: %s", tag, e)
            merged = flat_results
            trace.errors.append(f"merge_{attempt}: {e}")

        # ── Stage 7: Domain-based Re-ranking ─────────────────────────────────
        t_stage = time.time()
        try:
            ranked = self._domain_ranker.rank(merged, query)
            trace.record(f"domain_ranking_{attempt}", (time.time() - t_stage) * 1000)
        except Exception as e:
            logger.error("%s domain ranking failed: %s", tag, e)
            ranked = merged
            trace.errors.append(f"domain_rank_{attempt}: {e}")

        # ── Stage 8+9: Web Extraction Pipeline (v4) ────────────────────────
        # Replaces separate download/split/rank with unified deterministic pipeline:
        #   parallel download → DOM extraction → semantic chunking
        #   → BM25+embedding hybrid ranking → source trust → diversity
        t_stage = time.time()
        try:
            web_result = self._web_pipeline.run(
                ranked[:self._cfg.max_pages_to_download],
                query=query,
                top_k=self._cfg.top_k_passages,
            )
            passages = web_result.passages  # Already ranked
            top_passages = passages
            trace.passages_count = web_result.total_raw_passages
            # Store web extraction mode for adaptive thresholds
            _web_query_mode = getattr(web_result, "query_mode", None)
            _web_answered_from_kg = getattr(web_result, "answered_from_kg", False)
            trace.record(f"web_extraction_{attempt}", (time.time() - t_stage) * 1000)
            logger.info(
                "%s web extraction [%s]: %d pages → %d raw → %d ranked passages",
                tag, getattr(_web_query_mode, "value", "general"),
                web_result.total_pages_attempted,
                web_result.total_raw_passages, len(top_passages),
            )
        except Exception as e:
            logger.error("%s web extraction pipeline failed, using legacy: %s", tag, e)
            trace.errors.append(f"web_extraction_{attempt}: {e}")
            # Graceful fallback to legacy pipeline
            passages = self._download_and_split(ranked[:self._cfg.max_pages_to_download])
            if not passages:
                passages = self._snippets_to_passages(ranked)
            try:
                top_passages = self._semantic_ranker.rank(
                    passages, query,
                    top_k=self._cfg.top_k_passages,
                    min_similarity=self._cfg.min_similarity,
                )
            except Exception:
                top_passages = passages[:self._cfg.top_k_passages]
            _web_query_mode = None
            _web_answered_from_kg = False
            trace.passages_count = len(passages)
            trace.record(f"legacy_fallback_{attempt}", (time.time() - t_stage) * 1000)

        # ── Stage 10: Fact Extraction ────────────────────────────────────────
        # Fact extraction is regex-based (instant), so we extract from ALL
        # passages — not just top-ranked ones.  The semantic ranker (TF-IDF)
        # often filters out spec tables that don't "semantically" match the
        # query but contain the most valuable structured data.
        # If full-extraction yields significantly more facts, we use those.
        # Otherwise, we stick with the top_passages result.
        fact_subject = subject or (
            plan.detected_entities[0] if plan.detected_entities else query[:30]
        )

        t_stage = time.time()
        try:
            raw_facts_top = self._fact_ext.extract_from_passages(
                top_passages, subject=fact_subject,
            )
            # If top-passage extraction yields few facts, try ALL passages
            _MIN_FACTS_THRESHOLD = 5
            if len(raw_facts_top) < _MIN_FACTS_THRESHOLD and len(passages) > len(top_passages):
                raw_facts_all = self._fact_ext.extract_from_passages(
                    passages, subject=fact_subject,
                )
                if len(raw_facts_all) > len(raw_facts_top):
                    logger.info(
                        "%s fact expansion: %d (top %d) → %d (all %d passages)",
                        tag, len(raw_facts_top), len(top_passages),
                        len(raw_facts_all), len(passages),
                    )
                    raw_facts = raw_facts_all
                else:
                    raw_facts = raw_facts_top
            else:
                raw_facts = raw_facts_top
            trace.facts_extracted = len(raw_facts)
            trace.record(f"fact_extraction_{attempt}", (time.time() - t_stage) * 1000)
        except Exception as e:
            logger.error("%s fact extraction failed: %s", tag, e)
            raw_facts = []
            trace.errors.append(f"fact_ext_{attempt}: {e}")

        # ── Stage 11: Fact Aggregation ───────────────────────────────────────
        t_stage = time.time()
        try:
            fact_set = self._fact_agg.aggregate(raw_facts, subject=fact_subject)
            trace.record(f"fact_aggregation_{attempt}", (time.time() - t_stage) * 1000)
        except Exception as e:
            logger.error("%s fact aggregation failed: %s", tag, e)
            fact_set = FactSet(subject=fact_subject)
            trace.errors.append(f"fact_agg_{attempt}: {e}")

        # ── Stage 12: Fact Verification ──────────────────────────────────────
        t_stage = time.time()
        try:
            fact_set = self._fact_ver.verify(fact_set)
            trace.facts_verified = fact_set.verified_count
            trace.record(f"fact_verification_{attempt}", (time.time() - t_stage) * 1000)
        except Exception as e:
            logger.error("%s fact verification failed: %s", tag, e)
            trace.errors.append(f"fact_ver_{attempt}: {e}")

        # Cache verified facts — ONLY if quality is reasonable.
        # Caching near-zero confidence facts poisons subsequent queries
        # (follow-ups hit the fact store and return hallucinated data).
        if fact_set.facts and fact_subject and fact_set.confidence >= 0.40:
            try:
                self._fact_store.put(fact_subject, fact_set.facts)
                self._fact_store.save()
            except Exception:
                pass

        # ── Retrieval Policy: compute adaptive thresholds ────────────────────
        _intent = getattr(understanding, "intent", "") if understanding else ""
        _mode_value = getattr(_web_query_mode, "value", "general") if _web_query_mode else "general"
        _policy_decision = self._retrieval_policy.decide(
            intent=_intent,
            query_mode=_mode_value,
            answered_from_kg=_web_answered_from_kg,
        )
        logger.info(
            "%s retrieval policy: mode=%s min_facts=%d min_conf=%.2f passthrough=%s reason=%s",
            tag, _policy_decision.mode.value,
            _policy_decision.min_facts, _policy_decision.min_confidence,
            _policy_decision.allow_single_fact_passthrough, _policy_decision.reason,
        )

        # ── Stage 13–14: Generation + Self-Check ─────────────────────────────
        answer = self._generate_and_verify(
            query, fact_set, understanding, trace, lang,
            policy_decision=_policy_decision,
        )

        # ── Stage 15: Response Validation → Re-search trigger ────────────────
        if attempt <= self._MAX_RESEARCH_ATTEMPTS:
            facts_count = len(fact_set.facts) if fact_set.facts else 0
            if self._retrieval_policy.should_research(
                facts_count=facts_count,
                confidence=answer.confidence,
                has_hallucination_flags=bool(answer.hallucination_flags),
                decision=_policy_decision,
            ):
                logger.warning(
                    "%s quality too low (facts=%d, conf=%.2f, policy=%s) → re-searching",
                    tag, facts_count, answer.confidence, _policy_decision.mode.value,
                )
                trace.errors.append(f"quality_research_trigger_{attempt}")
                # Expand queries and retry with broader search
                broadened = self._broaden_queries(plan, understanding, attempt=attempt)
                return self._search_and_answer(
                    query=query,
                    plan=broadened,
                    understanding=understanding,
                    subject=fact_subject,
                    trace=trace,
                    lang=lang,
                    attempt=attempt + 1,
                )

        # ── Post-retry quality guard ─────────────────────────────────────
        # If all re-search attempts exhausted and quality still low,
        # check POLICY — not a hardcoded list of intents.
        facts_count_final = len(fact_set.facts) if fact_set.facts else 0
        still_bad = self._retrieval_policy.should_research(
            facts_count=facts_count_final,
            confidence=answer.confidence,
            has_hallucination_flags=bool(answer.hallucination_flags),
            decision=_policy_decision,
        )
        if still_bad and _policy_decision.min_facts > 0:
            # Only refuse for policies that actually require facts
            logger.warning(
                "[v3] all retries exhausted, quality still low "
                "(facts=%d, conf=%.2f, policy=%s) — refusing to hallucinate",
                facts_count_final, answer.confidence, _policy_decision.reason,
            )
            refusal = self._no_results_answer(query, trace, understanding)
            refusal._fact_set_ref = fact_set
            return refusal

        answer._fact_set_ref = fact_set
        return answer

    # ═══════════════════════════════════════════════════
    #  Generation + Self-Check
    # ═══════════════════════════════════════════════════

    def _generate_and_verify(
        self,
        query: str,
        fact_set: FactSet,
        understanding: QueryUnderstanding,
        trace: PipelineTrace,
        lang: str,
        policy_decision=None,
    ) -> PipelineAnswer:
        """Stage 13-14: fact-mode generation + self-check loop.

        Uses RetrievalPolicy for adaptive thresholds:
          STANDARD mode: 1 fact → direct passthrough (skip LLM)
          STRICT mode:   2+ facts required for LLM
          DIRECT mode:   KG answered, passthrough always

        ANTI-HALLUCINATION GATE: if fact_set is below policy threshold,
        do NOT call the LLM — return structured facts directly.
        """
        from lina.pipeline.retrieval_policy import PolicyDecision, RetrievalMode

        intent = getattr(understanding, "intent", "") if understanding else ""

        # ── Policy-aware adaptive threshold ──
        if policy_decision is None:
            # Fallback: compute policy here if not passed
            policy_decision = self._retrieval_policy.decide(intent=intent)

        facts_count = len(fact_set.facts) if fact_set.facts else 0
        min_facts = policy_decision.min_facts

        # ── GenerationGate (strict anti-hallucination for factual intents) ──
        # Only apply the generation gate for STRICT mode (Linux troubleshooting)
        # For STANDARD mode, the gate's min_verified_facts=1 matches policy
        gate_decision = self._generation_gate.evaluate(intent=intent, fact_set=fact_set)
        if not gate_decision.allow_generation:
            logger.info(
                "[v3] GENERATION GATE: intent=%s blocked (%s)",
                intent or "unknown", gate_decision.reason,
            )
            ans = PipelineAnswer(
                text=gate_decision.refusal_text or "Информация не найдена.",
                facts_used=fact_set.facts if fact_set else [],
                confidence=0.05,
                sources=list(set(s for f in (fact_set.facts if fact_set else []) for s in f.sources))[:10],
                verified=False,
            )
            ans._fact_set_ref = fact_set
            return ans

        # ── Adaptive pre-generation gate ──
        if facts_count < min_facts:
            logger.info(
                "[v3] ADAPTIVE GATE: %d facts < %d min (policy=%s) — skipping LLM",
                facts_count, min_facts, policy_decision.mode.value,
            )
            if fact_set.facts:
                text = fact_set.format_for_llm_ru() if lang == "ru" else fact_set.format_for_llm()
            else:
                text = "Информация не найдена."
            ans = PipelineAnswer(
                text=text,
                facts_used=fact_set.facts,
                confidence=fact_set.confidence * 0.5,
                sources=list(set(s for f in fact_set.facts for s in f.sources)),
                verified=False,
            )
            ans._fact_set_ref = fact_set
            return ans

        # ── Single-fact passthrough: format directly, skip LLM ──
        if (policy_decision.allow_single_fact_passthrough
                and facts_count <= 2
                and not self._llm_fn):
            text = fact_set.format_for_llm_ru() if lang == "ru" else fact_set.format_for_llm()
            ans = PipelineAnswer(
                text=text or "Информация не найдена.",
                facts_used=fact_set.facts,
                confidence=fact_set.confidence * 0.7,
                sources=list(set(s for f in fact_set.facts for s in f.sources)),
                verified=False,
            )
            ans._fact_set_ref = fact_set
            return ans

        if not self._llm_fn:
            text = fact_set.format_for_llm_ru() if lang == "ru" else fact_set.format_for_llm()
            ans = PipelineAnswer(
                text=text or "Информация не найдена.",
                facts_used=fact_set.facts,
                confidence=fact_set.confidence * 0.7,
                sources=list(set(s for f in fact_set.facts for s in f.sources)),
                verified=False,
            )
            ans._fact_set_ref = fact_set
            return ans

        # Build fact-mode prompt (v3)
        prompt = self._build_gen_prompt(
            query=query,
            fact_set=fact_set,
            lang=lang,
            max_facts=self._cfg.max_context_facts,
            understanding=understanding,
            max_prompt_chars=self._cfg.max_context_chars,
        )

        best_answer: PipelineAnswer | None = None
        max_attempts = self._cfg.max_regeneration_attempts + 1

        for attempt in range(1, max_attempts + 1):
            t_stage = time.time()
            try:
                raw_text = self._llm_fn(prompt)
                trace.record(f"llm_gen_{attempt}", (time.time() - t_stage) * 1000)
            except Exception as e:
                logger.error("[v3] LLM generation failed (attempt %d): %s", attempt, e)
                trace.errors.append(f"llm_{attempt}: {e}")
                continue

            sources = list(set(s for f in fact_set.facts for s in f.sources))
            answer = PipelineAnswer(
                text=raw_text.strip(),
                facts_used=fact_set.facts,
                confidence=fact_set.confidence,
                sources=sources[:10],
                generation_attempts=attempt,
            )
            answer._fact_set_ref = fact_set

            # Self-verification
            if self._cfg.enable_self_verification and attempt < max_attempts:
                t_sv = time.time()
                try:
                    ver_result = self._self_ver.verify(answer, fact_set, lang=lang)
                    trace.record(f"self_check_{attempt}", (time.time() - t_sv) * 1000)

                    if not ver_result.has_issues:
                        answer.verified = True
                        answer.confidence = min(1.0, answer.confidence + 0.10)
                        return answer

                    answer.hallucination_flags = (
                        ver_result.hallucinations + ver_result.mismatches
                    )
                    best_answer = answer
                    logger.warning(
                        "[v3] self-check attempt %d: %d issues",
                        attempt, len(answer.hallucination_flags),
                    )
                    continue

                except Exception as e:
                    logger.error("[v3] self-check failed: %s", e)
                    trace.errors.append(f"self_check_{attempt}: {e}")
                    answer.verified = False
                    return answer
            else:
                answer.verified = False
                return answer

        if best_answer is not None:
            best_answer.confidence = max(0.2, best_answer.confidence - 0.15)
            return best_answer

        ans = PipelineAnswer(
            text="Не удалось сгенерировать надёжный ответ.",
            confidence=0.1,
            verified=False,
        )
        ans._fact_set_ref = FactSet(subject="")
        return ans

    # ═══════════════════════════════════════════════════
    #  Page processing (download + clean + split)
    # ═══════════════════════════════════════════════════

    def _download_and_split(self, results: List[SearchResult]) -> List[Passage]:
        """Download pages, clean HTML, split into typed passages."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not results:
            return []

        all_passages: List[Passage] = []

        def _process_one(r: SearchResult) -> List[Passage]:
            try:
                html = self._fetch_html(r.url)
                if not html:
                    return []
                clean_text = self._clean_page(
                    html, max_length=self._cfg.max_page_chars,
                )
                if not clean_text or len(clean_text) < 50:
                    return []
                return self._splitter.split(
                    clean_text,
                    source_url=r.url,
                    source_title=r.title,
                )
            except Exception as e:
                logger.debug("[v3] page process failed %s: %s", r.url[:50], e)
                return []

        timeout = self._cfg.page_download_timeout_sec
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_process_one, r): r for r in results}
            for f in as_completed(futures, timeout=timeout + 10):
                try:
                    all_passages.extend(f.result(timeout=timeout + 2))
                except Exception:
                    pass

        return all_passages

    def _fetch_html(self, url: str) -> str:
        """Fetch raw HTML from URL."""
        from lina.utils.http import http_get
        try:
            timeout = int(self._cfg.page_download_timeout_sec)
            return http_get(
                url, timeout=timeout,
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                    "Gecko/20100101 Firefox/128.0"
                ),
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
        except Exception:
            return ""

    # ═══════════════════════════════════════════════════
    #  Re-search logic
    # ═══════════════════════════════════════════════════

    def _should_research(self, answer: PipelineAnswer, fact_set: FactSet) -> bool:
        """Check if answer quality is too low and re-search is warranted.

        DEPRECATED: Use self._retrieval_policy.should_research() directly
        with a PolicyDecision for adaptive thresholds.
        This method is kept for backward compatibility only.
        """
        from lina.pipeline.retrieval_policy import get_retrieval_policy

        facts_count = len(fact_set.facts) if fact_set.facts else 0
        # Use STANDARD policy as default (1 fact minimum)
        retrieval_policy = getattr(self, "_retrieval_policy", None) or get_retrieval_policy()
        decision = retrieval_policy.decide(intent="web_search")
        return retrieval_policy.should_research(
            facts_count=facts_count,
            confidence=answer.confidence,
            has_hallucination_flags=bool(answer.hallucination_flags),
            decision=decision,
        )

    def _broaden_queries(
        self,
        plan: QueryPlan,
        understanding: QueryUnderstanding,
        attempt: int = 1,
    ) -> QueryPlan:
        """Expand query plan with broader variants for re-search.

        On second+ retry, uses the LLM (if available) to generate focused
        follow-up queries — "iterative retrieval" pattern.
        """
        entity = understanding.primary_entity() or plan.original
        attrs = understanding.attributes[:2] if understanding.attributes else []

        new_queries = list(plan.queries)

        # ── Static broadening (always applied) ──
        new_queries.append(f"{entity} specifications")
        new_queries.append(f"{entity} review 2024")
        if attrs:
            for attr in attrs:
                new_queries.append(f"{entity} {attr}")

        # ── LLM-guided follow-up (attempt ≥ 2 and LLM available) ──
        if attempt >= 2 and self._llm_fn:
            try:
                follow_up_prompt = (
                    f"The user asked: \"{plan.original}\"\n"
                    f"Entity: {entity}\n"
                    f"Previous search queries didn't find enough information.\n"
                    f"Suggest 3 alternative search queries (one per line, no numbering):"
                )
                raw = self._llm_fn(follow_up_prompt)
                for line in raw.strip().splitlines()[:3]:
                    line = line.strip().lstrip("0123456789.-) ")
                    if line and len(line) > 5:
                        new_queries.append(line)
                logger.info("[v3] LLM-guided follow-up queries: %d generated", len(raw.strip().splitlines()))
            except Exception as e:
                logger.debug("[v3] LLM follow-up query generation failed: %s", e)

        # ── Additional broader patterns for late retries ──
        if attempt >= 2:
            new_queries.append(f"{entity} характеристики полные")
            new_queries.append(f"\"{entity}\" site:gsmarena.com OR site:kimovil.com")

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for q in new_queries:
            ql = q.strip().lower()
            if ql not in seen:
                seen.add(ql)
                unique.append(q)

        broadened = QueryPlan(
            original=plan.original,
            queries=unique[:10],
            detected_entities=plan.detected_entities,
            detected_intent=plan.detected_intent,
        )
        return broadened

    # ═══════════════════════════════════════════════════
    #  Finalize
    # ═══════════════════════════════════════════════════

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
            "[v3] complete: conf=%.2f, verified=%s, facts=%s, "
            "sources=%d, elapsed=%.0f ms, errors=%d",
            answer.confidence, answer.verified,
            len(answer.facts_used) if isinstance(answer.facts_used, list) else answer.facts_used,
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
                facts=fact_set.facts[:10] if fact_set.facts else [],
            )
            self._conv_state.add_turn(turn)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════
    #  Helpers
    # ═══════════════════════════════════════════════════

    def _extract_subject(self, query: str) -> str:
        """Extract main entity from query."""
        try:
            from lina.core.entity_parser import get_entity_parser
            parsed = get_entity_parser().parse(query)
            if parsed.entities:
                for e in parsed.entities:
                    if hasattr(e, "value"):
                        return e.value
                    if hasattr(e, "name"):
                        return e.name
        except Exception:
            pass
        return ""

    def _no_search_answer(
        self,
        query: str,
        understanding: QueryUnderstanding,
        trace: PipelineTrace,
        t_start: float,
    ) -> PipelineAnswer:
        """Answer for queries that don't need web search.

        - system_command / math → raise V3BypassSignal so caller delegates
          to legacy command handling (Commander builtins, MiniLLM, etc.)
        - chat / other → use LLM to generate a response.
        """
        elapsed = (time.time() - t_start) * 1000

        # System commands and math must be handled by the shell layer,
        # not by the RAG pipeline.  Signal bypass.
        if understanding.intent in ("system_command", "math"):
            raise V3BypassSignal(query, understanding.intent)

        # For chat / general questions without web — ask LLM directly
        if self._llm_fn:
            try:
                prompt = (
                    f"Ответь на вопрос пользователя кратко и по существу.\n"
                    f"Вопрос: {query}\nОтвет:"
                )
                text = self._llm_fn(prompt)
                trace.record("llm_no_search", (time.time() - t_start) * 1000 - elapsed)
                return PipelineAnswer(
                    text=text.strip() if text else "Привет! Чем могу помочь?",
                    facts_used=[],
                    confidence=0.5,
                    verified=False,
                    elapsed_ms=(time.time() - t_start) * 1000,
                )
            except Exception as e:
                logger.warning("[v3] LLM no-search answer failed: %s", e)

        return PipelineAnswer(
            text="Привет! Чем могу помочь?",
            facts_used=[],
            confidence=0.5,
            verified=False,
            elapsed_ms=elapsed,
        )

    def _no_results_answer(
        self,
        query: str,
        trace: PipelineTrace,
        understanding: Optional[QueryUnderstanding] = None,
    ) -> PipelineAnswer:
        """Fallback when search returns nothing.

        For factual queries (product_spec, hardware, price) — refuse to
        answer rather than hallucinate.  A mini model (2048 ctx) will
        invent wrong specs if asked to answer from "own knowledge".

        For chat/general queries — LLM fallback is acceptable.
        """
        intent = getattr(understanding, "intent", "") if understanding else ""
        is_factual = self._generation_gate.is_factual(intent)

        if is_factual:
            logger.info("[v3] no search results for factual query (intent=%s) "
                        "— refusing to hallucinate", intent)
            return PipelineAnswer(
                text="К сожалению, не удалось найти информацию в интернете. "
                     "Попробуйте повторить запрос позже или переформулировать вопрос.",
                facts_used=[],
                confidence=0.05,
                verified=False,
            )

        # Non-factual queries: LLM fallback is OK
        if self._llm_fn:
            try:
                prompt = (
                    "Не удалось найти информацию в интернете. "
                    "Ответь на вопрос пользователя на основе своих знаний. "
                    "Если не уверен, честно скажи об этом.\n"
                    f"Вопрос: {query}\nОтвет:"
                )
                t = time.time()
                raw = self._llm_fn(prompt)
                trace.record("llm_no_results_fallback", (time.time() - t) * 1000)
                text = (raw or "").strip()
                if text and len(text) > 10:
                    logger.info("[v3] no search results — LLM answered from own knowledge")
                    return PipelineAnswer(
                        text=text,
                        facts_used=[],
                        confidence=0.20,
                        verified=False,
                    )
            except Exception as e:
                logger.warning("[v3] LLM no-results fallback failed: %s", e)
                trace.errors.append(f"llm_no_results: {e}")

        return PipelineAnswer(
            text="К сожалению, не удалось найти информацию в интернете "
                 "и языковая модель не смогла ответить. "
                 "Попробуйте переформулировать вопрос.",
            facts_used=[],
            confidence=0.05,
            verified=False,
        )

    @staticmethod
    def _snippets_to_passages(results: List[SearchResult]) -> List[Passage]:
        """Convert search result snippets to minimal passages.

        When page downloads fail, snippets are the only data source.
        Combine title + snippet for richer text, and merge all snippets
        into one synthetic passage to help fact extraction see the full
        picture (individual snippets may be too short for regex patterns).
        """
        passages = []
        combined_parts = []
        for r in results[:10]:
            text = r.snippet or r.title
            if text and len(text) > 10:
                # Individual passage (original behaviour)
                full_text = f"{r.title}. {r.snippet}" if r.snippet else r.title
                passages.append(Passage(
                    text=full_text,
                    source_url=r.url,
                    source_title=r.title,
                    score=r.relevance,
                ))
                combined_parts.append(full_text)

        # Add a synthetic merged passage — combines ALL snippets so the fact
        # extractor can cross-reference partial specs from different sources.
        if len(combined_parts) >= 2:
            merged = "\n".join(combined_parts)
            passages.insert(0, Passage(
                text=merged,
                source_url=results[0].url if results else "",
                source_title="merged_snippets",
                score=max((r.relevance for r in results[:10]), default=0.5),
            ))
        return passages


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_pipeline: PipelineV3 | None = None


def get_pipeline_v3(llm_fn=None) -> PipelineV3:
    """
    Get or create the singleton v3 pipeline.

    Args:
        llm_fn: LLM generation function.  Required on first call.
    """
    global _pipeline
    if _pipeline is None:
        _pipeline = PipelineV3(llm_fn=llm_fn)
    return _pipeline
