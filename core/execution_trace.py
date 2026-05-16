# -*- coding: utf-8 -*-
"""
Lina Core — Execution Trace (Phase 23).

Фиксация трассировки каждого запроса:
  intent, confidence, execution_path, tokens_prompt,
  tokens_generated, validation_score, regeneration_attempts,
  final_status

Хранит ring-buffer последних N трейсов.
/system trace → последние 5.
"""

import time
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

logger = logging.getLogger("lina.core.execution_trace")


# ═══════════════════════════════════════════════════════════
#  Trace Entry
# ═══════════════════════════════════════════════════════════

@dataclass
class TraceEntry:
    """Одна запись трассировки."""
    trace_id: int = 0
    timestamp: float = field(default_factory=time.time)
    intent: str = ""
    confidence: float = 0.0
    execution_path: str = ""       # LLM | TOOL | SYSTEM | META
    tokens_prompt: int = 0
    tokens_generated: int = 0
    validation_score: float = 1.0
    regeneration_attempts: int = 0
    final_status: str = ""         # success | failed | regenerated | blocked
    duration_ms: float = 0.0
    user_input: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.trace_id,
            "time": round(self.timestamp, 3),
            "intent": self.intent,
            "confidence": round(self.confidence, 2),
            "path": self.execution_path,
            "tokens_prompt": self.tokens_prompt,
            "tokens_gen": self.tokens_generated,
            "val_score": round(self.validation_score, 2),
            "regen": self.regeneration_attempts,
            "status": self.final_status,
            "ms": round(self.duration_ms, 1),
        }
        if self.error:
            d["error"] = self.error
        return d

    def format(self) -> str:
        """Человекочитаемый формат для /system trace."""
        lines = [
            f"  #{self.trace_id} [{self.final_status}] {self.intent} "
            f"(conf={self.confidence:.2f}) via {self.execution_path}",
            f"    tokens: prompt={self.tokens_prompt} gen={self.tokens_generated} "
            f"| val={self.validation_score:.2f} regen={self.regeneration_attempts} "
            f"| {self.duration_ms:.0f}ms",
        ]
        if self.error:
            lines.append(f"    error: {self.error}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
#  Execution Tracer
# ═══════════════════════════════════════════════════════════

class ExecutionTracer:
    """Ring-buffer трассировки выполнения (Phase 23).

    Usage:
        tracer = ExecutionTracer(max_entries=50)
        entry = tracer.start("chat", 0.8, "LLM", "Привет")
        # ... выполнение ...
        tracer.complete(entry, tokens_prompt=120, tokens_generated=45,
                        validation_score=0.9, final_status="success")
    """

    def __init__(self, max_entries: int = 50):
        self._buffer: deque = deque(maxlen=max_entries)
        self._counter: int = 0
        self._total_tokens_prompt: int = 0
        self._total_tokens_generated: int = 0
        self._total_requests: int = 0
        self._error_count: int = 0

    def start(
        self, intent: str, confidence: float,
        execution_path: str, user_input: str = "",
    ) -> TraceEntry:
        """Начинает новую трассировку.

        Returns:
            TraceEntry (мутабельный, заполняется по ходу выполнения).
        """
        self._counter += 1
        entry = TraceEntry(
            trace_id=self._counter,
            intent=intent,
            confidence=confidence,
            execution_path=execution_path,
            user_input=user_input[:200],
        )
        return entry

    def complete(
        self, entry: TraceEntry, *,
        tokens_prompt: int = 0,
        tokens_generated: int = 0,
        validation_score: float = 1.0,
        regeneration_attempts: int = 0,
        final_status: str = "success",
        error: Optional[str] = None,
    ) -> None:
        """Завершает трассировку и помещает в буфер."""
        entry.tokens_prompt = tokens_prompt
        entry.tokens_generated = tokens_generated
        entry.validation_score = validation_score
        entry.regeneration_attempts = regeneration_attempts
        entry.final_status = final_status
        entry.error = error[:80] if error else None
        entry.duration_ms = (time.time() - entry.timestamp) * 1000

        self._buffer.append(entry)
        self._total_requests += 1
        self._total_tokens_prompt += tokens_prompt
        self._total_tokens_generated += tokens_generated
        if error or final_status == "failed":
            self._error_count += 1

        logger.debug(
            "TRACE: #%d %s via %s → %s (%.0fms)",
            entry.trace_id, entry.intent,
            entry.execution_path, final_status,
            entry.duration_ms,
        )

    def get_recent(self, limit: int = 5) -> List[TraceEntry]:
        """Последние N трейсов."""
        entries = list(self._buffer)
        return entries[-limit:]

    def format_recent(self, limit: int = 5) -> str:
        """Форматированные последние трейсы для /system trace."""
        entries = self.get_recent(limit)
        if not entries:
            return "═══ TRACE ═══\n  (пусто)"
        lines = ["═══ EXECUTION TRACE ═══"]
        for e in entries:
            lines.append(e.format())
        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """Статистика трассировки."""
        entries = list(self._buffer)
        avg_duration = 0.0
        if entries:
            avg_duration = sum(e.duration_ms for e in entries) / len(entries)

        return {
            "total_requests": self._total_requests,
            "buffer_size": len(self._buffer),
            "buffer_capacity": self._buffer.maxlen,
            "total_tokens_prompt": self._total_tokens_prompt,
            "total_tokens_generated": self._total_tokens_generated,
            "error_count": self._error_count,
            "avg_duration_ms": round(avg_duration, 1),
        }

    def get_failure_streak(self) -> int:
        """Количество последовательных провалов в конце буфера."""
        streak = 0
        for entry in reversed(list(self._buffer)):
            if entry.final_status in ("failed", "blocked"):
                streak += 1
            else:
                break
        return streak
