#!/usr/bin/env python3
"""
Test: No invalid attribute access across the codebase.

Scans source files for known contract violations:
- .anomaly_score on AnomalyReport (should be .score)
- .flags on AnomalyReport (should be .findings)  
- _routing_metrics.record( (should be .record_result()
- route(complexity_score= (should be route(query=)
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
print("  No Invalid Attribute Access — Contract Audit")
print("=" * 60)

project_root = os.path.join(os.path.dirname(__file__), '..', '..')

# Files to scan (source files, not logs)
scan_dirs = [
    os.path.join(project_root, 'shell'),
    os.path.join(project_root, 'runtime_v2'),
    os.path.join(project_root, 'tools'),
    os.path.join(project_root, 'runtime'),
]

# Collect all .py files
py_files = []
for d in scan_dirs:
    if os.path.isdir(d):
        for root, dirs, files in os.walk(d):
            for f in files:
                if f.endswith('.py'):
                    py_files.append(os.path.join(root, f))

print(f"    Scanning {len(py_files)} source files...\n")

# ── Violation patterns ──
# Each: (pattern, description, exception_file_patterns)
violations = [
    # AnomalyReport contract
    (r'anomaly\.anomaly_score', 'anomaly.anomaly_score (should be anomaly.score)', []),
    (r'anomaly\.flags', 'anomaly.flags (should be anomaly.findings)', []),

    # RoutingMetrics contract
    (r'_routing_metrics\.record\s*\(', '_routing_metrics.record() (should be .record_result())', []),
    (r'_routing_metrics\.add\s*\(', '_routing_metrics.add() (invalid method)', []),
    (r'_routing_metrics\.track\s*\(', '_routing_metrics.track() (invalid method)', []),

    # AdaptiveRouter contract
    (r'\.route\(\s*complexity_score\s*=', 'route(complexity_score=) (should be route(query=))', []),

    # SpanTree contract (exclude tracing.py which has its own RequestTrace.span_count)
    (r'\.end_span\s*\(', '.end_span() (should be .finish_span())', []),
    (r'\.span_count\b', '.span_count (should be .total_spans)', ['tracing.py']),

    # CircuitBreaker contract  
    (r'\.is_open\b', '.is_open (should be .allow_request())', ['test_']),
]

all_findings = []

for pattern, desc, exceptions in violations:
    findings = []
    for fpath in py_files:
        fname = os.path.basename(fpath)
        # Skip exception files
        if any(exc in fname for exc in exceptions):
            continue
        with open(fpath, 'r', errors='ignore') as f:
            for i, line in enumerate(f, 1):
                # Skip comments
                stripped = line.lstrip()
                if stripped.startswith('#'):
                    continue
                if re.search(pattern, line):
                    rel = os.path.relpath(fpath, project_root)
                    findings.append(f"{rel}:{i}")

    test(f"No '{desc}'",
         len(findings) == 0,
         f"Found in: {', '.join(findings[:5])}")
    all_findings.extend(findings)

# ── Summary ──
print()
if all_findings:
    print(f"    ⚠ Total violations: {len(all_findings)}")
    for f in all_findings[:10]:
        print(f"      {f}")
else:
    print("    ✅ No contract violations found in source code")

print()
print("=" * 60)
print(f"  Contract Audit: {passed}/{total} passed, {failed} failed")
print("=" * 60)
if __name__ == "__main__":
    sys.exit(1 if failed else 0)
