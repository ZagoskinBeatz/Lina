#!/usr/bin/env python3
"""
Phase 14 — Stress Test Runner: All stress tests.
Runs all stress test files and aggregates results.
"""

import sys
import os
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, '..', '..'))

STRESS_FILES = [
    "test_50_parallel_sessions.py",
    "test_100_parallel_requests.py",
    "test_burst_rate_limit.py",
    "test_token_budget_isolation.py",
    "test_session_memory_isolation.py",
]

print("=" * 60)
print("  Phase 14 — STRESS TEST SUITE")
print("=" * 60)

total_passed = 0
total_failed = 0
total_tests = 0

for fname in STRESS_FILES:
    fpath = os.path.join(ROOT, fname)
    print(f"\n▶ Running {fname}...")
    result = subprocess.run(
        [sys.executable, fpath],
        capture_output=True, text=True, timeout=120,
        cwd=os.path.join(ROOT, '..', '..')
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")

    # Parse results from output
    for line in result.stdout.splitlines():
        if "РЕЗУЛЬТАТ:" in line:
            parts = line.strip().split()
            for p in parts:
                if "/" in p:
                    try:
                        passed, tests = p.split("/")
                        total_passed += int(passed)
                        total_tests += int(tests)
                        total_failed += int(tests) - int(passed)
                    except ValueError:
                        pass

    if result.returncode != 0:
        print(f"  ⚠ {fname} returned exit code {result.returncode}")

print("\n" + "=" * 60)
print(f"  ИТОГО STRESS: {total_passed}/{total_tests} tests")
if total_failed:
    print(f"  ПРОВАЛЕНО: {total_failed}")
else:
    print("  ВСЕ STRESS ТЕСТЫ ПРОЙДЕНЫ! ✨")
print("=" * 60)
if __name__ == "__main__":
    sys.exit(1 if total_failed else 0)
