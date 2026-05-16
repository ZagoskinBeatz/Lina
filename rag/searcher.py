"""
Lina — Модуль поиска по RAG базе знаний.

Семантический поиск через BM25 + символьные n-граммы.
Поддерживает поиск по документам и по истории команд.
"""

import logging
from typing import List, Optional

from lina.config import config
from lina.rag.vectorstore import VectorStore, VECTOR_INDEX_FILE

logger = logging.getLogger(__name__)


class KnowledgeSearcher:
    """
    Семантический поиск по базе знаний.

    Использует VectorStore (BM25 + n-gram) для поиска
    наиболее релевантных фрагментов документов.
    """

    def __init__(self):
        self.rag_config = config.rag
        self._store: Optional[VectorStore] = None

    def _get_store(self) -> VectorStore:
        """Загружает VectorStore с диска если ещё не загружен."""
        if self._store is None:
            self._store = VectorStore()
            # Пробуем новый формат
            if not self._store.load():
                # Пробуем загрузить старый TF-IDF индекс (миграция)
                self._try_legacy_load()
        return self._store

    def _try_legacy_load(self) -> None:
        """Миграция со старого TF-IDF индекса."""
        import json
        from lina.rag.indexer import INDEX_FILE
        try:
            if INDEX_FILE.exists():
                with open(INDEX_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                chunks = data.get("chunks", [])
                metadata = data.get("metadata", [])
                if chunks:
                    self._store.build(chunks, metadata)
                    self._store.save()  # Пересохраняем в новом формате
        except Exception:
            logger.warning("Legacy index migration failed", exc_info=True)

    def reload_index(self) -> None:
        """Перезагружает индекс с диска."""
        self._store = None

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> List[dict]:
        """
        Семантический поиск по базе знаний.

        Args:
            query: Запрос пользователя.
            top_k: Количество результатов.

        Returns:
            [{text, score, source, filename, metadata}, ...]
        """
        k = top_k or self.rag_config.top_k
        store = self._get_store()
        return store.search(query, top_k=k)

    def build_context(
        self,
        query: str,
        top_k: Optional[int] = None,
        max_context_length: int = 2000,
    ) -> str:
        """
        Строит контекст для LLM из результатов поиска.

        Args:
            query: Запрос пользователя.
            top_k: Количество результатов.
            max_context_length: Максимальная длина контекста.

        Returns:
            Строка с контекстом для промпта LLM.
        """
        results = self.search(query, top_k)

        if not results:
            return ""

        context_parts = []
        total_length = 0

        for r in results:
            if r["score"] < self.rag_config.min_relevance_score:
                continue

            source_label = r.get("filename", "")
            if r.get("metadata", {}).get("type") == "history":
                source_label = "📜 история"

            chunk = f"[Источник: {source_label}]\n{r['text']}"

            if total_length + len(chunk) > max_context_length:
                remaining = max_context_length - total_length
                if remaining > 100:
                    chunk = chunk[:remaining] + "..."
                    context_parts.append(chunk)
                break

            context_parts.append(chunk)
            total_length += len(chunk)

        if not context_parts:
            return ""

        return "\n\n".join(context_parts)

    def has_documents(self) -> bool:
        """Проверяет, есть ли документы в базе знаний."""
        try:
            store = self._get_store()
            return store.total_chunks > 0
        except Exception:
            logger.error("Failed to check documents", exc_info=True)
            return False
