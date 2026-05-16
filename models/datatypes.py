# -*- coding: utf-8 -*-
"""
Lina Models — Core Data Types (v2 Pipeline).

Strongly-typed dataclasses for the entire search → fact → answer pipeline.
Every pipeline stage consumes and produces typed objects — no loose dicts.

Design: immutable-friendly, serializable, composable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


# ═══════════════════════════════════════════════════════════
#  Enums
# ═══════════════════════════════════════════════════════════

class IntentType(Enum):
    CHAT = "chat"
    WEB_SEARCH = "web_search"
    SYSTEM_COMMAND = "system_command"
    MATH = "math"
    RAG = "rag"
    DIAGNOSTIC = "diagnostic"
    WEATHER = "weather"
    UNKNOWN = "unknown"


class ConfidenceLevel(Enum):
    HIGH = "high"        # ≥ 0.75
    MEDIUM = "medium"    # 0.50 – 0.74
    LOW = "low"          # 0.25 – 0.49
    NONE = "none"        # < 0.25

    @classmethod
    def from_score(cls, score: float) -> "ConfidenceLevel":
        if score >= 0.75:
            return cls.HIGH
        if score >= 0.50:
            return cls.MEDIUM
        if score >= 0.25:
            return cls.LOW
        return cls.NONE


# ═══════════════════════════════════════════════════════════
#  Search
# ═══════════════════════════════════════════════════════════

@dataclass
class SearchResult:
    """One web search result (snippet-level)."""
    title: str
    url: str
    snippet: str
    relevance: float = 0.0
    content: str = ""           # Full page text (filled after download)
    source_engine: str = ""     # brave / ddgs / wikipedia / searxng
    domain_score: float = 0.5   # Domain reputation [0-1]
    timestamp: float = field(default_factory=time.time)

    def __repr__(self) -> str:
        return f"SearchResult({self.title[:40]!r}, rel={self.relevance:.2f}, src={self.source_engine})"


@dataclass
class QueryPlan:
    """Result of query rewriting — multiple search queries from one user query."""
    original: str
    queries: List[str] = field(default_factory=list)
    detected_entities: List[str] = field(default_factory=list)
    detected_intent: str = ""
    language: str = "ru"

    @property
    def primary(self) -> str:
        return self.queries[0] if self.queries else self.original


# ═══════════════════════════════════════════════════════════
#  Passages
# ═══════════════════════════════════════════════════════════

@dataclass
class Passage:
    """One paragraph / text chunk extracted from a web page."""
    text: str
    source_url: str = ""
    source_title: str = ""
    score: float = 0.0          # Relevance / similarity score
    char_offset: int = 0        # Offset in original page text
    word_count: int = 0

    def __post_init__(self):
        if self.word_count == 0 and self.text:
            self.word_count = len(self.text.split())

    def __repr__(self) -> str:
        preview = self.text[:50].replace("\n", " ")
        return f"Passage({preview!r}..., score={self.score:.2f})"


# ═══════════════════════════════════════════════════════════
#  Facts
# ═══════════════════════════════════════════════════════════

@dataclass
class Fact:
    """One structured fact: (subject, predicate, object)."""
    subject: str              # "Realme 10"
    predicate: str            # "процессор" / "processor"
    object_value: str         # "MediaTek Helio G99"
    sources: List[str] = field(default_factory=list)
    source_count: int = 1
    confidence: float = 0.5
    verified: bool = False

    def key(self) -> str:
        """Normalized key for deduplication / aggregation."""
        return f"{self.subject.lower().strip()}|{self.predicate.lower().strip()}"

    def __repr__(self) -> str:
        v = "✓" if self.verified else "?"
        return (f"Fact[{v}]({self.subject}: {self.predicate} = "
                f"{self.object_value}, conf={self.confidence:.2f}, "
                f"src={self.source_count})")


@dataclass
class FactSet:
    """Aggregated collection of facts about a topic."""
    subject: str
    facts: List[Fact] = field(default_factory=list)
    total_sources: int = 0
    confidence: float = 0.0

    @property
    def verified_facts(self) -> List[Fact]:
        return [f for f in self.facts if f.verified]

    @property
    def verified_count(self) -> int:
        return len(self.verified_facts)

    def get_by_predicate(self, predicate: str) -> Optional[Fact]:
        pred_low = predicate.lower()
        for f in self.facts:
            if f.predicate.lower() == pred_low:
                return f
        return None

    def format_for_llm(self) -> str:
        """Format facts for LLM context injection."""
        if not self.facts:
            return ""
        lines = [f"[VERIFIED FACTS about {self.subject}]"]
        lines.append(f"Sources: {self.total_sources}")
        lines.append(f"Confidence: {self.confidence:.0%}\n")
        for i, fact in enumerate(self.facts, 1):
            mark = "✓" if fact.verified else "?"
            lines.append(
                f"{i}. [{mark}] {fact.subject} — {fact.predicate}: "
                f"{fact.object_value} (sources: {fact.source_count})"
            )
        lines.append("\n[/FACTS]")
        return "\n".join(lines)

    def format_for_llm_ru(self) -> str:
        """Russian-language formatting."""
        if not self.facts:
            return ""
        lines = [f"[ПРОВЕРЕННЫЕ ФАКТЫ: {self.subject}]"]
        lines.append(f"Источников проанализировано: {self.total_sources}")
        lines.append(f"Уверенность: {self.confidence:.0%}\n")
        for i, fact in enumerate(self.facts, 1):
            mark = "✓" if fact.verified else "?"
            lines.append(
                f"{i}. [{mark}] {fact.subject} — {fact.predicate}: "
                f"{fact.object_value} (источников: {fact.source_count})"
            )
        lines.append("\n[/ФАКТЫ]")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
#  Answer
# ═══════════════════════════════════════════════════════════

@dataclass
class PipelineAnswer:
    """Final structured answer from the pipeline."""
    text: str
    facts_used: List[Fact] = field(default_factory=list)
    confidence: float = 0.0
    confidence_level: ConfidenceLevel = ConfidenceLevel.NONE
    sources: List[str] = field(default_factory=list)
    verified: bool = False
    hallucination_flags: List[str] = field(default_factory=list)
    generation_attempts: int = 1
    elapsed_ms: float = 0.0

    def __post_init__(self):
        self.confidence_level = ConfidenceLevel.from_score(self.confidence)

    def is_reliable(self) -> bool:
        """Answer is reliable if verified AND medium+ confidence."""
        return self.verified and self.confidence >= 0.50


# ═══════════════════════════════════════════════════════════
#  Conversation
# ═══════════════════════════════════════════════════════════

@dataclass
class ConversationTurn:
    """One turn in conversation history."""
    query: str
    answer: str
    intent: str = ""
    topic: str = ""
    entities: List[str] = field(default_factory=list)
    facts: List[Fact] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════
#  Pipeline Stages Metadata
# ═══════════════════════════════════════════════════════════

@dataclass
class PipelineTrace:
    """Timing and metadata for each pipeline stage — useful for debugging."""
    stage_timings: Dict[str, float] = field(default_factory=dict)
    search_results_count: int = 0
    passages_count: int = 0
    facts_extracted: int = 0
    facts_verified: int = 0
    errors: List[str] = field(default_factory=list)

    def record(self, stage: str, duration_ms: float) -> None:
        self.stage_timings[stage] = duration_ms

    def total_ms(self) -> float:
        return sum(self.stage_timings.values())


# ═══════════════════════════════════════════════════════════
#  v3 — Query Understanding
# ═══════════════════════════════════════════════════════════

@dataclass
class QueryUnderstanding:
    """Structured semantic representation of a user query (v3)."""
    raw_query: str
    intent: str = ""             # product_spec, comparison, price, review, general_info, opinion
    entities: List[str] = field(default_factory=list)       # ["Realme 10"]
    attributes: List[str] = field(default_factory=list)     # ["processor", "cpu"]
    language: str = "ru"
    query_type: str = "factual"  # factual, opinion, comparison
    need_web_search: bool = True
    confidence: float = 0.0

    def primary_entity(self) -> str:
        return self.entities[0] if self.entities else ""

    def primary_attribute(self) -> str:
        return self.attributes[0] if self.attributes else ""


@dataclass
class RetrievalResult:
    """Merged result from multiple search engines (v3)."""
    results: List[SearchResult] = field(default_factory=list)
    engines_used: List[str] = field(default_factory=list)
    total_raw: int = 0           # total before dedup
    total_deduped: int = 0       # after dedup
    elapsed_ms: float = 0.0

    def top(self, n: int = 10) -> List[SearchResult]:
        return self.results[:n]

