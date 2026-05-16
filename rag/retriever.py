"""
Lina — Улучшенный поиск по базе знаний (Retriever).

Расширяет KnowledgeSearcher:
  - Гибридный поиск: BM25 + n-gram (из VectorStore) + метаданные
  - Фильтрация по категории, дистрибутиву, тегам
  - Re-ranking по релевантности (code-boost, tag-boost, section match)
  - Context-window-aware: возвращает ровно столько, сколько
    влезает в контекст модели
  - Дедупликация результатов
"""

import re
import hashlib
import logging
from typing import List, Optional, Dict, Set

from lina.config import config
from lina.rag.vectorstore import VectorStore, VECTOR_INDEX_FILE

logger = logging.getLogger("lina.rag.retriever")


class KnowledgeRetriever:
    """
    Улучшенный поиск по RAG базе знаний.

    Особенности:
      - Фильтрация по category/distro/tags
      - Re-ranking: чанки с code получают буст,
        совпадение тегов даёт буст, точное совпадение секции даёт буст
      - Дедупликация: одинаковые/близкие чанки объединяются
      - Context-aware truncation: собирает контекст до лимита
    """

    # ── Веса re-ranking ──
    CODE_BOOST = 0.10        # буст для чанков с кодом при наличии "как" / "команда"
    TAG_BOOST = 0.05         # буст за каждый совпавший тег
    SECTION_BOOST = 0.08     # буст за совпадение запроса с section title
    DIVERSITY_PENALTY = 0.15 # штраф за чанки из одного файла

    def __init__(self):
        self.rag_config = config.rag
        self._store: Optional[VectorStore] = None

    def _get_store(self) -> VectorStore:
        """Загружает VectorStore с диска если ещё не загружен."""
        if self._store is None:
            self._store = VectorStore()
            if not self._store.load():
                # Попытка миграции со старого формата
                self._try_legacy_load()
        return self._store

    def _try_legacy_load(self) -> None:
        """Миграция со старого TF-IDF/v1 индекса."""
        import json
        try:
            from lina.rag.indexer import INDEX_FILE
            if INDEX_FILE.exists():
                with open(INDEX_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                chunks = data.get("chunks", [])
                metadata = data.get("metadata", [])
                if chunks:
                    self._store.build(chunks, metadata)
                    self._store.save()
        except Exception as exc:
            logger.warning("Legacy index migration failed: %s", exc)

    def reload_index(self) -> None:
        """Перезагружает индекс с диска."""
        self._store = None

    # ── Основной поиск ──

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        category: Optional[str] = None,
        distro: Optional[str] = None,
        tags: Optional[List[str]] = None,
        min_score: Optional[float] = None,
    ) -> List[dict]:
        """
        Гибридный поиск с фильтрацией и re-ranking.

        Args:
            query: Запрос пользователя.
            top_k: Количество результатов (по умолчанию из config).
            category: Фильтр по категории (troubleshooting, linux_core, ...).
            distro: Фильтр по дистрибутиву (arch, ubuntu, fedora, ...).
            tags: Фильтр по тегам (пересечение — хотя бы один тег совпадает).
            min_score: Минимальный порог скора.

        Returns:
            [{text, score, source, filename, metadata, rank_info}, ...]
        """
        k = top_k or self.rag_config.top_k
        threshold = min_score if min_score is not None else self.rag_config.min_relevance_score
        store = self._get_store()

        if not store.chunks:
            return []

        # Шаг 1: Широкий поиск BM25 + n-gram (берём больше, потом фильтруем)
        raw_k = min(k * 5, len(store.chunks))
        raw_results = store.search(query, top_k=raw_k)

        if not raw_results:
            return []

        # Шаг 2: Фильтрация по метаданным
        filtered = self._filter_results(raw_results, category, distro, tags)

        # Шаг 3: Re-ranking
        reranked = self._rerank(filtered, query)

        # Шаг 4: Дедупликация
        deduped = self._deduplicate(reranked)

        # Шаг 5: Порог + top_k
        final = [r for r in deduped if r["score"] >= threshold][:k]

        return final

    def build_context(
        self,
        query: str,
        top_k: Optional[int] = None,
        max_context_length: int = 2000,
        category: Optional[str] = None,
        distro: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> str:
        """
        Строит контекст для LLM из результатов поиска.

        Context-window-aware: собирает чанки до max_context_length.

        Args:
            query: Запрос пользователя.
            top_k: Количество результатов.
            max_context_length: Максимальная длина контекста (символы).
            category: Фильтр по категории.
            distro: Фильтр по дистрибутиву.
            tags: Фильтр по тегам.

        Returns:
            Строка контекста для LLM промпта.
        """
        results = self.search(
            query,
            top_k=top_k or self._context_top_k(max_context_length),
            category=category,
            distro=distro,
            tags=tags,
        )

        if not results:
            return ""

        context_parts = []
        total_length = 0

        for r in results:
            meta = r.get("metadata", {})
            source_label = self._format_source_label(r, meta)

            chunk = f"[Источник: {source_label}]\n{r['text']}"
            chunk_len = len(chunk)

            if total_length + chunk_len > max_context_length:
                remaining = max_context_length - total_length
                if remaining > 100:
                    chunk = chunk[:remaining] + "..."
                    context_parts.append(chunk)
                break

            context_parts.append(chunk)
            total_length += chunk_len

        if not context_parts:
            return ""

        return "\n\n---\n\n".join(context_parts)

    # ── Фильтрация ──

    def _filter_results(
        self,
        results: List[dict],
        category: Optional[str],
        distro: Optional[str],
        tags: Optional[List[str]],
    ) -> List[dict]:
        """Фильтрует результаты по метаданным."""
        if not category and not distro and not tags:
            return results

        filtered = []
        for r in results:
            meta = r.get("metadata", {})

            # Фильтр по категории
            if category and meta.get("category") not in (category, "general"):
                continue

            # Фильтр по дистрибутиву
            if distro:
                doc_distros = meta.get("distros", ["all"])
                if "all" not in doc_distros and distro.lower() not in [d.lower() for d in doc_distros]:
                    continue

            # Фильтр по тегам (хотя бы один совпадает)
            if tags:
                doc_tags = set(t.lower() for t in meta.get("tags", []))
                query_tags = set(t.lower() for t in tags)
                if not doc_tags.intersection(query_tags):
                    continue

            filtered.append(r)

        return filtered

    # ── Re-ranking ──

    def _rerank(self, results: List[dict], query: str) -> List[dict]:
        """
        Re-ranking результатов.

        Бусты:
          - Чанки с кодом получают бонус если запрос содержит
            слова-маркеры ("как", "команда", "command", "пример")
          - Совпадение тегов с ключевыми словами запроса
          - Совпадение запроса с section title
          - Штраф за повторение файла (diversity)
        """
        query_lower = query.lower()
        query_words = set(re.findall(r'[a-zа-яёA-ZА-ЯЁ0-9]+', query_lower))

        # Маркеры запроса кода
        code_markers = {"как", "команда", "command", "пример", "example", "скрипт", "script"}
        wants_code = bool(query_words.intersection(code_markers))

        seen_sources: Dict[str, int] = {}
        reranked = []

        for r in results:
            meta = r.get("metadata", {})
            boost = 0.0
            reasons = []

            # Code boost
            if wants_code and meta.get("has_code", False):
                boost += self.CODE_BOOST
                reasons.append("code_boost")

            # Tag boost
            doc_tags = set(t.lower() for t in meta.get("tags", []))
            tag_overlap = doc_tags.intersection(query_words)
            if tag_overlap:
                boost += self.TAG_BOOST * len(tag_overlap)
                reasons.append(f"tag_match({','.join(tag_overlap)})")

            # Section title boost
            section = meta.get("section", "").lower()
            if section:
                section_words = set(re.findall(r'[a-zа-яёA-ZА-ЯЁ0-9]+', section))
                sec_overlap = section_words.intersection(query_words)
                if sec_overlap:
                    boost += self.SECTION_BOOST
                    reasons.append("section_match")

            # Diversity penalty (source)
            source = meta.get("source", r.get("source", ""))
            source_count = seen_sources.get(source, 0)
            if source_count > 0:
                boost -= self.DIVERSITY_PENALTY * source_count
                reasons.append(f"diversity_penalty({source_count})")
            seen_sources[source] = source_count + 1

            new_score = max(0.0, r["score"] + boost)

            reranked.append({
                **r,
                "score": round(new_score, 4),
                "rank_info": {
                    "original_score": r["score"],
                    "boost": round(boost, 4),
                    "reasons": reasons,
                },
            })

        # Пересортировка
        reranked.sort(key=lambda x: x["score"], reverse=True)
        return reranked

    # ── Дедупликация ──

    def _deduplicate(self, results: List[dict]) -> List[dict]:
        """
        Удаляет дубликаты и очень похожие чанки.

        Стратегия:
          - Если два чанка имеют одинаковый md5 — удалить дубль
          - Если >70% текста перекрывается (по словам) — удалить дубль
        """
        if len(results) <= 1:
            return results

        seen_hashes: Set[str] = set()
        seen_word_sets: List[Set[str]] = []
        deduped = []

        for r in results:
            text = r["text"]
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

            # Точный дубль
            if text_hash in seen_hashes:
                continue
            seen_hashes.add(text_hash)

            # Нечёткий дубль (по словам)
            words = set(text.lower().split())
            is_near_dup = False
            for prev_words in seen_word_sets:
                if not words or not prev_words:
                    continue
                overlap = len(words.intersection(prev_words))
                smaller = min(len(words), len(prev_words))
                if smaller > 0 and overlap / smaller > 0.70:
                    is_near_dup = True
                    break

            if is_near_dup:
                continue

            seen_word_sets.append(words)
            deduped.append(r)

        return deduped

    # ── Утилиты ──

    def _context_top_k(self, max_context_length: int) -> int:
        """Оценивает сколько чанков влезет в контекст."""
        avg_chunk_len = 400  # средний размер чанка
        return max(3, max_context_length // avg_chunk_len)

    def _format_source_label(self, result: dict, meta: dict) -> str:
        """Форматирует метку источника для контекста."""
        if meta.get("type") == "history":
            return "📜 история команд"

        parts = []
        title = meta.get("title", "")
        if title:
            parts.append(title)

        section = meta.get("section", "")
        if section and section != title:
            parts.append(f"§ {section}")

        category = meta.get("category", "")
        if category and category != "general":
            parts.append(f"[{category}]")

        if parts:
            return " — ".join(parts)

        return result.get("filename", "документ")

    def has_documents(self) -> bool:
        """Проверяет, есть ли документы в базе знаний."""
        try:
            store = self._get_store()
            return store.total_chunks > 0
        except Exception:
            return False

    def get_categories(self) -> List[str]:
        """Возвращает список всех категорий в индексе."""
        store = self._get_store()
        categories: Set[str] = set()
        for meta in store.metadata:
            cat = meta.get("category", "")
            if cat:
                categories.add(cat)
        return sorted(categories)

    def get_distros(self) -> List[str]:
        """Возвращает список всех дистрибутивов в индексе."""
        store = self._get_store()
        distros: Set[str] = set()
        for meta in store.metadata:
            for d in meta.get("distros", []):
                if d != "all":
                    distros.add(d)
        return sorted(distros)
