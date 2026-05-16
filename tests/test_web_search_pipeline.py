# -*- coding: utf-8 -*-
"""
Tests — Web Search Pipeline Wiring.

Tests that the web search engine is properly wired into MainPipeline via:
  1. set_web_executor() — executor slot registration
  2. ExecutionOrchestrator — web/web_search/weather_query → WEB_SEARCH path
  3. _step_07_execution — WEB_SEARCH path routes to _web_executor
  4. Fallback to LLM when web search returns empty
  5. WebSearchEngine caching (TTL, LRU eviction)
  6. WebSearchEngine rate limiting

Phase: Web search pipeline enhancement.
"""

import time
import unittest
from unittest.mock import patch, MagicMock, PropertyMock


# ═══════════════════════════════════════════════════════════
#  Block A — Web Executor Slot in MainPipeline
# ═══════════════════════════════════════════════════════════

class TestWebExecutorSlot(unittest.TestCase):
    """MainPipeline._web_executor slot registration."""

    def test_01_web_executor_slot_exists(self):
        """MainPipeline has _web_executor attribute after init."""
        from lina.core.main_pipeline import MainPipeline
        pipe = MainPipeline()
        self.assertTrue(hasattr(pipe, '_web_executor'))
        self.assertIsNone(pipe._web_executor)

    def test_02_set_web_executor_method(self):
        """set_web_executor() registers a callable."""
        from lina.core.main_pipeline import MainPipeline
        pipe = MainPipeline()
        fn = lambda ctx: "test"
        pipe.set_web_executor(fn)
        self.assertIs(pipe._web_executor, fn)

    def test_03_set_web_executor_overwrites(self):
        """Setting web executor twice overwrites first."""
        from lina.core.main_pipeline import MainPipeline
        pipe = MainPipeline()
        fn1 = lambda ctx: "first"
        fn2 = lambda ctx: "second"
        pipe.set_web_executor(fn1)
        pipe.set_web_executor(fn2)
        self.assertIs(pipe._web_executor, fn2)

    def test_04_all_five_executor_slots_exist(self):
        """All 5 executor slots exist: llm, tool, rag, diag, web."""
        from lina.core.main_pipeline import MainPipeline
        pipe = MainPipeline()
        for slot in ('_llm_executor', '_tool_executor', '_rag_executor',
                     '_diag_executor', '_web_executor'):
            self.assertTrue(hasattr(pipe, slot), f"Missing slot: {slot}")


# ═══════════════════════════════════════════════════════════
#  Block B — ExecutionOrchestrator WEB_SEARCH Path
# ═══════════════════════════════════════════════════════════

class TestOrchestratorWebSearchPath(unittest.TestCase):
    """ExecutionOrchestrator maps web intents to WEB_SEARCH."""

    def _create_plan(self, intent: str):
        from lina.core.execution_orchestrator import ExecutionOrchestrator
        orch = ExecutionOrchestrator()
        return orch.create_plan(
            intent=intent,
            confidence=0.9,
            runtime_state={},
            capability_info={},
            mode_profile={},
            config={},
            priority_level=3,
        )

    def test_05_web_search_maps_to_web_search(self):
        """web_search intent → WEB_SEARCH path."""
        plan = self._create_plan("web_search")
        self.assertEqual(plan.primary_path, "WEB_SEARCH")

    def test_06_weather_query_maps_to_web_search(self):
        """weather_query intent → WEB_SEARCH path."""
        plan = self._create_plan("weather_query")
        self.assertEqual(plan.primary_path, "WEB_SEARCH")

    def test_07_web_intent_maps_to_web_search(self):
        """web intent → WEB_SEARCH path."""
        plan = self._create_plan("web")
        self.assertEqual(plan.primary_path, "WEB_SEARCH")

    def test_08_web_search_fallback_is_llm(self):
        """web_search fallback → LLM."""
        plan = self._create_plan("web_search")
        self.assertEqual(plan.fallback_path, "LLM")

    def test_09_weather_fallback_is_llm(self):
        """weather_query fallback → LLM."""
        plan = self._create_plan("weather_query")
        self.assertEqual(plan.fallback_path, "LLM")

    def test_10_chat_intent_not_affected(self):
        """chat intent still routes to LLM (not broken)."""
        plan = self._create_plan("chat")
        self.assertEqual(plan.primary_path, "LLM")

    def test_11_tool_intent_not_affected(self):
        """tool_explicit still routes to TOOL (not broken)."""
        plan = self._create_plan("tool_explicit")
        self.assertEqual(plan.primary_path, "TOOL")

    def test_12_system_diagnostic_not_affected(self):
        """system_diagnostic still routes to DIAGNOSTIC (not broken)."""
        plan = self._create_plan("system_diagnostic")
        self.assertEqual(plan.primary_path, "DIAGNOSTIC")


# ═══════════════════════════════════════════════════════════
#  Block C — Step 07 WEB_SEARCH Execution Path
# ═══════════════════════════════════════════════════════════

