# -*- coding: utf-8 -*-
"""
Tests for Lina v3 Pipeline modules.

Covers:
  - v3 datatypes (QueryUnderstanding, RetrievalResult)
  - core/query_understanding
  - retrieval/parallel_search (mocked engines)
  - retrieval/result_merger
  - retrieval/domain_ranker
  - processing/html_cleaner (delegate)
  - processing/passage_splitter (delegate)
  - embeddings/embedding_model
  - embeddings/semantic_ranker
  - knowledge/fact_extractor (delegate)
  - knowledge/fact_aggregator (delegate)
  - knowledge/fact_verifier (delegate)
  - knowledge/fact_store (delegate)
  - llm/fact_prompt
  - llm/self_check (delegate)
  - pipeline/pipeline_v3 (mocked search + LLM)
"""

import pytest
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from lina.models.datatypes import (
    SearchResult, Passage, Fact, FactSet, PipelineAnswer,
    PipelineTrace, QueryPlan,
)
from lina.pipeline.pipeline_v3 import V3BypassSignal


# ═══════════════════════════════════════════════════
#  1. v3 Datatypes
# ═══════════════════════════════════════════════════

from lina.models.datatypes import QueryUnderstanding, RetrievalResult


class TestQueryUnderstanding:
    def test_defaults(self):
        qu = QueryUnderstanding(raw_query="test")
        assert qu.raw_query == "test"
        assert qu.intent == ""
        assert qu.entities == []
        assert qu.language == "ru"
        assert qu.need_web_search is True
        assert qu.confidence == 0.0

    def test_primary_entity(self):
        qu = QueryUnderstanding(
            raw_query="test",
            entities=["Realme 10", "Samsung"],
        )
        assert qu.primary_entity() == "Realme 10"

    def test_primary_entity_empty(self):
        qu = QueryUnderstanding(raw_query="test")
        assert qu.primary_entity() == ""

    def test_primary_attribute(self):
        qu = QueryUnderstanding(
            raw_query="test",
            attributes=["processor", "ram"],
        )
        assert qu.primary_attribute() == "processor"

    def test_primary_attribute_empty(self):
        qu = QueryUnderstanding(raw_query="test")
        assert qu.primary_attribute() == ""


class TestRetrievalResult:
    def test_defaults(self):
        rr = RetrievalResult()
        assert rr.results == []
        assert rr.engines_used == []
        assert rr.total_raw == 0

    def test_top(self):
        results = [
            SearchResult(title=f"R{i}", url=f"https://r{i}.com", snippet=f"S{i}")
            for i in range(10)
        ]
        rr = RetrievalResult(results=results)
        top5 = rr.top(5)
        assert len(top5) == 5
        assert top5[0].title == "R0"


# ═══════════════════════════════════════════════════
#  2. Query Understanding
# ═══════════════════════════════════════════════════

from lina.core.query_understanding import QueryUnderstandingEngine, get_query_understanding


class TestQueryUnderstandingEngine:
    def setup_method(self):
        self.engine = QueryUnderstandingEngine()

    def test_analyze_ru(self):
        result = self.engine.analyze("какой процессор у Realme 10")
        assert isinstance(result, QueryUnderstanding)
        assert result.language == "ru"
        assert result.need_web_search is True

    def test_analyze_en(self):
        result = self.engine.analyze("what is the processor of Realme 10")
        assert result.language == "en"

    def test_analyze_math(self):
        result = self.engine.analyze("2 + 2 = ?")
        assert result.intent == "math"
        assert result.need_web_search is False

    def test_analyze_system(self):
        result = self.engine.analyze("выключи компьютер")
        assert result.intent == "system_command"
        assert result.need_web_search is False

    def test_analyze_chat(self):
        result = self.engine.analyze("привет как дела")
        assert result.intent == "chat"
        assert result.need_web_search is False

    def test_analyze_attributes(self):
        result = self.engine.analyze("сколько оперативной памяти у Samsung Galaxy S24")
        assert result.attributes  # should detect RAM-related attribute

    def test_analyze_comparison(self):
        result = self.engine.analyze("realme 10 vs samsung galaxy a15")
        assert result.intent == "comparison"

    def test_analyze_price(self):
        result = self.engine.analyze("цена Realme 10")
        assert result.intent == "price"

    def test_analyze_empty(self):
        result = self.engine.analyze("")
        assert result.raw_query == ""
        assert isinstance(result, QueryUnderstanding)

    def test_singleton(self):
        a = get_query_understanding()
        b = get_query_understanding()
        assert a is b


