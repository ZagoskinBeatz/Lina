"""
StateMachine — конечные автоматы для Installer Mode и Runtime Mode.

InstallerState:  INIT → SCAN_HARDWARE → PARTITION → FORMAT → MOUNT
                 → PACSTRAP → CONFIGURE → BOOTLOADER → NETWORK_SETUP
                 → FINALIZE → REBOOT → COMPLETE

RuntimeState:   BOOTING → INTEGRITY_CHECK → IDLE ⇄ PROCESSING
                IDLE → DIAGNOSING → FIXING → IDLE
                Any → DEGRADED → SAFE_MODE

Каждый переход:
  - Валидируется (допустимость)
  - Логируется (аудит)
  - Может иметь guard-функцию
  - Может иметь on_enter/on_exit callback

Phase: GOVERNANCE LAYER / Module 3
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def _key(state: Any) -> str:
    """Извлечь строковый ключ: .value для Enum, иначе str()."""
    return state.value if isinstance(state, Enum) else str(state)


# ─── States ────────────────────────────────────────────────────────────────────

class InstallerState(str, Enum):
    """Состояния Installer Mode."""
    INIT = "init"
    SCAN_HARDWARE = "scan_hardware"
    PARTITION = "partition"
    FORMAT = "format"
    MOUNT = "mount"
    PACSTRAP = "pacstrap"
    CONFIGURE = "configure"
    BOOTLOADER = "bootloader"
    NETWORK_SETUP = "network_setup"
    FINALIZE = "finalize"
    REBOOT = "reboot"
    COMPLETE = "complete"
    ERROR = "error"
    ROLLBACK = "rollback"


class RuntimeState(str, Enum):
    """Состояния Runtime Mode."""
    BOOTING = "booting"
    INTEGRITY_CHECK = "integrity_check"
    IDLE = "idle"
    PROCESSING = "processing"
    DIAGNOSING = "diagnosing"
    FIXING = "fixing"
    MONITORING = "monitoring"
    DEGRADED = "degraded"
    SAFE_MODE = "safe_mode"
    UPDATING = "updating"
    SHUTDOWN = "shutdown"


# ─── Transition ────────────────────────────────────────────────────────────────

@dataclass
class Transition:
    """Определение допустимого перехода."""
    from_state: str
    to_state: str
    guard: Optional[Callable[[], bool]] = None
    on_transition: Optional[Callable[[str, str], None]] = None
    description: str = ""


@dataclass
class StateEvent:
    """Событие перехода (для аудита)."""
    from_state: str
    to_state: str
    timestamp: float = 0.0
    success: bool = True
    reason: str = ""
    duration: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_state, "to": self.to_state,
            "timestamp": self.timestamp, "success": self.success,
            "reason": self.reason, "duration": round(self.duration, 3),
        }


# ─── StateMachine ─────────────────────────────────────────────────────────────

class StateMachine:
    """
    Универсальный конечный автомат с guard-функциями и аудитом.

    Пример:
        sm = StateMachine("runtime", RuntimeState.BOOTING)
        sm.add_transition(RuntimeState.BOOTING, RuntimeState.INTEGRITY_CHECK)
        sm.transition(RuntimeState.INTEGRITY_CHECK)
    """

    def __init__(self, name: str, initial_state: str) -> None:
        self._name = name
        self._state = initial_state
        self._transitions: Dict[str, Set[str]] = {}
        self._guards: Dict[Tuple[str, str], Callable[[], bool]] = {}
        self._on_enter: Dict[str, List[Callable[[str], None]]] = {}
        self._on_exit: Dict[str, List[Callable[[str], None]]] = {}
        self._on_transition: Dict[Tuple[str, str], Callable[[str, str], None]] = {}
        self._history: deque = deque(maxlen=1000)
        self._state_enter_time = time.monotonic()

    # ── Configuration ────────────────────────────────────

    def add_transition(self, from_state: str, to_state: str,
                       guard: Optional[Callable[[], bool]] = None) -> None:
        """Добавить допустимый переход."""
        key = _key(from_state)
        if key not in self._transitions:
            self._transitions[key] = set()
        self._transitions[key].add(_key(to_state))
        if guard:
            self._guards[(_key(from_state), _key(to_state))] = guard

    def add_transitions(self, transitions: List[Tuple[str, str]]) -> None:
        """Добавить список переходов."""
        for from_s, to_s in transitions:
            self.add_transition(from_s, to_s)

    def on_enter(self, state: str,
                 callback: Callable[[str], None]) -> None:
        """Зарегистрировать callback при входе в состояние."""
        key = _key(state)
        if key not in self._on_enter:
            self._on_enter[key] = []
        self._on_enter[key].append(callback)

    def on_exit(self, state: str,
                callback: Callable[[str], None]) -> None:
        """Зарегистрировать callback при выходе из состояния."""
        key = _key(state)
        if key not in self._on_exit:
            self._on_exit[key] = []
        self._on_exit[key].append(callback)

    def set_transition_callback(self, from_state: str, to_state: str,
                                callback: Callable[[str, str], None]) -> None:
        """Callback на конкретный переход."""
        self._on_transition[(_key(from_state), _key(to_state))] = callback

    # ── Transition ───────────────────────────────────────

    def transition(self, to_state: str, reason: str = "") -> StateEvent:
        """
        Выполнить переход.
        Returns: StateEvent
        """
        from_state = self._state
        to_str = _key(to_state)

        # Validate
        allowed = self._transitions.get(_key(from_state), set())
        if to_str not in allowed:
            event = StateEvent(
                from_state=from_state, to_state=to_str,
                success=False,
                reason=f"Transition {from_state} → {to_str} not allowed",
            )
            self._record(event)
            logger.warning("StateMachine[%s]: illegal %s → %s",
                           self._name, from_state, to_str)
            return event

        # Guard
        guard = self._guards.get((_key(from_state), to_str))
        if guard and not guard():
            event = StateEvent(
                from_state=from_state, to_state=to_str,
                success=False, reason="Guard rejected transition",
            )
            self._record(event)
            logger.warning("StateMachine[%s]: guard rejected %s → %s",
                           self._name, from_state, to_str)
            return event

        # Duration in previous state
        duration = time.monotonic() - self._state_enter_time

        # on_exit callbacks
        for cb in self._on_exit.get(_key(from_state), []):
            try:
                cb(to_str)
            except Exception as e:
                logger.error("on_exit callback error: %s", e)

        # Transition callback
        tcb = self._on_transition.get((_key(from_state), to_str))
        if tcb:
            try:
                tcb(from_state, to_str)
            except Exception as e:
                logger.error("on_transition callback error: %s", e)

        # Switch state
        self._state = to_str
        self._state_enter_time = time.monotonic()

        # on_enter callbacks
        for cb in self._on_enter.get(to_str, []):
            try:
                cb(from_state)
            except Exception as e:
                logger.error("on_enter callback error: %s", e)

        event = StateEvent(
            from_state=from_state, to_state=to_str,
            success=True, reason=reason or "OK", duration=duration,
        )
        self._record(event)
        logger.info("StateMachine[%s]: %s → %s", self._name, from_state, to_str)
        return event

    def force_state(self, state: str, reason: str = "forced") -> None:
        """Принудительно установить состояние (для recovery)."""
        old = self._state
        self._state = _key(state)
        self._state_enter_time = time.monotonic()
        event = StateEvent(
            from_state=old, to_state=_key(state),
            success=True, reason=f"FORCED: {reason}",
        )
        self._record(event)
        logger.warning("StateMachine[%s]: FORCED %s → %s (%s)",
                       self._name, old, state, reason)

    # ── Query ────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def name(self) -> str:
        return self._name

    def can_transition(self, to_state: str) -> bool:
        """Можно ли выполнить переход."""
        allowed = self._transitions.get(_key(self._state), set())
        return _key(to_state) in allowed

    def allowed_transitions(self) -> List[str]:
        """Список допустимых переходов из текущего состояния."""
        return sorted(self._transitions.get(_key(self._state), set()))

    def time_in_state(self) -> float:
        """Время в текущем состоянии (секунды)."""
        return time.monotonic() - self._state_enter_time

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Получить историю переходов."""
        return [e.to_dict() for e in list(self._history)[-limit:]]

    def get_stats(self) -> Dict[str, Any]:
        """Статистика."""
        state_times: Dict[str, float] = {}
        transition_counts: Dict[str, int] = {}
        for e in self._history:
            key = f"{e.from_state}→{e.to_state}"
            transition_counts[key] = transition_counts.get(key, 0) + 1
            state_times[e.from_state] = state_times.get(e.from_state, 0) + e.duration
        return {
            "name": self._name,
            "current_state": self._state,
            "total_transitions": len(self._history),
            "state_times": state_times,
            "transition_counts": transition_counts,
        }

    # ── Internal ─────────────────────────────────────────

    def _record(self, event: StateEvent) -> None:
        """Записать событие."""
        self._history.append(event)


