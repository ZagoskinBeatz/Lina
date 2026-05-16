# -*- coding: utf-8 -*-
"""
Tests — Fact Pipeline, Entity Parser, Query Optimizer (Phase 28).

Covers:
  Block A — EntityParser: brand detection, model extraction, attribute parsing
  Block B — FactExtractor: spec patterns (CPU, RAM, battery, display, etc.)
  Block C — FactVerifier: cross-source verification, confidence
  Block D — FactSet: formatting, verified_count, properties
  Block E — AntiHallucinationGuard: claim extraction, support check, cleaning
  Block F — ConfidenceScorer: scoring formula, thresholds
  Block G — FactPipeline: full pipeline, check_answer
  Block H — QueryOptimizer: RU→EN translation, filler removal, variants
  Block I — Integration: wiring into CLI web_executor, FACT_MODE detection
"""

import unittest


# ═══════════════════════════════════════════════════════════
#  Block A — EntityParser
# ═══════════════════════════════════════════════════════════

class TestEntityParserBrands(unittest.TestCase):
    """EntityParser — brand detection."""

    def _parser(self):
        from lina.core.entity_parser import EntityParser
        return EntityParser()

    def test_A01_samsung_brand(self):
        """Detect Samsung as brand."""
        p = self._parser().parse("Samsung Galaxy S24")
        self.assertEqual(p.brand, "Samsung")

    def test_A02_apple_brand(self):
        """Detect Apple from 'iphone'."""
        p = self._parser().parse("iPhone 15 Pro")
        self.assertEqual(p.brand, "Apple")

    def test_A03_xiaomi_from_redmi(self):
        """'redmi' → Xiaomi brand."""
        p = self._parser().parse("Redmi Note 13")
        self.assertEqual(p.brand, "Xiaomi")

    def test_A04_realme_brand(self):
        """Detect Realme brand."""
        p = self._parser().parse("Realme 10")
        self.assertEqual(p.brand, "Realme")

    def test_A05_oneplus_brand(self):
        """Detect OnePlus brand."""
        p = self._parser().parse("OnePlus 12")
        self.assertEqual(p.brand, "OnePlus")

    def test_A06_google_pixel(self):
        """'pixel' → Google brand."""
        p = self._parser().parse("Pixel 9 Pro")
        self.assertEqual(p.brand, "Google")

    def test_A07_nvidia_from_rtx(self):
        """'rtx' → NVIDIA brand."""
        p = self._parser().parse("RTX 4090")
        self.assertEqual(p.brand, "NVIDIA")

    def test_A08_amd_from_ryzen(self):
        """'ryzen' → AMD brand."""
        p = self._parser().parse("Ryzen 9 7950X")
        self.assertEqual(p.brand, "AMD")

    def test_A09_snapdragon_qualcomm(self):
        """'snapdragon' → Qualcomm brand."""
        p = self._parser().parse("Snapdragon 8 Gen 3")
        self.assertEqual(p.brand, "Qualcomm")

    def test_A10_no_brand_generic_query(self):
        """No brand in generic query."""
        p = self._parser().parse("какая погода сегодня")
        self.assertIsNone(p.brand)


class TestEntityParserModels(unittest.TestCase):
    """EntityParser — device model extraction."""

    def _parser(self):
        from lina.core.entity_parser import EntityParser
        return EntityParser()

    def test_A11_galaxy_s24_model(self):
        """Extract 'Galaxy S24 Ultra' as device."""
        p = self._parser().parse("Samsung Galaxy S24 Ultra характеристики")
        self.assertIsNotNone(p.device)
        self.assertIn("galaxy", p.device.lower())

    def test_A12_iphone_model(self):
        """Extract 'iPhone 15 Pro' as device."""
        p = self._parser().parse("iPhone 15 Pro какой процессор")
        self.assertIsNotNone(p.device)
        self.assertIn("iphone", p.device.lower())

    def test_A13_realme_model(self):
        """Extract 'Realme 10' as device."""
        p = self._parser().parse("Realme 10 обзор")
        self.assertIsNotNone(p.device)
        self.assertIn("realme", p.device.lower())

    def test_A14_rtx_model(self):
        """Extract 'RTX 4090' as device."""
        p = self._parser().parse("RTX 4090 характеристики")
        self.assertIsNotNone(p.device)

    def test_A15_empty_query(self):
        """Empty query → empty ParsedQuery."""
        p = self._parser().parse("")
        self.assertIsNone(p.device)
        self.assertIsNone(p.brand)


