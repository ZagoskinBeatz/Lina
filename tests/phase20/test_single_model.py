# -*- coding: utf-8 -*-
"""
Тесты Phase 20 — Single Heavy Model Architecture.

Покрытие:
  §1.  Pipeline: User → RAG → Full → HumanResponseLayer → User
  §2.  RAG heuristic (keyword-based, no JSON, no classifier)
  §3.  Full model config (temperature, top_p, repeat_penalty, n_ctx, max_tokens)
  §4.  No mini model (removed completely)
  §5.  HumanResponseLayer integration (sanitizer, leakage, fallback)
  §6.  Fallback = regenerate once
  §7.  Debug separation (all logs → logger.debug)
  §8.  UX guarantees (100% human-readable)
  §9.  Orchestrator stats
  §10. Edge cases
  §11. Safety layer in single-model pipeline
  §12. Контракты
"""

import sys
import os
import re
import json
import logging
import io

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
#  §1. Pipeline: single model end-to-end
# ═══════════════════════════════════════════════════════════

print("\n§1. Pipeline — single model end-to-end")

from lina.core.orchestrator import (
    LinaOrchestrator,
    OrchestratorResult,
    ToolSafetyLayer,
    SafetyVerdict,
    RAG_KEYWORDS,
    RAG_RELEVANCE_THRESHOLD,
    _check_rag_heuristic,
    BLOCKED_PATTERNS,
)
from lina.core.human_response import (
    HumanResponseLayer,
    SAFE_FALLBACK_RESPONSE,
)

responses = {}

def mock_generate(query, context, system_prompt):
    return responses.get("generate", f"Ответ на: {query}")

def mock_rag(query):
    return responses.get("rag", "")

orch = LinaOrchestrator(
    generate_fn=mock_generate,
    rag_fn=mock_rag,
)

# 1.1 Simple query → full model → clean response
responses["generate"] = "Привет! Я Lina, ваш ассистент."
result = orch.process("Привет")
check(
    "Lina" in result.response
    and result.model_used == "full"
    and not result.rag_used,
    "E2E_SIMPLE",
    f"simple → full model: {result.response[:30]}"
)

# 1.2 RAG query → context injected → full model
responses["rag"] = "Конфигурация: max_tokens=512"
responses["generate"] = "Из документации: max_tokens=512"
result = orch.process("покажи документ с конфигом")
check(
    result.rag_used and len(result.response) > 0,
    "E2E_RAG",
    f"RAG query → rag_used={result.rag_used}, response={result.response[:30]}"
)

# 1.3 Non-RAG query → no context
responses["rag"] = "some context"
responses["generate"] = "Обычный ответ"
result = orch.process("как дела?")
check(
    not result.rag_used,
    "E2E_NO_RAG",
    f"non-RAG → rag_used={result.rag_used}"
)

# 1.4 OrchestratorResult structure
d = result.to_dict()
check(
    all(k in d for k in ["response_length", "rag_used", "model_used", "is_fallback", "elapsed"]),
    "RESULT_DICT",
    f"all keys present"
)

# 1.5 Elapsed > 0
check(result.elapsed > 0, "ELAPSED",
      f"elapsed={result.elapsed:.4f}s")

# 1.6 model_used always "full"
for q in ["привет", "напиши код", "найди документ"]:
    r = orch.process(q)
    check(r.model_used == "full", f"FULL_{q[:5].upper()}",
          f"'{q}' → model_used={r.model_used}")


# ═══════════════════════════════════════════════════════════
#  §2. RAG heuristic
# ═══════════════════════════════════════════════════════════

print("\n§2. RAG heuristic — keyword-based")

# 2.1 All keywords trigger RAG
for kw in ["документ", "база", "config", "лог", "файл"]:
    check(_check_rag_heuristic(f"покажи {kw}"), f"RAG_{kw.upper()[:6]}",
          f"'{kw}' → RAG")

# 2.2 знани → RAG
check(_check_rag_heuristic("из базы знаний"), "RAG_KNOW",
      "знани → RAG")

# 2.3 конфиг → RAG
check(_check_rag_heuristic("покажи конфиг"), "RAG_KONFIG",
      "конфиг → RAG")

# 2.4 Normal text → no RAG
check(not _check_rag_heuristic("привет, как дела?"), "NO_RAG_HI",
      "greeting → no RAG")

