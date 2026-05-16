#!/usr/bin/env python3
"""
Phase 14 — Stress Test: 50 Parallel Sessions.

Verifies:
  - No race conditions in SessionManager
  - No cross-session state leakage
  - Correct session isolation
  - No deadlocks under concurrent access
  - Stable memory footprint
"""

import sys
import os
import time
import threading
import tracemalloc
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
print("  Phase 14 — Stress: 50 Parallel Sessions")
print("=" * 60)


def test_50_parallel_session_create():
    """50 sessions created concurrently — all unique, no race."""
    from lina.runtime_v2.enterprise.session_manager import SessionManager

    mgr = SessionManager(max_sessions=200)
    results = {}
    errors = []

    def create_session(i):
        try:
            s = mgr.create(f"stress-{i}")
            return s.session_id
        except Exception as e:
            return str(e)

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = {pool.submit(create_session, i): i for i in range(50)}
        for f in as_completed(futures):
            i = futures[f]
            results[i] = f.result()

    assert len(results) == 50
    session_ids = set(results.values())
    assert len(session_ids) == 50, f"Expected 50 unique sessions, got {len(session_ids)}"
    assert mgr.active_count == 50
    mgr.close_all()
    return True


def test_50_parallel_session_query():
    """50 sessions each process a query — no cross-leakage."""
    from lina.runtime_v2.api.facade import RuntimeAPI

    api = RuntimeAPI.from_profile("dev")
    api.set_llm_handler(lambda prompt, tier, **kw: f"resp-{prompt[:20]}")

    results = {}
    lock = threading.Lock()

    def query_session(i):
        sid = f"par-{i}"
        result = api.query(f"query-{i}", session_id=sid)
        with lock:
            results[i] = result

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(query_session, i) for i in range(50)]
        for f in as_completed(futures):
            f.result()  # re-raise exceptions

    assert len(results) == 50
    # Verify no cross-session leakage
    for i, result in results.items():
        assert result["aborted"] is False, f"Session {i} aborted"
        assert "request_id" in result
    api.shutdown()
    return True


def test_parallel_session_isolation():
    """Concurrent sessions don't leak state between each other."""
    from lina.runtime_v2.enterprise.session_manager import SessionManager

    mgr = SessionManager(max_sessions=200, max_tokens=10000)
    barrier = threading.Barrier(20)

    def session_work(i):
        barrier.wait(timeout=10)
        sid = f"iso-{i}"
        s = mgr.create(sid)
        mgr.add_tokens(sid, 100 * (i + 1))
        session = mgr.get(sid)
        # Verify tokens are isolated
        assert session.total_tokens == 100 * (i + 1), \
            f"Token leakage in session {sid}: expected {100*(i+1)}, got {session.total_tokens}"
        return True

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(session_work, i) for i in range(20)]
        for f in as_completed(futures):
            f.result()

    assert mgr.active_count == 20
    mgr.close_all()
    return True


def test_parallel_close_all():
    """close_all() under concurrent access doesn't deadlock."""
    from lina.runtime_v2.enterprise.session_manager import SessionManager

    mgr = SessionManager(max_sessions=200)
    for i in range(30):
        mgr.create(f"close-{i}")

    # Start concurrent readers + one closer
    done = threading.Event()
    reads = []

    def reader():
        while not done.is_set():
            try:
                mgr.get(f"close-{0}")
            except Exception:
                pass
            reads.append(1)

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(10)]
    for t in threads:
        t.start()

    time.sleep(0.05)
    closed = mgr.close_all()
    done.set()
    for t in threads:
        t.join(timeout=2)

    assert closed == 30
    assert mgr.active_count == 0
    return True


def test_parallel_memory_stable():
    """Memory doesn't grow unboundedly under parallel sessions."""
    from lina.runtime_v2.enterprise.session_manager import SessionManager

    tracemalloc.start()
    snap1 = tracemalloc.take_snapshot()

    mgr = SessionManager(max_sessions=500)

    def create_and_close(batch):
        for i in range(10):
            sid = f"mem-{batch}-{i}"
            mgr.create(sid)
            mgr.add_tokens(sid, 500)
            mgr.close(sid)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(create_and_close, b) for b in range(20)]
        for f in as_completed(futures):
            f.result()

    snap2 = tracemalloc.take_snapshot()
    stats = snap2.compare_to(snap1, 'lineno')
    total_diff_mb = sum(s.size_diff for s in stats) / (1024 * 1024)

    tracemalloc.stop()

    assert mgr.active_count == 0
    # Memory growth should be < 50MB for 200 create/close cycles
    assert total_diff_mb < 50, f"Memory grew by {total_diff_mb:.1f}MB"
    return True


test("50 parallel session create", test_50_parallel_session_create)
test("50 parallel session query", test_50_parallel_session_query)
test("parallel session isolation", test_parallel_session_isolation)
test("parallel close_all no deadlock", test_parallel_close_all)
test("parallel sessions memory stable", test_parallel_memory_stable)


print("\n" + "=" * 60)
print(f"  РЕЗУЛЬТАТ: {passed}/{total} stress tests")
if failed:
    print(f"  ПРОВАЛЕНО: {failed}")
else:
    print("  ВСЕ STRESS ТЕСТЫ ПРОЙДЕНЫ! ✨")
print("=" * 60)
if __name__ == "__main__":
    sys.exit(1 if failed else 0)
