#!/usr/bin/env python3
"""
Test: AnomalyReport contract consistency.

Verifies that AnomalyReport has a stable, documented API
and that all consumers (commander, tests, injection_graph) use it correctly.
"""

import os
import sys
import ast
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
print("  AnomalyReport Contract Consistency Tests")
print("=" * 60)

# ── 1. Import and instantiate ──
try:
    from lina.core.security.anomaly_detector import AnomalyReport, AnomalyDetector
except ImportError:
    from lina.runtime_v2.security_v3.anomaly_detector import AnomalyReport, AnomalyDetector

report = AnomalyReport(is_anomalous=False, score=0.42, findings=["test"])

test("AnomalyReport has 'score' field", hasattr(report, 'score'))
test("AnomalyReport has 'is_anomalous' field", hasattr(report, 'is_anomalous'))
test("AnomalyReport has 'findings' field", hasattr(report, 'findings'))
test("AnomalyReport has 'entropy' field", hasattr(report, 'entropy'))
test("AnomalyReport has 'repetition_ratio' field", hasattr(report, 'repetition_ratio'))
test("AnomalyReport has 'to_dict' method", hasattr(report, 'to_dict') and callable(report.to_dict))

# ── 2. Contract: score is canonical, anomaly_score does NOT exist ──
test("AnomalyReport does NOT have 'anomaly_score'",
     not hasattr(report, 'anomaly_score'),
     f"Found anomaly_score={getattr(report, 'anomaly_score', '?')}")
test("AnomalyReport does NOT have 'flags'",
     not hasattr(report, 'flags'),
     "Found 'flags' — should be 'findings'")

# ── 3. Value consistency ──
test("score value correct", report.score == 0.42)
test("is_anomalous value correct", report.is_anomalous is False)
test("findings value correct", report.findings == ["test"])

# ── 4. to_dict contract ──
d = report.to_dict()
test("to_dict has 'score' key", 'score' in d)
test("to_dict has 'is_anomalous' key", 'is_anomalous' in d)
test("to_dict has 'findings' key", 'findings' in d)
test("to_dict has 'entropy' key", 'entropy' in d)
test("to_dict has 'repetition_ratio' key", 'repetition_ratio' in d)
test("to_dict does NOT have 'anomaly_score'", 'anomaly_score' not in d)
test("to_dict does NOT have 'flags'", 'flags' not in d)

# ── 5. AnomalyDetector.analyze() returns AnomalyReport ──
detector = AnomalyDetector()
result = detector.analyze("hello world")
test("analyze() returns AnomalyReport", isinstance(result, AnomalyReport))
test("analyze() result has 'score'", hasattr(result, 'score'))
test("analyze() result score is float", isinstance(result.score, float))

# ── 6. Source code audit — no .anomaly_score on AnomalyReport consumers ──
print()
print("── Source Code Audit ──")
project_root = os.path.join(os.path.dirname(__file__), '..', '..')
commander_path = os.path.join(project_root, 'shell', 'commander.py')

if os.path.exists(commander_path):
    with open(commander_path, 'r') as f:
        source = f.read()

    # Find all anomaly.anomaly_score references
    bad_refs = re.findall(r'anomaly\.anomaly_score', source)
    test("commander.py: no 'anomaly.anomaly_score' references",
         len(bad_refs) == 0,
         f"Found {len(bad_refs)} occurrences")

    # Find all anomaly.flags references
    bad_flags = re.findall(r'anomaly\.flags', source)
    test("commander.py: no 'anomaly.flags' references",
         len(bad_flags) == 0,
         f"Found {len(bad_flags)} occurrences")

    # Verify anomaly.score is used correctly
    good_refs = re.findall(r'anomaly\.score', source)
    test("commander.py: uses 'anomaly.score' correctly",
         len(good_refs) >= 1,
         "No anomaly.score references found")

    # Verify anomaly.findings is used correctly
    good_findings = re.findall(r'anomaly\.findings', source)
    test("commander.py: uses 'anomaly.findings' correctly",
         len(good_findings) >= 1,
         "No anomaly.findings references found")

    # Verify route() is called with query= not complexity_score=
    bad_route = re.findall(r'route\(\s*complexity_score=', source)
    test("commander.py: route() uses query= not complexity_score=",
         len(bad_route) == 0,
         f"Found {len(bad_route)} bad route() calls")
else:
    test("commander.py exists", False, f"Not found at {commander_path}")

# ── 7. AnomalyReport dataclass field enumeration ──
print()
print("── Field Enumeration ──")
import dataclasses
fields = {f.name: f.type for f in dataclasses.fields(AnomalyReport)}
print(f"    Fields: {fields}")
test("Exactly 5 dataclass fields",
     len(fields) == 5,
     f"Got {len(fields)}: {list(fields.keys())}")
expected = {'is_anomalous', 'score', 'findings', 'entropy', 'repetition_ratio'}
test("Field names match expected set",
     set(fields.keys()) == expected,
     f"Got {set(fields.keys())}, expected {expected}")

print()
print("=" * 60)
print(f"  AnomalyReport Contract: {passed}/{total} passed, {failed} failed")
print("=" * 60)
if __name__ == "__main__":
    sys.exit(1 if failed else 0)
