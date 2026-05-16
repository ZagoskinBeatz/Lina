# -*- coding: utf-8 -*-
"""
Lina Core — Request Envelope (Phase 26).

Единая обёртка запроса и ответа, проходящая через весь пайплайн.

RequestEnvelope — входной пакет (immutable после создания).
ResponseEnvelope — выходной пакет (собирается по мере прохождения).

Ни один модуль не работает с raw strings — только через envelope.
"""

import time
import hashlib
import itertools
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger("lina.core.envelope")

_request_counter = itertools.count(1)


# ═══════════════════════════════════════════════════════════
#  Request Envelope
# ═══════════════════════════════════════════════════════════

@dataclass
class RequestEnvelope:
    """Входной пакет запроса.

    Создаётся один раз в начале пайплайна.
    Передаётся как read-only через все этапы.
    """
    request_id: int = 0
    user_input: str = ""
    timestamp: float = field(default_factory=time.time)
    session_id: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.request_id:
            self.request_id = next(_request_counter)

    def input_hash(self) -> str:
        """Hash входа для кеширования и дедупликации."""
        return hashlib.sha256(
            self.user_input.encode("utf-8")
        ).hexdigest()[:12]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "input": self.user_input[:100],
            "input_hash": self.input_hash(),
            "timestamp": round(self.timestamp, 3),
            "session_id": self.session_id,
        }


# ═══════════════════════════════════════════════════════════
#  Pipeline Stage
# ═══════════════════════════════════════════════════════════

@dataclass
class StageRecord:
    """Запись об этапе пайплайна."""
    name: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    status: str = ""              # ok | skipped | error
    detail: str = ""

    @property
    def duration_ms(self) -> float:
        if self.ended_at and self.started_at:
            return (self.ended_at - self.started_at) * 1000
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.name,
            "status": self.status,
            "ms": round(self.duration_ms, 1),
        }


# ═══════════════════════════════════════════════════════════
#  Response Envelope
# ═══════════════════════════════════════════════════════════

@dataclass
class ResponseEnvelope:
    """Выходной пакет ответа.

    Собирается по мере прохождения через пайплайн.
    Каждый этап дописывает свою часть.
    """
    request_id: int = 0
    response_text: str = ""
    intent: str = ""
    confidence: float = 0.0
    execution_path: str = ""      # LLM | TOOL | SYSTEM
    plan_hash: str = ""
    priority_level: int = 5
    validation_score: float = 1.0
    consistency_score: float = 1.0
    drift_detected: bool = False
    regeneration_attempts: int = 0
    final_status: str = ""        # success | failed | blocked | regenerated
    blocked: bool = False
    blocked_reason: str = ""
    tokens_prompt: int = 0
    tokens_generated: int = 0
    stages: List[StageRecord] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    ended_at: float = 0.0

    @property
    def total_duration_ms(self) -> float:
        if self.ended_at and self.started_at:
            return (self.ended_at - self.started_at) * 1000
        return 0.0

    def add_stage(
        self, name: str, status: str = "ok", detail: str = "",
        started: float = 0.0, ended: float = 0.0,
    ) -> None:
        """Записать этап."""
        _MAX_STAGES = 100
        if len(self.stages) >= _MAX_STAGES:
            return
        self.stages.append(StageRecord(
            name=name, started_at=started, ended_at=ended,
            status=status, detail=detail,
        ))

    def finalize(self, status: str = "success") -> None:
        """Завершить обработку."""
        self.ended_at = time.time()
        self.final_status = status

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "intent": self.intent,
            "confidence": round(self.confidence, 2),
            "path": self.execution_path,
            "plan_hash": self.plan_hash,
            "priority": self.priority_level,
            "validation": round(self.validation_score, 2),
            "consistency": round(self.consistency_score, 2),
            "drift": self.drift_detected,
            "regen": self.regeneration_attempts,
            "status": self.final_status,
            "blocked": self.blocked,
            "tokens_prompt": self.tokens_prompt,
            "tokens_gen": self.tokens_generated,
            "total_ms": round(self.total_duration_ms, 1),
            "stages": [s.to_dict() for s in self.stages],
            "errors": self.errors,
        }

    def summary(self) -> str:
        """Краткая сводка для лога."""
        return (
            f"[{self.final_status}] intent={self.intent} "
            f"path={self.execution_path} "
            f"val={self.validation_score:.2f} "
            f"cons={self.consistency_score:.2f} "
            f"drift={self.drift_detected} "
            f"{self.total_duration_ms:.0f}ms"
        )
