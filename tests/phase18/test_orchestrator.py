# -*- coding: utf-8 -*-
"""
Тесты Phase 18 (adapted for Phase 20).

Покрытие оставшихся компонентов:
  §1. ToolSafetyLayer — блокировка опасных команд
  §2. Config — параметры full модели (Phase 20)
  §3. Math direct eval
  §4. ToolSafetyLayer — edge cases + stats

IntentClassifier и RoutingEngine удалены в Phase 20.
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
#  §1. ToolSafetyLayer — блокировка опасных команд
# ═══════════════════════════════════════════════════════════

print("\n§1. ToolSafetyLayer — безопасность")

from lina.core.orchestrator import (
    ToolSafetyLayer,
    SafetyVerdict,
    BLOCKED_PATTERNS,
    OrchestratorResult,
    LinaOrchestrator,
    RAG_KEYWORDS,
    RAG_RELEVANCE_THRESHOLD,
    _check_rag_heuristic,
)

safety = ToolSafetyLayer()

# 1.1 rm -rf / → blocked
v = safety.check("rm -rf /")
check(not v.safe, "RM_RF", "rm -rf / blocked")

# 1.2 rm -rf ~ → blocked
v = safety.check("rm -rf ~")
check(not v.safe, "RM_RF_HOME", "rm -rf ~ blocked")

# 1.3 fork bomb → blocked
v = safety.check(":(){ :|:& };:")
check(not v.safe, "FORKBOMB", "fork bomb blocked")

# 1.4 shutdown → blocked
v = safety.check("sudo shutdown -h now")
check(not v.safe, "SHUTDOWN", "shutdown blocked")

# 1.5 reboot → blocked
v = safety.check("reboot")
check(not v.safe, "REBOOT", "reboot blocked")

# 1.6 mkfs → blocked
v = safety.check("mkfs.ext4 /dev/sda1")
check(not v.safe, "MKFS", "mkfs blocked")

# 1.7 dd → blocked
v = safety.check("dd if=/dev/zero of=/dev/sda")
check(not v.safe, "DD", "dd if= blocked")

# 1.8 poweroff → blocked
v = safety.check("poweroff")
check(not v.safe, "POWEROFF", "poweroff blocked")

# 1.9 Безопасная команда → allowed
v = safety.check("ls -la /home")
check(v.safe, "LS_SAFE", "ls -la allowed")

# 1.10 cat → allowed
v = safety.check("cat /etc/hostname")
check(v.safe, "CAT_SAFE", "cat allowed")

# 1.11 echo → allowed
v = safety.check("echo hello world")
check(v.safe, "ECHO_SAFE", "echo allowed")

# 1.12 SafetyVerdict.to_dict
v = safety.check("rm -rf /")
d = v.to_dict()
check("safe" in d and "blocked_pattern" in d, "VERDICT_DICT",
      f"keys={list(d.keys())}")

# 1.13 Stats
stats = safety.get_stats()
check(stats["blocked"] >= 8 and stats["allowed"] >= 3, "SAFETY_STATS",
      f"blocked={stats['blocked']}, allowed={stats['allowed']}")

# 1.14 BLOCKED_PATTERNS count
check(len(BLOCKED_PATTERNS) >= 12, "PATTERN_COUNT",
      f"{len(BLOCKED_PATTERNS)} blocked patterns")


# ═══════════════════════════════════════════════════════════
#  §2. Config — параметры full модели (Phase 20)
# ═══════════════════════════════════════════════════════════

print("\n§2. Config — параметры единственной модели (Phase 20)")

from lina.config import config, LLMConfig, ModelProfile

# 2.1 Full: temperature=0.7
check(config.llm.full.temperature == 0.7, "FULL_TEMP",
      f"full temp={config.llm.full.temperature}")

# 2.2 Full: top_p=0.95
check(config.llm.full.top_p == 0.95, "FULL_TOP_P",
      f"full top_p={config.llm.full.top_p}")

# 2.3 Full: repeat_penalty=1.1
check(config.llm.full.repeat_penalty == 1.1, "FULL_REPEAT",
      f"full repeat_penalty={config.llm.full.repeat_penalty}")

# 2.4 Full: n_ctx=4096
check(config.llm.full.n_ctx == 4096, "FULL_CTX",
      f"full n_ctx={config.llm.full.n_ctx}")

# 2.5 Full: max_tokens=512
check(config.llm.full.max_tokens == 512, "FULL_TOKENS",
      f"full max_tokens={config.llm.full.max_tokens}")

# 2.6 No mini attribute — mini model removed
check(not hasattr(config.llm, 'mini'), "NO_MINI",
      "mini model removed from config")

# 2.7 No mini_system_prompt
check(not hasattr(config.llm, 'mini_system_prompt'), "NO_MINI_PROMPT",
      "mini_system_prompt removed")

# 2.8 system_prompt exists (full model prompt)
check(len(config.llm.system_prompt) > 0, "SYS_PROMPT",
      f"system_prompt exists, len={len(config.llm.system_prompt)}")

# 2.9 model_path property points to full model
check("full" in config.llm.model_path.lower(), "MODEL_PATH",
      f"model_path → full: {config.llm.model_path[-30:]}")

# 2.10 LLMConfig is Single Model (no 'hybrid' in docstring)
check("Single" in LLMConfig.__doc__ or "single" in LLMConfig.__doc__.lower(),
      "SINGLE_MODEL_DOC", "LLMConfig doc says Single")


# ═══════════════════════════════════════════════════════════
#  §3. Math direct eval
# ═══════════════════════════════════════════════════════════

print("\n§3. Math direct eval (deterministic — NOT removed)")

# Math eval is useful utility even in single model arch
# but it's no longer in the orchestrator pipeline
# Verify it was NOT moved but exists in old tests
# Instead test that orchestrator handles math via full model

def mock_gen(query, context, system_prompt):
    return f"Ответ: {query}"

orch_math = LinaOrchestrator(generate_fn=mock_gen)

# 3.1 Simple text query → full model
result = orch_math.process("2+2")
check(len(result.response) > 0, "MATH_QUERY",
      f"'2+2' → response={result.response[:30]}")

# 3.2 General query → full model
result = orch_math.process("Привет")
check(len(result.response) > 0, "GENERAL_QUERY",
      f"'Привет' → response={result.response[:30]}")

# 3.3 model_used always "full"
check(result.model_used == "full", "MODEL_ALWAYS_FULL",
      f"model_used={result.model_used}")


# ═══════════════════════════════════════════════════════════
#  §4. RAG Heuristic
# ═══════════════════════════════════════════════════════════

print("\n§4. RAG heuristic")

# 4.1 Keywords trigger RAG
check(_check_rag_heuristic("покажи документ"), "RAG_DOC",
      "документ → RAG needed")

# 4.2 база → RAG
check(_check_rag_heuristic("найди в базе"), "RAG_BASE",
      "база → RAG needed")

# 4.3 config → RAG
check(_check_rag_heuristic("покажи config"), "RAG_CONFIG",
      "config → RAG needed")

# 4.4 файл → RAG
check(_check_rag_heuristic("прочитай файл"), "RAG_FILE",
      "файл → RAG needed")

# 4.5 лог → RAG
check(_check_rag_heuristic("покажи лог"), "RAG_LOG",
      "лог → RAG needed")

# 4.6 Normal query → no RAG
check(not _check_rag_heuristic("привет, как дела?"), "NO_RAG_NORMAL",
      "plain greeting → no RAG")

# 4.7 Math → no RAG
check(not _check_rag_heuristic("2+2=?"), "NO_RAG_MATH",
      "math → no RAG")

# 4.8 RAG_KEYWORDS is non-empty
check(len(RAG_KEYWORDS) >= 5, "RAG_KW_COUNT",
      f"{len(RAG_KEYWORDS)} RAG keywords")

# 4.9 RAG_RELEVANCE_THRESHOLD
check(RAG_RELEVANCE_THRESHOLD > 0, "RAG_THRESHOLD",
      f"threshold={RAG_RELEVANCE_THRESHOLD}")


# ═══════════════════════════════════════════════════════════
#  ИТОГИ
# ═══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"  Phase 18 — Safety & Config Tests (Phase 20 adapted)")
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