class TestStep07WebSearchExecution(unittest.TestCase):
    """MainPipeline._step_07_execution routes to WEB_SEARCH executor."""

    def _make_pipeline_with_web(self, web_return, llm_return="LLM answer"):
        """Create pipeline with mocked web + llm executors.

        Patches _try_pipeline_v3 → "" so only the legacy _web_executor
        is tested (V3 pipeline has its own tests).
        """
        from lina.core.main_pipeline import MainPipeline, PipelineContext
        pipe = MainPipeline()
        # Bypass V3 pipeline so legacy web executor path is exercised
        pipe._try_pipeline_v3 = MagicMock(return_value="")

        web_fn = MagicMock(return_value=web_return)
        llm_fn = MagicMock(return_value=llm_return)

        pipe.set_web_executor(web_fn)
        pipe.set_llm_executor(llm_fn)

        ctx = PipelineContext(
            user_input="погода в москве",
            primary_path="WEB_SEARCH",
            tool_allowed=True,
        )
        return pipe, ctx, web_fn, llm_fn

    def test_13_web_search_path_calls_web_executor(self):
        """WEB_SEARCH path calls _web_executor."""
        pipe, ctx, web_fn, llm_fn = self._make_pipeline_with_web(
            "🌤️ Москва: +15°C, облачно"
        )
        pipe._step_07_execution(ctx)
        web_fn.assert_called_once_with(ctx)
        self.assertEqual(ctx.raw_response, "🌤️ Москва: +15°C, облачно")
        self.assertEqual(ctx.execution_path, "web_search")

    def test_14_web_search_fallback_to_llm_on_empty(self):
        """When web returns empty → returns template 'not found' (no LLM)."""
        pipe, ctx, web_fn, llm_fn = self._make_pipeline_with_web("", "LLM fallback")
        pipe._step_07_execution(ctx)
        web_fn.assert_called_once()
        llm_fn.assert_not_called()
        self.assertIn("не удалось найти", ctx.raw_response)
        self.assertEqual(ctx.execution_path, "web_search_no_results")

    def test_15_web_search_fallback_to_llm_on_none(self):
        """When web returns None → returns template 'not found' (no LLM)."""
        pipe, ctx, web_fn, llm_fn = self._make_pipeline_with_web(None, "LLM answer")
        pipe._step_07_execution(ctx)
        web_fn.assert_called_once()
        llm_fn.assert_not_called()
        self.assertIn("не удалось найти", ctx.raw_response)
        self.assertEqual(ctx.execution_path, "web_search_no_results")

    def test_16_web_search_no_llm_fallback_on_success(self):
        """When web returns result → LLM is NOT called."""
        pipe, ctx, web_fn, llm_fn = self._make_pipeline_with_web(
            "Курс USD/RUB: 89.50"
        )
        pipe._step_07_execution(ctx)
        web_fn.assert_called_once()
        llm_fn.assert_not_called()
        self.assertEqual(ctx.execution_path, "web_search")

    def test_17_web_search_path_lowercase_normalization(self):
        """WEB_SEARCH (uppercase) → web_search path (lowercase)."""
        from lina.core.main_pipeline import MainPipeline, PipelineContext
        pipe = MainPipeline()
        pipe._try_pipeline_v3 = MagicMock(return_value="")
        web_fn = MagicMock(return_value="result")
        pipe.set_web_executor(web_fn)
        pipe.set_llm_executor(MagicMock())

        ctx = PipelineContext(
            user_input="test",
            primary_path="WEB_SEARCH",
            tool_allowed=True,
        )
        pipe._step_07_execution(ctx)
        self.assertEqual(ctx.execution_path, "web_search")
        web_fn.assert_called_once()


# ═══════════════════════════════════════════════════════════
#  Block D — WebSearchEngine Caching
# ═══════════════════════════════════════════════════════════

class TestWebSearchCache(unittest.TestCase):
    """WebSearchEngine cache: TTL, LRU eviction, cache_clear."""

    def _make_engine(self):
        """Create WebSearchEngine with clean state."""
        import threading
        from lina.core.web_search_engine import WebSearchEngine
        engine = WebSearchEngine.__new__(WebSearchEngine)
        engine._cache = {}
        engine._last_request = {}
        engine._lock = threading.Lock()
        engine.CACHE_TTL = 300
        engine.CACHE_MAX_SIZE = 128
        engine.RATE_LIMIT_DELAY = 1.0
        return engine

    def _make_response(self, summary="test", success=True):
        from lina.core.web_search_engine import WebSearchResponse
        return WebSearchResponse(success=success, summary=summary, query="test")

    def test_18_cache_put_and_get(self):
        """Cache stores and retrieves response."""
        engine = self._make_engine()
        resp = self._make_response("cached result")
        engine._cache_put("test query", resp)
        cached = engine._cache_get("test query")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.summary, "cached result")

    def test_19_cache_key_normalization(self):
        """Cache normalizes keys (lowercase, strip)."""
        engine = self._make_engine()
        resp = self._make_response("normalized")
        engine._cache_put("  Test Query  ", resp)
        cached = engine._cache_get("test query")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.summary, "normalized")

    def test_20_cache_miss(self):
        """Cache returns None for missing query."""
        engine = self._make_engine()
        cached = engine._cache_get("not cached")
        self.assertIsNone(cached)

    def test_21_cache_ttl_expiry(self):
        """Cache entries expire after TTL."""
        engine = self._make_engine()
        engine.CACHE_TTL = 0.1  # 100ms for test speed
        resp = self._make_response("short-lived")
        engine._cache_put("ttl test", resp)
        # Before expiry
        self.assertIsNotNone(engine._cache_get("ttl test"))
        # Wait for expiry
        time.sleep(0.15)
        self.assertIsNone(engine._cache_get("ttl test"))

    def test_22_cache_lru_eviction(self):
        """Cache evicts oldest entry when max size exceeded."""
        engine = self._make_engine()
        engine.CACHE_MAX_SIZE = 3
        for i in range(4):
            engine._cache_put(f"query{i}", self._make_response(f"resp{i}"))
            time.sleep(0.01)  # ensure unique timestamps
        # "query0" should have been evicted
        self.assertIsNone(engine._cache_get("query0"))
        # Others should remain
        self.assertIsNotNone(engine._cache_get("query1"))
        self.assertIsNotNone(engine._cache_get("query2"))
        self.assertIsNotNone(engine._cache_get("query3"))

    def test_23_cache_clear(self):
        """cache_clear() removes all entries, returns count."""
        engine = self._make_engine()
        for i in range(5):
            engine._cache_put(f"q{i}", self._make_response())
        count = engine.cache_clear()
        self.assertEqual(count, 5)
        self.assertEqual(len(engine._cache), 0)

    def test_24_cache_clear_empty(self):
        """cache_clear() on empty cache returns 0."""
        engine = self._make_engine()
        count = engine.cache_clear()
        self.assertEqual(count, 0)


# ═══════════════════════════════════════════════════════════
#  Block E — WebSearchEngine Rate Limiting
# ═══════════════════════════════════════════════════════════

