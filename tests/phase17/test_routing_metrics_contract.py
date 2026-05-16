#!/usr/bin/env python3
"""
Test: RoutingMetrics contract consistency.

Verifies that RoutingMetrics has a stable API
and that all consumers use canonical methods only.
"""

import os
import sys
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

passed = 0
failed = 0
total = 0


def test(name, condition, detail=""):
    global passed, failed, total
    total += 1
    if condition:
        passed += 1
        print(f"  ✅ {total:03d}. {name}")
    else:
        failed += 1
        print(f"  ❌ {total:03d}. {name}  — {detail}")


print("=" * 60)
print("  RoutingMetrics Contract Consistency Tests")
print("=" * 60)

# ── 1. Import and instantiate ──
try:
    from lina.runtime_v2.routing.routing_metrics import RoutingMetrics, TierRecord
except ImportError:
    from runtime_v2.routing.routing_metrics import RoutingMetrics, TierRecord

metrics = RoutingMetrics()

# ── 2. Canonical methods exist ──
test("has record_result()", hasattr(metrics, 'record_result') and callable(metrics.record_result))
test("has record_decision()", hasattr(metrics, 'record_decision') and callable(metrics.record_decision))
test("has avg_latency()", hasattr(metrics, 'avg_latency') and callable(metrics.avg_latency))
test("has failure_rate()", hasattr(metrics, 'failure_rate') and callable(metrics.failure_rate))
test("has p95_latency()", hasattr(metrics, 'p95_latency') and callable(metrics.p95_latency))
test("has get_stats()", hasattr(metrics, 'get_stats') and callable(metrics.get_stats))
test("has reset()", hasattr(metrics, 'reset') and callable(metrics.reset))

# ── 3. Obsolete methods do NOT exist ──
test("NO .record() method", not hasattr(metrics, 'record'),
     "Found .record() — should be record_result()")
test("NO .add() method", not hasattr(metrics, 'add'))
test("NO .track() method", not hasattr(metrics, 'track'))
test("NO .observe() method", not hasattr(metrics, 'observe'))
test("NO .log_result() method", not hasattr(metrics, 'log_result'))

# ── 4. record_result() works correctly ──
metrics.record_result(tier="full", latency_ms=50.0, success=True)
metrics.record_result(tier="full", latency_ms=100.0, success=False)
metrics.record_result(tier="full", latency_ms=200.0, success=True)

test("avg_latency(full) > 0", metrics.avg_latency("full") > 0)
test("failure_rate(full) > 0", metrics.failure_rate("full") > 0)
test("p95_latency(full) > 0", metrics.p95_latency("full") > 0)
test("avg_latency(full) after 3 records", metrics.avg_latency("full") > 0)

# ── 5. get_stats() structure ──
stats = metrics.get_stats()
test("stats has total_decisions", 'total_decisions' in stats)
test("stats has tier_counts", 'tier_counts' in stats)
test("stats has per_tier", 'per_tier' in stats)
test("per_tier has full", 'full' in stats['per_tier'])
test("per_tier no mini", 'mini' not in stats['per_tier'])

# ── 6. Source code audit — commander.py ──
print()
print("── Source Code Audit ──")
project_root = os.path.join(os.path.dirname(__file__), '..', '..')
commander_path = os.path.join(project_root, 'shell', 'commander.py')

if os.path.exists(commander_path):
    with open(commander_path, 'r') as f:
        source = f.read()

    # No .record( calls on routing_metrics
    bad = re.findall(r'_routing_metrics\.record\s*\(', source)
    test("commander.py: no _routing_metrics.record() calls",
         len(bad) == 0,
         f"Found {len(bad)} — should be record_result()")

    # Has .record_result( calls
    good = re.findall(r'_routing_metrics\.record_result\s*\(', source)
    test("commander.py: uses _routing_metrics.record_result()",
         len(good) >= 1,
         "No record_result() calls found")

    # No .add( calls
    bad_add = re.findall(r'_routing_metrics\.add\s*\(', source)
    test("commander.py: no _routing_metrics.add() calls",
         len(bad_add) == 0)

    # No .track( calls
    bad_track = re.findall(r'_routing_metrics\.track\s*\(', source)
    test("commander.py: no _routing_metrics.track() calls",
         len(bad_track) == 0)
else:
    test("commander.py exists", False, f"Not found at {commander_path}")

# ── 7. Source code audit — adaptive_router.py ──
router_path = os.path.join(project_root, 'runtime_v2', 'routing', 'adaptive_router.py')
if os.path.exists(router_path):
    with open(router_path, 'r') as f:
        router_source = f.read()

    bad_router = re.findall(r'_metrics\.record\s*\(', router_source)
    test("adaptive_router.py: no _metrics.record() calls",
         len(bad_router) == 0,
         f"Found {len(bad_router)} — should be record_result()")
else:
    test("adaptive_router.py exists", False)

# ── 8. Thread safety ──
import threading
errors = []

def stress_record(n):
    try:
        for i in range(n):
            metrics.record_result(tier="full", latency_ms=float(i), success=True)
    except Exception as e:
        errors.append(str(e))

threads = [threading.Thread(target=stress_record, args=(100,)) for _ in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()

test("Thread-safe record_result() (10 threads × 100)", len(errors) == 0,
     f"Errors: {errors[:3]}")

print()
print("=" * 60)
print(f"  RoutingMetrics Contract: {passed}/{total} passed, {failed} failed")
print("=" * 60)
if __name__ == "__main__":
    sys.exit(1 if failed else 0)