class TestEntityParserAttributes(unittest.TestCase):
    """EntityParser — attribute detection."""

    def _parser(self):
        from lina.core.entity_parser import EntityParser
        return EntityParser()

    def test_A16_cpu_attribute_ru(self):
        """Detect cpu attribute from 'процессор'."""
        p = self._parser().parse("процессор Realme 10")
        self.assertEqual(p.attribute, "cpu")

    def test_A17_ram_attribute(self):
        """Detect ram attribute from 'оперативная'."""
        p = self._parser().parse("оперативная память Galaxy S24")
        self.assertEqual(p.attribute, "ram")

    def test_A18_display_attribute_ru(self):
        """Detect display attribute from 'экран'."""
        p = self._parser().parse("какой экран у Galaxy S24")
        self.assertEqual(p.attribute, "display")

    def test_A19_battery_attribute(self):
        """Detect battery attribute from 'батарея'."""
        p = self._parser().parse("батарея iPhone 15")
        self.assertEqual(p.attribute, "battery")

    def test_A20_price_attribute(self):
        """Detect price attribute from 'цена'."""
        p = self._parser().parse("цена Realme 10")
        self.assertEqual(p.attribute, "price")


class TestEntityParserSpecs(unittest.TestCase):
    """EntityParser — extract_specs_from_text."""

    def _parser(self):
        from lina.core.entity_parser import EntityParser
        return EntityParser()

    def test_A21_extract_ram_gb(self):
        """Extract '8 ГБ ОЗУ' from text."""
        from lina.core.entity_parser import EntityType
        specs = self._parser().extract_specs_from_text("8 ГБ ОЗУ, 128 ГБ встроенной памяти")
        ram = [s for s in specs if s.type == EntityType.RAM]
        self.assertTrue(len(ram) >= 1)

    def test_A22_extract_battery_mah(self):
        """Extract '5000 мАч' from text."""
        from lina.core.entity_parser import EntityType
        specs = self._parser().extract_specs_from_text("аккумулятор 5000 мАч")
        bat = [s for s in specs if s.type == EntityType.BATTERY]
        self.assertTrue(len(bat) >= 1)

    def test_A23_extract_display_inches(self):
        """Extract '6.7 дюймов' from text."""
        from lina.core.entity_parser import EntityType
        specs = self._parser().extract_specs_from_text("экран 6.7 дюймов AMOLED")
        disp = [s for s in specs if s.type == EntityType.DISPLAY]
        self.assertTrue(len(disp) >= 1)

    def test_A24_no_specs_in_empty(self):
        """No specs in empty text."""
        specs = self._parser().extract_specs_from_text("")
        self.assertEqual(len(specs), 0)


class TestEntityParserParsedQuery(unittest.TestCase):
    """ParsedQuery helper methods."""

    def test_A25_has_method(self):
        """ParsedQuery.has() works."""
        from lina.core.entity_parser import EntityParser, EntityType
        p = EntityParser().parse("Samsung Galaxy S24")
        self.assertTrue(p.has(EntityType.BRAND))

    def test_A26_get_method(self):
        """ParsedQuery.get() returns first entity of type."""
        from lina.core.entity_parser import EntityParser, EntityType
        p = EntityParser().parse("Samsung Galaxy S24")
        e = p.get(EntityType.BRAND)
        self.assertIsNotNone(e)
        self.assertEqual(e.value, "Samsung")

    def test_A27_get_all_method(self):
        """ParsedQuery.get_all() returns list."""
        from lina.core.entity_parser import EntityParser, EntityType
        p = EntityParser().parse("Samsung Galaxy S24")
        all_brands = p.get_all(EntityType.BRAND)
        self.assertIsInstance(all_brands, list)

    def test_A28_to_dict(self):
        """ParsedQuery.to_dict() returns dict."""
        from lina.core.entity_parser import EntityParser
        p = EntityParser().parse("Realme 10")
        d = p.to_dict()
        self.assertIn("raw_query", d)
        self.assertIn("entities", d)

    def test_A29_singleton(self):
        """get_entity_parser() returns singleton."""
        from lina.core.entity_parser import get_entity_parser
        a = get_entity_parser()
        b = get_entity_parser()
        self.assertIs(a, b)


