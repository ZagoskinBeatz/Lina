# Dead Code Audit — Lina v1.0

**Generated:** auto-analysis of production import graph + deprecation markers.

---

## Summary

| Category | Files | Lines | Status |
|----------|-------|-------|--------|
| **runtime_v2/ (deprecated)** | 74 | 13,547 | Marked deprecated; still used by commander.py |
| **core/orchestrator.py** | 1 | ~250 | Zero production imports, test-only |
| **core/pipeline.py** | 1 | ~200 | Zero production imports, test-only |
| **safety/policy.py** | 1 | ~50 | Deprecated shim → governance.policy_engine |
| **Deprecated methods** | 5+ | ~150 | Marked deprecated, still in codebase |
| **Legacy REPL path** | lina.py | ~100 | `_legacy_route_governance()` |
| **Total removable** | | **~14,300** | |

---

## Details

### 1. runtime_v2/ — 13.5K lines, 74 files

**Deprecation notice:** `"lina.runtime_v2 is deprecated and frozen. Use lina.core.main_pipeline.MainPipeline"`

**Blocker:** `shell/commander.py` imports 19 modules from runtime_v2:
- `runtime_v2.api.facade.RuntimeAPI`
- `runtime_v2.security_v3.*` (risk_engine, injection, anomaly, prompt_seal)
- `runtime_v2.performance_v2.*` (complexity, routing)
- `runtime_v2.chaos.*` (fault_injector)
- `runtime_v2.observability.*` (trace_context, latency)
- `runtime_v2.system.*` (env_guard, error_taxonomy)

**Action:** After Commander unification (see COMMANDER_UNIFICATION_PLAN.md),
all 19 imports move into MainPipeline steps → runtime_v2/ can be deleted entirely.

### 2. core/orchestrator.py — ~250 lines

Zero production imports. Only referenced from:
- `integration_tests/test_phase9.py`
- `integration_tests/test_phase10.py`
- `tests/` (15 matches)

**Action:** Migrate tests to use `execution_orchestrator.py`, then delete.

### 3. core/pipeline.py — ~200 lines

Zero production imports. Only referenced from:
- `integration_tests/test_phase9.py` (8 matches)
- `integration_tests/test_phase10.py`
- `tests/test_engine_and_tools.py`

**Action:** Migrate tests to use `main_pipeline.py`, then delete.

### 4. safety/policy.py — ~50 lines

Entire module deprecated:
> `"lina.safety.policy is deprecated. Use lina.governance.policy_engine.PolicyEngine"`

**Action:** Check if any code still imports it; if not, delete.

### 5. Deprecated methods (still present)

| Method | File | Lines |
|--------|------|-------|
| `ContextBuilder.detect_intent()` | core/context.py | 118–129 |
| `ChatController._try_direct()` | gui/chat.py | 484–497 |
| `Commander._handle_system_command()` | shell/commander.py | ~966 |
| `_legacy_route_governance()` | lina.py | ~275 |
| `_handle_llm_query_legacy()` | shell/commander.py | ~1285 |
| RAG `_try_legacy_load()` / `_load_legacy()` | rag/*.py | various |

### 6. core/tools.py — 1,665 lines

Contains weather, exchange rate, and web search functions that overlap with:
- `core/web_search_engine.py` (1,509 lines) — same weather/exchange APIs
- `core/smart_workflows.py` (658 lines) — same weather workflow

**Action:** Audit for overlap; consolidate into web_search_engine.py.

---

## Removal Roadmap

| Phase | What | Lines freed | Prerequisite |
|-------|------|------------|-------------|
| 1 | Delete safety/policy.py | ~50 | None |
| 2 | Delete core/orchestrator.py, core/pipeline.py | ~450 | Migrate 17 test files |
| 3 | Commander unification | ~325 | COMMANDER_UNIFICATION_PLAN.md |
| 4 | Delete runtime_v2/ | ~13,500 | Phase 3 complete |
| 5 | Consolidate tools.py + web_search_engine.py | ~500 | Audit specifics |
| **Total** | | **~14,825** | |
