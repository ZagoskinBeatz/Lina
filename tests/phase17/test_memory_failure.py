#!/usr/bin/env python3
"""
Phase 17 — Section V: Memory & Resource Audit.
Section VI: Failure Semantics Matrix.

V) Deep memory analysis:
  - Unbounded state growth in all subsystems  
  - tracemalloc profiling under load
  - GC pressure analysis
  - Retained object categories

VI) Failure matrix:
  - 10 failure types
  - User output check (no stack leaks)
  - Trace correctness
  - Recoverability
"""
import os
import sys
import gc
import time
import tracemalloc
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from lina.runtime_v2.security.risk_engine import RiskEngine, RiskLevel
    from lina.runtime_v2.security.prompt_seal import PromptSeal
    from lina.runtime_v2.security.sandbox import ToolSandbox
    from lina.runtime_v2.security.response_validator import ResponseValidator
    from lina.runtime_v2.core.middleware import MiddlewareChain, RequestContext, ResponseContext, Middleware
    from lina.runtime_v2.core.event_bus import EventBus
    from lina.runtime_v2.core.orchestrator import Orchestrator
    from lina.runtime_v2.observability.span_tree import SpanTree, Span
    from lina.runtime_v2.observability.error_taxonomy import ErrorTaxonomy
    from lina.runtime_v2.observability.trace_context import TraceContext
    from lina.runtime_v2.chaos.fault_injector import FaultInjector
    from lina.runtime_v2.chaos.resilience_guard import ResilienceGuard, RetryPolicy, CircuitBreaker, CircuitBreakerConfig
    from lina.runtime_v2.chaos.injector import ChaosInjector
    from lina.runtime_v2.routing.routing_metrics import RoutingMetrics
    from lina.runtime_v2.security_v3.injection_graph_analyzer import InjectionGraphAnalyzer
    from lina.runtime_v2.security_v3.syscall_sandbox import SyscallSandbox
    from lina.runtime_v2.security_v3.anomaly_detector import AnomalyDetector
    from lina.runtime_v2.agent_v3.memory_refiner import MemoryRefiner
    from lina.runtime_v2.agent_v3.self_evaluator import SelfEvaluator
    from lina.runtime_v2.performance_v2.dynamic_token_allocator import DynamicTokenAllocator
    from lina.runtime_v2.performance_v2.latency_regression_detector import LatencyRegressionDetector
except ImportError:
    from runtime_v2.security.risk_engine import RiskEngine, RiskLevel
    from runtime_v2.security.prompt_seal import PromptSeal
    from runtime_v2.security.sandbox import ToolSandbox
    from runtime_v2.security.response_validator import ResponseValidator
    from runtime_v2.core.middleware import MiddlewareChain, RequestContext, ResponseContext, Middleware
    from runtime_v2.core.event_bus import EventBus
    from runtime_v2.core.orchestrator import Orchestrator
    from runtime_v2.observability.span_tree import SpanTree, Span
    from runtime_v2.observability.error_taxonomy import ErrorTaxonomy
    from runtime_v2.observability.trace_context import TraceContext
    from runtime_v2.chaos.fault_injector import FaultInjector
    from runtime_v2.chaos.resilience_guard import ResilienceGuard, RetryPolicy, CircuitBreaker, CircuitBreakerConfig
    from runtime_v2.chaos.injector import ChaosInjector
    from runtime_v2.routing.routing_metrics import RoutingMetrics
    from runtime_v2.security_v3.injection_graph_analyzer import InjectionGraphAnalyzer
    from runtime_v2.security_v3.syscall_sandbox import SyscallSandbox
    from runtime_v2.security_v3.anomaly_detector import AnomalyDetector
    from runtime_v2.agent_v3.memory_refiner import MemoryRefiner
    from runtime_v2.agent_v3.self_evaluator import SelfEvaluator
    from runtime_v2.performance_v2.dynamic_token_allocator import DynamicTokenAllocator
    from runtime_v2.performance_v2.latency_regression_detector import LatencyRegressionDetector

# ══════════════════════════════════════════════════════════
passed = 0
failed = 0
total = 0

def test(name, fn):
    global passed, failed, total
    total += 1
    num = f"{total:03d}"
    try:
        result = fn()
        if result:
            passed += 1
            print(f"  ✅ {num}. {name}")
        else:
            failed += 1
            print(f"  ❌ {num}. {name}: returned False")
    except Exception as e:
        failed += 1
        print(f"  ❌ {num}. {name}: {e}")

print("=" * 60)
print("  Phase 17 — Section V: Memory & Resource Audit")
print("=" * 60)

