# -*- coding: utf-8 -*-
"""
Lina Core — Intent Lock (Phase 25).

После утверждения execution_plan intent фиксируется.
LLM не может «переинтерпретировать» задачу.

IntentLock — ТОЛЬКО фиксирует и проверяет.
Не выполняет и не маршрутизирует.
"""

import time
import logging
from collections import deque
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger("lina.core.intent_lock")


# ═══════════════════════════════════════════════════════════
#  Lock State
# ═══════════════════════════════════════════════════════════

@dataclass
class LockState:
    """Состояние блокировки intent."""
    locked: bool = False
    intent: str = ""
    plan_hash: str = ""
    locked_at: float = 0.0
    lock_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "locked": self.locked,
            "intent": self.intent,
            "plan_hash": self.plan_hash,
            "reason": self.lock_reason,
        }


# ═══════════════════════════════════════════════════════════
#  Lock Violation
# ═══════════════════════════════════════════════════════════

@dataclass
class LockViolation:
    """Нарушение блокировки intent."""
    original_intent: str = ""
    attempted_intent: str = ""
    reason: str = ""
    severity: str = "warning"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original": self.original_intent,
            "attempted": self.attempted_intent,
            "reason": self.reason,
            "severity": self.severity,
        }


# ═══════════════════════════════════════════════════════════
#  Intent Lock
# ═══════════════════════════════════════════════════════════

class IntentLock:
    """Фиксация intent после утверждения плана (Phase 25).

    После lock() любая попытка сменить intent
    без явного unlock() будет отклонена.

    Usage:
        lock = IntentLock()
        lock.lock("chat", plan_hash="abc123")
        assert lock.is_locked()

        # Попытка переинтерпретации
        v = lock.validate("web")
        assert v is not None  # violation!

        lock.unlock()
    """

    def __init__(self):
        self._state = LockState()
        self._violations: deque = deque(maxlen=100)
        self._lock_count: int = 0
        self._violation_count: int = 0

    def lock(
        self,
        intent: str,
        plan_hash: str = "",
        reason: str = "plan approved",
    ) -> None:
        """Фиксирует intent.

        Args:
            intent: Фиксируемый intent.
            plan_hash: Hash плана (для верификации).
            reason: Причина фиксации.
        """
        self._state = LockState(
            locked=True,
            intent=intent,
            plan_hash=plan_hash,
            locked_at=time.time(),
            lock_reason=reason,
        )
        self._lock_count += 1
        logger.debug("INTENT_LOCK: locked '%s' (hash=%s)", intent, plan_hash)

    def unlock(self, reason: str = "execution complete") -> None:
        """Снимает блокировку."""
        old = self._state.intent
        self._state = LockState()
        logger.debug("INTENT_LOCK: unlocked '%s' — %s", old, reason)

    def is_locked(self) -> bool:
        """Проверяет наличие блокировки."""
        return self._state.locked

    def get_locked_intent(self) -> str:
        """Возвращает зафиксированный intent."""
        return self._state.intent if self._state.locked else ""

    def get_plan_hash(self) -> str:
        """Возвращает hash плана."""
        return self._state.plan_hash if self._state.locked else ""

    def validate(self, attempted_intent: str) -> Optional[LockViolation]:
        """Проверяет, совместим ли attempted_intent с lock.

        Args:
            attempted_intent: Intent, который пытается выполниться.

        Returns:
            LockViolation если нарушение, None если всё OK.
        """
        if not self._state.locked:
            return None

        if attempted_intent == self._state.intent:
            return None

        # Violation!
        violation = LockViolation(
            original_intent=self._state.intent,
            attempted_intent=attempted_intent,
            reason=f"Intent reinterpretation blocked: "
                   f"locked='{self._state.intent}', "
                   f"attempted='{attempted_intent}'",
            severity="warning",
        )
        self._violations.append(violation)
        self._violation_count += 1

        logger.warning(
            "INTENT_LOCK: VIOLATION — locked=%s attempted=%s",
            self._state.intent, attempted_intent,
        )
        return violation

    def get_state(self) -> LockState:
        """Текущее состояние lock."""
        return self._state

    def get_violations(self) -> list:
        """Все нарушения."""
        return [v.to_dict() for v in self._violations]

    def clear(self) -> None:
        """Полный сброс."""
        self._state = LockState()
        self._violations.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для отладки."""
        return {
            "locked": self._state.locked,
            "current_intent": self._state.intent or "(none)",
            "total_locks": self._lock_count,
            "violations": self._violation_count,
        }