# 2.5 Math → no RAG
check(not _check_rag_heuristic("2+2=?"), "NO_RAG_MATH",
      "math → no RAG")

# 2.6 Code → no RAG
check(not _check_rag_heuristic("напиши hello world"), "NO_RAG_CODE",
      "code → no RAG")

# 2.7 Case insensitive
check(_check_rag_heuristic("Покажи ДОКУМЕНТ"), "RAG_CASE",
      "case insensitive")

# 2.8 No JSON in RAG heuristic
# The function returns bool, not JSON
result = _check_rag_heuristic("test")
check(isinstance(result, bool), "RAG_BOOL",
      f"returns bool, not JSON/dict")

# 2.9 Empty RAG context → no injection
responses["rag"] = ""
responses["generate"] = "Ответ без контекста"
result = orch.process("покажи документ")
check(not result.rag_used, "RAG_EMPTY_CTX",
      f"empty RAG context → rag_used={result.rag_used}")

# 2.10 RAG fn returns None → graceful
responses["rag"] = None
result = orch.process("покажи документ")
check(not result.rag_used, "RAG_NONE_CTX",
      f"None RAG context → rag_used={result.rag_used}")

# Fix rag mock to return proper string for remaining tests
responses["rag"] = ""


# ═══════════════════════════════════════════════════════════
#  §3. Full model config
# ═══════════════════════════════════════════════════════════

print("\n§3. Full model config")

from lina.config import config

# 3.1 temperature = 0.7
check(config.llm.full.temperature == 0.7, "CFG_TEMP",
      f"temp={config.llm.full.temperature}")

# 3.2 top_p = 0.95
check(config.llm.full.top_p == 0.95, "CFG_TOP_P",
      f"top_p={config.llm.full.top_p}")

# 3.3 repeat_penalty = 1.1
check(config.llm.full.repeat_penalty == 1.1, "CFG_REPEAT",
      f"repeat_penalty={config.llm.full.repeat_penalty}")

# 3.4 n_ctx = 4096
check(config.llm.full.n_ctx == 4096, "CFG_CTX",
      f"n_ctx={config.llm.full.n_ctx}")

# 3.5 max_tokens = 512
check(config.llm.full.max_tokens == 512, "CFG_TOKENS",
      f"max_tokens={config.llm.full.max_tokens}")


# ═══════════════════════════════════════════════════════════
#  §4. No mini model
# ═══════════════════════════════════════════════════════════

print("\n§4. No mini model")

# 4.1 No mini in LLMConfig
check(not hasattr(config.llm, 'mini'), "NO_MINI_ATTR",
      "LLMConfig has no 'mini'")

# 4.2 No mini_system_prompt
check(not hasattr(config.llm, 'mini_system_prompt'), "NO_MINI_PROMPT",
      "no mini_system_prompt")

# 4.3 No IntentClassifier import in orchestrator
import inspect
orch_module_src = inspect.getsource(sys.modules["lina.core.orchestrator"])
# Check no actual import of IntentClassifier (docstrings mentioning it are ok)
has_import = "from lina.core.intent_classifier import" in orch_module_src or \
             "import IntentClassifier" in orch_module_src
check(not has_import, "NO_CLF_IMPORT",
      "no IntentClassifier import in orchestrator")

# 4.4 No classify_fn parameter
sig = inspect.signature(LinaOrchestrator.__init__)
check("classify_fn" not in sig.parameters, "NO_CLASSIFY_FN",
      "no classify_fn parameter")

# 4.5 No mini_chat_fn parameter
check("mini_chat_fn" not in sig.parameters, "NO_MINI_CHAT_FN",
      "no mini_chat_fn parameter")

# 4.6 No MINI_CHAT constants in orchestrator module
import lina.core.orchestrator as orch_mod
check(not hasattr(orch_mod, 'MINI_CHAT_SYSTEM_PROMPT'), "NO_MINI_CHAT_PROMPT",
      "no MINI_CHAT_SYSTEM_PROMPT")
check(not hasattr(orch_mod, 'MINI_CHAT_TEMPERATURE'), "NO_MINI_CHAT_TEMP",
      "no MINI_CHAT_TEMPERATURE")

