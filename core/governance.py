# -*- coding: utf-8 -*-
"""
Lina Core — Runtime State Manager.

CANONICAL: lina.governance.state_machine
Этот файл — compat shim. Формальная FSM логика — в governance.
Существующий API сохранён для обратной совместимости.

Единственный источник глобального состояния (key-value).
Ни один модуль НЕ хранит своё глобальное состояние —
всё читается/пишется только через RuntimeStateManager.

Хранит:
  active_model, current_profile, safe_mode, tool_mode,
  rag_enabled, cv_enabled, last_intent, last_execution_path,
  consecutive_failures, regeneration_count
"""

import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

logger = logging.getLogger("lina.core.governance")


# ═══════════════════════════════════════════════════════════
#  State Snapshot
# ═══════════════════════════════════════════════════════════

@dataclass
class StateSnapshot:
    """Снимок состояния — immutable, для аудита."""
    timestamp: float = 0.0
    active_model: str = ""
    current_profile: str = ""
    safe_mode: bool = False
    tool_mode: str = "normal"
    rag_enabled: bool = True
    cv_enabled: bool = True
    last_intent: str = ""
    last_execution_path: str = ""
    consecutive_failures: int = 0
    regeneration_count: int = 0
    mode: str = "normal"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": round(self.timestamp, 3),
            "active_model": self.active_model,
            "current_profile": self.current_profile,
            "safe_mode": self.safe_mode,
            "tool_mode": self.tool_mode,
            "rag_enabled": self.rag_enabled,
            "cv_enabled": self.cv_enabled,
            "last_intent": self.last_intent,
            "last_execution_path": self.last_execution_path,
            "consecutive_failures": self.consecutive_failures,
            "regeneration_count": self.regeneration_count,
            "mode": self.mode,
        }


# ═══════════════════════════════════════════════════════════
#  Runtime State Manager
# ═══════════════════════════════════════════════════════════

class RuntimeStateManager:
    """Единый менеджер глобального состояния.

    CANONICAL: lina.governance.state_machine (FSM transitions)
    Этот класс — key-value store для app state.
    При переключении safe_mode/mode — синхронизирует с governance StateMachine.

    Thread-safe по дизайну: все мутации через set().
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {
            "active_model": "full",
            "current_profile": "default",
            "safe_mode": False,
            "tool_mode": "normal",       # normal | restricted | disabled
            "rag_enabled": True,
            "cv_enabled": True,
            "last_intent": "",
            "last_execution_path": "",    # LLM | TOOL | SYSTEM | META
            "consecutive_failures": 0,
            "regeneration_count": 0,
            "mode": "normal",            # normal | strict | safe | diagnostic | minimal
        }
        self._created_at = time.time()
        self._mutation_count = 0
        self._listeners: list = []
        self._governance_sm = None  # Lazy init

    def _get_governance_sm(self):
        """Lazy init governance StateMachine."""
        if self._governance_sm is None:
            try:
                from lina.governance.state_machine import get_runtime_machine
                self._governance_sm = get_runtime_machine()
            except Exception as e:
                logger.error("Cannot load governance SM: %s", e, exc_info=True)
        return self._governance_sm

    def get(self, key: str, default: Any = None) -> Any:
        """Читает значение."""
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> bool:
        """Устанавливает значение с логированием.

        Returns:
            True если ключ существует и значение установлено.
        """
        changed = False
        old = None
        with self._lock:
            if key not in self._state:
                logger.warning("STATE: unknown key '%s'", key)
                return False

            # Type enforcement: value must match existing type
            expected = type(self._state[key])
            if not isinstance(value, expected):
                logger.warning("STATE: type mismatch for '%s': expected %s, got %s",
                               key, expected.__name__, type(value).__name__)
                return False

            old = self._state[key]
            self._state[key] = value
            self._mutation_count += 1

            if old != value:
                changed = True
                logger.debug("STATE_CHANGE: %s: %s → %s", key, old, value)

        # Notify OUTSIDE lock to prevent deadlock when listeners call set()/increment()
        if changed:
            self._notify(key, old, value)
            self._sync_to_governance(key, value)

        return True

    def _sync_to_governance(self, key: str, value: Any) -> None:
        """Синхронизировать критические изменения с governance StateMachine."""
        sm = self._get_governance_sm()
        if sm is None:
            return
        try:
            if key == "safe_mode" and value is True:
                sm.transition("safe_mode")
            elif key == "mode" and value == "safe":
                sm.transition("safe_mode")
            elif key == "mode" and value == "diagnostic":
                sm.transition("diagnosing")
        except Exception as e:
            logger.warning("Governance sync failed: %s", e, exc_info=True)

    def increment(self, key: str, delta: int = 1) -> int:
        """Атомарный инкремент числового поля.

        Returns:
            Новое значение.
        """
        with self._lock:
            val = self._state.get(key, 0)
            if not isinstance(val, (int, float)):
                return 0
            old_val = val
            new_val = val + delta
            self._state[key] = new_val
            self._mutation_count += 1
        # Notify OUTSIDE lock (consistent with set())
        self._notify(key, old_val, new_val)
        return new_val

    def reset_counter(self, key: str) -> None:
        """Сброс счётчика в 0."""
        if key in self._state and isinstance(self._state[key], int):
            self.set(key, 0)

    def snapshot(self) -> StateSnapshot:
        """Создаёт immutable снимок текущего состояния."""
        return StateSnapshot(
            timestamp=time.time(),
            active_model=self._state["active_model"],
            current_profile=self._state["current_profile"],
            safe_mode=self._state["safe_mode"],
            tool_mode=self._state["tool_mode"],
            rag_enabled=self._state["rag_enabled"],
            cv_enabled=self._state["cv_enabled"],
            last_intent=self._state["last_intent"],
            last_execution_path=self._state["last_execution_path"],
            consecutive_failures=self._state["consecutive_failures"],
            regeneration_count=self._state["regeneration_count"],
            mode=self._state["mode"],
        )

    def register_listener(self, callback) -> None:
        """Регистрирует слушателя изменений состояния.

        callback(key, old_value, new_value)
        """
        _MAX_LISTENERS = 50
        if len(self._listeners) >= _MAX_LISTENERS:
            logger.warning("STATE: listener limit reached (%d)", _MAX_LISTENERS)
            return
        self._listeners.append(callback)

    def _notify(self, key: str, old: Any, new: Any) -> None:
        """Уведомляет слушателей об изменении."""
        for cb in self._listeners:
            try:
                cb(key, old, new)
            except Exception as e:
                logger.warning("STATE: listener error: %s", e, exc_info=True)

    def to_dict(self) -> Dict[str, Any]:
        """Полное состояние."""
        return {
            **self._state,
            "uptime": round(time.time() - self._created_at, 1),
            "mutations": self._mutation_count,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для SystemControl."""
        return {
            "mutations": self._mutation_count,
            "uptime_seconds": int(time.time() - self._created_at),
            "mode": self._state["mode"],
            "safe_mode": self._state["safe_mode"],
            "consecutive_failures": self._state["consecutive_failures"],
        }
