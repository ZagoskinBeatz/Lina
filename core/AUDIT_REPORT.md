# COMPREHENSIVE AUDIT — `lina/core/`

> Generated from full source code reading of **all 43 Python files** in `lina/core/`.
> Every class, function, method signature, constant, enum, and dataclass is documented.

---

## Table of Contents

1.  [`__init__.py`](#1-initpy)
2.  [`application_resolver.py`](#2-application_resolverpy)
3.  [`bootstrap.py`](#3-bootstrappy)
4.  [`budget_governor.py`](#4-budget_governorpy)
5.  [`capability_registry.py`](#5-capability_registrypy)
6.  [`cli.py`](#6-clipy)
7.  [`config_manager.py`](#7-config_managerpy)
8.  [`consistency_engine.py`](#8-consistency_enginepy)
9.  [`context_budget.py`](#9-context_budgetpy)
10. [`context.py`](#10-contextpy)
11. [`degradation.py`](#11-degradationpy)
12. [`drift_detector.py`](#12-drift_detectorpy)
13. [`envelope.py`](#13-envelopepy)
14. [`execution_orchestrator.py`](#14-execution_orchestratorpy)
15. [`execution_trace.py`](#15-execution_tracepy)
16. [`governance.py`](#16-governancepy)
17. [`human_response.py`](#17-human_responsepy)
18. [`i18n.py`](#18-i18npy)
19. [`integrity_checker.py`](#19-integrity_checkerpy)
20. [`intent_lock.py`](#20-intent_lockpy)
21. [`intent_router.py`](#21-intent_routerpy)
22. [`lifecycle.py`](#22-lifecyclepy)
23. [`main_pipeline.py`](#23-main_pipelinepy)
24. [`metrics.py`](#24-metricspy)
25. [`mode_control.py`](#25-mode_controlpy)
26. [`model_router.py`](#26-model_routerpy)
27. [`orchestrator.py`](#27-orchestratorpy)
28. [`output.py`](#28-outputpy)
29. [`pipeline_coordinator.py`](#29-pipeline_coordinatorpy)
30. [`pipeline.py`](#30-pipelinepy)
31. [`post_processor.py`](#31-post_processorpy)
32. [`priority_resolver.py`](#32-priority_resolverpy)
33. [`production_guard.py`](#33-production_guardpy)
34. [`prompts.py`](#34-promptspy)
35. [`repl.py`](#35-replpy)
36. [`response_validator.py`](#36-response_validatorpy)
37. [`runtime_state.py`](#37-runtime_statepy)
38. [`runtime.py`](#38-runtimepy)
39. [`semantic_drift.py`](#39-semantic_driftpy)
40. [`step_memory.py`](#40-step_memorypy)
41. [`system_control.py`](#41-system_controlpy)
42. [`system_interaction.py`](#42-system_interactionpy)
43. [`tool_engine.py`](#43-tool_enginepy)
44. [`tools.py`](#44-toolspy)
45. [`web_search_engine.py`](#45-web_search_enginepy)

---

## 1. `__init__.py`
**Lines:** ~20 | **Phase:** 11

### Purpose
Package initializer. Declares `__version__` and documents sub-module layout.

### Constants
| Name | Value |
|------|-------|
| `__version__` | `"1.1.0"` |

### Notes
Docstring lists core sub-modules: pipeline.py, context.py, model_router.py, runtime_state.py, output.py, bootstrap.py, cli.py, repl.py, runtime.py.

---

## 2. `application_resolver.py`
**Lines:** 1099 | **Phase:** 27

### Purpose
Universal application discovery, fuzzy-matching, launch, verification, and install-suggestion system. Scans `.desktop`, Flatpak, Snap, AppImage, and PATH sources. Provides Russian alias mapping for natural language app names.

### Dataclasses

| Dataclass | Fields | Key Methods |
|-----------|--------|-------------|
| `AppInfo` | `display_name`, `exec_command`, `source`, `desktop_file`, `package_name`, `keywords`, `icon`, `categories`, `wm_class` | `to_dict()` |
| `AppCandidate` | `app: AppInfo`, `confidence: float`, `match_reason: str` | — |
| `LaunchResult` | `success`, `message`, `pid`, `command`, `app_name`, `verified` | `to_dict()` |
| `InstallSuggestion` | `method`, `command`, `package_name`, `source`, `url` | — |

### Constants
| Name | Description |
|------|-------------|
| `_RU_ALIASES` | `Dict[str, List[str]]` — Russian→English app name mapping (хром→chrome, терминал→konsole, etc.) |

### Module-Level Functions
| Function | Signature | Description |
|----------|-----------|-------------|
| `_levenshtein` | `(s1: str, s2: str) -> int` | Edit distance |
| `_levenshtein_similarity` | `(s1: str, s2: str) -> float` | Normalized similarity 0.0–1.0 |
| `_token_similarity` | `(query: str, target: str) -> float` | Jaccard similarity on word tokens |
| `_normalized_contains` | `(query: str, target: str) -> float` | Containment score with normalization |
| `_get_desktop_dirs` | `() -> List[str]` | All XDG .desktop directories |
| `_parse_desktop_file` | `(filepath: str) -> Optional[AppInfo]` | Parse a single .desktop file |
| `_url_encode` | `(text: str) -> str` | URL-encode helper |
| `get_resolver` | `() -> ApplicationResolver` | **Singleton accessor** |

### Class: `ApplicationResolver`

| Constant | Value |
|----------|-------|
| `CACHE_TTL` | `300.0` (5 minutes) |
| `CONFIDENCE_THRESHOLD` | `0.5` |
| `VERIFY_DELAY` | `1.5` seconds |

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init empty index, history |
| `find_installed_apps` | `(force_refresh=False) -> List[AppInfo]` | Scan all sources, build cached index |
| `_scan_flatpak` | `() -> List[AppInfo]` | `flatpak list --app` scanning |
| `_scan_snap` | `() -> List[AppInfo]` | `snap list` scanning |
| `_scan_appimages` | `() -> List[AppInfo]` | Scan ~/Applications, ~/Downloads etc. |
| `match_app` | `(user_input, top_n=3) -> List[AppCandidate]` | Fuzzy search with multi-signal scoring |
| `_score_app` | `(query, search_terms, app) -> Tuple[float, str]` | Multi-signal confidence scorer |
| `_normalize_input` | `(text) -> str` | Strip prefixes like "открой", "запусти" |
| `_expand_aliases` | `(query) -> List[str]` | Expand via `_RU_ALIASES` |
| `resolve_launch_command` | `(app: AppInfo) -> str` | Clean exec command (strip %u etc.) |
| `launch` | `(user_input: str) -> LaunchResult` | Find → Launch → Verify full cycle |
| `_try_fallback_launch` | `(candidates, user_input) -> LaunchResult` | Try next candidate on failure |
| `verify_launch` | `(pid: int) -> bool` | Check /proc/{pid} alive |
| `_schedule_verify` | `(result: LaunchResult)` | Async PID check after VERIFY_DELAY |
| `suggest_installation` | `(app_name) -> List[InstallSuggestion]` | Search pacman/apt/dnf/flatpak/snap |
| `_search_package_manager` | `(query) -> Optional[InstallSuggestion]` | Detect distro → search PM |
| `_detect_distro` | `() -> str` | arch/debian/fedora/unknown |
| `_search_pacman` | `(query) -> Optional[InstallSuggestion]` | `pacman -Ss` |
| `_search_apt` | `(query) -> Optional[InstallSuggestion]` | `apt search` |
| `_search_dnf` | `(query) -> Optional[InstallSuggestion]` | `dnf search` |
| `_search_flatpak_remote` | `(query) -> Optional[InstallSuggestion]` | `flatpak search` |
| `_search_snap_store` | `(query) -> Optional[InstallSuggestion]` | `snap find` |
| `_format_not_found` | `(app_name, suggestions) -> str` | Human-readable "not found" message |
| `get_stats` | `() -> Dict[str, Any]` | Index size, age, launch count |

---

## 3. `bootstrap.py`
**Lines:** ~120 | **Phase:** —

### Purpose
Safe startup initialization: faulthandler, signal handlers, sys.path, logging.

### Functions
| Function | Signature | Description |
|----------|-----------|-------------|
| `enable_faulthandler` | `() -> None` | Enables faulthandler for segfault debugging |
| `setup_signal_handlers` | `(cleanup_fn: Optional[Callable] = None) -> None` | SIGINT/SIGTERM handlers |
| `safe_import` | `(module_path: str, fallback=None)` | Import with fallback on error |
| `bootstrap` | `() -> None` | Full environment init |

---

## 4. `budget_governor.py`
**Lines:** ~155 | **Phase:** 23

### Purpose
Session resource budget control. Tracks total tokens used, enforces session limits, auto-reduces max_tokens when average exceeds threshold.

### Class: `BudgetGovernor`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(*, session_budget=100_000, avg_threshold=400, window_size=20, tool_output_cap=300)` | Init with budget limits |
| `record_response` | `(tokens_prompt=0, tokens_generated=0) -> None` | Track token usage |
| `record_tool_output` | `(tokens: int) -> None` | Track tool output tokens |
| `session_tokens_used` | *property* → `int` | Total prompt + generated |
| `session_remaining` | *property* → `int` | Budget remaining |
| `avg_response_tokens` | *property* → `float` | Sliding window average |
| `peak_tokens` | *property* → `int` | Max single response |
| `is_budget_exhausted` | `() -> bool` | True if used >= budget |
| `recommended_max_tokens` | `(current_cap=512) -> int` | Auto-reduce if avg > threshold |
| `check_tool_output` | `(tokens: int) -> bool` | True if within tool_output_cap |
| `to_dict` | `() -> Dict` | Serialization |
| `get_stats` | `() -> Dict` | Stats for SystemControl |

---

## 5. `capability_registry.py`
**Lines:** 264 | **Phase:** 24

### Purpose
Tracks system capabilities (llm, tool, rag, cv, web, etc.) with ACTIVE/DISABLED/BLOCKED states.

### Enum: `CapabilityStatus`
`ACTIVE`, `DISABLED`, `BLOCKED`

### Dataclass: `CapabilityInfo`
Fields: `name`, `status`, `description`, `disabled_reason`, `blocked_reason`, `blocked_at`
Method: `to_dict()`

### Class: `CapabilityRegistry`

| Constant | Value |
|----------|-------|
| `DEFAULT_CAPABILITIES` | llm, tool, rag, cv, web, app_launcher, file, system, chain, macro |

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(register_defaults=True)` | Register default capabilities |
| `register` | `(name, description="") -> None` | Add new capability |
| `disable` | `(name, reason="") -> bool` | Set to DISABLED |
| `block` | `(name, reason="") -> bool` | Set to BLOCKED (stronger) |
| `enable` | `(name) -> bool` | Restore from DISABLED |
| `unblock` | `(name) -> bool` | Restore from BLOCKED |
| `is_available` | `(name) -> bool` | True only if ACTIVE |
| `get_active` | `() -> List[str]` | All ACTIVE names |
| `get_disabled` | `() -> List[str]` | All DISABLED names |
| `get_blocked` | `() -> List[str]` | All BLOCKED names |
| `get_all` | `() -> Dict[str, Dict]` | Full registry status |
| `get_history` | `() -> List[Dict]` | Change log |
| `get_stats` | `() -> Dict[str, Any]` | Stats for SystemControl |

---

## 6. `cli.py`
**Lines:** 485 | **Phase:** —

### Purpose
CLI argument parsing, logging setup, pipeline creation with LLM/RAG/Tool wiring, and main entry point (`main()`).

### Dataclass: `LinaArgs`
Fields: `verbose`, `index`, `model`, `knowledge_dir`, `web`, `port`, `notify`, `preinstall`, `cv`, `oneshot`, `quiet`, `trace`, `trace_json`, `chaos`, `adaptive_routing`, `agent_v3`, `secure_shell`, `profile`

### Constants
| Name | Description |
|------|-------------|
| `BANNER` | ASCII art banner |

### Functions
| Function | Signature | Description |
|----------|-----------|-------------|
| `build_parser` | `() -> argparse.ArgumentParser` | Full CLI parser with all flags |
| `parse_args` | `(argv=None) -> LinaArgs` | Parse and return LinaArgs |
| `_setup_logging` | `(verbose=False) -> None` | Configure logging level |
| `_create_pipeline` | `() -> MainPipeline` | Create MainPipeline, wire LLM/RAG/Commander executors, attach QueryPreprocessor |
| `main` | `(argv=None) -> int` | **Entry point**: --oneshot, --preinstall, --index, --web, or interactive REPL with fast-path preprocessing |

### Interconnections
- Creates `MainPipeline`, `SystemSnapshot`, `QueryPreprocessor`, `ActionExecutor`
- Wires `LLM engine` (from `lina.inference`), `RAG searcher` (from `lina.rag`), `Commander` (from `lina.shell`)
- Fast-path: `QueryPreprocessor.try_direct_answer()` bypasses full pipeline for greetings, system info queries, brightness/volume

---

## 7. `config_manager.py`
**Lines:** ~200 | **Phase:** 22

### Purpose
Centralized configuration with defaults, runtime overrides, and persistent JSON storage.

### Dataclass: `LinaConfig`
| Field | Default |
|-------|---------|
| `max_history_messages` | 20 |
| `max_rag_tokens` | 500 |
| `max_tool_output_tokens` | 300 |
| `router_confidence_threshold` | 0.5 |
| `llm_max_tokens_cap` | 512 |
| `safe_mode` | False |
| `debug_mode` | False |
| `auto_regenerate` | True |
| `strict_validation` | False |

Methods: `to_dict()`, `_validate()` (range assertions)

### Class: `ConfigManager`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(config_path=None)` | Load from JSON or defaults |
| `get` | `(key, default=None) -> Any` | Priority: override → persistent → default |
| `set` | `(key, value, persist=False) -> bool` | Set override or persistent value |
| `reset` | `(key=None) -> None` | Reset one or all overrides |
| `get_all` | `() -> Dict` | Merged config dict |
| `get_overrides` | `() -> Dict` | Only overrides |
| `_load` | `() -> None` | Load from disk |
| `_save` / `save` | `() -> None / bool` | Persist to disk |

---

## 8. `consistency_engine.py`
**Lines:** 309 | **Phase:** 25

### Purpose
Logical/semantic consistency checking between planned and actual execution. Checks intent match, path match, entity overlap, strategy, and fingerprint.

### Dataclass: `ConsistencyResult`
Fields: `consistency_score`, `drift_detected`, `drift_type`, `reason`, `regeneration_reason`, `passed`, `checks_performed`
Methods: `to_dict()`, `for_trace()`

### Class: `ConsistencyEngine`

| Constant | Value |
|----------|-------|
| `PASS_THRESHOLD` | 0.5 |
| `DRIFT_ENTITY_MIN_OVERLAP` | 0.3 |
| `_INTENT_MARKERS` | Dict mapping intents to expected response markers |

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(*, pass_threshold=0.5)` | Init with threshold |
| `check` | `(*, intent, actual_path, planned_path, response_text, prev_entities, curr_entities, prev_strategy, curr_strategy, prev_fingerprint, curr_fingerprint) -> ConsistencyResult` | Full consistency check |
| `check_response_not_empty` | `(response) -> float` | Score: 0.0 if empty, 1.0 if content |
| `_check_intent_match` | `(intent, response) -> float` | Check response contains intent markers |
| `average_score` | `() -> float` | Historical average |
| `stability_rating` | `() -> str` | stable/degraded/unstable |
| `get_stats` | `() -> Dict` | Stats for SystemControl |

---

## 9. `context_budget.py`
**Lines:** 520 | **Phase:** 20.2

### Purpose
Guarantees `prompt_tokens + max_tokens <= n_ctx`. Multi-stage trimming: RAG → history → system prompt → user input. Hard enforcement with assertion at end.

### Constants
| Name | Value |
|------|-------|
| `MAX_HISTORY_TOKENS` | 1500 |
| `MAX_RAG_TOKENS` | 1000 |
| `SYSTEM_PROMPT_LIMIT` | 600 |
| `SAFETY_MARGIN` | 64 |
| `MIN_GENERATION_THRESHOLD` | 64 |
| `CHARS_PER_TOKEN` | 2.2 |

### Dataclass: `BudgetResult`
Fields: `prompt`, `max_tokens`, `prompt_tokens`, `total_budget`, `n_ctx`, `history_trimmed`, `rag_trimmed`, `history_entries_kept`, `rag_tokens_original`, `rag_tokens_final`
Property: `fits -> bool`

### Class: `HeuristicTokenizer`
Fallback tokenizer when LLM tokenizer unavailable.
Method: `tokenize(data) -> list`

### Class: `ContextBudgetManager`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(llm=None, n_ctx=4096, max_history_tokens, max_rag_tokens, system_prompt_limit)` | Init with context window size |
| `count` | `(text) -> int` | Token counting (LLM tokenizer or heuristic) |
| `build_prompt` | `(system_prompt, history, rag_context, user_input, max_tokens=256) -> Tuple[str, int]` | Build prompt with trimming, return (prompt, max_tokens) |
| `build_prompt_detailed` | `(system_prompt, history, rag_context, user_input, max_tokens=256) -> BudgetResult` | Same but returns full BudgetResult |
| `_assemble` | `(system_prompt, history, rag_context, user_input) -> str` | Assemble prompt blocks |
| `_trim_text_to_tokens` | `(text, max_tokens) -> str` | Binary search trim |
| `_trim_text_by_ratio` | `(text, ratio) -> str` | Trim to fraction |
| `_trim_history` | `(history, max_tokens) -> List[str]` | Drop oldest entries |

### Trimming Strategy
1. Trim RAG (50% → 25% → 0%)
2. Trim history (oldest first)
3. Trim system prompt
4. Trim user input (last resort)
5. **Hard enforcement** via `assert total_budget <= n_ctx`

---

## 10. `context.py`
**Lines:** ~250 | **Phase:** 9

### Purpose
Context builder for pipeline: intent detection via regex, RAG enrichment, runtime context.

### Constants
Regex patterns: `_META_PATTERN`, `_COMMAND_PATTERN`, `_CHAIN_PATTERN`, `_CV_PATTERNS`, `_PLANNING_PATTERNS`, `_RAG_PATTERNS`

### Class: `ContextBuilder`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(rag_fn=None, runtime_fn=None, max_rag_chars=2000)` | Init with callbacks |
| `detect_intent` | `(raw_input) -> IntentType` | Regex-based: meta → command → chain → cv → rag → planning → question |
| `build` | `(ctx: RequestContext) -> RequestContext` | Enrich context with intent, RAG, runtime |
| `_get_rag_context` | `(query) -> str` | Call RAG callback |
| `_get_runtime_context` | `() -> str` | Call runtime callback |
| `detect_intent_batch` | `(inputs) -> List[IntentType]` | Batch intent detection |

---

## 11. `degradation.py`
**Lines:** ~260 | **Phase:** 23

### Purpose
Automatic stabilization during degradation. Tracks failure streaks by category and recommends actions.

### Enum: `ActionType`
`NONE`, `ENABLE_STRICT`, `DISABLE_TOOL`, `RELOAD_MODEL`, `ENABLE_SAFE_MODE`, `REDUCE_TOKENS`, `RESET_COUNTERS`

### Dataclasses
| Dataclass | Fields |
|-----------|--------|
| `DegradationAction` | `action`, `reason`, `details`, `severity`, `timestamp` |
| `FailureRecord` | `category`, `timestamp`, `detail` |

### Class: `DegradationStrategy`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(*, validation_threshold=2, tool_threshold=3, llm_threshold=3, general_threshold=5)` | Thresholds |
| `record_failure` | `(category, detail="") -> None` | Push failure record |
| `record_success` | `() -> None` | Reset current streak |
| `evaluate` | `() -> DegradationAction` | Check streaks: 5 general→safe_mode, 3 llm→reload, 3 tool→disable, 2 validation→strict |
| `_compute_streaks` | `() -> Dict[str, int]` | Count consecutive failures by category |
| `clear` | `()` | Reset all |
| `get_disabled_tools` | `()` | List of disabled tools |
| `get_actions_history` | `()` | Action log |
| `get_stats` | `()` | Stats for SystemControl |

---

## 12. `drift_detector.py`
**Lines:** 211 | **Phase:** 23

### Purpose
Detects state drift: system prompt hash change, model version change, config silent overrides, unexpected intents.

### Dataclass: `DriftEvent`
Fields: `category`, `description`, `old_value`, `new_value`, `severity`

### Class: `StateDriftDetector`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init with empty baseline |
| `set_baseline` | `(*, system_prompt_hash, model_version, config_snapshot, known_intents) -> None` | Set reference state |
| `check` | `(*, current_prompt_hash, current_model, current_config, current_intents) -> List[DriftEvent]` | Compare current vs baseline |
| `get_events` | `(limit=10)` | Recent events |
| `format_events` | `(limit=5)` | Formatted string |
| `clear` | `()` | Reset |
| `get_stats` | `()` | Stats |

---

## 13. `envelope.py`
**Lines:** ~190 | **Phase:** 26

### Purpose
Request/Response envelope wrappers for full traceability.

### Dataclasses
| Dataclass | Key Fields | Key Methods |
|-----------|------------|-------------|
| `RequestEnvelope` | `request_id`, `user_input`, `timestamp`, `session_id`, `metadata` | `input_hash()`, `to_dict()` |
| `StageRecord` | `name`, `started_at`, `ended_at`, `status`, `detail` | property `duration_ms` |
| `ResponseEnvelope` | `request_id`, `response_text`, `intent`, `confidence`, `execution_path`, `plan_hash`, `priority_level`, `validation_score`, `consistency_score`, `drift_detected`, `regeneration_attempts`, `final_status`, `blocked`, `blocked_reason`, `tokens_prompt`, `tokens_generated`, `stages`, `errors`, `started_at`, `ended_at` | `add_stage()`, `finalize()`, `to_dict()`, `summary()` |

---

## 14. `execution_orchestrator.py`
**Lines:** 474 | **Phase:** 24

### Purpose
Strategic execution decisions. Router classifies intent; Orchestrator decides HOW to execute (path, fallback, regeneration, validation, tokens).

### Dataclasses
| Dataclass | Fields |
|-----------|--------|
| `ExecutionStep` | `step_number`, `path`, `requires_guard`, `requires_validation`, `description` |
| `ExecutionPlan` | `primary_path`, `fallback_path`, `regeneration_allowed`, `tool_allowed`, `max_tokens_override`, `validation_policy`, `priority_level`, `plan_hash`, `steps`, `intent`, `confidence`, `metadata` |

`ExecutionPlan` methods: `_compute_hash() -> str` (deterministic MD5), `is_multi_step() -> bool`

### Constants (Mappings)
| Name | Description |
|------|-------------|
| `_INTENT_PATH_MAP` | Intent → primary execution path |
| `_FALLBACK_MAP` | Intent → fallback path |
| `_INTENT_PRIORITY` | Intent → priority level |
| `_INTENT_VALIDATION` | Intent → validation policy |
| `_INTENT_REGEN` | Intent → regeneration policy |

### Class: `ExecutionOrchestrator`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init counters |
| `create_plan` | `(intent, confidence, *, runtime_state, capability_info, mode_profile, config, priority_level, trace_context) -> ExecutionPlan` | Single-step plan |
| `create_multi_step_plan` | `(step_definitions, *, intent, confidence, runtime_state, capability_info, mode_profile, priority_level) -> ExecutionPlan` | Multi-step plan |
| `verify_determinism` | `(plan_a, plan_b) -> bool` | Check hash equality |
| `get_stats` | `() -> Dict` | Plans created, multi/single counts |

---

## 15. `execution_trace.py`
**Lines:** ~190 | **Phase:** 23

### Purpose
Ring-buffer execution trace for diagnostics. Records each request with timing, tokens, status.

### Dataclass: `TraceEntry`
Fields: `trace_id`, `timestamp`, `intent`, `confidence`, `execution_path`, `tokens_prompt`, `tokens_generated`, `validation_score`, `regeneration_attempts`, `final_status`, `duration_ms`, `user_input`, `error`
Methods: `to_dict()`, `format()`

### Class: `ExecutionTracer`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(max_entries=50)` | Ring buffer size |
| `start` | `(intent, confidence, execution_path, user_input="") -> TraceEntry` | Begin tracing |
| `complete` | `(entry, *, tokens_prompt, tokens_generated, validation_score, regeneration_attempts, final_status, error) -> None` | Finalize entry |
| `get_recent` | `(limit=5)` | Last N entries |
| `format_recent` | `(limit=5)` | Formatted string |
| `get_stats` | `()` | Total, success, failure, avg tokens |
| `get_failure_streak` | `() -> int` | Consecutive failures from tail |

---

## 16. `governance.py`
**Lines:** ~200 | **Phase:** 23

### Purpose
Runtime State Manager — single source of truth for global mutable state with listener notification.

### Dataclass: `StateSnapshot`
Fields: `timestamp`, `active_model`, `current_profile`, `safe_mode`, `tool_mode`, `rag_enabled`, `cv_enabled`, `last_intent`, `last_execution_path`, `consecutive_failures`, `regeneration_count`, `mode`

### Class: `RuntimeStateManager`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init default state dict |
| `get` | `(key, default=None) -> Any` | Read state |
| `set` | `(key, value) -> bool` | Write state + notify listeners |
| `increment` | `(key, delta=1) -> int` | Atomic increment |
| `reset_counter` | `(key) -> None` | Reset to 0 |
| `snapshot` | `() -> StateSnapshot` | Immutable snapshot |
| `register_listener` | `(callback) -> None` | Register change callback |
| `_notify` | `(key, old, new)` | Notify all listeners |
| `to_dict` | `()` | Full state |
| `get_stats` | `()` | Stats for SystemControl |

---

## 17. `human_response.py`
**Lines:** 296 | **Phase:** 19

### Purpose
Sanitizes LLM output: strips internal leakage patterns, tech prefixes, JSON blobs, whitespace. Blocks responses that expose internal state.

### Constants
| Name | Description |
|------|-------------|
| `_LEAKAGE_PATTERNS` | 8 regex patterns for internal data |
| `_TECH_PREFIXES` | 6 regex for debug/system prefixes |
| `SAFE_FALLBACK_RESPONSE` | Default safe response text |
| `_MIN_USEFUL_LENGTH` | 1 |
| `_FULL_JSON_RE` | Match full JSON objects |

### Dataclass: `SanitizeResult`
Fields: `text`, `was_sanitized`, `leakage_detected`, `fallback_used`, `original_length`, `issues`

### Class: `HumanResponseLayer`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(fallback_fn=None)` | Optional regeneration callback |
| `sanitize` | `(response, query="") -> SanitizeResult` | Full sanitization pipeline |
| `_is_leakage` | `(text) -> bool` | Check for internal data patterns |
| `_do_fallback` | `(query, issues) -> str` | Fallback generation (recursive check) |
| `get_stats` | `() -> dict` | clean/sanitized/fallback counts |

---

## 18. `i18n.py`
**Lines:** 335 | **Phase:** X3

### Purpose
Internationalization: Russian (primary) + English translations.

### Constants
| Name | Description |
|------|-------------|
| `TRANSLATIONS` | `Dict[str, Dict[str, str]]` — translations for "ru" and "en" covering app, ui, chat, tray, settings, voice, diagnostics, wizard, errors, system |

### Class: `I18n`

| Constant | Value |
|----------|-------|
| `SUPPORTED_LANGUAGES` | `["ru", "en"]` |
| `DEFAULT_LANGUAGE` | `"ru"` |

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(language="ru")` | Init language |
| `language` | *property/setter* | Get/set current language |
| `t` | `(key, **kwargs) -> str` | Translate key with substitutions |
| `add_override` | `(key, value)` | Custom translation |
| `remove_override` | `(key)` | Remove custom translation |
| `has_key` | `(key) -> bool` | Check key exists |
| `get_all_keys` | `() -> list` | All keys for current language |
| `get_keys_by_prefix` | `(prefix) -> Dict[str, str]` | Filter by prefix |
| `get_missing_keys` | `() -> list` | Keys in ru but not in current |
| `get_supported_languages` | `() -> list` | Supported list |
| `load_language_pack` | `(lang, translations)` | Add custom language |
| `to_dict` | `() -> Dict` | Serialization |

### Singletons
| Function | Description |
|----------|-------------|
| `get_i18n(language="ru")` | Global singleton |
| `reset_i18n()` | Reset singleton |

---

## 19. `integrity_checker.py`
**Lines:** ~250 | **Phase:** 24

### Purpose
Verifies execution integrity: planned path vs actual path, plan hash match, multi-step sequence verification.

### Dataclass: `IntegrityResult`
Fields: `passed`, `planned_path`, `actual_path`, `severity`, `message`, `recommend_safe_mode`

### Class: `IntegrityChecker`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init violation log |
| `check` | `(planned_path, actual_path, *, plan_hash="", expected_hash="") -> IntegrityResult` | Check path + hash |
| `check_step_sequence` | `(planned_steps, actual_steps) -> IntegrityResult` | Multi-step verification |
| `get_violations` | `()` | All violations |
| `clear` | `()` | Reset |
| `get_stats` | `()` | Checks/violations/passed counts |

---

## 20. `intent_lock.py`
**Lines:** ~195 | **Phase:** 25

### Purpose
Locks intent after plan is approved. Prevents intent mutation during execution.

### Dataclasses
| Dataclass | Fields |
|-----------|--------|
| `LockState` | `locked`, `intent`, `plan_hash`, `locked_at`, `lock_reason` |
| `LockViolation` | `original_intent`, `attempted_intent`, `reason`, `severity` |

### Class: `IntentLock`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init unlocked |
| `lock` | `(intent, plan_hash="", reason="plan approved")` | Lock intent |
| `unlock` | `(reason="execution complete")` | Release lock |
| `is_locked` | `() -> bool` | Check lock state |
| `get_locked_intent` | `() -> str` | Locked intent name |
| `get_plan_hash` | `() -> str` | Plan hash |
| `validate` | `(attempted_intent) -> Optional[LockViolation]` | Check for violation |
| `get_state` | `()` | Current state |
| `get_violations` | `()` | All violations |
| `clear` | `()` | Reset |
| `get_stats` | `()` | Lock/violation counts |

---

## 21. `intent_router.py`
**Lines:** 455 | **Phase:** 22

### Purpose
Regex-based intent classification. Priority-ordered pattern matching for 16 intent types.

### Enum: `Intent`
`CHAT`, `MATH`, `SYSTEM_COMMAND`, `FILE_OPERATION`, `WEB`, `WEB_SEARCH`, `WEATHER_QUERY`, `INSTALL_APPLICATION`, `RAG`, `CV`, `TOOL_EXPLICIT`, `META`, `CHAIN`, `MACRO`, `OPEN_APPLICATION`, `SYSTEM_DIAGNOSTIC`

### Dataclass: `RoutingDecision`
Fields: `intent`, `confidence`, `reason`, `alternatives`, `metadata`

### Regex Pattern Constants
`_META_PATTERN`, `_SYSTEM_CMD_PATTERN`, `_CHAIN_PATTERN`, `_CV_PATTERNS`, `_RAG_PATTERNS`, `_FILE_PATTERNS`, `_LLM_TOOL_PATTERNS`, `_SYSTEM_INFO_PATTERNS`, `_WEATHER_PATTERNS`, `_INSTALL_PATTERNS`, `_WEB_SEARCH_PATTERNS`, `_WEB_PATTERNS`, `_MACRO_PATTERNS`, `_APP_LAUNCH_PATTERNS`, `_DIAGNOSTIC_PATTERNS`, `_MATH_PATTERN`

### Class: `IntentRouter`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(confidence_threshold=0.5)` | Init with threshold |
| `route` | `(user_input) -> RoutingDecision` | Priority chain: meta→system→chain→macro→CV→RAG→file→app→diagnostic→weather→install→web_search→web→tool→system_info→math→chat |
| `_classify` | `(text) -> RoutingDecision` | Internal classification logic |
| `get_stats` | `() -> Dict[str, int]` | Per-intent counters |
| `reset_stats` | `() -> None` | Reset counters |

### Classification Priority Order
1. META (!/system commands)
2. SYSTEM_COMMAND (starts with `!`)
3. CHAIN (chain separators)
4. MACRO
5. CV
6. RAG
7. FILE_OPERATION
8. OPEN_APPLICATION (with app_name extraction)
9. SYSTEM_DIAGNOSTIC
10. WEATHER_QUERY
11. INSTALL_APPLICATION
12. WEB_SEARCH
13. WEB (legacy)
14. TOOL_EXPLICIT
15. SYSTEM_COMMAND (info patterns)
16. MATH
17. CHAT (default, confidence 0.6)

---

## 22. `lifecycle.py`
**Lines:** ~250 | **Phase:** 26

### Purpose
Request lifecycle management. Defines the 10 pipeline stages and runs handlers sequentially.

### Enum: `PipelineStage`
`INIT`, `ROUTE`, `PLAN`, `LOCK`, `EXECUTE`, `VALIDATE`, `CONSISTENCY`, `GUARD`, `TRACE`, `COMPLETE`

### Constants
| Name | Value |
|------|-------|
| `STAGE_ORDER` | 10-element ordered list of PipelineStage |

### Dataclass: `StageResult`
Fields: `stage`, `status`, `duration_ms`, `data`, `error`

### Type Alias
`StageHandler = Callable[[Dict[str, Any]], StageResult]`

### Class: `LifecycleManager`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init empty handlers |
| `register` | `(stage, handler)` | Register stage handler |
| `run` | `(context) -> List[StageResult]` | Execute all 10 stages sequentially |
| `get_registered_stages` | `()` | List registered stages |
| `get_stage_order` | `()` | Full stage order |
| `get_stats` | `()` | Registered/total counts |

---

## 23. `main_pipeline.py`
**Lines:** 929 | **Phase:** Block A (Master)

### Purpose
**THE single entry point** for ALL request processing. Orchestrates 21+ modules through a 14-step pipeline.

### Constants
| Name | Value |
|------|-------|
| `SAFE_FALLBACK_RESPONSE` | Default safe response |

### Dataclasses
| Dataclass | Key Fields |
|-----------|------------|
| `FinalResponse` | `text`, `status` (success\|blocked\|error\|degraded), `source` (system\|llm\|tool\|fallback) |
| `PipelineContext` | ~40 fields: `user_input`, `intent`, `confidence`, `priority_level`, `plan`, `plan_hash`, `primary_path`, `fallback_path`, `execution_path`, `raw_response`, `cleaned_response`, `validation_score`, `consistency_score`, `drift_detected`, `regeneration_attempts`, `final_status`, `locked`, `guard_passed`, `errors`, `stage_timings`, etc. |

### Type Alias
`ExecutorCallback = Callable[["PipelineContext"], str]`

### Class: `MainPipeline`

**Constructor** instantiates ALL modules:
- Phase 22: `IntentRouter`, `PostProcessor`, `ResponseValidator`, `ConfigManager`, `SystemControl`, `ToolEngine`
- Phase 23: `RuntimeStateManager`, `ExecutionTracer`, `DegradationStrategy`, `ModeController`, `BudgetGovernor`, `StateDriftDetector`, `ProductionGuard`
- Phase 24: `ExecutionOrchestrator`, `CapabilityRegistry`, `PriorityResolver`, `IntegrityChecker`
- Phase 25: `ConsistencyEngine`, `StepMemory`, `SemanticDriftDetector`, `IntentLock`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(pipeline_config=None)` | Create & wire all 21+ modules |
| `set_llm_executor` | `(fn)` | Register LLM callback |
| `set_tool_executor` | `(fn)` | Register tool callback |
| `set_rag_executor` | `(fn)` | Register RAG callback |
| `process_request` | `(user_input) -> FinalResponse` | **THE entry point** — 14-step pipeline |
| `_handle_system_command` | `(ctx) -> FinalResponse` | /system commands bypass pipeline |
| `_step_01_runtime_snapshot` | `(ctx)` | Collect runtime state |
| `_step_02_intent_classification` | `(ctx)` | Route intent |
| `_step_03_priority_resolution` | `(ctx)` | Resolve priority |
| `_step_04_execution_planning` | `(ctx)` | Create execution plan |
| `_step_05_integrity_precheck` | `(ctx)` | Verify plan integrity |
| `_step_06_intent_lock` | `(ctx)` | Lock intent |
| `_step_07_execution` | `(ctx)` | Execute (LLM/TOOL/RAG) |
| `_step_08_post_processing` | `(ctx)` | Clean response |
| `_step_09_production_guard` | `(ctx)` | Final safety filter |
| `_step_10_response_validation` | `(ctx)` | Validate quality |
| `_step_11_degradation_handling` | `(ctx)` | Handle failures, trigger regeneration |
| `_step_12_drift_detection` | `(ctx)` | Consistency + semantic + state drift |
| `_step_13_budget_update` | `(ctx)` | Update token budget |
| `_step_14_trace_finalization` | `(ctx)` | Write trace entry |
| `_wire_system_control` | `()` | Register all providers for /system commands |
| `_get_full_status` | `() -> Dict` | Full system status |
| `get_stats` | `() -> Dict` | Pipeline stats |
| `get_module` | `(name) -> Any` | Access module by name (for testing) |

### Module-Level Verification Functions
| Function | Description |
|----------|-------------|
| `verify_pipeline_order() -> bool` | Check all 14 step methods exist |
| `verify_single_entry_point() -> bool` | Confirm `process_request` is only public processing method |
| `verify_all_modules_isolated() -> bool` | Confirm exactly 14 `_step_*` methods |

### 14-Step Pipeline Flow
```
1. Runtime Snapshot → 2. Intent Classification → 3. Priority Resolution →
4. Execution Planning → 5. Integrity Pre-check → 6. Intent Lock →
7. Execution → 8. Post-Processing → 9. Production Guard →
10. Response Validation → 11. Degradation Handling → 12. Drift Detection →
13. Budget Update → 14. Trace Finalization
```

---

## 24. `metrics.py`
**Lines:** ~350 | **Phase:** X4

### Purpose
Extended metrics and monitoring. Tracks query performance, source distribution, resource usage.

### Enum-like: `ResponseSource`
`RAG`, `LLM`, `DIAGNOSTIC`, `SYSTEM`, `CACHE`, `UNKNOWN`

### Dataclasses
| Dataclass | Fields |
|-----------|--------|
| `MetricEntry` | `timestamp`, `query`, `source`, `response_time_ms`, `tokens_in`, `tokens_out`, `success`, `error` |
| `ResourceSnapshot` | `timestamp`, `ram_mb`, `cpu_percent`, `model_loaded`, `uptime_seconds` |

### Class: `LinaMetrics`

| Constant | Value |
|----------|-------|
| `MAX_HISTORY` | 10000 |

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init deques |
| `start_query` | `(query)` | Begin timing |
| `end_query` | `(source, success, error, tokens_in, tokens_out) -> MetricEntry` | End timing |
| `record_query` | `(query, source, response_time_ms, ...) -> MetricEntry` | Direct record |
| `record_resource_snapshot` | `(ram_mb, cpu_percent, model_loaded) -> ResourceSnapshot` | System metrics |
| `get_current_resources` | `() -> Dict` | Read /proc/self/status |
| `get_average_response_time` | `() -> float` | Average ms |
| `get_response_time_percentile` | `(percentile=95) -> float` | P95 etc. |
| `get_source_distribution` | `() -> Dict` | Count by source |
| `get_source_percentages` | `() -> Dict` | Percentage by source |
| `get_top_queries` | `(n=10)` | Most frequent queries |
| `get_error_rate` | `() -> float` | Error percentage |
| `get_total_queries` / `get_success_count` / `get_error_count` | `() -> int` | Counts |
| `get_top_errors` | `(n=5)` | Most common errors |
| `get_noanswer_rate` | `() -> float` | No-answer rate |
| `get_summary` / `get_summary_text` | `() -> Dict / str` | Full summary |
| `clear` | `()` | Reset all |
| `to_dict` | `()` | Serialization |

### Singletons
`get_metrics()`, `reset_metrics()`

---

## 25. `mode_control.py`
**Lines:** ~260 | **Phase:** 23

### Purpose
Operating mode control: NORMAL, STRICT, SAFE, DIAGNOSTIC, MINIMAL with predefined profiles.

### Enum: `OperatingMode`
`NORMAL`, `STRICT`, `SAFE`, `DIAGNOSTIC`, `MINIMAL`

### Dataclass: `ModeProfile`
Fields: `router_threshold`, `tool_execution`, `rag_limit`, `regeneration_policy`, `max_tokens_cap`, `strict_validation`, `strict_post_processing`, `debug_output`, `description`

### Constant: `MODE_PROFILES`
`Dict[OperatingMode, ModeProfile]` — 5 predefined profiles with different settings.

### Class: `ModeController`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(initial=OperatingMode.NORMAL)` | Start in NORMAL |
| `mode` | *property* | Current mode |
| `switch` | `(mode, reason="") -> ModeProfile` | Switch mode |
| `switch_by_name` | `(name, reason="") -> Optional[ModeProfile]` | Switch by string name |
| `get_profile` | `()` | Current profile |
| `get_all_modes` | `()` | All available modes |
| `get_history` | `()` | Mode change history |
| `get_stats` | `()` | Stats |

---

## 26. `model_router.py`
**Lines:** ~115 | **Phase:** 20.1

### Purpose
Model selection router. Single Heavy Model architecture — always returns "full".

### Type Alias
`ModelTier = Literal["full"]`

### Class: `ModelRouter`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(full_keywords=None, full_available=True, **kwargs)` | Init |
| `route` | `(ctx, force_tier=None) -> str` | ALWAYS returns "full" |
| `update_availability` | `(full=None)` | Update availability |
| `get_stats` | `()` | Stats |
| `format_status` | `()` | Status string |

---

## 27. `orchestrator.py`
**Lines:** ~380 | **Phase:** 20

### Purpose
Main orchestrator for Single Heavy Model Architecture. Handles RAG heuristic, safety, generation, response sanitization.

### Constants
| Name | Description |
|------|-------------|
| `RAG_KEYWORDS` | Set of RAG-triggering keywords |
| `RAG_RELEVANCE_THRESHOLD` | 0.05 |
| `BLOCKED_PATTERNS` | 12 regex patterns |

### Functions
| Function | Signature | Description |
|----------|-----------|-------------|
| `_check_rag_heuristic` | `(query) -> bool` | Should RAG be used? |

### Dataclasses
| Dataclass | Fields |
|-----------|--------|
| `SafetyVerdict` | `safe`, `blocked_pattern`, `plan`, `query` |
| `OrchestratorResult` | `response`, `rag_used`, `model_used`, `is_fallback`, `elapsed`, `metadata` |

### Class: `ToolSafetyLayer`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(extra_patterns)` | Compile blocked patterns |
| `check` | `(query) -> SafetyVerdict` | Check query safety |
| `get_stats` | `()` | Blocked/checked counts |

### Class: `LinaOrchestrator`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(generate_fn, rag_fn, tool_execute_fn)` | Init with callbacks + ToolSafetyLayer + ContextBudgetManager + HumanResponseLayer |
| `process` | `(query) -> OrchestratorResult` | RAG heuristic → generate → sanitize |
| `_generate` | `(query, context="") -> str` | Build prompt + call LLM |
| `_safe_generate` | `(query) -> str` | Generate with safety check |
| `_regenerate_once` | `(query) -> str` | Fallback regeneration |
| `get_stats` | `()` | Stats |
| `format_status` | `()` | Status string |

---

## 28. `output.py`
**Lines:** ~295 | **Phase:** 11

### Purpose
Output isolation — safe printing for TTY, PIPE, and CI modes. Emoji→ASCII conversion for non-TTY.

### Enum: `OutputMode`
`TTY`, `PIPE`, `CI`

### Constants
| Name | Description |
|------|-------------|
| `_EMOJI_TO_ASCII` | 18 emoji → ASCII mappings |

### Functions
| Function | Signature | Description |
|----------|-----------|-------------|
| `detect_output_mode` | `() -> OutputMode` | Auto-detect from env/isatty |
| `sanitize_text` | `(text, mode) -> str` | Mode-appropriate sanitization |

### Class: `SafePrinter`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(mode=None, stream=None)` | Init with auto-detection |
| `mode` / `quiet` / `is_tty` / `is_pipe` / `is_ci` | *properties* | Mode checks |
| `print` | `(*args, **kwargs)` | Safe print |
| `status` | `(icon, message, indent=2)` | Status line |
| `banner` | `(text)` | Banner print |
| `separator` | `(char, width)` | Separator line |
| `prompt_text` | `(label="Lina") -> str` | Prompt string |

### Singletons
`get_printer()`, `reset_printer(mode=None)`

---

## 29. `pipeline_coordinator.py`
**Lines:** ~280 | **Phase:** 26

### Purpose
Unified coordinator linking all 21+ modules in defined order. Alternative to MainPipeline for callback-style composition.

### Type Alias
`ModuleCallback = Callable[[Dict[str, Any]], Dict[str, Any]]`

### Class: `PipelineCoordinator`

| Constant | Value |
|----------|-------|
| `MODULE_ORDER` | 16 modules: router, priority, capabilities, orchestrator, intent_lock, executor, validator, budget, consistency, step_memory, drift, post_processor, guard, degradation, tracer, state |

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init empty registry |
| `register_module` | `(name, callback)` | Register module callback |
| `process` | `(user_input, **kwargs) -> Dict` | Execute all modules in order through shared context dict |
| `get_registered_modules` | `()` | List registered |
| `get_missing_modules` | `()` | Modules in ORDER but not registered |
| `get_stage_metrics` | `()` | Per-stage timing |
| `get_stats` | `()` | Stats |

---

## 30. `pipeline.py`
**Lines:** ~380 | **Phase:** 9

### Purpose
Original unified pipeline (pre-Block A). Intent → context → model → safety → dispatch → metrics.

### Dataclass: `PipelineResult`
Fields: `response`, `context`, `safety_blocked`, `plan_created`, `from_cache`, `model_tier`, `elapsed`, `metadata`

### Class: `CorePipeline`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(generate_fn, process_fn, rag_fn, runtime_fn)` | Init with RuntimeState, ContextBuilder, ModelRouter, SafetyValidator, PolicyEngine, RuntimeProfiler |
| `process` | `(raw_input, force_tier=None) -> PipelineResult` | Intent → context → model → safety → dispatch → metrics |
| `_check_safety` | `(ctx) -> Optional[PipelineResult]` | Safety gate |
| `_dispatch` | `(ctx) -> str` | Route by intent type |
| `get_status` | `()` | Status dict |
| `format_status` | `()` | Formatted string |
| `get_metrics_report` | `(output_path=None)` | Export metrics |

---

## 31. `post_processor.py`
**Lines:** ~210 | **Phase:** 22

### Purpose
LLM response cleanup — removes debug markers, system prompt leaks, raw tool output, internal JSON.

### Constants
| Name | Count | Description |
|------|-------|-------------|
| `_SYSTEM_PROMPT_LEAKS` | 7 regex | System prompt leakage patterns |
| `_DEBUG_MARKERS` | 7 regex | Debug output patterns |
| `_INTERNAL_JSON` | 1 regex | Internal JSON structures |
| `_TOOL_RAW` | 3 regex | Raw tool output patterns |

### Dataclass: `ProcessingResult`
Fields: `text` (Optional), `blocked`, `modifications`, `leak_found`, `details`

### Class: `PostProcessor`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(strict=False)` | Strict mode for aggressive cleaning |
| `process` | `(response) -> ProcessingResult` | Full cleanup pipeline |
| `get_stats` | `()` | Processed/leaks/blocked counts |
| `reset_stats` | `()` | Reset counters |

---

## 32. `priority_resolver.py`
**Lines:** ~230 | **Phase:** 24

### Purpose
Execution priority resolution: determines which requests get higher priority.

### Enum: `PriorityLevel(IntEnum)`
| Value | Name |
|-------|------|
| 1 | SYSTEM |
| 2 | SAFETY |
| 3 | USER_TOOL |
| 4 | LLM |
| 5 | FALLBACK |

### Constants
`PRIORITY_DESCRIPTIONS`, `_INTENT_PRIORITIES`

### Dataclass: `PriorityResult`
Fields: `level`, `description`, `intent`, `confidence`, `override_reason`

### Class: `PriorityResolver`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init with empty overrides |
| `resolve` | `(intent, confidence, *, safe_mode, is_system, is_explicit_tool) -> PriorityResult` | Determine priority |
| `compare` | `(a, b) -> int` | Compare two priority results |
| `set_override` | `(intent, level)` | Manual override |
| `clear_overrides` | `()` | Reset overrides |
| `get_stats` | `()` | Stats |

---

## 33. `production_guard.py`
**Lines:** ~175 | **Phase:** 23

### Purpose
Final safety filter before response delivery. ONLY blocks — NEVER modifies.

### Constants
| Name | Count | Description |
|------|-------|-------------|
| `_FORBIDDEN_PATTERNS` | 28 regex | Debug markers, system leaks, internal state, raw tool output |

### Dataclass: `GuardResult`
Fields: `passed`, `violations`, `blocked`

### Class: `ProductionGuard`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Compile forbidden patterns |
| `check` | `(response) -> GuardResult` | Check response against all 28 patterns |
| `get_stats` | `()` | Checked/blocked/violations counts |
| `reset_stats` | `()` | Reset |

---

## 34. `prompts.py`
**Lines:** 382 | **Phase:** X1

### Purpose
Prompt engineering templates for system, RAG, diagnostic, command, and explanation prompts.

### Constants
| Name | Description |
|------|-------------|
| `SYSTEM_PROMPT_RU` | Russian system prompt with `{distro}`, `{package_manager}`, `{desktop}`, `{kernel}` placeholders |
| `SYSTEM_PROMPT_EN` | English system prompt |
| `RAG_PROMPT_TEMPLATE` / `_EN` | RAG context template |
| `DIAGNOSTIC_PROMPT_TEMPLATE` | Diagnostic analysis template |
| `COMMAND_GENERATION_PROMPT` | Shell command generation template |
| `EXPLANATION_PROMPT` | Command explanation template |

### Dataclasses
| Dataclass | Fields |
|-----------|--------|
| `PromptConfig` | `language`, `max_context_tokens`, `max_system_tokens`, `include_system_info`, `safety_level` |
| `SystemContext` | `distro`, `package_manager`, `desktop`, `kernel` |

### Class: `PromptBuilder`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(config=None, system_ctx=None)` | Init with config |
| `get_system_prompt` | `() -> str` | Language-appropriate system prompt |
| `build_rag_prompt` | `(query, context) -> str` | RAG prompt |
| `build_diagnostic_prompt` | `(logs, system_info="") -> str` | Diagnostic prompt |
| `build_command_prompt` | `(task) -> str` | Command generation prompt |
| `build_explanation_prompt` | `(command) -> str` | Command explanation prompt |
| `add_template` | `(name, template)` | Register custom template |
| `build_custom` | `(template_name, **kwargs)` | Build from custom template |
| `list_templates` | `()` | All template names |
| `check_safety` | `(text) -> Dict[str, bool]` | Safety flags |
| `estimate_tokens` | `(text) -> int` | Rough token estimate |

---

## 35. `repl.py`
**Lines:** ~165 | **Phase:** —

### Purpose
Interactive REPL (read-eval-print loop).

### Class: `REPLSession`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(commander, printer=None, web_server=None)` | Init with Commander |
| `is_running` | *property* → `bool` | Running state |
| `run` | `() -> None` | Main REPL loop (handles EOF, KeyboardInterrupt) |
| `run_oneshot` | `(query) -> str` | Single query mode |
| `_shutdown` | `(message="...")` | Unload model, stop watchdog, web |
| `stop` | `() -> None` | Signal stop |

---

## 36. `response_validator.py`
**Lines:** ~195 | **Phase:** 22

### Purpose
LLM response validation: checks emptiness, fallback patterns, system leaks, truncation, length, repetition, language mismatch.

### Constants
| Name | Count | Description |
|------|-------|-------------|
| `_FALLBACK_PATTERNS` | 3 regex | Generic fallback responses |
| `_TRUNCATION_PATTERN` | 1 regex | Truncated output |
| `_SYSTEM_LEAK_PATTERNS` | 4 regex | System prompt leaks |

### Dataclass: `ValidationResult`
Fields: `is_valid`, `score`, `issues`, `can_retry`

### Class: `ResponseValidator`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(score_threshold=0.3, max_retries=2)` | Init with thresholds |
| `validate` | `(response, user_input="", context=None) -> ValidationResult` | Full validation |
| `get_stats` | `()` | Validated/valid/invalid counts |
| `reset_stats` | `()` | Reset |

---

## 37. `runtime_state.py`
**Lines:** ~310 | **Phase:** 9

### Purpose
Runtime state definitions: request phases, intent types, request context, feature flags.

### Enums
| Enum | Values |
|------|--------|
| `RequestPhase` | RECEIVED, INTENT_DETECTED, CONTEXT_BUILT, BUDGET_CHECKED, MODEL_SELECTED, SAFETY_CHECKED, GENERATED, PLANNED, EXECUTED, COMPLETED |
| `IntentType` | COMMAND, QUESTION, RAG_QUERY, MACRO, CHAIN, META, PLANNING, CV |

### Dataclass: `RequestContext`
Fields: `request_id`, `raw_input`, `intent`, `phase`, `model_tier`, `rag_context`, `runtime_context`, `prompt`, `response`, `safety_verdict`, `plan_active`, `metadata`, `created_at`, `elapsed`, `errors`

### Class: `RuntimeState`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(max_history=100)` | Init with pipeline_config dict |
| `new_request` | `(raw_input) -> RequestContext` | Create new request context |
| `complete_request` | `() -> None` | Finalize current request |
| `enable_feature` / `disable_feature` | `(feature)` | Toggle pipeline features |
| `is_enabled` | `(feature) -> bool` | Check feature flag |
| `request_count` | *property* → `int` | Total requests |
| `get_recent_history` | `(limit=10)` | Recent requests |
| `to_dict` | `()` | Full state |
| `format_status` | `()` | Formatted string |

### Pipeline Config Keys
`safety_enabled`, `planning_enabled`, `metrics_enabled`, `rag_enabled`, `cache_enabled`

---

## 38. `runtime.py`
**Lines:** ~350 | **Phase:** —

### Purpose
Main launch module — combines bootstrap, CLI, output, REPL into a runnable entry point.

### Constants
| Name | Value |
|------|-------|
| `BANNER` | ASCII art banner |
| `VERSION` | `"0.5.0"` |

### Functions
| Function | Signature | Description |
|----------|-----------|-------------|
| `apply_config` | `(args: LinaArgs) -> None` | Apply CLI args to global config |
| `print_startup_info` | `(printer, commander) -> None` | Print startup banner |
| `print_optional_info` | `(printer, commander, args) -> None` | Print optional info |
| `start_web_server` | `(printer, commander)` | Optional LinaWebServer |
| `start_notifications` | `(printer, commander) -> None` | System notifications |
| `start_watchdog` | `(commander) -> None` | Watchdog process |
| `run` | `(argv=None) -> int` | **MAIN ENTRY**: bootstrap → CLI → config → output → Commander → signals → startup → REPL/oneshot |
| `_setup_console_logging` | `(level=DEBUG)` | Console logging |
| `_cleanup` | `(commander)` | Cleanup on exit |

---

## 39. `semantic_drift.py`
**Lines:** 214 | **Phase:** 25

### Purpose
Detects semantic drift between pipeline steps: entity drift, strategy drift, fingerprint radical changes.

### Dataclass: `DriftResult`
Fields: `drift_detected`, `drift_type`, `reason`, `severity`, `recommend_regenerate`, `recommend_strict`

### Class: `SemanticDriftDetector`

| Constant | Value |
|----------|-------|
| `DRIFT_STREAK_THRESHOLD` | 3 |

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init streak counter |
| `check` | `(*, prev_entities, curr_entities, prev_strategy, curr_strategy, prev_fingerprint, curr_fingerprint, prev_intent, curr_intent) -> DriftResult` | Multi-signal drift check |
| `_record_drift` | `(result)` | Increment streak if drift |
| `reset_streak` | `()` | Reset |
| `get_consecutive_drifts` | `() -> int` | Current streak |
| `clear` | `()` | Full reset |
| `get_stats` | `()` | Stats |

---

## 40. `step_memory.py`
**Lines:** ~220 | **Phase:** 25

### Purpose
Multi-step execution memory. Records snapshots of each pipeline step with entities, strategy, fingerprint.

### Dataclass: `StepSnapshot`
Fields: `step_number`, `intent`, `path`, `status`, `summary_reasoning`, `semantic_fingerprint`, `entities`, `strategy`, `consistency_score`

### Class: `StepMemory`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(max_steps=20)` | Ring buffer size |
| `record_step` | `(step_number, *, intent, path, status, summary, entities, strategy, consistency_score) -> StepSnapshot` | Record step |
| `get_previous` | `() -> Optional[StepSnapshot]` | Last step |
| `get_step` | `(step_number)` | Specific step |
| `get_all` | `()` | All steps |
| `get_entities_history` | `()` | Entities across all steps |
| `clear` / `new_session` | `()` | Reset |
| `_compute_fingerprint` | `(intent, entities, strategy) -> str` | MD5 hash |
| `get_stats` | `()` | Stats |

---

## 41. `system_control.py`
**Lines:** 404 | **Phase:** 22-26

### Purpose
Handles `/system *` diagnostic commands. Dispatches to registered providers.

### Class: `SystemControl`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init empty providers |
| `register_provider` | `(name, provider)` | Register callable provider |
| `handle` | `(command) -> Optional[str]` | Parse /system command and dispatch |
| `_get_data` | `(provider_name) -> Dict` | Safe provider call |

### 27 Subcommands
`status`, `config`, `router`, `tools`, `memory`, `history`, `budget`, `performance`, `reload`, `safe-mode`, `trace`, `mode`, `drift`, `state`, `degradation`, `guard`, `orchestrator`, `capabilities`, `priority`, `integrity`, `consistency`, `stepmem`, `semdrift`, `intentlock`, `pipeline`, `lifecycle`, `envelope`

Each maps to a `_cmd_*` method that reads from the registered provider.

---

## 42. `system_interaction.py`
**Lines:** 780 | **Phase:** —

### Purpose
**SOLE module for OS interaction.** System snapshot collection, command extraction from LLM responses, safe action execution, query preprocessing with direct-answer fast path, and LLM context enrichment.

### Dataclass: `SystemSnapshot`
Fields: `kernel`, `hostname`, `username`, `distro`, `distro_id`, `de`, `shell`, `uptime`, `cpu_model`, `cpu_cores`, `ram_total_mb`, `ram_free_mb`, `disk_total_gb`, `disk_free_gb`, `display_server`, `gpu`, `ip_local`, `has_brightnessctl`, `has_pactl`, `has_nmcli`, `has_systemctl`, `has_journalctl`, `has_flatpak`, `has_snap`, `has_docker`

### Module-Level Functions
| Function | Signature | Description |
|----------|-----------|-------------|
| `collect_system_snapshot` | `() -> SystemSnapshot` | Collects real OS data via procfs/sysfs/CLI |
| `format_snapshot_for_prompt` | `(snap) -> str` | Format snapshot for LLM prompt |
| `extract_commands` | `(llm_response) -> List[ExtractedCommand]` | Parse markdown code blocks for shell commands |
| `_normalize_query` | `(query) -> str` | Lowercase, strip filler words |

### Constants
| Name | Description |
|------|-------------|
| `_DANGEROUS_PATTERNS` | 12 regex patterns for blocked commands |
| `_DANGEROUS_RE` | Compiled combined pattern |
| `_SAFE_AUTO_PATTERNS` | 13 regex patterns for safe read-only commands |
| `_SAFE_AUTO_RE` | Compiled combined pattern |
| `_DIRECT_QUERIES` | `Dict[str, str]` — stem→command mapping (ядр→`uname -r`, памят→`free -h`, etc.) |
| `_DIRECT_ACTIONS` | `Dict[str, str]` — action→command mapping (яркость 100→`brightnessctl set 100%`, etc.) |
| `_FUZZY_BRIGHTNESS_UP/DOWN` | Regex for fuzzy brightness patterns |
| `_FUZZY_VOLUME_UP/DOWN` | Regex for fuzzy volume patterns |
| `_OPEN_PATTERN` | Regex for "открой X" / "запусти X" |
| `_GREETING_PATTERNS` | Regex for greetings |
| `_GREETING_RESPONSES` | 3 greeting responses |
| `_META_RESPONSES` | Dict for help/who/version responses |

### Dataclass: `ExtractedCommand`
Fields: `command`, `is_dangerous`, `is_safe_auto`, `description`, `needs_sudo`

### Dataclass: `ExecutionResult`
Fields: `command`, `stdout`, `stderr`, `returncode`, `success`, `skipped`, `reason`

### Class: `ActionExecutor`
| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(interactive=True, timeout=30)` | Init execution context |
| `execute` | `(cmd: ExtractedCommand) -> ExecutionResult` | Execute one command with safety checks |
| `execute_many` | `(commands) -> List[ExecutionResult]` | Execute list sequentially |

### Class: `QueryPreprocessor`
| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(snapshot=None)` | Init with system snapshot |
| `snapshot` | *property* → `SystemSnapshot` | Access snapshot |
| `try_direct_answer` | `(query) -> Optional[str]` | Fast path: greetings → meta → direct queries → direct actions → brightness% → volume% → fuzzy brightness → fuzzy volume → "открой X" via ApplicationResolver |
| `enrich_for_llm` | `(query) -> str` | Add relevant system context for LLM (disk/net/process/brightness/audio data) |
| `_run_safe` | `(cmd) -> Optional[str]` | Safe subprocess execution |

---

## 43. `tool_engine.py`
**Lines:** ~190 | **Phase:** 22

### Purpose
Safe tool execution wrapper: register → sanitize → execute → format → validate. Never writes to history or registers knowledge.

### Dataclass: `ToolResult`
Fields: `success`, `output`, `error`, `tool_name`, `sanitized`, `truncated`, `raw_length`
Method: `to_dict()`

### Class: `ToolEngine`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(max_output_tokens=300)` | Init with output token limit |
| `register` | `(name, handler, safe_mode_allowed=True)` | Register tool handler |
| `set_allowed_tools` | `(tools: Optional[List[str]])` | Whitelist (None = all) |
| `execute` | `(tool_name, args=None, safe_mode=False) -> ToolResult` | Full execute pipeline: exists? → whitelist? → safe_mode? → sanitize → execute → format |
| `_sanitize_input` | `(args) -> Dict` | Strip null/control chars |
| `_format_output` | `(raw) -> tuple` | Truncate to max_output_tokens × 2.2 chars |
| `list_tools` | `() -> Dict[str, bool]` | Tool names → safe_mode_allowed |
| `get_stats` | `() -> Dict` | Executions/errors/truncated |
| `reset_stats` | `()` | Reset |

---

## 44. `tools.py`
**Lines:** 1370 | **Phase:** —

### Purpose
All tool definitions for function-calling. Contains `ToolRegistry` with 27 built-in tools and their implementations.

### Dataclass: `ToolResult`
Fields: `success`, `output`, `error`, `needs_full_llm`

### Class: `ToolRegistry`

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `()` | Init + `_register_builtins()` |
| `register` | `(name, description, parameters, handler)` | Register tool |
| `get_tools_prompt` | `() -> str` | Generate tool descriptions for system prompt |
| `execute` | `(name, arguments) -> ToolResult` | Execute tool by name |
| `tool_names` | *property* → `List[str]` | All tool names |

### 27 Built-in Tools

| Tool Name | Handler | Description |
|-----------|---------|-------------|
| `respond` | `_tool_respond` | Text response to user |
| `set_brightness` | `_tool_brightness` | Brightness via `brightnessctl` |
| `set_volume` | `_tool_volume` | Volume via `pactl` |
| `open_app` | `_tool_open_app` | Launch app via `ApplicationResolver` |
| `run_shell` | `_tool_shell` | Execute shell command (with safety check) |
| `system_info` | `_tool_system_info` | System info by category (os/cpu/ram/disk/network/processes/all) |
| `ask_full_llm` | `_tool_full_llm` | Pass to heavy LLM |
| `screenshot` | `_tool_screenshot` | Screenshot via `spectacle` |
| `media_control` | `_tool_media_control` | Media via `playerctl` |
| `set_timer` | `_tool_timer` | Timer with notification via `notify-send` |
| `power_control` | `_tool_power_control` | lock/sleep/reboot/shutdown via `systemctl` |
| `toggle_wifi` | `_tool_wifi` | WiFi via `nmcli` |
| `toggle_bluetooth` | `_tool_bluetooth` | Bluetooth via `bluetoothctl` |
| `night_mode` | `_tool_night_mode` | Night light via KDE DBus |
| `clipboard` | `_tool_clipboard` | Clipboard via `wl-copy`/`wl-paste` |
| `send_notification` | `_tool_notification` | Desktop notification via `notify-send` |
| `kill_process` | `_tool_kill_process` | Kill process via `pkill` |
| `open_url` | `_tool_open_url` | Open URL via `xdg-open` |
| `find_file` | `_tool_find_file` | Find files via `find` |
| `weather` | `_tool_weather` | Weather via `WebSearchEngine` (fallback: wttr.in) |
| `web_search` | `_tool_web_search` | Web search via `WebSearchEngine` |
| `install_app` | `_tool_install_app` | Install suggestions via `ApplicationResolver` |
| `run_in_console` | `_tool_run_in_console` | Open terminal with command |
| `diagnose_problem` | `_tool_diagnose_problem` | Full diagnostics via scanner/log_engine/classifier/memory |
| `system_health` | `_tool_system_health` | Quick health check via scanner |
| `fix_problem` | `_tool_fix_problem` | Auto-fix via autofix engine (safe/assist/auto modes) |
| `system_overview` | `_tool_system_overview` | Full system overview via `FullSystemControlLayer` |
| `predictive_report` | `_tool_predictive_report` | Predictive analysis (OVERLORD) |
| `drift_check` | `_tool_drift_check` | Config drift detection (OVERLORD) |
| `risk_assess` | `_tool_risk_assess` | Command risk assessment (OVERLORD) |
| `integrity_check` | `_tool_integrity_check` | Module integrity verification (OVERLORD) |
| `healer_status` | `_tool_healer_status` | Self-healing status (OVERLORD) |
| `web_solution` | `_tool_web_solution` | Web intelligence for problem solving (OVERLORD) |

---

## 45. `web_search_engine.py`
**Lines:** 997 | **Phase:** 27

### Purpose
Production-grade web search with retry, fallback chain, relevance ranking, and specialized flows for weather/currency/news.

### Dataclasses
| Dataclass | Key Fields |
|-----------|------------|
| `SearchResult` | `title`, `url`, `snippet`, `relevance` |
| `WebSearchResponse` | `success`, `query`, `results`, `summary`, `source`, `error`, `attempts`, `elapsed_ms` |
| `WeatherData` | `city`, `temperature`, `description`, `humidity`, `wind`, `raw_text`, `source` |

### Helper Classes/Functions
| Name | Description |
|------|-------------|
| `_HTMLTextExtractor(HTMLParser)` | Extract text from HTML, skip script/style |
| `_extract_text(html) -> str` | Parse HTML to text |
| `_strip_tags(html) -> str` | Regex tag removal |

### Pattern Constants
`_WEATHER_PATTERNS`, `_CURRENCY_PATTERNS`, `_NEWS_PATTERNS`, `_CITY_EXTRACT`

### Class: `WebSearchEngine`

| Constant | Value |
|----------|-------|
| `CURL_TIMEOUT` | 12 |
| `MAX_RETRIES` | 2 |
| `RELEVANCE_THRESHOLD` | 0.3 |
| `MAX_FETCH_PAGES` | 3 |
| `MAX_SUMMARY_CHARS` | 3000 |
| `_SEARXNG_INSTANCES` | 4 public SearXNG URLs |
| `_WMO_CODES` | Weather code → description mapping |
| `_CITY_CASE_MAP` | Russian declension → nominative city name |

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(web_capable=True)` | Init with capability flag |
| `search` | `(query) -> WebSearchResponse` | **Main API**: classify → weather/currency/general → retry → rank → summarize |
| `fetch` | `(url, max_length=50000) -> Dict` | Download page, extract text |
| `_classify_query` | `(query) -> str` | weather/currency/news/general |
| `_handle_weather` | `(query, start) -> WebSearchResponse` | Weather flow: wttr.in JSON → open-meteo → wttr.in text → general |
| `_try_wttr_json` | `(city) -> Optional[WeatherData]` | wttr.in JSON API |
| `_try_wttr_text` | `(city) -> Optional[WeatherData]` | wttr.in text format |
| `_try_open_meteo` | `(city) -> Optional[WeatherData]` | Open-Meteo free API (geocode + weather) |
| `_transliterate` | `(text) -> str` | Russian → Latin transliteration |
| `_extract_city` | `(query) -> str` | Extract city from weather query (with Russian declension handling) |
| `_handle_currency` | `(query, start) -> WebSearchResponse` | Currency flow: exchange rate API → general |
| `_try_exchange_rate` | `(query) -> Optional[str]` | open.er-api.com free API |
| `_handle_general_search` | `(query, start) -> WebSearchResponse` | General search with retry + fallback chain |
| `_search_duckduckgo` | `(query) -> List[SearchResult]` | DDG Lite POST |
| `_search_duckduckgo_html` | `(query) -> List[SearchResult]` | DDG HTML POST |
| `_parse_ddg_lite` | `(html) -> List[SearchResult]` | Parse DDG Lite results |
| `_parse_ddg_html` | `(html) -> List[SearchResult]` | Parse DDG HTML results |
| `_search_searxng` | `(query) -> List[SearchResult]` | SearXNG JSON API (4 public instances) |
| `_search_wikipedia` | `(query) -> List[SearchResult]` | Wikipedia API (ru + en) |
| `_rank_results` | `(query, results) -> List[SearchResult]` | Relevance scoring |
| `_fetch_and_summarize` | `(query, results) -> str` | Download top pages + build summary |
| `_validate_response` | `(summary) -> bool` | Validate response quality |
| `_elapsed_ms` | `(start) -> int` | Timer helper |
| `get_stats` | `() -> Dict` | Searches/successes/failures/retries/fallbacks |
| `set_web_capable` | `(capable)` | Enable/disable web |

### Search Fallback Chain
```
DuckDuckGo Lite → DuckDuckGo HTML → SearXNG (4 instances) → Wikipedia (ru + en)
```

### Singleton
`get_web_search_engine() -> WebSearchEngine`

---

## Module Interconnection Map

```
┌─────────────────────────────────────────────────────────────────┐
│                        ENTRY POINTS                             │
│   runtime.py → cli.py → main_pipeline.py (process_request)     │
│                    OR                                           │
│   cli.py → QueryPreprocessor.try_direct_answer() [fast path]   │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                   MainPipeline (14 steps)                       │
│                                                                 │
│  Step 1:  governance.RuntimeStateManager                        │
│           mode_control.ModeController                           │
│           config_manager.ConfigManager                          │
│           capability_registry.CapabilityRegistry                │
│           budget_governor.BudgetGovernor                        │
│                                                                 │
│  Step 2:  intent_router.IntentRouter                            │
│                                                                 │
│  Step 3:  priority_resolver.PriorityResolver                    │
│                                                                 │
│  Step 4:  execution_orchestrator.ExecutionOrchestrator          │
│                                                                 │
│  Step 5:  integrity_checker.IntegrityChecker                    │
│                                                                 │
│  Step 6:  intent_lock.IntentLock                                │
│                                                                 │
│  Step 7:  [LLM / TOOL / RAG executor callbacks]                 │
│           ├── llm: inference engine → context_budget.py         │
│           ├── tool: shell.Commander                             │
│           └── rag: rag.Searcher                                 │
│                                                                 │
│  Step 8:  post_processor.PostProcessor                          │
│                                                                 │
│  Step 9:  production_guard.ProductionGuard                      │
│                                                                 │
│  Step 10: response_validator.ResponseValidator                  │
│                                                                 │
│  Step 11: degradation.DegradationStrategy                       │
│           (may re-run Steps 7-10 for regeneration)              │
│                                                                 │
│  Step 12: consistency_engine.ConsistencyEngine                  │
│           semantic_drift.SemanticDriftDetector                   │
│           drift_detector.StateDriftDetector                     │
│           step_memory.StepMemory                                │
│                                                                 │
│  Step 13: budget_governor.BudgetGovernor                        │
│                                                                 │
│  Step 14: execution_trace.ExecutionTracer                       │
│                                                                 │
│  /system: system_control.SystemControl (27 subcommands)         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    EXTERNAL INTERACTION                          │
│                                                                 │
│  system_interaction.py — SOLE OS interaction module             │
│  ├── collect_system_snapshot() → SystemSnapshot                 │
│  ├── extract_commands() → ExtractedCommand                      │
│  ├── ActionExecutor.execute() → ExecutionResult                 │
│  ├── QueryPreprocessor.try_direct_answer() [fast path]          │
│  └── QueryPreprocessor.enrich_for_llm() [context enrichment]   │
│                                                                 │
│  application_resolver.py — App discovery & launch               │
│  ├── find_installed_apps() → scan .desktop/flatpak/snap/appimg  │
│  ├── match_app() → fuzzy matching with Levenshtein              │
│  ├── launch() → Popen + PID verification                       │
│  └── suggest_installation() → pacman/apt/dnf/flatpak/snap      │
│                                                                 │
│  web_search_engine.py — Web search with fallback chain          │
│  ├── DDG Lite → DDG HTML → SearXNG → Wikipedia                 │
│  ├── Weather: wttr.in → open-meteo → text fallback             │
│  └── Currency: open.er-api.com                                  │
│                                                                 │
│  tools.py — 27+ tool definitions for function-calling           │
│  tool_engine.py — Safe execution wrapper with sanitization      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    SUPPORT MODULES                               │
│                                                                 │
│  bootstrap.py — Safe startup (faulthandler, signals)            │
│  output.py — TTY/PIPE/CI safe printing                          │
│  prompts.py — Prompt templates (RU/EN)                          │
│  i18n.py — Internationalization (RU primary + EN)               │
│  metrics.py — Extended performance metrics                      │
│  context.py — Context builder (Phase 9, original)               │
│  context_budget.py — Token budget management                    │
│  model_router.py — Model selection (single model)               │
│  runtime_state.py — State definitions                           │
│  pipeline.py — Original pipeline (Phase 9)                      │
│  orchestrator.py — Original orchestrator (Phase 20)             │
│  human_response.py — LLM output sanitization                   │
│  envelope.py — Request/Response wrappers                        │
│  lifecycle.py — Stage-based lifecycle                           │
│  pipeline_coordinator.py — Alternative module coordinator       │
│  repl.py — Interactive REPL                                     │
│  runtime.py — Launch entry point                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Design Principles

1. **Strict Isolation**: Each module ONLY checks/decides, NEVER executes directly. Only `system_interaction.py` and `tools.py` touch the OS.
2. **Single Entry Point**: `MainPipeline.process_request()` is the only public processing method.
3. **14-Step Pipeline**: Fixed, verified order with private step methods.
4. **Deterministic Plans**: `ExecutionPlan.plan_hash` is MD5 of plan parameters for reproducibility.
5. **Defensive Layers**: PostProcessor → ProductionGuard → ResponseValidator → HumanResponseLayer — 4 layers of output filtering.
6. **Degradation → Recovery**: Failure streaks trigger mode switches (STRICT → SAFE) and regeneration attempts.
7. **No Side Effects in Modules**: Tool engine never writes history. State changes only through `RuntimeStateManager` with listener notification.
8. **Read-Only Diagnostics**: `SystemControl` only reads from registered providers.
