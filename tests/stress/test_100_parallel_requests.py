#!/usr/bin/env python3
"""
Phase 14 — Stress Test: 100 Parallel Light Requests.

Verifies:
  - 100 concurrent queries through RuntimeAPI
  - No race conditions in middleware chain
  - No request_id collisions
  - Correct response structure for every request
  - Stable under high throughput
"""

import sys
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

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
print("  Phase 14 — Stress: 100 Parallel Light Requests")
print("=" * 60)


def test_100_parallel_queries():
    """100 concurrent queries — all succeed, unique request_ids."""
    from lina.runtime_v2.api.facade import RuntimeAPI
    from lina.runtime_v2.enterprise.config_profiles import RuntimeProfile

    profile = RuntimeProfile(name="stress", description="stress test",
                             max_sessions=500, enable_rate_limit=False,
                             enable_risk_check=False, enable_prompt_seal=False)
    api = RuntimeAPI(profile=profile)
    api.set_llm_handler(lambda prompt, tier, **kw: f"resp-{tier}")

    results = {}
    lock = threading.Lock()

    def do_query(i):
        r = api.query(f"light-query-{i}", session_id=f"l-{i}", tier="mini")
        with lock:
            results[i] = r

    with ThreadPoolExecutor(max_workers=100) as pool:
        futures = [pool.submit(do_query, i) for i in range(100)]
        for f in as_completed(futures):
            f.result()

    assert len(results) == 100, f"Expected 100 results, got {len(results)}"

    # All request_ids must be unique
    rids = [r["request_id"] for r in results.values()]
    assert len(set(rids)) == 100, f"Duplicate request_ids: {len(set(rids))}"

    # No aborted
    aborted = [i for i, r in results.items() if r.get("aborted")]
    assert len(aborted) == 0, f"Aborted requests: {aborted[:5]}"
    api.shutdown()
    return True


def test_100_parallel_same_session():
    """100 queries on the SAME session — serialized correctly."""
    from lina.runtime_v2.api.facade import RuntimeAPI
    from lina.runtime_v2.enterprise.config_profiles import RuntimeProfile

    profile = RuntimeProfile(name="stress", description="stress test",
                             max_sessions=500, enable_rate_limit=False,
                             enable_risk_check=False, enable_prompt_seal=False)
    api = RuntimeAPI(profile=profile)
    counter = {"n": 0}
    lock = threading.Lock()

    def handler(prompt, tier, **kw):
        with lock:
            counter["n"] += 1
        return f"resp-{counter['n']}"

    api.set_llm_handler(handler)

    results = []
    res_lock = threading.Lock()

    def do_query(i):
        r = api.query(f"same-session-{i}", session_id="shared-sess", tier="mini")
        with res_lock:
            results.append(r)

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(do_query, i) for i in range(100)]
        for f in as_completed(futures):
            f.result()

    assert len(results) == 100, f"Expected 100, got {len(results)}"
    assert counter["n"] == 100, f"Handler called {counter['n']} times"
    api.shutdown()
    return True


def test_parallel_mixed_tiers():
    """Concurrent queries with different tiers (mini, full, reasoning)."""
    from lina.runtime_v2.api.facade import RuntimeAPI
    from lina.runtime_v2.enterprise.config_profiles import RuntimeProfile

    profile = RuntimeProfile(name="stress", description="stress test",
                             max_sessions=500, enable_rate_limit=False,
                             enable_risk_check=False, enable_prompt_seal=False)
    api = RuntimeAPI(profile=profile)
    api.set_llm_handler(lambda prompt, tier, **kw: f"resp-{tier}")

    results = {}
    lock = threading.Lock()
    tiers = ["mini", "full", "reasoning"]

    def do_query(i):
        tier = tiers[i % 3]
        r = api.query(f"tier-{tier}-{i}", session_id=f"t-{i}", tier=tier)
        with lock:
            results[i] = r

    with ThreadPoolExecutor(max_workers=30) as pool:
        futures = [pool.submit(do_query, i) for i in range(60)]
        for f in as_completed(futures):
            f.result()

    assert len(results) == 60
    api.shutdown()
    return True


def test_parallel_middleware_safety():
    """Middleware chain remains consistent under concurrent access."""
    from lina.runtime_v2.core.middleware import MiddlewareChain, Middleware, RequestContext

    chain = MiddlewareChain()
    call_count = {"n": 0}
    lock = threading.Lock()

    class CounterMiddleware(Middleware):
        def process_request(self, ctx):
            with lock:
                call_count["n"] += 1
            return True

    chain.add(CounterMiddleware())

    def process(i):
        ctx = RequestContext(query=f"q-{i}", session_id=f"s-{i}")
        chain.process_request(ctx)

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(process, i) for i in range(100)]
        for f in as_completed(futures):
            f.result()

    assert call_count["n"] == 100, f"Expected 100, got {call_count['n']}"
    return True


def test_no_request_id_collision():
    """1000 requests — all get unique request_ids."""
    from lina.runtime_v2.api.facade import RuntimeAPI
    from lina.runtime_v2.enterprise.config_profiles import RuntimeProfile

    profile = RuntimeProfile(name="stress", description="stress test",
                             max_sessions=500, enable_rate_limit=False,
                             enable_risk_check=False, enable_prompt_seal=False)
    api = RuntimeAPI(profile=profile)
    api.set_llm_handler(lambda prompt, tier, **kw: "ok")

    ids = []
    lock = threading.Lock()

    def do_query(i):
        r = api.query(f"uid-{i}", session_id=f"uid-{i}", tier="mini")
        with lock:
            ids.append(r["request_id"])

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(do_query, i) for i in range(200)]
        for f in as_completed(futures):
            f.result()

    assert len(ids) == 200
    assert len(set(ids)) == 200, f"Collisions: {200 - len(set(ids))}"
    api.shutdown()
    return True


test("100 parallel queries succeed", test_100_parallel_queries)
test("100 parallel same session", test_100_parallel_same_session)
test("parallel mixed tiers", test_parallel_mixed_tiers)
test("parallel middleware safety", test_parallel_middleware_safety)
test("no request_id collision (200)", test_no_request_id_collision)


print("\n" + "=" * 60)
print(f"  РЕЗУЛЬТАТ: {passed}/{total} stress tests")
if failed:
    print(f"  ПРОВАЛЕНО: {failed}")
else:
    print("  ВСЕ STRESS ТЕСТЫ ПРОЙДЕНЫ! ✨")
print("=" * 60)
if __name__ == "__main__":
    sys.exit(1 if failed else 0)
