# -*- coding: utf-8 -*-
"""
Lina Core — Embedding Ranker (v2 Pipeline).

Ranks passages by **semantic similarity** to the user query.
This is dramatically better than keyword matching.

Backend priority:
  1. sentence-transformers (all-MiniLM-L6-v2)  — best quality
  2. sklearn TfidfVectorizer + cosine_similarity — decent, no GPU
  3. Pure-Python BM25-like tf-idf                — always works

Graceful fallback: if the best backend isn't installed, the next one
is used automatically.  Zero hard dependencies.

Design: stateless ranker, lazy-loaded models, thread-safe.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter
from typing import List, Optional, Tuple

from lina.models.datatypes import Passage

logger = logging.getLogger("lina.core.embedding_ranker")


# ═══════════════════════════════════════════════════
#  Backend detection
# ═══════════════════════════════════════════════════

_BACKEND: Optional[str] = None

try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    _BACKEND = "sentence_transformers"
except ImportError:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity as _sk_cosine
        import numpy as np
        _BACKEND = "sklearn"
    except ImportError:
        _BACKEND = "python"

logger.info("EmbeddingRanker backend: %s", _BACKEND)


class EmbeddingRanker:
    """
    Ranks passages by semantic similarity to a query.

    Automatically selects the best available backend.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._st_model = None  # Lazy-loaded SentenceTransformer
        self._backend = _BACKEND

    def rank(
        self,
        passages: List[Passage],
        query: str,
        top_k: int = 10,
        min_similarity: float = 0.20,
    ) -> List[Passage]:
        """
        Rank passages by similarity to query, return top_k.

        Args:
            passages:  Input passages.
            query:     User query.
            top_k:     Number of top passages to return.
            min_similarity: Filter threshold (0–1).

        Returns:
            Sorted list of Passage objects (best first), .score updated.
        """
        if not passages or not query:
            return passages[:top_k]

        t0 = time.time()
        texts = [p.text for p in passages]

        if self._backend == "sentence_transformers":
            scores = self._rank_st(texts, query)
        elif self._backend == "sklearn":
            scores = self._rank_sklearn(texts, query)
        else:
            scores = self._rank_python(texts, query)

        # Update passage scores
        for p, s in zip(passages, scores):
            p.score = s

        # Filter and sort
        filtered = [p for p in passages if p.score >= min_similarity]
        filtered.sort(key=lambda p: p.score, reverse=True)

        elapsed = (time.time() - t0) * 1000
        logger.info(
            "EmbeddingRanker [%s]: %d → %d passages in %.0f ms (top_score=%.3f)",
            self._backend, len(passages), len(filtered[:top_k]),
            elapsed, filtered[0].score if filtered else 0,
        )

        return filtered[:top_k]

    # ── Backend implementations ──

    def _rank_st(self, texts: List[str], query: str) -> List[float]:
        """Rank using sentence-transformers (highest quality)."""
        if self._st_model is None:
            logger.info("Loading sentence-transformers model: %s", self._model_name)
            self._st_model = SentenceTransformer(self._model_name)

        query_emb = self._st_model.encode([query], convert_to_numpy=True)
        text_embs = self._st_model.encode(texts, convert_to_numpy=True,
                                          batch_size=32, show_progress_bar=False)
        # Cosine similarity
        sims = np.dot(text_embs, query_emb.T).flatten()
        # Normalize to [0, 1]
        norms_t = np.linalg.norm(text_embs, axis=1)
        norms_q = np.linalg.norm(query_emb)
        with np.errstate(divide="ignore", invalid="ignore"):
            sims = sims / (norms_t * norms_q)
        sims = np.nan_to_num(sims, nan=0.0)
        return sims.tolist()

    def _rank_sklearn(self, texts: List[str], query: str) -> List[float]:
        """Rank using sklearn TF-IDF + cosine similarity."""
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        corpus = [query] + texts
        vectorizer = TfidfVectorizer(
            max_features=10000,
            sublinear_tf=True,
            ngram_range=(1, 2),
        )
        try:
            tfidf = vectorizer.fit_transform(corpus)
        except ValueError:
            return [0.0] * len(texts)

        query_vec = tfidf[0:1]
        text_vecs = tfidf[1:]
        sims = cosine_similarity(query_vec, text_vecs).flatten()
        return sims.tolist()

    def _rank_python(self, texts: List[str], query: str) -> List[float]:
        """Pure-Python BM25-inspired ranking (no dependencies)."""
        query_terms = _tokenize(query)
        if not query_terms:
            return [0.0] * len(texts)

        # Build document frequency table
        doc_count = len(texts)
        df: Counter = Counter()
        doc_term_freqs = []
        doc_lengths = []

        for t in texts:
            terms = _tokenize(t)
            doc_lengths.append(len(terms))
            tf = Counter(terms)
            doc_term_freqs.append(tf)
            for term in set(terms):
                df[term] += 1

        avg_dl = sum(doc_lengths) / max(doc_count, 1)

        # BM25 scoring (k1=1.5, b=0.75)
        k1, b = 1.5, 0.75
        scores: List[float] = []

        for i, tf in enumerate(doc_term_freqs):
            score = 0.0
            dl = doc_lengths[i]
            for qt in query_terms:
                n = df.get(qt, 0)
                idf = math.log((doc_count - n + 0.5) / (n + 0.5) + 1.0)
                freq = tf.get(qt, 0)
                numerator = freq * (k1 + 1)
                denominator = freq + k1 * (1 - b + b * dl / max(avg_dl, 1))
                score += idf * numerator / denominator
            scores.append(score)

        # Normalize to [0, 1]
        mx = max(scores) if scores else 1.0
        if mx > 0:
            scores = [s / mx for s in scores]
        return scores


# ── Helpers ──

def _tokenize(text: str) -> List[str]:
    """Simple whitespace + lowercase tokenizer for BM25."""
    return [w.lower() for w in re.findall(r'\w{2,}', text)]


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_ranker: EmbeddingRanker | None = None


def get_embedding_ranker() -> EmbeddingRanker:
    global _ranker
    if _ranker is None:
        _ranker = EmbeddingRanker()
    return _ranker
