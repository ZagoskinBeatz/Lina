# -*- coding: utf-8 -*-
"""
Lina Core — Degradation Strategy (Phase 23).

Автоматическая стабилизация при деградации:

Правила:
  - 2 подряд validation failures → strict mode
  - 3 подряд tool failures → auto-disable tool
  - 3 подряд LLM failures → перезагрузка модели (сигнал)
  - 5 подряд общих ошибок → safe-mode

Система обязана самостабилизироваться.
DegradationStrategy ТОЛЬКО решает — НИКОГДА не исполняет.
Возвращает DegradationAction, вызывающий слой применяет.
"""

import time
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional

logger = logging.getLogger("lina.core.degradation")


# ═══════════════════════════════════════════════════════════
#  Action Types
# ═══════════════════════════════════════════════════════════

class ActionType(str, Enum):
    """Типы корректирующих действий."""
    NONE = "none"
    ENABLE_STRICT = "enable_strict"
    DISABLE_TOOL = "disable_tool"
    RELOAD_MODEL = "reload_model"
    ENABLE_SAFE_MODE = "enable_safe_mode"
    REDUCE_TOKENS = "reduce_tokens"
    RESET_COUNTERS = "reset_counters"


@dataclass
class DegradationAction:
    """Корректирующее действие (Phase 23).

    DegradationStrategy возвращает это; caller применяет.
    """
    action: ActionType = ActionType.NONE
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    severity: str = "info"         # info | warning | critical
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "severity": self.severity,
            "details": self.details,
        }


# ═══════════════════════════════════════════════════════════
#  Failure Tracker
# ═══════════════════════════════════════════════════════════

@dataclass
class FailureRecord:
    """Запись об ошибке."""
    category: str = ""             # validation | tool | llm | general
    timestamp: float = field(default_factory=time.time)
    detail: str = ""


# ═══════════════════════════════════════════════════════════
#  Degradation Strategy
# ═══════════════════════════════════════════════════════════

class DegradationStrategy:
    """Стратегия деградации (Phase 23).

    ТОЛЬКО решает — НИКОГДА не исполняет.
    Изолирована от engine-ов.

    Пороги (настраиваемые):
      validation_threshold: 2
      tool_threshold: 3
      llm_threshold: 3
      general_threshold: 5

    Usage:
        ds = DegradationStrategy()
        ds.record_failure("validation", "low score")
        action = ds.evaluate()
        if action.action != ActionType.NONE:
            apply(action)
    """

    def __init__(
        self, *,
        validation_threshold: int = 2,
        tool_threshold: int = 3,
        llm_threshold: int = 3,
        general_threshold: int = 5,
    ):
        self.validation_threshold = validation_threshold
        self.tool_threshold = tool_threshold
        self.llm_threshold = llm_threshold
        self.general_threshold = general_threshold

        self._lock = threading.Lock()
        self._failures: deque = deque(maxlen=200)
        self._actions_taken: deque = deque(maxlen=100)
        self._disabled_tools: set = set()

    def record_failure(self, category: str, detail: str = "") -> None:
        """Записывает ошибку.

        Args:
            category: validation | tool | llm | general
            detail: Описание.
        """
        with self._lock:
            self._failures.append(FailureRecord(
                category=category, detail=detail,
            ))
        logger.debug("DEGRADATION: failure recorded: %s — %s", category, detail)

    def record_success(self) -> None:
        """Записывает успех — сбрасывает счётчики подряд."""
        # Успех прерывает серию — добавляем запись-маркер
        with self._lock:
            self._failures.append(FailureRecord(category="_success"))

    def evaluate(self) -> DegradationAction:
        """Оценивает текущую ситуацию и возвращает действие.

        Приоритет (от высшего):
          1. 5 общих ошибок подряд → safe-mode
          2. 3 LLM ошибок подряд → reload_model
          3. 3 tool ошибок подряд → disable_tool
          4. 2 validation ошибок подряд → strict mode

        Returns:
            DegradationAction с рекомендацией.
        """
        with self._lock:
            streaks = self._compute_streaks()

        # 1. General: 5 подряд → safe mode
        if streaks.get("general", 0) >= self.general_threshold:
            action = DegradationAction(
                action=ActionType.ENABLE_SAFE_MODE,
                reason=f"{streaks['general']} consecutive general failures",
                severity="critical",
                details=streaks,
            )
            self._actions_taken.append(action)
            logger.warning("DEGRADATION: → SAFE MODE (%s)", action.reason)
            return action

        # 2. LLM: 3 подряд → reload
        if streaks.get("llm", 0) >= self.llm_threshold:
            action = DegradationAction(
                action=ActionType.RELOAD_MODEL,
                reason=f"{streaks['llm']} consecutive LLM failures",
                severity="critical",
                details=streaks,
            )
            self._actions_taken.append(action)
            logger.warning("DEGRADATION: → RELOAD MODEL (%s)", action.reason)
            return action

        # 3. Tool: 3 подряд → disable
        if streaks.get("tool", 0) >= self.tool_threshold:
            action = DegradationAction(
                action=ActionType.DISABLE_TOOL,
                reason=f"{streaks['tool']} consecutive tool failures",
                severity="warning",
                details=streaks,
            )
            self._actions_taken.append(action)
            self._disabled_tools.add("tool")
            logger.warning("DEGRADATION: → DISABLE TOOL (%s)", action.reason)
            return action

        # 4. Validation: 2 подряд → strict
        if streaks.get("validation", 0) >= self.validation_threshold:
            action = DegradationAction(
                action=ActionType.ENABLE_STRICT,
                reason=f"{streaks['validation']} consecutive validation failures",
                severity="warning",
                details=streaks,
            )
            self._actions_taken.append(action)
            logger.warning("DEGRADATION: → STRICT MODE (%s)", action.reason)
            return action

        return DegradationAction(action=ActionType.NONE)

    def _compute_streaks(self) -> Dict[str, int]:
        """Считает последовательные ошибки по категориям (с конца).

        Per-category: longest *consecutive* tail run (stops at first
        different-category record after the run starts).
        General: total failures since the last success marker.
        """
        streaks: Dict[str, int] = {}

        # Per-category consecutive streaks
        for cat in ("validation", "tool", "llm"):
            count = 0
            for rec in reversed(self._failures):
                if rec.category == "_success":
                    break
                if rec.category == cat:
                    count += 1
                elif count > 0:
                    # Streak was active and a different category appeared
                    break
            streaks[cat] = count

        # General: total failures since last success
        general = 0
        for rec in reversed(self._failures):
            if rec.category == "_success":
                break
            general += 1
        streaks["general"] = general

        return streaks

    def clear(self) -> None:
        """Сброс всех счётчиков (после стабилизации)."""
        with self._lock:
            self._failures.clear()
        logger.debug("DEGRADATION: counters cleared")

    def get_disabled_tools(self) -> set:
        """Список отключённых инструментов."""
        return set(self._disabled_tools)

    def get_actions_history(self) -> List[Dict[str, Any]]:
        """История корректирующих действий."""
        return [a.to_dict() for a in self._actions_taken]

    def get_stats(self) -> Dict[str, Any]:
        """Статистика деградации."""
        with self._lock:
            return {
                "total_failures": len([f for f in self._failures if f.category != "_success"]),
                "actions_taken": len(self._actions_taken),
                "current_streaks": self._compute_streaks(),
                "disabled_tools": list(self._disabled_tools),
            }
