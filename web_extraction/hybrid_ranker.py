# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Hybrid Ranker.

Two-stage ranking pipeline optimized for RAG retrieval quality:
  Stage 1 (Recall): Fast BM25 scoring — selects top-N candidates cheaply
  Stage 2 (Precision): Semantic re-ranking — embedding similarity on candidates

This hybrid approach gives BM25's recall (exact keyword matching) combined
with embeddings' semantic understanding (paraphrases, synonyms, multilingual).

Scoring fusion:
  final_score = α × bm25_normalized + (1 - α) × embedding_similarity

Backend priority for Stage 2:
  1. sentence-transformers (all-MiniLM-L6-v2) — best quality
  2. sklearn TF-IDF cosine similarity — decent, no GPU
  3. Pure-Python BM25 only (no re-ranking) — always works

No LLM calls. Fully deterministic.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter
from typing import List, Optional, Tuple

from lina.models.datatypes import Passage

logger = logging.getLogger("lina.web_extraction.hybrid_ranker")


# ═══════════════════════════════════════════════════
#  Backend detection
# ═══════════════════════════════════════════════════

_EMBEDDING_BACKEND: Optional[str] = None

try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    _EMBEDDING_BACKEND = "sentence_transformers"
except ImportError:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity as _sk_cosine
        import numpy as np
        _EMBEDDING_BACKEND = "sklearn"
    except ImportError:
        _EMBEDDING_BACKEND = None

logger.info("HybridRanker embedding backend: %s", _EMBEDDING_BACKEND or "none (BM25 only)")


# ═══════════════════════════════════════════════════
#  BM25 Implementation
# ═══════════════════════════════════════════════════

