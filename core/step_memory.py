# -*- coding: utf-8 -*-
"""
Lina Core — Step Memory (Phase 25).

Память шагов multi-step выполнения.

Каждый шаг сохраняет:
  - summary_reasoning  (краткое описание результата)
  - semantic_fingerprint (hash ключевых сущностей)
  - intent, path, status

Следующий шаг обязан проверять согласованность через snapshot.

StepMemory — ТОЛЬКО хранит. НЕ решает и НЕ выполняет.
"""

import hashlib
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger("lina.core.step_memory")


# ═══════════════════════════════════════════════════════════
#  Step Snapshot
# ═══════════════════════════════════════════════════════════

@dataclass
class StepSnapshot:
    """Снимок одного шага выполнения."""
    step_number: int = 0
    intent: str = ""
    path: str = ""                     # LLM | TOOL | SYSTEM
    status: str = ""                   # success | failed | regenerated
    summary_reasoning: str = ""
    semantic_fingerprint: str = ""
    entities: List[str] = field(default_factory=list)
    strategy: str = ""
    consistency_score: float = 1.0
    user_input: str = ""               # исходный запрос пользователя

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step_number,
            "intent": self.intent,
            "path": self.path,
            "status": self.status,
            "summary": self.summary_reasoning[:100],
            "fingerprint": self.semantic_fingerprint,
            "entities": self.entities[:5],
            "strategy": self.strategy,
            "consistency": round(self.consistency_score, 2),
        }


# ═══════════════════════════════════════════════════════════
#  Step Memory
# ═══════════════════════════════════════════════════════════

class StepMemory:
    """Память шагов multi-step выполнения (Phase 25).

    Хранит ring-buffer снимков шагов.
    Каждый шаг сохраняет summary + semantic fingerprint.

    Usage:
        mem = StepMemory()
        mem.record_step(1, intent="chat", path="LLM",
                        summary="ответ на вопрос",
                        entities=["Python", "функции"],
                        strategy="direct_answer")
        prev = mem.get_previous()
        # prev.summary_reasoning == "ответ на вопрос"
    """

    def __init__(self, max_steps: int = 20):
        self._lock = threading.Lock()
        self._steps: deque = deque(maxlen=max_steps)
        self._session_id: int = 0
        self._total_steps: int = 0

    def record_step(
        self,
        step_number: int,
        *,
        intent: str = "",
        path: str = "",
        status: str = "success",
        summary: str = "",
        entities: Optional[List[str]] = None,
        strategy: str = "",
        consistency_score: float = 1.0,
        user_input: str = "",
    ) -> StepSnapshot:
        """Записывает снимок шага.

        Args:
            step_number: Номер шага.
            intent: Намерение.
            path: Execution path (LLM|TOOL|SYSTEM).
            status: Статус (success|failed|regenerated).
            summary: Краткое описание результата.
            entities: Ключевые сущности ответа.
            strategy: Выбранная стратегия.
            consistency_score: Оценка согласованности (0-1).

        Returns:
            StepSnapshot.
        """
        ents = entities or []
        fingerprint = self._compute_fingerprint(intent, ents, strategy)

        snap = StepSnapshot(
            step_number=step_number,
            intent=intent,
            path=path,
            status=status,
            summary_reasoning=summary[:500],
            semantic_fingerprint=fingerprint,
            entities=ents[:10],
            strategy=strategy,
            consistency_score=consistency_score,
            user_input=user_input[:200],
        )

        with self._lock:
            self._steps.append(snap)
            self._total_steps += 1

        logger.debug(
            "STEP_MEMORY: step %d recorded — intent=%s fp=%s",
            step_number, intent, fingerprint,
        )
        return snap

    def get_previous(self) -> Optional[StepSnapshot]:
        """Возвращает предыдущий шаг (или None)."""
        if len(self._steps) < 1:
            return None
        return self._steps[-1]

    def get_step(self, step_number: int) -> Optional[StepSnapshot]:
        """Возвращает шаг по номеру."""
        for s in self._steps:
            if s.step_number == step_number:
                return s
        return None

    def get_all(self) -> List[StepSnapshot]:
        """Все шаги в памяти."""
        return list(self._steps)

    def get_entities_history(self) -> List[str]:
        """Уникальные сущности из всех шагов."""
        seen: set = set()
        result: List[str] = []
        for s in self._steps:
            for e in s.entities:
                if e not in seen:
                    seen.add(e)
                    result.append(e)
        return result

    def clear(self) -> None:
        """Очищает память (новая сессия)."""
        self._steps.clear()
        self._total_steps = 0
        self._session_id += 1

    def new_session(self) -> None:
        """Начинает новую сессию."""
        self.clear()

    @staticmethod
    def _compute_fingerprint(
        intent: str, entities: List[str], strategy: str,
    ) -> str:
        """Вычисляет семантический fingerprint шага."""
        raw = f"{intent}|{'|'.join(sorted(entities))}|{strategy}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для отладки."""
        return {
            "steps_in_memory": len(self._steps),
            "total_steps_recorded": self._total_steps,
            "sessions": self._session_id + 1,
            "entities_tracked": len(self.get_entities_history()),
        }
