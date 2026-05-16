# -*- coding: utf-8 -*-
"""
Lina Agent — Рабочая память (Agent Memory).

Хранилище состояния и истории для агента:
  1. Рабочая переменная (working state)
  2. История действий (action history)
  3. Веса релевантности (relevance weights)
  4. Контекст для LLM (assembled prompt context)

Phase 10 — AI Runtime v2.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

logger = logging.getLogger("lina.agent.memory")


# ═══════════════════════════════════════════════════════════
#  Конфигурация
# ═══════════════════════════════════════════════════════════

MAX_HISTORY = 100      # Максимум записей истории
MAX_CONTEXT_CHARS = 3000  # Максимум символов контекста


# ═══════════════════════════════════════════════════════════
#  Запись в памяти
# ═══════════════════════════════════════════════════════════

@dataclass
class MemoryEntry:
    """Одна запись в памяти агента.

    Attributes:
        action: Описание действия.
        result: Результат.
        success: Успешно ли выполнено.
        relevance: Вес релевантности (0.0-1.0).
        timestamp: Время создания.
        metadata: Метаданные.
    """
    action: str
    result: str = ""
    success: bool = True
    relevance: float = 1.0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "action": self.action[:200],
            "result": self.result[:200],
            "success": self.success,
            "relevance": round(self.relevance, 2),
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════════════
#  AgentMemory
# ═══════════════════════════════════════════════════════════

class AgentMemory:
    """Рабочая память агента.

    Хранит:
      - working_state: текущие переменные (key-value)
      - history: журнал действий (deque)
      - goal: текущая цель
      - context: накопленный контекст для промпта

    Использование:
        memory = AgentMemory()
        memory.set_goal("Установить nginx")
        memory.set("os", "CachyOS")
        memory.add_action("Проверил систему", "CachyOS Linux")
        context = memory.get_context()

    Attributes:
        max_history: Максимум записей истории.
        max_context_chars: Максимум символов контекста.
        _working_state: Рабочие переменные.
        _history: История действий.
        _goal: Текущая цель.
    """

    def __init__(
        self,
        max_history: int = MAX_HISTORY,
        max_context_chars: int = MAX_CONTEXT_CHARS,
    ):
        """Инициализация.

        Args:
            max_history: Максимум записей.
            max_context_chars: Максимум символов контекста.
        """
        self.max_history = max_history
        self.max_context_chars = max_context_chars
        self._working_state: Dict[str, Any] = {}
        self._history: deque = deque(maxlen=max_history)
        self._goal: str = ""

    # ───────────────────────────────────────────────────────
    #  Цель
    # ───────────────────────────────────────────────────────

    def set_goal(self, goal: str) -> None:
        """Устанавливает текущую цель.

        Args:
            goal: Цель агента.
        """
        self._goal = goal

    @property
    def goal(self) -> str:
        """Текущая цель."""
        return self._goal

    # ───────────────────────────────────────────────────────
    #  Рабочее состояние (key-value)
    # ───────────────────────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        """Устанавливает рабочую переменную.

        Args:
            key: Ключ.
            value: Значение.
        """
        _MAX_STATE_VARS = 200
        if key not in self._working_state and len(self._working_state) >= _MAX_STATE_VARS:
            logger.warning("MEMORY: working state limit reached (%d)", _MAX_STATE_VARS)
            return
        self._working_state[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Получает рабочую переменную.

        Args:
            key: Ключ.
            default: Значение по умолчанию.

        Returns:
            Значение или default.
        """
        return self._working_state.get(key, default)

    def remove(self, key: str) -> None:
        """Удаляет рабочую переменную.

        Args:
            key: Ключ для удаления.
        """
        self._working_state.pop(key, None)

    @property
    def state(self) -> Dict[str, Any]:
        """Текущее рабочее состояние (копия)."""
        return dict(self._working_state)

    # ───────────────────────────────────────────────────────
    #  История действий
    # ───────────────────────────────────────────────────────

    def add_action(
        self,
        action: str,
        result: str = "",
        success: bool = True,
        relevance: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryEntry:
        """Добавляет действие в историю.

        Args:
            action: Описание действия.
            result: Результат.
            success: Успешно ли.
            relevance: Вес релевантности.
            metadata: Метаданные.

        Returns:
            Созданная MemoryEntry.
        """
        entry = MemoryEntry(
            action=action,
            result=result,
            success=success,
            relevance=relevance,
            metadata=metadata or {},
        )
        self._history.append(entry)
        return entry

    @property
    def history(self) -> List[MemoryEntry]:
        """Вся история (список)."""
        return list(self._history)

    @property
    def history_size(self) -> int:
        """Размер истории."""
        return len(self._history)

    def get_recent(self, n: int = 5) -> List[MemoryEntry]:
        """Последние N записей.

        Args:
            n: Количество записей.

        Returns:
            Список последних записей.
        """
        entries = list(self._history)
        return entries[-n:] if len(entries) >= n else entries

    def get_relevant(
        self,
        min_relevance: float = 0.5,
        n: int = 10,
    ) -> List[MemoryEntry]:
        """Записи с высокой релевантностью.

        Args:
            min_relevance: Минимальный вес.
            n: Максимум записей.

        Returns:
            Отсортированные по релевантности записи.
        """
        filtered = [
            e for e in self._history
            if e.relevance >= min_relevance
        ]
        filtered.sort(key=lambda e: e.relevance, reverse=True)
        return filtered[:n]

    # ───────────────────────────────────────────────────────
    #  Контекст для LLM
    # ───────────────────────────────────────────────────────

    def get_context(self, max_chars: Optional[int] = None) -> str:
        """Собирает контекст для промпта LLM.

        Формат:
          Цель: ...
          Состояние: ...
          Последние действия:
            1. [OK] action → result
            2. [FAIL] action → error

        Args:
            max_chars: Максимум символов (None = default).

        Returns:
            Строка контекста.
        """
        limit = max_chars or self.max_context_chars
        parts = []

        # Цель
        if self._goal:
            parts.append(f"Цель: {self._goal}")

        # Рабочее состояние
        if self._working_state:
            state_str = ", ".join(
                f"{k}={v}" for k, v in
                list(self._working_state.items())[:10]
            )
            parts.append(f"Состояние: {state_str}")

        # Последние действия
        recent = self.get_recent(5)
        if recent:
            parts.append("Последние действия:")
            for i, entry in enumerate(recent, 1):
                status = "OK" if entry.success else "FAIL"
                result = entry.result[:100] if entry.result else "—"
                parts.append(
                    f"  {i}. [{status}] {entry.action[:100]} → {result}"
                )

        context = "\n".join(parts)
        if len(context) > limit:
            context = context[:limit - 3] + "..."
        return context

    # ───────────────────────────────────────────────────────
    #  Очистка и сброс
    # ───────────────────────────────────────────────────────

    def clear(self) -> None:
        """Полная очистка памяти."""
        self._working_state.clear()
        self._history.clear()
        self._goal = ""

    def clear_history(self) -> None:
        """Очищает только историю."""
        self._history.clear()

    # ───────────────────────────────────────────────────────
    #  Сериализация
    # ───────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "goal": self._goal,
            "working_state": dict(self._working_state),
            "history_size": len(self._history),
            "history": [e.to_dict() for e in self.get_recent(10)],
        }

    def format_status(self) -> str:
        """Форматированный статус для CLI."""
        lines = [
            f"Agent Memory:",
            f"  Goal: {self._goal[:80] or '—'}",
            f"  State vars: {len(self._working_state)}",
            f"  History: {len(self._history)}/{self.max_history}",
        ]
        recent = self.get_recent(3)
        for entry in recent:
            status = "✓" if entry.success else "✗"
            lines.append(
                f"  {status} {entry.action[:50]}"
            )
        return "\n".join(lines)
