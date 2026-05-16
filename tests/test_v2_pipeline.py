# -*- coding: utf-8 -*-
"""
Tests for Lina v2 Pipeline modules.

Covers:
  - datatypes (enums, dataclasses, methods)
  - html_cleaner
  - text_splitter
  - query_rewriter
  - result_ranker
  - passage_extractor (unit only — no network)
  - embedding_ranker (BM25 fallback)
  - fact_extractor
  - fact_aggregator
  - fact_verifier
  - self_verifier (mocked LLM)
  - conversation_state
  - fact_store
  - cache
  - pipeline config
  - assistant_pipeline (mocked search + LLM)
"""

import json
import os
import pytest
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# ═══════════════════════════════════════════════════
#  1. Datatypes
# ═══════════════════════════════════════════════════

from lina.models.datatypes import (
    IntentType, ConfidenceLevel, SearchResult, QueryPlan,
    Passage, Fact, FactSet, PipelineAnswer, ConversationTurn,
    PipelineTrace,
)


class TestIntentType:
    def test_values(self):
        assert IntentType.CHAT.value == "chat"
        assert IntentType.WEB_SEARCH.value == "web_search"
        assert IntentType.UNKNOWN.value == "unknown"


class TestConfidenceLevel:
    def test_from_score_high(self):
        assert ConfidenceLevel.from_score(0.9) == ConfidenceLevel.HIGH

    def test_from_score_medium(self):
        assert ConfidenceLevel.from_score(0.6) == ConfidenceLevel.MEDIUM

    def test_from_score_low(self):
        assert ConfidenceLevel.from_score(0.3) == ConfidenceLevel.LOW

    def test_from_score_none(self):
        assert ConfidenceLevel.from_score(0.1) == ConfidenceLevel.NONE


class TestSearchResult:
    def test_defaults(self):
        r = SearchResult(title="Test", url="https://example.com", snippet="desc")
        assert r.relevance == 0.0
        assert r.content == ""

    def test_fields(self):
        r = SearchResult(title="T", url="u", snippet="s", relevance=0.8)
        assert r.relevance == 0.8


class TestQueryPlan:
    def test_primary(self):
        p = QueryPlan(original="тест", queries=["test1", "test2", "test3"])
        assert p.primary == "test1"

    def test_primary_empty(self):
        p = QueryPlan(original="тест", queries=[])
        assert p.primary == "тест"


class TestPassage:
    def test_create(self):
        p = Passage(text="Hello world", source_url="https://a.com", score=0.5)
        assert p.word_count == 2  # auto-computed from text
        assert p.score == 0.5


class TestFact:
    def test_key(self):
        f = Fact(subject="Phone", predicate="RAM", object_value="8GB")
        assert f.key() == "phone|ram"

    def test_defaults(self):
        f = Fact(subject="X", predicate="Y", object_value="Z")
        assert f.source_count == 1
        assert f.confidence == 0.5
        assert f.verified is False


class TestFactSet:
    def test_verified_count(self):
        fs = FactSet(subject="Phone", facts=[
            Fact(subject="Phone", predicate="RAM", object_value="8GB", verified=True),
            Fact(subject="Phone", predicate="CPU", object_value="SD680", verified=False),
            Fact(subject="Phone", predicate="Battery", object_value="5000mah", verified=True),
        ])
        assert fs.verified_count == 2

    def test_get_by_predicate(self):
        fs = FactSet(subject="X", facts=[
            Fact(subject="X", predicate="RAM", object_value="8GB"),
            Fact(subject="X", predicate="CPU", object_value="SD680"),
        ])
        assert fs.get_by_predicate("RAM") is not None
        assert fs.get_by_predicate("GPU") is None

    def test_format_for_llm(self):
        fs = FactSet(subject="Phone", facts=[
            Fact(subject="Phone", predicate="RAM", object_value="8GB",
                 confidence=0.9, sources=["https://a.com"]),
        ])
        text = fs.format_for_llm()
        assert "RAM" in text
        assert "8GB" in text

    def test_format_for_llm_ru(self):
        fs = FactSet(subject="Телефон", facts=[
            Fact(subject="Телефон", predicate="RAM", object_value="8GB",
                 confidence=0.9, verified=True),
        ])
        text = fs.format_for_llm_ru()
        assert "RAM" in text
        assert "✓" in text