# ═══════════════════════════════════════════════════
#  3. Parallel Search (mocked)
# ═══════════════════════════════════════════════════

from lina.retrieval.parallel_search import (
    ParallelSearch,
    DuckDuckGoEngine,
    WikipediaEngine,
    SearchEngine,
    get_parallel_search,
)


class TestSearchEngineInterface:
    def test_duckduckgo_is_search_engine(self):
        assert issubclass(DuckDuckGoEngine, SearchEngine)

    def test_wikipedia_is_search_engine(self):
        assert issubclass(WikipediaEngine, SearchEngine)


class TestParallelSearch:
    def test_init(self):
        ps = ParallelSearch()
        assert ps._engines  # at least DDG exists

    @patch.object(DuckDuckGoEngine, "search")
    def test_search_returns_dict(self, mock_ddg):
        mock_ddg.return_value = [
            SearchResult(title="Test", url="https://test.com", snippet="desc"),
        ]
        ps = ParallelSearch()
        # Replace engines with only mocked DDG
        ps._engines = [DuckDuckGoEngine()]
        result = ps.search(queries=["test query"], max_results_per_engine=5)
        assert isinstance(result, dict)

    def test_singleton(self):
        a = get_parallel_search()
        b = get_parallel_search()
        assert a is b


# ═══════════════════════════════════════════════════
#  4. Result Merger
# ═══════════════════════════════════════════════════

from lina.retrieval.result_merger import ResultMerger, get_result_merger


class TestResultMerger:
    def setup_method(self):
        self.merger = ResultMerger()

    def test_merge_empty(self):
        result = self.merger.merge({})
        assert isinstance(result, RetrievalResult)
        assert len(result.results) == 0

    def test_merge_single_engine(self):
        results = {
            "ddg": [
                SearchResult(title="R1", url="https://a.com", snippet="s1", relevance=0.9),
                SearchResult(title="R2", url="https://b.com", snippet="s2", relevance=0.8),
            ]
        }
        merged = self.merger.merge(results)
        assert len(merged.results) == 2
        assert merged.engines_used == ["ddg"]
        assert merged.total_raw == 2

    def test_merge_dedup(self):
        """Duplicate URLs across engines should be deduped."""
        results = {
            "ddg": [
                SearchResult(title="R1", url="https://example.com/page", snippet="s1"),
            ],
            "wiki": [
                SearchResult(title="R1w", url="https://example.com/page", snippet="s1w"),
            ],
        }
        merged = self.merger.merge(results)
        assert merged.total_deduped <= merged.total_raw

    def test_merge_rrf_boosts_multi_engine(self):
        """Results appearing in multiple engines should get RRF boost."""
        results = {
            "ddg": [
                SearchResult(title="Good", url="https://good.com", snippet="g"),
                SearchResult(title="Only DDG", url="https://only-ddg.com", snippet="d"),
            ],
            "wiki": [
                SearchResult(title="Good", url="https://good.com", snippet="g"),
                SearchResult(title="Only Wiki", url="https://only-wiki.com", snippet="w"),
            ],
        }
        merged = self.merger.merge(results)
        # good.com should be first (appeared in both)
        assert merged.results[0].url in ("https://good.com",)

    def test_spam_filtering(self):
        """Spam domains should be filtered out."""
        results = {
            "ddg": [
                SearchResult(title="Good", url="https://wikipedia.org/Foo", snippet="g"),
                SearchResult(title="Spam", url="https://pinterest.com/spam", snippet="s"),
            ],
        }
        merged = self.merger.merge(results)
        urls = [r.url for r in merged.results]
        assert not any("pinterest" in u for u in urls)

    def test_singleton(self):
        a = get_result_merger()
        b = get_result_merger()
        assert a is b


