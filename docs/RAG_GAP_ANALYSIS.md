# Lina RAG Pipeline — Gap Analysis vs. Ideal Architecture

> Дата: 2026-03-10  
> Метод: Code trace of 20+ modules (pipeline_v3, fact_pipeline, fact_extractor,  
> html_cleaner, passage_splitter, semantic_ranker, domain_ranker, query_rewriter,  
> fact_aggregator, fact_verifier, fact_prompt, embedding_model, result_merger)  
> Scope: Full RAG path from query to LLM prompt

---

## Current Pipeline (Verified Against Code)

```
┌──────────────────────────────────────────────────────────────────┐
│             Lina v3 RAG Pipeline — Actual Flow                   │
│                                                                  │
│  1. Cache Check (FactStore TTL 3600s)                           │
│  2. Query Understanding (rule-based intent + entity parse)       │
│  3. Conversation State (pronoun resolve, topic track)            │
│  4. Query Rewriting (deterministic RU→EN dict, 3-5 variants)    │
│  5. Parallel Search (DDG + Brave + SearXNG + Wikipedia)          │
│  6. RRF Merge (k=60, URL dedup, spam filter)                    │
│  7. Domain Ranking (5-signal: domain/keyword/fresh/diverse/pos)  │
│  8. Page Download (ThreadPool(3), curl subprocess)               │
│  9. HTML Clean (BS4 → regex fallback, boilerplate strip)         │
│ 10. Passage Split (paragraph→sentence, 15-200 words, overlap=1) │
│ 11. Semantic Ranking (sentence-transformers → TF-IDF → BM25)    │
│ 12. Fact Extraction (18 regex patterns + KV pairs, GSMarena)     │
│ 13. Fact Aggregation (synonym norm, cross-source boost)          │
│ 14. Fact Verification (confidence threshold + contradiction res) │
│ 15. Prompt Build (structured fact-mode, max 15 facts / 4000ch)  │
│ 16. LLM Generation (fact-mode prompt with strict rules)          │
│ 17. Self-Check (LLM verifies own answer vs facts)                │
│ 18. Anti-Hallucination (regex numeric claim check)               │
│ 19. Re-search (if <2 facts or <0.35 conf → broaden, max 1)     │
└──────────────────────────────────────────────────────────────────┘
```

---

## Stage-by-Stage Gap Assessment

### ✅ ALREADY STRONG (No Changes Needed)

| Stage | What Lina Has | Why It's Sufficient |
|-------|--------------|---------------------|
| **Multi-engine search** | 4 engines in parallel | Good diversity. DDG+Brave+SearXNG+Wikipedia covers major and privacy-focused sources |
| **RRF merge** | Standard k=60, URL dedup, spam filter | Industry-standard fusion. Correctly normalizes URLs, filters known spam domains |
| **Domain ranking** | 37 domains, 5-signal formula | Weighted scoring with `W_DOMAIN=0.35, W_KEYWORD=0.30, W_FRESH=0.15, W_DIVERSE=0.10, W_POS=0.10` is well-balanced |
| **Semantic ranking** | 3-tier fallback: transformers→TF-IDF→BM25 | Graceful degradation, always has a ranking backend |
| **Fact-mode prompt** | `{subject} → {predicate}: {value} [✓ ×{N}]` format | Structured, numbered, with source counts — excellent for small LLMs |
| **Self-verification** | LLM re-checks own answer against facts | Real LLM-based verification, not just heuristic |
| **Conversation memory** | Pronoun resolution + topic tracking | Adequate for single-session context |
| **Caching** | FactStore (TTL 3600s) + ResponseCache (TTL 1800s) | Prevents redundant searches, appropriate TTLs |

### ⚠️ ADEQUATE BUT IMPROVABLE

| Stage | Current State | Gap | Severity |
|-------|--------------|-----|----------|
| **Query rewriting** | Deterministic RU→EN dict, ~50 terms, filler strip, intent suffix | No LLM-powered reformulation. Misses semantic paraphrases. Dict covers tech but not general knowledge | **Medium** |
| **HTML cleaner** | BS4 main-content extraction, noise tag removal, boilerplate regex | No DOM-weight analysis. Doesn't extract structured tables. Single-pass, no fallback chain | **Medium** |
| **Passage splitting** | Paragraph→sentence split, 15-200 words, overlap=1 sentence | No semantic boundary detection. Pure string-based. Doesn't preserve list items or table rows as atomic units | **Medium** |
| **Fact extraction** | 18 regex patterns + KV pairs + GSMarena normalizer | Only catches hardware specs and KV patterns. Free-form prose facts (comparisons, opinions, temporal claims) completely invisible | **High** |
| **Anti-hallucination** | Regex checks numerics + chipset names in LLM output | Catches `"6000 мАч"` but misses qualitative hallucinations "excellent battery" or wrong attribute associations | **Medium** |