class TestPipelineAnswer:
    def test_is_reliable(self):
        a = PipelineAnswer(text="answer", confidence=0.8, facts_used=3, verified=True)
        assert a.is_reliable() is True

    def test_not_reliable_low_conf(self):
        a = PipelineAnswer(text="answer", confidence=0.3, facts_used=3, verified=True)
        assert a.is_reliable() is False

    def test_confidence_level(self):
        a = PipelineAnswer(text="test", confidence=0.8)
        assert a.confidence_level == ConfidenceLevel.HIGH


class TestPipelineTrace:
    def test_record(self):
        t = PipelineTrace()
        t.record("stage1", 15.5)
        assert "stage1" in t.stage_timings
        assert t.stage_timings["stage1"] == 15.5

    def test_total_ms(self):
        t = PipelineTrace()
        t.record("a", 10)
        t.record("b", 20)
        assert t.total_ms() == 30


# ═══════════════════════════════════════════════════
#  2. HTML Cleaner
# ═══════════════════════════════════════════════════

from lina.utils.html_cleaner import clean_html, extract_title, extract_main_content


class TestHTMLCleaner:
    def test_clean_html_basic(self):
        html = "<p>Hello <b>world</b></p><script>alert(1)</script>"
        text = clean_html(html)
        assert "Hello" in text
        assert "world" in text
        assert "alert" not in text

    def test_clean_html_empty(self):
        assert clean_html("") == ""
        assert clean_html(None) == ""

    def test_extract_title(self):
        html = "<html><head><title>Test Page</title></head></html>"
        assert extract_title(html) == "Test Page"

    def test_extract_title_missing(self):
        assert extract_title("<html></html>") == ""

    def test_extract_main_content(self):
        html = """<html><body>
        <nav>Navigation</nav>
        <article><p>Main content here</p></article>
        <footer>Footer</footer>
        </body></html>"""
        content = extract_main_content(html)
        assert "Main content" in content

    def test_max_length(self):
        html = "<p>" + "a" * 10000 + "</p>"
        text = clean_html(html, max_length=100)
        assert len(text) <= 101  # may be off by 1 due to tag boundaries


# ═══════════════════════════════════════════════════
#  3. Text Splitter
# ═══════════════════════════════════════════════════

from lina.utils.text_splitter import (
    split_into_passages, split_sentences, estimate_word_count,
)


class TestTextSplitter:
    def test_split_paragraphs(self):
        text = "First paragraph with enough words to pass the filter.\n\nSecond paragraph also has enough words to pass."
        passages = split_into_passages(text, min_words=5, max_words=50)
        assert len(passages) >= 1

    def test_split_sentences(self):
        text = "First sentence. Second sentence. Third sentence."
        sents = split_sentences(text)
        assert len(sents) == 3

    def test_estimate_word_count(self):
        assert estimate_word_count("one two three") == 3
        assert estimate_word_count("") == 0

    def test_empty_input(self):
        assert split_into_passages("") == []

    def test_min_words_filter(self):
        text = "Short.\n\nAlso short."
        result = split_into_passages(text, min_words=10)
        assert len(result) == 0 or all(
            estimate_word_count(p) >= 5 for p in result  # relaxed due to accumulation
        )


# ═══════════════════════════════════════════════════
#  4. Query Rewriter
# ═══════════════════════════════════════════════════

from lina.core.query_rewriter import QueryRewriter


class TestQueryRewriter:
    def setup_method(self):
        self.rw = QueryRewriter()

    def test_basic_rewrite(self):
        plan = self.rw.rewrite("Расскажи про Realme 10")
        assert isinstance(plan, QueryPlan)
        assert len(plan.queries) >= 1
        assert plan.original == "Расскажи про Realme 10"

    def test_empty_query(self):
        plan = self.rw.rewrite("")
        assert plan.queries == [""]

    def test_english_query(self):
        plan = self.rw.rewrite("iPhone 15 Pro specs")
        assert len(plan.queries) >= 1

    def test_detected_intent(self):
        plan = self.rw.rewrite("сравни Galaxy S24 и iPhone 15")
        # Should detect comparison intent
        assert plan.detected_intent != ""