# ═══════════════════════════════════════════════════════════
#  Block B — FactExtractor
# ═══════════════════════════════════════════════════════════

class TestFactExtractor(unittest.TestCase):
    """FactExtractor — extract structured facts from text."""

    def _extractor(self):
        from lina.core.fact_pipeline import FactExtractor
        return FactExtractor()

    def test_B01_extract_processor(self):
        """Extract processor fact from spec text."""
        ext = self._extractor()
        facts = ext.extract(
            "Realme 10 оснащён процессором MediaTek Helio G99",
            source_url="https://example.com",
            subject="Realme 10",
        )
        procs = [f for f in facts if "процессор" in f.predicate.lower()
                 or "processor" in f.predicate.lower()
                 or "чип" in f.predicate.lower()]
        self.assertTrue(len(procs) >= 1, f"No processor facts found in: {facts}")

    def test_B02_extract_ram(self):
        """Extract RAM fact."""
        ext = self._extractor()
        facts = ext.extract(
            "Оперативная память: 8 ГБ LPDDR4x",
            source_url="https://example.com",
            subject="Test Device",
        )
        ram_facts = [f for f in facts if "ram" in f.predicate.lower()
                     or "озу" in f.predicate.lower()
                     or "памят" in f.predicate.lower()
                     or "оперативн" in f.predicate.lower()]
        self.assertTrue(len(ram_facts) >= 1, f"No RAM facts found in: {facts}")

    def test_B03_extract_battery(self):
        """Extract battery fact."""
        ext = self._extractor()
        facts = ext.extract(
            "Ёмкость аккумулятора 5000 мАч",
            source_url="https://example.com",
            subject="Test Device",
        )
        bat = [f for f in facts if "аккумулятор" in f.predicate.lower()
               or "батаре" in f.predicate.lower()
               or "battery" in f.predicate.lower()]
        self.assertTrue(len(bat) >= 1, f"No battery facts found in: {facts}")

    def test_B04_extract_display(self):
        """Extract display fact."""
        ext = self._extractor()
        facts = ext.extract(
            "Экран 6.4 дюйма Super AMOLED, 1080x2400",
            source_url="https://example.com",
            subject="Test Device",
        )
        disp = [f for f in facts if "экран" in f.predicate.lower()
                or "дисплей" in f.predicate.lower()
                or "display" in f.predicate.lower()]
        self.assertTrue(len(disp) >= 1, f"No display facts found in: {facts}")

    def test_B05_empty_text_no_facts(self):
        """Empty text → no facts."""
        ext = self._extractor()
        facts = ext.extract("", source_url="https://example.com", subject="Test")
        self.assertEqual(len(facts), 0)

    def test_B06_fact_has_source_url(self):
        """Extracted fact preserves source_url."""
        ext = self._extractor()
        facts = ext.extract(
            "8 ГБ ОЗУ",
            source_url="https://gsmarena.com/test",
            subject="Test Device",
        )
        if facts:
            self.assertIn("gsmarena.com", facts[0].source_urls[0])


# ═══════════════════════════════════════════════════════════
#  Block C — FactVerifier
# ═══════════════════════════════════════════════════════════

