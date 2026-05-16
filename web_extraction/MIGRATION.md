# Web Extraction Pipeline — План миграции

## Обзор

Модуль `web_extraction` заменяет два разрозненных этапа (stages 8+9) в `pipeline_v3`:
- Stage 8: `PassageExtractor` (download + split)
- Stage 9: `EmbeddingRanker` (semantic ranking)

Новый пайплайн объединяет их в единый `WebExtractionPipeline` с:
- DOM-based content extraction с text density analysis
- Token-aware semantic chunking (200–400 tokens)
- Hybrid BM25 + embedding ranking
- Source trust scoring + cross-source verification
- Parallel page processing с cross-page deduplication

---

## Фаза 0: Текущее состояние (ГОТОВО)

Код уже внедрён с **graceful fallback**:

```python
# pipeline_v3.py — stages 8+9
try:
    web_result = self._web_pipeline.run(query, snippets_with_urls)
    if web_result and web_result.passages:
        passages = web_result.passages
        ...
except Exception as e:
    logger.warning("web_extraction failed, fallback to legacy: %s", e)
    # legacy PassageExtractor + EmbeddingRanker continues to work
```

**Риск**: нулевой. При любой ошибке нового кода — откат на старый путь.

---

## Фаза 1: A/B мониторинг (1–2 недели)

### Что делать

1. Добавить метрики сравнения в лог:

```python
# В pipeline_v3._run_stage_8_9():
legacy_passages = self._legacy_passage_extractor(...)
new_passages = self._web_pipeline.run(...)

log_comparison({
    "query": query,
    "legacy_count": len(legacy_passages),
    "new_count": len(new_passages) if new_passages else 0,
    "legacy_avg_len": avg([len(p) for p in legacy_passages]),
    "new_avg_len": avg([len(p.text) for p in new_passages.passages]) if new_passages else 0,
    "new_trust_scores": [p.get("trust_score") for p in new_passages.passages] if new_passages else [],
})
```

2. Использовать результаты нового пайплайна, но логировать оба.

### Критерии перехода к Фазе 2

- Новый pipeline возвращает результаты в ≥95% случаев
- Среднее количество passages ≥ legacy
- Нет regression в качестве ответов (ручная проверка ~50 запросов)

---

## Фаза 2: Новый pipeline как primary (2–4 недели)

### Что делать

1. Убрать fallback на legacy для основного пути
2. Оставить legacy code, но пометить как deprecated:

```python
# passage_extractor.py / embedding_ranker.py
import warnings
warnings.warn("Legacy passage extraction is deprecated. Use web_extraction.", DeprecationWarning)
```

3. Мониторинг:
   - Latency: новый pipeline ≤ 2× legacy (параллельная загрузка компенсирует)
   - Error rate: < 1%
   - Memory: пиковое потребление не выше 50MB сверх legacy

### Критерии перехода к Фазе 3

- 4 недели стабильной работы без regression
- Latency в пределах target

---

## Фаза 3: Удаление legacy (после 4+ недель)

### Что удалить

1. `core/passage_extractor.py` — заменён `web_extraction/content_extractor.py` + `page_processor.py`
2. `core/embedding_ranker.py` — заменён `web_extraction/hybrid_ranker.py`
3. Fallback-ветка в `pipeline_v3.py`
4. Неиспользуемые импорты

### Что оставить

- `core/search_pipeline.py` — search API logic (ортогональна к web extraction)
- `core/fact_extractor.py` — работает поверх passages, не затронут
- `core/fact_verifier.py` — будет получать `trust_score` из нового pipeline

---

## Карта зависимостей

```
pipeline_v3.py
  └── web_extraction/web_pipeline.py (NEW — replaces stages 8+9)
       ├── page_processor.py
       │    ├── content_extractor.py  (replaces passage_extractor.py)
       │    └── semantic_chunker.py   (replaces word-based split in passage_extractor)
       ├── hybrid_ranker.py           (replaces embedding_ranker.py)
       └── source_trust.py            (NEW — no legacy equivalent)
```

---

## Rollback

На любой фазе откат — одна строка в `pipeline_v3.py`:

```python
# Чтобы вернуть legacy: закомментировать блок try/except с self._web_pipeline
# и раскомментировать старые вызовы stages 8+9
```

Или через конфиг:

```python
# config.py
WEB_EXTRACTION_ENABLED = False  # выключает новый pipeline
```

---

## Зависимости (все опциональные)

| Пакет | Для чего | Fallback |
|-------|---------|----------|
| `beautifulsoup4` | DOM-парсинг | regex-based extraction |
| `sentence-transformers` | Embedding ranking | sklearn TF-IDF → pure BM25 |
| `scikit-learn` | TF-IDF vectorizer | pure BM25 |

Минимально для работы: **только stdlib Python 3.10+**.
