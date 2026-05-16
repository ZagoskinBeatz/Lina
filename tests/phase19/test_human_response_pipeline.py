# -*- coding: utf-8 -*-
"""
Тесты Phase 19 (adapted for Phase 20).

Покрытие HumanResponseLayer:
  §1.  Leakage detection
  §2.  Prefix removal
  §3.  Fallback (empty, short, leaked)
  §4.  Clean passthrough
  §5.  Stats tracking
  §6.  Constants
  §7.  Контракты

IntentClassifier и dual-mode удалены в Phase 20.
HumanResponseLayer остаётся как sanitizer/leakage/fallback guard.
"""

import sys
import os
import re
import json

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LINA_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
if _LINA_ROOT not in sys.path:
    sys.path.insert(0, _LINA_ROOT)

passed = 0
failed = 0
total = 0


def ok(tag, msg=""):
    global passed, total
    passed += 1
    total += 1
    print(f"  ✅ [{tag}] {msg}")


def fail(tag, msg=""):
    global failed, total
    failed += 1
    total += 1
    print(f"  ❌ [{tag}] {msg}")


def check(cond, tag, msg=""):
    if cond:
        ok(tag, msg)
    else:
        fail(tag, msg)


# ═══════════════════════════════════════════════════════════
#  §1. HumanResponseLayer — leakage detection
# ═══════════════════════════════════════════════════════════

print("\n§1. HumanResponseLayer — leakage detection")

from lina.core.human_response import (
    HumanResponseLayer,
    SanitizeResult,
    SAFE_FALLBACK_RESPONSE,
    _LEAKAGE_PATTERNS,
    _TECH_PREFIXES,
    _MIN_USEFUL_LENGTH,
)

layer = HumanResponseLayer()

# 1.1 JSON с intent и confidence → leakage
sr = layer.sanitize('{"intent": "general", "confidence": 0.9}', "test")
check(sr.leakage_detected, "LEAK_JSON",
      f"JSON intent/confidence → leakage={sr.leakage_detected}")

# 1.2 Routing debug → leakage
sr = layer.sanitize('Route: intent=general mode=default fallback=none', "test")
check(sr.leakage_detected, "LEAK_ROUTE",
      f"routing debug → leakage={sr.leakage_detected}")

# 1.3 Normal text → NOT leakage
sr = layer.sanitize("Привет! Как я могу помочь?", "test")
check(not sr.leakage_detected, "NO_LEAK",
      f"normal text → ok")

# 1.4 JSON without intent → NOT leakage
sr = layer.sanitize('{"name": "test", "value": 42}', "test")
check(not sr.leakage_detected, "NO_LEAK_JSON",
      f"JSON without intent → ok")

# 1.5 Nested JSON with intent → leakage
sr = layer.sanitize('Result: {"intent": "general", "confidence": 0.5}', "test")
check(sr.leakage_detected, "LEAK_NESTED",
      f"nested intent JSON → leakage")

# 1.6 Patterns count
check(len(_LEAKAGE_PATTERNS) >= 8, "PATTERN_COUNT",
      f"{len(_LEAKAGE_PATTERNS)} patterns")


# ═══════════════════════════════════════════════════════════
#  §2. Prefix removal
# ═══════════════════════════════════════════════════════════

print("\n§2. Prefix removal")

layer2 = HumanResponseLayer()

# 2.1 ### ASSISTANT → removed
sr = layer2.sanitize("### ASSISTANT\nОтвет.", "test")
check("Ответ." in sr.text, "PREFIX_ASSISTANT",
      f"### ASSISTANT removed")

# 2.2 Lina: → removed
sr = layer2.sanitize("Lina: Вот ответ!", "test")
check("Вот ответ!" in sr.text, "PREFIX_LINA",
      f"Lina: removed")

# 2.3 Normal → unchanged
sr = layer2.sanitize("Нормальный ответ.", "test")
check(sr.text == "Нормальный ответ.", "NO_PREFIX",
      f"no prefix → unchanged")

# 2.4 Tech prefix count
check(len(_TECH_PREFIXES) >= 6, "PREFIX_COUNT",
      f"{len(_TECH_PREFIXES)} prefixes")


# ═══════════════════════════════════════════════════════════
#  §3. Fallback (empty, short, leaked)
# ═══════════════════════════════════════════════════════════

print("\n§3. Fallback")

# 3.1 Empty → fallback
layer3 = HumanResponseLayer()
sr = layer3.sanitize("", "test")
check(sr.fallback_used and sr.text == SAFE_FALLBACK_RESPONSE, "FB_EMPTY",
      "empty → fallback")

# 3.2 None → fallback
sr = layer3.sanitize(None, "test")
check(sr.fallback_used, "FB_NONE", "None → fallback")

# 3.3 Whitespace → fallback
sr = layer3.sanitize("   \n\t  ", "test")
check(sr.fallback_used, "FB_WS", "whitespace → fallback")

# 3.4 Custom fallback_fn called
called = {"n": 0}
def mock_fb(q):
    called["n"] += 1
    return f"Fallback: {q}"

layer3b = HumanResponseLayer(fallback_fn=mock_fb)
sr = layer3b.sanitize("", "query")
check(called["n"] == 1 and "Fallback" in sr.text, "FB_CUSTOM",
      "custom fallback called")

# 3.5 Leaky fallback → safe response
def leaky_fb(q):
    return '{"intent": "general", "confidence": 0.9}'