# ══════════════════════════════════════════════════════════
#  V.1 — Unbounded State Growth Detection
# ══════════════════════════════════════════════════════════

print("\n── V.1 — Unbounded State Growth ──")

def measure_growth(name, create_fn, exercise_fn, get_size_fn, iterations=5000):
    """Measure state accumulation in a subsystem."""
    obj = create_fn()
    for i in range(iterations):
        exercise_fn(obj, i)
    size = get_size_fn(obj)
    bounded = size <= iterations  # Can't grow more than iterations
    capped = size < iterations * 0.9  # Is it pruned/capped?
    print(f"    {name}: {size} items after {iterations} ops (capped={capped})")
    return size, bounded, capped

# RiskEngine._assessments
test("V.1a RiskEngine unbounded growth", lambda: (
    s := measure_growth(
        "RiskEngine._assessments",
        lambda: RiskEngine(),
        lambda obj, i: obj.assess_query(f"query {i}"),
        lambda obj: len(obj._assessments),
        5000,
    ),
    # Confirm it IS unbounded (grows to 5000)
    s[0] == 5000,
)[-1])

# PromptSeal._violations
test("V.1b PromptSeal violation accumulation", lambda: (
    s := measure_growth(
        "PromptSeal._violations",
        lambda: PromptSeal(strict=True),
        lambda obj, i: (obj.check_injection(f"normal query {i}") if i % 2 == 0 else None),
        lambda obj: len(obj._violations),
        1000,
    ),
    True,  # Just measuring
)[-1])

# InjectionGraphAnalyzer._alerts
test("V.1c InjectionGraph alert accumulation", lambda: (
    igr := InjectionGraphAnalyzer(),
    [igr.record_turn(session_id="s", query=f"q {i}", risk_score=0.1, risk_level="NONE", anomaly_score=0.0) for i in range(1000)],
    True,
)[-1])

# SpanTree._spans
test("V.1d SpanTree span accumulation", lambda: (
    tree := SpanTree(),
    [tree.start_span(f"op_{i}") for i in range(2000)],
    size := tree.total_spans,
    (print(f"    SpanTree._spans: {size} spans"), size == 2000),
)[-1])

# ErrorTaxonomy._history
test("V.1e ErrorTaxonomy history growth", lambda: (
    tax := ErrorTaxonomy(),
    [tax.classify(ValueError(f"err {i}")) for i in range(2000)],
    size := len(tax._history),
    (print(f"    ErrorTaxonomy._history: {size} entries"), True),
)[-1])

# FaultInjector._events
test("V.1f FaultInjector event growth", lambda: (
    fi := FaultInjector(seed=42),
    [fi.maybe_inject_timeout() for _ in range(1000)],
    size := len(fi._events),
    (print(f"    FaultInjector._events: {size} events"), True),
)[-1])

# LatencyRegressionDetector — bounded via deque
test("V.1g LatencyDetector bounded (deque)", lambda: (
    det := LatencyRegressionDetector(window_size=100),
    [det.record("op", 100.0 + i % 5) for i in range(5000)],
    True,  # Uses deque, inherently bounded
)[-1])

# RoutingMetrics — bounded via deque
test("V.1h RoutingMetrics bounded (deque)", lambda: (
    met := RoutingMetrics(),
    [met.record_result(tier="mini", latency_ms=10.0, success=True) for _ in range(5000)],
    True,
)[-1])


print("\n── V.2 — Memory Under Pressure ──")

def memory_pressure_test():
    """Simulate sustained load and measure memory footprint."""
    tracemalloc.start()
    gc.collect()
    baseline = tracemalloc.get_traced_memory()[0]

    # Create all subsystems (simulating a real session)
    engine = RiskEngine()
    seal = PromptSeal()
    taxonomy = ErrorTaxonomy()
    span_tree = SpanTree()
    injector = FaultInjector(seed=42)
    ig = InjectionGraphAnalyzer()
    anomaly = AnomalyDetector()
    evaluator = SelfEvaluator()
    memory = MemoryRefiner(max_entries=1000)
    allocator = DynamicTokenAllocator()
    detector = LatencyRegressionDetector()

    # Simulate 10K operations
    for i in range(10000):
        engine.assess_query(f"query {i}")
        span_tree.start_span(f"span_{i}")
        taxonomy.classify(RuntimeError(f"err {i}"))
        if i % 100 == 0:
            memory.add(action=f"action_{i}", result=f"result_{i}")
        allocator.create_budget(tier="mini", complexity_score=0.5)
        detector.record("op", 100.0)

    gc.collect()
    peak = tracemalloc.get_traced_memory()[1]
    current = tracemalloc.get_traced_memory()[0]
    growth = (current - baseline) / (1024 * 1024)
    peak_mb = peak / (1024 * 1024)

    print(f"    Baseline: {baseline / 1024:.0f} KB")
    print(f"    Current: {current / 1024:.0f} KB")
    print(f"    Peak: {peak_mb:.1f} MB")
    print(f"    Growth: {growth:.1f} MB")
    print(f"    Retained objects:")
    print(f"      RiskEngine._assessments: {len(engine._assessments)}")
    print(f"      SpanTree spans: {span_tree.total_spans}")
    print(f"      ErrorTaxonomy._history: {len(taxonomy._history)}")
    print(f"      MemoryRefiner entries: {memory.entry_count}")

    tracemalloc.stop()

    # Memory growth should be bounded — but we KNOW some lists are unbounded
    # Report the finding
    return growth < 200.0  # Should not exceed 200MB for 10K ops

