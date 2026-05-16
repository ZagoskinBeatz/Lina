# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Semantic Chunker.

Token-aware text chunking optimized for Retrieval-Augmented Generation.

Design:
  - Target chunk size: 200–400 tokens with 40–80 token overlap
  - Splits on semantic boundaries: paragraphs > headings > sentences
  - Never breaks mid-sentence
  - Preserves structured blocks (KV pairs, tables, lists) as atomic units
  - Each chunk carries provenance metadata (source URL, title, position)

Algorithm:
  1. Detect and isolate structured blocks (KV lines, bullet lists)
  2. Split text into semantic sections (by headings)
  3. Within sections, split into paragraphs
  4. Within paragraphs, split into sentences
  5. Greedily fill chunks to target size, respecting boundaries
  6. Add overlap from previous chunk's last sentences

No LLM calls. Fully deterministic.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from lina.models.datatypes import Passage

logger = logging.getLogger("lina.web_extraction.semantic_chunker")


# ═══════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════

# Token estimation: ~1.3 tokens per word for English, ~1.5 for Russian
# Using 1.4 as average for mixed content
_TOKENS_PER_WORD = 1.4

# Sentence-ending patterns (handles . ! ? …)
_SENT_END = re.compile(r'(?<=[.!?…])\s+')

# Paragraph boundary (double+ newline)
_PARA_SPLIT = re.compile(r'\n\s*\n')

# Heading pattern (## Heading from content extractor output)
_HEADING_RE = re.compile(r'^#{1,6}\s+(.+)$', re.MULTILINE)

# Structured line patterns
_KV_LINE_RE = re.compile(r'^[A-ZА-ЯЁa-zа-яё][\w\s]{2,40}:\s*.{3,}$')
_BULLET_LINE_RE = re.compile(r'^[•\-\*]\s+.{3,}$')

# Abbreviation protection for sentence splitting
_ABBREV_PROTECT = [
    (re.compile(r'(\d)\.(\d)'), r'\1<DOT>\2'),           # 3.14
    (re.compile(r'\b(т)\.(д|п|е|к|н)'), r'\1<DOT>\2'),   # т.д.
    (re.compile(r'\b(др|пр|см|рис|табл)\.'), r'\1<DOT>'), # др.
    (re.compile(r'\b(Mr|Ms|Mrs|Dr|Prof|Inc|Corp|Ltd|Jr|Sr)\.'), r'\1<DOT>'),
    (re.compile(r'\b(vs|etc|approx|est|min|max)\.'), r'\1<DOT>'),
]


# ═══════════════════════════════════════════════════
#  Data types
# ═══════════════════════════════════════════════════

@dataclass
class ChunkMetadata:
    """Metadata about a text chunk's position and context."""
    section_title: str = ""        # Heading of the section this chunk belongs to
    chunk_index: int = 0           # Position within the document (0-based)
    total_chunks: int = 0          # Total chunks from this document
    has_overlap: bool = False      # Whether this chunk has overlap from previous
    is_structured: bool = False    # Contains structured data (KV pairs, tables)
    estimated_tokens: int = 0      # Estimated token count


@dataclass
class SemanticChunk:
    """An enriched text chunk with metadata for RAG."""
    text: str
    source_url: str = ""
    source_title: str = ""
    section_title: str = ""
    metadata: ChunkMetadata = field(default_factory=ChunkMetadata)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def estimated_tokens(self) -> int:
        return int(self.word_count * _TOKENS_PER_WORD)

    def to_passage(self, score: float = 0.0) -> Passage:
        """Convert to existing Passage dataclass for pipeline compatibility."""
        return Passage(
            text=self.text,
            source_url=self.source_url,
            source_title=self.source_title,
            score=score,
            word_count=self.word_count,
        )


# ═══════════════════════════════════════════════════
#  Semantic Chunker
# ═══════════════════════════════════════════════════

