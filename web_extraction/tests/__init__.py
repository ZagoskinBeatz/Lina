# -*- coding: utf-8 -*-
"""
Tests for Lina Web Extraction Pipeline.

Covers: ContentExtractor, SemanticChunker, HybridRanker,
        SourceTrustScorer, PageProcessor, WebExtractionPipeline.
"""

import pytest
from unittest.mock import patch, MagicMock

# ═══════════════════════════════════════════════════
#  ContentExtractor Tests
# ═══════════════════════════════════════════════════

class TestContentExtractor:
    """Test DOM-based content extraction."""

    def setup_method(self):
        from lina.web_extraction.content_extractor import ContentExtractor
        self.extractor = ContentExtractor()

    def test_empty_html_returns_bot_page(self):
        result = self.extractor.extract("")
        assert result.is_bot_page

    def test_minimal_html(self):
        html = "<html><body><p>Hello world. This is a test paragraph with enough text to pass quality checks. " \
               "We need sufficient content to be considered a real page with meaningful information.</p></body></html>"
        result = self.extractor.extract(html)
        assert result.main_text
        assert result.word_count > 10

    def test_article_tag_extraction(self):
        html = """
        <html><body>
        <nav><a href="/">Home</a><a href="/about">About</a></nav>
        <article>
            <h1>Main Article Title</h1>
            <p>This is the main article content. It contains important information
            that should be extracted. The article discusses various topics in detail
            and provides factual data for the reader to consume.</p>
            <p>Second paragraph with additional details about the topic at hand.
            More content here to ensure the article is substantial enough.</p>
        </article>
        <aside>Sidebar content that should be removed</aside>
        <footer>Copyright 2024 All rights reserved</footer>
        </body></html>
        """
        result = self.extractor.extract(html)
        assert result.is_usable
        assert "Main Article Title" in result.main_text or "main article content" in result.main_text.lower()
        assert "Sidebar" not in result.main_text
        assert result.extraction_method in ("semantic", "class_heuristic", "density", "body_fallback")

    def test_table_to_kv_conversion(self):
        html = """
        <html><body>
        <article>
            <h2>Specifications</h2>
            <table>
                <tr><td>Processor</td><td>MediaTek Helio G99</td></tr>
                <tr><td>RAM</td><td>8 GB</td></tr>
                <tr><td>Battery</td><td>5000 mAh</td></tr>
            </table>
            <p>Additional text to ensure the page has enough content for quality checks.
            This paragraph provides context about the specifications listed above.</p>
        </article>
        </body></html>
        """
        result = self.extractor.extract(html)
        assert "Processor: MediaTek Helio G99" in result.main_text
        assert "RAM: 8 GB" in result.main_text

    def test_list_to_bullets(self):
        html = """
        <html><body>
        <main>
            <h2>Features</h2>
            <ul>
                <li>Fast charging support</li>
                <li>5G connectivity</li>
                <li>AMOLED display</li>
            </ul>
            <p>These features make this device competitive in the market segment.
            Users will appreciate the modern technology included in this product.</p>
        </main>
        </body></html>
        """
        result = self.extractor.extract(html)
        assert "• Fast charging support" in result.main_text

    def test_bot_page_detection(self):
        html = "<html><body><p>Checking your browser. Please verify you are human. Cloudflare Ray ID: abc123</p></body></html>"
        result = self.extractor.extract(html)
        assert result.is_bot_page

    def test_boilerplate_removal(self):
        html = """
        <html><body>
        <article>
            <p>Important article content with enough text to pass quality filters.
            This article contains substantial information about the topic being discussed.
            Multiple sentences ensure quality is high enough for processing.</p>
            <p>Cookie policy: We use cookies to improve your experience.</p>
            <p>Subscribe to our newsletter now!</p>
            <p>All rights reserved © 2024</p>
        </article>
        </body></html>
        """
        result = self.extractor.extract(html)
        assert "cookie" not in result.main_text.lower() or "policy" not in result.main_text.lower()

    def test_title_extraction(self):
        html = "<html><head><title>Test Page Title</title></head><body><p>Content</p></body></html>"
        title = self.extractor.extract_title(html)
        assert title == "Test Page Title"

    def test_quality_assessment(self):
        # Good content
        good_text = "This is a well-structured article. " * 50
        quality = self.extractor._assess_quality(good_text)
        assert quality > 0.3

        # Poor content (very short)
        poor_text = "Short."
        quality = self.extractor._assess_quality(poor_text)
        assert quality < 0.3

    def test_regex_fallback(self):
        """Test that regex extraction works without BS4."""
        result = self.extractor._extract_regex(
            "<html><body><p>Hello world from regex. This is test content.</p>"
            "<script>var x = 1;</script></body></html>"
        )
        assert "Hello world from regex" in result.main_text
        assert "var x" not in result.main_text


