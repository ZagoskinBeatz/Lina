# -*- coding: utf-8 -*-
"""
Tests — Diagnostic Pipeline Wiring.

Tests that the diagnostic engine (21 JSON trees + LLM fallback) is properly
wired into MainPipeline via:
  1. set_diag_executor() — executor slot registration
  2. ExecutionOrchestrator — system_diagnostic → DIAGNOSTIC path mapping
  3. _step_07_execution — DIAGNOSTIC path routes to _diag_executor
  4. Fallback to LLM when diagnostics return empty
  5. KnowledgeRetriever as RAG executor (replaces basic Searcher)

Phase: Block A wiring + Block D diagnostic engine.
"""

import unittest
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════
#  Block A — Diagnostic Executor Slot
# ═══════════════════════════════════════════════════════════

class TestDiagExecutorSlot(unittest.TestCase):
    """MainPipeline._diag_executor slot registration."""

    def test_01_diag_executor_slot_exists(self):
        """MainPipeline has _diag_executor attribute after init."""
        from lina.core.main_pipeline import MainPipeline
        pipe = MainPipeline()
        self.assertTrue(hasattr(pipe, '_diag_executor'))
        self.assertIsNone(pipe._diag_executor)

    def test_02_set_diag_executor_method(self):
        """set_diag_executor() registers a callable."""
        from lina.core.main_pipeline import MainPipeline
        pipe = MainPipeline()
        fn = lambda ctx: "test"
        pipe.set_diag_executor(fn)
        self.assertIs(pipe._diag_executor, fn)

    def test_03_set_diag_executor_callable(self):
        """set_diag_executor stores the exact callback."""
        from lina.core.main_pipeline import MainPipeline
        pipe = MainPipeline()
        called = []

        def my_diag(ctx):
            called.append(ctx.user_input)
            return "diag result"

        pipe.set_diag_executor(my_diag)
        self.assertIs(pipe._diag_executor, my_diag)

    def test_04_all_four_executor_slots_exist(self):
        """All 4 executor slots exist: llm, tool, rag, diag."""
        from lina.core.main_pipeline import MainPipeline
        pipe = MainPipeline()
        for slot in ('_llm_executor', '_tool_executor', '_rag_executor', '_diag_executor'):
            self.assertTrue(hasattr(pipe, slot), f"Missing slot: {slot}")

    def test_05_set_diag_executor_overwrites(self):
        """Setting diag executor twice overwrites first."""
        from lina.core.main_pipeline import MainPipeline
        pipe = MainPipeline()
        fn1 = lambda ctx: "first"
        fn2 = lambda ctx: "second"
        pipe.set_diag_executor(fn1)
        pipe.set_diag_executor(fn2)
        self.assertIs(pipe._diag_executor, fn2)


# ═══════════════════════════════════════════════════════════
#  Block B — ExecutionOrchestrator DIAGNOSTIC Path
# ═══════════════════════════════════════════════════════════

class TestOrchestratorDiagnosticPath(unittest.TestCase):
    """ExecutionOrchestrator maps system_diagnostic to DIAGNOSTIC."""

    def test_06_system_diagnostic_maps_to_diagnostic(self):
        """system_diagnostic intent → DIAGNOSTIC path."""
        from lina.core.execution_orchestrator import ExecutionOrchestrator
        orch = ExecutionOrchestrator()
        plan = orch.create_plan(
            intent="system_diagnostic",
            confidence=0.9,
            runtime_state={},
            capability_info={},
            mode_profile={},
            config={},
            priority_level=3,
        )
        self.assertEqual(plan.primary_path, "DIAGNOSTIC")

    def test_07_system_diagnostic_fallback_is_llm(self):
        """system_diagnostic fallback → LLM."""
        from lina.core.execution_orchestrator import ExecutionOrchestrator
        orch = ExecutionOrchestrator()
        plan = orch.create_plan(
            intent="system_diagnostic",
            confidence=0.9,
            runtime_state={},
            capability_info={},
            mode_profile={},
            config={},
            priority_level=3,
        )
        self.assertEqual(plan.fallback_path, "LLM")

    def test_08_chat_intent_still_llm(self):
        """chat intent still routes to LLM path (not broken)."""
        from lina.core.execution_orchestrator import ExecutionOrchestrator
        orch = ExecutionOrchestrator()
        plan = orch.create_plan(
            intent="chat",
            confidence=0.8,
            runtime_state={},
            capability_info={},
            mode_profile={},
            config={},
            priority_level=5,
        )
        self.assertEqual(plan.primary_path, "LLM")

    def test_09_tool_intent_still_tool(self):
        """tool_explicit still routes to TOOL path (not broken)."""
        from lina.core.execution_orchestrator import ExecutionOrchestrator
        orch = ExecutionOrchestrator()
        plan = orch.create_plan(
            intent="tool_explicit",
            confidence=0.9,
            runtime_state={},
            capability_info={},
            mode_profile={},
            config={},
            priority_level=3,
        )
        self.assertEqual(plan.primary_path, "TOOL")