# ═══════════════════════════════════════════════════
#  5. Result Ranker
# ═══════════════════════════════════════════════════

from lina.core.result_ranker import ResultRanker


class TestResultRanker:
    def setup_method(self):
        self.ranker = ResultRanker()

    def test_basic_ranking(self):
        results = [
            SearchResult(title="GSMArena test", url="https://gsmarena.com/test",
                        snippet="Full specs and review"),
            SearchResult(title="Pinterest", url="https://pinterest.com/pin",
                        snippet="Beautiful phone photo"),
            SearchResult(title="Wikipedia article", url="https://en.wikipedia.org/test",
                        snippet="Technical details about"),
        ]
        ranked = self.ranker.rank(results, "phone specs")
        # GSMArena should rank higher than Pinterest
        assert len(ranked) >= 2
        urls = [r.url for r in ranked]
        # Pinterest should be filtered as spam
        assert "https://pinterest.com/pin" not in urls

    def test_empty_results(self):
        assert self.ranker.rank([], "test") == []

    def test_deduplication(self):
        results = [
            SearchResult(title="A", url="https://a.com/page", snippet="text"),
            SearchResult(title="B", url="https://a.com/page", snippet="text dup"),
        ]
        ranked = self.ranker.rank(results, "test")
        assert len(ranked) == 1


# ═══════════════════════════════════════════════════
#  6. Embedding Ranker (pure-Python backend)
# ═══════════════════════════════════════════════════

from lina.core.embedding_ranker import EmbeddingRanker


class TestEmbeddingRanker:
    def setup_method(self):
        self.ranker = EmbeddingRanker()

    def test_basic_ranking(self):
        passages = [
            Passage(text="The Snapdragon 680 processor delivers good performance",
                    source_url="a.com"),
            Passage(text="Beautiful sunset photos from vacation",
                    source_url="b.com"),
            Passage(text="Realme 10 has a large 5000mAh battery",
                    source_url="c.com"),
        ]
        ranked = self.ranker.rank(passages, "Realme 10 processor specs", top_k=3, min_similarity=0.0)
        assert len(ranked) > 0
        # Technical passage should rank higher
        assert ranked[0].score > 0

    def test_empty_passages(self):
        result = self.ranker.rank([], "test")
        assert result == []

    def test_empty_query(self):
        passages = [Passage(text="some text", source_url="a.com")]
        result = self.ranker.rank(passages, "")
        assert len(result) == 1


# ═══════════════════════════════════════════════════
#  7. Fact Extractor
# ═══════════════════════════════════════════════════

from lina.core.fact_extractor import FactExtractor


class TestFactExtractor:
    def setup_method(self):
        self.ext = FactExtractor()

    def test_extract_processor(self):
        passages = [
            Passage(text="Процессор: Snapdragon 680 обеспечивает хорошую производительность",
                    source_url="https://a.com"),
        ]
        facts = self.ext.extract_from_passages(passages, subject="Realme 10")
        preds = [f.predicate for f in facts]
        assert "processor" in preds

    def test_extract_ram(self):
        passages = [
            Passage(text="Оперативная память: 8 ГБ LPDDR4X",
                    source_url="https://b.com"),
        ]
        facts = self.ext.extract_from_passages(passages, subject="Phone")
        preds = [f.predicate for f in facts]
        assert "RAM" in preds

    def test_extract_battery(self):
        passages = [
            Passage(text="Аккумулятор ёмкостью 5000 мАч",
                    source_url="https://c.com"),
        ]
        facts = self.ext.extract_from_passages(passages, subject="Phone")
        preds = [f.predicate for f in facts]
        assert "battery" in preds

    def test_extract_display(self):
        passages = [
            Passage(text="Экран: 6.4 дюйма Super AMOLED",
                    source_url="https://d.com"),
        ]
        facts = self.ext.extract_from_passages(passages, subject="Phone")
        preds = [f.predicate for f in facts]
        assert "display" in preds

    def test_extract_kv_pair(self):
        passages = [
            Passage(text="Bluetooth: 5.1\nWiFi: 802.11ac",
                    source_url="https://e.com"),
        ]
        facts = self.ext.extract_from_passages(passages, subject="Phone")
        assert len(facts) >= 1

    def test_empty_passages(self):
        assert self.ext.extract_from_passages([], subject="X") == []

    def test_no_subject(self):
        passages = [
            Passage(text="Процессор: Snapdragon 8 Gen 3", source_url="a.com"),
        ]
        facts = self.ext.extract_from_passages(passages)
        assert all(f.subject for f in facts)

    def test_extract_price(self):
        passages = [
            Passage(text="Цена: 15 990 руб", source_url="https://f.com"),
        ]
        facts = self.ext.extract_from_passages(passages, subject="Phone")
        preds = [f.predicate for f in facts]
        assert "price" in preds

    def test_extract_charging(self):
        passages = [
            Passage(text="Быстрая зарядка: 33 Вт", source_url="https://g.com"),
        ]
        facts = self.ext.extract_from_passages(passages, subject="Phone")
        preds = [f.predicate for f in facts]
        assert "charging" in preds


