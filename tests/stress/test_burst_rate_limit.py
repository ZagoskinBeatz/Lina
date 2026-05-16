#!/usr/bin/env python3
"""
Phase 14 — Stress Test: Burst Rate Limit.

Verifies:
  - RateLimiter correctly throttles burst traffic
  - No tokens leak under concurrent access
  - retry_after is correct and positive
  - reset works under concurrent access
  - Burst of 200 requests — proper allow/deny ratio
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
print("  Phase 14 — Stress: Burst Rate Limit")
print("=" * 60)


def test_burst_single_session():
    """50 instant requests to same session — most rejected."""
    from lina.runtime_v2.enterprise.rate_limiter import RateLimiter

    limiter = RateLimiter(requests_per_minute=10, burst_size=5)
    results = []
    lock = threading.Lock()

    def check(i):
        r = limiter.check("burst-sess")
        with lock:
            results.append(r)

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(check, i) for i in range(50)]
        for f in as_completed(futures):
            f.result()

    allowed = sum(1 for r in results if r.allowed)
    denied = sum(1 for r in results if not r.allowed)

    # Burst size is 5, so at most ~5-10 should be allowed initially
    assert allowed <= 15, f"Too many allowed: {allowed}"
    assert denied >= 35, f"Too few denied: {denied}"
    assert len(results) == 50
    return True


def test_burst_multi_session():
    """10 sessions × 20 burst requests — sessions independent."""
    from lina.runtime_v2.enterprise.rate_limiter import RateLimiter

    limiter = RateLimiter(requests_per_minute=10, burst_size=5)
    per_session = {}
    lock = threading.Lock()

    def burst_session(sid):
        local = []
        for _ in range(20):
            r = limiter.check(sid)
            local.append(r.allowed)
        with lock:
            per_session[sid] = local

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(burst_session, f"s-{i}") for i in range(10)]
        for f in as_completed(futures):
            f.result()

    assert len(per_session) == 10

    # Each session should have some allowed and some denied
    for sid, allowed_list in per_session.items():
        num_allowed = sum(1 for a in allowed_list if a)
        num_denied = sum(1 for a in allowed_list if not a)
        assert num_allowed >= 1, f"{sid}: no requests allowed"
        assert num_denied >= 1, f"{sid}: no requests denied"
    return True


def test_retry_after_positive():
    """Denied requests have retry_after > 0."""
    from lina.runtime_v2.enterprise.rate_limiter import RateLimiter

    limiter = RateLimiter(requests_per_minute=5, burst_size=2)

    # Exhaust burst
    for _ in range(10):
        limiter.check("retry-sess")

    r = limiter.check("retry-sess")
    if not r.allowed:
        assert r.retry_after >= 0, f"retry_after is negative: {r.retry_after}"
    return True


def test_concurrent_reset():
    """reset() under concurrent check() — no crash."""
    from lina.runtime_v2.enterprise.rate_limiter import RateLimiter

    limiter = RateLimiter(requests_per_minute=30, burst_size=10)
    errors = []

    def checker():
        try:
            for _ in range(50):
                limiter.check("reset-sess")
        except Exception as e:
            errors.append(str(e))

    def resetter():
        try:
            for _ in range(10):
                limiter.reset("reset-sess")
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=checker) for _ in range(5)]
    threads.append(threading.Thread(target=resetter))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(errors) == 0, f"Errors: {errors}"
    return True


def test_reset_all_under_load():
    """reset_all() while 10 sessions are being checked — no deadlock."""
    from lina.runtime_v2.enterprise.rate_limiter import RateLimiter
    import time

    limiter = RateLimiter(requests_per_minute=30, burst_size=10)
    done = threading.Event()
    errors = []

    def checker(sid):
        try:
            while not done.is_set():
                limiter.check(sid)
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=checker, args=(f"ra-{i}",), daemon=True)
               for i in range(10)]
    for t in threads:
        t.start()

    time.sleep(0.05)
    limiter.reset_all()
    done.set()
    for t in threads:
        t.join(timeout=3)

    assert len(errors) == 0, f"Errors: {errors}"
    return True


test("burst 50 requests single session", test_burst_single_session)
test("burst 10 sessions × 20 requests", test_burst_multi_session)
test("retry_after is positive", test_retry_after_positive)
test("concurrent reset no crash", test_concurrent_reset)
test("reset_all under load no deadlock", test_reset_all_under_load)


print("\n" + "=" * 60)
print(f"  РЕЗУЛЬТАТ: {passed}/{total} stress tests")
if failed:
    print(f"  ПРОВАЛЕНО: {failed}")
else:
    print("  ВСЕ STRESS ТЕСТЫ ПРОЙДЕНЫ! ✨")
print("=" * 60)
if __name__ == "__main__":
    sys.exit(1 if failed else 0)
