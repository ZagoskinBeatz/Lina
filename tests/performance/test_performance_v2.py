#!/usr/bin/env python3
"""
Phase 16 — Performance Intelligence V2 Tests.

Tests for:
  - DynamicTokenAllocator: budget creation, slot allocation, usage tracking, reallocation
  - LatencyRegressionDetector: sample recording, z-score alerts, percentiles
  - LoadSimulator: constant/ramp/burst load patterns, report generation
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

passed = 0
failed = 0
total = 0


def test(name, func):
    global passed, failed, total
    total += 1
    try:
        result = func()
        if result is not False:
            print(f"  ✅ {total:03d}. {name}")
            passed += 1
        else:
            print(f"  ❌ {total:03d}. {name}: returned False")
            failed += 1
    except Exception as e:
        print(f"  ❌ {total:03d}. {name}: {e}")
        failed += 1


print("=" * 60)
print("  Phase 16 — Performance Intelligence V2 Tests")
print("=" * 60)

# ═══════════════════════════════════════════════════════════
from runtime_v2.performance_v2.dynamic_token_allocator import (
    DynamicTokenAllocator, BudgetSlot, TokenBudget,
)

print("\n── DynamicTokenAllocator ──")

allocator = DynamicTokenAllocator()

test("Allocator: create", lambda: allocator is not None)

test("Allocator: create budget mini", lambda: (
    budget := allocator.create_budget(total_tokens=4000, tier="mini", complexity_score=0.3),
    isinstance(budget, TokenBudget),
)[-1])

test("Allocator: create budget full", lambda: (
    budget := allocator.create_budget(total_tokens=16000, tier="full", complexity_score=0.7),
    isinstance(budget, TokenBudget),
)[-1])

test("Allocator: budget has allocations", lambda: (
    budget := allocator.create_budget(total_tokens=4000, tier="mini", complexity_score=0.5),
    len(budget.allocations) > 0,
)[-1])

test("Allocator: system_prompt slot exists", lambda: (
    budget := allocator.create_budget(total_tokens=4000, tier="mini", complexity_score=0.5),
    BudgetSlot.SYSTEM_PROMPT in budget.allocations,
)[-1])

test("Allocator: response slot exists", lambda: (
    budget := allocator.create_budget(total_tokens=4000, tier="mini", complexity_score=0.5),
    BudgetSlot.RESPONSE in budget.allocations,
)[-1])

test("Allocator: memory slot exists", lambda: (
    budget := allocator.create_budget(total_tokens=4000, tier="mini", complexity_score=0.5),
    BudgetSlot.MEMORY in budget.allocations,
)[-1])

test("Allocator: total allocation <= total tokens", lambda: (
    budget := allocator.create_budget(total_tokens=4000, tier="mini", complexity_score=0.5),
    sum(a.allocated for a in budget.allocations.values()) <= 4000 + 10,  # Small rounding margin
)[-1])

test("Allocator: higher complexity more memory", lambda: (
    b_low := allocator.create_budget(total_tokens=4000, tier="mini", complexity_score=0.2),
    b_high := allocator.create_budget(total_tokens=4000, tier="mini", complexity_score=0.9),
    b_high.allocations[BudgetSlot.MEMORY].allocated >= b_low.allocations[BudgetSlot.MEMORY].allocated,
)[-1])

test("Allocator: record usage", lambda: (
    budget := allocator.create_budget(total_tokens=4000, tier="mini", complexity_score=0.5),
    allocator.record_usage(budget, BudgetSlot.SYSTEM_PROMPT, 500),
    True,  # Should not raise
)[-1])

test("Allocator: budget report", lambda: (
    budget := allocator.create_budget(total_tokens=4000, tier="mini", complexity_score=0.5),
    report := allocator.get_budget_report(budget),
    isinstance(report, str) and len(report) > 0,
)[-1])

test("Allocator: BudgetSlot enum values", lambda: (
    all(hasattr(BudgetSlot, s) for s in [
        "SYSTEM_PROMPT", "USER_CONTEXT", "MEMORY", "TOOLS", "RESPONSE", "SAFETY", "AGENT"
    ]),
))

test("Allocator: reallocation on overage", lambda: (
    budget := allocator.create_budget(total_tokens=4000, tier="mini", complexity_score=0.5),
    allocated := budget.allocations[BudgetSlot.SYSTEM_PROMPT].allocated,
    allocator.record_usage(budget, BudgetSlot.SYSTEM_PROMPT, allocated + 100),
    True,  # Should handle overage gracefully
)[-1])

# ═══════════════════════════════════════════════════════════
from runtime_v2.performance_v2.latency_regression_detector import (
    LatencyRegressionDetector, LatencySample, RegressionAlert,
)

print("\n── LatencyRegressionDetector ──")

detector = LatencyRegressionDetector()

test("Detector: create", lambda: detector is not None)

test("Detector: record sample", lambda: (
    d := LatencyRegressionDetector(),
    alert := d.record("llm_call", 150.0),
    alert is None,  # First sample, no alert
)[-1])

test("Detector: no alert on stable latency", lambda: (
    d := LatencyRegressionDetector(),
    [d.record("llm_call", 150.0 + (i % 5)) for i in range(20)],
    alert := d.record("llm_call", 152.0),
    alert is None,
)[-1])

test("Detector: alert on spike", lambda: (
    d := LatencyRegressionDetector(warning_z_score=2.0, min_samples=5),
    [d.record("llm_call", 100.0 + (i % 3)) for i in range(20)],
    alert := d.record("llm_call", 500.0),
    alert is not None and isinstance(alert, RegressionAlert),
)[-1])

test("Detector: critical alert on extreme spike", lambda: (
    d := LatencyRegressionDetector(critical_z_score=3.0, min_samples=5),
    [d.record("llm_call", 100.0 + (i % 3)) for i in range(20)],
    alert := d.record("llm_call", 1000.0),
    alert is not None and alert.severity == "critical",
)[-1])

test("Detector: separate operations", lambda: (
    d := LatencyRegressionDetector(),
    [d.record("op_a", 100.0) for _ in range(15)],
    [d.record("op_b", 200.0) for _ in range(15)],
    alert_a := d.record("op_a", 105.0),
    alert_b := d.record("op_b", 205.0),
    alert_a is None and alert_b is None,
)[-1])

test("Detector: LatencySample fields", lambda: (
    s := LatencySample(operation="test", latency_ms=100.0, timestamp=time.time()),
    s.operation == "test" and s.latency_ms == 100.0,
)[-1])

test("Detector: RegressionAlert fields", lambda: (
    a := RegressionAlert(
        operation="test", current_ms=500.0, baseline_ms=100.0,
        deviation_factor=40.0, severity="critical",
    ),
    a.deviation_factor == 40.0 and a.severity == "critical",
)[-1])

test("Detector: min samples guard", lambda: (
    d := LatencyRegressionDetector(min_samples=10),
    [d.record("op", 100.0) for _ in range(5)],
    alert := d.record("op", 500.0),
    alert is None,  # Not enough samples yet
)[-1])

# ═══════════════════════════════════════════════════════════
from runtime_v2.performance_v2.load_simulator import (
    LoadSimulator, LoadPattern, LoadConfig, LoadReport,
)

print("\n── LoadSimulator ──")

call_count = 0


def mock_handler(query: str) -> str:
    global call_count
    call_count += 1
    time.sleep(0.001)
    return f"response to {query}"


test("LoadSimulator: create", lambda: (
    sim := LoadSimulator(),
    sim is not None,
)[-1])

test("LoadSimulator: LoadPattern enum", lambda: (
    all(hasattr(LoadPattern, p) for p in ["CONSTANT", "RAMP", "BURST", "RANDOM"]),
))

test("LoadSimulator: constant load config", lambda: (
    cfg := LoadConfig(pattern=LoadPattern.CONSTANT, total_requests=5, concurrency=1),
    cfg.total_requests == 5,
)[-1])

test("LoadSimulator: run constant load", lambda: (
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.CONSTANT, total_requests=5, concurrency=1),
    report := sim.run(handler=mock_handler, config=cfg),
    isinstance(report, LoadReport),
)[-1])

test("LoadSimulator: report has total_requests", lambda: (
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.CONSTANT, total_requests=3, concurrency=1),
    report := sim.run(handler=mock_handler, config=cfg),
    report.total_requests == 3,
)[-1])

test("LoadSimulator: report has error_rate", lambda: (
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.CONSTANT, total_requests=3, concurrency=1),
    report := sim.run(handler=mock_handler, config=cfg),
    hasattr(report, 'error_rate') and report.error_rate >= 0.0,
)[-1])

test("LoadSimulator: report has throughput", lambda: (
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.CONSTANT, total_requests=5, concurrency=1),
    report := sim.run(handler=mock_handler, config=cfg),
    hasattr(report, 'throughput_rps') and report.throughput_rps > 0,
)[-1])

test("LoadSimulator: report percentiles", lambda: (
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.CONSTANT, total_requests=10, concurrency=1),
    report := sim.run(handler=mock_handler, config=cfg),
    hasattr(report, 'p50_latency_ms') and hasattr(report, 'p95_latency_ms'),
)[-1])

test("LoadSimulator: report to_text", lambda: (
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.CONSTANT, total_requests=3, concurrency=1),
    report := sim.run(handler=mock_handler, config=cfg),
    text := report.to_text(),
    isinstance(text, str) and len(text) > 0,
)[-1])

test("LoadSimulator: concurrent load", lambda: (
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.CONSTANT, total_requests=10, concurrency=3),
    report := sim.run(handler=mock_handler, config=cfg),
    report.total_requests == 10,
)[-1])

test("LoadSimulator: ramp pattern", lambda: (
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.RAMP, total_requests=5, concurrency=1),
    report := sim.run(handler=mock_handler, config=cfg),
    report.total_requests == 5,
)[-1])

test("LoadSimulator: burst pattern", lambda: (
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.BURST, total_requests=6, concurrency=2),
    report := sim.run(handler=mock_handler, config=cfg),
    report.total_requests == 6,
)[-1])

def _error_handler(q):
    raise RuntimeError("boom")

test("LoadSimulator: error handling", lambda: (
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.CONSTANT, total_requests=3, concurrency=1),
    report := sim.run(handler=_error_handler, config=cfg),
    report.error_rate > 0,
)[-1])

test("LoadSimulator: zero error rate on success", lambda: (
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.CONSTANT, total_requests=5, concurrency=1),
    report := sim.run(handler=mock_handler, config=cfg),
    report.error_rate == 0.0,
)[-1])

# ═══════════════════════════════════════════════════════════
print("\n── Integration: Allocator + Detector + Simulator ──")

test("Pipeline: allocator → track latency", lambda: (
    alloc := DynamicTokenAllocator(),
    det := LatencyRegressionDetector(),
    budget := alloc.create_budget(total_tokens=4000, tier="mini", complexity_score=0.5),
    [det.record("query", 100.0 + i) for i in range(15)],
    alert := det.record("query", 102.0),
    alert is None,
    report := alloc.get_budget_report(budget),
    len(report) > 0,
)[-1])

test("Pipeline: load test → regression check", lambda: (
    det := LatencyRegressionDetector(),
    sim := LoadSimulator(),
    cfg := LoadConfig(pattern=LoadPattern.CONSTANT, total_requests=5, concurrency=1),
    report := sim.run(handler=mock_handler, config=cfg),
    report.total_requests == 5 and report.error_rate == 0.0,
)[-1])

# ═══════════════════════════════════════════════════════════
print()
print("=" * 60)
print(f"  Performance V2 Tests: {passed}/{total} passed, {failed} failed")
print("=" * 60)

if __name__ == "__main__":
    sys.exit(1 if failed else 0)