# 4.7 No RoutingEngine
check(not hasattr(orch_mod, 'RoutingEngine'), "NO_ROUTING_ENGINE",
      "no RoutingEngine")

# 4.8 No RoutingDecision
check(not hasattr(orch_mod, 'RoutingDecision'), "NO_ROUTING_DECISION",
      "no RoutingDecision")

# 4.9 No INTENT_MODES
check(not hasattr(orch_mod, 'INTENT_MODES'), "NO_INTENT_MODES",
      "no INTENT_MODES")

# 4.10 intent_classifier.py does not exist
import importlib
try:
    importlib.import_module("lina.core.intent_classifier")
    check(False, "NO_CLF_MODULE", "intent_classifier module should not exist")
except (ImportError, ModuleNotFoundError):
    check(True, "NO_CLF_MODULE", "intent_classifier module removed")

# 4.11 full_model_keywords removed (Phase 20.1)
check(not hasattr(config.llm, 'full_model_keywords'), "NO_KEYWORDS",
      "full_model_keywords removed in Phase 20.1")


# ═══════════════════════════════════════════════════════════
#  §5. HumanResponseLayer integration
# ═══════════════════════════════════════════════════════════

print("\n§5. HumanResponseLayer integration")

# 5.1 Orchestrator has human_layer
check(hasattr(orch, 'human_layer'), "HAS_HUMAN_LAYER",
      "orchestrator has human_layer")

# 5.2 JSON response → blocked by human_layer
responses["generate"] = '{"intent": "general", "confidence": 0.9}'
result = orch.process("test")
check(
    '{"intent"' not in result.response and '"confidence"' not in result.response,
    "BLOCK_JSON",
    f"JSON blocked, response={result.response[:30]}"
)

# 5.3 Normal response → passed through
responses["generate"] = "Нормальный человеческий ответ."
result = orch.process("test")
check(result.response == "Нормальный человеческий ответ.", "PASS_NORMAL",
      f"normal → passed through")

# 5.4 Empty response → fallback
responses["generate"] = ""
result = orch.process("test")
check(
    len(result.response) > 0 and result.is_fallback,
    "FB_EMPTY",
    f"empty → fallback, response={result.response[:30]}"
)

# 5.5 Metadata includes sanitize info
responses["generate"] = "Тест metadata"
result = orch.process("test")
check("sanitize" in result.metadata, "META_SANITIZE",
      "metadata has sanitize info")


# ═══════════════════════════════════════════════════════════
#  §6. Fallback = regenerate once
# ═══════════════════════════════════════════════════════════

print("\n§6. Fallback = regenerate once")

gen_calls = {"count": 0}

def tracking_generate(query, context, system_prompt):
    gen_calls["count"] += 1
    if gen_calls["count"] == 1:
        return ""  # First call returns empty → triggers regeneration
    return f"Regenerated: {query}"

orch_regen = LinaOrchestrator(generate_fn=tracking_generate)
gen_calls["count"] = 0
result = orch_regen.process("тест регенерации")

# 6.1 Generate called at least 2 times (original + regenerate)
check(gen_calls["count"] >= 2, "REGEN_CALLS",
      f"generate called {gen_calls['count']}x")

# 6.2 Final response is the regenerated one
check("Regenerated" in result.response or result.is_fallback, "REGEN_RESULT",
      f"regenerated response")

# 6.3 Stats track regenerations
stats = orch_regen.get_stats()
check(stats["orchestrator"]["regenerations"] >= 1, "REGEN_STATS",
      f"regenerations={stats['orchestrator']['regenerations']}")

# 6.4 Failsafe: generate_fn throws → safe fallback
def crashing_generate(query, context, system_prompt):
    raise RuntimeError("model crashed")

orch_crash = LinaOrchestrator(generate_fn=crashing_generate)
result = orch_crash.process("test crash")
check(
    result.is_fallback and len(result.response) > 0,
    "CRASH_SAFE",
    f"crash → safe fallback: {result.response[:30]}"
)

# 6.5 No generate_fn → safe fallback
orch_none = LinaOrchestrator()
result = orch_none.process("test none")
check(
    result.response == SAFE_FALLBACK_RESPONSE,
    "NO_GEN_FB",
    f"no generate_fn → safe fallback"
)