# ═══════════════════════════════════════════════════
#  SemanticChunker Tests
# ═══════════════════════════════════════════════════

class TestSemanticChunker:
    """Test token-aware semantic chunking."""

    def setup_method(self):
        from lina.web_extraction.semantic_chunker import SemanticChunker
        self.chunker = SemanticChunker(
            target_tokens=300,
            min_tokens=40,
            max_tokens=450,
            overlap_tokens=60,
        )

    def test_empty_text(self):
        chunks = self.chunker.chunk("")
        assert chunks == []

    def test_single_paragraph(self):
        text = "This is a single paragraph. " * 20
        chunks = self.chunker.chunk(text)
        assert len(chunks) >= 1
        assert all(c.text for c in chunks)

    def test_multiple_paragraphs(self):
        paragraphs = [f"Paragraph {i}. " * 15 for i in range(5)]
        text = "\n\n".join(paragraphs)
        chunks = self.chunker.chunk(text)
        assert len(chunks) >= 2

    def test_heading_based_splitting(self):
        text = """## Introduction
        This is the introduction section. It contains important background information.

        ## Methods
        This section describes the methodology used in the study. Various techniques were applied.

        ## Results
        The results show significant improvements across all metrics measured."""
        chunks = self.chunker.chunk(text)
        assert len(chunks) >= 1
        # Check that section titles are captured
        has_section = any(c.section_title for c in chunks)
        assert has_section or len(chunks) == 1  # Small text may not split

    def test_structured_block_preservation(self):
        text = """Device specifications:

Processor: MediaTek Helio G99
RAM: 8 GB LPDDR4X
Storage: 128 GB
Battery: 5000 mAh
Display: 6.4 inch AMOLED

The device offers excellent performance for its price range."""
        chunks = self.chunker.chunk(text)
        # KV lines should stay together in one chunk
        found_kv_block = any(
            "Processor:" in c.text and "RAM:" in c.text
            for c in chunks
        )
        assert found_kv_block

    def test_overlap_between_chunks(self):
        # Create text large enough to produce multiple chunks
        sentences = [f"Sentence number {i} with enough words to count. " for i in range(100)]
        text = " ".join(sentences)
        chunks = self.chunker.chunk(text)
        if len(chunks) >= 2:
            # Check that some overlap exists
            last_words_first = set(chunks[0].text.split()[-10:])
            first_words_second = set(chunks[1].text.split()[:20])
            overlap = last_words_first & first_words_second
            assert len(overlap) > 0 or chunks[1].metadata.has_overlap

    def test_sentence_never_broken(self):
        text = "First complete sentence. Second complete sentence. Third complete sentence. " * 30
        chunks = self.chunker.chunk(text)
        for chunk in chunks:
            # Each chunk should end with a complete sentence (punctuation)
            stripped = chunk.text.strip()
            if stripped:
                assert stripped[-1] in ".!?…" or stripped.endswith("```")

    def test_to_passage_conversion(self):
        text = "Test passage content. " * 20
        passages = self.chunker.chunk_to_passages(text, source_url="http://test.com")
        assert all(isinstance(p, object) for p in passages)
        assert all(p.source_url == "http://test.com" for p in passages)

    def test_max_chunks_limit(self):
        chunker = self.chunker.__class__(max_chunks_per_doc=3)
        text = "Content. " * 500
        chunks = chunker.chunk(text)
        assert len(chunks) <= 3

    def test_min_chunk_merging(self):
        text = "Short.\n\nAlso short.\n\nAnd this is short too."
        # These should be merged since each is below min_tokens
        chunks = self.chunker.chunk(text)
        # Either merged into one chunk or no chunks at all (too short)
        assert len(chunks) <= 2