# ═══════════════════════════════════════════════════
#  5. Domain Ranker
# ═══════════════════════════════════════════════════

from lina.retrieval.domain_ranker import DomainRanker, get_domain_ranker


class TestDomainRanker:
    def setup_method(self):
        self.ranker = DomainRanker()

    def test_rank_preserves_results(self):
        results = [
            SearchResult(title="A", url="https://unknown.com/a", snippet="a", relevance=0.5),
            SearchResult(title="B", url="https://gsmarena.com/b", snippet="b", relevance=0.5),
        ]
        ranked = self.ranker.rank(results, "test query")
        assert len(ranked) == 2

    def test_rank_boosts_authoritative(self):
        """GSMArena (0.95 rep) should rank above unknown domain."""
        results = [
            SearchResult(title="Unknown", url="https://random-blog.xyz/a", snippet="a", relevance=0.5),
            SearchResult(title="GSM", url="https://gsmarena.com/b", snippet="b", relevance=0.5),
        ]
        ranked = self.ranker.rank(results, "realme 10 specs")
        # gsmarena should be first or near first
        assert ranked[0].url == "https://gsmarena.com/b"

    def test_rank_empty(self):
        assert self.ranker.rank([], "q") == []

    def test_singleton(self):
        a = get_domain_ranker()
        b = get_domain_ranker()
        assert a is b


# ═══════════════════════════════════════════════════
#  6. Processing — HTML Cleaner (delegate)
# ═══════════════════════════════════════════════════

from lina.processing.html_cleaner import clean_html, extract_title, clean_page


class TestProcessingHtmlCleaner:
    def test_clean_html_delegate(self):
        """Should re-export the v2 clean_html function."""
        result = clean_html("<p>Hello <b>world</b></p>")
        assert "Hello" in result
        assert "world" in result

    def test_extract_title(self):
        html = "<html><head><title>My Title</title></head><body></body></html>"
        assert "My Title" in extract_title(html)

    def test_clean_page(self):
        html = "<html><body><p>Main content here with enough text for testing.</p></body></html>"
        result = clean_page(html)
        assert "Main content" in result

    def test_clean_page_boilerplate_removal(self):
        html = """<html><body>
        <p>Main useful content for the user.</p>
        <p>We use cookies to improve your experience.</p>
        <p>© 2024 All rights reserved</p>
        </body></html>"""
        result = clean_page(html)
        # Boilerplate should be reduced/removed
        assert "Main useful content" in result


# ═══════════════════════════════════════════════════
#  7. Processing — Passage Splitter (delegate)
# ═══════════════════════════════════════════════════

from lina.processing.passage_splitter import (
    split_into_passages as v3_split,
    PassageSplitter,
    get_passage_splitter,
)


class TestPassageSplitter:
    def test_delegate_split(self):
        """V3 re-export should work."""
        text = "Sentence one is here. Sentence two follows. And third sentence."
        result = v3_split(text)
        assert isinstance(result, list)

    def test_passage_splitter_class(self):
        splitter = PassageSplitter()
        text = ("This is a first passage with enough words to be meaningful. " * 5 +
                "Second passage also has content. " * 5)
        passages = splitter.split(text, source_url="https://test.com", source_title="Test")
        assert all(isinstance(p, Passage) for p in passages)
        if passages:
            assert passages[0].source_url == "https://test.com"

    def test_singleton(self):
        a = get_passage_splitter()
        b = get_passage_splitter()
        assert a is b


# ═══════════════════════════════════════════════════
#  8. Embeddings — Embedding Model
# ═══════════════════════════════════════════════════

from lina.embeddings.embedding_model import EmbeddingModel, get_embedding_model


class TestEmbeddingModel:
    def test_init(self):
        model = EmbeddingModel()
        assert model._backend in ("sentence_transformers", "sklearn", "bm25", "python", None)

    def test_similarity(self):
        model = EmbeddingModel()
        scores = model.similarity("processor specs", ["CPU info", "weather today"])
        assert len(scores) == 2
        assert all(isinstance(s, float) for s in scores)

    def test_similarity_empty(self):
        model = EmbeddingModel()
        scores = model.similarity("query", [])
        assert scores == []

    def test_singleton(self):
        a = get_embedding_model()
        b = get_embedding_model()
        assert a is b


