# Commander Unification Plan — Eliminating Split-Brain Architecture

**Status:** Planned — execute in a dedicated refactoring session.
**Estimated effort:** ~4 hours, ~325 net LOC reduction, 0 feature regression.
**Risk:** Medium — touches security paths, must validate with full test suite.

---

## Problem

Commander has 3 parallel query paths with different security coverage:

| Path | Security | RAG | Fact Verification |
|------|----------|-----|-------------------|
| `_handle_llm_query()` (legacy) | ✅ Full Phase 16 | Basic `_rag_layer` | ❌ None |
| `_handle_llm_query_v3()` | ❌ **Bypassed** | Multi-engine parallel | ✅ Full |
| `_handle_llm_query_legacy()` | ✅ (delegates to ^1) | Same as legacy | ❌ None |

**Critical gap:** PipelineV3 bypasses anomaly detection, injection graph, risk engine,
rate limiting, and prompt seal entirely.

---

## Target Architecture

```
Commander.process()
  ├── meta / system / chain / macro / builtin / mini-llm  [unchanged]
  └── _handle_llm_query(query)
         └── MainPipeline.process_request(query)  ← SINGLE PATH
              ├── step 0: Security pre-checks (anomaly + risk + injection)
              ├── steps 1–6: Intent → Priority → Plan → Integrity → Lock
              ├── step 7: Execution
              │     ├── intent=web_search → PipelineV3.run()
              │     ├── intent=tool → ToolEngine
              │     └── intent=general → LLM generate
              ├── steps 8–14: PostProcess → Guard → Validate → Degrade → Drift → Budget → Trace
              └── FinalResponse
```

---

## Step-by-Step Execution Plan

### Phase 1: Add security pre-steps to MainPipeline

**Files:** `core/main_pipeline.py`
**Changes:**
1. Import AnomalyDetector, InjectionGraph, RiskEngine from runtime_v2
2. Add `step_0_security_precheck()` that runs:
   - `AnomalyDetector.analyze(query)`
   - `RiskEngine.assess_query(query)`
   - `InjectionGraph.record_turn()` + `check_escalation()`
3. Wire into `_run_steps()` before step 1

### Phase 2: Add PipelineV3 as execution strategy

**Files:** `core/main_pipeline.py`, `core/execution_orchestrator.py`
**Changes:**
1. In step 7 (Execution), detect when intent = `web_search` / `product_spec` / factual
2. Delegate to `PipelineV3.run()` for those intents
3. Keep LLM direct generation for `chat` / `system_command` intents

### Phase 3: Thin out Commander

**Files:** `shell/commander.py`
**Changes:**
1. Replace `_handle_llm_query()` body (145 lines) with:
   ```python
   def _handle_llm_query(self, query: str) -> str:
       from lina.core.main_pipeline import get_pipeline
       pipeline = get_pipeline()
       result = pipeline.process_request(query, session_id=self._session_id)
       return result.text
   ```
2. Delete `_handle_llm_query_v3()` (60 lines)
3. Delete `_handle_llm_query_legacy()` (15 lines)
4. Remove Phase 16 subsystem instantiation from `__init__` (80 lines)

### Phase 4: Clean up GUI

**Files:** `gui/app.py`
**Changes:**
1. Remove `pipeline_version` temp-mutation pattern
2. `_handler()` / `_stream_handler()` delegate to MainPipeline (same as Commander)

### Phase 5: Remove pipeline_version config

**Files:** `config.py`, `core/runtime.py`, tests
**Changes:**
1. Remove `pipeline_version: str = "legacy"` from PipelineConfig
2. Remove `--pipeline-v3` CLI flag
3. Update 8 test assertions

---

## Validation

1. Run full test suite: `python -m pytest lina/tests/ -x`
2. Manual REPL test: `lina --verbose` → ask factual + chat + system queries
3. Verify security: check that anomaly detection fires for all query types
4. Performance: compare latency before/after (should be identical)

---

## Files Affected Summary

| File | Change | LOC delta |
|------|--------|-----------|
| `core/main_pipeline.py` | Add step 0 security + PipelineV3 integration | +30 |
| `shell/commander.py` | Thin 3 paths → 1 delegate | −215 |
| `gui/app.py` | Remove version branching | −50 |
| `config.py` | Remove pipeline_version | −5 |
| `core/runtime.py` | Remove --pipeline-v3 flag | −5 |
| tests (various) | Update assertions | −10 |
| **Net** | | **~−255** |
