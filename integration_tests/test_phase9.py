#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lina Phase 9 — Интеграционные тесты.

Тестирует взаимодействие модулей Phase 9 между собой:
  1. Safety pipeline: Validator → Policy → Decision
  2. Planning pipeline: Planner → Executor → Evaluator
  3. Metrics pipeline: Latency + Tokens + Profiler
  4. Core Pipeline: полный поток обработки запроса
  5. Cross-module: безопасность + планирование + метрики

Запуск:
  python lina/integration_tests/test_phase9.py
"""

import sys
import os
import json
import time
import tempfile

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
            print(f"  ✅ {total:03d}. {name}")
        else:
            failed += 1
            print(f"  ❌ {total:03d}. {name}: returned False")
    except Exception as e:
        failed += 1
        print(f"  ❌ {total:03d}. {name}: {e}")


test.__test__ = False



if __name__ == "__main__":
    print("=" * 60)
    print("  Phase 9 — Integration Tests")
    print("=" * 60)


    # ═══════════════════════════════════════════════════════════
    #  1. Safety Pipeline Integration
    # ═══════════════════════════════════════════════════════════
    print("\n── Safety Pipeline ──")


    def test_safety_pipeline_safe():
        """Безопасная команда проходит Validator → Policy."""
        from lina.safety.validator import SafetyValidator
        from lina.safety.policy import PolicyEngine
        v = SafetyValidator()
        pe = PolicyEngine()
        verdict = v.validate("ls -la /home")
        decision = pe.evaluate(verdict, "ls -la /home")
        assert decision.allowed is True
        assert verdict.safe is True
        assert verdict.risk_level == 0
        return True


    def test_safety_pipeline_blocked():
        """Опасная команда блокируется Validator → Policy."""
        from lina.safety.validator import SafetyValidator
        from lina.safety.policy import PolicyEngine
        v = SafetyValidator()
        pe = PolicyEngine()
        verdict = v.validate("rm -rf /")
        decision = pe.evaluate(verdict, "rm -rf /")
        assert decision.allowed is False
        assert verdict.safe is False
        assert verdict.risk_level >= 3
        assert len(decision.policy_rules_applied) > 0
        return True


    def test_safety_pipeline_override():
        """Override работает для не-критических команд."""
        from lina.safety.validator import SafetyValidator
        from lina.safety.policy import PolicyEngine
        v = SafetyValidator()
        pe = PolicyEngine()
        verdict = v.validate("chmod 777 /tmp/test")
        if not verdict.safe:
            decision = pe.evaluate(verdict, "chmod 777 /tmp/test",
                                   allow_override=True)
            # Override при risk < CRITICAL
            if verdict.risk_level < 4:
                assert decision.allowed is True
                assert decision.override is True
        return True


    def test_safety_pipeline_batch():
        """Batch validation → каждая команда проверена."""
        from lina.safety.validator import SafetyValidator
        from lina.safety.policy import PolicyEngine
        v = SafetyValidator()
        pe = PolicyEngine()
        commands = [
            "ls -la",          # safe
            "echo hello",      # safe
            "rm -rf /",        # blocked
            "cat README.md",   # safe
            "sudo su -",       # blocked
        ]
        verdicts = v.validate_batch(commands)
        decisions = [pe.evaluate(vr, cmd) for vr, cmd in zip(verdicts, commands)]
        safe_count = sum(1 for d in decisions if d.allowed)
        blocked_count = sum(1 for d in decisions if not d.allowed)
        assert safe_count == 3
        assert blocked_count == 2
        return True


    def test_safety_pipeline_stats():
        """Статистика набирается при прохождении pipeline."""
        from lina.safety.validator import SafetyValidator
        from lina.safety.policy import PolicyEngine
        v = SafetyValidator()
        pe = PolicyEngine()
        for cmd in ["ls", "cat x.txt", "rm -rf /", "echo hi"]:
            verdict = v.validate(cmd)
            pe.evaluate(verdict, cmd)
        v_stats = v.get_stats()
        p_stats = pe.get_stats()
        assert v_stats["total_checks"] == 4
        assert p_stats["total_decisions"] == 4
        assert v_stats["safe_count"] >= 3
        assert p_stats["blocked_count"] >= 1
        return True


    test("safety pipeline safe", test_safety_pipeline_safe)
    test("safety pipeline blocked", test_safety_pipeline_blocked)
    test("safety pipeline override", test_safety_pipeline_override)
    test("safety pipeline batch", test_safety_pipeline_batch)
    test("safety pipeline stats", test_safety_pipeline_stats)


    # ═══════════════════════════════════════════════════════════
    #  2. Planning Pipeline Integration
    # ═══════════════════════════════════════════════════════════
    print("\n── Planning Pipeline ──")


    def test_planning_full_cycle():
        """Полный цикл: Planner → Executor → Evaluator."""
        from lina.planning.planner import Planner
        from lina.planning.executor import StepExecutor
        from lina.planning.evaluator import Evaluator
        from lina.planning.state import PlanState, PlanStatus, StepStatus

        planner = Planner()
        executor = StepExecutor(process_fn=lambda x: f"OK: {x}")
        evaluator = Evaluator()

        # Создаём план
        plan = planner.create_plan("проверка системы")
        assert plan.step_count >= 2

        # Выполняем каждый шаг
        state = PlanState(plan)
        state.start()

        completed_steps = 0
        for i in range(plan.step_count):
            step = state.current_step
            result = executor.execute(step)
            eval_result = evaluator.evaluate(step, result, plan.goal)
            state.record_result(result)
            completed_steps += 1
            if i < plan.step_count - 1:
                state.advance()

        assert completed_steps == plan.step_count
        assert executor.get_stats()["steps_succeeded"] == plan.step_count
        return True


    def test_planning_with_llm_plan():
        """LLM-план создаётся и выполняется."""
        import json
        from lina.planning.planner import Planner
        from lina.planning.executor import StepExecutor
        from lina.planning.evaluator import Evaluator
        from lina.planning.state import PlanState, StepStatus

        mock_plan = json.dumps({
            "goal": "тестовая задача",
            "steps": [
                {"id": 1, "description": "Шаг 1", "type": "shell",
                 "command": "echo test", "expected_result": "test"},
                {"id": 2, "description": "Шаг 2", "type": "llm",
                 "command": "анализ", "expected_result": "результат"},
            ],
        })
        planner = Planner(llm_fn=lambda p: mock_plan)
        executor = StepExecutor(process_fn=lambda x: "output ok")
        evaluator = Evaluator()

        plan = planner.create_plan("тестовая задача", force_llm=True)
        assert plan.step_count == 2

        state = PlanState(plan)
        state.start()
        for i in range(plan.step_count):
            step = state.current_step
            result = executor.execute(step)
            evaluator.evaluate(step, result, plan.goal)
            state.record_result(result)
            if i < plan.step_count - 1:
                state.advance()

        assert executor.get_stats()["steps_succeeded"] == 2
        return True


    def test_planning_replan_on_failure():
        """Replan при ошибке шага."""
        from lina.planning.planner import Planner
        from lina.planning.executor import StepExecutor
        from lina.planning.evaluator import Evaluator
        from lina.planning.state import (
            Plan, PlanStep, PlanState, StepType, StepStatus,
            PlanStatus, EvalDecision,
        )

        # Имитируем ошибку на первом шаге
        call_count = [0]

        def mock_process(x):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("command not found")
            return "success"

        plan = Plan(goal="test", steps=[
            PlanStep(id=1, description="fail step", step_type=StepType.SHELL,
                     command="bad_cmd"),
            PlanStep(id=2, description="ok step", step_type=StepType.SHELL,
                     command="echo ok"),
        ])

        executor = StepExecutor(process_fn=mock_process)
        evaluator = Evaluator()

        state = PlanState(plan)
        state.start()

        # Шаг 1: должен провалиться
        step1 = state.current_step
        result1 = executor.execute(step1)
        assert result1.status == StepStatus.FAILED
        eval1 = evaluator.evaluate(step1, result1, "test")
        assert eval1.decision in (EvalDecision.REPLAN, EvalDecision.FAIL)
        return True


    def test_planning_safety_integration():
        """Executor отклоняет опасные команды через safety_fn."""
        from lina.safety.validator import SafetyValidator
        from lina.planning.executor import StepExecutor
        from lina.planning.state import PlanStep, StepType, StepStatus

        validator = SafetyValidator()

        def safety_check(cmd):
            """Проверяет команду через валидатор."""
            verdict = validator.validate(cmd)
            return {
                "safe": verdict.safe,
                "reason": verdict.reason,
                "confidence": verdict.confidence,
            }

        executor = StepExecutor(
            process_fn=lambda x: "ok",
            safety_fn=safety_check,
        )

        # Безопасный шаг
        safe_step = PlanStep(id=1, description="safe", step_type=StepType.SHELL,
                             command="echo hello")
        safe_result = executor.execute(safe_step)
        assert safe_result.status == StepStatus.COMPLETED

        # Опасный шаг
        danger_step = PlanStep(id=2, description="danger", step_type=StepType.SHELL,
                               command="rm -rf /")
        danger_result = executor.execute(danger_step)
        assert danger_result.status == StepStatus.FAILED
        assert "Safety" in danger_result.error
        return True


    test("planning full cycle", test_planning_full_cycle)
    test("planning with llm plan", test_planning_with_llm_plan)
    test("planning replan on failure", test_planning_replan_on_failure)
    test("planning safety integration", test_planning_safety_integration)


    # ═══════════════════════════════════════════════════════════
    #  3. Metrics Pipeline Integration
    # ═══════════════════════════════════════════════════════════
    print("\n── Metrics Pipeline ──")


    def test_metrics_profiler_latency():
        """Profiler корректно собирает latency через measure()."""
        from lina.metrics.profiler import RuntimeProfiler
        p = RuntimeProfiler()
        with p.latency.measure("inference"):
            time.sleep(0.01)
        with p.latency.measure("inference"):
            time.sleep(0.01)
        stats = p.latency.get_stats("inference")
        assert stats["inference"].count == 2
        assert stats["inference"].avg_ms > 5  # > 5ms
        return True


    def test_metrics_profiler_tokens():
        """Profiler корректно собирает token metrics."""
        from lina.metrics.profiler import RuntimeProfiler
        p = RuntimeProfiler()
        p.tokens.record("mini", 100, 50, 2048, 0.5)
        p.tokens.record("full", 500, 200, 8192, 2.0)
        summary = p.tokens.get_summary()
        assert summary.total_requests == 2
        assert summary.total_input_tokens == 600
        assert summary.total_output_tokens == 250
        assert "mini" in summary.model_usage
        assert "full" in summary.model_usage
        return True


    def test_metrics_combined_report():
        """Profiler генерирует комплексный отчёт."""
        from lina.metrics.profiler import RuntimeProfiler
        p = RuntimeProfiler()
        p.record_request()
        p.record_request()
        p.record_safety_rejection()
        p.latency.record("test_op", 0.05)
        p.tokens.record("mini", 100, 50, 2048, 0.3)
        report = p.get_report()
        assert report["counters"]["total_requests"] == 2
        assert report["counters"]["safety_rejections"] == 1
        assert "test_op" in report["latency"]
        assert report["tokens"]["total_requests"] == 1
        return True


    def test_metrics_export_import():
        """Profiler export → JSON → корректная структура."""
        from lina.metrics.profiler import RuntimeProfiler
        p = RuntimeProfiler()
        p.record_request()
        p.tokens.record("mini", 100, 50, 2048, 0.5)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            p.export_json(path)
            with open(path) as f:
                data = json.load(f)
            assert "counters" in data
            assert "tokens" in data
            assert "latency" in data
            assert data["counters"]["total_requests"] == 1
        finally:
            os.unlink(path)
        return True


    test("metrics profiler latency", test_metrics_profiler_latency)
    test("metrics profiler tokens", test_metrics_profiler_tokens)
    test("metrics combined report", test_metrics_combined_report)
    test("metrics export/import", test_metrics_export_import)


    # ═══════════════════════════════════════════════════════════
    #  4. Core Pipeline Integration
    # ═══════════════════════════════════════════════════════════
    print("\n── Core Pipeline ──")


    def test_core_pipeline_question():
        """CorePipeline обрабатывает вопрос → ответ."""
        from lina.core.pipeline import CorePipeline
        p = CorePipeline(
            generate_fn=lambda q, ctx, tier: f"Ответ на: {q}",
            rag_fn=lambda q: "Linux — ОС",
            runtime_fn=lambda: "CPU: 10%",
        )
        result = p.process("Что такое Linux?")
        assert "Ответ" in result.response
        assert not result.safety_blocked
        assert result.model_tier is not None
        assert result.elapsed >= 0
        return True


    def test_core_pipeline_safe_command():
        """CorePipeline разрешает безопасную команду."""
        from lina.core.pipeline import CorePipeline
        p = CorePipeline(
            process_fn=lambda x: "файлы: a.txt b.txt",
        )
        result = p.process("!ls -la")
        assert not result.safety_blocked
        assert "файлы" in result.response
        return True


    def test_core_pipeline_dangerous_command():
        """CorePipeline блокирует опасную команду."""
        from lina.core.pipeline import CorePipeline
        p = CorePipeline(
            process_fn=lambda x: "should not reach",
        )
        result = p.process("!rm -rf /")
        assert result.safety_blocked is True
        assert "Заблокировано" in result.response
        return True


    def test_core_pipeline_metrics():
        """CorePipeline собирает метрики при работе."""
        from lina.core.pipeline import CorePipeline
        p = CorePipeline(
            generate_fn=lambda q, ctx, tier: "answer",
        )
        p.process("вопрос 1")
        p.process("вопрос 2")
        status = p.get_status()
        assert status["profiler"]["counters"]["total_requests"] == 2
        return True


    def test_core_pipeline_features_toggle():
        """CorePipeline — включение/отключение features."""
        from lina.core.pipeline import CorePipeline
        p = CorePipeline(
            process_fn=lambda x: "ok",
        )
        # Отключаем safety → опасная команда проходит
        p.state.disable_feature("safety")
        result = p.process("!rm -rf /")
        assert not result.safety_blocked
        # Включаем обратно
        p.state.enable_feature("safety")
        result2 = p.process("!rm -rf /")
        assert result2.safety_blocked is True
        return True


    def test_core_pipeline_context_enrichment():
        """CorePipeline обогащает контекст RAG + runtime."""
        from lina.core.pipeline import CorePipeline
        rag_called = [False]
        runtime_called = [False]

        def mock_rag(q):
            rag_called[0] = True
            return "RAG data"

        def mock_runtime():
            runtime_called[0] = True
            return "CPU: 5%"

        p = CorePipeline(
            generate_fn=lambda q, ctx, tier: "answer",
            rag_fn=mock_rag,
            runtime_fn=mock_runtime,
        )
        p.process("что такое Linux?")
        assert rag_called[0] is True
        assert runtime_called[0] is True
        return True


    test("core pipeline question", test_core_pipeline_question)
    test("core pipeline safe cmd", test_core_pipeline_safe_command)
    test("core pipeline danger cmd", test_core_pipeline_dangerous_command)
    test("core pipeline metrics", test_core_pipeline_metrics)
    test("core pipeline features", test_core_pipeline_features_toggle)
    test("core pipeline context", test_core_pipeline_context_enrichment)


    # ═══════════════════════════════════════════════════════════
    #  5. Cross-Module Integration
    # ═══════════════════════════════════════════════════════════
    print("\n── Cross-Module Integration ──")


    def test_cross_safety_planning_metrics():
        """Safety + Planning + Metrics — все вместе."""
        from lina.safety.validator import SafetyValidator
        from lina.safety.policy import PolicyEngine
        from lina.planning.planner import Planner
        from lina.planning.executor import StepExecutor
        from lina.planning.state import PlanState
        from lina.metrics.profiler import RuntimeProfiler

        validator = SafetyValidator()
        policy = PolicyEngine()
        planner = Planner()
        profiler = RuntimeProfiler()

        # Создаём план
        profiler.record_request()
        with profiler.latency.measure("planning"):
            plan = planner.create_plan("проверка системы")

        # Проверяем безопасность каждого шага
        def safety_fn(cmd):
            verdict = validator.validate(cmd)
            decision = policy.evaluate(verdict, cmd)
            if not decision.allowed:
                profiler.record_safety_rejection()
            return {"safe": decision.allowed, "reason": decision.reason,
                    "confidence": verdict.confidence}

        executor = StepExecutor(process_fn=lambda x: "ok", safety_fn=safety_fn)
        state = PlanState(plan)
        state.start()

        for i in range(plan.step_count):
            step = state.current_step
            with profiler.latency.measure("step_execution"):
                result = executor.execute(step)
            profiler.record_plan_iteration()
            state.record_result(result)
            if i < plan.step_count - 1:
                state.advance()

        report = profiler.get_report()
        assert report["counters"]["total_requests"] == 1
        assert report["counters"]["plan_iterations"] == plan.step_count
        assert "planning" in report["latency"]
        assert "step_execution" in report["latency"]
        return True


    def test_cross_pipeline_end_to_end():
        """CorePipeline end-to-end с разными типами запросов."""
        from lina.core.pipeline import CorePipeline
        requests = [
            ("!ls -la", False),          # safe command
            ("!rm -rf /", True),         # blocked
            ("/help", False),            # meta
            ("что такое Linux?", False), # question
        ]
        p = CorePipeline(
            generate_fn=lambda q, ctx, tier: f"answer: {q}",
            process_fn=lambda x: f"cmd: {x}",
        )
        for input_text, expect_blocked in requests:
            result = p.process(input_text)
            assert result.safety_blocked == expect_blocked, (
                f"Input '{input_text}': expected blocked={expect_blocked}, "
                f"got {result.safety_blocked}"
            )
        status = p.get_status()
        assert status["profiler"]["counters"]["total_requests"] == 4
        return True


    def test_cross_model_routing():
        """ModelRouter + CorePipeline — маршрутизация модели."""
        from lina.core.pipeline import CorePipeline
        tiers_used = []

        def mock_generate(q, ctx, tier):
            tiers_used.append(tier)
            return f"answer ({tier})"

        p = CorePipeline(generate_fn=mock_generate)
        p.process("привет")  # Short → mini
        p.process("объясни подробно архитектуру")  # Keywords → full
        assert "mini" in tiers_used
        assert "full" in tiers_used
        return True


    test("cross safety+planning+metrics", test_cross_safety_planning_metrics)
    test("cross pipeline end-to-end", test_cross_pipeline_end_to_end)
    test("cross model routing", test_cross_model_routing)


    # ═══════════════════════════════════════════════════════════
    #  Итог
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print(f"  Phase 9 Integration: {passed}/{total}")
    if failed:
        print(f"  ПРОВАЛЕНО: {failed}")
    else:
        print("  ВСЕ ТЕСТЫ ПРОЙДЕНЫ! ✨")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