# ═══════════════════════════════════════════════════════════
#  §7. Debug separation
# ═══════════════════════════════════════════════════════════

print("\n§7. Debug separation")

log_buf = io.StringIO()
handler = logging.StreamHandler(log_buf)
handler.setLevel(logging.DEBUG)
handler.setFormatter(logging.Formatter("%(name)s:%(levelname)s:%(message)s"))

orch_logger = logging.getLogger("lina.core.orchestrator")
orch_logger.addHandler(handler)
orch_logger.setLevel(logging.DEBUG)

responses["generate"] = "Debug test response"
result = orch.process("debug test")

log_output = log_buf.getvalue()
orch_logger.removeHandler(handler)

# 7.1 Debug logs present
check("Generated:" in log_output or "Sanitize:" in log_output, "DEBUG_PRESENT",
      "debug logs present")

# 7.2 No debug in response
check(
    "Generated:" not in result.response
    and "Sanitize:" not in result.response
    and "Route:" not in result.response,
    "NO_DEBUG_RESPONSE",
    "no debug in response"
)


# ═══════════════════════════════════════════════════════════
#  §8. UX guarantees
# ═══════════════════════════════════════════════════════════

print("\n§8. UX guarantees")

# 8.1 Any input → non-empty response
for q in ["Привет", "2+2", "напиши код", "", "   "]:
    actual_q = q if q.strip() else "пустой запрос"
    responses["generate"] = f"Ответ на: {actual_q}"
    r = orch.process(actual_q)
    check(len(r.response.strip()) > 0, f"UX_NE_{actual_q[:5]}",
          f"'{actual_q[:10]}' → non-empty")

# 8.2 Response never starts with {
for _ in range(3):
    responses["generate"] = "Нормальный ответ"
    r = orch.process("тест")
    check(not r.response.strip().startswith("{"), "UX_NO_JSON_START",
          "response doesn't start with {")

# 8.3 No JSON intent/confidence in any response
responses["generate"] = "Ответ без JSON"
r = orch.process("тест")
check(
    '{"intent"' not in r.response and '"confidence"' not in r.response,
    "UX_NO_JSON_LEAK",
    "no JSON leak"
)

# 8.4 Unicode/emoji preserved
responses["generate"] = "🎉 Результат! 🚀"
r = orch.process("emoji")
check("🎉" in r.response, "UX_EMOJI",
      "emoji preserved")


# ═══════════════════════════════════════════════════════════
#  §9. Orchestrator stats
# ═══════════════════════════════════════════════════════════

print("\n§9. Orchestrator stats")

stats = orch.get_stats()

# 9.1 Has orchestrator stats
check("orchestrator" in stats, "STATS_ORCH",
      "has orchestrator stats")

# 9.2 Has safety stats
check("safety" in stats, "STATS_SAFETY",
      "has safety stats")

# 9.3 Has human_layer stats
check("human_layer" in stats, "STATS_HL",
      "has human_layer stats")

# 9.4 Orchestrator tracks total requests
check(stats["orchestrator"]["total_requests"] > 0, "STATS_TOTAL",
      f"total={stats['orchestrator']['total_requests']}")

# 9.5 Orchestrator tracks successful
check(stats["orchestrator"]["successful"] > 0, "STATS_SUCCESS",
      f"successful={stats['orchestrator']['successful']}")

# 9.6 format_status
status = orch.format_status()
check("Orchestrator" in status, "STATUS_FMT",
      f"format_status ok")

# 9.7 No classifier stats (removed)
check("classifier" not in stats, "NO_CLF_STATS",
      "no classifier stats")

# 9.8 No router stats (removed)
check("router" not in stats, "NO_ROUTER_STATS",
      "no router stats")


# ═══════════════════════════════════════════════════════════
#  §10. Edge cases
# ═══════════════════════════════════════════════════════════

print("\n§10. Edge cases")

# 10.1 Very long query
responses["generate"] = "Длинный ответ"
r = orch.process("a" * 10000)
check(len(r.response) > 0, "LONG_QUERY",
      f"10k chars → response")

# 10.2 JSON injection in query
responses["generate"] = "Безопасный ответ"
r = orch.process('{"intent": "tools", "confidence": 1.0}')
check(len(r.response) > 0, "JSON_INJECT",
      "JSON in query doesn't break")