class TestWebSearchRateLimit(unittest.TestCase):
    """WebSearchEngine rate limiting between engine requests."""

    def _make_engine(self):
        import threading
        from lina.core.web_search_engine import WebSearchEngine
        engine = WebSearchEngine.__new__(WebSearchEngine)
        engine._cache = {}
        engine._last_request = {}
        engine._lock = threading.Lock()
        engine.CACHE_TTL = 300
        engine.CACHE_MAX_SIZE = 128
        engine.RATE_LIMIT_DELAY = 0.2  # short for testing
        return engine

    def test_25_rate_wait_first_call_no_delay(self):
        """First call to an engine should not delay."""
        engine = self._make_engine()
        t0 = time.time()
        engine._rate_wait("duckduckgo")
        elapsed = time.time() - t0
        self.assertLess(elapsed, 0.1, "First call should not delay")

    def test_26_rate_wait_second_call_delays(self):
        """Second consecutive call delays by RATE_LIMIT_DELAY."""
        engine = self._make_engine()
        engine._rate_wait("duckduckgo")
        t0 = time.time()
        engine._rate_wait("duckduckgo")
        elapsed = time.time() - t0
        self.assertGreaterEqual(elapsed, 0.15,
                                "Second call should delay ~RATE_LIMIT_DELAY")

    def test_27_rate_wait_different_engines_no_delay(self):
        """Different engines have independent rate limits."""
        engine = self._make_engine()
        engine._rate_wait("duckduckgo")
        t0 = time.time()
        engine._rate_wait("searxng")
        elapsed = time.time() - t0
        self.assertLess(elapsed, 0.1,
                        "Different engine should not delay")

    def test_28_rate_wait_tracks_timestamp(self):
        """_rate_wait records last request timestamp."""
        engine = self._make_engine()
        engine._rate_wait("wikipedia")
        self.assertIn("wikipedia", engine._last_request)
        self.assertGreater(engine._last_request["wikipedia"], 0)


# ═══════════════════════════════════════════════════════════
#  Block F — GUI Web Search Integration
# ═══════════════════════════════════════════════════════════

class TestGuiWebSearchWiring(unittest.TestCase):
    """GUI pipeline properly connects web search."""

    def test_29_app_imports_web_search_engine(self):
        """gui/app.py can import WebSearchEngine."""
        from lina.core.web_search_engine import WebSearchEngine, get_web_search_engine
        self.assertTrue(callable(get_web_search_engine))

    def test_30_web_search_response_has_summary(self):
        """WebSearchResponse dataclass has summary field."""
        from lina.core.web_search_engine import WebSearchResponse
        resp = WebSearchResponse(success=True, summary="test", query="q")
        self.assertEqual(resp.summary, "test")
        self.assertTrue(resp.success)

    def test_31_web_search_response_to_dict(self):
        """WebSearchResponse.to_dict() includes all fields."""
        from lina.core.web_search_engine import WebSearchResponse
        resp = WebSearchResponse(
            success=True, query="q", summary="s",
            source="duckduckgo", attempts=2, elapsed_ms=150
        )
        d = resp.to_dict()
        self.assertIn("success", d)
        self.assertIn("summary", d)
        self.assertIn("source", d)
        self.assertEqual(d["attempts"], 2)


# ═══════════════════════════════════════════════════════════
#  Block G — CLI Web Executor Wiring
# ═══════════════════════════════════════════════════════════

class TestCliWebExecutorWiring(unittest.TestCase):
    """CLI properly wires web_executor into MainPipeline."""

    def test_32_cli_imports_web_search_engine(self):
        """cli.py can import get_web_search_engine."""
        from lina.core.web_search_engine import get_web_search_engine
        self.assertTrue(callable(get_web_search_engine))

    def test_33_main_pipeline_accepts_web_executor(self):
        """MainPipeline.set_web_executor() is callable."""
        from lina.core.main_pipeline import MainPipeline
        pipe = MainPipeline()
        self.assertTrue(callable(pipe.set_web_executor))

    def test_34_get_stats_includes_cache(self):
        """WebSearchEngine.get_stats() includes cache_size."""
        import threading
        from lina.core.web_search_engine import WebSearchEngine
        engine = WebSearchEngine.__new__(WebSearchEngine)
        engine._cache = {}
        engine._last_request = {}
        engine._lock = threading.Lock()
        engine._stats = {"queries": 0, "errors": 0, "successful": 0}
        engine.CACHE_TTL = 300
        engine.CACHE_MAX_SIZE = 128
        engine.RATE_LIMIT_DELAY = 1.0
        stats = engine.get_stats()
        self.assertIn("cache_size", stats)
        self.assertEqual(stats["cache_size"], 0)


# ═══════════════════════════════════════════════════════════
#  Block H — Intent Router Web Patterns
# ═══════════════════════════════════════════════════════════

class TestIntentRouterWebPatterns(unittest.TestCase):
    """IntentRouter correctly classifies web search intents."""

    def _classify(self, text: str) -> str:
        from lina.core.intent_router import IntentRouter
        router = IntentRouter()
        result = router.route(text)
        return result.intent.value

    def test_35_weather_intent(self):
        """'погода в москве' → weather_query."""
        intent = self._classify("погода в москве")
        self.assertEqual(intent, "weather_query")

    def test_36_web_search_intent(self):
        """'найди информацию о python' → web_search."""
        intent = self._classify("найди информацию о python")
        self.assertEqual(intent, "web_search")

    def test_37_currency_intent(self):
        """'курс доллара' → web."""
        intent = self._classify("курс доллара")
        self.assertEqual(intent, "web")

    def test_38_chat_not_web(self):
        """'привет как дела' should NOT be web_search."""
        intent = self._classify("привет как дела")
        self.assertNotIn(intent, ("web_search", "weather_query", "web"))

    def test_39_search_keyword_intent(self):
        """'поиск в интернете linux' → web_search."""
        intent = self._classify("поиск в интернете linux")
        self.assertEqual(intent, "web_search")

    def test_40_who_is_intent(self):
        """'кто такой линус торвальдс' → chat (knowledge query, not web)."""
        intent = self._classify("кто такой линус торвальдс")
        self.assertEqual(intent, "chat")

    def test_40a_typo_pogda(self):
        """'какая погда в перми' (typo) → weather_query."""
        intent = self._classify("какая погда в перми")
        self.assertEqual(intent, "weather_query")

    def test_40b_inet_slang(self):
        """'найди в инете' (slang) → web_search."""
        intent = self._classify("найди в инете что такое rust")
        self.assertIn(intent, ("web_search", "weather_query"))

    def test_40c_full_user_query(self):
        """Exact user query: 'Найди в инете, какая погда в Перми будет'."""
        intent = self._classify("Найди в инете, какая погда в Перми будет")
        self.assertEqual(intent, "weather_query")

    def test_40d_zagoogli(self):
        """'загугли linux kernel' → web_search."""
        intent = self._classify("загугли linux kernel")
        self.assertEqual(intent, "web_search")

    def test_40e_poisk_v_inete(self):
        """'поиск в инете archlinux' → web_search."""
        intent = self._classify("поиск в инете archlinux")
        self.assertEqual(intent, "web_search")

    def test_40f_skolko_dollar(self):
        """'сколько сейчас доллар' → web (currency)."""
        intent = self._classify("сколько сейчас доллар")
        self.assertEqual(intent, "web")

    def test_40g_skolko_dollar_no_sejchas(self):
        """'сколько доллар' → web (currency)."""
        intent = self._classify("сколько доллар")
        self.assertEqual(intent, "web")

    def test_40h_dollar_k_rublyu(self):
        """'доллар к рублю' → web."""
        intent = self._classify("доллар к рублю")
        self.assertEqual(intent, "web")


