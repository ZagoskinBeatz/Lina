# -*- coding: utf-8 -*-
"""
Lina Embeddings — Semantic Ranker (v3).

Ranks passages by semantic similarity to the user query
using the EmbeddingModel abstraction.

v3 uses the embedding_model layer for clean separation of concerns.
"""

from __future__ import annotations

import logging
import time
from typing import List

from lina.models.datatypes import Passage
from lina.embeddings.embedding_model import get_embedding_model

logger = logging.getLogger("lina.embeddings.semantic_ranker")


class SemanticRanker:
    """
    Ranks passages by semantic similarity to query.

    Usage:
        ranker = SemanticRanker()
        top = ranker.rank(passages, "realme 10 processor", top_k=10)
    """

    def __init__(self):
        self._model = get_embedding_model()

    def rank(
        self,
        passages: List[Passage],
        query: str,
        top_k: int = 10,
        min_similarity: float = 0.15,
    ) -> List[Passage]:
        """
        Rank passages by semantic similarity.

        Args:
            passages: Input passages.
            query: User query.
            top_k: Max results.
            min_similarity: Minimum similarity threshold.

        Returns:
            Sorted passages (best first), .score updated.
        """
        if not passages or not query:
            return passages[:top_k]

        t0 = time.time()
        texts = [p.text for p in passages]

        # Get similarity scores from embedding model
        scores = self._model.similarity(query, texts)

        # Update passage scores
        for p, s in zip(passages, scores):
            p.score = s

        # Filter and sort
        filtered = [p for p in passages if p.score >= min_similarity]
        filtered.sort(key=lambda p: p.score, reverse=True)

        elapsed = (time.time() - t0) * 1000
        logger.info(
            "SemanticRanker [%s]: %d → %d passages in %.0f ms (top=%.3f)",
            self._model.backend, len(passages), len(filtered[:top_k]),
            elapsed, filtered[0].score if filtered else 0,
        )
        return filtered[:top_k]


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_ranker: SemanticRanker | None = None

def get_semantic_ranker() -> SemanticRanker:
    global _ranker
    if _ranker is None:
        _ranker = SemanticRanker()
    return _ranker
