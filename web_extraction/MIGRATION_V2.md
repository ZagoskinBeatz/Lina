# Dual-Mode Web Extraction Pipeline v2 — Migration Plan

## Overview

This document describes the migration from the single-mode Web Extraction
Pipeline (v1) to the dual-mode architecture (v2) with Linux troubleshooting
support.

## What Changed

### New Modules (5 files, ~2700 lines)

| Module | Lines | Purpose |
|--------|-------|---------|
| `query_classifier.py` | ~460 | Deterministic query routing: GENERAL / LINUX / ERROR |
| `linux_commands.py` | ~560 | Linux command extraction with type/risk classification |
| `solution_detector.py` | ~620 | Error detection (60+ patterns) + solution block detection |
| `error_knowledge_graph.py` | ~960 | Persistent JSON-backed database of Linux errors → solutions |
| `tests/test_dual_mode.py` | ~780 | 59 smoke + unit tests covering all new modules |

### Modified Modules

| Module | Change |
|--------|--------|
| `hybrid_ranker.py` | Added `rank_linux()` method with Linux-specific scoring bonuses |
| `source_trust.py` | Added 40+ Linux-specific domain trust entries (Arch Wiki, man7, askubuntu, etc.) |
| `__init__.py` | Extended exports for all new classes and enums |
| `query_classifier.py` | Expanded `linux_error_types` set for better coverage |

### Replaced Modules

| Old | New | Notes |
|-----|-----|-------|
| `web_pipeline.py` (v1) | `web_pipeline.py` (v2) | Backed up as `web_pipeline_v1.py` |

## Backward Compatibility

### API Stability

The v2 pipeline preserves full backward compatibility:

1. **`get_web_extraction_pipeline()`** — Same singleton function name and return type
2. **`WebExtractionPipeline.run(results, query, top_k)`** — Same signature
3. **`WebExtractionResult.passages`** — Same field, same type
4. **`WebExtractionResult.total_raw_passages`** — Same field
5. **`WebExtractionResult.total_pages_attempted`** — Same field
6. **`extract_passages()`** — Backward-compat wrapper preserved
7. **`extract_and_format()`** — Backward-compat wrapper preserved

### pipeline_v3.py Integration

Zero changes required. The v3 pipeline calls:
```python
web_result = self._web_pipeline.run(ranked, query=query, top_k=top_k)
passages = web_result.passages
```
All accessed fields exist in both v1 and v2 results.

### New Fields (additive only)

| Field | Type | Description |
|-------|------|-------------|
| `query_mode` | `QueryMode` | GENERAL/LINUX/ERROR |
| `query_classification` | `QueryClassification` | Full classification details |
| `solutions` | `List[SolutionBlock]` | Detected problem→solution blocks |
| `commands` | `List[LinuxCommand]` | Extracted Linux commands |
| `detected_errors` | `List[DetectedError]` | Error strings found in passages |
| `kg_lookup` | `LookupResult` | Error Knowledge Graph match |
| `answered_from_kg` | `bool` | Whether KG answered directly |

## Rollback Plan

If issues occur:
1. Rename `web_pipeline.py` → `web_pipeline_v2.py`
2. Rename `web_pipeline_v1.py` → `web_pipeline.py`
3. No other changes needed — v1 API is identical

## Migration Phases

### Phase 1: Deploy (Current)
- [x] All modules created and tested
- [x] 59/59 tests passing
- [x] Backward-compatible API verified
- [x] `web_pipeline_v1.py` preserved as rollback target

### Phase 2: Monitor (Next)
- [ ] Enable logging: `lina.web_extraction.query_classifier` logger
- [ ] Track query mode distribution (GENERAL vs LINUX vs ERROR)
- [ ] Monitor Error KG hit rate and `answered_from_kg` ratio
- [ ] Validate that GENERAL mode behavior is unchanged

### Phase 3: Enhance
- [ ] Tune classification thresholds based on real query data
- [ ] Expand Error KG built-in entries from 11 to 50+
- [ ] Add distro-specific solution routing
- [ ] Add Russian-language error pattern support

### Phase 4: Cleanup
- [ ] Remove `web_pipeline_v1.py` backup after 2 weeks of stable operation
- [ ] Archive test results for baseline

## Error Knowledge Graph

### Storage
- Path: `lina/cache/error_knowledge_graph.json`
- Format: JSON, atomic writes (write .tmp → rename)
- Thread-safe: `threading.Lock` protects all mutations

### Built-in Entries (11)
| ID | Error | Solutions |
|----|-------|-----------|
| `apt_unable_to_locate` | Unable to locate package | 3 |
| `apt_unmet_deps` | Unmet dependencies | 2 |
| `permission_denied` | Permission denied | 3 |
| `service_start_fail` | Failed to start service | 3 |
| `no_space` | No space left on device | 2 |
| `connection_refused` | Connection refused | 2 |
| `dns_fail` | DNS resolution failure | 3 |
| `module_not_found` | Python module not found | 3 |
| `cmd_not_found` | Command not found | 3 |
| `dpkg_interrupted` | dpkg interrupted | 1 |
| `oom_killer` | OOM killer | 2 |

### Learning
In ERROR mode, after web retrieval, the pipeline automatically:
1. Matches detected errors against KG entries
2. Creates new KG entries for unknown errors
3. Adds web-discovered solutions with `confidence × 0.5` (conservatively)
4. Persists atomically to disk

## Test Coverage

```
59 passed, 0 failed

TestQueryClassifier:          8 tests  (GENERAL/LINUX/ERROR routing, confidence)
TestLinuxCommandExtractor:    9 tests  (code blocks, inline, prompts, risk, dedup)
TestErrorDetector:            8 tests  (apt, permission, service, no_space, segfault)
TestSolutionDetector:         6 tests  (blocks, commands, steps, confidence)
TestErrorKnowledgeGraph:      9 tests  (builtin, lookup, learn, persist, stats)
TestHybridRankerLinux:        5 tests  (bonus scoring, error matching, ranking)
TestWebExtractionPipelineV2: 11 tests  (modes, KG, format, backward compat)
TestCrossModuleIntegration:   3 tests  (classifier→pipeline, detector→KG, sol→cmd)
```

## File Inventory

```
web_extraction/
├── __init__.py                    # Updated: +15 new exports
├── content_extractor.py           # Unchanged (v1)
├── semantic_chunker.py            # Unchanged (v1)
├── hybrid_ranker.py               # Modified: +rank_linux(), +_linux_bonus()
├── source_trust.py                # Modified: +40 Linux domains
├── page_processor.py              # Unchanged (v1)
├── web_pipeline.py                # ★ Rewritten: dual-mode orchestrator (v2)
├── web_pipeline_v1.py             # Backup of original pipeline
├── query_classifier.py            # ★ New: deterministic query routing
├── linux_commands.py              # ★ New: command extraction + risk
├── solution_detector.py           # ★ New: error + solution detection
├── error_knowledge_graph.py       # ★ New: persistent error→solution DB
└── tests/
    ├── __init__.py
    └── test_dual_mode.py          # ★ New: 59 tests
```