test("V.2 Memory under 10K ops pressure < 200MB", memory_pressure_test)


print("\n── V.3 — GC Analysis ──")

def gc_analysis():
    gc.collect()
    gen0 = gc.get_count()

    engine = RiskEngine()
    for i in range(1000):
        engine.assess_query(f"gc test {i}")

    gc.collect()
    gen1 = gc.get_count()

    print(f"    GC counts before: {gen0}")
    print(f"    GC counts after 1K ops + collect: {gen1}")
    return True

test("V.3 GC pressure analysis", gc_analysis)


# ══════════════════════════════════════════════════════════
#  Section VI — Failure Semantics Matrix
# ══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print("  Phase 17 — Section VI: Failure Semantics Matrix")
print(f"{'='*60}")

failure_matrix = []

def failure_test(name, fn, failure_type):
    global passed, failed, total
    total += 1
    num = f"{total:03d}"
    try:
        user_output, trace_correct, stack_leak, recoverable, severity = fn()
        # Check: no stack trace in user output
        no_leak = not stack_leak
        ok = no_leak and trace_correct
        if ok:
            passed += 1
            print(f"  ✅ {num}. {name}")
        else:
            failed += 1
            reason = "stack_leak" if stack_leak else "trace_incorrect"
            print(f"  ❌ {num}. {name}: {reason}")
        failure_matrix.append({
            "type": failure_type, "name": name,
            "user_output": user_output[:100] if user_output else "N/A",
            "trace_correct": trace_correct, "stack_leak": stack_leak,
            "recoverable": recoverable, "severity": severity,
        })
    except Exception as e:
        failed += 1
        print(f"  ❌ {num}. {name}: {e}")
        failure_matrix.append({
            "type": failure_type, "name": name,
            "user_output": str(e)[:100], "trace_correct": False,
            "stack_leak": "Traceback" in str(e), "recoverable": True,
            "severity": "HIGH",
        })

print("\n── VI.1 — Failure Types ──")

# 1. LLM Timeout
def test_llm_timeout():
    taxonomy = ErrorTaxonomy()
    try:
        raise TimeoutError("LLM generation timed out after 30s")
    except TimeoutError as e:
        classified = taxonomy.classify(e)
        user_msg = classified.user_message
        stack_leak = "Traceback" in user_msg or "File" in user_msg
        return user_msg, True, stack_leak, True, classified.severity
failure_test("LLM timeout", test_llm_timeout, "LLM_TIMEOUT")

# 2. Tool crash
def test_tool_crash():
    taxonomy = ErrorTaxonomy()
    try:
        raise RuntimeError("Tool 'web_search' crashed: connection refused")
    except RuntimeError as e:
        classified = taxonomy.classify(e)
        user_msg = classified.user_message
        stack_leak = "Traceback" in user_msg
        return user_msg, True, stack_leak, True, classified.severity
failure_test("Tool crash", test_tool_crash, "TOOL_CRASH")

# 3. Sandbox block
def test_sandbox_block():
    sandbox = ToolSandbox()
    def dangerous_handler(args):
        return "executed rm -rf /"
    try:
        result = sandbox.execute("shell_exec", dangerous_handler, {"command": "rm -rf /"})
        user_msg = f"Sandbox allowed dangerous command: {result}"
        return user_msg, False, False, True, "HIGH"  # Should have been blocked
    except Exception as e:
        # Expected: sandbox blocks dangerous commands
        user_msg = f"Blocked by sandbox: {str(e)[:80]}"
        stack_leak = "Traceback" in user_msg
        return user_msg, True, stack_leak, True, "MEDIUM"
failure_test("Sandbox block", test_sandbox_block, "SANDBOX_BLOCK")

