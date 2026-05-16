# Lina — Complete Module Reference

> Auto-generated deep scan of every Python module.  
> Each entry: exact class names, method signatures, imports, constants, dataclasses, enums, decorators.

---

## Table of Contents

- [0. models/datatypes.py — Core Data Types](#0-modelsdatatypespy)
- [A. core/ — Core Pipeline & Routing](#a-core)
  - [A1. intent_router.py](#a1-intent_routerpy)
  - [A2. query_understanding.py](#a2-query_understandingpy)
  - [A3. entity_parser.py](#a3-entity_parserpy)
  - [A4. query_rewriter.py](#a4-query_rewriterpy)
  - [A5. web_search_engine.py](#a5-web_search_enginepy)
  - [A6. fact_pipeline.py](#a6-fact_pipelinepy)
  - [A7. fact_extractor.py](#a7-fact_extractorpy)
  - [A8. fact_aggregator.py](#a8-fact_aggregatorpy)
  - [A9. fact_verifier.py](#a9-fact_verifierpy)
  - [A10. context_budget.py](#a10-context_budgetpy)
  - [A11. main_pipeline.py](#a11-main_pipelinepy)
  - [A12. execution_orchestrator.py](#a12-execution_orchestratorpy)
  - [A13. tool_engine.py](#a13-tool_enginepy)
  - [A14. tools.py](#a14-toolspy)
  - [A15. cli.py](#a15-clipy)
  - [A16. repl.py](#a16-replpy)
  - [A17. bootstrap.py](#a17-bootstrappy)
- [B. llm/ — LLM Inference](#b-llm)
  - [B1. engine.py](#b1-enginepy)
  - [B2. mini_engine.py](#b2-mini_enginepy)
  - [B3. token_budget.py](#b3-token_budgetpy)
  - [B4. self_verifier.py](#b4-self_verifierpy)
  - [B5. fact_prompt.py](#b5-fact_promptpy)
- [C. pipeline/pipeline_v3.py](#c-pipelinepipeline_v3py)
- [D. shell/commander.py](#d-shellcommanderpy)
- [E. gui/app.py](#e-guiapppy)
- [F. system/ — System Management](#f-system)
  - [F1. executor.py](#f1-executorpy)
  - [F2. sandbox.py](#f2-sandboxpy)
  - [F3. monitor.py](#f3-monitorpy)
  - [F4. package_manager.py](#f4-package_managerpy)
- [G. rag/ — Retrieval-Augmented Generation](#g-rag)
  - [G1. retriever.py](#g1-retrieverpy)
  - [G2. indexer.py](#g2-indexerpy)
  - [G3. vectorstore.py](#g3-vectorstorepy)
- [H. voice/ — Voice Pipeline](#h-voice)
  - [H1. stt.py](#h1-sttpy)
  - [H2. tts.py](#h2-ttspy)
  - [H3. pipeline.py](#h3-pipelinepy)
- [I. diagnostics/](#i-diagnostics)
  - [I1. engine.py](#i1-enginepy)
  - [I2. session.py](#i2-sessionpy)
- [J. governance/ — Policy & State](#j-governance)
  - [J1. policy_engine.py](#j1-policy_enginepy)
  - [J2. action_registry.py](#j2-action_registrypy)
  - [J3. state_machine.py](#j3-state_machinepy)
- [K. config.py — Global Configuration](#k-configpy)
- [L. processing/html_cleaner.py](#l-processinghtml_cleanerpy)
- [M. embeddings/semantic_ranker.py](#m-embeddingssemantic_rankerpy)
- [N. memory/ — Conversation & Fact Persistence](#n-memory)
  - [N1. conversation_state.py](#n1-conversation_statepy)
  - [N2. fact_store.py](#n2-fact_storepy)
- [O. retrieval/ — Parallel Search & Ranking](#o-retrieval)
  - [O1. parallel_search.py](#o1-parallel_searchpy)
  - [O2. result_merger.py](#o2-result_mergerpy)
  - [O3. domain_ranker.py](#o3-domain_rankerpy)
- [P. safety/validator.py](#p-safetyvalidatorpy)
- [Q. security/input_validator.py](#q-securityinput_validatorpy)

---

## 0. models/datatypes.py

> Core data types shared across the entire pipeline. 296 lines.

**Imports:** `time`, `dataclasses (dataclass, field)`, `enum (Enum)`, `typing`

### Enums

| Enum | Values |
|------|--------|
| `IntentType(Enum)` | CHAT, WEB_SEARCH, SYSTEM_COMMAND, MATH, RAG, DIAGNOSTIC, WEATHER, UNKNOWN |
| `ConfidenceLevel(Enum)` | HIGH (≥0.75), MEDIUM (0.50–0.74), LOW (0.25–0.49), NONE (<0.25); `classmethod from_score(score) → ConfidenceLevel` |

### Dataclasses

| Dataclass | Fields | Methods/Properties |
|-----------|--------|--------------------|
| `SearchResult` | title, url, snippet, relevance=0.0, content="", source_engine="", domain_score=0.5, timestamp | `__repr__` |
| `QueryPlan` | original, queries=[], detected_entities=[], detected_intent="", language="ru" | `@property primary → str` |
| `Passage` | text, source_url="", source_title="", score=0.0, char_offset=0, word_count=0 | `__post_init__`, `__repr__` |
| `Fact` | subject, predicate, object_value, sources=[], source_count=1, confidence=0.5, verified=False | `key() → str`, `__repr__` |
| `FactSet` | subject, facts=[], total_sources=0, confidence=0.0 | `@property verified_facts`, `@property verified_count`, `get_by_predicate(predicate) → Optional[Fact]`, `format_for_llm() → str`, `format_for_llm_ru() → str` |
| `PipelineAnswer` | text, facts_used=[], confidence=0.0, confidence_level, sources=[], verified=False, hallucination_flags=[], generation_attempts=1, elapsed_ms=0.0 | `__post_init__`, `is_reliable() → bool` |
| `ConversationTurn` | query, answer, intent="", topic="", entities=[], facts=[], timestamp | — |
| `PipelineTrace` | stage_timings={}, search_results_count=0, passages_count=0, facts_extracted=0, facts_verified=0, errors=[] | `record(stage, duration_ms)`, `total_ms() → float` |
| `QueryUnderstanding` | raw_query, intent="", entities=[], attributes=[], language="ru", query_type="factual", need_web_search=True, confidence=0.0 | `primary_entity() → str`, `primary_attribute() → str` |
| `RetrievalResult` | results=[], engines_used=[], total_raw=0, total_deduped=0, elapsed_ms=0.0 | `top(n=10) → List[SearchResult]` |

---

## A. core/

### A1. intent_router.py

> 716 lines. Routes user queries to correct intent.

**Imports:** `re`, `logging`, `dataclasses`, `enum`, `typing`

**Enum:** `Intent(str, Enum)` — 16 values:
`CHAT`, `MATH`, `SYSTEM_COMMAND`, `FILE_OPERATION`, `WEB`, `WEB_SEARCH`, `WEATHER_QUERY`, `INSTALL_APPLICATION`, `RAG`, `CV`, `TOOL_EXPLICIT`, `META`, `CHAIN`, `MACRO`, `OPEN_APPLICATION`, `SYSTEM_DIAGNOSTIC`

**Dataclass:** `RoutingDecision`
- Fields: `intent: Intent`, `confidence: float`, `reason: str`, `alternatives: List`, `metadata: dict`
- Methods: `to_dict() → dict`

**Class:** `IntentRouter`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, confidence_threshold: float = 0.5)` |
| `route` | `(self, user_input: str) → RoutingDecision` |
| `_classify` | `(self, text: str) → RoutingDecision` |
| `get_stats` | `(self) → dict` |
| `reset_stats` | `(self)` |

**Constants (~15 compiled regex groups):**
`_META_PATTERN`, `_SYSTEM_CMD_PATTERN`, `_CHAIN_PATTERN`, `_CV_PATTERNS`, `_RAG_PATTERNS`, `_FILE_PATTERNS`, `_LLM_TOOL_PATTERNS`, `_SYSTEM_INFO_PATTERNS`, `_PRODUCT_BRAND_RE`, `_WEATHER_PATTERNS`, `_INSTALL_PATTERNS`, `_WEB_SEARCH_PATTERNS`, `_WEB_PATTERNS`, `_APP_LAUNCH_PATTERNS`, `_DIAGNOSTIC_PATTERNS`, `_SYSTEM_CONTROL_PATTERNS`, `_MATH_PATTERN`, `_DATETIME_PATTERNS`

**Helper:** `_clean_install_target(raw) → str`  
**Sets:** `_INSTALL_EXCEPTIONS`, `_INSTALL_CATEGORY_WORDS`, `_INSTALL_STOP_WORDS`

---

### A2. query_understanding.py

> 252 lines. Semantic analysis of user queries.

**Imports:** `re`, `logging`, `typing`; internal: `lina.models.datatypes.QueryUnderstanding`, `lina.core.entity_parser.EntityParser`

**Class:** `QueryUnderstandingEngine`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self)` |
| `analyze` | `(self, query: str) → QueryUnderstanding` |
| `_classify_intent` | `(self, text, entities, attributes) → str` |
| `_classify_query_type` | `(self, text) → str` |
| `_estimate_confidence` | `(self, entities, attributes, intent) → float` |

**Functions:** `_detect_language(text) → str`, `get_query_understanding() → QueryUnderstandingEngine` (singleton)

**Constants:** `_ATTRIBUTE_KEYWORDS` (dict, ~50 entries), regexes: `_COMPARISON_RE`, `_REVIEW_RE`, `_PRICE_RE`, `_SPEC_RE`, `_CHAT_RE`, `_MATH_RE`, `_SYSTEM_RE`

---

### A3. entity_parser.py

> 412 lines. Extracts entities (devices, brands, specs) from queries.

**Imports:** `re`, `logging`, `dataclasses`, `enum`, `typing`

**Enum:** `EntityType(Enum)` — DEVICE, CPU, GPU, RAM, STORAGE, DISPLAY, BATTERY, OS, BRAND, MODEL, PRICE, PERSON, PLACE, ATTRIBUTE

**Dataclasses:**
- `Entity` — type, value, span, confidence
- `ParsedQuery` — raw_query, entities, device, brand, attribute; methods: `has()`, `get()`, `get_all()`, `to_dict()`

**Class:** `EntityParser`
| Method | Signature |
|--------|-----------|
| `parse` | `(self, query: str) → ParsedQuery` |
| `extract_specs_from_text` | `(self, text: str) → List[Entity]` |
| `extract_from_web_text` | `(self, text: str) → List[Entity]` |

**Function:** `get_entity_parser() → EntityParser` (singleton)

**Constants:** `_BRAND_MAP` (~80 entries), `_BRAND_TYPO_ALIASES` (frozenset), `_BRAND_RE`, `_MODEL_PATTERNS` (4 compiled), `_ATTRIBUTE_MAP` (~40 entries), `_ATTRIBUTE_RE`, `_SPEC_PATTERNS` (5 patterns for RAM/STORAGE/BATTERY/DISPLAY/PRICE)

---

### A4. query_rewriter.py

> 263 lines. Rewrites user queries into multi-query search plans.

**Imports:** `re`, `logging`, `typing`; internal: `lina.models.datatypes.QueryPlan`, `lina.core.entity_parser.get_entity_parser`

**Class:** `QueryRewriter`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, max_queries=5, min_queries=3)` |
| `rewrite` | `(self, query: str) → QueryPlan` |
| `_parse_entities` | `(self, query)` |
| `_remove_fillers` | `(self, query) → str` |
| `_translate_to_en` | `(self, query) → str` |
| `_detect_intent_hint` | `(self, query) → str` |
| `_get_intent_suffix` | `(self, query) → str` |

**Function:** `get_query_rewriter() → QueryRewriter` (singleton)

**Constants:** `_RU_EN` (dict, ~50 RU→EN tech translations), `_FILLERS` (set, ~50 filler words), `_INTENT_SUFFIXES` (dict)

---

### A5. web_search_engine.py

> 1510 lines. Multi-backend web search with weather/currency support.

**Imports:** `re`, `subprocess`, `json`, `logging`, `time`, `threading`, `warnings`, `dataclasses`, `typing`, `urllib.parse`, `urllib.request`, `html.parser`; optional: `duckduckgo_search`

**Dataclasses:**
- `SearchResult` — title, url, snippet, relevance
- `WebSearchResponse` — success, query, results, summary, source, error, attempts, elapsed_ms
- `WeatherData` — city, temperature, description, humidity, wind, raw_text, source

**Class:** `_HTMLTextExtractor(HTMLParser)` — SKIP_TAGS, handle_starttag, handle_endtag, handle_data, get_text

**Class:** `WebSearchEngine`

| Category | Methods |
|----------|---------|
| Config | `CURL_TIMEOUT=12`, `MAX_RETRIES=2`, `RELEVANCE_THRESHOLD=0.3`, `MAX_FETCH_PAGES=3`, `MAX_SUMMARY_CHARS=3000`, `CACHE_TTL=300`, `CACHE_MAX_SIZE=128`, `RATE_LIMIT_DELAY=1.0` |
| Public | `search(query) → WebSearchResponse`, `fetch(url, max_length=50000) → Dict`, `get_stats()`, `set_web_capable(capable)`, `cache_clear()` |
| Query | `_clean_search_query(query)`, `_extract_key_terms(query)`, `_results_match_query(query, results)` |
| Backends | `_search_brave(query)`, `_search_ddgs_library(query)`, `_search_duckduckgo(query)`, `_search_duckduckgo_html(query)`, `_search_searxng(query)`, `_search_wikipedia(query)` |
| HTML | `_parse_brave_html(html)`, `_parse_ddg_lite(html)`, `_parse_ddg_html(html)`, `_decode_html(raw)` |
| Special | `_handle_weather(query, start)`, `_handle_currency(query, start)`, `_handle_general_search(query, start)` |
| Weather | `_try_wttr_json(city)`, `_try_wttr_text(city)`, `_try_open_meteo(city)`, `_extract_city(query)`, `_transliterate(text)` |
| Currency | `_try_exchange_rate(query)` |
| Ranking | `_rank_results(query, results)`, `_fetch_and_summarize(query, results)`, `_validate_response(summary)` |
| Cache | `_cache_get(query)`, `_cache_put(query, resp)`, `_rate_wait(engine_name)` |

**Function:** `get_web_search_engine() → WebSearchEngine` (thread-safe singleton)

---

### A6. fact_pipeline.py

> 855 lines. Full fact extraction → verification → anti-hallucination pipeline.

**Imports:** `re`, `logging`, `dataclasses`, `typing`, `collections`

**Dataclasses:**
- `Fact` — subject, predicate, value, source_urls, source_count, confidence
- `FactSet` — subject, facts, total_sources, confidence, raw_source_count

**Classes:**

| Class | Key Methods |
|-------|-------------|
| `FactExtractor` | `extract(text, source_url, subject) → List[Fact]`, `_extract_kv()`, `_extract_sentences()`, `_deduplicate()` |
| `FactVerifier` | `verify(all_facts: Dict) → FactSet`, `_verify_predicate()`, `_normalize_value()`, `_unique_domains()`, `_compute_confidence()`; constants: `_TRUSTED_DOMAINS` (~17), `_UNIT_NORMALIZE` |
| `AntiHallucinationGuard` | `check(answer, fact_set) → Tuple[str, List[str]]`, `_extract_claims()`, `_is_supported()`, `_generate_from_facts()` |
| `ConfidenceScorer` | `score(source_count, domain_scores, keyword_match_ratio, fact_overlap) → float`, `should_generate()`, `format_warning()`; constant: `MIN_CONFIDENCE=0.40` |
| `FactPipeline` | `process(web_summary, results, subject) → FactSet`, `check_answer(answer, fact_set)`, `compute_confidence()` |

**Function:** `get_fact_pipeline() → FactPipeline` (singleton)

---

### A7. fact_extractor.py

> 483 lines. Extracts structured facts from passages using regex patterns.

**Imports:** `re`, `logging`, `typing`; internal: `lina.models.datatypes.Fact`, `lina.models.datatypes.Passage`

**Class:** `FactExtractor`
| Method | Signature |
|--------|-----------|
| `extract_from_passages` | `(self, passages: List[Passage], subject: str) → List[Fact]` |
| `_extract_from_text` | `(self, text: str, source_url: str, subject: str) → List[Fact]` |

**Functions:** `_normalize_table_text(text)`, `_collapse_multiline_cells(text)`, `_normalize_value(value)`, `_is_meaningful_kv(label, value)`, `get_fact_extractor()` (singleton)

**Constants:** `_SPEC_PATTERNS` (20+ tuples: predicate/pattern/group_idx for processor, RAM, storage, battery, display, camera, OS, charging, refresh rate, resolution, weight, dimensions, protection, price, GPU), `_KV_RE`, `_TABLE_HEADERS` (frozenset), `_TABLE_SUBLABELS` (frozenset)

---

### A8. fact_aggregator.py

> 189 lines. Merges duplicate facts from multiple sources.

**Imports:** `re`, `logging`, `collections.defaultdict`, `typing`; internal: `lina.models.datatypes.Fact`, `lina.models.datatypes.FactSet`

**Class:** `FactAggregator`
| Method | Signature |
|--------|-----------|
| `aggregate` | `(self, facts: List[Fact], subject: str) → FactSet` |
| `_norm_key` | `(self, predicate: str) → str` |
| `_norm_value` | `(self, value: str) → str` |

**Constants:** `BOOST_2_SOURCES = 0.15`, `BOOST_3_SOURCES = 0.25`

**Function:** `get_fact_aggregator()` (singleton)

---

### A9. fact_verifier.py

> 236 lines. Cross-validates facts against multiple sources.

**Imports:** `re`, `logging`, `typing`; internal: `lina.models.datatypes.Fact`, `lina.models.datatypes.FactSet`

**Class:** `FactVerifier`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, min_confidence=0.40, require_multi_source=False)` |
| `verify` | `(self, fact_set: FactSet) → FactSet` |
| `_resolve_contradictions` | `(self, facts)` |
| `_is_meaningful` | `(self, f)` |
| `_unique_domains` | `(self, facts)` |

**Function:** `get_fact_verifier()` (singleton)

---

### A10. context_budget.py

> 399 lines. Token budget management for LLM prompts.

**Imports:** `logging`, `dataclasses`, `typing`

**Constants:**
| Constant | Value |
|----------|-------|
| `MAX_HISTORY_TOKENS` | 1500 |
| `MAX_RAG_TOKENS` | 1000 |
| `SYSTEM_PROMPT_LIMIT` | 600 |
| `SAFETY_MARGIN` | 64 |
| `MIN_GENERATION_THRESHOLD` | 64 |
| `MIN_USEFUL_TOKENS` | 16 |
| `CHARS_PER_TOKEN` | 2.2 |

**Dataclass:** `BudgetResult`
- Fields: prompt, max_tokens, prompt_tokens, total_budget, n_ctx, history_trimmed, rag_trimmed, budget_constrained, history_entries_kept, rag_tokens_original, rag_tokens_final
- Property: `fits → bool`
- Method: `to_dict() → dict`

**Class:** `HeuristicTokenizer` — `tokenize(data) → list`

**Class:** `ContextBudgetManager`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, llm, n_ctx=4096, ...)` |
| `count` | `(self, text: str) → int` |
| `build_prompt` | `(self, system_prompt, history, rag_context, user_input, max_tokens) → Tuple[str, int]` |
| `build_prompt_detailed` | `(...) → BudgetResult` |
| `_assemble` | `(...)` |
| `_trim_text_to_tokens` | `(self, text, max_tokens)` |
| `_trim_text_by_ratio` | `(self, text, ratio)` |
| `_trim_history` | `(self, history, max_tokens)` |

---

### A11. main_pipeline.py

> 1081 lines. 14-step deterministic pipeline — the core orchestrator.

**Imports:** `re`, `time`, `hashlib`, `logging`, `threading`, `typing`, `dataclasses`; internal (lazy): `IntentRouter`, `PostProcessor`, `ResponseValidator`, `ConfigManager`

**Dataclasses:**
- `FinalResponse` — text, status, source
- `PipelineContext` — ~40 fields covering all 14 steps

**Class:** `MainPipeline`
| Category | Methods |
|----------|---------|
| Setup | `__init__(pipeline_config)`, `set_llm_executor(fn)`, `set_tool_executor(fn)`, `set_rag_executor(fn)`, `set_diag_executor(fn)`, `set_web_executor(fn)` |
| Entry | `process_request(user_input) → FinalResponse` |
| Steps | `_step_01_runtime_snapshot`, `_step_02_intent_classification`, `_step_03_priority_resolution`, `_step_04_execution_planning`, `_step_05_integrity_precheck`, `_step_06_intent_lock`, `_step_07_execution`, `_step_08_post_processing`, `_step_09_production_guard`, `_step_10_response_validation`, `_step_11_degradation_handling`, `_step_12_drift_detection`, `_step_13_budget_update`, `_step_14_trace_finalization` |
| Helpers | `_handle_system_command`, `_is_followup_query`, `_extract_topic`, `_wire_system_control`, `_get_full_status`, `_get_last_stage_timings`, `get_stats`, `get_module` |

**Top-level verification functions:**
- `verify_pipeline_order()`
- `verify_single_entry_point()`
- `verify_all_modules_isolated()`

---

### A12. execution_orchestrator.py

> Deterministic execution plans with hash verification.

**Imports:** `hashlib`, `json`, `logging`, `threading`, `dataclasses`, `typing`

**Dataclasses:**
- `ExecutionStep`
- `ExecutionPlan` — methods: `__post_init__`, `_compute_hash`, `to_dict`, `is_multi_step`

**Class:** `ExecutionOrchestrator`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self)` |
| `create_plan` | `(...)` |
| `create_multi_step_plan` | `(...)` |
| `verify_determinism` | `(...)` |

---

### A13. tool_engine.py

> Safe tool execution with output formatting.

**Imports:** `re`, `json`, `logging`, `threading`, `dataclasses`, `typing`

**Dataclass:** `ToolResult` — success, output, error, truncated, tool_name

**Class:** `ToolEngine`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, max_output_tokens=300)` |
| `register` | `(self, name, handler, safe_mode_allowed)` |
| `set_allowed_tools` | `(self, tools)` |
| `execute` | `(self, name, args, ...)` |
| `_strip_control_chars` | `(self, args)` |
| `_strip_control_chars_value` | `(self, value)` |
| `_format_output` | `(self, raw)` |

---

### A14. tools.py

> Built-in tool registry (file ops, shell, search).

**Imports:** `json`, `os`, `re`, `shlex`, `shutil`, `subprocess`, `logging`, `threading`, `time`, `dataclasses`, `typing`

**Dataclass:** `ToolResult`

**Class:** `ToolRegistry`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self)` |
| `register` | `(self, name, description, handler, ...)` |
| `get_tools_prompt` | `(self) → str` |
| `execute` | `(self, name, arguments) → ToolResult` |
| `tool_names` | `→ List[str]` (property) |
| `_register_builtins` | `(self)` |

---

### A15. cli.py

> Command-line argument parsing and entry point.

**Imports:** `argparse`, `logging`, `os`, `re`, `sys`, `dataclasses`, `typing`; internal (lazy): `MainPipeline`, `system_interaction`

**Dataclass:** `LinaArgs`

**Functions:**
| Function | Signature |
|----------|-----------|
| `_get_version` | `()` |
| `build_parser` | `() → argparse.ArgumentParser` |
| `parse_args` | `(argv) → LinaArgs` |
| `_setup_logging` | `(verbose)` |
| `_create_pipeline` | `()` |
| `_route_via_governance` | `(text, pipeline, source)` |
| `main` | `(argv) → int` |

---

### A16. repl.py

> Interactive REPL session.

**Imports:** `sys`, `logging`, `typing`; internal: `lina.core.output.SafePrinter`, `lina.core.output.get_printer`, `lina.shell.commander.Commander`

**Class:** `REPLSession`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, ...)` |
| `is_running` | `(self)` |
| `_route_via_governance` | `(self, text)` |

---

### A17. bootstrap.py

> Startup: faulthandler, signal handlers, safe imports.

**Imports:** `sys`, `os`, `signal`, `logging`, `faulthandler`, `typing`

**Functions:**
| Function | Signature |
|----------|-----------|
| `enable_faulthandler` | `()` |
| `setup_signal_handlers` | `(cleanup_fn)` |
| `safe_import` | `(module_path, fallback)` |
| `bootstrap` | `()` |

---

## B. llm/

### B1. engine.py

> 1393 lines. Dual-model LLM engine (mini 3B + full 7B+), lazy loading, caching.

**Imports:** `json`, `os`, `time`, `hashlib`, `gc`, `logging`, `threading`, `pathlib`, `typing`, `re`; internal: `lina.config (config, ModelProfile)`, `lina.llm.token_budget (TokenBudget, BudgetReport)`, `lina.core.output (get_printer)`, `lina.core.context_budget (ContextBudgetManager, SAFETY_MARGIN)`; third-party (optional): `llama_cpp`

**Constants:** `MAX_GENERATION_TOKENS = 1024`, ~10 precompiled regex cleaning patterns

**Class:** `ResponseCache`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self)` |
| `_load` / `_save` | `(self)` |
| `_make_key` | `(self, query, context, session_id, tier, intent)` |
| `get` | `(...)` |
| `put` | `(...)` |
| `clear` | `(self)` |

**Class:** `QueryClassifier`
| Attribute / Method | Details |
|--------------------|---------|
| `STICKY_SECONDS` | 120 |
| `__init__` | `(self)` |
| `record` | `(self, tier)` |
| `classify` | `(self, query, context) → str` |
| `_FULL_PATTERNS` | ~20 compiled regexes |
| `_MINI_PATTERNS` | ~18 compiled regexes |

**Class:** `_LoadedModel`
- Fields: model, profile, tier
- Methods: `touch()`, `@property idle_seconds`

**Class:** `LLMEngine`
| Category | Methods |
|----------|---------|
| Properties | `is_loaded`, `active_tier`, `active_profile` |
| Loading | `_get_profile(tier)`, `_check_llama_available()`, `_detect_gpu_layers()`, `load(tier)`, `_load_locked(tier)`, `unload()`, `_unload_internal()`, `_check_resources(profile)`, `check_idle_unload()` |
| Generation | `generate(query, context, ...) → str`, `_clean_answer(text)`, `generate_stream(query, ...) → Generator` |
| Prompt | `_prepare_prompt(...)`, `_budget_prompt(...)`, `_assemble_prompt(...)`, `_build_prompt(query, context)`, `_build_runtime_section()`, `_get_runtime_info_fallback()` |
| Budget | `last_budget_report`, `_log_budget(report)`, `_log_token_usage(...)` |
| Status | `clear_cache()`, `get_status()`, `format_status()`, `get_token_metrics()` |

---

### B2. mini_engine.py

> Lightweight 3B model engine with tool calling.

**Imports:** `gc`, `json`, `logging`, `os`, `re`, `time`, `pathlib`, `typing`; internal: `lina.config (config, ModelProfile, MODELS_DIR)`, `lina.core.tools (ToolRegistry, ToolResult)`, `lina.core.output (get_printer)`

**Class:** `MiniLLMEngine` (line 238)

**Functions:**
- `_safe_tool_error(error) → str`
- `_build_system_prompt(tools, system_context) → str`
- `parse_tool_call(raw_response) → Optional[Tuple[str, Dict]]`

---

### B3. token_budget.py

> Token estimation and budget calculations.

**Imports:** `logging`, `dataclasses`, `enum`, `typing`

**Enum:** `TrimStrategy(Enum)`

**Dataclass:** `BudgetReport` — methods: `is_warning`, `to_dict`

**Class:** `TokenBudget`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, chars_per_token)` |
| `estimate_tokens` | `(self, text: str) → int` |
| `calculate` | `(...)` |
| `auto_trim` | `(...)` |
| `fits_in_context` | `(...)` |
| `get_model_limits` | `(self, tier) → Dict` |

---

### B4. self_verifier.py

> Post-generation fact verification.

**Imports:** `re`, `logging`, `time`, `dataclasses`, `typing`; internal: `lina.models.datatypes (Fact, FactSet, PipelineAnswer)`

**Dataclass:** `VerificationResult` — method: `has_issues`

**Class:** `SelfVerifier`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, llm_fn)` |
| `verify` | `(...)` |
| `_parse_response` | `(self, response) → VerificationResult` |

**Function:** `get_self_verifier(llm_fn) → SelfVerifier`

---

### B5. fact_prompt.py

> LLM prompt builders for fact-grounded generation.

**Imports:** `typing`; internal: `lina.models.datatypes (Fact, FactSet, QueryUnderstanding)`

**Functions:**
- `_format_facts(facts, max_facts, ...)`
- `build_generation_prompt(...)`
- `build_verification_prompt(...)`

---

## C. pipeline/pipeline_v3.py

> V3 search-focused pipeline for web queries.

**Imports:** `logging`, `time`, `typing`; internal: `lina.models.datatypes (QueryUnderstanding, QueryPlan, SearchResult, Passage, Fact, FactSet, PipelineAnswer)`, `lina.pipeline.config (PipelineConfig, get_pipeline_config)`, `lina.core.query_understanding`, `lina.core.query_rewriter`, `lina.retrieval.parallel_search`

**Class:** `V3BypassSignal(Exception)` — `__init__(query, intent)`

**Class:** `PipelineV3`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, ...)` |
| `_init_components` | `(self)` |
| `run` | `(self, query, intent, ...) → PipelineAnswer` |
| `_search_and_answer` | `(...)` |
| `_generate_and_verify` | `(...)` |
| `_download_and_split` | `(self, results) → List[Passage]` |
| `_fetch_html` | `(self, url) → str` |
| `_should_research` | `(self, answer, fact_set) → bool` |
| `_broaden_queries` | `(...)` |
| `_finalize` | `(...)` |
| `_extract_subject` | `(self, query) → str` |
| `_no_search_answer` | `(...)` |
| `_no_results_answer` | `(...)` |
| `_snippets_to_passages` | `(self, results)` |

---

## D. shell/commander.py

> Shell command processing and builtins. 26 methods.

**Imports:** `json`, `os`, `re`, `time`, `uuid`, `collections.deque`, `pathlib`, `typing`; internal: `lina.config`, `lina.system.files.FileManager`, `lina.system.executor.CommandExecutor`, `lina.system.monitor.SystemMonitor`, `lina.system.logger`

**Class:** `CommandType` (line 88)

**Class:** `Commander`
| Category | Methods |
|----------|---------|
| Properties | `session_id`, `trace_enabled`, `runtime` |
| Core | `process(user_input) → str`, `_record(command, response, start_time)`, `_match_builtin(text)` |
| Handlers | `_handle_meta(action)`, `_handle_builtin(action, args)`, `_handle_system_command(command)`, `_handle_system_command_governed(command)`, `_handle_chain(chain)`, `_handle_macro(macro)` |
| LLM | `_v2_llm_handler(prompt, tier)`, `chain_executor`, `_handle_llm_query(query)`, `_handle_llm_query_v3(query)`, `_handle_llm_query_legacy(query)`, `_print_trace(...)` |
| Utility | `_format_file_list(items)`, `_get_help()`, `_get_preinstall_status()`, `_get_full_status()` |

---

## E. gui/app.py

> Qt GUI application entry point. 32 functions.

**Imports:** `sys`, `logging`, `re`, `threading`, `signal`, `typing`; internal (lazy): `lina.gui (get_qt_modules, is_gui_available)`, `lina.gui.chat.ChatController`, `lina.gui.settings`, `lina.gui.tray (TrayIconController, TrayConfig)`, `lina.gui.main_window`

**Top-level functions:**
| Function | Purpose |
|----------|---------|
| `run_gui(argv)` | Main GUI entry |
| `_setup_pipeline_handler(controller)` | Wire pipeline to UI |
| `_get_engine()` / `_get_system_context()` / `_get_retriever()` | Lazy component init |
| `_try_diagnostics(text)` / `_get_web_search()` / `_get_fact_pipeline()` | Feature getters |
| `_get_conversation_state()` | Memory getter |
| `_try_web_search(text, intent)` / `_try_install_search(text, intent, decision)` | Search dispatchers |
| `_ensure_loaded()` / `_is_web_followup(text)` | State checks |
| `_build_context(text)` / `_save_last_intent(intent, query)` | Context management |
| `_execute_commands(response, executor, intent)` | Command execution |
| `_get_history(max_pairs)` | Conversation history |
| `_is_llm_deflecting(response)` / `_is_vague_answer(response, query)` | Response quality checks |
| `_clean_web_for_context(summary)` | Text cleaning |
| `_web_search_fallback(text, full_context)` | Fallback handler |
| `_fix_truncated_response(response)` | Response repair |
| `_is_garbage_install_target(app_name)` / `_find_install_app_in_history()` | Install helpers |
| `_enrich_followup(text)` / `_handle_v3(text)` / `_handler(text)` | Request handlers |
| `_stream_handler(text, cancel_flag)` | Streaming handler |
| `_preload_model()` | Async model preload |
| `_open_settings(window)` / `_quit_app(window, app)` / `main()` | App lifecycle |

---

## F. system/

### F1. executor.py

**Class:** `CommandExecutor`
- `__init__()`
- `execute(...)` — runs shell commands with sandboxing
- `execute_script(...)` — runs script files

### F2. sandbox.py

**Class:** `SubprocessSandbox`
- `__init__()`
- `is_safe(command) → tuple` — safety check before execution
- `execute(...)` — sandboxed subprocess run

### F3. monitor.py

**Class:** `SystemMonitor` — 19 methods
| Method | Signature |
|--------|-----------|
| `__init__` | `()` |
| `get_system_info` | `()` |
| `get_cpu_usage` / `get_memory_usage` | `()` |
| `get_top_processes` | `(n)` |
| `check_resources_ok` | `(max_ram_mb, max_cpu)` |
| `format_status` / `format_extended_status` | `()` |
| `find_llm_processes` / `set_llm_pid` / `get_llm_memory_mb` | LLM process tracking |
| `is_overloaded` | `(ram_threshold_pct, cpu_threshold)` |
| `start_watchdog` / `stop_watchdog` | Background monitoring |
| `_get_info_psutil` / `_get_info_proc` | Backend implementations |
| `_read_cpu_proc` / `_read_memory_proc` | /proc readers |

### F4. package_manager.py

**Class:** `PackageManager` — 20+ methods
| Category | Methods |
|----------|---------|
| Properties | `distro_info`, `manager_name` |
| Public | `search(query, limit)`, `info(name)`, `is_installed(name)`, `list_installed(limit)`, `install(name)`, `remove(name)`, `update()`, `check_updates()`, `list_orphans()` |
| Flatpak | `flatpak_search(query)`, `flatpak_list()` |
| Backends | `_pacman_search`, `_apt_search`, `_dnf_search`, `_zypper_search`, `_pacman_info`, `_apt_info` |

---

## G. rag/

### G1. retriever.py

**Class:** `KnowledgeRetriever` — 15 methods
| Method | Signature |
|--------|-----------|
| `__init__` | `()` |
| `_get_store` / `_try_legacy_load` | Store initialization |
| `reload_index` | `()` |
| `search` | `(...)` |
| `build_context` | `(...)` |
| `_filter_results` / `_rerank` / `_deduplicate` | Result processing |
| `_context_top_k` | `(max_context_length)` |
| `_format_source_label` | `(result, meta)` |
| `has_documents` | `()` |
| `get_categories` / `get_distros` | Metadata queries |

### G2. indexer.py

| Class | Methods |
|-------|---------|
| `TextChunker` | `split(...)` |
| `DocumentLoader` | `load_directory(...)` |
| `TFIDFIndex` | `build(...)`, `search(...)`, `save(...)`, `load(...)`, `_load_legacy(...)`, `@property chunks`, `@property vocabulary` |
| `KnowledgeIndexer` | `index_documents(...)`, `get_store(...)`, `get_stats(...)`, `clear(...)`, `_ensure_store(...)` |

### G3. vectorstore.py

**Class:** `VectorStore` — 9 methods
| Method | Signature |
|--------|-----------|
| `__init__` | `()` |
| `build` | `(chunks, metadata)` |
| `search` | `(...)` |
| `save` / `load` | `(path)` |
| `add_chunks` | `(new_chunks, new_metadata)` |
| `total_chunks` / `vocab_size` | Properties |

---

## H. voice/

### H1. stt.py

**Enum:** `STTBackend(Enum)` — sounddevice, pyaudio, arecord  
**Dataclasses:** `STTConfig`, `AudioChunk`

**Class:** `AudioRecorder` — `start`, `stop`, `is_recording`, `add_chunk`, `record_seconds`, `_record_sounddevice`, `_record_pyaudio`, `_record_arecord`, `_build_wav`

**Class:** `SpeechToText` (line 286)

### H2. tts.py

**Enum:** `TTSBackend(Enum)` — piper, espeak, festival  
**Dataclasses:** `VoiceInfo`, `TTSConfig`

**Class:** `AudioPlayer` — `play`, `play_async`, `stop`, `is_playing`, `_build_command`

**Class:** `TextToSpeech` — `speak`, `is_available`, `get_backend`, `get_available_voices`, `_detect_backend`

### H3. pipeline.py

**Enums:** `VoicePipelineState(Enum)`, `InteractionMode(Enum)`  
**Dataclasses:** `VoicePipelineConfig`, `VoiceEvent`

**Class:** `VoicePipeline`
| Method | Signature |
|--------|-----------|
| `set_stt` / `set_tts` | Component setters |
| `set_request_handler` | Pipeline handler |
| `set_on_state_change` / `set_on_event` / `set_on_text_recognized` / `set_on_response` | Callbacks |
| `get_state` / `is_active` | State queries |
| `process_single` | Single-shot mode |
| `start_session` / `stop_session` | Continuous mode |

---

## I. diagnostics/

### I1. engine.py

**Dataclasses:**
- `StepResult`
- `DiagnosticReport` — methods: `to_dict`, `format_text`

**Class:** `DiagnosticEngine`
| Method | Signature |
|--------|-----------|
| `__init__` | `(trees_dir)` |
| `load_trees` / `_load_file` / `load_tree_from_dict` | Tree loading |
| `match_problem` | `(user_input)` |
| `get_tree_ids` / `get_tree` / `get_categories` / `list_trees` | Navigation |
| `run_diagnostic` | `(tree_id) → DiagnosticReport` |
| `_execute_check` | `(command, timeout)` |
| `_check_pattern` | `(output, pattern)` |
| `_calc_confidence` | `(report)` |
| `get_report` | `()` |
| `collect_system_context` | `(max_lines)` |
| `build_llm_prompt` | `(user_input, context)` |

### I2. session.py

**Enum:** `SessionState(Enum)`  
**Dataclass:** `StepSnapshot` — method: `format_text`

**Class:** `DiagnosticSession`
| Method / Property | Signature |
|-------------------|-----------|
| `__init__` | `(engine)` |
| `state` / `tree_id` / `progress` / `steps_completed` / `total_steps` | Properties |
| `start` / `begin` | `(problem_text)` |
| `step_forward` | `()` |
| `_finalize` | `()` |
| `get_report` / `get_snapshots` / `get_step_results` / `get_alternatives` | Getters |
| `_suggest_alternatives` | `(text)` |
| `_make_fallback_report` | `(problem_text)` |
| `cancel` | `()` |

---

## J. governance/

### J1. policy_engine.py

**Enum:** `PolicyDecision(str, Enum)`  
**Dataclasses:** `PolicyConfig`, `PolicyCheckResult`

**Class:** `PolicyEngine`
| Method | Signature |
|--------|-----------|
| `__init__` | `(config)` |
| `_load_toml` / `_apply_toml` / `_write_default_toml` / `_get_policy_path` | Config loading |
| `reload` | `()` |
| `check` | `(action_id, ...)` |
| `check_internet` | `(url)` |
| `_rate_limited` | `(action_id)` |
| `_result` | `(action_id, decision, ...)` |
| `config` | Property |
| `get_audit_log` | `(limit)` |
| `check_content_safety` | `(...)` |
| `get_stats` | `()` |

### J2. action_registry.py

**Enums:**
- `ActionRisk(str, Enum)` — risk levels
- `ActionCategory(str, Enum)` — action categories
- `ExecStatus(str, Enum)` — execution statuses

**Dataclasses:**
- `ActionDef` — method: `to_dict`
- `ActionResult` — method: `to_dict`

**Class:** `ActionRegistry`
| Method | Signature |
|--------|-----------|
| `__init__` | `()` |
| `register` / `unregister` / `get` / `has` | CRUD |
| `list_actions` | `(category, domain)` |
| `validate_action` | `(action_id, ...)` |
| `prepare` / `execute` | `(action_id, ...)` |
| `_build_command` / `_build_command_raw` | Command construction |
| `_run` | `(cmd, timeout)` |
| `_log_audit` | `(action_id, status, ...)` |

### J3. state_machine.py

**Enums:**
- `InstallerState(str, Enum)` — installer states
- `RuntimeState(str, Enum)` — runtime states

**Dataclasses:** `Transition`, `StateEvent`

**Class:** `StateMachine`
| Method / Property | Signature |
|-------------------|-----------|
| `__init__` | `(name, initial_state)` |
| `add_transition` / `add_transitions` | Transition setup |
| `on_enter` / `on_exit` | State callbacks |
| `set_transition_callback` | `(from_state, to_state, ...)` |
| `transition` | `(to_state, reason)` |
| `force_state` | `(state, reason)` |
| `state` / `name` | Properties |
| `can_transition` / `allowed_transitions` | Query |
| `time_in_state` / `get_history` | Observability |

---

## K. config.py

> 349 lines. Single global configuration singleton.

**Imports:** `os`, `pathlib`, `dataclasses`, `typing`

**Path Constants:**
- `BASE_DIR` — project root
- `KNOWLEDGE_DIR`, `CHROMA_DIR`, `CACHE_DIR`, `LOGS_DIR`, `MODELS_DIR`

**Dataclasses (all `@dataclass`):**

| Dataclass | Key Fields |
|-----------|------------|
| `ResourceLimits` | max_ram_mb=6144, max_cpu_percent=60, shell_max_ram_mb=100, subprocess_timeout=60, llm_timeout=120 |
| `ModelProfile` | model_path, n_ctx=2048, n_threads=4, n_gpu_layers=0, temperature=0.7, max_tokens=512, top_p=0.9, repeat_penalty=1.1, estimated_ram_mb=0 |
| `LLMConfig` | mini/full (ModelProfile), auto_unload, idle_unload_seconds=300, system_prompt; property: `model_path`; method: `get_profile(tier)` |
| `RAGConfig` | collection_name, chroma_persist_dir, chunk_size=500, chunk_overlap=50, top_k=3, min_relevance_score=0.15 |
| `CacheConfig` | enabled, cache_file, max_entries=200, ttl_seconds=3600 |
| `SecurityConfig` | blocked_commands, allowed_dirs, max_file_size_mb=50; methods: `is_command_safe`, `is_path_allowed` |
| `WebConfig` | enabled, host, port=8585, allow_commands |
| `NotifyConfig` | enabled, on_llm_load, on_chain_complete, on_error, on_overload |
| `ChainConfig` | max_steps=10, step_timeout=120, save_macros |
| `ToolsConfig` | web_search_enabled, ide_integration, api_enabled |
| `LearningConfig` | collect_fragments, min_quality=0.5, auto_export, export_threshold=50 |
| `PreinstallConfig` | enabled, auto_scan, save_hw_report, faq_file |
| `CVConfig` | enabled, screenshot_interval=5, ocr_lang, auto_detect, screenshots_dir, max_screenshots=100 |
| `PipelineConfig` | pipeline_version="legacy", safe_mode, initial_mode="normal", router_confidence_threshold=0.5, session_budget_tokens=100000, avg_response_threshold=400, budget_window_size=20, max_regeneration_attempts=1, validation_threshold=0.5, degradation_failure_streak=3, guard_block_on_leak/violation, trace_max_entries=50, trace_enabled, enable_tool/rag/web/cv, step_memory_size=20, consistency_threshold=0.5 |
| `LinaConfig` | Aggregates all above + verbose, language="ru"; method: `_init_system_prompt` |

**Singleton:** `config = LinaConfig()`

---

## L. processing/html_cleaner.py

> HTML → clean text for passage extraction.

**Functions:**
| Function | Signature |
|----------|-----------|
| `is_bot_protection_page` | `(text: str) → bool` |
| `clean_page` | `(html: str, max_length: int = 80000) → str` |
| `_remove_boilerplate` | `(text: str) → str` |

---

## M. embeddings/semantic_ranker.py

> 91 lines. Passage ranking by semantic similarity.

**Imports:** `logging`, `time`, `typing`; internal: `lina.models.datatypes.Passage`, `lina.embeddings.embedding_model.get_embedding_model`

**Class:** `SemanticRanker`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self)` — initializes `self._model = get_embedding_model()` |
| `rank` | `(self, passages: List[Passage], query: str, top_k: int = 10, min_similarity: float = 0.15) → List[Passage]` |

**Function:** `get_semantic_ranker() → SemanticRanker` (singleton)

---

## N. memory/

### N1. conversation_state.py

> 186 lines. Multi-turn conversation tracking with pronoun resolution.

**Imports:** `logging`, `threading`, `time`, `typing`; internal: `lina.models.datatypes.ConversationTurn`, `lina.models.datatypes.Fact`, `lina.models.datatypes.IntentType`

**Class:** `ConversationState`
| Method / Property | Signature |
|-------------------|-----------|
| `__init__` | `(self, max_turns: int = 10)` |
| `add_turn` | `(self, turn: ConversationTurn) → None` |
| `current_topic` | `→ str` (property) |
| `active_entities` | `→ List[str]` (property) |
| `last_turn` | `→ Optional[ConversationTurn]` (property) |
| `turn_count` | `→ int` (property) |
| `get_recent_facts` | `(self, topic: str = "") → List[Fact]` |
| `resolve_pronoun_subject` | `(self, query: str) → str` |
| `build_context_hint` | `(self) → str` |
| `clear` | `(self) → None` |
| `to_dict` | `(self) → dict` |

### N2. fact_store.py

> 270 lines. Persistent fact cache with TTL, backed by JSON.

**Imports:** `json`, `logging`, `os`, `re`, `threading`, `time`, `pathlib`, `typing`; internal: `lina.models.datatypes.Fact`

**Class:** `FactStore`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, cache_dir="", ttl_seconds=3600, max_entities=200)` |
| `get` | `(self, entity: str) → List[Fact]` |
| `put` | `(self, entity: str, facts: List[Fact]) → None` |
| `_is_valid_fact` | `(f: Fact) → bool` (static) |
| `has` | `(self, entity: str) → bool` |
| `remove` / `clear` | Entity management |
| `save` | `(self) → None` (write to disk) |
| `entity_count` | `→ int` (property) |
| `_load` / `_evict_if_needed` | Internal lifecycle |
| `_norm` | `(entity: str) → str` (static) |
| `_serialize_facts` / `_deserialize_facts` | JSON serialization (static) |

---

## O. retrieval/

### O1. parallel_search.py

> 354 lines. Concurrent multi-engine web search.

**Imports:** `json`, `logging`, `os`, `re`, `subprocess`, `time`, `concurrent.futures (ThreadPoolExecutor, as_completed)`, `typing`, `urllib.parse`; internal: `lina.models.datatypes.SearchResult`

**Abstract Class:** `SearchEngine` — `name: str`, `search(query, max_results=10) → List[SearchResult]`

**Engine Implementations:**

| Class | Inherits | Key Methods |
|-------|----------|-------------|
| `DuckDuckGoEngine(SearchEngine)` | SearchEngine | `__init__`, `_get_engine`, `search` |
| `WikipediaEngine(SearchEngine)` | SearchEngine | `search` — calls Wikipedia API |
| `BraveEngine(SearchEngine)` | SearchEngine | `__init__`, `@property available`, `search` — requires `BRAVE_API_KEY` |
| `SearXNGEngine(SearchEngine)` | SearchEngine | `__init__`, `@property available`, `search` — requires `SEARXNG_URL` |

**Class:** `ParallelSearch`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, max_workers=4, timeout=15.0)` |
| `_init_engines` | `() → List[SearchEngine]` |
| `engine_names` | `→ List[str]` (property) |
| `search` | `(self, queries: List[str], max_results=10, ...) → Dict[str, List[SearchResult]]` |
| `_safe_search` | `(self, engine, query, max_results) → Tuple` |

**Functions:** `_strip_html(text) → str`, `get_parallel_search() → ParallelSearch` (singleton)

### O2. result_merger.py

> 159 lines. Reciprocal Rank Fusion (RRF) merging.

**Imports:** `logging`, `re`, `collections.defaultdict`, `typing`, `urllib.parse`; internal: `lina.models.datatypes.RetrievalResult`, `lina.models.datatypes.SearchResult`

**Class:** `ResultMerger`
| Attribute / Method | Details |
|--------------------|---------|
| `RRF_K` | 60 (standard RRF constant) |
| `merge` | `(self, engine_results: Dict[str, List[SearchResult]], ...) → RetrievalResult` |
| `_normalize_url` | `(url: str) → str` (static) |
| `_is_spam` | `(cls, norm_url: str) → bool` (classmethod) |

**Function:** `get_result_merger() → ResultMerger` (singleton)

### O3. domain_ranker.py

> 197 lines. Multi-signal result re-ranking by domain reputation.

**Imports:** `re`, `logging`, `time`, `typing`, `urllib.parse`; internal: `lina.models.datatypes.SearchResult`

**Constants:** `_DOMAIN_SCORES` (Dict[str, float], ~35 entries — gsmarena.com=0.95, stackoverflow.com=0.85, habr.com=0.78, etc.)

**Class:** `DomainRanker`
| Attribute / Method | Details |
|--------------------|---------|
| `W_DOMAIN` / `W_KEYWORD` / `W_FRESH` / `W_DIVERSITY` / `W_POSITION` | 0.35 / 0.30 / 0.15 / 0.10 / 0.10 |
| `rank` | `(self, results: List[SearchResult], query: str, ...) → List[SearchResult]` |
| `_extract_domain` | `(url: str) → str` (static) |
| `_freshness_score` | `(text: str) → float` (static) |

**Function:** `get_domain_ranker() → DomainRanker` (singleton)

---

## P. safety/validator.py

> 454 lines. Pre-execution command safety analysis.

**Imports:** `logging`, `re`, `typing`; internal: `lina.safety.models (SafetyVerdict, RiskLevel, ThreatType, SecurityPattern, get_all_patterns, SAFE_COMMAND_PREFIXES)`

**Constant:** `SAFETY_ANALYSIS_PROMPT` — Russian-language LLM prompt for risk assessment (0–5 scale)

**Class:** `SafetyValidator`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, patterns=None, llm_fn=None, extra_blocked=None)` |
| `validate` | `(self, command: str, ...) → SafetyVerdict` |
| `_check_whitelist` | `(self, command) → Optional[SafetyVerdict]` |
| `_check_patterns` | `(self, command) → Dict[str, Any]` |
| `_analyze_with_llm` | `(self, command, ...) → Dict` |
| `_parse_llm_response` | `(self, response) → Dict` |
| `_combine_results` | `(self, pattern_result, llm_result, ...) → SafetyVerdict` |
| `_make_verdict` | `(self, ...)` |
| `validate_batch` | `(self, commands, ...) → List[SafetyVerdict]` |
| `get_stats` / `reset_stats` | Statistics |
| `add_pattern` | `(self, pattern: SecurityPattern) → None` |
| `add_safe_prefix` | `(self, prefix: str) → None` |

**Supporting models (safety/models.py):**

| Type | Name |
|------|------|
| Enum | `RiskLevel(IntEnum)` — 0–5 scale |
| Enum | `ThreatType(str, Enum)` |
| Dataclass | `SecurityPattern` — method: `matches(command) → bool` |
| Dataclass | `SafetyVerdict` — methods: `to_dict`, `is_blocked`, `needs_confirmation` |
| Dataclass | `PolicyDecision` — method: `to_dict` |
| Function | `get_all_patterns() → List[SecurityPattern]` |

---

## Q. security/input_validator.py

> 353 lines. Zero-trust input validation layer.

**Imports:** `logging`, `re`, `unicodedata`, `typing`

**Constants:**
| Constant | Value |
|----------|-------|
| `MAX_INPUT_LENGTH` | 4096 |
| `MAX_DOMAIN_LENGTH` | 64 |
| `MAX_ACTION_LENGTH` | 128 |
| `MAX_SOURCE_LENGTH` | 32 |
| `MAX_PARAMS_DEPTH` | 4 |
| `MAX_PARAMS_KEYS` | 32 |
| `MAX_PARAM_VALUE_LENGTH` | 1024 |
| `VALID_SOURCES` | frozenset: ui, cli, dbus, hotkey, internal, test, gui, repl |
| `VALID_DOMAINS` | frozenset: service, package, network, disk, config, user, boot, display, audio, security, installer, desktop, system, safety, general, "" |
| `_CONTROL_CHARS` | regex for null/control chars |
| `_OBFUSCATION_PATTERNS` | 6 compiled regexes (base64, hex, eval, $(), backticks) |
| `_INJECTION_PATTERNS` | regex for shell metacharacters |

**Class:** `ValidationResult`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, valid: bool, reason="", ...)` |
| `__bool__` | `→ bool` |
| `__repr__` | `→ str` |

**Class:** `InputValidator`
| Method | Signature |
|--------|-----------|
| `__init__` | `(self, *, max_input_length=MAX_INPUT_LENGTH)` |
| `validate_text` | `(self, text: str) → ValidationResult` |
| `validate_domain` | `(self, domain: str) → Tuple[bool, str]` |
| `validate_source` | `(self, source: str) → Tuple[bool, str]` |
| `validate_action` | `(self, action: str) → Tuple[bool, str]` |
| `validate_params` | `(self, params: Any, *, max_depth=MAX_PARAMS_DEPTH)` |
| `validate_json_payload` | `(self, payload: str, ...)` |
| `detect_injection` | `(self, text: str) → Tuple[bool, str]` |
| `validate_confidence` | `(confidence: float) → float` (static) |

**Function:** `get_input_validator() → InputValidator` (singleton)

---

## Architecture Summary

```
User Input
  │
  ├─ CLI (cli.py) ─────┐
  ├─ REPL (repl.py) ────┤
  ├─ GUI (gui/app.py) ──┤
  └─ Voice (voice/) ────┘
                        │
              InputValidator (Q)
                        │
              IntentRouter (A1) → Intent enum
                        │
              MainPipeline (A11) [14 steps]
              ┌─────────┼─────────┐
              │         │         │
        LLMEngine    PipelineV3  Commander
           (B1)        (C)        (D)
              │         │
              │    ┌────┴────┐
              │    │ Parallel │
              │    │ Search   │ (O1)
              │    └────┬────┘
              │    ResultMerger (O2)
              │    DomainRanker (O3)
              │    SemanticRanker (M)
              │         │
              │    FactExtractor (A7)
              │    FactAggregator (A8)
              │    FactVerifier (A9)
              │         │
              │    LLM Generate + SelfVerifier (B4)
              │         │
              └────┬────┘
                   │
           SafetyValidator (P) → PolicyEngine (J1)
                   │
           CommandExecutor (F1) ← SubprocessSandbox (F2)
                   │
              Response → User
```

**Third-party dependencies:** `llama-cpp-python` (LLM), `duckduckgo-search` (optional), PyQt5/PySide6 (GUI), `sounddevice`/`pyaudio` (voice), `piper-tts`/`espeak` (TTS)

**Design patterns:** Singletons via `get_*()` functions, lazy imports, thread-safe via `threading.Lock`, dataclass-first API boundaries, no external vector DB required (custom TF-IDF VectorStore).