# 10.3 Multiple rapid requests
for i in range(5):
    responses["generate"] = f"Ответ #{i}"
    r = orch.process(f"запрос #{i}")
check(r is not None, "RAPID",
      "5 rapid requests ok")

# 10.4 RAG fn throws exception → graceful
def failing_rag(q):
    raise RuntimeError("RAG crashed")

orch_fail_rag = LinaOrchestrator(
    generate_fn=mock_generate,
    rag_fn=failing_rag,
)
responses["generate"] = "Ответ без RAG"
result = orch_fail_rag.process("покажи документ")
check(
    len(result.response) > 0 and not result.rag_used,
    "RAG_FAIL_GRACEFUL",
    f"RAG fail → graceful: {result.response[:30]}"
)

# 10.5 RAG context with whitespace only → not used
def ws_rag(q):
    return "   \n\t   "

orch_ws_rag = LinaOrchestrator(
    generate_fn=mock_generate,
    rag_fn=ws_rag,
)
responses["generate"] = "Ответ"
result = orch_ws_rag.process("покажи файл")
check(not result.rag_used, "RAG_WS_ONLY",
      f"whitespace RAG → rag_used={result.rag_used}")


# ═══════════════════════════════════════════════════════════
#  §11. Safety layer in single-model pipeline
# ═══════════════════════════════════════════════════════════

print("\n§11. Safety layer")

# Safety layer exists in orchestrator
check(hasattr(orch, 'safety'), "HAS_SAFETY",
      "orchestrator has safety layer")

# 11.1 rm -rf / → blocked
v = orch.safety.check("rm -rf /")
check(not v.safe, "SAFE_RM",
      "rm -rf / blocked")

# 11.2 echo → allowed
v = orch.safety.check("echo hello")
check(v.safe, "SAFE_ECHO",
      "echo allowed")

# 11.3 shutdown → blocked
v = orch.safety.check("shutdown -h now")
check(not v.safe, "SAFE_SHUTDOWN",
      "shutdown blocked")


# ═══════════════════════════════════════════════════════════
#  §12. Контракты
# ═══════════════════════════════════════════════════════════

print("\n§12. Контракты")

# 12.1 Pipeline: every request passes through human_layer
orch_stats = orch.get_stats()
orch_total = orch_stats["orchestrator"]["total_requests"]
hl_total = orch_stats["human_layer"]["total"]
check(hl_total >= orch_total, "CONTRACT_SANITIZED",
      f"human_layer.total({hl_total}) >= orch.total({orch_total})")

# 12.2 No intent-based routing
check(not hasattr(orch, 'classifier'), "CONTRACT_NO_CLF",
      "no classifier in orchestrator")
check(not hasattr(orch, 'router'), "CONTRACT_NO_ROUTER",
      "no router in orchestrator")

# 12.3 Single model — always "full"
responses["generate"] = "Контрактный ответ"
r = orch.process("контракт")
check(r.model_used == "full", "CONTRACT_FULL",
      f"always full: model_used={r.model_used}")

# 12.4 RAG is optional enhancement, not routing
r_no_rag = orch.process("простой вопрос")
r_with_rag = orch.process("покажи документ")
check(
    r_no_rag.model_used == r_with_rag.model_used == "full",
    "CONTRACT_RAG_OPTIONAL",
    "RAG doesn't change model — always full"
)

# 12.5 Fallback never returns empty
for q in ["", "test", "ошибка"]:
    actual = q or "пустой"
    responses["generate"] = ""  # force empty response
    r = orch.process(actual)
    check(len(r.response.strip()) > 0, f"CONTRACT_NE_{actual[:5]}",
          f"'{actual}' → never empty response")

# 12.6 Orchestrator docstring mentions Phase 20
check("Phase 20" in LinaOrchestrator.__doc__, "CONTRACT_DOC",
      "docstring mentions Phase 20")

# 12.7 Orchestrator docstring mentions Single
check("Single" in LinaOrchestrator.__doc__ or "single" in LinaOrchestrator.__doc__.lower(),
      "CONTRACT_SINGLE_DOC",
      "docstring mentions Single")


# ═══════════════════════════════════════════════════════════
#  ИТОГИ
# ═══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"  Phase 20 — Single Heavy Model Architecture Tests")
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