# ═══════════════════════════════════════════════════
#  9. Embeddings — Semantic Ranker
# ═══════════════════════════════════════════════════

from lina.embeddings.semantic_ranker import SemanticRanker, get_semantic_ranker


class TestSemanticRanker:
    def test_rank_empty(self):
        ranker = SemanticRanker()
        assert ranker.rank([], "query") == []

    def test_rank_returns_passages(self):
        ranker = SemanticRanker()
        passages = [
            Passage(text="Realme 10 has MediaTek Helio G99 processor",
                   source_url="https://a.com"),
            Passage(text="Weather report for today shows sunny skies",
                   source_url="https://b.com"),
        ]
        ranked = ranker.rank(passages, "realme 10 processor", top_k=2)
        assert all(isinstance(p, Passage) for p in ranked)
        assert len(ranked) <= 2

    def test_singleton(self):
        a = get_semantic_ranker()
        b = get_semantic_ranker()
        assert a is b


# ═══════════════════════════════════════════════════
# 10. Knowledge — Delegates
# ═══════════════════════════════════════════════════

from lina.knowledge.fact_extractor import (
    FactExtractor as KnFE,
    get_fact_extractor as kn_get_fe,
)
from lina.knowledge.fact_aggregator import (
    FactAggregator as KnFA,
    get_fact_aggregator as kn_get_fa,
)
from lina.knowledge.fact_verifier import (
    FactVerifier as KnFV,
    get_fact_verifier as kn_get_fv,
)
from lina.knowledge.fact_store import (
    FactStore as KnFS,
    get_fact_store as kn_get_fs,
)


class TestKnowledgeDelegates:
    def test_fact_extractor_import(self):
        """Knowledge layer should re-export core FactExtractor."""
        from lina.core.fact_extractor import FactExtractor as CoreFE
        assert KnFE is CoreFE

    def test_fact_aggregator_import(self):
        from lina.core.fact_aggregator import FactAggregator as CoreFA
        assert KnFA is CoreFA

    def test_fact_verifier_import(self):
        from lina.core.fact_verifier import FactVerifier as CoreFV
        assert KnFV is CoreFV

    def test_fact_store_import(self):
        from lina.memory.fact_store import FactStore as MemFS
        assert KnFS is MemFS

    def test_fact_extractor_singleton(self):
        fe = kn_get_fe()
        assert isinstance(fe, KnFE)

    def test_fact_aggregator_singleton(self):
        fa = kn_get_fa()
        assert isinstance(fa, KnFA)

    def test_fact_verifier_singleton(self):
        fv = kn_get_fv()
        assert isinstance(fv, KnFV)


# ═══════════════════════════════════════════════════
# 11. LLM — fact_prompt
# ═══════════════════════════════════════════════════

from lina.llm.fact_prompt import build_generation_prompt, build_verification_prompt


class TestFactPrompt:
    def setup_method(self):
        self.facts = [
            Fact(subject="Realme 10", predicate="processor",
                 object_value="MediaTek Helio G99",
                 sources=["https://gsmarena.com"], source_count=2,
                 confidence=0.9, verified=True),
            Fact(subject="Realme 10", predicate="ram",
                 object_value="8 GB",
                 sources=["https://gsmarena.com"], source_count=1,
                 confidence=0.85, verified=True),
        ]
        self.fact_set = FactSet(
            subject="Realme 10",
            facts=self.facts,
            total_sources=1,
            confidence=0.87,
        )

    def test_build_generation_prompt_ru(self):
        prompt = build_generation_prompt("процессор Realme 10", self.fact_set, lang="ru")
        assert "MediaTek Helio G99" in prompt
        assert "ФАКТЫ" in prompt
        assert "ВОПРОС" in prompt

    def test_build_generation_prompt_en(self):
        prompt = build_generation_prompt("Realme 10 processor", self.fact_set, lang="en")
        assert "MediaTek Helio G99" in prompt
        assert "FACTS" in prompt
        assert "QUESTION" in prompt

    def test_build_verification_prompt_ru(self):
        prompt = build_verification_prompt(
            "Realme 10 имеет процессор Helio G99",
            self.fact_set, lang="ru",
        )
        assert "ОТВЕТ" in prompt
        assert "ФАКТЫ" in prompt
        assert "hallucination" in prompt.lower() or "галлюцинац" in prompt.lower()

    def test_build_generation_with_understanding(self):
        qu = QueryUnderstanding(
            raw_query="процессор Realme 10",
            entities=["Realme 10"],
            attributes=["processor"],
        )
        prompt = build_generation_prompt(
            "процессор Realme 10", self.fact_set, lang="ru",
            understanding=qu,
        )
        assert "Realme 10" in prompt

    def test_empty_facts(self):
        empty_fs = FactSet(subject="test")
        prompt = build_generation_prompt("test", empty_fs)
        assert "нет фактов" in prompt.lower() or "no facts" in prompt.lower()


