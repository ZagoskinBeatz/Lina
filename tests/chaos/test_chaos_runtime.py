#!/usr/bin/env python3
"""
Phase 16 — Chaos Engineering Runtime Tests.

50+ tests covering:
  - FaultInjector: configure(), each fault type, probability gates, stats
  - ResilienceGuard: retry, circuit breaker, primary/fallback/degraded
  - ChaosProfile: profile loading, build_chaos_stack
  - ChaosInjector (existing): policy management
  - Integration: resilience survives faults, circuit breaker recovery
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
print("  Phase 16 — Chaos Engineering Runtime Tests")
print("=" * 60)

# ═══════════════════════════════════════════════════════════
from runtime_v2.chaos.fault_injector import FaultInjector, FaultType, FaultConfig

print("\n── FaultInjector ──")

test("FaultInjector: create default", lambda: FaultInjector(seed=42) is not None)

test("FaultInjector: configure timeout", lambda: (
    fi := FaultInjector(seed=42),
    fi.configure(FaultType.TIMEOUT, enabled=True, probability=0.5, max_delay_ms=100),
    True,
)[-1])

test("FaultInjector: no fault when not enabled", lambda: (
    fi := FaultInjector(seed=42),
    fi.maybe_inject_timeout("test") == False,
)[-1])


def _test_timeout():
    fi = FaultInjector(seed=42)
    fi.configure(FaultType.TIMEOUT, enabled=True, probability=1.0, max_delay_ms=1)
    try:
        fi.maybe_inject_timeout("test")
        return False
    except TimeoutError:
        return True


test("FaultInjector: timeout raises TimeoutError", _test_timeout)


def _test_middleware_crash():
    fi = FaultInjector(seed=42)
    fi.configure(FaultType.MIDDLEWARE_CRASH, enabled=True, probability=1.0)
    try:
        fi.maybe_crash_middleware("security")
        return False
    except RuntimeError as e:
        return "CHAOS" in str(e)


test("FaultInjector: middleware crash raises RuntimeError", _test_middleware_crash)


def _test_tool_failure():
    fi = FaultInjector(seed=42)
    fi.configure(FaultType.TOOL_FAILURE, enabled=True, probability=1.0)
    try:
        fi.maybe_fail_tool("shell", {"cmd": "ls"})
        return False
    except OSError as e:
        return "CHAOS" in str(e)


test("FaultInjector: tool failure raises OSError", _test_tool_failure)

test("FaultInjector: truncate response at p=1", lambda: (
    fi := FaultInjector(seed=42),
    fi.configure(FaultType.PARTIAL_LLM, enabled=True, probability=1.0, truncate_ratio=0.5),
    result := fi.maybe_truncate_response("Hello World 1234567890", "llm"),
    len(result) < len("Hello World 1234567890"),
)[-1])

test("FaultInjector: no truncate when disabled", lambda: (
    fi := FaultInjector(seed=42),
    result := fi.maybe_truncate_response("Hello World", "llm"),
    result == "Hello World",
)[-1])


def _test_memory_pressure():
    fi = FaultInjector(seed=42)
    fi.configure(FaultType.MEMORY_PRESSURE, enabled=True, probability=1.0)
    return fi.maybe_inject_memory_pressure("system") is True


test("FaultInjector: memory pressure fires", _test_memory_pressure)


def _test_network_error():
    fi = FaultInjector(seed=42)
    fi.configure(FaultType.NETWORK_ERROR, enabled=True, probability=1.0)
    try:
        fi.maybe_inject_network_error("api")
        return False
    except ConnectionError as e:
        return "CHAOS" in str(e)


test("FaultInjector: network error fires", _test_network_error)

test("FaultInjector: multiple configs", lambda: (
    fi := FaultInjector(seed=42),
    fi.configure(FaultType.TIMEOUT, enabled=True, probability=0.5),
    fi.configure(FaultType.NETWORK_ERROR, enabled=True, probability=0.3),
    stats := fi.get_stats(),
    len(stats["configured"]) == 2,
)[-1])

test("FaultConfig: should_fire at p=0", lambda: (
    cfg := FaultConfig(enabled=True, probability=0.0),
    not cfg.should_fire(__import__('random').Random(42)),
)[-1])

test("FaultConfig: should_fire at p=1", lambda: (
    cfg := FaultConfig(enabled=True, probability=1.0),
    cfg.should_fire(__import__('random').Random(42)),
)[-1])

test("FaultConfig: disabled never fires", lambda: (
    cfg := FaultConfig(enabled=False, probability=1.0),
    not cfg.should_fire(__import__('random').Random(42)),
)[-1])

test("FaultInjector: get_stats", lambda: (
    fi := FaultInjector(seed=42),
    fi.configure(FaultType.TIMEOUT, enabled=True, probability=1.0, max_delay_ms=1),
    stats := fi.get_stats(),
    "total_faults" in stats and "by_type" in stats,
)[-1])

test("FaultInjector: reset clears events", lambda: (
    fi := FaultInjector(seed=42),
    fi.configure(FaultType.MEMORY_PRESSURE, enabled=True, probability=1.0),
    fi.maybe_inject_memory_pressure("test"),
    fi.reset(),
    fi.get_stats()["total_faults"] == 0,
)[-1])

# ═══════════════════════════════════════════════════════════
from runtime_v2.chaos.resilience_guard import (
    ResilienceGuard, RetryPolicy, CircuitBreaker,
    CircuitBreakerConfig, CircuitState,
)

print("\n── ResilienceGuard ──")

test("RetryPolicy: default", lambda: (
    p := RetryPolicy(),
    p.max_retries == 3 and p.base_delay_ms == 100.0,
)[-1])

test("RetryPolicy: custom", lambda: (
    p := RetryPolicy(max_retries=5, base_delay_ms=50.0, max_delay_ms=1000.0),
    p.max_retries == 5 and p.base_delay_ms == 50.0,
)[-1])

test("RetryPolicy: delay_for_attempt", lambda: (
    p := RetryPolicy(base_delay_ms=100.0, exponential_base=2.0, jitter=False),
    delay := p.delay_for_attempt(0, __import__('random').Random(42)),
    delay == 0.1,
)[-1])

test("CircuitBreaker: starts CLOSED", lambda: (
    cb := CircuitBreaker(),
    cb.state == CircuitState.CLOSED,
)[-1])

test("CircuitBreaker: allows in CLOSED", lambda: (
    cb := CircuitBreaker(),
    cb.allow_request() is True,
)[-1])

test("CircuitBreaker: opens on failures", lambda: (
    cb := CircuitBreaker(CircuitBreakerConfig(failure_threshold=3)),
    [cb.record_failure() for _ in range(3)],
    cb.state == CircuitState.OPEN,
)[-1])

test("CircuitBreaker: blocks in OPEN", lambda: (
    cb := CircuitBreaker(CircuitBreakerConfig(failure_threshold=2)),
    [cb.record_failure() for _ in range(2)],
    cb.allow_request() is False,
)[-1])

test("CircuitBreaker: success decrements counter", lambda: (
    cb := CircuitBreaker(CircuitBreakerConfig(failure_threshold=5)),
    cb.record_failure(),
    cb.record_success(),
    cb.state == CircuitState.CLOSED,
)[-1])

test("CircuitBreaker: reset to CLOSED", lambda: (
    cb := CircuitBreaker(CircuitBreakerConfig(failure_threshold=2)),
    [cb.record_failure() for _ in range(2)],
    cb.reset(),
    cb.state == CircuitState.CLOSED,
)[-1])

test("CircuitBreaker: get_stats", lambda: (
    cb := CircuitBreaker(name="test_cb"),
    stats := cb.get_stats(),
    stats["name"] == "test_cb" and "state" in stats,
)[-1])

test("ResilienceGuard: create default", lambda: ResilienceGuard() is not None)


def _test_retry_success():
    guard = ResilienceGuard(retry_policy=RetryPolicy(max_retries=3, base_delay_ms=1), seed=42)
    return guard.execute_with_retry(lambda: "ok") == "ok"


test("ResilienceGuard: execute_with_retry success", _test_retry_success)


def _test_retry_retries():
    call_count = [0]

    def flaky():
        call_count[0] += 1
        if call_count[0] < 3:
            raise RuntimeError("fail")
        return "ok"

    guard = ResilienceGuard(retry_policy=RetryPolicy(max_retries=3, base_delay_ms=1), seed=42)
    result = guard.execute_with_retry(flaky)
    return result == "ok" and call_count[0] == 3


test("ResilienceGuard: execute_with_retry retries", _test_retry_retries)

test("ResilienceGuard: execute_with_resilience primary", lambda: (
    guard := ResilienceGuard(seed=42),
    result := guard.execute_with_resilience(primary=lambda: "primary_ok"),
    result == "primary_ok",
)[-1])


def _test_resilience_fallback():
    guard = ResilienceGuard(
        retry_policy=RetryPolicy(max_retries=0, base_delay_ms=1),
        seed=42,
    )
    result = guard.execute_with_resilience(
        primary=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
        fallback=lambda: "fallback_ok",
    )
    return result == "fallback_ok"


test("ResilienceGuard: execute_with_resilience fallback", _test_resilience_fallback)


def _test_resilience_degraded():
    guard = ResilienceGuard(
        retry_policy=RetryPolicy(max_retries=0, base_delay_ms=1),
        seed=42,
    )
    result = guard.execute_with_resilience(
        primary=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
        fallback=lambda: (_ for _ in ()).throw(RuntimeError("fail2")),
        degraded_response="degraded_ok",
    )
    return result == "degraded_ok"


test("ResilienceGuard: execute_with_resilience degraded", _test_resilience_degraded)

test("ResilienceGuard: get_stats", lambda: (
    guard := ResilienceGuard(seed=42),
    guard.execute_with_resilience(primary=lambda: "ok"),
    stats := guard.get_stats(),
    "total_calls" in stats and stats["total_calls"] == 1,
)[-1])

test("ResilienceGuard: reset", lambda: (
    guard := ResilienceGuard(seed=42),
    guard.execute_with_resilience(primary=lambda: "ok"),
    guard.reset(),
    guard.get_stats()["total_calls"] == 0,
)[-1])

# ═══════════════════════════════════════════════════════════
from runtime_v2.chaos.chaos_profile import (
    ChaosProfile, CHAOS_PROFILES, build_chaos_stack, get_chaos_profile,
)

print("\n── ChaosProfile ──")

test("ChaosProfile: disabled exists", lambda: "disabled" in CHAOS_PROFILES)
test("ChaosProfile: dev_chaos exists", lambda: "dev_chaos" in CHAOS_PROFILES)
test("ChaosProfile: staging_chaos exists", lambda: "staging_chaos" in CHAOS_PROFILES)

test("ChaosProfile: disabled has no faults", lambda: (
    p := CHAOS_PROFILES["disabled"],
    not p.enable_timeout and not p.enable_middleware_crash,
)[-1])

test("ChaosProfile: dev_chaos has faults", lambda: (
    p := CHAOS_PROFILES["dev_chaos"],
    p.enable_timeout and p.timeout_probability > 0,
)[-1])

test("ChaosProfile: to_dict", lambda: (
    d := CHAOS_PROFILES["disabled"].to_dict(),
    d["name"] == "disabled" and "chaos_policy" in d,
)[-1])

test("build_chaos_stack: disabled", lambda: (
    result := build_chaos_stack("disabled", seed=42),
    len(result) == 3 and result[0] is not None,
)[-1])

test("build_chaos_stack: dev_chaos", lambda: (
    result := build_chaos_stack("dev_chaos", seed=42),
    len(result) == 3 and result[0] is not None,
)[-1])

test("build_chaos_stack: staging_chaos", lambda: (
    result := build_chaos_stack("staging_chaos", seed=42),
    len(result) == 3 and result[0] is not None,
)[-1])


def _test_unknown_profile():
    try:
        get_chaos_profile("nonexistent")
        return False
    except ValueError:
        return True


test("build_chaos_stack: unknown raises ValueError", _test_unknown_profile)

# ═══════════════════════════════════════════════════════════
from runtime_v2.chaos.injector import ChaosInjector, ChaosPolicy

print("\n── ChaosInjector (existing) ──")

test("ChaosInjector: create", lambda: ChaosInjector() is not None)

test("ChaosInjector: with LIGHT policy", lambda: (
    ci := ChaosInjector(ChaosPolicy.LIGHT, seed=42),
    ci.policy == ChaosPolicy.LIGHT,
)[-1])

test("ChaosInjector: DISABLED not enabled", lambda: (
    ci := ChaosInjector(ChaosPolicy.DISABLED),
    ci.enabled is False,
)[-1])

test("ChaosInjector: LIGHT is enabled", lambda: (
    ci := ChaosInjector(ChaosPolicy.LIGHT),
    ci.enabled is True,
)[-1])

# ═══════════════════════════════════════════════════════════
print("\n── Chaos Integration ──")


def _test_resilience_survives_timeout():
    fi = FaultInjector(seed=42)
    fi.configure(FaultType.TIMEOUT, enabled=True, probability=1.0, max_delay_ms=1)
    guard = ResilienceGuard(
        retry_policy=RetryPolicy(max_retries=0, base_delay_ms=1),
        seed=42,
    )
    result = guard.execute_with_resilience(
        primary=lambda: fi.maybe_inject_timeout("test") or "never",
        fallback=lambda: "fallback_saved",
    )
    return result == "fallback_saved"


test("Integration: resilience survives timeout fault", _test_resilience_survives_timeout)


def _test_resilience_survives_network():
    fi = FaultInjector(seed=42)
    fi.configure(FaultType.NETWORK_ERROR, enabled=True, probability=1.0)
    guard = ResilienceGuard(
        retry_policy=RetryPolicy(max_retries=0, base_delay_ms=1),
        seed=42,
    )
    result = guard.execute_with_resilience(
        primary=lambda: fi.maybe_inject_network_error("api") or "never",
        fallback=lambda: "fallback_saved",
    )
    return result == "fallback_saved"


test("Integration: resilience survives network fault", _test_resilience_survives_network)

test("Integration: no fault at disabled profile", lambda: (
    result := build_chaos_stack("disabled", seed=42),
    fi := result[1],
    fi.maybe_inject_timeout("test") == False,
)[-1])

test("Integration: circuit breaker prevents cascade", lambda: (
    guard := ResilienceGuard(
        retry_policy=RetryPolicy(max_retries=0, base_delay_ms=1),
        circuit_breaker_config=CircuitBreakerConfig(failure_threshold=2),
        seed=42,
    ),
    [guard.execute_with_resilience(
        primary=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
        degraded_response="degraded",
        component="test",
    ) for _ in range(3)],
    stats := guard.get_stats(),
    stats["circuit_breaks"] >= 1,
)[-1])

test("Integration: resilience with dict return", lambda: (
    guard := ResilienceGuard(seed=42),
    result := guard.execute_with_resilience(
        primary=lambda: {"status": "ok", "data": [1, 2, 3]},
    ),
    result["status"] == "ok" and len(result["data"]) == 3,
)[-1])

test("Integration: truncation preserves partial", lambda: (
    fi := FaultInjector(seed=42),
    fi.configure(FaultType.PARTIAL_LLM, enabled=True, probability=1.0, truncate_ratio=0.5),
    original := "A" * 100,
    result := fi.maybe_truncate_response(original, "llm"),
    len(result) == 50,
)[-1])

test("Integration: FaultInjector stats after events", lambda: (
    fi := FaultInjector(seed=42),
    fi.configure(FaultType.MEMORY_PRESSURE, enabled=True, probability=1.0),
    fi.maybe_inject_memory_pressure("test"),
    fi.maybe_inject_memory_pressure("test"),
    stats := fi.get_stats(),
    stats["total_faults"] == 2,
)[-1])


def _test_half_open_recovery():
    cb = CircuitBreaker(CircuitBreakerConfig(
        failure_threshold=2,
        recovery_timeout_s=0.05,
        success_threshold=1,
    ))
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.06)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow_request() is True
    cb.record_success()
    return cb.state == CircuitState.CLOSED


test("Integration: CircuitBreaker half-open recovery", _test_half_open_recovery)

# ═══════════════════════════════════════════════════════════
print()
print("=" * 60)
print(f"  Chaos Engineering Tests: {passed}/{total} passed, {failed} failed")
print("=" * 60)

if __name__ == "__main__":
    sys.exit(1 if failed else 0)
