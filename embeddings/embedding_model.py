# -*- coding: utf-8 -*-
"""
Lina Embeddings — Embedding Model (v3).

Abstraction layer for embedding model loading and management.

Backends (auto-detected, priority order):
  1. sentence-transformers — best quality
  2. sklearn TfidfVectorizer — decent, no GPU
  3. Pure-Python tf-idf — always works

Design:
  - Lazy loading (model loads on first encode() call)
  - Thread-safe
  - Configurable model name
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import Counter
from typing import List, Optional

logger = logging.getLogger("lina.embeddings.embedding_model")


# ═══════════════════════════════════════════════════
#  Backend detection
# ═══════════════════════════════════════════════════

_BACKEND: Optional[str] = None
_np = None

try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    _np = np
    _BACKEND = "sentence_transformers"
except ImportError:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np
        _np = np
        _BACKEND = "sklearn"
    except ImportError:
        _BACKEND = "python"

logger.info("EmbeddingModel backend: %s", _BACKEND)


class EmbeddingModel:
    """
    Loads and manages an embedding model.

    Usage:
        model = EmbeddingModel()
        vecs = model.encode(["hello world", "test query"])
        sim = model.similarity("query", ["passage1", "passage2"])
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._backend = _BACKEND
        self._st_model = None
        self._lock = threading.Lock()

    @property
    def backend(self) -> str:
        return self._backend

    def encode(self, texts: List[str]) -> list:
        """
        Encode texts to vectors.

        Returns:
            For sentence_transformers: numpy array (N, D).
            For sklearn/python: list of dicts (sparse TF-IDF).
        """
        if self._backend == "sentence_transformers":
            return self._encode_st(texts)
        elif self._backend == "sklearn":
            return self._encode_sklearn(texts)
        return self._encode_python(texts)

    def similarity(
        self,
        query: str,
        texts: List[str],
    ) -> List[float]:
        """
        Compute similarity between query and each text.

        Returns:
            List of floats [0, 1], one per text.
        """
        if not texts or not query:
            return [0.0] * len(texts)

        if self._backend == "sentence_transformers":
            return self._sim_st(query, texts)
        elif self._backend == "sklearn":
            return self._sim_sklearn(query, texts)
        return self._sim_python(query, texts)

    # ── sentence-transformers ──

    def _load_st(self):
        with self._lock:
            if self._st_model is None:
                logger.info("Loading sentence-transformers: %s", self._model_name)
                from sentence_transformers import SentenceTransformer
                self._st_model = SentenceTransformer(self._model_name)

    def _encode_st(self, texts: List[str]):
        self._load_st()
        return self._st_model.encode(
            texts, convert_to_numpy=True,
            batch_size=32, show_progress_bar=False,
        )

    def _sim_st(self, query: str, texts: List[str]) -> List[float]:
        self._load_st()
        import numpy as np
        q_emb = self._st_model.encode([query], convert_to_numpy=True)
        t_embs = self._st_model.encode(texts, convert_to_numpy=True,
                                        batch_size=32, show_progress_bar=False)
        sims = np.dot(t_embs, q_emb.T).flatten()
        norms_t = np.linalg.norm(t_embs, axis=1)
        norms_q = np.linalg.norm(q_emb)
        with np.errstate(divide="ignore", invalid="ignore"):
            sims = sims / (norms_t * norms_q)
        sims = np.nan_to_num(sims, nan=0.0)
        return sims.tolist()

    # ── sklearn ──

    def _encode_sklearn(self, texts: List[str]):
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(max_features=10000, sublinear_tf=True, ngram_range=(1, 2))
        try:
            return vec.fit_transform(texts)
        except ValueError:
            return None

    def _sim_sklearn(self, query: str, texts: List[str]) -> List[float]:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        corpus = [query] + texts
        vec = TfidfVectorizer(max_features=10000, sublinear_tf=True, ngram_range=(1, 2))
        try:
            tfidf = vec.fit_transform(corpus)
        except ValueError:
            return [0.0] * len(texts)
        sims = cosine_similarity(tfidf[0:1], tfidf[1:]).flatten()
        return sims.tolist()

    # ── pure Python BM25 ──

    def _encode_python(self, texts: List[str]):
        """Return tokenized representations."""
        return [_tokenize(t) for t in texts]

    def _sim_python(self, query: str, texts: List[str]) -> List[float]:
        """BM25-like scoring."""
        query_terms = _tokenize(query)
        if not query_terms:
            return [0.0] * len(texts)

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

        mx = max(scores) if scores else 1.0
        if mx > 0:
            scores = [s / mx for s in scores]
        return scores


def _tokenize(text: str) -> List[str]:
    return [w.lower() for w in re.findall(r'\w{2,}', text)]


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_model: EmbeddingModel | None = None

def get_embedding_model() -> EmbeddingModel:
    global _model
    if _model is None:
        _model = EmbeddingModel()
    return _model