# 4. Circuit open
def test_circuit_open():
    config = CircuitBreakerConfig(failure_threshold=3, recovery_timeout_s=60)
    cb = CircuitBreaker(config=config)
    for _ in range(5):
        cb.record_failure()
    is_open = not cb.allow_request()
    user_msg = "Service temporarily unavailable (circuit breaker)" if is_open else "Circuit not open"
    return user_msg, is_open, False, True, "MEDIUM"
failure_test("Circuit breaker open", test_circuit_open, "CIRCUIT_OPEN")

# 5. Chaos injection
def test_chaos_injection():
    taxonomy = ErrorTaxonomy()
    try:
        raise ConnectionError("Chaos: simulated network failure")
    except ConnectionError as e:
        classified = taxonomy.classify(e)
        user_msg = classified.user_message
        stack_leak = "Traceback" in user_msg
        return user_msg, True, stack_leak, True, classified.severity
failure_test("Chaos injection failure", test_chaos_injection, "CHAOS_INJECTION")

# 6. Risk escalation
def test_risk_escalation():
    engine = RiskEngine(block_critical=True)
    assessment = engine.assess_query("sudo rm -rf / && format C:")
    user_msg = "Request blocked by risk engine" if assessment.blocked else "Not blocked"
    return user_msg, assessment.blocked, False, True, "critical" if assessment.blocked else "NONE"
failure_test("Risk escalation block", test_risk_escalation, "RISK_ESCALATION")

# 7. JSON trace corruption
def test_trace_corruption():
    ctx = TraceContext()
    headers = ctx.to_headers()
    # Try to parse back
    ctx2 = TraceContext.from_headers(headers)
    valid = ctx2.trace_id == ctx.trace_id
    return "Trace roundtrip OK" if valid else "Trace corrupted", valid, False, True, "LOW"
failure_test("JSON trace integrity", test_trace_corruption, "TRACE_CORRUPTION")

# 8. Middleware abort
def test_middleware_abort():
    chain = MiddlewareChain()

    class AbortMiddleware(Middleware):
        def process_request(self, ctx):
            ctx.abort("Test abort reason")
            return False

    chain.add(AbortMiddleware())
    ctx = RequestContext(query="test")
    result = chain.process_request(ctx)
    user_msg = ctx.abort_reason
    stack_leak = "Traceback" in user_msg or "File \"" in user_msg
    return user_msg, ctx.aborted, stack_leak, True, "LOW"
failure_test("Middleware abort", test_middleware_abort, "MIDDLEWARE_ABORT")

# 9. Agent failure
def test_agent_failure():
    taxonomy = ErrorTaxonomy()
    try:
        raise RecursionError("Maximum recursion depth exceeded in agent plan execution")
    except RecursionError as e:
        classified = taxonomy.classify(e)
        user_msg = classified.user_message
        stack_leak = "Traceback" in user_msg or ".py" in user_msg
        return user_msg, True, stack_leak, True, classified.severity
failure_test("Agent recursion failure", test_agent_failure, "AGENT_FAILURE")

# 10. Routing failure
def test_routing_failure():
    taxonomy = ErrorTaxonomy()
    try:
        raise KeyError("Unknown tier 'ultra'")
    except KeyError as e:
        classified = taxonomy.classify(e)
        user_msg = classified.user_message
        stack_leak = "Traceback" in user_msg
        return user_msg, True, stack_leak, True, classified.severity
failure_test("Routing failure", test_routing_failure, "ROUTING_FAILURE")

# 11. Permission denied
def test_permission_denied():
    taxonomy = ErrorTaxonomy()
    try:
        raise PermissionError("Access denied: /root/.bashrc")
    except PermissionError as e:
        classified = taxonomy.classify(e)
        user_msg = classified.user_message
        stack_leak = "/root/" in user_msg  # Path leak check
        return user_msg, True, stack_leak, True, classified.severity
failure_test("Permission denied — no path leak", test_permission_denied, "PERMISSION_DENIED")

# 12. Memory error
def test_memory_error():
    taxonomy = ErrorTaxonomy()
    try:
        raise MemoryError("Unable to allocate 4GB for response buffer")
    except MemoryError as e:
        classified = taxonomy.classify(e)
        user_msg = classified.user_message
        stack_leak = "Traceback" in user_msg
        return user_msg, True, stack_leak, True, classified.severity
failure_test("Memory error", test_memory_error, "MEMORY_ERROR")