# ═══════════════════════════════════════════════════
# 12. LLM — self_check (delegate)
# ═══════════════════════════════════════════════════

from lina.llm.self_check import SelfVerifier, VerificationResult, get_self_verifier


class TestSelfCheckDelegate:
    def test_import_identity(self):
        from lina.llm.self_verifier import SelfVerifier as OrigSV
        assert SelfVerifier is OrigSV

    def test_verification_result_exists(self):
        vr = VerificationResult()
        assert vr.is_faithful is True
        assert not vr.has_issues

    def test_no_llm_passes(self):
        """Without LLM fn, verification should pass through."""
        sv = SelfVerifier(llm_fn=None)
        answer = PipelineAnswer(text="answer", confidence=0.8)
        fs = FactSet(subject="test", facts=[])
        result = sv.verify(answer, fs)
        assert isinstance(result, VerificationResult)


# ═══════════════════════════════════════════════════
# 13. Pipeline V3 (integration with mocks)
# ═══════════════════════════════════════════════════

from lina.pipeline.pipeline_v3 import PipelineV3, get_pipeline_v3


class TestPipelineV3Init:
    def test_init_no_llm(self):
        """Pipeline should initialize without LLM."""
        p = PipelineV3(llm_fn=None)
        assert p._llm_fn is None

    def test_init_with_mock_llm(self):
        mock_llm = MagicMock(return_value="test answer")
        p = PipelineV3(llm_fn=mock_llm)
        assert p._llm_fn is mock_llm


class TestPipelineV3NoSearch:
    """Test queries that don't require web search."""

    def setup_method(self):
        self.pipeline = PipelineV3(llm_fn=None)

    def test_math_query_raises_bypass(self):
        """Math queries should raise V3BypassSignal for legacy handling."""
        with pytest.raises(V3BypassSignal) as exc_info:
            self.pipeline.run("2 + 2 = ?")
        assert exc_info.value.intent == "math"

    def test_chat_query(self):
        answer = self.pipeline.run("привет")
        assert isinstance(answer, PipelineAnswer)

    def test_system_command_raises_bypass(self):
        """System commands should raise V3BypassSignal for legacy handling."""
        with pytest.raises(V3BypassSignal) as exc_info:
            self.pipeline.run("выключи компьютер")
        assert exc_info.value.intent == "system_command"


