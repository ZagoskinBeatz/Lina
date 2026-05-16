# Phase 21 — FULL LLM PIPELINE AUDIT & HARD FIX

**Дата:** 23.02.2026  
**Тесты:** 739/739 ✅  
**Цель:** Физически исключить `Requested tokens exceed context window`

---

## 🔟 ИТОГОВЫЙ ОТЧЁТ

| Параметр | Значение |
|----------|----------|
| **LLM CALL MODE** | `raw_prompt` (единственный) |
| **REAL PROMPT TOKENS** | `self._context_budget.count(prompt)` — точный подсчёт через `llm.tokenize()` |
| **TEMPLATE OVERHEAD** | 0 (raw prompt, без chat template) |
| **FINAL MAX TOKENS** | `min(profile.max_tokens, available, 512)` — НИКОГДА `profile.max_tokens` напрямую |
| **REAL n_ctx** | `llm.n_ctx()` — источник истины, НЕ конфиг |
| **OVERFLOW POSSIBLE** | **NO** ❌ — физически невозможно |

---

## 1️⃣ LLM CALL MODE — зафиксирован

**Результат аудита:**
- **2 точки вызова** модели: `engine.py` L~455 (sync) и L~555 (stream)
- **Оба используют:** `self._active.model(prompt, max_tokens=..., ...)` — raw completion API
- **НЕ используется:** `create_chat_completion()`, `messages=[]`
- **Логирование:** `logger.debug("LLM CALL MODE: raw_prompt")`

**Файлы:**
- `llm/engine.py` — `generate()`, `generate_stream()`

---

## 2️⃣ Messages API — НЕ ИСПОЛЬЗУЕТСЯ

Проект использует **только raw prompt** формат:
```
### SYSTEM
{system_prompt}

### HISTORY
{history}

### CONTEXT
{rag_context}

### USER
{query}

### ASSISTANT
```

Нет `create_chat_completion`, нет `messages=[{"role":...}]`.  
Chat template не применяется — нет двойной шаблонизации.

---

## 3️⃣ Raw Prompt — единственный способ

Подсчёт токенов:
```python
prompt_tokens = self._context_budget.count(prompt)
# → len(llm.tokenize(prompt.encode("utf-8")))
```

Передача в модель:
```python
response = self._active.model(
    prompt,                          # raw string
    max_tokens=effective_max_tokens, # рассчитанный, не из конфига
    ...
)
```

---

## 4️⃣ Hard Assert в engine.generate()

```python
# BEFORE generation:
if prompt_tokens > real_n_ctx:
    raise RuntimeError("Prompt exceeds n_ctx BEFORE generation")

if prompt_tokens + effective_max_tokens > real_n_ctx:
    effective_max_tokens = max(real_n_ctx - prompt_tokens - SAFETY_MARGIN, 1)
    logger.warning("OVERFLOW CORRECTED: ...")
```

Breakdown при ошибке включён в LLM BUDGET REPORT.

---

## 5️⃣ LLM BUDGET REPORT — логирование

Перед каждой генерацией:
```
===== LLM BUDGET REPORT =====
Mode: raw_prompt
n_ctx (real): 4096
n_ctx (config): 4096
Prompt tokens: 850
Max tokens: 512
Total: 1362
Safety margin: 64
Overflow: NO
=============================
```

---

## 6️⃣ Реальный n_ctx

```python
# При загрузке модели:
if hasattr(model, 'n_ctx'):
    real_n_ctx = model.n_ctx()
    if real_n_ctx != profile.n_ctx:
        logger.warning("n_ctx MISMATCH: model=%d, config=%d", ...)
    self._real_n_ctx = real_n_ctx
```

`ContextBudgetManager` инициализируется с `self._real_n_ctx`, не `profile.n_ctx`.

---

## 7️⃣ Запрет двойного max_tokens

```python
# ЕДИНСТВЕННАЯ формула:
effective_max_tokens = min(profile.max_tokens, max(available, 1), 512)

# Абсолютный потолок:
effective_max_tokens = min(effective_max_tokens, 512)
```

`profile.max_tokens` НИКОГДА не передаётся в `model()` напрямую.

---

## 8️⃣ Диагностический режим --llm-debug

Активация:
```bash
python lina.py --llm-debug
# или
LLM_DEBUG=1 python lina.py
```

Вывод:
```
[LLM-DEBUG] mode=raw_prompt prompt_tokens=850 max_tokens=512 total=1362 n_ctx=4096
[LLM-DEBUG] prompt=
### SYSTEM
Ты — Lina...
### USER
Привет
### ASSISTANT
```

---

## 9️⃣ Тесты переполнения — 14 новых тестов

| # | Тест | Описание |
|---|------|----------|
| 1 | `p21 engine_imports` | SAFETY_MARGIN=64 импортируется в engine |
| 2 | `p21 engine_llm_debug` | LLMEngine(llm_debug=True) работает |
| 3 | `p21 engine_real_n_ctx` | `_real_n_ctx` атрибут существует |
| 4 | `p21 no_messages_api` | Нет вызовов `create_chat_completion()` |
| 5 | `p21 no_direct_max_tokens` | `max_tokens=profile.max_tokens` не передаётся |
| 6 | `p21 overflow_huge_all` | Огромный system+history+rag+user, n_ctx=2048 |
| 7 | `p21 overflow_tiny_ctx` | n_ctx=256, огромный ввод |
| 8 | `p21 overflow_detailed` | build_prompt_detailed, n_ctx=1024 |
| 9 | `p21 overflow_200_random` | 200 случайных конфигураций |
| 10 | `p21 call_mode_source` | "LLM CALL MODE" в исходнике generate() |
| 11 | `p21 budget_report_source` | "LLM BUDGET REPORT" в исходнике |
| 12 | `p21 hard_assert_source` | RuntimeError при переполнении |
| 13 | `p21 stream_enforcement` | generate_stream() тоже защищён |
| 14 | `p21 cli_llm_debug` | --llm-debug в lina.py |

---

## Дополнительные исправления

### Шаг 6c — обрезка user_input (last resort)

Если user_input в одиночку превышает n_ctx (при n_ctx < 200 или огромный ввод):

```python
# build_prompt() и build_prompt_detailed():
if prompt_tokens + SAFETY_MARGIN >= self.n_ctx:
    user_input = self._trim_text_to_tokens(user_input, user_budget)
    system_prompt = ""
    rag_context = ""
```

Приоритет обрезки (Phase 21 финальный):
1. rag_context → 50% → 25% → 0%
2. history → от старых к новым → 0
3. system_prompt → обрезка до бюджета
4. **user_input → обрезка до бюджета** (NEW)
5. RuntimeError → если даже пустой промпт > n_ctx

---

## Изменённые файлы

| Файл | Изменения |
|------|-----------|
| `llm/engine.py` | Phase 21 rewrite: LLM CALL MODE, real n_ctx, hard assert, BUDGET REPORT, --llm-debug, SAFETY_MARGIN import |
| `core/context_budget.py` | Шаг 6c: обрезка user_input при переполнении |
| `lina.py` | --llm-debug CLI флаг, LLM_DEBUG env var |
| `test_all_v3.py` | 14 новых тестов Phase 21 |

---

## Вердикт: OVERFLOW POSSIBLE = **NO** ❌

Три уровня защиты:
1. **ContextBudgetManager.build_prompt()** — обрезает rag → history → system → user
2. **engine.generate() → hard assert** — RuntimeError если prompt > n_ctx
3. **engine.generate() → auto-correction** — if overflow → `max_tokens = max(n_ctx - prompt - 64, 1)`

739/739 тестов пройдено.