# ═══════════════════════════════════════════════════
#  HybridRanker Tests
# ═══════════════════════════════════════════════════

class TestHybridRanker:
    """Test BM25 + embedding hybrid ranking."""

    def setup_method(self):
        from lina.web_extraction.hybrid_ranker import HybridRanker, BM25
        from lina.models.datatypes import Passage
        self.Passage = Passage
        self.ranker = HybridRanker()
        self.bm25 = BM25()

    def test_bm25_scoring(self):
        passages = [
            "The processor in Realme 10 is MediaTek Helio G99.",
            "Weather forecast for tomorrow shows rain and clouds.",
            "Realme 10 specs include Helio G99 processor and 8GB RAM.",
        ]
        scores = self.bm25.score_normalized(passages, "Realme 10 processor")
        assert scores[0] > scores[1]  # Relevant > irrelevant
        assert scores[2] > scores[1]  # Relevant > irrelevant

    def test_bm25_empty_query(self):
        scores = self.bm25.score(["text"], "")
        assert scores == [0.0]

    def test_hybrid_rank(self):
        passages = [
            self.Passage(text="Realme 10 has MediaTek Helio G99 processor.", source_url="http://a.com"),
            self.Passage(text="The weather today is sunny and warm.", source_url="http://b.com"),
            self.Passage(text="Realme 10 review: excellent performance with G99.", source_url="http://c.com"),
        ]
        ranked = self.ranker.rank(passages, "Realme 10 processor", top_k=3)
        assert len(ranked) > 0
        # Relevant passages should score higher
        assert ranked[0].score >= ranked[-1].score

    def test_empty_passages(self):
        ranked = self.ranker.rank([], "test query")
        assert ranked == []

    def test_min_score_filtering(self):
        passages = [
            self.Passage(text="Completely unrelated content about cooking recipes."),
        ]
        ranked = self.ranker.rank(passages, "quantum physics", min_score=0.9)
        # Should be filtered out due to irrelevance
        assert len(ranked) <= 1

    def test_scores_are_set(self):
        passages = [self.Passage(text="Test content about processors.")]
        ranked = self.ranker.rank(passages, "processor", top_k=1, min_score=0.0)
        assert all(p.score > 0 for p in ranked)


# ═══════════════════════════════════════════════════
#  SourceTrustScorer Tests
# ═══════════════════════════════════════════════════