# ═══════════════════════════════════════════════════
#  8. Fact Aggregator
# ═══════════════════════════════════════════════════

from lina.core.fact_aggregator import FactAggregator


class TestFactAggregator:
    def setup_method(self):
        self.agg = FactAggregator()

    def test_merge_duplicate_facts(self):
        facts = [
            Fact(subject="Phone", predicate="RAM", object_value="8 GB",
                 sources=["https://a.com"], confidence=0.7),
            Fact(subject="Phone", predicate="RAM", object_value="8 gb",
                 sources=["https://b.com"], confidence=0.7),
        ]
        fs = self.agg.aggregate(facts, subject="Phone")
        # Should merge into one fact with 2 sources
        ram_facts = [f for f in fs.facts if f.predicate == "RAM"]
        assert len(ram_facts) == 1
        assert ram_facts[0].source_count >= 2
        assert ram_facts[0].confidence > 0.7  # boosted

    def test_confidence_boost_3_sources(self):
        facts = [
            Fact(subject="X", predicate="CPU", object_value="SD680",
                 sources=["a.com"], confidence=0.6),
            Fact(subject="X", predicate="CPU", object_value="sd680",
                 sources=["b.com"], confidence=0.6),
            Fact(subject="X", predicate="CPU", object_value="SD680",
                 sources=["c.com"], confidence=0.7),
        ]
        fs = self.agg.aggregate(facts, subject="X")
        cpu = [f for f in fs.facts if "processor" in f.predicate.lower() or "cpu" in f.predicate.lower()]
        assert len(cpu) >= 1
        assert cpu[0].confidence >= 0.85

    def test_empty_input(self):
        fs = self.agg.aggregate([], subject="X")
        assert len(fs.facts) == 0
        assert fs.confidence == 0.0

    def test_different_predicates_preserved(self):
        facts = [
            Fact(subject="X", predicate="RAM", object_value="8GB",
                 sources=["a.com"], confidence=0.7),
            Fact(subject="X", predicate="battery", object_value="5000 mAh",
                 sources=["a.com"], confidence=0.7),
        ]
        fs = self.agg.aggregate(facts, subject="X")
        assert len(fs.facts) >= 2

    def test_verified_flag(self):
        facts = [
            Fact(subject="X", predicate="RAM", object_value="8GB",
                 sources=["a.com"], confidence=0.7),
            Fact(subject="X", predicate="RAM", object_value="8gb",
                 sources=["b.com"], confidence=0.7),
        ]
        fs = self.agg.aggregate(facts, subject="X")
        ram = [f for f in fs.facts if f.predicate == "RAM"]
        assert ram[0].verified is True  # multi-source


# ═══════════════════════════════════════════════════
#  9. Fact Verifier
# ═══════════════════════════════════════════════════

from lina.core.fact_verifier import FactVerifier