# ─── Factory: pre-configured state machines ──────────────────────────────────

def create_installer_machine() -> StateMachine:
    """Создать StateMachine для Installer Mode с предустановленными переходами."""
    sm = StateMachine("installer", _key(InstallerState.INIT))
    transitions = [
        (InstallerState.INIT, InstallerState.SCAN_HARDWARE),
        (InstallerState.SCAN_HARDWARE, InstallerState.PARTITION),
        (InstallerState.PARTITION, InstallerState.FORMAT),
        (InstallerState.FORMAT, InstallerState.MOUNT),
        (InstallerState.MOUNT, InstallerState.PACSTRAP),
        (InstallerState.PACSTRAP, InstallerState.CONFIGURE),
        (InstallerState.CONFIGURE, InstallerState.BOOTLOADER),
        (InstallerState.BOOTLOADER, InstallerState.NETWORK_SETUP),
        (InstallerState.NETWORK_SETUP, InstallerState.FINALIZE),
        (InstallerState.FINALIZE, InstallerState.REBOOT),
        (InstallerState.REBOOT, InstallerState.COMPLETE),
        # Error transitions (any → error)
        (InstallerState.SCAN_HARDWARE, InstallerState.ERROR),
        (InstallerState.PARTITION, InstallerState.ERROR),
        (InstallerState.FORMAT, InstallerState.ERROR),
        (InstallerState.MOUNT, InstallerState.ERROR),
        (InstallerState.PACSTRAP, InstallerState.ERROR),
        (InstallerState.CONFIGURE, InstallerState.ERROR),
        (InstallerState.BOOTLOADER, InstallerState.ERROR),
        (InstallerState.NETWORK_SETUP, InstallerState.ERROR),
        (InstallerState.FINALIZE, InstallerState.ERROR),
        # Error → rollback
        (InstallerState.ERROR, InstallerState.ROLLBACK),
        # Rollback → previous or init
        (InstallerState.ROLLBACK, InstallerState.INIT),
        (InstallerState.ROLLBACK, InstallerState.PARTITION),
        (InstallerState.ROLLBACK, InstallerState.FORMAT),
        (InstallerState.ROLLBACK, InstallerState.MOUNT),
    ]
    sm.add_transitions(transitions)
    return sm