class TestPipelineV3Mocked:
    """Full pipeline with mocked search."""

    def setup_method(self):
        self.mock_llm = MagicMock(return_value="Realme 10 has Helio G99 processor.")
        self.pipeline = PipelineV3(llm_fn=self.mock_llm)

    @patch("lina.retrieval.parallel_search.ParallelSearch.search")
    def test_run_with_mock_search(self, mock_search):
        """Full pipeline run with mocked search results."""
        mock_search.return_value = {
            "ddg": [
                SearchResult(
                    title="Realme 10 Specs",
                    url="https://gsmarena.com/realme10",
                    snippet="Realme 10 uses MediaTek Helio G99, 8GB RAM, 128GB storage",
                    relevance=0.9,
                ),
                SearchResult(
                    title="Realme 10 Review",
                    url="https://kimovil.com/realme10",
                    snippet="The Realme 10 features a 6.4 inch AMOLED display",
                    relevance=0.8,
                ),
            ],
        }

        answer = self.pipeline.run("какой процессор у Realme 10")
        assert isinstance(answer, PipelineAnswer)
        assert answer.confidence >= 0

    @patch("lina.retrieval.parallel_search.ParallelSearch.search")
    def test_run_empty_search(self, mock_search):
        """Pipeline should handle empty search gracefully."""
        mock_search.return_value = {"ddg": []}
        answer = self.pipeline.run("realme 10 processor")
        assert isinstance(answer, PipelineAnswer)

    def test_no_results_answer(self):
        """When search returns nothing but LLM is available, LLM should answer."""
        trace = PipelineTrace()
        answer = self.pipeline._no_results_answer("test", trace)
        assert isinstance(answer, PipelineAnswer)
        # With llm_fn present, should get an LLM response (not the static error)
        assert len(answer.text) > 5
        assert answer.confidence <= 0.25  # low confidence since no web results

    def test_no_results_answer_without_llm(self):
        """When search returns nothing and no LLM, should give static error."""
        pipeline = PipelineV3(llm_fn=None)
        trace = PipelineTrace()
        answer = pipeline._no_results_answer("test", trace)
        assert "не удалось" in answer.text.lower() or answer.confidence < 0.1

    def test_snippets_to_passages(self):
        results = [
            SearchResult(title="T", url="https://a.com", snippet="Some snippet text here"),
        ]
        passages = PipelineV3._snippets_to_passages(results)
        assert len(passages) == 1
        assert passages[0].source_url == "https://a.com"

    def test_generation_gate_blocks_llm_for_factual_without_verified(self):
        """For factual intent, LLM must not be called when verified facts are absent."""
        trace = PipelineTrace()
        understanding = QueryUnderstanding(raw_query="realme 10 processor", intent="web_search")
        fact_set = FactSet(
            subject="Realme 10",
            facts=[
                Fact(
                    subject="Realme 10",
                    predicate="processor",
                    object_value="Helio G99",
                    sources=["https://example.com"],
                    source_count=1,
                    confidence=0.9,
                    verified=False,
                )
            ],
            confidence=0.9,
        )

        answer = self.pipeline._generate_and_verify(
            query="какой процессор у Realme 10",
            fact_set=fact_set,
            understanding=understanding,
            trace=trace,
            lang="ru",
        )

        assert isinstance(answer, PipelineAnswer)
        assert self.mock_llm.call_count == 0
        assert "верифиц" in answer.text.lower() or "недостаточно" in answer.text.lower()


class TestPipelineV3ResearchTrigger:
    """Test the response validation + re-search logic."""

    def setup_method(self):
        self.pipeline = PipelineV3(llm_fn=None)

    def test_should_research_low_facts(self):
        answer = PipelineAnswer(text="test", confidence=0.8)
        fact_set = FactSet(subject="test", facts=[])  # 0 facts
        assert self.pipeline._should_research(answer, fact_set) is True

    def test_should_research_low_confidence(self):
        facts = [
            Fact(subject="T", predicate="p", object_value="v",
                 sources=["s"], source_count=1, confidence=0.2),
        ] * 3
        answer = PipelineAnswer(text="test", confidence=0.1)
        fact_set = FactSet(subject="test", facts=facts)
        assert self.pipeline._should_research(answer, fact_set) is True

    def test_no_research_good_answer(self):
        facts = [
            Fact(subject="T", predicate="p", object_value="v",
                 sources=["s"], source_count=2, confidence=0.8, verified=True),
        ] * 5
        answer = PipelineAnswer(text="good answer", confidence=0.7)
        fact_set = FactSet(subject="test", facts=facts)
        assert self.pipeline._should_research(answer, fact_set) is False

    def test_broaden_queries(self):
        qu = QueryUnderstanding(
            raw_query="realme 10 processor",
            entities=["Realme 10"],
            attributes=["processor"],
        )
        plan = QueryPlan(
            original="realme 10 processor",
            queries=["realme 10 processor"],
            detected_entities=["Realme 10"],
        )
        broadened = self.pipeline._broaden_queries(plan, qu)
        assert len(broadened.queries) > len(plan.queries)