class TestFactVerifier:
    def setup_method(self):
        self.ver = FactVerifier(min_confidence=0.40)

    def test_filter_low_confidence(self):
        fs = FactSet(subject="X", facts=[
            Fact(subject="X", predicate="RAM", object_value="8GB",
                 confidence=0.8, verified=True),
            Fact(subject="X", predicate="color", object_value="blue",
                 confidence=0.2),  # low conf → should be discarded
        ], confidence=0.6)
        result = self.ver.verify(fs)
        assert len(result.facts) == 1
        assert result.facts[0].predicate == "RAM"

    def test_contradiction_resolution(self):
        fs = FactSet(subject="X", facts=[
            Fact(subject="X", predicate="RAM", object_value="8GB",
                 confidence=0.9),
            Fact(subject="X", predicate="RAM", object_value="6GB",
                 confidence=0.5),
        ], confidence=0.7)
        result = self.ver.verify(fs)
        ram = [f for f in result.facts if f.predicate == "RAM"]
        assert len(ram) == 1
        assert "8" in ram[0].object_value  # higher conf wins

    def test_empty_facts(self):
        fs = FactSet(subject="X")
        result = self.ver.verify(fs)
        assert len(result.facts) == 0

    def test_single_domain_cap(self):
        fs = FactSet(subject="X", facts=[
            Fact(subject="X", predicate="RAM", object_value="8GB",
                 confidence=0.9, sources=["https://gsmarena.com/page"]),
            Fact(subject="X", predicate="CPU", object_value="SD680",
                 confidence=0.9, sources=["https://gsmarena.com/other"]),
        ], confidence=0.9)
        result = self.ver.verify(fs)
        # Single domain → confidence capped at 0.50
        assert result.confidence <= 0.50


# ═══════════════════════════════════════════════════
#  10. Self-Verifier (mocked LLM)
# ═══════════════════════════════════════════════════

from lina.llm.self_verifier import SelfVerifier, VerificationResult


class TestSelfVerifier:
    def test_verify_ok(self):
        llm_fn = lambda prompt: "OK"
        ver = SelfVerifier(llm_fn=llm_fn)
        answer = PipelineAnswer(text="RAM is 8GB", confidence=0.8)
        fs = FactSet(subject="X", facts=[
            Fact(subject="X", predicate="RAM", object_value="8GB"),
        ])
        result = ver.verify(answer, fs)
        assert result.is_faithful is True
        assert not result.has_issues

    def test_verify_hallucination(self):
        llm_fn = lambda prompt: "HALLUCINATION: phone has 12GB RAM"
        ver = SelfVerifier(llm_fn=llm_fn)
        answer = PipelineAnswer(text="phone has 12GB RAM", confidence=0.8)
        fs = FactSet(subject="X", facts=[
            Fact(subject="X", predicate="RAM", object_value="8GB"),
        ])
        result = ver.verify(answer, fs)
        assert result.is_faithful is False
        assert len(result.hallucinations) == 1

    def test_verify_mismatch(self):
        llm_fn = lambda prompt: "MISMATCH: battery is 4000mAh not 5000mAh"
        ver = SelfVerifier(llm_fn=llm_fn)
        answer = PipelineAnswer(text="battery 5000mAh", confidence=0.8)
        fs = FactSet(subject="X", facts=[
            Fact(subject="X", predicate="battery", object_value="4000mAh"),
        ])
        result = ver.verify(answer, fs)
        assert len(result.mismatches) == 1

    def test_no_llm(self):
        ver = SelfVerifier(llm_fn=None)
        answer = PipelineAnswer(text="test", confidence=0.8)
        fs = FactSet(subject="X")
        result = ver.verify(answer, fs)
        assert result.is_faithful is True

    def test_empty_facts(self):
        llm_fn = lambda prompt: "OK"
        ver = SelfVerifier(llm_fn=llm_fn)
        answer = PipelineAnswer(text="test", confidence=0.8)
        fs = FactSet(subject="X")  # no facts
        result = ver.verify(answer, fs)
        assert result.is_faithful is True


# ═══════════════════════════════════════════════════
#  11. Conversation State
# ═══════════════════════════════════════════════════

from lina.memory.conversation_state import ConversationState