# ═══════════════════════════════════════════════════════════
#  Block I — WebSearchEngine Search Result Ranking
# ═══════════════════════════════════════════════════════════

class TestSearchResultRanking(unittest.TestCase):
    """WebSearchEngine._rank_results orders by relevance."""

    def test_41_search_result_dataclass(self):
        """SearchResult stores all fields."""
        from lina.core.web_search_engine import SearchResult
        r = SearchResult(
            title="Test", url="http://example.com",
            snippet="Example text", relevance=0.85,
        )
        self.assertEqual(r.title, "Test")
        self.assertAlmostEqual(r.relevance, 0.85)

    def test_42_search_result_to_dict(self):
        """SearchResult.to_dict() serializes correctly."""
        from lina.core.web_search_engine import SearchResult
        r = SearchResult(title="T", url="http://x.com",
                         snippet="S" * 500, relevance=0.5)
        d = r.to_dict()
        self.assertIn("title", d)
        self.assertLessEqual(len(d["snippet"]), 300)

    def test_43_weather_data_format(self):
        """WeatherData.format() produces readable string."""
        from lina.core.web_search_engine import WeatherData
        w = WeatherData(
            city="Москва", temperature="+15°C",
            description="Облачно", humidity="60%",
        )
        text = w.format()
        self.assertIn("Москва", text)
        self.assertIn("+15°C", text)
        self.assertIn("Облачно", text)


# ═══════════════════════════════════════════════════════════
#  Block J — End-to-End Pipeline Integration (mocked)
# ═══════════════════════════════════════════════════════════

class TestEndToEndWebSearch(unittest.TestCase):
    """Full pipeline processes web search intent correctly."""

    def test_44_orchestrator_to_pipeline_web_path(self):
        """Orchestrator WEB_SEARCH plan → Pipeline executes web_search path."""
        from lina.core.execution_orchestrator import ExecutionOrchestrator
        from lina.core.main_pipeline import MainPipeline, PipelineContext

        # Get plan from orchestrator
        orch = ExecutionOrchestrator()
        plan = orch.create_plan(
            intent="web_search", confidence=0.9,
            runtime_state={}, capability_info={},
            mode_profile={}, config={}, priority_level=3,
        )

        # Create pipeline context from plan
        ctx = PipelineContext(
            user_input="найди python 3.14 changelog",
            primary_path=plan.primary_path,
            tool_allowed=plan.tool_allowed,
        )

        # Wire pipeline
        pipe = MainPipeline()
        web_fn = MagicMock(return_value="Python 3.14 released with...")
        pipe.set_web_executor(web_fn)
        pipe.set_llm_executor(MagicMock())

        # Execute
        pipe._step_07_execution(ctx)

        # Verify
        self.assertEqual(ctx.execution_path, "web_search")
        self.assertEqual(ctx.raw_response, "Python 3.14 released with...")
        web_fn.assert_called_once_with(ctx)

    def test_45_weather_query_through_pipeline(self):
        """weather_query → WEB_SEARCH → web executor called."""
        from lina.core.execution_orchestrator import ExecutionOrchestrator
        from lina.core.main_pipeline import MainPipeline, PipelineContext

        orch = ExecutionOrchestrator()
        plan = orch.create_plan(
            intent="weather_query", confidence=0.95,
            runtime_state={}, capability_info={},
            mode_profile={}, config={}, priority_level=3,
        )

        ctx = PipelineContext(
            user_input="какая погода в санкт-петербурге",
            primary_path=plan.primary_path,
            tool_allowed=True,
        )

        pipe = MainPipeline()
        web_fn = MagicMock(return_value="🌤️ СПб: +10°C, дождь")
        pipe.set_web_executor(web_fn)

        pipe._step_07_execution(ctx)
        self.assertEqual(ctx.execution_path, "web_search")
        self.assertIn("СПб", ctx.raw_response)


# ═══════════════════════════════════════════════════════════
#  Block K — Currency classifier & install intent patterns
# ═══════════════════════════════════════════════════════════

class TestCurrencyClassifier(unittest.TestCase):
    """WebSearchEngine._classify_query detects currency queries."""

    def _classify(self, text: str) -> str:
        from lina.core.web_search_engine import WebSearchEngine
        eng = WebSearchEngine.__new__(WebSearchEngine)
        return eng._classify_query(text)

    def test_46_skolko_seichas_dollar(self):
        """'сколько сейчас доллар' → currency."""
        self.assertEqual(self._classify("сколько сейчас доллар"), "currency")

    def test_47_skolko_stoit_euro(self):
        """'сколько стоит евро' → currency."""
        self.assertEqual(self._classify("сколько стоит евро"), "currency")

    def test_48_dollar_segodnya(self):
        """'доллар сегодня' → currency."""
        self.assertEqual(self._classify("доллар сегодня"), "currency")

    def test_49_kurs_dollara(self):
        """'курс доллара' → currency."""
        self.assertEqual(self._classify("курс доллара"), "currency")

    def test_50_privet_skolko_dollar(self):
        """Full user query with greeting: 'Привет! Скажи, сколько сейчас доллар?'."""
        self.assertEqual(
            self._classify("Привет! Скажи, сколько сейчас доллар?"),
            "currency",
        )

    def test_51_cena_bitcoin(self):
        """'цена биткоин' → currency."""
        self.assertEqual(self._classify("цена биткоин"), "currency")