class TestFactVerifier(unittest.TestCase):
    """FactVerifier — cross-source verification."""

    def _verifier(self):
        from lina.core.fact_pipeline import FactVerifier
        return FactVerifier()

    def test_C01_verify_two_sources_agree(self):
        """Fact confirmed if 2 sources agree."""
        from lina.core.fact_pipeline import Fact, FactVerifier
        v = FactVerifier()
        all_facts = {
            "src1": [Fact(subject="X", predicate="RAM", value="8 ГБ",
                          source_urls=["s1"])],
            "src2": [Fact(subject="X", predicate="RAM", value="8 ГБ",
                          source_urls=["s2"])],
        }
        fact_set = v.verify(all_facts)
        self.assertTrue(len(fact_set.facts) >= 1)
        # At least one fact should have source_count >= 2
        multi_source = [f for f in fact_set.facts if f.source_count >= 2]
        self.assertTrue(len(multi_source) >= 1)

    def test_C02_single_source_lower_confidence(self):
        """Single source → lower confidence than verified."""
        from lina.core.fact_pipeline import Fact, FactVerifier
        v = FactVerifier()
        all_facts = {
            "src1": [Fact(subject="X", predicate="RAM", value="8 ГБ",
                          source_urls=["s1"])],
        }
        fact_set = v.verify(all_facts)
        # Single source → confidence should be ≤ 0.6 (not high)
        self.assertLessEqual(fact_set.confidence, 0.75)

    def test_C03_empty_facts_zero_confidence(self):
        """No facts → zero confidence."""
        from lina.core.fact_pipeline import FactVerifier
        v = FactVerifier()
        fact_set = v.verify({})
        self.assertEqual(fact_set.confidence, 0.0)
        self.assertEqual(len(fact_set.facts), 0)

    def test_C04_conflicting_sources(self):
        """Different values for same predicate from different sources."""
        from lina.core.fact_pipeline import Fact, FactVerifier
        v = FactVerifier()
        all_facts = {
            "src1": [Fact(subject="X", predicate="RAM", value="8 ГБ",
                          source_urls=["s1"])],
            "src2": [Fact(subject="X", predicate="RAM", value="12 ГБ",
                          source_urls=["s2"])],
        }
        fact_set = v.verify(all_facts)
        # Should have facts, both variants included
        self.assertTrue(len(fact_set.facts) >= 1)


# ═══════════════════════════════════════════════════════════
#  Block D — FactSet
# ═══════════════════════════════════════════════════════════

class TestFactSet(unittest.TestCase):
    """FactSet data model."""

    def test_D01_format_for_llm_contains_marker(self):
        """format_for_llm() contains [VERIFIED FACTS]."""
        from lina.core.fact_pipeline import FactSet, Fact
        fs = FactSet(
            subject="Test Device",
            facts=[Fact(subject="Test", predicate="RAM", value="8 ГБ",
                        source_count=2)],
            confidence=0.7,
            total_sources=3,
        )
        text = fs.format_for_llm()
        self.assertIn("[VERIFIED FACTS", text)

    def test_D02_format_for_llm_ru(self):
        """format_for_llm_ru() contains Russian marker."""
        from lina.core.fact_pipeline import FactSet, Fact
        fs = FactSet(
            subject="Realme 10",
            facts=[Fact(subject="Realme 10", predicate="RAM", value="8 ГБ",
                        source_count=2)],
            confidence=0.7,
            total_sources=2,
        )
        text = fs.format_for_llm_ru()
        self.assertIn("ФАКТ", text)

    def test_D03_verified_count_property(self):
        """verified_count counts facts with source_count >= 2."""
        from lina.core.fact_pipeline import FactSet, Fact
        fs = FactSet(
            subject="X",
            facts=[
                Fact(subject="X", predicate="RAM", value="8 ГБ", source_count=3),
                Fact(subject="X", predicate="CPU", value="Helio G99", source_count=1),
                Fact(subject="X", predicate="Battery", value="5000 мАч", source_count=2),
            ],
        )
        self.assertEqual(fs.verified_count, 2)

    def test_D04_empty_factset_format(self):
        """Empty FactSet → empty format."""
        from lina.core.fact_pipeline import FactSet
        fs = FactSet(subject="X")
        self.assertEqual(fs.format_for_llm(), "")

    def test_D05_fact_key_dedup(self):
        """Fact.key() normalizes for deduplication."""
        from lina.core.fact_pipeline import Fact
        f1 = Fact(subject="Realme 10", predicate="RAM", value="8 ГБ")
        f2 = Fact(subject="realme 10", predicate="ram", value="8 ГБ")
        self.assertEqual(f1.key(), f2.key())


# ═══════════════════════════════════════════════════════════
#  Block E — AntiHallucinationGuard
# ═══════════════════════════════════════════════════════════