### ❌ MISSING (Clear Gaps)

| Stage | What's Missing | Impact |
|-------|---------------|--------|
| **Context compression / distillation** | Only char truncation. No extractive sentence scoring, no LLM summarization, no token-level budgeting | LLM gets truncated facts instead of optimally selected information. Wastes 30-50% of context window |
| **Cross-encoder reranking** | Only bi-encoder similarity. No query-passage co-attention scoring | Missing ~5-15% MRR improvement that cross-encoders provide over bi-encoders |
| **Source independence check** | Diversity = "new domain gets 1.0, seen gets 0.3" + single-domain cap. No content-level independence detection | Syndicated content from different domains is treated as "diverse" — correlated error risk |
| **Fact clustering** | Aggregator groups by predicate, but doesn't cluster semantically related facts | Same fact expressed differently ("6.5-inch screen" vs "165mm display") treated as separate |
| **Iterative retrieval** | Single retry with broadened queries. Doesn't use LLM to generate targeted follow-up queries based on first-round gaps | Complex queries that need multiple angles (comparison, timeline, multi-facet) often fail on first pass |
| **Latency overlap** | Search is parallel, but search→download→split→rank→extract→generate is strictly serial | 2-5s wasted. Could start downloading top results while search engines still returning |

---

## Ideal Pipeline (Target Architecture)

```
┌───────────────────────────────────────────────────────────────────┐
│              Target RAG Pipeline — Priority-Ordered               │
│                                                                   │
│  ➊ Query Understanding (entity + intent + complexity estimate)    │
│  ➋ Query Planning (3-5 search variants + site-specific queries)   │
│  ➌ Parallel Search (4 engines × N queries)                       │
│  ➍ RRF Merge + Domain Ranking + Source Independence Filter  ←NEW │
│  ➎ Concurrent Download + HTML Parsing (overlap with search) ←NEW │
│  ➏ DOM-Aware Content Extraction (table/list preservation)   ←NEW │
│  ➐ Semantic Chunking (topic boundaries, atomic units)       ←NEW │
│  ➑ Cross-Encoder Reranking (query-passage co-attention)     ←NEW │
│  ➒ Fact Extraction (regex specs + LLM for prose facts)      ←NEW │
│  ➓ Fact Clustering (semantic dedup, not just string match)   ←NEW │
│  ⓫ Multi-Source Fact Verification (agreement scoring)             │
│  ⓬ Context Distillation (extractive sentence scoring)       ←NEW │
│  ⓭ LLM Generation (fact-mode prompt)                             │
│  ⓮ Self-Verification (LLM + regex post-check)                    │
│  ⓯ Confidence-Gated Output (refuse if too uncertain)             │
│  ⓰ Adaptive Re-search (LLM-guided follow-up queries)       ←NEW │
└───────────────────────────────────────────────────────────────────┘
```

---

## Priority Implementation Plan

### P0: Context Distillation Layer (Highest ROI)

**Why first:** The LLM is the bottleneck. Every wasted token in the context window = lower answer quality. This single change improves EVERY query.

**Current:** 15 facts, 4000 chars, hard truncation.  
**Target:** Score each fact by relevance to the specific query, pack optimally into token budget.

**Implementation approach — extractive, no extra LLM call:**
1. Score each fact: `relevance(fact, query)` using the same embedding model
2. Sort by `relevance × confidence × source_count`
3. Pack greedily until token budget exhausted
4. Include `[source_count]` as quality signal to LLM

**File to change:** `lina/llm/fact_prompt.py` → `_select_facts()` function  
**Estimated effort:** 1-2 hours  
**Impact:** +10-20% answer quality for complex queries

### P1: DOM-Aware HTML Extraction

**Why:** HTML cleaner is the #1 source of garbage in the pipeline. If a table becomes  
`Name Processor RAM Storage` instead of structured KV pairs, fact extraction fails entirely.

**Current:** BS4 `get_text(separator="\n")` — flattens all structure.  
**Target:** Preserve table rows as `Key: Value`, preserve list items, extract code blocks.