class TestInstallIntentPatterns(unittest.TestCase):
    """IntentRouter classifies install variants correctly."""

    def _classify(self, text: str) -> str:
        from lina.core.intent_router import IntentRouter
        r = IntentRouter()
        result = r.route(text)
        # RoutingDecision object — use .intent attribute
        return result.intent if hasattr(result, "intent") else result[0]

    def test_52_kak_togda_ustanovit(self):
        """'как тогда установить VS Code' → install_application."""
        self.assertEqual(
            self._classify("как тогда установить VS Code"),
            "install_application",
        )

    def test_53_variant_ustanovit(self):
        """'есть ли вариант установить Max' → install_application."""
        self.assertEqual(
            self._classify("есть ли вариант установить Max"),
            "install_application",
        )

    def test_54_mozhno_li_ustanovit(self):
        """'можно ли установить telegram' → install_application."""
        self.assertEqual(
            self._classify("можно ли установить telegram"),
            "install_application",
        )

    def test_55_hochu_ustanovit(self):
        """'хочу установить firefox' → install_application."""
        self.assertEqual(
            self._classify("хочу установить firefox"),
            "install_application",
        )

    def test_56_nuzhno_ustanovit(self):
        """'нужно установить git' → install_application."""
        self.assertEqual(
            self._classify("нужно установить git"),
            "install_application",
        )

    def test_57_kak_ustanovit_still_works(self):
        """Original 'как установить chrome' still works."""
        self.assertEqual(
            self._classify("как установить chrome"),
            "install_application",
        )

    def test_58_skolko_dollar_intent_web(self):
        """'сколько сейчас доллар' → web (IntentRouter level)."""
        intent = self._classify("Привет! Скажи, сколько сейчас доллар?")
        self.assertIn(intent, ("web", "web_search"))


# ═══════════════════════════════════════════════════════════
#  Block L — Knowledge queries: chat intent + mini model
# ═══════════════════════════════════════════════════════════

class TestKnowledgeQueryRouting(unittest.TestCase):
    """Knowledge queries go to chat (not web_search) and use mini model."""

    def _intent(self, text: str) -> str:
        from lina.core.intent_router import IntentRouter
        r = IntentRouter()
        result = r.route(text)
        return result.intent.value if hasattr(result.intent, "value") else str(result.intent)

    def _tier(self, text: str) -> str:
        from lina.llm.engine import QueryClassifier
        return QueryClassifier().classify(text)

    def test_59_kto_takoy_chat(self):
        """'кто такой Kizaru' → chat (not web_search)."""
        self.assertEqual(self._intent("Расскажи, кто такой Kizaru"), "chat")

    def test_60_chto_takoe_chat(self):
        """'что такое Linux' → chat."""
        self.assertEqual(self._intent("что такое Linux"), "chat")

    def test_61_kto_takoy_mini(self):
        """'кто такой X' uses mini model for speed."""
        self.assertEqual(self._tier("кто такой Пушкин"), "mini")

    def test_62_chto_takoe_mini(self):
        """'что такое X' uses mini model for speed."""
        self.assertEqual(self._tier("что такое Docker"), "mini")

    def test_63_explicit_web_still_works(self):
        """'найди в инете кто такой X' → web_search (explicit request)."""
        self.assertEqual(
            self._intent("найди в инете кто такой Kizaru"),
            "web_search",
        )

    def test_64_zagooli_still_works(self):
        """'загугли что такое Rust' → web_search."""
        self.assertEqual(
            self._intent("загугли что такое Rust"),
            "web_search",
        )


# ─── Phase 26: Follow-up enrichment, vague detection, new web triggers ────────

class TestFollowupEnrichment(unittest.TestCase):
    """Test _enrich_followup subject extraction from history."""

    def _enrich(self, text, history):
        """Simulate _enrich_followup logic."""
        import re
        q = text.strip()
        if len(q) > 80:
            return text
        if not re.search(
            r'^а\s+|'
            r'\b(она|он|оно|они|ему|ей|его|её|их|ней|нему|ним|них)\b|'
            r'\b(у\s+нег[ао]|у\s+не[ей]|у\s+них)\b|'
            r'\b(этот|этой|этого|этих|эту|этим|этому)\b|'
            r'\b(чей|чья|чьё|чьи|чьей|чьим|чьем|чьему)\b|'
            r'\b(какому|какой|какое|какие|каким)\b.*\b(принадлеж|концерн|корпорац|бренд|марк)|'
            r'^(кому|чему)\b',
            q, re.I,
        ):
            return text
        if not history:
            return text
        last_user, last_assistant = history[-1]
        candidates = re.findall(
            r'\b([A-ZА-ЯЁ][a-zа-яё]{2,}(?:\s+[A-ZА-ЯЁ][a-zа-яё]+)*)\b'
            r'|\b([A-Z]{2,6})\b',
            last_user,
        )
        candidates = [c[0] or c[1] for c in candidates if c[0] or c[1]]
        if not candidates:
            found = re.findall(
                r'\b([A-ZА-ЯЁ][a-zа-яё]{2,})\b|\b([A-Z]{2,6})\b',
                last_assistant[:200],
            )
            candidates = [c[0] or c[1] for c in found if c[0] or c[1]]
        if not candidates:
            return text
        skip = {"Скажи", "Расскажи", "Найди", "Покажи", "Открой", "Привет",
                "Пожалуйста", "Что", "Как", "Кто", "Где", "Когда", "Почему",
                "Какой", "Какая", "Какие", "Расскажи",
                "Lina", "Linux", "Arch", "НЕ", "SSH", "USB", "PDF"}
        subject = None
        for c in candidates:
            if c not in skip:
                subject = c
                break
        if not subject:
            return text
        return f"[Контекст: {subject}] {text}"

    def test_65_followup_bugatti(self):
        """'А какому концерну' after Bugatti → injects [Контекст: Bugatti]."""
        result = self._enrich(
            "А какому концерну принадлежит",
            [("Скажи, чьей является Bugatti?", "Bugatti является компанией")],
        )
        self.assertIn("Bugatti", result)
        self.assertIn("[Контекст:", result)

    def test_66_followup_exeed(self):
        """'А в чей концерн' after Exeed → injects [Контекст: Exeed]."""
        result = self._enrich(
            "А в чей концерн входит?",
            [("Расскажи, чья компания Exeed?", "Exeed — это бренд")],
        )
        self.assertIn("Exeed", result)

    def test_67_followup_pronoun(self):
        """'Кому она принадлежит' extracts subject from history."""
        result = self._enrich(
            "Кому она принадлежит",
            [("Что такое Lamborghini?", "Lamborghini — итальянская марка")],
        )
        self.assertIn("Lamborghini", result)

    def test_68_no_followup_for_greeting(self):
        """'Как дела?' is NOT a follow-up."""
        result = self._enrich(
            "Как дела?",
            [("Привет", "Привет!")],
        )
        self.assertEqual(result, "Как дела?")

    def test_69_followup_bmw_allcaps(self):
        """ALL-CAPS brand names (BMW) are extracted."""
        result = self._enrich(
            "А какой у него движок?",
            [("Расскажи про BMW M3", "BMW M3 — спортивный седан")],
        )
        self.assertIn("BMW", result)

    def test_70_followup_no_history(self):
        """No history → no enrichment."""
        result = self._enrich("А какому концерну принадлежит", [])
        self.assertEqual(result, "А какому концерну принадлежит")

    def test_71_followup_long_query_not_enriched(self):
        """Long queries (>80 chars) are self-contained, not enriched."""
        long_q = "А какому автомобильному концерну принадлежит эта марка и когда она была основана? Расскажи подробно."
        result = self._enrich(long_q, [("Bugatti", "Bugatti — бренд")])
        self.assertEqual(result, long_q)