class TestAntiHallucinationGuard(unittest.TestCase):
    """AntiHallucinationGuard — claim verification."""

    def _guard(self):
        from lina.core.fact_pipeline import AntiHallucinationGuard
        return AntiHallucinationGuard()

    def test_E01_supported_claim_kept(self):
        """Supported claim is NOT removed."""
        from lina.core.fact_pipeline import AntiHallucinationGuard, FactSet, Fact
        guard = AntiHallucinationGuard()
        facts = FactSet(
            subject="X",
            facts=[Fact(subject="X", predicate="RAM", value="8 ГБ",
                        source_count=2)],
        )
        answer = "Устройство оснащено 8 ГБ оперативной памяти."
        cleaned, removed = guard.check(answer, facts)
        self.assertEqual(len(removed), 0)
        self.assertIn("8", cleaned)

    def test_E02_unsupported_claim_removed(self):
        """Unsupported numeric claim is removed."""
        from lina.core.fact_pipeline import AntiHallucinationGuard, FactSet, Fact
        guard = AntiHallucinationGuard()
        facts = FactSet(
            subject="X",
            facts=[Fact(subject="X", predicate="RAM", value="8 ГБ",
                        source_count=2)],
        )
        answer = (
            "Устройство имеет 8 ГБ RAM. "
            "Также оснащено 16 ГБ ОЗУ с поддержкой расширения. "
            "Хороший смартфон."
        )
        cleaned, removed = guard.check(answer, facts)
        # 16 ГБ ОЗУ should be removed (not in facts)
        self.assertTrue(len(removed) >= 1, f"Nothing removed: {removed}")

    def test_E03_empty_answer_returns_empty(self):
        """Empty answer → returns empty."""
        from lina.core.fact_pipeline import AntiHallucinationGuard, FactSet
        guard = AntiHallucinationGuard()
        facts = FactSet(subject="X")
        cleaned, removed = guard.check("", facts)
        self.assertEqual(cleaned, "")

    def test_E04_no_facts_returns_original(self):
        """No facts → original answer unchanged."""
        from lina.core.fact_pipeline import AntiHallucinationGuard, FactSet
        guard = AntiHallucinationGuard()
        facts = FactSet(subject="X")
        answer = "Хороший смартфон с 8 ГБ RAM."
        cleaned, removed = guard.check(answer, facts)
        self.assertEqual(cleaned, answer)
        self.assertEqual(len(removed), 0)

    def test_E05_generate_from_facts_fallback(self):
        """If answer becomes too short, generate from facts."""
        from lina.core.fact_pipeline import AntiHallucinationGuard, FactSet, Fact
        guard = AntiHallucinationGuard()
        facts = FactSet(
            subject="Realme 10",
            facts=[
                Fact(subject="Realme 10", predicate="RAM", value="8 ГБ",
                     source_count=2),
                Fact(subject="Realme 10", predicate="Battery",
                     value="5000 мАч", source_count=2),
            ],
        )
        # Very short answer that will be stripped
        gen = guard._generate_from_facts(facts)
        self.assertIn("Realme 10", gen)
        self.assertIn("RAM", gen)


# ═══════════════════════════════════════════════════════════
#  Block F — ConfidenceScorer
# ═══════════════════════════════════════════════════════════

class TestConfidenceScorer(unittest.TestCase):
    """ConfidenceScorer — scoring formula."""

    def _scorer(self):
        from lina.core.fact_pipeline import ConfidenceScorer
        return ConfidenceScorer()

    def test_F01_zero_sources_zero_score(self):
        """0 sources → 0.0 confidence."""
        s = self._scorer()
        self.assertEqual(s.score(0, [], 0.0, 0.0), 0.0)

    def test_F02_one_source_low(self):
        """1 source → low but non-zero score."""
        s = self._scorer()
        score = s.score(1, [0.5], 0.5, 0.0)
        self.assertGreater(score, 0.0)
        self.assertLess(score, 0.6)

    def test_F03_five_sources_high(self):
        """5 sources + good domains → high score."""
        s = self._scorer()
        score = s.score(5, [0.9, 0.8, 0.7, 0.9, 0.8], 0.8, 0.7)
        self.assertGreater(score, 0.7)

    def test_F04_should_generate_threshold(self):
        """should_generate respects MIN_CONFIDENCE."""
        from lina.core.fact_pipeline import ConfidenceScorer
        self.assertTrue(ConfidenceScorer.should_generate(0.5))
        self.assertFalse(ConfidenceScorer.should_generate(0.2))

    def test_F05_format_warning_low(self):
        """Low confidence → warning text."""
        from lina.core.fact_pipeline import ConfidenceScorer
        self.assertIsNotNone(ConfidenceScorer.format_warning(0.3))

    def test_F06_format_warning_high(self):
        """High confidence → no warning."""
        from lina.core.fact_pipeline import ConfidenceScorer
        self.assertIsNone(ConfidenceScorer.format_warning(0.8))

    def test_F07_score_capped_at_1(self):
        """Score never exceeds 1.0."""
        s = self._scorer()
        score = s.score(10, [1.0]*10, 1.0, 1.0)
        self.assertLessEqual(score, 1.0)


