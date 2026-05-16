# -*- coding: utf-8 -*-
"""
Lina Processing — Passage Splitter (v3).

Splits clean text into passage-level chunks for embedding ranking.

v3 re-exports the v2 implementation and adds passage scoring.
"""

from __future__ import annotations

import re
import logging
from typing import List

from lina.models.datatypes import Passage

# Re-export v2 core functions
from lina.utils.text_splitter import (
    split_into_passages,
    split_sentences,
    estimate_word_count,
)

logger = logging.getLogger("lina.processing.passage_splitter")


class PassageSplitter:
    """
    v3 passage splitter with Passage object creation.

    Wraps v2 text_splitter and converts raw text chunks to
    typed Passage objects with metadata.
    """

    def __init__(
        self,
        min_words: int = 15,
        max_words: int = 200,
        overlap_sentences: int = 1,
        max_passages_per_page: int = 30,
    ):
        self._min_words = min_words
        self._max_words = max_words
        self._overlap = overlap_sentences
        self._max_per_page = max_passages_per_page

    def split(
        self,
        text: str,
        source_url: str = "",
        source_title: str = "",
    ) -> List[Passage]:
        """
        Split text into Passage objects.

        Args:
            text: Clean text (from html_cleaner).
            source_url: Source URL for provenance.
            source_title: Source page title.

        Returns:
            List of Passage objects.
        """
        if not text or not text.strip():
            return []

        chunks = split_into_passages(
            text,
            min_words=self._min_words,
            max_words=self._max_words,
            overlap_sentences=self._overlap,
        )

        passages: List[Passage] = []
        offset = 0

        for chunk in chunks[:self._max_per_page]:
            if not chunk or not chunk.strip():
                continue

            # Find offset in original text
            idx = text.find(chunk[:50], offset)
            if idx >= 0:
                offset = idx

            p = Passage(
                text=chunk.strip(),
                source_url=source_url,
                source_title=source_title,
                char_offset=max(offset, 0),
            )
            passages.append(p)

        logger.debug(
            "PassageSplitter: %d chars → %d passages (source=%s)",
            len(text), len(passages), source_url[:40],
        )
        return passages


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_splitter: PassageSplitter | None = None

def get_passage_splitter() -> PassageSplitter:
    global _splitter
    if _splitter is None:
        _splitter = PassageSplitter()
    return _splitter


__all__ = [
    "split_into_passages", "split_sentences", "estimate_word_count",
    "PassageSplitter", "get_passage_splitter",
]