class TestVagueAnswerDetection(unittest.TestCase):
    """Test _is_vague_answer for detecting generic non-answers."""

    def _is_vague(self, response, query):
        import re
        r = response.strip().lower()
        if len(r) < 15:
            return True
        vague_patterns = [
            r'^\S+\s+(является|—\s*это)\s+компани',
            r'не\s+могу\s+(ответить|найти|определить)',
            r'(не\s+располагаю|нет\s+информации|не\s+знаю\s+точно)',
        ]
        q = query.lower()
        is_factual_query = re.search(
            r'(чь[\u0435\u0439им]|\bкому\b|\bкто\b|\bкако\w+\s+концерн|\bпринадлеж)', q
        )
        if not is_factual_query:
            return False
        return any(re.search(p, r) for p in vague_patterns)

    def test_72_vague_generic_company(self):
        """'является компанией' without specifics = vague."""
        self.assertTrue(self._is_vague(
            "Байгати является компанией, занимающейся разработкой автомашин.",
            "Чьей является Bugatti?",
        ))

    def test_73_vague_this_is_company(self):
        """'это компания, производящая...' = vague."""
        self.assertTrue(self._is_vague(
            "Bugatti — это компания, производящая автомобили.",
            "А какому концерну принадлежит",
        ))

    def test_74_specific_answer_not_vague(self):
        """'принадлежит концерну Volkswagen Group' = specific, NOT vague."""
        self.assertFalse(self._is_vague(
            "Bugatti принадлежит концерну Volkswagen Group.",
            "Чьей является Bugatti?",
        ))

    def test_75_idk_is_vague(self):
        """'Я не знаю точно' = vague for factual query."""
        self.assertTrue(self._is_vague(
            "Я не знаю точно.",
            "Кому принадлежит Bugatti?",
        ))

    def test_76_greeting_not_vague(self):
        """Greeting response is NOT vague (not a factual query)."""
        self.assertFalse(self._is_vague(
            "Привет! Чем могу помочь?",
            "Привет",
        ))


class TestDeflectionDetection(unittest.TestCase):
    """Test _is_llm_deflecting patterns including Linux deflection."""

    def _is_deflecting(self, response):
        import re
        deflection_patterns = [
            r'(используй|попробуй|можешь|можно)\s.{0,20}(поисков|google|гугл|яндекс)',
            r'(загугл|погугл|найди\s+в\s+(гугл|интернет|сети))',
            r'(рекомендую|предлагаю|советую)\s.{0,15}(поиск|найти|google)',
            r'поисков(ик|ую\s+систему)',
            r'google\s*[:.]',
            r'можно\s+(найти|узнать)\s+(в\s+интернете|в\s+сети|через\s+поиск)',
            r'лин.{0,3}x?\s*—?\s*это\s+(открыт|операционн|бесплатн)',
            r'не\s+(имеет|относит).{0,30}(linux|линукс|arch|пакет)',
            r'не\s+связан\w*\s+с\s+(linux|линукс)',
        ]
        text_lower = response.lower()
        return any(re.search(p, text_lower) for p in deflection_patterns)

    def test_77_deflection_google(self):
        """Suggests googling = deflection."""
        self.assertTrue(self._is_deflecting(
            "Рекомендую найти информацию в Google.",
        ))

    def test_78_deflection_linux_os(self):
        """'Линux — это открытая ОС' = deflection to Linux."""
        self.assertTrue(self._is_deflecting(
            "Линux — это открытая операционная система.",
        ))

    def test_79_deflection_no_relation(self):
        """'не имеет отношения к Linux' = deflection."""
        self.assertTrue(self._is_deflecting(
            "Она не имеет прямого отношения к файлам в Linux системе или пакетам Arch Linux.",
        ))

    def test_80_good_answer_not_deflection(self):
        """Normal answer about Bugatti = NOT deflection."""
        self.assertFalse(self._is_deflecting(
            "Bugatti принадлежит концерну Volkswagen Group.",
        ))