def create_runtime_machine() -> StateMachine:
    """Создать StateMachine для Runtime Mode с предустановленными переходами."""
    sm = StateMachine("runtime", _key(RuntimeState.BOOTING))
    transitions = [
        # Boot sequence
        (RuntimeState.BOOTING, RuntimeState.INTEGRITY_CHECK),
        (RuntimeState.INTEGRITY_CHECK, RuntimeState.IDLE),
        (RuntimeState.INTEGRITY_CHECK, RuntimeState.SAFE_MODE),
        # Normal flow
        (RuntimeState.IDLE, RuntimeState.PROCESSING),
        (RuntimeState.PROCESSING, RuntimeState.IDLE),
        (RuntimeState.IDLE, RuntimeState.DIAGNOSING),
        (RuntimeState.DIAGNOSING, RuntimeState.FIXING),
        (RuntimeState.FIXING, RuntimeState.IDLE),
        (RuntimeState.DIAGNOSING, RuntimeState.IDLE),
        (RuntimeState.IDLE, RuntimeState.MONITORING),
        (RuntimeState.MONITORING, RuntimeState.IDLE),
        (RuntimeState.MONITORING, RuntimeState.DIAGNOSING),
        # Update
        (RuntimeState.IDLE, RuntimeState.UPDATING),
        (RuntimeState.UPDATING, RuntimeState.IDLE),
        # Degradation chain
        (RuntimeState.IDLE, RuntimeState.DEGRADED),
        (RuntimeState.PROCESSING, RuntimeState.DEGRADED),
        (RuntimeState.DIAGNOSING, RuntimeState.DEGRADED),
        (RuntimeState.FIXING, RuntimeState.DEGRADED),
        (RuntimeState.MONITORING, RuntimeState.DEGRADED),
        (RuntimeState.DEGRADED, RuntimeState.SAFE_MODE),
        (RuntimeState.DEGRADED, RuntimeState.IDLE),
        (RuntimeState.SAFE_MODE, RuntimeState.IDLE),
        # Shutdown
        (RuntimeState.IDLE, RuntimeState.SHUTDOWN),
        (RuntimeState.DEGRADED, RuntimeState.SHUTDOWN),
        (RuntimeState.SAFE_MODE, RuntimeState.SHUTDOWN),
    ]
    sm.add_transitions(transitions)
    return sm


# ─── Singletons ───────────────────────────────────────────────────────────────

_installer_sm: Optional[StateMachine] = None
_runtime_sm: Optional[StateMachine] = None

def get_installer_machine() -> StateMachine:
    """Получить единственный InstallerStateMachine."""
    global _installer_sm
    if _installer_sm is None:
        _installer_sm = create_installer_machine()
    return _installer_sm

def get_runtime_machine() -> StateMachine:
    """Получить единственный RuntimeStateMachine."""
    global _runtime_sm
    if _runtime_sm is None:
        _runtime_sm = create_runtime_machine()
    return _runtime_sm