class BM25:
    """
    Okapi BM25 scorer for passage ranking.

    Parameters:
      k1 = 1.5 (term frequency saturation)
      b  = 0.75 (document length normalization)

    Supports both English and Russian tokenization.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

    def score(self, passages: List[str], query: str) -> List[float]:
        """
        Score passages against query using BM25.

        Args:
            passages: List of passage texts.
            query: User query.

        Returns:
            List of BM25 scores (unnormalized).
        """
        query_terms = _tokenize(query)
        if not query_terms or not passages:
            return [0.0] * len(passages)

        # Build index
        doc_count = len(passages)
        df: Counter = Counter()
        doc_term_freqs: List[Counter] = []
        doc_lengths: List[int] = []

        for text in passages:
            terms = _tokenize(text)
            doc_lengths.append(len(terms))
            tf = Counter(terms)
            doc_term_freqs.append(tf)
            for term in set(terms):
                df[term] += 1

        avg_dl = sum(doc_lengths) / max(doc_count, 1)

        # Score each document
        scores: List[float] = []
        for i, tf in enumerate(doc_term_freqs):
            score = 0.0
            dl = doc_lengths[i]
            for qt in query_terms:
                n = df.get(qt, 0)
                if n == 0:
                    continue
                idf = math.log((doc_count - n + 0.5) / (n + 0.5) + 1.0)
                freq = tf.get(qt, 0)
                numerator = freq * (self.k1 + 1)
                denominator = freq + self.k1 * (1 - self.b + self.b * dl / max(avg_dl, 1))
                score += idf * numerator / denominator
            scores.append(score)

        return scores

    def score_normalized(self, passages: List[str], query: str) -> List[float]:
        """Score and normalize to [0, 1]."""
        scores = self.score(passages, query)
        mx = max(scores) if scores else 1.0
        if mx > 0:
            return [s / mx for s in scores]
        return scores


# ═══════════════════════════════════════════════════
#  Hybrid Ranker
# ═══════════════════════════════════════════════════

class HybridRanker:
    """
    Two-stage hybrid ranker: BM25 (recall) → Embedding (precision).

    Stage 1: BM25 selects top-N candidates from all passages.
    Stage 2: Embedding model re-ranks candidates for semantic precision.
    Final score = α × BM25_normalized + (1 - α) × embedding_similarity.

    When no embedding backend is available, uses BM25 only.

    Usage:
        ranker = HybridRanker()
        ranked = ranker.rank(passages, query="what is the processor?", top_k=5)
    """

    def __init__(
        self,
        bm25_weight: float = 0.35,
        embedding_weight: float = 0.65,
        bm25_top_n: int = 25,
        model_name: str = "all-MiniLM-L6-v2",
    ):
        """
        Args:
            bm25_weight: Weight for BM25 in final fusion (α).
            embedding_weight: Weight for embedding similarity (1 - α).
            bm25_top_n: Number of candidates BM25 passes to Stage 2.
            model_name: sentence-transformers model name.
        """
        self._bm25_weight = bm25_weight
        self._embed_weight = embedding_weight
        self._bm25_top_n = bm25_top_n
        self._model_name = model_name
        self._bm25 = BM25()
        self._st_model = None  # Lazy-loaded
        self._backend = _EMBEDDING_BACKEND

    def rank(
        self,
        passages: List[Passage],
        query: str,
        top_k: int = 10,
        min_score: float = 0.15,
    ) -> List[Passage]:
        """
        Rank passages by hybrid BM25 + embedding score.

        Args:
            passages: Input passages to rank.
            query: User query.
            top_k: Return top-K passages.
            min_score: Minimum final score threshold.

        Returns:
            List of Passage objects sorted by score (best first).
        """
        if not passages or not query:
            return passages[:top_k]

        t0 = time.time()
        texts = [p.text for p in passages]

        # ── Stage 1: BM25 candidate selection ──
        bm25_scores = self._bm25.score_normalized(texts, query)

        # Apply BM25 scores and select top candidates
        scored_passages = list(zip(passages, bm25_scores))
        scored_passages.sort(key=lambda x: x[1], reverse=True)

        # Select top-N candidates for Stage 2
        candidates = scored_passages[:self._bm25_top_n]

        if not candidates:
            return []

        # ── Stage 2: Embedding re-ranking ──
        if self._backend and len(candidates) > 1:
            cand_passages = [p for p, _ in candidates]
            cand_bm25 = [s for _, s in candidates]
            cand_texts = [p.text for p in cand_passages]

            embed_scores = self._compute_embeddings(cand_texts, query)

            # Fusion
            for i, p in enumerate(cand_passages):
                p.score = (
                    self._bm25_weight * cand_bm25[i]
                    + self._embed_weight * embed_scores[i]
                )
        else:
            # No embedding backend — BM25 only
            for p, s in candidates:
                p.score = s

        # Sort by final score
        result_passages = [p for p, _ in candidates]
        result_passages.sort(key=lambda p: p.score, reverse=True)

        # Filter by min_score
        result = [p for p in result_passages if p.score >= min_score]

        elapsed = (time.time() - t0) * 1000
        logger.info(
            "HybridRanker [bm25+%s]: %d → %d passages in %.0f ms "
            "(top=%.3f, stage1=%d candidates)",
            self._backend or "none", len(passages), len(result[:top_k]),
            elapsed, result[0].score if result else 0,
            len(candidates),
        )

        return result[:top_k]

    # ── Embedding backends ──

    def _compute_embeddings(self, texts: List[str], query: str) -> List[float]:
        """Compute embedding similarity scores."""
        if self._backend == "sentence_transformers":
            return self._embed_st(texts, query)
        elif self._backend == "sklearn":
            return self._embed_sklearn(texts, query)
        else:
            return [0.0] * len(texts)

    def _embed_st(self, texts: List[str], query: str) -> List[float]:
        """Sentence-transformers embedding similarity."""
        if self._st_model is None:
            logger.info("Loading sentence-transformers model: %s", self._model_name)
            from sentence_transformers import SentenceTransformer
            self._st_model = SentenceTransformer(self._model_name)

        import numpy as np
        query_emb = self._st_model.encode([query], convert_to_numpy=True)
        text_embs = self._st_model.encode(
            texts, convert_to_numpy=True,
            batch_size=32, show_progress_bar=False,
        )
        sims = np.dot(text_embs, query_emb.T).flatten()
        norms_t = np.linalg.norm(text_embs, axis=1)
        norms_q = np.linalg.norm(query_emb)
        with np.errstate(divide="ignore", invalid="ignore"):
            sims = sims / (norms_t * norms_q)
        sims = np.nan_to_num(sims, nan=0.0)
        return sims.tolist()

    def _embed_sklearn(self, texts: List[str], query: str) -> List[float]:
        """TF-IDF cosine similarity (fallback)."""
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

    # ── Linux-aware scoring boost ──

    def rank_linux(
        self,
        passages: List[Passage],
        query: str,
        top_k: int = 10,
        min_score: float = 0.15,
        error_strings: List[str] | None = None,
    ) -> List[Passage]:
        """
        Rank passages for Linux troubleshooting queries.

        Adds bonuses for:
          - Passages containing Linux commands (code blocks)
          - Passages with solution-block indicators
          - Passages matching detected error strings
          - Passages from high-trust Linux domains

        Uses the standard hybrid ranking as base, then adds Linux bonuses.

        Args:
            passages: Input passages.
            query: User query.
            top_k: Return top-K.
            min_score: Minimum score.
            error_strings: Error strings to match in passages (bonus).

        Returns:
            Ranked passages with Linux bonuses applied.
        """
        # Base ranking
        ranked = self.rank(passages, query, top_k=top_k * 3, min_score=0.0)

        # Apply Linux bonuses
        for p in ranked:
            bonus = self._linux_bonus(p.text, error_strings)
            p.score = min(p.score + bonus, 1.0)

        ranked.sort(key=lambda p: p.score, reverse=True)

        # Filter and return
        result = [p for p in ranked if p.score >= min_score]
        return result[:top_k]

    @staticmethod
    def _linux_bonus(text: str, error_strings: List[str] | None = None) -> float:
        """
        Compute Linux-relevance bonus for a passage.

        Scoring:
          +0.08  if contains code block(s) (``` ... ```)
          +0.06  if contains shell prompt ($/#)
          +0.05  if contains known Linux commands (sudo, apt, systemctl, etc.)
          +0.05  if contains solution keywords (fix, solve, resolution)
          +0.04  if contains step-by-step pattern (1. ... 2. ...)
          +0.06  if matches any of the error strings
        """
        bonus = 0.0
        text_lower = text.lower()

        # Code blocks
        if '```' in text or '<pre>' in text_lower:
            bonus += 0.08

        # Shell prompts
        if re.search(r'^\s*[$#]\s+\w', text, re.MULTILINE):
            bonus += 0.06

        # Linux commands
        _LINUX_CMDS = (
            'sudo ', 'apt ', 'apt-get ', 'pacman ', 'dnf ', 'yum ',
            'systemctl ', 'journalctl ', 'service ', 'chmod ',
            'chown ', 'mount ', 'modprobe ', 'iptables ', 'ufw ',
            'nmcli ', 'grub-', 'dkms ', 'dpkg ', 'useradd ',
        )
        if any(cmd in text_lower for cmd in _LINUX_CMDS):
            bonus += 0.05

        # Solution keywords
        _SOL_KW = (
            'solution', 'fix', 'solve', 'resolution', 'workaround',
            'fixed it', 'i fixed', 'to fix',
            'решение', 'исправить', 'исправлено', 'починить',
        )
        if any(kw in text_lower for kw in _SOL_KW):
            bonus += 0.05

        # Step-by-step
        if re.search(r'(?:^|\n)\s*[12]\.\s+\w', text):
            bonus += 0.04

        # Error string match
        if error_strings:
            for err in error_strings:
                if err.lower() in text_lower:
                    bonus += 0.06
                    break

        return bonus


# ═══════════════════════════════════════════════════
#  Utilities
# ═══════════════════════════════════════════════════

def _tokenize(text: str) -> List[str]:
    """Tokenize for BM25: lowercase words ≥2 chars (EN + RU)."""
    return [w.lower() for w in re.findall(r'\w{2,}', text)]


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_ranker: HybridRanker | None = None


def get_hybrid_ranker() -> HybridRanker:
    global _ranker
    if _ranker is None:
        _ranker = HybridRanker()
    return _ranker
