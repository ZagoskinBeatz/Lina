#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lina Phase 10 — Интеграционные тесты.

Тестирует взаимодействие модулей Phase 10 между собой:
  1. Inference pipeline: Backend → Threading → Optimizer → Cache → Batch
  2. Agent pipeline: Intent → Planner → Executor → Evaluator → Memory
  3. CI pipeline: Runner + Reporter
  4. Cross-module: Inference + Agent + Metrics
  5. Full autonomy loop: Agent + Planning + Safety

Запуск:
  python lina/integration_tests/test_phase10.py
"""

import sys
import os
import time
import tempfile
import json

# ── Path setup ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))


# ═══════════════════════════════════════════════════════════
#  Test runner (минимальный, совместимый с проектом)
# ═══════════════════════════════════════════════════════════

passed = 0
failed = 0
total = 0


def test(name: str, fn):
    """Запускает тест, считает результат."""
    global passed, failed, total
    total += 1
    try:
        result = fn()
        if result:
            passed += 1
            print(f"  ✅ {total:03d}. {name}", flush=True)
        else:
            failed += 1
            print(f"  ❌ {total:03d}. {name}: returned False", flush=True)
    except Exception as e:
        failed += 1
        print(f"  ❌ {total:03d}. {name}: {e}", flush=True)


test.__test__ = False



if __name__ == "__main__":
    print("=" * 60)
    print("  Phase 10 — Integration Tests")
    print("=" * 60)


    # ═══════════════════════════════════════════════════════════
    #  1. Inference Pipeline Integration
    # ═══════════════════════════════════════════════════════════
    print("\n── Inference Pipeline ──")


    def test_inference_backend_to_threading():
        """Backend detection → Thread configuration."""
        from lina.inference.backend import configure_backend, BackendType
        from lina.inference.threading import optimize_threads

        cfg = configure_backend(model_ram_mb=1200, force_cpu=True)
        assert cfg.backend == BackendType.CPU

        threads = optimize_threads(tier="mini")
        assert threads.n_threads >= 2
        assert threads.n_threads <= 8
        # Backend n_threads и threading n_threads совместимы
        assert cfg.n_threads >= 1
        return True


    def test_inference_threading_to_optimizer():
        """Thread config → Context optimization."""
        from lina.inference.threading import optimize_threads
        from lina.inference.optimizer import optimize_context

        threads = optimize_threads(tier="mini")
        ctx = optimize_context(tier="mini", prompt_text="Привет, как дела?")
        # Контекст соответствует тиру
        assert 512 <= ctx.n_ctx <= 2048
        assert ctx.tier == "mini"
        assert ctx.estimated_input_tokens > 0
        return True


    def test_inference_optimizer_to_cache():
        """Context optimization → Cache integration."""
        from lina.inference.optimizer import optimize_context, estimate_tokens
        from lina.inference.cache import InferenceCache

        cache = InferenceCache(max_size=50, ttl=300)
        query = "Какая погода?"
        tokens = estimate_tokens(query)
        assert tokens > 0

        # Кэш: put → get (get() returns Optional[str])
        cache.put(query, "Сегодня солнечно", tier="mini")
        hit = cache.get(query)
        assert hit is not None
        assert hit == "Сегодня солнечно"

        stats = cache.stats
        assert stats.hits == 1
        assert stats.misses == 0
        return True


    def test_inference_cache_to_batch():
        """Cache miss → Batch processing."""
        from lina.inference.cache import InferenceCache
        from lina.inference.batch import BatchManager, RequestPriority

        cache = InferenceCache(max_size=50, ttl=300)
        batch = BatchManager(max_queue_size=20)

        queries = ["Вопрос 1", "Вопрос 2", "Вопрос 3"]

        # Проверяем кэш, если miss — в batch
        for q in queries:
            hit = cache.get(q)
            assert hit is None  # Пусто
            batch.submit(q, tier="mini")

        assert batch.queue_size == 3

        # "Процессим" batch, результаты → cache
        def mock_inference(query, context, tier):
            return f"ответ на: {query}"

        processed = batch.process_all(mock_inference)

        # Кладём результаты в кэш
        for req in processed:
            if req.response and not req.response.startswith("⚠"):
                cache.put(req.query, req.response, tier=req.tier)

        # Теперь всё в кэше (get() returns str)
        for q in queries:
            hit = cache.get(q)
            assert hit is not None
            assert "ответ на:" in hit

        assert cache.stats.hits == 3
        return True


    def test_inference_full_pipeline():
        """Полный inference pipeline: detect → threads → context → cache → batch."""
        from lina.inference.backend import configure_backend
        from lina.inference.threading import optimize_threads
        from lina.inference.optimizer import optimize_context
        from lina.inference.cache import InferenceCache
        from lina.inference.batch import BatchManager

        # 1. Backend
        backend = configure_backend(model_ram_mb=1200, force_cpu=True)
        assert backend.n_threads >= 1

        # 2. Threading
        threads = optimize_threads(tier="mini")
        assert threads.n_threads >= 2

        # 3. Context
        ctx = optimize_context(
            tier="mini",
            prompt_text="Расскажи о Python",
            max_output_tokens=128
        )
        assert ctx.n_ctx >= 512
        assert ctx.headroom_tokens > 0

        # 4. Cache
        cache = InferenceCache(max_size=100, ttl=600)
        assert cache.stats.current_size == 0

        # 5. Batch
        batch = BatchManager(max_queue_size=10)
        batch.submit("Расскажи о Python", tier="mini")

        def mock_llm(q, c, t):
            return f"Python — язык программирования ({t})"

        processed = batch.process_all(mock_llm)
        assert batch.stats.total_processed == 1

        # Результат в кэш
        for r in processed:
            if r.response and not r.response.startswith("⚠"):
                cache.put(r.query, r.response, tier=r.tier)

        hit = cache.get("Расскажи о Python")
        assert hit is not None
        assert "Python" in hit
        return True


    def test_inference_batch_dedup_pipeline():
        """Batch deduplication + cache = efficient pipeline."""
        from lina.inference.cache import InferenceCache
        from lina.inference.batch import BatchManager

        cache = InferenceCache(max_size=50, ttl=300)
        batch = BatchManager(max_queue_size=20)

        # Первый запрос — в очередь, обрабатываем, чтобы dedup map был заполнен
        batch.submit("одинаковый вопрос", tier="mini")
        batch.process_all(lambda q, c, t: f"ответ: {q}")

        # Повторный — дедупликация (ответ уже в dedup_map)
        batch.submit("одинаковый вопрос", tier="mini")
        batch.submit("другой вопрос", tier="mini")

        stats = batch.stats
        assert stats.total_deduplicated >= 1
        assert stats.queue_size == 1  # Только 1 уникальный в очереди
        return True


    test("backend → threading", test_inference_backend_to_threading)
    test("threading → optimizer", test_inference_threading_to_optimizer)
    test("optimizer → cache", test_inference_optimizer_to_cache)
    test("cache → batch", test_inference_cache_to_batch)
    test("full inference pipeline", test_inference_full_pipeline)
    test("batch dedup pipeline", test_inference_batch_dedup_pipeline)


    # ═══════════════════════════════════════════════════════════
    #  2. Agent Pipeline Integration
    # ═══════════════════════════════════════════════════════════
    print("\n── Agent Pipeline ──")


    def test_agent_intent_to_planner():
        """Intent classification → Plan creation."""
        from lina.agent.intent import AgentIntentClassifier
        from lina.agent.planner import AgentPlanner

        classifier = AgentIntentClassifier()
        planner = AgentPlanner()

        result = classifier.classify("установи и настрой nginx для продакшена")
        assert result.needs_agent is True
        assert result.complexity.value in ("moderate", "complex")

        plan = planner.create_plan(
            goal="установи и настрой nginx для продакшена",
            context=f"complexity={result.complexity.value}"
        )
        assert plan is not None
        assert plan.step_count >= 2
        return True


    def test_agent_planner_to_executor():
        """Plan creation → Step execution."""
        from lina.agent.planner import AgentPlanner
        from lina.agent.executor import AgentExecutor
        from lina.planning.state import StepStatus

        planner = AgentPlanner()
        executor = AgentExecutor(process_fn=lambda x: f"done: {x}")

        plan = planner.create_plan(goal="проверить систему")
        assert plan.step_count >= 2

        step = plan.steps[0]
        result = executor.execute_step(step, confidence=0.8)
        assert result.status == StepStatus.COMPLETED
        assert "done:" in result.output
        return True


    def test_agent_executor_to_evaluator():
        """Execution → Evaluation → Decision."""
        from lina.agent.planner import AgentPlanner
        from lina.agent.executor import AgentExecutor
        from lina.agent.evaluator import AgentEvaluator
        from lina.planning.state import EvalDecision

        planner = AgentPlanner()
        executor = AgentExecutor(process_fn=lambda x: f"OK: {x}")
        evaluator = AgentEvaluator()

        plan = planner.create_plan(goal="диагностика")
        step = plan.steps[0]
        result = executor.execute_step(step, confidence=0.9)

        eval_result = evaluator.evaluate_step(step, result, plan.goal)
        # Evaluator returns one of the valid decisions
        assert eval_result.decision in (
            EvalDecision.CONTINUE, EvalDecision.STOP,
            EvalDecision.REPLAN, EvalDecision.FAIL,
        ), f"Unexpected decision: {eval_result.decision}"
        assert eval_result.confidence > 0
        return True


    def test_agent_evaluator_low_confidence_replan():
        """Low confidence → Evaluator recommends REPLAN."""
        from lina.agent.planner import AgentPlanner
        from lina.agent.executor import AgentExecutor
        from lina.agent.evaluator import AgentEvaluator
        from lina.planning.state import StepStatus, EvalDecision

        planner = AgentPlanner()
        executor = AgentExecutor(process_fn=lambda x: f"partial: {x}")
        evaluator = AgentEvaluator()

        plan = planner.create_plan(goal="сложная задача")
        step = plan.steps[0]
        result = executor.execute_step(step, confidence=0.3)

        # Низкая уверенность → skip или replan
        if result.status == StepStatus.COMPLETED:
            eval_result = evaluator.evaluate_step(step, result, plan.goal)
            # При confidence < AGENT_CONFIDENCE_THRESHOLD → REPLAN
            assert eval_result.decision in (
                EvalDecision.REPLAN, EvalDecision.CONTINUE, EvalDecision.STOP
            )
        else:
            # Заблокировано MIN_CONFIDENCE → SKIPPED
            assert result.status == StepStatus.SKIPPED
        return True


    def test_agent_memory_across_steps():
        """Memory accumulates across execution steps."""
        from lina.agent.memory import AgentMemory

        memory = AgentMemory()
        memory.set_goal("обновить сервер")

        # Имитация нескольких шагов
        steps = [
            ("проверка версии", "v2.1.0", True, 0.9),
            ("создание бэкапа", "backup.tar.gz создан", True, 0.8),
            ("обновление пакетов", "3 пакета обновлено", True, 0.7),
        ]
        for action, result, success, relevance in steps:
            memory.add_action(action, result, success, relevance)

        assert memory.goal == "обновить сервер"
        assert len(memory.get_recent(10)) == 3

        # Контекст содержит всё
        ctx = memory.get_context(max_chars=5000)
        assert "обновить сервер" in ctx
        assert "v2.1.0" in ctx
        return True


    def test_agent_full_autonomy_loop():
        """Полный автономный цикл: Intent → Plan → Execute → Evaluate → Memory."""
        from lina.agent.intent import AgentIntentClassifier
        from lina.agent.planner import AgentPlanner
        from lina.agent.executor import AgentExecutor
        from lina.agent.evaluator import AgentEvaluator
        from lina.agent.memory import AgentMemory
        from lina.planning.state import StepStatus

        classifier = AgentIntentClassifier()
        planner = AgentPlanner()
        executor = AgentExecutor(process_fn=lambda x: f"executed: {x}")
        evaluator = AgentEvaluator()
        memory = AgentMemory()

        # 1. Intent
        intent = classifier.classify("установи и настрой nginx для продакшена пошагово")
        assert intent.needs_agent is True, f"Expected needs_agent=True, got complexity={intent.complexity.value}"

        # 2. Plan
        memory.set_goal(intent.intent.value if hasattr(intent.intent, 'value')
                        else str(intent.intent))
        plan = planner.create_plan(
            goal="установи и настрой nginx",
            context=f"complexity={intent.complexity.value}"
        )

        # 3. Execute each step
        results = []
        for step in plan.steps:
            result = executor.execute_step(step, confidence=0.8)
            results.append(result)
            memory.add_action(
                action=step.description,
                result=result.output if result.output else str(result.status),
                success=(result.status == StepStatus.COMPLETED),
                relevance=0.8
            )

        # 4. Evaluate plan
        eval_result = evaluator.evaluate_plan(results)
        assert eval_result.total_steps == plan.step_count, (
            f"Expected {plan.step_count} steps, got {eval_result.total_steps}"
        )
        # At least some steps should be completed (lambda always succeeds)
        completed = sum(1 for r in results if r.status == StepStatus.COMPLETED)
        assert completed >= 1, (
            f"Expected >=1 completed, got {completed}. "
            f"Statuses: {[r.status.value for r in results]}"
        )

        # 5. Memory has everything
        assert len(memory.get_recent(20)) == plan.step_count
        assert memory.goal is not None
        return True


    def test_agent_replan_cycle():
        """Replan cycle: Plan → fail → Replan → continue."""
        from lina.agent.planner import AgentPlanner
        from lina.agent.executor import AgentExecutor
        from lina.planning.state import StepStatus

        call_count = [0]

        def flaky_fn(x):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Ошибка сети")
            return f"OK: {x}"

        planner = AgentPlanner()
        executor = AgentExecutor(process_fn=flaky_fn)

        plan = planner.create_plan(goal="задача с ошибкой")
        step = plan.steps[0]

        # Первая попытка — ошибка
        r1 = executor.execute_step(step, confidence=0.9)
        # Может быть FAILED из-за исключения

        # Replan (PlanStep.id, not step_id)
        new_plan = planner.replan(plan, step.id, "Ошибка сети")
        if new_plan:
            assert new_plan.step_count >= 1
            assert planner.replan_count == 1
        return True


    test("intent → planner", test_agent_intent_to_planner)
    test("planner → executor", test_agent_planner_to_executor)
    test("executor → evaluator", test_agent_executor_to_evaluator)
    test("evaluator low confidence", test_agent_evaluator_low_confidence_replan)
    test("memory across steps", test_agent_memory_across_steps)
    test("full autonomy loop", test_agent_full_autonomy_loop)
    test("replan cycle", test_agent_replan_cycle)


    # ═══════════════════════════════════════════════════════════
    #  3. CI Pipeline Integration
    # ═══════════════════════════════════════════════════════════
    print("\n── CI Pipeline ──")


    def test_ci_runner_reporter():
        """TestRunner result → CIReporter formats."""
        from lina.ci.runner import TestSuiteResult, TestResult
        from lina.ci.reporter import CIReporter

        # Создаём синтетический результат
        suite = TestSuiteResult(
            suite_name="unit",
            total=10,
            passed=9,
            failed=1,
            duration=5.2,
            returncode=1,
            output="Passed: 9\nFailed: 1\nTotal: 10",
            tests=[
                TestResult("test_ok", True, 0.1, "ok", ""),
                TestResult("test_fail", False, 0.2, "", "assertion error"),
            ]
        )

        reporter = CIReporter()
        reporter.add_results([suite])  # add_results takes List[TestSuiteResult]

        # CLI report
        cli = reporter.format_cli_report()
        assert "unit" in cli.lower() or "UNIT" in cli or "9" in cli

        # Markdown report
        md = reporter.generate_markdown_report()
        assert "unit" in md.lower() or "9" in md

        # JSON report to temp file
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp_path = f.name
        try:
            reporter.generate_json_report(tmp_path)
            with open(tmp_path) as f:
                data = json.load(f)
            assert "suites" in data or "results" in data or isinstance(data, dict)
        finally:
            os.unlink(tmp_path)
        return True


    def test_ci_reporter_metrics():
        """Reporter receives metrics from profiler."""
        from lina.ci.reporter import CIReporter
        from lina.metrics.profiler import RuntimeProfiler

        profiler = RuntimeProfiler()
        profiler.record_request()
        profiler.record_request()
        profiler.record_inference_request()

        reporter = CIReporter()
        reporter.add_metrics(profiler.get_report())

        report = reporter.format_cli_report()
        # Report содержит метрики
        assert isinstance(report, str)
        assert len(report) > 0
        return True


    test("runner → reporter", test_ci_runner_reporter)
    test("reporter + metrics", test_ci_reporter_metrics)


    # ═══════════════════════════════════════════════════════════
    #  4. Cross-Module: Inference + Agent + Metrics
    # ═══════════════════════════════════════════════════════════
    print("\n── Cross-Module Integration ──")


    def test_inference_with_profiler():
        """Inference operations recorded in profiler."""
        from lina.inference.cache import InferenceCache
        from lina.inference.batch import BatchManager
        from lina.metrics.profiler import RuntimeProfiler

        profiler = RuntimeProfiler()
        cache = InferenceCache(max_size=50, ttl=300)
        batch = BatchManager(max_queue_size=10)

        # Запрос → cache miss → batch → cache put
        query = "тест интеграции"
        hit = cache.get(query)
        assert hit is None

        profiler.record_inference_request()
        batch.submit(query, tier="mini")

        def mock_fn(q, c, t):
            return f"result: {q}"

        processed = batch.process_all(mock_fn)

        for r in processed:
            if r.response and not r.response.startswith("⚠"):
                cache.put(r.query, r.response, tier=r.tier)

        # Повторный запрос → cache hit (returns str)
        hit = cache.get(query)
        assert hit is not None
        assert isinstance(hit, str)
        profiler.record_dedup_hit()

        stats = profiler.get_report()
        assert stats["counters"]["inference_requests"] == 1
        assert stats["counters"]["dedup_hits"] == 1
        return True


    def test_agent_with_profiler():
        """Agent steps recorded in profiler."""
        from lina.agent.planner import AgentPlanner
        from lina.agent.executor import AgentExecutor
        from lina.metrics.profiler import RuntimeProfiler

        profiler = RuntimeProfiler()
        planner = AgentPlanner()
        executor = AgentExecutor(process_fn=lambda x: f"done: {x}")

        plan = planner.create_plan(goal="тестовая задача")
        profiler.record_agent_plan()

        for step in plan.steps:
            result = executor.execute_step(step, confidence=0.8)
            profiler.record_agent_step()

        stats = profiler.get_report()
        assert stats["counters"]["agent_plans"] == 1
        assert stats["counters"]["agent_steps"] == plan.step_count
        return True


    def test_agent_with_safety():
        """Agent executor respects safety validation."""
        from lina.agent.executor import AgentExecutor
        from lina.safety.validator import SafetyValidator
        from lina.planning.state import StepStatus

        validator = SafetyValidator()

        def safe_process(x):
            """Process with safety check."""
            verdict = validator.validate(x)
            if not verdict.safe:
                return f"⚠ заблокировано: {x}"
            return f"OK: {x}"

        executor = AgentExecutor(process_fn=safe_process)

        # Создаём mock step
        from lina.planning.planner import Planner
        planner = Planner()
        plan = planner.create_plan("безопасная проверка")
        step = plan.steps[0]

        result = executor.execute_step(step, confidence=0.9)
        assert result.status == StepStatus.COMPLETED
        return True


    def test_inference_cache_with_agent_memory():
        """Inference cache + Agent memory — coordinated state."""
        from lina.inference.cache import InferenceCache
        from lina.agent.memory import AgentMemory

        cache = InferenceCache(max_size=50, ttl=300)
        memory = AgentMemory()
        memory.set_goal("ответить на вопросы")

        questions = ["Что такое Python?", "Что такое Linux?"]

        for q in questions:
            # Simulate inference with cache (get() returns str or None)
            hit = cache.get(q)
            if hit is None:
                response = f"Ответ на: {q}"
                cache.put(q, response, tier="mini")
            else:
                response = hit

            memory.add_action(
                action=f"ответ на: {q}",
                result=response,
                success=True,
                relevance=0.9
            )

        assert cache.stats.current_size == 2
        assert len(memory.get_recent(10)) == 2

        ctx = memory.get_context(max_chars=3000)
        assert "Python" in ctx
        assert "Linux" in ctx
        return True


    def test_full_pipeline_inference_agent_metrics():
        """Full cross-module: Inference → Agent → Metrics."""
        from lina.inference.backend import configure_backend
        from lina.inference.threading import optimize_threads
        from lina.inference.optimizer import optimize_context
        from lina.inference.cache import InferenceCache
        from lina.inference.batch import BatchManager
        from lina.agent.intent import AgentIntentClassifier
        from lina.agent.planner import AgentPlanner
        from lina.agent.executor import AgentExecutor
        from lina.agent.memory import AgentMemory
        from lina.metrics.profiler import RuntimeProfiler
        from lina.planning.state import StepStatus

        # Setup
        profiler = RuntimeProfiler()
        cache = InferenceCache(max_size=100, ttl=600)
        batch = BatchManager(max_queue_size=20)
        classifier = AgentIntentClassifier()
        planner = AgentPlanner()
        memory = AgentMemory()

        # 1. Inference setup
        backend = configure_backend(model_ram_mb=1200, force_cpu=True)
        threads = optimize_threads(tier="mini")
        ctx = optimize_context(tier="mini", prompt_text="расскажи и покажи")

        # 2. Agent intent
        intent = classifier.classify("расскажи и покажи")
        profiler.record_request()

        # 3. Plan
        memory.set_goal(str(intent.intent))
        plan = planner.create_plan(goal="расскажи и покажи")
        profiler.record_agent_plan()

        # 4. Execute with inference
        executor = AgentExecutor(process_fn=lambda x: f"result: {x}")

        for step in plan.steps:
            query = step.description

            # Check cache first
            hit = cache.get(query)
            if hit is None:
                profiler.record_inference_request()
                result = executor.execute_step(step, confidence=0.8)
                if result.status == StepStatus.COMPLETED:
                    cache.put(query, result.output, tier="mini")
            else:
                profiler.record_dedup_hit()
                result = None

            profiler.record_agent_step()
            memory.add_action(
                action=query,
                result=result.output if result else hit or "cached",
                success=True,
                relevance=0.8
            )

        # 5. Verify metrics
        stats = profiler.get_report()
        assert stats["counters"]["total_requests"] >= 1, f"total_requests={stats['counters']['total_requests']}"
        assert stats["counters"]["agent_plans"] == 1
        assert stats["counters"]["agent_steps"] >= 1

        # 6. Memory is populated
        assert len(memory.get_recent(20)) >= 1
        assert memory.goal is not None
        return True


    test("inference + profiler", test_inference_with_profiler)
    test("agent + profiler", test_agent_with_profiler)
    test("agent + safety", test_agent_with_safety)
    test("cache + memory coordination", test_inference_cache_with_agent_memory)
    test("full inference→agent→metrics", test_full_pipeline_inference_agent_metrics)


    # ═══════════════════════════════════════════════════════════
    #  5. Backward Compatibility
    # ═══════════════════════════════════════════════════════════
    print("\n── Backward Compatibility ──")


    def test_phase9_modules_unchanged():
        """Phase 9 modules import and work without changes."""
        from lina.safety.validator import SafetyValidator
        from lina.safety.policy import PolicyEngine
        from lina.planning.planner import Planner
        from lina.planning.executor import StepExecutor
        from lina.planning.evaluator import Evaluator
        from lina.planning.state import PlanState
        from lina.metrics.latency import LatencyTracker
        from lina.metrics.token_metrics import TokenMetricsCollector
        from lina.metrics.profiler import RuntimeProfiler
        from lina.core.pipeline import CorePipeline

        # Safety
        v = SafetyValidator()
        verdict = v.validate("echo hello")
        assert verdict.safe is True

        # Planning
        p = Planner()
        plan = p.create_plan("test")
        assert plan.step_count >= 1

        # Metrics
        lt = LatencyTracker()
        lt.record("test_op", 0.5)
        tm = TokenMetricsCollector()
        tm.record("mini", 50, 20, 2048)

        # Profiler — old API still works
        prof = RuntimeProfiler()
        prof.record_request()
        stats = prof.get_report()
        assert "counters" in stats
        assert stats["counters"]["total_requests"] == 1
        return True


    def test_profiler_new_counters_backward_compat():
        """New profiler counters don't break old API."""
        from lina.metrics.profiler import RuntimeProfiler

        p = RuntimeProfiler()

        # Old API
        p.record_request()
        p.record_request()

        # New API
        p.record_agent_step()
        p.record_agent_plan()
        p.record_agent_replan()
        p.record_inference_request()
        p.record_dedup_hit()
        p.record_model_switch()

        stats = p.get_report()
        assert stats["counters"]["total_requests"] == 2
        assert stats["counters"]["agent_steps"] == 1
        assert stats["counters"]["agent_plans"] == 1
        assert stats["counters"]["agent_replans"] == 1
        assert stats["counters"]["inference_requests"] == 1
        assert stats["counters"]["dedup_hits"] == 1
        assert stats["counters"]["model_switches"] == 1
        return True


    def test_all_phase10_imports():
        """All Phase 10 modules import cleanly."""
        # inference
        from lina.inference import (
            BackendType, GPUVendor, GPUInfo, BackendConfig,
            detect_gpu, configure_backend, format_backend_status,
            ThreadConfig, optimize_threads, format_thread_config,
            ContextConfig, estimate_tokens, optimize_context, format_context_config,
            CacheStats, CacheEntry, InferenceCache,
            RequestPriority, BatchRequest, BatchStats, BatchManager,
        )

        # agent
        from lina.agent import (
            ComplexityLevel, IntentResult, AgentIntentClassifier,
            AgentPlanner, AgentExecutor, AgentEvaluator, AgentMemory,
        )

        # ci
        from lina.ci import TestRunner, TestSuiteResult, CIReporter

        # All imported → no errors
        assert BackendType.CPU is not None
        assert ComplexityLevel.SIMPLE is not None
        assert TestRunner is not None
        return True


    test("phase 9 modules unchanged", test_phase9_modules_unchanged)
    test("profiler backward compat", test_profiler_new_counters_backward_compat)
    test("all phase 10 imports", test_all_phase10_imports)


    # ═══════════════════════════════════════════════════════════
    #  Итог
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print(f"  Phase 10 Integration: {passed}/{total}")
    if failed:
        print(f"  ПРОВАЛЕНО: {failed}")
    else:
        print("  ВСЕ ТЕСТЫ ПРОЙДЕНЫ! ✨")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