class SemanticChunker:
    """
    Token-aware semantic text chunker optimized for RAG retrieval.

    Chunk sizes target 200–400 tokens with 40–80 token overlap to ensure
    that context is preserved across chunk boundaries. Splitting always
    respects semantic boundaries — sentences are never broken.

    Usage:
        chunker = SemanticChunker()
        chunks = chunker.chunk(text, source_url="...", source_title="...")
        passages = chunker.chunk_to_passages(text, source_url="...", source_title="...")
    """

    def __init__(
        self,
        target_tokens: int = 300,
        min_tokens: int = 60,
        max_tokens: int = 450,
        overlap_tokens: int = 60,
        max_chunks_per_doc: int = 40,
    ):
        """
        Args:
            target_tokens: Ideal chunk size in tokens (200–400 range).
            min_tokens: Minimum viable chunk size (smaller chunks are merged).
            max_tokens: Hard maximum — chunks exceeding this are split.
            overlap_tokens: Tokens to overlap between consecutive chunks.
            max_chunks_per_doc: Maximum chunks from one document.
        """
        self._target_tokens = target_tokens
        self._min_tokens = min_tokens
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens
        self._max_chunks = max_chunks_per_doc

        # Convert token limits to approximate word counts
        self._target_words = int(target_tokens / _TOKENS_PER_WORD)
        self._min_words = int(min_tokens / _TOKENS_PER_WORD)
        self._max_words = int(max_tokens / _TOKENS_PER_WORD)
        self._overlap_words = int(overlap_tokens / _TOKENS_PER_WORD)

    def chunk(
        self,
        text: str,
        source_url: str = "",
        source_title: str = "",
    ) -> List[SemanticChunk]:
        """
        Split text into semantic chunks with metadata.

        Algorithm:
          1. Isolate structured blocks (KV pairs, bullet lists)
          2. Split into sections by headings
          3. Within sections, split into paragraphs
          4. Greedily fill chunks to target size, never breaking sentences
          5. Add overlap from previous chunk

        Args:
            text: Clean text (output of ContentExtractor).
            source_url: Source page URL.
            source_title: Source page title.

        Returns:
            List of SemanticChunk objects.
        """
        if not text or not text.strip():
            return []

        # Step 1: Pre-process — group structured blocks
        processed = self._group_structured_blocks(text)

        # Step 2: Split into sections by headings
        sections = self._split_into_sections(processed)

        # Step 3: Process each section into chunks
        all_chunks: List[SemanticChunk] = []
        prev_overlap_sentences: List[str] = []

        for section_title, section_text in sections:
            if not section_text.strip():
                continue

            chunks, prev_overlap_sentences = self._chunk_section(
                section_text,
                section_title=section_title,
                prev_overlap=prev_overlap_sentences,
            )

            for chunk in chunks:
                chunk.source_url = source_url
                chunk.source_title = source_title
                all_chunks.append(chunk)

        # Step 4: Merge undersized trailing chunks
        all_chunks = self._merge_small_chunks(all_chunks)

        # Step 5: Assign indices and enforce limits
        total = min(len(all_chunks), self._max_chunks)
        result = all_chunks[:total]
        for i, chunk in enumerate(result):
            chunk.metadata.chunk_index = i
            chunk.metadata.total_chunks = total
            chunk.metadata.estimated_tokens = chunk.estimated_tokens

        return result

    def chunk_to_passages(
        self,
        text: str,
        source_url: str = "",
        source_title: str = "",
    ) -> List[Passage]:
        """
        Convenience method: chunk text and convert to Passage objects.

        Compatible with existing pipeline_v3 Passage interface.
        """
        chunks = self.chunk(text, source_url=source_url, source_title=source_title)
        return [c.to_passage() for c in chunks]

    # ═══════════════════════════════════════════════
    #  Section splitting
    # ═══════════════════════════════════════════════

    def _split_into_sections(self, text: str) -> List[Tuple[str, str]]:
        """
        Split text into sections by headings.

        Returns:
            List of (section_title, section_text) tuples.
            First section may have empty title if text starts without heading.
        """
        sections: List[Tuple[str, str]] = []
        current_title = ""
        current_lines: List[str] = []

        for line in text.split("\n"):
            heading_match = _HEADING_RE.match(line.strip())
            if heading_match:
                # Flush previous section
                if current_lines:
                    section_text = "\n".join(current_lines).strip()
                    if section_text:
                        sections.append((current_title, section_text))
                current_title = heading_match.group(1).strip()
                current_lines = []
            else:
                current_lines.append(line)

        # Flush last section
        if current_lines:
            section_text = "\n".join(current_lines).strip()
            if section_text:
                sections.append((current_title, section_text))

        return sections if sections else [("", text)]

    # ═══════════════════════════════════════════════
    #  Core chunking algorithm
    # ═══════════════════════════════════════════════

    def _chunk_section(
        self,
        text: str,
        section_title: str = "",
        prev_overlap: List[str] | None = None,
    ) -> Tuple[List[SemanticChunk], List[str]]:
        """
        Greedily chunk a section of text.

        Algorithm:
          1. Split section into paragraphs
          2. For each paragraph:
             a. If paragraph fits in current chunk budget → add it
             b. If paragraph is too large → split on sentences
             c. If adding exceeds max → flush current chunk, start new
          3. Prepend overlap sentences from previous chunk

        Returns:
            (chunks, last_overlap_sentences) — overlap for next section.
        """
        paragraphs = _PARA_SPLIT.split(text.strip())
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        # Handle single-paragraph text with many newlines
        if len(paragraphs) == 1 and paragraphs[0].count("\n") > 5:
            paragraphs = self._infer_paragraphs(paragraphs[0])

        chunks: List[SemanticChunk] = []
        current_parts: List[str] = []
        current_wc = 0
        has_overlap = False

        # Prepend overlap from previous chunk
        if prev_overlap:
            overlap_text = " ".join(prev_overlap)
            overlap_wc = len(overlap_text.split())
            if overlap_wc <= self._overlap_words * 2:
                current_parts.append(overlap_text)
                current_wc += overlap_wc
                has_overlap = True

        for para in paragraphs:
            para_wc = len(para.split())

            # Check if paragraph is a structured block (KV/bullet lines)
            is_structured = self._is_structured_block(para)

            # Case 1: Paragraph fits in current chunk
            if current_wc + para_wc <= self._max_words:
                current_parts.append(para)
                current_wc += para_wc

                # Flush if we hit target size
                if current_wc >= self._target_words:
                    chunk = self._make_chunk(
                        current_parts, section_title, has_overlap, is_structured,
                    )
                    chunks.append(chunk)
                    # Prepare overlap for next chunk
                    overlap_sents = self._get_overlap_sentences(current_parts)
                    current_parts = []
                    current_wc = 0
                    has_overlap = False
                    # Prepend overlap
                    if overlap_sents:
                        overlap_text = " ".join(overlap_sents)
                        current_parts.append(overlap_text)
                        current_wc = len(overlap_text.split())
                        has_overlap = True

                continue

            # Case 2: Need to flush current chunk first
            if current_parts:
                chunk = self._make_chunk(
                    current_parts, section_title, has_overlap, False,
                )
                chunks.append(chunk)
                overlap_sents = self._get_overlap_sentences(current_parts)
                current_parts = []
                current_wc = 0
                has_overlap = False
                if overlap_sents:
                    overlap_text = " ".join(overlap_sents)
                    current_parts.append(overlap_text)
                    current_wc = len(overlap_text.split())
                    has_overlap = True

            # Case 3: Paragraph alone exceeds max → split on sentences
            if para_wc > self._max_words:
                sent_chunks = self._split_long_paragraph(
                    para, section_title, is_structured,
                )
                chunks.extend(sent_chunks)
                # Get overlap from last sentence chunk
                if sent_chunks:
                    last_text = sent_chunks[-1].text
                    overlap_sents = self._get_overlap_sentences([last_text])
                    if overlap_sents:
                        overlap_text = " ".join(overlap_sents)
                        current_parts = [overlap_text]
                        current_wc = len(overlap_text.split())
                        has_overlap = True
            else:
                current_parts.append(para)
                current_wc += para_wc

        # Flush remaining
        if current_parts:
            text_joined = "\n\n".join(current_parts).strip()
            if len(text_joined.split()) >= self._min_words:
                chunk = self._make_chunk(
                    current_parts, section_title, has_overlap, False,
                )
                chunks.append(chunk)

        # Return overlap for next section
        last_overlap: List[str] = []
        if chunks:
            last_overlap = self._get_overlap_sentences([chunks[-1].text])

        return chunks, last_overlap

    def _split_long_paragraph(
        self,
        text: str,
        section_title: str,
        is_structured: bool,
    ) -> List[SemanticChunk]:
        """Split a paragraph that exceeds max_words on sentence boundaries."""
        sentences = self._split_sentences(text)
        if not sentences:
            return []

        chunks: List[SemanticChunk] = []
        current: List[str] = []
        current_wc = 0

        for sent in sentences:
            sent_wc = len(sent.split())

            if current_wc + sent_wc > self._max_words and current:
                chunk = self._make_chunk(
                    current, section_title, bool(chunks), is_structured,
                )
                chunks.append(chunk)
                # Overlap
                overlap = current[-2:] if len(current) >= 2 else current[-1:]
                current = list(overlap)
                current_wc = sum(len(s.split()) for s in current)

            current.append(sent)
            current_wc += sent_wc

        if current and current_wc >= self._min_words:
            chunk = self._make_chunk(
                current, section_title, bool(chunks), is_structured,
            )
            chunks.append(chunk)

        return chunks

    # ═══════════════════════════════════════════════
    #  Helpers
    # ═══════════════════════════════════════════════

    def _make_chunk(
        self,
        parts: List[str],
        section_title: str,
        has_overlap: bool,
        is_structured: bool,
    ) -> SemanticChunk:
        """Create a SemanticChunk from text parts."""
        text = "\n\n".join(p for p in parts if p.strip())
        return SemanticChunk(
            text=text,
            section_title=section_title,
            metadata=ChunkMetadata(
                section_title=section_title,
                has_overlap=has_overlap,
                is_structured=is_structured,
            ),
        )

    def _get_overlap_sentences(self, parts: List[str]) -> List[str]:
        """Get last N sentences for overlap, targeting overlap_words tokens."""
        full_text = " ".join(parts)
        sentences = self._split_sentences(full_text)
        if not sentences:
            return []

        # Take sentences from the end until we hit overlap word budget
        overlap: List[str] = []
        wc = 0
        for sent in reversed(sentences):
            sent_wc = len(sent.split())
            if wc + sent_wc > self._overlap_words and overlap:
                break
            overlap.insert(0, sent)
            wc += sent_wc

        return overlap

    def _group_structured_blocks(self, text: str) -> str:
        """
        Group consecutive KV/bullet lines into atomic blocks.

        Consecutive structured lines (Label: Value, • Item) get bounded
        by double-newlines so the paragraph splitter treats them as one unit.
        """
        lines = text.split("\n")
        result: List[str] = []
        block: List[str] = []
        in_structured = False

        for line in lines:
            stripped = line.strip()
            is_struct = bool(
                _KV_LINE_RE.match(stripped) or _BULLET_LINE_RE.match(stripped)
            )

            if is_struct:
                if not in_structured and block:
                    result.append("\n".join(block))
                    result.append("")
                    block = []
                block.append(stripped)
                in_structured = True
            else:
                if in_structured and block:
                    result.append("\n".join(block))
                    result.append("")
                    block = []
                    in_structured = False
                block.append(line)

        if block:
            result.append("\n".join(block))

        return "\n".join(result)

    def _is_structured_block(self, text: str) -> bool:
        """Check if text is a structured block (KV pairs or bullet list)."""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if len(lines) < 2:
            return False
        structured = sum(
            1 for l in lines
            if _KV_LINE_RE.match(l) or _BULLET_LINE_RE.match(l)
        )
        return structured / len(lines) >= 0.6

    def _infer_paragraphs(self, text: str) -> List[str]:
        """Infer paragraph boundaries for single-newline text."""
        lines = text.split("\n")
        paragraphs: List[str] = []
        current: List[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if current:
                    paragraphs.append("\n".join(current))
                    current = []
                continue

            # Heading → new paragraph
            if _HEADING_RE.match(stripped):
                if current:
                    paragraphs.append("\n".join(current))
                    current = []
                current.append(stripped)
                continue

            # Topic shift heuristic
            if current and len(current) >= 3:
                prev = current[-1].strip()
                if (prev and prev[-1] not in ".!?…:;,"
                        and stripped[0:1].isupper()
                        and len(stripped) > 30):
                    paragraphs.append("\n".join(current))
                    current = []

            current.append(stripped)

        if current:
            paragraphs.append("\n".join(current))

        return paragraphs if len(paragraphs) > 1 else [text]

    def _split_sentences(self, text: str) -> List[str]:
        """
        Split text into sentences respecting abbreviations and decimals.

        Protected patterns: 3.14, т.д., т.п., Mr., etc.
        """
        protected = text
        for pattern, replacement in _ABBREV_PROTECT:
            protected = pattern.sub(replacement, protected)

        parts = _SENT_END.split(protected)
        sentences = []
        for p in parts:
            s = p.replace("<DOT>", ".").strip()
            if s:
                sentences.append(s)
        return sentences

    def _merge_small_chunks(self, chunks: List[SemanticChunk]) -> List[SemanticChunk]:
        """Merge consecutive undersized chunks."""
        if len(chunks) <= 1:
            return chunks

        merged: List[SemanticChunk] = []
        buffer: SemanticChunk | None = None

        for chunk in chunks:
            if buffer is None:
                buffer = chunk
                continue

            if buffer.word_count < self._min_words:
                # Merge with current chunk
                combined_text = buffer.text + "\n\n" + chunk.text
                buffer = SemanticChunk(
                    text=combined_text,
                    source_url=chunk.source_url,
                    source_title=chunk.source_title,
                    section_title=buffer.section_title or chunk.section_title,
                    metadata=ChunkMetadata(
                        section_title=buffer.section_title or chunk.section_title,
                        has_overlap=buffer.metadata.has_overlap,
                        is_structured=(
                            buffer.metadata.is_structured or chunk.metadata.is_structured
                        ),
                    ),
                )
            else:
                merged.append(buffer)
                buffer = chunk

        if buffer is not None:
            merged.append(buffer)

        return merged


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_chunker: SemanticChunker | None = None


def get_semantic_chunker() -> SemanticChunker:
    global _chunker
    if _chunker is None:
        _chunker = SemanticChunker()
    return _chunker