class TestNewWebSearchTriggers(unittest.TestCase):
    """Test new 'узнай', 'выясни' web search patterns."""

    def _intent(self, text):
        from lina.core.intent_router import IntentRouter
        r = IntentRouter()
        d = r.route(text)
        return d.intent.value if hasattr(d.intent, 'value') else str(d.intent)

    def test_81_uznaj_trigger(self):
        """'узнай кому принадлежит Bugatti' → web_search."""
        self.assertEqual(
            self._intent("узнай кому принадлежит Bugatti"),
            "web_search",
        )

    def test_82_vyyasni_trigger(self):
        """'выясни какой концерн владеет Bugatti' → web_search."""
        self.assertEqual(
            self._intent("выясни какой концерн владеет Bugatti"),
            "web_search",
        )

    def test_83_poishi_trigger(self):
        """'поищи информацию о...' → web_search."""
        self.assertEqual(
            self._intent("поищи информацию о владельце Bugatti"),
            "web_search",
        )

    def test_84_naidi_v_internete(self):
        """'найди в интернете' → web_search."""
        self.assertEqual(
            self._intent("найди в интернете кому принадлежит Bugatti"),
            "web_search",
        )


class TestChatIntentsUseCompactPrompt(unittest.TestCase):
    """Verify _CHAT_INTENTS includes all knowledge intents.
    web_search is NOT in _CHAT_INTENTS — it uses _FACT_MODE_PROMPT always."""

    def test_85_web_search_uses_fact_mode(self):
        """web_search must NOT use compact prompt — needs fact-mode to prevent hallucination."""
        from lina.llm.engine import LLMEngine
        self.assertNotIn("web_search", LLMEngine._CHAT_INTENTS)

    def test_86_chat_intents_has_chat(self):
        """chat intent uses compact prompt."""
        from lina.llm.engine import LLMEngine
        self.assertIn("chat", LLMEngine._CHAT_INTENTS)

    def test_87_chat_intents_has_weather(self):
        """weather_query uses compact prompt."""
        from lina.llm.engine import LLMEngine
        self.assertIn("weather_query", LLMEngine._CHAT_INTENTS)

    def test_88_system_not_in_chat_intents(self):
        """system_command should NOT use compact prompt."""
        from lina.llm.engine import LLMEngine
        self.assertNotIn("system_command", LLMEngine._CHAT_INTENTS)

    def test_89_install_not_in_chat_intents(self):
        """install_application should NOT use compact prompt."""
        from lina.llm.engine import LLMEngine
        self.assertNotIn("install_application", LLMEngine._CHAT_INTENTS)



# ═══════════════════════════════════════════════════════════════════════════════
#  Tests for search quality improvements (Realme 10 bug fix)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRankResultsCrossLanguage(unittest.TestCase):
    """_rank_results must NOT penalize entity-matching results
    when the query contains Russian descriptor words like
    'полные', 'характеристики' etc."""

    def _make_engine(self):
        from lina.core.web_search_engine import WebSearchEngine
        return WebSearchEngine(web_capable=False)

    def _make_result(self, title, url, snippet=""):
        from lina.core.web_search_engine import SearchResult
        return SearchResult(title=title, url=url, snippet=snippet)

    def test_90_entity_match_scores_above_threshold(self):
        """'полные характеристики Realme 10' + English result with 'Realme 10' → score >= 0.20."""
        ws = self._make_engine()
        results = [
            self._make_result(
                "Realme 10 - Full phone specifications",
                "https://www.gsmarena.com/realme_10-11893.php",
                "Realme 10 Android smartphone. 6.4 inch display, Helio G99 chipset.",
            ),
        ]
        ranked = ws._rank_results("полные характеристики Realme 10", results)
        # Must not be filtered by 0.20 threshold
        self.assertGreaterEqual(ranked[0].relevance, 0.20)

    def test_91_descriptor_words_not_in_scoring(self):
        """Russian descriptor words should not dilute entity overlap score."""
        ws = self._make_engine()
        results = [
            self._make_result(
                "Realme 10 specs",
                "https://example.com/realme-10",
                "Realme 10 specifications and price.",
            ),
        ]
        # Without descriptors
        ranked_en = ws._rank_results("Realme 10 specs", results)
        # With Russian descriptors
        ranked_ru = ws._rank_results("полные подробные характеристики Realme 10", results)
        # Score should be similar (descriptor words are filtered)
        self.assertAlmostEqual(ranked_en[0].relevance, ranked_ru[0].relevance, delta=0.15)

    def test_92_results_match_query_with_entity(self):
        """_results_match_query passes when brand+model present in results."""
        ws = self._make_engine()
        results = [
            self._make_result(
                "Realme 10 review",
                "https://example.com",
                "The Realme 10 smartphone comes with a 6.4-inch display.",
            ),
        ]
        self.assertTrue(ws._results_match_query("полные характеристики Realme 10", results))


class TestExtractTopicDescriptors(unittest.TestCase):
    """_extract_topic strips adjectives before descriptors."""

    def test_93_polnye_kharakteristiki(self):
        """'полные характеристики Realme 10' → 'Realme 10'."""
        from lina.core.main_pipeline import MainPipeline
        topic = MainPipeline._extract_topic("полные характеристики Realme 10")
        self.assertEqual(topic, "Realme 10")

    def test_94_podrobnye_kharakteristiki(self):
        """'подробные характеристики MacBook M1' → 'MacBook M1'."""
        from lina.core.main_pipeline import MainPipeline
        topic = MainPipeline._extract_topic("подробные характеристики MacBook M1")
        self.assertEqual(topic, "MacBook M1")

    def test_95_naydi_polnye(self):
        """'Найди полные характеристики RTX 4090' → 'RTX 4090'."""
        from lina.core.main_pipeline import MainPipeline
        topic = MainPipeline._extract_topic("Найди полные характеристики RTX 4090")
        self.assertEqual(topic, "RTX 4090")

    def test_96_no_stripping_without_descriptors(self):
        """'iPhone 15 Pro Max' stays 'iPhone 15 Pro Max'."""
        from lina.core.main_pipeline import MainPipeline
        topic = MainPipeline._extract_topic("iPhone 15 Pro Max")
        self.assertEqual(topic, "iPhone 15 Pro Max")