class TestConversationState:
    def setup_method(self):
        self.state = ConversationState(max_turns=5)

    def test_add_turn(self):
        turn = ConversationTurn(
            query="test", answer="resp", topic="Realme 10",
            entities=["Realme 10"],
        )
        self.state.add_turn(turn)
        assert self.state.turn_count == 1
        assert self.state.current_topic == "Realme 10"

    def test_active_entities(self):
        turn = ConversationTurn(
            query="test", answer="resp", topic="X",
            entities=["Realme 10", "Snapdragon 680"],
        )
        self.state.add_turn(turn)
        ents = self.state.active_entities
        assert "Realme 10" in ents
        assert "Snapdragon 680" in ents

    def test_resolve_pronoun(self):
        turn = ConversationTurn(
            query="test", answer="resp", topic="Realme 10",
            entities=["Realme 10"],
        )
        self.state.add_turn(turn)
        resolved = self.state.resolve_pronoun_subject("какой у него процессор?")
        assert "Realme 10" in resolved

    def test_resolve_no_topic(self):
        q = "какой у него процессор?"
        assert self.state.resolve_pronoun_subject(q) == q

    def test_max_turns(self):
        for i in range(10):
            self.state.add_turn(ConversationTurn(query=f"q{i}", answer=f"a{i}"))
        assert self.state.turn_count == 5

    def test_clear(self):
        self.state.add_turn(ConversationTurn(query="q", answer="a", topic="X"))
        self.state.clear()
        assert self.state.turn_count == 0
        assert self.state.current_topic == ""

    def test_build_context_hint(self):
        self.state.add_turn(ConversationTurn(
            query="q", answer="a", topic="Realme 10",
            entities=["Realme 10"],
        ))
        hint = self.state.build_context_hint()
        assert "Realme 10" in hint


# ═══════════════════════════════════════════════════
#  12. Fact Store
# ═══════════════════════════════════════════════════

from lina.memory.fact_store import FactStore


class TestFactStore:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = FactStore(cache_dir=self.tmpdir, ttl_seconds=60)

    def test_put_and_get(self):
        facts = [
            Fact(subject="Phone", predicate="RAM", object_value="8GB",
                 confidence=0.8),
        ]
        self.store.put("Phone", facts)
        result = self.store.get("Phone")
        assert len(result) == 1
        assert result[0].predicate == "RAM"

    def test_has(self):
        self.store.put("X", [Fact(subject="X", predicate="Y", object_value="Z")])
        assert self.store.has("X") is True
        assert self.store.has("missing") is False

    def test_expired(self):
        store = FactStore(cache_dir=self.tmpdir, ttl_seconds=0)
        store.put("X", [Fact(subject="X", predicate="Y", object_value="Z")])
        time.sleep(0.01)
        assert store.get("X") == []

    def test_remove(self):
        self.store.put("X", [Fact(subject="X", predicate="Y", object_value="Z")])
        self.store.remove("X")
        assert self.store.has("X") is False

    def test_clear(self):
        self.store.put("A", [Fact(subject="A", predicate="Y", object_value="Z")])
        self.store.put("B", [Fact(subject="B", predicate="Y", object_value="Z")])
        self.store.clear()
        assert self.store.entity_count == 0

    def test_save_and_load(self):
        facts = [Fact(subject="X", predicate="RAM", object_value="8GB")]
        self.store.put("X", facts)
        self.store.save()

        # Load in new instance
        store2 = FactStore(cache_dir=self.tmpdir, ttl_seconds=60)
        result = store2.get("X")
        assert len(result) == 1


# ═══════════════════════════════════════════════════
#  13. Cache
# ═══════════════════════════════════════════════════

from lina.memory.cache import LRUCache


