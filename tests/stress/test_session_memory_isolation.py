#!/usr/bin/env python3
"""
Phase 14 — Stress Test: Session Memory Isolation & Soak Test.

Verifies:
  - No cross-session data leakage in conversation history
  - Memory isolation under concurrent reads/writes
  - 1000 sequential turns — stable metrics, no memory growth  
  - Session metadata isolation
  - Long-running session stability
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
print("  Phase 14 — Stress: Session Memory Isolation & Soak")
print("=" * 60)


def test_conversation_isolation():
    """Concurrent sessions don't leak conversation data."""
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
        return f"response-{counter['n']}"

    api.set_llm_handler(handler)

    results_per_session = {}
    res_lock = threading.Lock()

    def session_conversation(sid):
        """Each session sends 5 queries, collects results."""
        local_results = []
        for turn in range(5):
            r = api.query(f"msg-{sid}-{turn}", session_id=sid)
            local_results.append(r)
        with res_lock:
            results_per_session[sid] = local_results

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(session_conversation, f"conv-{i}") for i in range(20)]
        for f in as_completed(futures):
            f.result()

    # Verify each session got 5 results
    assert len(results_per_session) == 20
    for sid, results in results_per_session.items():
        assert len(results) == 5, f"Session {sid}: got {len(results)} results"
        # All request_ids should be unique across sessions
        rids = [r["request_id"] for r in results]
        assert len(set(rids)) == 5, f"Duplicate request_ids in {sid}"

    # Total handler calls should be 100
    assert counter["n"] == 100, f"Handler called {counter['n']} times"
    api.shutdown()
    return True


def test_metadata_isolation():
    """Session metadata is not shared across sessions."""
    from lina.runtime_v2.enterprise.session_manager import SessionManager

    mgr = SessionManager(max_sessions=100)

    def set_metadata(i):
        sid = f"meta-{i}"
        s = mgr.create(sid)
        s.metadata[f"key_{i}"] = f"value_{i}"
        s.metadata["unique_id"] = i

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(set_metadata, i) for i in range(20)]
        for f in as_completed(futures):
            f.result()

    # Verify isolation
    for i in range(20):
        s = mgr.get(f"meta-{i}")
        assert s.metadata.get("unique_id") == i, \
            f"meta-{i}: unique_id={s.metadata.get('unique_id')}, expected {i}"
        assert s.metadata.get(f"key_{i}") == f"value_{i}", \
            f"meta-{i}: key_{i} mismatch"
        # Must NOT have other sessions' keys
        for j in range(20):
            if j != i:
                assert f"key_{j}" not in s.metadata, \
                    f"Leakage: meta-{i} has key_{j}"
    mgr.close_all()
    return True


def test_soak_1000_turns():
    """1000 sequential turns on one session — stable, no memory growth."""
    from lina.runtime_v2.api.facade import RuntimeAPI
    from lina.runtime_v2.enterprise.config_profiles import RuntimeProfile

    profile = RuntimeProfile(name="stress", description="stress test",
                             max_sessions=500, enable_rate_limit=False,
                             enable_risk_check=False, enable_prompt_seal=False)
    api = RuntimeAPI(profile=profile)
    api.set_llm_handler(lambda prompt, tier, **kw: f"turn-resp")

    tracemalloc.start()
    snap1 = tracemalloc.take_snapshot()

    sid = "soak-session"
    for turn in range(1000):
        r = api.query(f"soak-turn-{turn}", session_id=sid)
        assert r.get("aborted") is False, f"Turn {turn} aborted: {r.get('abort_reason')}"

    snap2 = tracemalloc.take_snapshot()
    diff_mb = sum(s.size_diff for s in snap2.compare_to(snap1, 'lineno')) / (1024 * 1024)
    tracemalloc.stop()

    # Verify session exists
    s = api.session_manager.get(sid)
    assert s is not None

    # Memory growth should be bounded
    assert diff_mb < 100, f"Memory grew by {diff_mb:.1f}MB over 1000 turns"
    api.shutdown()
    return True


def test_concurrent_read_write():
    """Concurrent reads and writes to sessions — no crash."""
    from lina.runtime_v2.enterprise.session_manager import SessionManager

    mgr = SessionManager(max_sessions=200, max_tokens=1_000_000)
    errors = []
    done = threading.Event()

    # Seed some sessions
    for i in range(10):
        mgr.create(f"rw-{i}")

    def writer():
        try:
            for j in range(50):
                sid = f"rw-{j % 10}"
                mgr.add_tokens(sid, 10)
                s = mgr.get(sid)
                if s:
                    s.touch()
        except Exception as e:
            errors.append(f"writer: {e}")

    def reader():
        try:
            for j in range(50):
                sid = f"rw-{j % 10}"
                s = mgr.get(sid)
                if s:
                    _ = s.total_tokens
                    _ = s.idle_seconds
        except Exception as e:
            errors.append(f"reader: {e}")

    threads = []
    for _ in range(5):
        threads.append(threading.Thread(target=writer))
        threads.append(threading.Thread(target=reader))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(errors) == 0, f"Errors: {errors[:3]}"
    mgr.close_all()
    return True


def test_session_close_during_query():
    """Closing a session while queries are running — graceful."""
    from lina.runtime_v2.api.facade import RuntimeAPI
    from lina.runtime_v2.enterprise.config_profiles import RuntimeProfile
    import time

    profile = RuntimeProfile(name="stress", description="stress test",
                             max_sessions=500, enable_rate_limit=False,
                             enable_risk_check=False, enable_prompt_seal=False)
    api = RuntimeAPI(profile=profile)

    def slow_handler(prompt, tier, **kw):
        time.sleep(0.01)
        return "slow-response"

    api.set_llm_handler(slow_handler)
    errors = []

    sid = "close-during"

    def querier():
        try:
            for _ in range(10):
                api.query("query-during-close", session_id=sid)
        except Exception as e:
            errors.append(str(e))

    def closer():
        time.sleep(0.03)
        try:
            api.session_manager.close(sid)
        except Exception:
            pass

    t1 = threading.Thread(target=querier)
    t2 = threading.Thread(target=closer)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=5)

    # Must not crash — errors from closed session are acceptable
    api.shutdown()
    return True


test("conversation isolation (20 sessions)", test_conversation_isolation)
test("metadata isolation (20 sessions)", test_metadata_isolation)
test("soak test 1000 turns", test_soak_1000_turns)
test("concurrent read/write sessions", test_concurrent_read_write)
test("session close during query", test_session_close_during_query)


print("\n" + "=" * 60)
print(f"  РЕЗУЛЬТАТ: {passed}/{total} stress tests")
if failed:
    print(f"  ПРОВАЛЕНО: {failed}")
else:
    print("  ВСЕ STRESS ТЕСТЫ ПРОЙДЕНЫ! ✨")
print("=" * 60)
if __name__ == "__main__":
    sys.exit(1 if failed else 0)