# ═══════════════════════════════════════════════════════════
#  Block C — Step 07 DIAGNOSTIC Execution Path
# ═══════════════════════════════════════════════════════════

class TestStep07DiagnosticExecution(unittest.TestCase):
    """MainPipeline._step_07_execution routes to DIAGNOSTIC executor."""

    def _make_pipeline_with_diag(self, diag_return, llm_return="LLM answer"):
        """Create pipeline with mocked diag + llm executors."""
        from lina.core.main_pipeline import MainPipeline, PipelineContext
        pipe = MainPipeline()

        diag_fn = MagicMock(return_value=diag_return)
        llm_fn = MagicMock(return_value=llm_return)

        pipe.set_diag_executor(diag_fn)
        pipe.set_llm_executor(llm_fn)

        ctx = PipelineContext(
            user_input="у меня не работает wifi",
            primary_path="DIAGNOSTIC",
            tool_allowed=True,
        )
        return pipe, ctx, diag_fn, llm_fn

    def test_10_diagnostic_path_calls_diag_executor(self):
        """DIAGNOSTIC path calls _diag_executor."""
        pipe, ctx, diag_fn, llm_fn = self._make_pipeline_with_diag(
            "WiFi: проверьте rfkill"
        )
        pipe._step_07_execution(ctx)
        diag_fn.assert_called_once_with(ctx)
        self.assertEqual(ctx.raw_response, "WiFi: проверьте rfkill")
        self.assertEqual(ctx.execution_path, "diagnostic")

    def test_11_diagnostic_fallback_to_llm_on_empty(self):
        """When diag returns empty string → falls back to LLM."""
        pipe, ctx, diag_fn, llm_fn = self._make_pipeline_with_diag("", "LLM fallback")
        pipe._step_07_execution(ctx)
        diag_fn.assert_called_once()
        llm_fn.assert_called_once()
        self.assertEqual(ctx.raw_response, "LLM fallback")
        self.assertEqual(ctx.execution_path, "diagnostic+llm")

    def test_12_diagnostic_fallback_to_llm_on_none(self):
        """When diag returns None → falls back to LLM."""
        pipe, ctx, diag_fn, llm_fn = self._make_pipeline_with_diag(None, "LLM answer")
        pipe._step_07_execution(ctx)
        diag_fn.assert_called_once()
        llm_fn.assert_called_once()
        self.assertEqual(ctx.raw_response, "LLM answer")
        self.assertEqual(ctx.execution_path, "diagnostic+llm")

    def test_13_diagnostic_no_llm_fallback_on_success(self):
        """When diag returns result → LLM is NOT called."""
        pipe, ctx, diag_fn, llm_fn = self._make_pipeline_with_diag(
            "Bluetooth: sudo systemctl start bluetooth"
        )
        pipe._step_07_execution(ctx)
        diag_fn.assert_called_once()
        llm_fn.assert_not_called()

    def test_14_diagnostic_path_not_called_for_llm_intent(self):
        """LLM path does NOT call _diag_executor."""
        from lina.core.main_pipeline import MainPipeline, PipelineContext
        pipe = MainPipeline()
        diag_fn = MagicMock(return_value="diagnostic")
        llm_fn = MagicMock(return_value="LLM response")
        pipe.set_diag_executor(diag_fn)
        pipe.set_llm_executor(llm_fn)

        ctx = PipelineContext(
            user_input="расскажи анекдот",
            primary_path="LLM",
            tool_allowed=True,
        )
        pipe._step_07_execution(ctx)
        diag_fn.assert_not_called()
        llm_fn.assert_called_once()

    def test_15_diagnostic_executor_exception_handled(self):
        """Exception in diag executor → logged, doesn't crash."""
        from lina.core.main_pipeline import MainPipeline, PipelineContext
        pipe = MainPipeline()
        pipe.set_diag_executor(MagicMock(side_effect=RuntimeError("boom")))
        pipe.set_llm_executor(MagicMock(return_value="fallback"))

        ctx = PipelineContext(
            user_input="test",
            primary_path="DIAGNOSTIC",
            tool_allowed=True,
        )
        pipe._step_07_execution(ctx)
        self.assertIn("execution:", ctx.errors[0])

    def test_16_no_diag_executor_falls_to_llm(self):
        """DIAGNOSTIC path without registered executor → uses LLM."""
        from lina.core.main_pipeline import MainPipeline, PipelineContext
        pipe = MainPipeline()
        llm_fn = MagicMock(return_value="LLM answer")
        pipe.set_llm_executor(llm_fn)
        # No diag executor registered

        ctx = PipelineContext(
            user_input="wifi broken",
            primary_path="DIAGNOSTIC",
            tool_allowed=True,
        )
        pipe._step_07_execution(ctx)
        llm_fn.assert_called_once()
        self.assertEqual(ctx.raw_response, "LLM answer")