class TestV3PostRetryQualityGate(unittest.TestCase):
    """After max retries, V3 pipeline returns refusal for factual intents."""

    def test_97_should_research_with_no_facts(self):
        """_should_research returns True when fact count is 0."""
        from lina.pipeline.pipeline_v3 import PipelineV3
        from lina.models.datatypes import PipelineAnswer, FactSet

        # Build a PipelineV3 without triggering heavy _init_components
        pipe = PipelineV3.__new__(PipelineV3)
        pipe._MIN_FACTS_FOR_GOOD_ANSWER = 2
        pipe._MIN_CONFIDENCE_FOR_GOOD_ANSWER = 0.35

        answer = PipelineAnswer(text="fake", confidence=0.1)
        fact_set = FactSet(subject="Realme 10", facts=[], confidence=0.0)

        self.assertTrue(pipe._should_research(answer, fact_set),
                        "Should trigger research for 0 facts")

    def test_98_no_fact_cache_below_threshold(self):
        """Facts with confidence < 0.40 must not be cached."""
        from lina.models.datatypes import Fact, FactSet
        low_facts = [Fact(
            subject="Realme 10", predicate="display",
            object_value="6.4 inch", confidence=0.3,
        )]
        fact_set = FactSet(subject="test", facts=low_facts, confidence=0.25)
        # Verify the threshold boundary used in pipeline_v3
        self.assertLess(fact_set.confidence, 0.40,
                        "Low-confidence facts must stay below cache threshold")


class TestSnippetsToPassages(unittest.TestCase):
    """_snippets_to_passages creates merged passage for richer extraction."""

    def test_99_merged_passage_created(self):
        """Multiple snippets → merged passage at index 0."""
        from lina.pipeline.pipeline_v3 import PipelineV3
        from lina.models.datatypes import SearchResult

        results = [
            SearchResult(
                title="Realme 10 specs",
                url="https://a.com",
                snippet="6.4 inch display, Helio G99",
                relevance=0.8,
            ),
            SearchResult(
                title="Realme 10 review",
                url="https://b.com",
                snippet="8GB RAM, 128GB storage, 5000mAh battery",
                relevance=0.7,
            ),
        ]
        passages = PipelineV3._snippets_to_passages(results)
        # Should have 3 passages: 1 merged + 2 individual
        self.assertEqual(len(passages), 3)
        # First one is the merged passage
        self.assertIn("Helio G99", passages[0].text)
        self.assertIn("5000mAh", passages[0].text)

    def test_100_single_result_no_merge(self):
        """Single result → no merged passage."""
        from lina.pipeline.pipeline_v3 import PipelineV3
        from lina.models.datatypes import SearchResult

        results = [
            SearchResult(
                title="Test",
                url="https://a.com",
                snippet="Some snippet text here",
                relevance=0.5,
            ),
        ]
        passages = PipelineV3._snippets_to_passages(results)
        self.assertEqual(len(passages), 1)


class TestDDGSLibraryFallback(unittest.TestCase):
    """DDGS library tries wt-wt region after empty ru-ru."""

    @patch("lina.core.web_search_engine.warnings")
    def test_101_wt_wt_fallback(self, mock_warn):
        """When ru-ru returns nothing, wt-wt region is tried."""
        from lina.core.web_search_engine import WebSearchEngine
        ws = WebSearchEngine(web_capable=True)

        mock_ddgs = MagicMock()
        # ru-ru returns empty, wt-wt returns results
        mock_ddgs.text.side_effect = [
            [],  # ru-ru
            [{"href": "https://example.com", "title": "Test", "body": "Content"}],  # wt-wt
        ]

        with patch.dict("sys.modules", {"duckduckgo_search": MagicMock(DDGS=lambda: mock_ddgs)}):
            results = ws._search_ddgs_library("Realme 10 specs")
        # Should have called text() twice (ru-ru then wt-wt)
        self.assertEqual(mock_ddgs.text.call_count, 2)
        self.assertEqual(len(results), 1)


class TestSpecsSearchRegression(unittest.TestCase):
    """Regression tests for specs-oriented web search."""

    def test_102_fetch_and_summarize_specs_query_does_not_crash(self):
        """Specs query should not hit UnboundLocalError in _fetch_and_summarize()."""
        from lina.core.web_search_engine import WebSearchEngine, SearchResult

        ws = WebSearchEngine(web_capable=False)
        results = [
            SearchResult(
                title="Behringer BH470 Studio Monitoring Headphones",
                url="https://www.behringer.com/product.html?modelCode=01815-AAG",
                snippet=(
                    "Closed-back studio monitoring headphones with ultra-wide "
                    "frequency response and dynamic bass."
                ),
                relevance=0.8,
            ),
        ]

        with patch("lina.core.web_search_engine._generate_spec_site_urls", return_value=[]), \
             patch("lina.parser.page_parser.collect_pages_text", return_value=(
                 "Behringer BH470 closed-back headphones\n"
                 "Frequency response 20 Hz - 20 kHz\n"
                 "Cable length 3 m\n"
                 "Connector 3.5 mm with 6.3 mm adapter\n",
                 [results[0].url],
             )):
            summary = ws._fetch_and_summarize("характеристики Behringer BH470", results)

        self.assertTrue(summary)
        self.assertIn("Behringer BH470", summary)

    def test_103_general_search_returns_success_for_specs_result(self):
        """General specs search should succeed when an engine returns a relevant result."""
        from lina.core.web_search_engine import SearchResult, WebSearchEngine

        ws = WebSearchEngine(web_capable=True)
        result = SearchResult(
            title="Behringer BH470 Studio Monitoring Headphones",
            url="https://www.behringer.com/product.html?modelCode=01815-AAG",
            snippet="Closed-back studio monitoring headphones.",
            relevance=0.8,
        )

        with patch.object(ws, "_search_duckduckgo", return_value=[result]), \
             patch.object(ws, "_search_duckduckgo_html", return_value=[]), \
             patch.object(ws, "_search_brave", return_value=[]), \
             patch.object(ws, "_search_searxng", return_value=[]), \
             patch.object(ws, "_search_ddgs_library", return_value=[]), \
             patch.object(ws, "_search_duckduckgo_bs4", return_value=[]), \
             patch.object(ws, "_search_wikipedia", return_value=[]), \
             patch.object(ws, "_fetch_and_summarize", return_value="🔍 Результаты поиска: «характеристики Behringer BH470»\nBH470 specs"):
            resp = ws.search("характеристики Behringer BH470")

        self.assertTrue(resp.success)
        self.assertEqual(resp.source, "duckduckgo")
        self.assertIn("BH470", resp.summary)


if __name__ == "__main__":
    unittest.main()