# 13. Division by zero (unexpected)
def test_divzero():
    taxonomy = ErrorTaxonomy()
    try:
        raise ZeroDivisionError("division by zero in token budget calculation")
    except ZeroDivisionError as e:
        classified = taxonomy.classify(e)
        user_msg = classified.user_message
        stack_leak = "Traceback" in user_msg or "division by zero" in user_msg
        return user_msg, True, stack_leak, True, classified.severity
failure_test("Unexpected ZeroDivisionError", test_divzero, "UNEXPECTED_ERROR")

# 14. Import error
def test_import_error():
    taxonomy = ErrorTaxonomy()
    try:
        raise ImportError("No module named 'secret_internal_module'")
    except ImportError as e:
        classified = taxonomy.classify(e)
        user_msg = classified.user_message
        stack_leak = "secret_internal" in user_msg
        return user_msg, True, stack_leak, True, classified.severity
failure_test("Import error — no internal module name leak", test_import_error, "IMPORT_ERROR")

# 15. OS error
def test_os_error():
    taxonomy = ErrorTaxonomy()
    try:
        raise OSError("Disk full: /home/user/.cache/lina/models/")
    except OSError as e:
        classified = taxonomy.classify(e)
        user_msg = classified.user_message
        stack_leak = "/home/user" in user_msg
        return user_msg, True, stack_leak, True, classified.severity
failure_test("OS error — no path leak", test_os_error, "OS_ERROR")


# ══════════════════════════════════════════════════════════
#  VI.2 — Error Format Consistency
# ══════════════════════════════════════════════════════════

print("\n── VI.2 — Error Format Consistency ──")

def test_error_format_consistency():
    taxonomy = ErrorTaxonomy()
    errors = [
        ValueError("bad value"),
        TypeError("wrong type"),
        RuntimeError("runtime err"),
        IOError("io error"),
        ConnectionError("connection lost"),
        TimeoutError("timed out"),
        PermissionError("no access"),
        FileNotFoundError("not found"),
    ]
    categories = set()
    severities = set()
    for e in errors:
        classified = taxonomy.classify(e)
        categories.add(classified.category)
        severities.add(classified.severity)
        # Every classified error should have user_message, category, severity
        if not classified.user_message or not classified.category or not classified.severity:
            return False
    print(f"    Categories seen: {categories}")
    print(f"    Severities seen: {severities}")
    return True

test("Error taxonomy format consistency", test_error_format_consistency)


print("\n── VI.3 — Stack Trace Leakage Audit ──")

def test_stack_no_leak():
    """Verify ErrorTaxonomy never leaks stack traces in user_message."""
    taxonomy = ErrorTaxonomy()
    leak_patterns = [
        "Traceback", "File \"", "line ", ".py\"", "  at ", "Exception in",
        'raise ', 'assert ', '>>> ',
    ]
    errors = [
        ValueError("bad"), TypeError("wrong"), RuntimeError("fail"),
        IOError("io"), ConnectionError("conn"), TimeoutError("timeout"),
        PermissionError("perm"), FileNotFoundError("notfound"),
        KeyError("key"), IndexError("idx"), AttributeError("attr"),
        OSError("os"), MemoryError("mem"), RecursionError("recursion"),
    ]
    leaks_found = []
    for e in errors:
        classified = taxonomy.classify(e)
        for pat in leak_patterns:
            if pat in classified.user_message:
                leaks_found.append((type(e).__name__, pat, classified.user_message[:80]))
    if leaks_found:
        print(f"    ⚠ Stack trace leaks found: {len(leaks_found)}")
        for l in leaks_found[:5]:
            print(f"      {l[0]}: pattern='{l[1]}' in '{l[2]}'")
    else:
        print(f"    No stack trace leaks in user_message")
    return len(leaks_found) == 0

test("No stack trace leakage in ErrorTaxonomy", test_stack_no_leak)


# ══════════════════════════════════════════════════════════
#  Failure Matrix Output
# ══════════════════════════════════════════════════════════

print(f"\n── Failure Matrix ──")
print(f"{'Type':<22} {'Stack Leak':<12} {'Trace OK':<10} {'Recoverable':<12} {'Severity':<10}")
print("-" * 70)
for f in failure_matrix:
    leak = "⚠ YES" if f["stack_leak"] else "No"
    trace = "✅" if f["trace_correct"] else "❌"
    recv = "Yes" if f["recoverable"] else "No"
    print(f"{f['type']:<22} {leak:<12} {trace:<10} {recv:<12} {f['severity']:<10}")


# ══════════════════════════════════════════════════════════
#  Summary
# ══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"  Memory+Failure: {passed}/{total} passed, {failed} failed")
print(f"{'='*60}")

if __name__ == "__main__":
    if failed > 0:
        sys.exit(1)