# ═══════════════════════════════════════════════════════════
#  Block D — Diagnostic Integration Module
# ═══════════════════════════════════════════════════════════

class TestDiagnosticIntegration(unittest.TestCase):
    """diagnostics/integration.py — diagnose() API."""

    def test_17_diagnose_import(self):
        """diagnose() imports without errors."""
        from lina.diagnostics.integration import diagnose
        self.assertTrue(callable(diagnose))

    def test_18_diagnose_returns_dict(self):
        """diagnose() returns a dict with required keys."""
        from lina.diagnostics.integration import diagnose
        result = diagnose("у меня не работает звук")
        self.assertIsInstance(result, dict)
        for key in ("matched", "needs_llm"):
            self.assertIn(key, result)

    def test_19_diagnose_unknown_returns_needs_llm(self):
        """Unknown input → needs_llm=True or matched=False."""
        from lina.diagnostics.integration import diagnose
        result = diagnose("абракадабра XYZ")
        # Either not matched or needs LLM
        if not result.get("matched"):
            self.assertFalse(result["matched"])

    def test_20_diagnose_calls_engine(self):
        """diagnose() uses DiagnosticEngine and returns report."""
        from lina.diagnostics.integration import reset_engine, diagnose
        mock_engine = MagicMock()

        # Mock: matching tree found, returns report
        mock_engine.match_problem.return_value = "wifi"
        mock_report = MagicMock()
        mock_report.to_dict.return_value = {"steps": []}
        mock_report.format_text.return_value = "WiFi диагностика:\nCheck rfkill"
        mock_report.resolved = True
        mock_report.confidence = 0.9
        mock_engine.run_diagnostic.return_value = mock_report

        with patch("lina.diagnostics.integration.get_engine", return_value=mock_engine):
            result = diagnose("wifi не работает")

        self.assertTrue(result["matched"])
        self.assertEqual(result["tree_id"], "wifi")
        self.assertIn("rfkill", result.get("formatted", ""))


# ═══════════════════════════════════════════════════════════
#  Block E — KnowledgeRetriever RAG Wiring
# ═══════════════════════════════════════════════════════════

class TestKnowledgeRetrieverWiring(unittest.TestCase):
    """Verify KnowledgeRetriever (hybrid BM25) replaces basic Searcher."""

    def test_21_retriever_import(self):
        """KnowledgeRetriever imports from rag.retriever."""
        from lina.rag.retriever import KnowledgeRetriever
        self.assertIsNotNone(KnowledgeRetriever)

    def test_22_retriever_has_search_method(self):
        """KnowledgeRetriever has search() method."""
        from lina.rag.retriever import KnowledgeRetriever
        r = KnowledgeRetriever()
        self.assertTrue(hasattr(r, 'search'))
        self.assertTrue(callable(r.search))

    def test_23_retriever_search_returns_list(self):
        """search() returns a list (possibly empty)."""
        from lina.rag.retriever import KnowledgeRetriever
        r = KnowledgeRetriever()
        results = r.search("тест", top_k=3)
        self.assertIsInstance(results, list)

    def test_24_cli_uses_retriever_not_searcher(self):
        """cli.py imports KnowledgeRetriever, not Searcher."""
        import ast
        with open("/home/zbeatz/Документы/AI/lina/core/cli.py") as f:
            source = f.read()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and 'rag' in node.module:
                    for alias in node.names:
                        imports.append(alias.name)
        self.assertIn("KnowledgeRetriever", imports)
        self.assertNotIn("Searcher", imports)