# ═══════════════════════════════════════════════════
# 14. Cross-layer imports (smoke tests)
# ═══════════════════════════════════════════════════

class TestCrossLayerImports:
    """Verify that all v3 modules can be imported without error."""

    def test_import_query_understanding(self):
        from lina.core.query_understanding import QueryUnderstandingEngine
        assert QueryUnderstandingEngine is not None

    def test_import_parallel_search(self):
        from lina.retrieval.parallel_search import ParallelSearch
        assert ParallelSearch is not None

    def test_import_result_merger(self):
        from lina.retrieval.result_merger import ResultMerger
        assert ResultMerger is not None

    def test_import_domain_ranker(self):
        from lina.retrieval.domain_ranker import DomainRanker
        assert DomainRanker is not None

    def test_import_html_cleaner_v3(self):
        from lina.processing.html_cleaner import clean_page
        assert callable(clean_page)

    def test_import_passage_splitter_v3(self):
        from lina.processing.passage_splitter import PassageSplitter
        assert PassageSplitter is not None

    def test_import_embedding_model(self):
        from lina.embeddings.embedding_model import EmbeddingModel
        assert EmbeddingModel is not None

    def test_import_semantic_ranker(self):
        from lina.embeddings.semantic_ranker import SemanticRanker
        assert SemanticRanker is not None

    def test_import_knowledge_fact_extractor(self):
        from lina.knowledge.fact_extractor import FactExtractor
        assert FactExtractor is not None

    def test_import_knowledge_fact_aggregator(self):
        from lina.knowledge.fact_aggregator import FactAggregator
        assert FactAggregator is not None

    def test_import_knowledge_fact_verifier(self):
        from lina.knowledge.fact_verifier import FactVerifier
        assert FactVerifier is not None

    def test_import_knowledge_fact_store(self):
        from lina.knowledge.fact_store import FactStore
        assert FactStore is not None

    def test_import_fact_prompt(self):
        from lina.llm.fact_prompt import build_generation_prompt
        assert callable(build_generation_prompt)

    def test_import_self_check(self):
        from lina.llm.self_check import SelfVerifier
        assert SelfVerifier is not None

    def test_import_pipeline_v3(self):
        from lina.pipeline.pipeline_v3 import PipelineV3
        assert PipelineV3 is not None


# ═══════════════════════════════════════════════════
# 15. Config + CLI wiring
# ═══════════════════════════════════════════════════


class TestPipelineVersionRemoved:
    """Verify pipeline_version was removed in Phase 28 cleanup."""

    def test_config_no_pipeline_version(self):
        from lina.config import LinaConfig
        cfg = LinaConfig()
        assert not hasattr(cfg.pipeline, 'pipeline_version')

    def test_cli_no_pipeline_v3_flag(self):
        from lina.core.cli import LinaArgs
        assert not hasattr(LinaArgs, 'pipeline_v3') or not LinaArgs.__dataclass_fields__.get('pipeline_v3')


class TestCommanderV3Wiring:
    """Test that Commander delegates to PipelineV3 when configured."""

    def test_handle_llm_query_v3_method_exists(self):
        """Commander should have _handle_llm_query_v3 method."""
        from lina.shell.commander import Commander
        assert hasattr(Commander, "_handle_llm_query_v3")

    def test_handle_llm_query_v3_delegation(self):
        """Commander._handle_llm_query_v3 delegates to unified _handle_llm_query (Phase 27)."""
        from lina.shell.commander import Commander
        # After unification, _handle_llm_query_v3 is a thin wrapper
        # that calls _handle_llm_query (unified path through MainPipeline).
        cmd = Commander.__new__(Commander)  # skip __init__
        called = []
        cmd._handle_llm_query = lambda q: (called.append(q), "unified response")[1]
        result = cmd._handle_llm_query_v3("test query")
        assert result == "unified response"
        assert called == ["test query"]

    def test_apply_config_no_pipeline_version_mutation(self):
        """apply_config should NOT mutate pipeline_version (field removed)."""
        from lina.core.runtime import apply_config
        from lina.core.cli import LinaArgs
        from lina.config import config
        args = LinaArgs()
        apply_config(args)
        assert not hasattr(config.pipeline, 'pipeline_version')