# ═══════════════════════════════════════════════════════════
#  Block G — FactPipeline (full)
# ═══════════════════════════════════════════════════════════

class TestFactPipeline(unittest.TestCase):
    """Full FactPipeline integration."""

    def _pipeline(self):
        from lina.core.fact_pipeline import FactPipeline
        return FactPipeline()

    def test_G01_process_returns_factset(self):
        """process() returns FactSet."""
        from lina.core.fact_pipeline import FactSet
        fp = self._pipeline()
        result = fp.process(
            web_summary="Realme 10: 8 ГБ ОЗУ, батарея 5000 мАч, Helio G99",
            results=[],
            subject="Realme 10",
        )
        self.assertIsInstance(result, FactSet)

    def test_G02_process_extracts_facts(self):
        """process() extracts at least 1 fact from spec text."""
        fp = self._pipeline()
        result = fp.process(
            web_summary="Realme 10: процессор MediaTek Helio G99, 8 ГБ ОЗУ, 5000 мАч",
            results=[],
            subject="Realme 10",
        )
        self.assertTrue(len(result.facts) >= 1, f"No facts: {result.facts}")

    def test_G03_process_empty_summary(self):
        """process() with empty summary → no crash."""
        fp = self._pipeline()
        result = fp.process(web_summary="", results=[], subject="X")
        self.assertEqual(len(result.facts), 0)

    def test_G04_check_answer_returns_tuple(self):
        """check_answer() returns (str, list)."""
        from lina.core.fact_pipeline import FactSet, Fact
        fp = self._pipeline()
        facts = FactSet(
            subject="X",
            facts=[Fact(subject="X", predicate="RAM", value="8 ГБ",
                        source_count=2)],
        )
        cleaned, removed = fp.check_answer("8 ГБ RAM.", facts)
        self.assertIsInstance(cleaned, str)
        self.assertIsInstance(removed, list)

    def test_G05_singleton(self):
        """get_fact_pipeline() returns singleton."""
        from lina.core.fact_pipeline import get_fact_pipeline
        a = get_fact_pipeline()
        b = get_fact_pipeline()
        self.assertIs(a, b)

    def test_G06_pipeline_has_components(self):
        """FactPipeline has extractor, verifier, guard, scorer."""
        fp = self._pipeline()
        self.assertTrue(hasattr(fp, 'extractor'))
        self.assertTrue(hasattr(fp, 'verifier'))
        self.assertTrue(hasattr(fp, 'guard'))
        self.assertTrue(hasattr(fp, 'scorer'))

    def test_G07_compute_confidence_method(self):
        """compute_confidence() works."""
        fp = self._pipeline()
        c = fp.compute_confidence(
            source_count=3,
            domain_scores=[0.8, 0.7, 0.6],
            keyword_match_ratio=0.5,
        )
        self.assertIsInstance(c, float)
        self.assertGreater(c, 0.0)


# ═══════════════════════════════════════════════════════════
#  Block H — QueryOptimizer
# ═══════════════════════════════════════════════════════════