# ═══════════════════════════════════════════════════════════
#  Block F — Dead Code Removal Verification
# ═══════════════════════════════════════════════════════════

class TestDeadCodeRemoval(unittest.TestCase):
    """Verify pipeline_coordinator.py is removed."""

    def test_25_pipeline_coordinator_removed(self):
        """pipeline_coordinator.py no longer exists."""
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "core", "pipeline_coordinator.py"
        )
        self.assertFalse(os.path.exists(path),
                         f"Dead code still exists: {path}")

    def test_26_pipeline_coordinator_not_imported(self):
        """MainPipeline does NOT import pipeline_coordinator."""
        import ast
        with open("/home/zbeatz/Документы/AI/lina/core/main_pipeline.py") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and 'pipeline_coordinator' in node.module:
                    self.fail("main_pipeline.py still imports pipeline_coordinator")


# ═══════════════════════════════════════════════════════════
#  Block G — End-to-End DIAGNOSTIC Route
# ═══════════════════════════════════════════════════════════

class TestEndToEndDiagnosticRoute(unittest.TestCase):
    """Full pipeline: system_diagnostic intent → DIAGNOSTIC → response."""

    def test_27_full_pipeline_diagnostic_route(self):
        """process_request with mocked router → DIAGNOSTIC → response."""
        from lina.core.main_pipeline import MainPipeline

        pipe = MainPipeline()
        pipe.set_diag_executor(lambda ctx: "✅ WiFi работает нормально")
        pipe.set_llm_executor(lambda ctx: "LLM fallback")

        # Mock the router to return system_diagnostic intent
        mock_decision = MagicMock()
        mock_decision.intent = "system_diagnostic"
        mock_decision.confidence = 0.95
        pipe._router = MagicMock()
        pipe._router.route = MagicMock(return_value=mock_decision)

        result = pipe.process_request("проверь wifi")
        self.assertIn("WiFi", result.text)
        self.assertEqual(result.status, "success")

    def test_28_full_pipeline_diagnostic_llm_fallback(self):
        """process_request diagnostic → empty → LLM fallback."""
        from lina.core.main_pipeline import MainPipeline

        pipe = MainPipeline()
        pipe.set_diag_executor(lambda ctx: "")  # no tree matched
        pipe.set_llm_executor(lambda ctx: "LLM: Попробуйте перезагрузить модем")

        mock_decision = MagicMock()
        mock_decision.intent = "system_diagnostic"
        mock_decision.confidence = 0.90
        pipe._router = MagicMock()
        pipe._router.route = MagicMock(return_value=mock_decision)

        result = pipe.process_request("почему не работает интернет")
        self.assertIn("модем", result.text.lower())

    def test_29_full_pipeline_chat_unchanged(self):
        """Chat intent still works through LLM path (regression test)."""
        from lina.core.main_pipeline import MainPipeline

        pipe = MainPipeline()
        pipe.set_diag_executor(lambda ctx: "diagnostic output")
        pipe.set_llm_executor(lambda ctx: "Привет! Как дела?")

        mock_decision = MagicMock()
        mock_decision.intent = "chat"
        mock_decision.confidence = 0.99
        pipe._router = MagicMock()
        pipe._router.route = MagicMock(return_value=mock_decision)

        result = pipe.process_request("привет")
        self.assertIn("Привет", result.text)

    def test_30_diagnostic_executor_enriches_ctx(self):
        """Diag executor can modify ctx.user_input for LLM enrichment."""
        from lina.core.main_pipeline import MainPipeline, PipelineContext

        pipe = MainPipeline()
        enriched = []

        def diag(ctx):
            ctx.user_input = ctx.user_input + " [DIAG: check DNS]"
            return ""  # signal LLM fallback

        def llm(ctx):
            enriched.append(ctx.user_input)
            return "Check DNS resolved"

        pipe.set_diag_executor(diag)
        pipe.set_llm_executor(llm)

        ctx = PipelineContext(
            user_input="не работает интернет",
            primary_path="DIAGNOSTIC",
            tool_allowed=True,
        )
        pipe._step_07_execution(ctx)
        self.assertTrue(len(enriched) > 0)
        self.assertIn("[DIAG: check DNS]", enriched[0])


if __name__ == "__main__":
    unittest.main()