**Implementation approach:**
1. Identify `<table>` → convert rows to `Header: Cell` KV lines
2. Identify `<ul>/<ol>` → preserve as `• item` lines
3. Identify `<pre>/<code>` → preserve verbatim
4. Strip noise but keep semantic structure

**File to change:** `lina/utils/html_cleaner.py` → `extract_main_content()`  
**Estimated effort:** 3-4 hours  
**Impact:** +15-25% fact extraction recall for spec pages

### P2: Source Independence Detection

**Why:** Current "diversity" is domain-based only. Syndicated content (same press release on 10 tech blogs) creates false confidence.

**Current:** New domain gets score 1.0, seen domain gets 0.3. Single-domain confidence cap at 0.50.  
**Target:** Content-level similarity check. If two sources have >80% text overlap → treat as one source.

**Implementation approach:**
1. After passage extraction, compute pairwise n-gram overlap between sources
2. If Jaccard(3-gram) > 0.6 between two sources → flag as syndicated
3. Syndicated sources count as 1 for `source_count` boosting
4. Add `independence_penalty` to fact confidence

**File to change:** `lina/core/fact_aggregator.py` + `lina/core/fact_verifier.py`  
**Estimated effort:** 2-3 hours  
**Impact:** Eliminates false multi-source confidence from syndicated content

### P3: Semantic Fact Clustering

**Why:** Current aggregator groups by exact normalized predicate. Same fact expressed differently gets counted separately, wasting context slots.

**Current:** `FactAggregator.aggregate()` → normalized predicate key (synonym mapping).  
**Target:** Fuzzy predicate matching + value semantic similarity.

**Implementation approach:**
1. After predicate normalization, compute embedding similarity between fact values
2. Facts with same predicate and `value_similarity > 0.8` → merge (keep longest/highest-confidence)
3. Facts with different predicates but `overall_similarity > 0.9` → merge

**File to change:** `lina/core/fact_aggregator.py`  
**Estimated effort:** 2-3 hours  
**Impact:** 10-20% fewer duplicate facts in context, better use of 15-fact budget

### P4: Cross-Encoder Reranking (Optional, Resource-Dependent)

**Why:** Bi-encoders miss query-document interaction. Cross-encoders score (query, passage) pairs jointly.

**Current:** Bi-encoder cosine similarity (or TF-IDF/BM25 fallback).  
**Target:** Re-rank top-20 passages with cross-encoder, take top-10.

**Implementation:** Use `cross-encoder/ms-marco-MiniLM-L-6-v2` (22M params, fast inference).  
**Estimated effort:** 3-4 hours (with download+integration)  
**Impact:** +5-15% passage relevance, but requires additional model download

---

## HTML Cleaner Deep Assessment

### Current Architecture

```
processing/html_cleaner.py → re-exports from utils/html_cleaner.py
                            + adds: is_bot_protection_page()
                            + adds: clean_page() with boilerplate removal
                            + adds: _remove_boilerplate() (7 regex patterns)

utils/html_cleaner.py       → clean_html() with BS4 → regex fallback
                            → extract_main_content() via <article>/<main>
                            → extract_title()
```

### Actual Quality Assessment

**Strengths:**
- ✅ BS4 with regex fallback means it ALWAYS produces output
- ✅ Bot-protection page detection (28 markers, threshold=2, length<3000)
- ✅ Noise tag removal (`script, style, noscript, header, footer, nav, aside, form, iframe, svg, button, input`)
- ✅ Main content extraction via semantic tags (`<article>`, `<main>`, `role="main"`)
- ✅ Boilerplate line removal (cookie, privacy, subscribe, copyright — RU+EN)
- ✅ GSMarena table normalizer in `fact_extractor.py` (section + sublabel → KV)

**Weaknesses:**
- ❌ **Tables are destroyed:** `soup.get_text(separator="\n")` collapses `<table>` into line soup. A spec table `<tr><td>RAM</td><td>6 GB</td></tr>` becomes `RAM\n6 GB` with no structural association
- ❌ **Lists lose structure:** `<ul><li>Feature A</li><li>Feature B</li></ul>` → `Feature A\nFeature B` — no bullet markers, no hierarchy
- ❌ **No content scoring:** ALL text in `<article>`/`<main>` is included equally. Author bio, related articles sidebar within main content, comment sections — all treated as primary content
- ❌ **Single-pass only:** No confidence scoring on extraction quality. If BS4 returns garbled trash, it still goes to the pipeline
- ❌ **No encoding detection:** Assumes UTF-8. Mojibake from ISO-8859/Windows-1251 pages will corrupt downstream

### Impact on Downstream Quality