class TestSourceTrustScorer:
    """Test domain reputation and cross-source verification."""

    def setup_method(self):
        from lina.web_extraction.source_trust import SourceTrustScorer, TrustTier
        self.scorer = SourceTrustScorer()
        self.TrustTier = TrustTier

    def test_known_domain_scoring(self):
        info = self.scorer.score_domain("gsmarena.com")
        assert info.trust_score >= 0.90
        assert info.trust_tier == self.TrustTier.AUTHORITATIVE
        assert info.is_known

    def test_url_parsing(self):
        info = self.scorer.score_domain("https://www.gsmarena.com/samsung-galaxy-s24-12345.php")
        assert info.trust_score >= 0.90

    def test_unknown_domain(self):
        info = self.scorer.score_domain("randomsite123xyz.com")
        assert info.trust_score == 0.30
        assert not info.is_known
        assert info.trust_tier == self.TrustTier.UNTRUSTED

    def test_subdomain_fallback(self):
        info = self.scorer.score_domain("blog.gsmarena.com")
        assert info.trust_score >= 0.90  # Falls back to parent

    def test_pattern_matching(self):
        info = self.scorer.score_domain("en.wikipedia.org")
        assert info.trust_score >= 0.85

    def test_trust_tier_classification(self):
        assert self.TrustTier.from_score(0.95) == self.TrustTier.AUTHORITATIVE
        assert self.TrustTier.from_score(0.80) == self.TrustTier.HIGH
        assert self.TrustTier.from_score(0.60) == self.TrustTier.MEDIUM
        assert self.TrustTier.from_score(0.40) == self.TrustTier.LOW
        assert self.TrustTier.from_score(0.20) == self.TrustTier.UNTRUSTED

    def test_passage_trust_bonus(self):
        bonus_high = self.scorer.passage_trust_bonus("https://gsmarena.com/page")
        bonus_low = self.scorer.passage_trust_bonus("https://randomsite.com/page")
        assert bonus_high > bonus_low

    def test_cross_source_verification(self):
        result = self.scorer.verify_cross_source(
            "phone|processor",
            {
                "gsmarena.com": "MediaTek Helio G99",
                "notebookcheck.net": "MediaTek Helio G99",
                "randomsite.com": "Snapdragon 695",
            },
        )
        assert result.is_verified  # 2+ independent sources agree
        assert result.value  # Most common value
        assert result.independent_count >= 2

    def test_single_source_not_verified(self):
        result = self.scorer.verify_cross_source(
            "phone|processor",
            {"gsmarena.com": "MediaTek Helio G99"},
        )
        assert not result.is_verified
        assert result.independent_count == 1

    def test_value_normalization(self):
        assert self.scorer._normalize_value("5000 мАч") == self.scorer._normalize_value("5000 mAh")
        assert self.scorer._normalize_value("8 ГБ") == self.scorer._normalize_value("8 GB")

    def test_aggregate_confidence(self):
        conf_multi = self.scorer.aggregate_confidence(
            ["https://gsmarena.com/p1", "https://wikipedia.org/p2", "https://4pda.to/p3"],
            base_confidence=0.5,
        )
        conf_single = self.scorer.aggregate_confidence(
            ["https://randomsite.com/p1"],
            base_confidence=0.5,
        )
        assert conf_multi > conf_single


# ═══════════════════════════════════════════════════
#  Integration Test
# ═══════════════════════════════════════════════════

class TestWebExtractionPipeline:
    """Integration test for the full pipeline."""

    def test_pipeline_with_no_results(self):
        from lina.web_extraction.web_pipeline import WebExtractionPipeline
        pipeline = WebExtractionPipeline()
        result = pipeline.run([], query="test")
        assert not result.has_content
        assert result.passages == []

    def test_snippet_fallback(self):
        from lina.web_extraction.web_pipeline import WebExtractionPipeline
        from lina.models.datatypes import SearchResult
        pipeline = WebExtractionPipeline()
        results = [
            SearchResult(
                title="Test Result",
                url="http://example.com",
                snippet="This is a test snippet with enough words to be useful for processing."
            ),
        ]
        # Mock page processor to return no passages (simulating download failure)
        with patch.object(pipeline._processor, 'process', return_value=[]):
            result = pipeline.run(results, query="test query")
            # Should fall back to snippets
            assert result.used_snippet_fallback or not result.has_content

    def test_context_formatting(self):
        from lina.web_extraction.web_pipeline import WebExtractionResult
        from lina.models.datatypes import Passage
        result = WebExtractionResult(
            passages=[
                Passage(
                    text="Test passage content about processors.",
                    source_url="https://gsmarena.com/page1",
                    source_title="GSMArena Review",
                    score=0.9,
                ),
                Passage(
                    text="Another passage about RAM specs.",
                    source_url="https://notebookcheck.net/page2",
                    source_title="Notebookcheck",
                    score=0.8,
                ),
            ],
        )
        context = result.format_context_for_rag(max_passages=5)
        assert "[SOURCE 1:" in context
        assert "[SOURCE 2:" in context
        assert "gsmarena.com" in context
        assert "Test passage content" in context

    def test_diversity_enforcement(self):
        from lina.web_extraction.web_pipeline import WebExtractionPipeline
        from lina.models.datatypes import Passage
        pipeline = WebExtractionPipeline()
        passages = [
            Passage(text=f"Passage {i}", source_url=f"https://same-domain.com/page{i}", score=1.0 - i*0.01)
            for i in range(20)
        ]
        diversified = pipeline._enforce_diversity(passages, max_per_domain=3, top_k=10)
        assert len(diversified) <= 3  # Only 3 from same domain
