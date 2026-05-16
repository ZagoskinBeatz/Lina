"""
Lina Runtime — RAG Layer.

Безопасная прослойка над RAG-поиском.

Гарантии:
  1. Контекст НЕ содержит маркеров [Источник: ...]
  2. Контекст НЕ содержит --- Контекст из базы знаний ---
  3. Источники доступны ТОЛЬКО в debug-режиме
  4. Контекст обрезается до лимита tier'а

Использование:
    rag = RAGLayer(searcher)
    context = rag.get_context("запрос", tier="full")
    # → чистый текст без маркеров

    if DEBUG_RAG:
        sources = rag.get_sources("запрос")
"""

import re
import logging
from typing import List, Optional

logger = logging.getLogger("lina.runtime.rag_layer")

# Показывать ли источники (только для отладки)
DEBUG_RAG = False

# Лимиты контекста по tier
_CONTEXT_LIMITS = {
    "full": 2000,
}

# Паттерны для очистки
_SOURCE_LABEL = re.compile(r"\[Источник:\s*[^\]]*\]\n?")
_HISTORY_EMOJI = re.compile(r"📜\s*история\s*")
_RAG_BLOCK_MARKERS = re.compile(
    r"---\s*(Контекст из базы знаний|Конец контекста)\s*---",
)


class RAGLayer:
    """
    Безопасная прослойка над KnowledgeSearcher.

    Очищает RAG-контекст ОТ МАРКЕРОВ перед передачей в LLM.
    Маркеры видят только разработчики (DEBUG_RAG=True).
    """

    def __init__(self, searcher=None):
        """
        Args:
            searcher: KnowledgeSearcher из lina.rag.searcher.
        """
        self._searcher = searcher

    def get_context(
        self,
        query: str,
        tier: str = "full",
        max_length: Optional[int] = None,
    ) -> str:
        """
        Получает очищенный RAG-контекст для промпта.

        Args:
            query: Запрос пользователя.
            tier: Тип модели (влияет на лимит длины).
            max_length: Переопределение лимита длины.

        Returns:
            Чистый текст контекста (без маркеров).
        """
        if not self._searcher or not self.has_documents():
            return ""

        limit = max_length or _CONTEXT_LIMITS.get(tier, 2000)

        # Получаем сырой контекст
        raw = self._searcher.build_context(query, max_context_length=limit)
        if not raw:
            return ""

        # Очищаем от маркеров
        clean = self._sanitize(raw)

        # Обрезаем до лимита
        if len(clean) > limit:
            clean = clean[:limit] + "..."

        return clean

    def get_sources(self, query: str, top_k: int = 3) -> List[dict]:
        """
        Возвращает источники с метаданными (для debug).

        Args:
            query: Запрос.
            top_k: Количество результатов.

        Returns:
            [{text, score, filename}, ...]
        """
        if not self._searcher:
            return []
        return self._searcher.search(query, top_k=top_k)

    def has_documents(self) -> bool:
        """Есть ли документы в базе знаний."""
        if not self._searcher:
            return False
        try:
            return self._searcher.has_documents()
        except Exception as e:
            logger.warning("RAG has_documents check failed: %s", e, exc_info=True)
            return False

    def _sanitize(self, text: str) -> str:
        """
        Удаляет все RAG-маркеры из текста.

        Убирает:
          - [Источник: filename.txt]
          - --- Контекст из базы знаний ---
          - --- Конец контекста ---
          - 📜 история

        Args:
            text: Сырой RAG-контекст.

        Returns:
            Чистый текст.
        """
        result = text
        result = _SOURCE_LABEL.sub("", result)
        result = _RAG_BLOCK_MARKERS.sub("", result)
        result = _HISTORY_EMOJI.sub("", result)
        # Нормализуем пробелы
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()
