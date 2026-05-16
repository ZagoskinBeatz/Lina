# -*- coding: utf-8 -*-
"""
Lina Memory — Conversation State (v2 Pipeline).

Tracks multi-turn conversation context:
  - Topic continuity (what are we discussing)
  - Active entities (e.g. "Realme 10", "Snapdragon 680")
  - Recent facts linked to topic
  - Turn history with timestamps

This enables follow-up queries like:
  Q: "Расскажи про Realme 10"
  Q: "А какой у него процессор?"  ← resolves "него" → "Realme 10"
  Q: "А сколько стоит?"            ← same context

Design: in-memory state, not persisted (use FactStore for persistence).
Thread-safe via simple locking.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Set

from lina.models.datatypes import ConversationTurn, Fact, IntentType

logger = logging.getLogger("lina.memory.conversation_state")


class ConversationState:
    """
    Stateful conversation tracker.

    Maintains sliding window of turns and active topic/entity context.
    """

    def __init__(self, max_turns: int = 10):
        self._max_turns = max_turns
        self._turns: List[ConversationTurn] = []
        self._current_topic: str = ""
        self._active_entities: Dict[str, float] = {}  # entity → last_seen timestamp
        self._topic_facts: Dict[str, List[Fact]] = {}  # topic → facts
        self._lock = threading.Lock()

    # ── Public API ──

    def add_turn(self, turn: ConversationTurn) -> None:
        """Record a new conversation turn."""
        with self._lock:
            self._turns.append(turn)
            if len(self._turns) > self._max_turns:
                self._turns = self._turns[-self._max_turns:]

            # Update topic if present
            if turn.topic:
                self._current_topic = turn.topic

            # Track entities
            now = time.time()
            for ent in turn.entities:
                self._active_entities[ent] = now

            # Track facts per topic
            if turn.topic and turn.facts:
                if turn.topic not in self._topic_facts:
                    self._topic_facts[turn.topic] = []
                self._topic_facts[turn.topic].extend(turn.facts)
                # Limit facts per topic
                self._topic_facts[turn.topic] = self._topic_facts[turn.topic][-50:]

    @property
    def current_topic(self) -> str:
        with self._lock:
            return self._current_topic

    @property
    def active_entities(self) -> List[str]:
        """Entities mentioned in the last 5 minutes."""
        cutoff = time.time() - 300
        with self._lock:
            return [
                e for e, ts in self._active_entities.items()
                if ts > cutoff
            ]

    @property
    def last_turn(self) -> Optional[ConversationTurn]:
        with self._lock:
            return self._turns[-1] if self._turns else None

    @property
    def turn_count(self) -> int:
        with self._lock:
            return len(self._turns)

    def get_recent_facts(self, topic: str = "") -> List[Fact]:
        """Get facts from the current or specified topic."""
        t = topic or self._current_topic
        with self._lock:
            return list(self._topic_facts.get(t, []))

    def resolve_pronoun_subject(self, query: str) -> str:
        """
        Try to resolve pronoun references in the query.

        E.g. "какой у него процессор?" → "какой у Realme 10 процессор?"

        Returns the query (possibly modified).
        """
        # Common RU pronouns that may reference the topic entity
        pronouns = [
            "у него", "у неё", "у нее", "у этого", "у этой",
            "его", "её", "ее", "этого", "этой",
            "it", "this", "that", "its",
        ]

        if not self._current_topic:
            return query

        q_lower = query.lower()
        for pron in pronouns:
            if pron in q_lower:
                # Replace pronoun with topic entity
                # Preserve casing of original query
                idx = q_lower.index(pron)
                replacement = f"у {self._current_topic}" if pron.startswith("у ") else self._current_topic
                resolved = query[:idx] + replacement + query[idx + len(pron):]
                logger.info(
                    "ConversationState: resolved '%s' → '%s'",
                    pron, self._current_topic,
                )
                return resolved

        return query

    def build_context_hint(self) -> str:
        """
        Build a context hint string for the query rewriter.

        Returns something like:
        "Current topic: Realme 10. Active entities: Realme 10, Snapdragon 680."
        """
        parts = []
        if self._current_topic:
            parts.append(f"Topic: {self._current_topic}")
        ents = self.active_entities
        if ents:
            parts.append(f"Entities: {', '.join(ents[:5])}")
        return ". ".join(parts) if parts else ""

    def clear(self) -> None:
        """Reset conversation state."""
        with self._lock:
            self._turns.clear()
            self._current_topic = ""
            self._active_entities.clear()
            self._topic_facts.clear()

    def to_dict(self) -> dict:
        """Serialize for debugging/logging."""
        with self._lock:
            return {
                "topic": self._current_topic,
                "entities": list(self._active_entities.keys()),
                "turns": self.turn_count,
                "facts": {
                    k: len(v) for k, v in self._topic_facts.items()
                },
            }


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_state: ConversationState | None = None


def get_conversation_state() -> ConversationState:
    global _state
    if _state is None:
        _state = ConversationState()
    return _state
