# -*- coding: utf-8 -*-
"""
Lina Utils — Text Splitter.

Splits long text into semantically meaningful passages (paragraphs / chunks).
Used for passage-level retrieval and ranking.

Design: stateless, configurable via parameters.
"""

from __future__ import annotations

import re
import logging
from typing import List

logger = logging.getLogger("lina.utils.text_splitter")

# Sentence-ending regex (handles ., !, ?, …)
_SENT_END = re.compile(r'(?<=[.!?…])\s+')

# Paragraph boundary (double+ newline)
_PARA_SPLIT = re.compile(r'\n\s*\n')

# Single-newline with short lines (common in cleaned HTML):
# Two or more consecutive lines that are each < 80 chars
# indicate missing paragraph breaks (e.g. single-<br> formatted pages)
_SHORT_LINE = re.compile(r'^.{5,79}$', re.MULTILINE)

# Structured line patterns (from enhanced HTML cleaner)
_KV_LINE = re.compile(r'^[A-ZА-ЯЁa-zа-яё][\w\s]{2,30}:\s*.{3,}$')
_BULLET_LINE = re.compile(r'^[•\-\*]\s+.{3,}$')
_HEADING_LINE = re.compile(r'^#{1,4}\s+.{3,}$')


def split_into_passages(
    text: str,
    min_words: int = 15,
    max_words: int = 200,
    overlap_sentences: int = 1,
) -> List[str]:
    """
    Split text into passages suitable for embedding / ranking.

    Strategy:
      1. Detect and preserve structured blocks (KV lines, lists, headings).
      2. Split on paragraph breaks (double newline).
      3. Handle single-newline pages (many short lines → infer paragraphs).
      4. If a paragraph is too long, split on sentences.
      5. If a paragraph is too short, merge with the next.
      6. Add overlap for context continuity.

    Args:
        text: Input text.
        min_words: Skip passages shorter than this.
        max_words: Split passages longer than this.
        overlap_sentences: Overlap with previous chunk (sentences).

    Returns:
        List of passage strings.
    """
    if not text or not text.strip():
        return []

    # ── Pre-process: detect structured blocks ──
    # Group consecutive KV/bullet lines into atomic passages
    text = _group_structured_blocks(text)

    # ── Step 1: paragraph-level split ──
    paragraphs = _PARA_SPLIT.split(text.strip())
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # ── Step 1b: Handle single-newline pages ──
    # If we got only 1 "paragraph" but it has many lines → infer paragraph breaks
    if len(paragraphs) == 1 and paragraphs[0].count("\n") > 5:
        paragraphs = _infer_paragraph_breaks(paragraphs[0])

    # Step 2: process each paragraph
    passages: List[str] = []
    buffer = ""

    for para in paragraphs:
        wc = len(para.split())

        # Too short — accumulate
        if wc < min_words:
            buffer = (buffer + "\n" + para).strip() if buffer else para
            if len(buffer.split()) >= min_words:
                passages.append(buffer)
                buffer = ""
            continue

        # Flush buffer first
        if buffer:
            combined = buffer + "\n" + para
            if len(combined.split()) <= max_words:
                passages.append(combined.strip())
                buffer = ""
                continue
            else:
                passages.append(buffer)
                buffer = ""

        # Too long — split on sentences
        if wc > max_words:
            sentences = _split_sentences(para)
            chunk: List[str] = []
            chunk_wc = 0

            for sent in sentences:
                swc = len(sent.split())
                if chunk_wc + swc > max_words and chunk:
                    passages.append(" ".join(chunk))
                    # Overlap: keep last N sentences
                    chunk = chunk[-overlap_sentences:] if overlap_sentences else []
                    chunk_wc = sum(len(s.split()) for s in chunk)
                chunk.append(sent)
                chunk_wc += swc

            if chunk:
                joined = " ".join(chunk)
                if len(joined.split()) >= min_words:
                    passages.append(joined)
        else:
            passages.append(para)

    # Flush remaining buffer
    if buffer and len(buffer.split()) >= min_words:
        passages.append(buffer)

    return passages


def _group_structured_blocks(text: str) -> str:
    """Group consecutive KV/bullet lines into atomic blocks.

    Consecutive lines matching Key: Value or • Item patterns are
    joined into one block bounded by double-newlines, so the paragraph
    splitter treats them as a single passage.
    """
    lines = text.split("\n")
    result: list[str] = []
    block: list[str] = []
    in_structured = False

    for line in lines:
        stripped = line.strip()
        is_structured = bool(
            _KV_LINE.match(stripped) or _BULLET_LINE.match(stripped)
        )

        if is_structured:
            if not in_structured and block:
                # Flush preceding non-structured text
                result.append("\n".join(block))
                result.append("")  # paragraph break
                block = []
            block.append(stripped)
            in_structured = True
        else:
            if in_structured and block:
                # Flush structured block
                result.append("\n".join(block))
                result.append("")  # paragraph break
                block = []
                in_structured = False
            block.append(line)

    if block:
        result.append("\n".join(block))

    return "\n".join(result)


def _infer_paragraph_breaks(text: str) -> List[str]:
    """Infer paragraph boundaries in text that uses only single newlines.

    Heuristics:
      - Heading lines (## ...) start a new paragraph
      - After a run of KV/bullet lines, start a new paragraph
      - After a blank-ish line or a line ending without punctuation
        followed by a line starting uppercase → new paragraph
    """
    lines = text.split("\n")
    paragraphs: list[str] = []
    current: list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Empty line → paragraph break
        if not stripped:
            if current:
                paragraphs.append("\n".join(current))
                current = []
            continue

        # Heading → new paragraph
        if _HEADING_LINE.match(stripped):
            if current:
                paragraphs.append("\n".join(current))
                current = []
            current.append(stripped)
            continue

        # Topic shift: previous line didn't end with punctuation,
        # current line starts with uppercase letter → possible new paragraph
        if current and len(current) >= 3:
            prev = current[-1].strip()
            if (prev and not prev[-1] in '.!?…:;,' and
                    stripped[0].isupper() and len(stripped) > 30):
                paragraphs.append("\n".join(current))
                current = []

        current.append(stripped)

    if current:
        paragraphs.append("\n".join(current))

    return paragraphs if len(paragraphs) > 1 else [text]


def split_sentences(text: str) -> List[str]:
    """Public API: split text into sentences."""
    return _split_sentences(text)


def _split_sentences(text: str) -> List[str]:
    """
    Split text into sentences.  Handles:
      - Standard punctuation (. ! ?)
      - Abbreviations (approx. / т.д. / т.п.)
      - Numbers (3.14 / 1.5 ГГц)
    """
    # Protect common abbreviations and decimal numbers
    protected = text
    protected = re.sub(r'(\d)\.(\d)', r'\1<DOT>\2', protected)
    protected = re.sub(r'\b(т)\.(д|п|е|к|н)', r'\1<DOT>\2', protected)
    protected = re.sub(r'\b(др|пр|см|рис|табл)\.',
                       r'\1<DOT>', protected)

    parts = _SENT_END.split(protected)
    sentences = []
    for p in parts:
        s = p.replace('<DOT>', '.').strip()
        if s:
            sentences.append(s)
    return sentences


def estimate_word_count(text: str) -> int:
    """Fast word count estimate."""
    return len(text.split())