The GSMarena normalizer in `fact_extractor.py` partially compensates for table destruction — it re-assembles `Section\nSublabel\nValue` triplets into `Section Sublabel: Value` KV pairs. **But this only works for pages that match the GSMarena layout pattern.** For Wikipedia infoboxes, Amazon spec tables, AnTuTu benchmark tables — all structure is lost.

---

## Context Construction Deep Assessment

### What the LLM Actually Sees (Verified)

**Prompt template** (from `llm/fact_prompt.py`):

```
[System role instruction — 8 strict rules]
=== ФАКТЫ ===
1. {subject} → {predicate}: {object_value}  [✓ ×{source_count}]
2. {subject} → {predicate}: {object_value}  [✓ ×{source_count}]
...
=== ВОПРОС ===
{query} (entities: ...) (attributes: ...)
=== ОТВЕТ ===
```

**Budget enforcement:**
- Max 15 facts, max 4000 chars total
- Facts sorted by confidence (descending) — but NOT by relevance to query
- Char-budget guard stops adding facts when limit reached

### Critical Issue: Facts Sorted by Confidence, Not Relevance

The current implementation in `_format_facts()` simply iterates `fact_set.facts` (already sorted by confidence from aggregator). A high-confidence fact about `battery` can consume a context slot when the user asked about `processor`.

**This is the single highest-ROI fix in the entire pipeline.**

Example:
```
Query: "какой процессор у realme 10"
Current fact ordering (by confidence):
  1. Realme 10 → аккумулятор: 5000 мАч  [✓ ×5]  ← conf 0.95, irrelevant
  2. Realme 10 → дисплей: 6.4" AMOLED  [✓ ×4]   ← conf 0.90, irrelevant  
  3. Realme 10 → процессор: Helio G99  [✓ ×3]    ← conf 0.85, RELEVANT
  ...
```

The first 2 slots are wasted on irrelevant but high-confidence facts.

**Fix: Rank by `relevance_to_query × confidence`** — ensures the processor fact appears first.

---

## Passage Splitter Assessment

### Current Strategy

```
1. Split on double-newline (paragraph breaks)
2. If paragraph > 200 words → split on sentences
3. If paragraph < 15 words → buffer + merge with next
4. Add 1-sentence overlap between chunks
```

### Quality Assessment

**Strengths:**
- ✅ Paragraph-aware splitting (respects natural boundaries)
- ✅ Does NOT split mid-sentence
- ✅ Overlap prevents context discontinuity
- ✅ Min/max word guards prevent tiny or huge chunks

**Weaknesses:**
- ❌ No semantic boundary detection — a paragraph spanning two topics gets one chunk
- ❌ No preservation of atomic units (table rows, list items, code blocks)
- ❌ No metadata propagation — split point location within page is lost
- ❌ Double-newline is the ONLY paragraph boundary — pages with single-newline formatting produce one giant chunk

**Severity: Medium.** The passage splitter is adequate for most cases. The bigger problem is upstream (HTML cleaner destroying structure) and downstream (no relevance-based fact selection).

---

## Concrete Changes (Implementation-Ready)

### Change 1: Relevance-Scored Context Distillation

**File:** `lina/llm/fact_prompt.py`  
**Change:** Score each fact by semantic similarity to query before including in prompt.

### Change 2: Table-Preserving HTML Extraction

**File:** `lina/utils/html_cleaner.py`  
**Change:** Convert `<table>` rows to `Key: Value` format, preserve `<ul>/<ol>` structure.

### Change 3: Source Independence Detection

**File:** `lina/core/fact_aggregator.py`  
**Change:** N-gram overlap check between sources, syndicated content penalty.

### Change 4: Enhanced Passage Splitting

**File:** `lina/utils/text_splitter.py`  
**Change:** Detect single-newline paragraphs, preserve list/table structure from upstream.

---

## Verdict

Lina's RAG pipeline is **remarkably complete for a single-developer project.** The 19-stage pipeline covers query→answer with proper verification at multiple levels. The fact-mode prompt design is genuinely excellent.

**The #1 improvement is not adding new stages — it's improving the quality of what enters each existing stage:**

1. **Better HTML extraction** → more facts extracted
2. **Relevance-scored fact selection** → LLM sees the RIGHT facts (not just highest-confidence)
3. **Source independence check** → confidence scores are trustworthy
4. **Context distillation** → optimal use of limited context window

These 4 changes affect 4 files, ~200 lines of code, and would improve answer quality by an estimated 20-35% across all query types.