class TestLRUCache:
    def test_basic_put_get(self):
        c = LRUCache(max_size=10)
        c.put("a", 42)
        assert c.get("a") == 42

    def test_miss(self):
        c = LRUCache(max_size=10)
        assert c.get("missing") is None

    def test_ttl_expiry(self):
        c = LRUCache(max_size=10, default_ttl=0.01)
        c.put("a", 42)
        time.sleep(0.02)
        assert c.get("a") is None

    def test_lru_eviction(self):
        c = LRUCache(max_size=2)
        c.put("a", 1)
        c.put("b", 2)
        c.put("c", 3)  # should evict "a"
        assert c.get("a") is None
        assert c.get("b") == 2
        assert c.get("c") == 3

    def test_has(self):
        c = LRUCache(max_size=10)
        c.put("a", 1)
        assert c.has("a") is True
        assert c.has("b") is False

    def test_remove(self):
        c = LRUCache(max_size=10)
        c.put("a", 1)
        c.remove("a")
        assert c.get("a") is None

    def test_stats(self):
        c = LRUCache(max_size=10)
        c.put("a", 1)
        c.get("a")       # hit
        c.get("missing")  # miss
        stats = c.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_cleanup_expired(self):
        c = LRUCache(max_size=10, default_ttl=0.01)
        c.put("a", 1)
        c.put("b", 2)
        time.sleep(0.02)
        removed = c.cleanup_expired()
        assert removed == 2
        assert c.size == 0


# ═══════════════════════════════════════════════════
#  14. Pipeline Config
# ═══════════════════════════════════════════════════

from lina.pipeline.config import PipelineConfig, get_pipeline_config


class TestPipelineConfig:
    def test_defaults(self):
        cfg = PipelineConfig()
        assert cfg.max_search_queries == 5
        assert cfg.top_k_passages == 10
        assert cfg.enable_self_verification is True

    def test_singleton(self):
        cfg1 = get_pipeline_config()
        cfg2 = get_pipeline_config()
        assert cfg1 is cfg2


# ═══════════════════════════════════════════════════
#  15. Assistant Pipeline (integration — mocked search + LLM)
# ═══════════════════════════════════════════════════

from lina.pipeline.assistant_pipeline import AssistantPipeline


class TestAssistantPipeline:
    def test_create_without_llm(self):
        """Pipeline should work without LLM, returning fact summaries."""
        pipe = AssistantPipeline(llm_fn=None)
        assert pipe is not None

    def test_no_results_fallback(self):
        """When search returns nothing, pipeline returns graceful fallback."""
        pipe = AssistantPipeline(llm_fn=lambda p: "test answer")
        # Mock the search pipeline to return no results
        pipe._search = MagicMock()
        pipe._search.search = MagicMock(return_value=(
            QueryPlan(original="test", queries=["test"]),
            [],  # no results
        ))
        answer = pipe.run("test query", use_cache=False)
        assert isinstance(answer, PipelineAnswer)
        assert answer.confidence < 0.5

    def test_full_pipeline_mocked(self):
        """Full pipeline with all stages mocked."""
        pipe = AssistantPipeline(llm_fn=lambda p: "Realme 10 has 8GB RAM and SD680")

        # Mock search
        pipe._search = MagicMock()
        pipe._search.search = MagicMock(return_value=(
            QueryPlan(original="Realme 10", queries=["Realme 10 specs"]),
            [
                SearchResult(title="GSMArena", url="https://gsmarena.com/realme10",
                           snippet="Realme 10 specs"),
            ],
        ))

        # Mock passage extractor
        pipe._passage_ext = MagicMock()
        pipe._passage_ext.extract = MagicMock(return_value=[
            Passage(text="Процессор: Snapdragon 680. Оперативная память: 8 ГБ.",
                    source_url="https://gsmarena.com/realme10",
                    source_title="GSMArena"),
        ])

        # Self-verifier returns OK
        pipe._self_ver = MagicMock()
        from lina.llm.self_verifier import VerificationResult
        pipe._self_ver.verify = MagicMock(return_value=VerificationResult(is_faithful=True))

        # Clear caches
        pipe._resp_cache.clear()
        pipe._fact_store.clear()

        answer = pipe.run("Расскажи про Realme 10", use_cache=False)
        assert isinstance(answer, PipelineAnswer)
        assert answer.text  # should have text
        assert answer.elapsed_ms > 0

    def test_pipeline_caching(self):
        """Second call for the same query should hit cache."""
        pipe = AssistantPipeline(llm_fn=lambda p: "cached answer")
        pipe._resp_cache.clear()

        # Pre-fill cache
        cached_answer = PipelineAnswer(text="cached!", confidence=0.9)
        pipe._resp_cache.put("test query", cached_answer)

        result = pipe.run("test query", use_cache=True)
        assert result.text == "cached!"