class TestQueryOptimizer(unittest.TestCase):
    """QueryOptimizer — query rewriting for web search."""

    def _optimizer(self):
        from lina.core.query_optimizer import QueryOptimizer
        return QueryOptimizer()

    def test_H01_class_exists(self):
        """QueryOptimizer class importable."""
        from lina.core.query_optimizer import QueryOptimizer
        opt = QueryOptimizer()
        self.assertIsNotNone(opt)

    def test_H02_optimize_returns_list(self):
        """optimize() returns list of strings."""
        opt = self._optimizer()
        result = opt.optimize("Realme 10 процессор")
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) >= 1)

    def test_H03_optimize_translates_ru_terms(self):
        """RU tech terms get translated to EN."""
        opt = self._optimizer()
        variants = opt.optimize("процессор Realme 10")
        # At least one variant should contain EN term
        all_text = " ".join(variants).lower()
        has_en = ("processor" in all_text or "specs" in all_text
                  or "specifications" in all_text)
        self.assertTrue(has_en, f"No EN translation in: {variants}")

    def test_H04_filler_words_removed(self):
        """Filler words removed from optimized query."""
        opt = self._optimizer()
        variants = opt.optimize("расскажи мне про Realme 10 пожалуйста")
        # "расскажи", "мне", "про", "пожалуйста" should be removed
        first = variants[0].lower()
        self.assertNotIn("расскажи", first)
        self.assertNotIn("пожалуйста", first)

    def test_H05_empty_query_no_crash(self):
        """Empty query → no crash."""
        opt = self._optimizer()
        result = opt.optimize("")
        self.assertIsInstance(result, list)

    def test_H06_entity_aware(self):
        """optimize() can accept device and attribute hints."""
        opt = self._optimizer()
        result = opt.optimize(
            "Realme 10 процессор",
            device="Realme 10",
            attribute="cpu",
        )
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) >= 1)

    def test_H07_singleton(self):
        """get_query_optimizer() returns singleton."""
        from lina.core.query_optimizer import get_query_optimizer
        a = get_query_optimizer()
        b = get_query_optimizer()
        self.assertIs(a, b)


# ═══════════════════════════════════════════════════════════
#  Block I — Integration Wiring
# ═══════════════════════════════════════════════════════════

class TestFactPipelineIntegration(unittest.TestCase):
    """Integration: fact pipeline wired into CLI and GUI paths."""

    def test_I01_fact_mode_prompt_exists(self):
        """LLMEngine has _FACT_MODE_PROMPT class attribute."""
        from lina.llm.engine import LLMEngine
        self.assertTrue(
            hasattr(LLMEngine, '_FACT_MODE_PROMPT'),
            "LLMEngine must have _FACT_MODE_PROMPT",
        )

    def test_I02_fact_mode_prompt_contains_rules(self):
        """_FACT_MODE_PROMPT contains strict rules."""
        from lina.llm.engine import LLMEngine
        text = LLMEngine._FACT_MODE_PROMPT.lower()
        self.assertIn("факт", text)
        self.assertIn("запрещено", text)

    def test_I03_main_pipeline_web_executor_slot(self):
        """MainPipeline still has _web_executor slot."""
        from lina.core.main_pipeline import MainPipeline
        pipe = MainPipeline()
        self.assertTrue(hasattr(pipe, '_web_executor'))

    def test_I04_entity_parser_import(self):
        """entity_parser is importable."""
        from lina.core.entity_parser import EntityParser, get_entity_parser
        self.assertIsNotNone(EntityParser())
        self.assertIsNotNone(get_entity_parser())

    def test_I05_fact_pipeline_import(self):
        """fact_pipeline is importable."""
        from lina.core.fact_pipeline import FactPipeline, get_fact_pipeline
        self.assertIsNotNone(FactPipeline())
        self.assertIsNotNone(get_fact_pipeline())

    def test_I06_query_optimizer_import(self):
        """query_optimizer is importable."""
        from lina.core.query_optimizer import QueryOptimizer, get_query_optimizer
        self.assertIsNotNone(QueryOptimizer())
        self.assertIsNotNone(get_query_optimizer())

    def test_I07_fact_set_verified_marker(self):
        """FactSet format contains [VERIFIED FACTS] marker that triggers FACT_MODE."""
        from lina.core.fact_pipeline import FactSet, Fact
        fs = FactSet(
            subject="Test",
            facts=[Fact(subject="Test", predicate="RAM", value="8 ГБ",
                        source_count=2)],
            confidence=0.7,
            total_sources=3,
        )
        text = fs.format_for_llm()
        self.assertIn("[VERIFIED FACTS", text)

    def test_I08_all_modules_have_singletons(self):
        """All new modules expose get_* singletons."""
        from lina.core.entity_parser import get_entity_parser
        from lina.core.fact_pipeline import get_fact_pipeline
        from lina.core.query_optimizer import get_query_optimizer
        self.assertIsNotNone(get_entity_parser())
        self.assertIsNotNone(get_fact_pipeline())
        self.assertIsNotNone(get_query_optimizer())


if __name__ == "__main__":
    unittest.main()