layer3c = HumanResponseLayer(fallback_fn=leaky_fb)
sr = layer3c.sanitize("", "test")
check(sr.text == SAFE_FALLBACK_RESPONSE, "FB_LEAK",
      "leaky fallback → safe")

# 3.6 Failing fallback → safe response
def fail_fb(q):
    raise RuntimeError("crash")

layer3d = HumanResponseLayer(fallback_fn=fail_fb)
sr = layer3d.sanitize("", "test")
check(sr.text == SAFE_FALLBACK_RESPONSE, "FB_ERROR",
      "failing fallback → safe")

# 3.7 Leakage → fallback
layer3e = HumanResponseLayer(fallback_fn=mock_fb)
called["n"] = 0
sr = layer3e.sanitize('{"intent": "math", "confidence": 0.9}', "2+2")
check(sr.leakage_detected and called["n"] == 1, "FB_LEAK_DETECT",
      "leakage → fallback called")


# ═══════════════════════════════════════════════════════════
#  §4. Clean passthrough
# ═══════════════════════════════════════════════════════════

print("\n§4. Clean passthrough")

layer4 = HumanResponseLayer()

# 4.1 Normal → clean
sr = layer4.sanitize("Здравствуйте!", "test")
check(sr.text == "Здравствуйте!" and not sr.was_sanitized, "CLEAN",
      "normal → clean")

# 4.2 Code → clean
sr = layer4.sanitize("def f():\n    return 42", "test")
check(not sr.leakage_detected, "CLEAN_CODE",
      "code → clean")

# 4.3 Single char → clean (min_length=1)
sr = layer4.sanitize("4", "2+2")
check(sr.text == "4" and not sr.was_sanitized, "CLEAN_SINGLE",
      "'4' → clean")

# 4.4 Triple newlines → reduced
sr = layer4.sanitize("A\n\n\n\nB", "test")
check("\n\n\n" not in sr.text, "NEWLINES",
      "triple newlines reduced")

# 4.5 to_dict
d = sr.to_dict()
expected = {"was_sanitized", "leakage_detected", "fallback_used", "original_length", "issues"}
check(expected.issubset(d.keys()), "TO_DICT",
      "SanitizeResult.to_dict keys ok")


# ═══════════════════════════════════════════════════════════
#  §5. Stats tracking
# ═══════════════════════════════════════════════════════════

print("\n§5. Stats tracking")

layer5 = HumanResponseLayer()
layer5.sanitize("Чистый.", "q1")
layer5.sanitize('{"intent": "math", "confidence": 0.9}', "q2")
layer5.sanitize("", "q3")

stats = layer5.get_stats()

check(stats["total"] == 3, "STATS_TOTAL", f"total={stats['total']}")
check(stats["clean"] == 1, "STATS_CLEAN", f"clean={stats['clean']}")
check(stats["leakage_blocked"] == 1, "STATS_LEAK", f"leak={stats['leakage_blocked']}")
check(stats["fallback_used"] >= 2, "STATS_FB", f"fallback={stats['fallback_used']}")


# ═══════════════════════════════════════════════════════════
#  §6. Constants
# ═══════════════════════════════════════════════════════════

print("\n§6. Constants")

check(_MIN_USEFUL_LENGTH == 1, "MIN_LEN", f"min={_MIN_USEFUL_LENGTH}")
check(len(SAFE_FALLBACK_RESPONSE) > 10, "SAFE_FB_LEN",
      f"safe fallback len={len(SAFE_FALLBACK_RESPONSE)}")
check("{" not in SAFE_FALLBACK_RESPONSE, "SAFE_FB_NO_JSON",
      "safe fallback has no JSON")
check("ошибка" in SAFE_FALLBACK_RESPONSE.lower(), "SAFE_FB_ERR",
      "mentions error")


# ═══════════════════════════════════════════════════════════
#  §7. Контракты
# ═══════════════════════════════════════════════════════════

print("\n§7. Контракты")

# 7.1 SAFE_FALLBACK_RESPONSE is clean
layer7 = HumanResponseLayer()
sr = layer7.sanitize(SAFE_FALLBACK_RESPONSE, "test")
check(not sr.leakage_detected and not sr.fallback_used, "CONTRACT_SAFE",
      "safe fallback is itself clean")

# 7.2 Layer without fallback_fn still works
layer7b = HumanResponseLayer(fallback_fn=None)
sr = layer7b.sanitize("", "test")
check(sr.text == SAFE_FALLBACK_RESPONSE, "CONTRACT_NO_FB",
      "no fallback_fn → safe response")

# 7.3 Regeneration semantics (orchestrator uses regenerate-once)
regen_calls = {"n": 0}
def regen_fn(q):
    regen_calls["n"] += 1
    return f"Regenerated: {q}"

layer7c = HumanResponseLayer(fallback_fn=regen_fn)
sr = layer7c.sanitize("", "тест")
check(regen_calls["n"] == 1 and "Regenerated" in sr.text, "CONTRACT_REGEN",
      "fallback = regeneration once")


# ═══════════════════════════════════════════════════════════
#  ИТОГИ
# ═══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"  Phase 19 — HumanResponseLayer Tests (Phase 20 adapted)")
print(f"  Passed: {passed}/{total}")
print(f"  Failed: {failed}/{total}")
print(f"{'='*60}")

if failed > 0:
    print(f"\n❌ {failed} test(s) FAILED")
    if __name__ == "__main__":
        sys.exit(1)
else:
    print(f"\n✅ All {passed} tests PASSED")
    if __name__ == "__main__":
        sys.exit(0)
