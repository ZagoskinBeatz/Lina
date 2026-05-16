"""
EscalationManager — управление эскалацией к пользователю.

Логика:
  1. Если автоматическое решение не найдено → эскалация
  2. Если risk > policy threshold → запрос подтверждения
  3. Если стратегия destructive → подтверждение + объяснение
  4. Три режима: auto / confirm / manual

Каждая эскалация содержит:
  - Описание проблемы (ru/en)
  - Предложенное действие
  - Risk level
  - Альтернативы
  - Timeout (auto-cancel)

Phase: GOVERNANCE LAYER / Module 7
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Enum ────────────────────────────────────────────────────────────────────

class EscalationLevel(str, Enum):
    """Уровень эскалации."""
    INFO = "info"           # Информирование (не требует ответа)
    CONFIRM = "confirm"     # Подтверждение (Да/Нет)
    CHOOSE = "choose"       # Выбор из вариантов
    MANUAL = "manual"       # Требуется ручное действие
    CRITICAL = "critical"   # Критическая проблема, нужно внимание


class EscalationStatus(str, Enum):
    """Статус эскалации."""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ALTERNATIVE = "alternative"


# ─── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class EscalationRequest:
    """Запрос эскалации к пользователю."""
    id: str
    level: str = EscalationLevel.CONFIRM
    title: str = ""
    title_ru: str = ""
    description: str = ""
    description_ru: str = ""
    domain: str = ""
    risk_level: str = "medium"
    proposed_action: str = ""
    proposed_command: str = ""
    alternatives: List[str] = field(default_factory=list)
    alternative_descriptions: List[str] = field(default_factory=list)
    timeout: int = 60           # seconds
    timestamp: float = 0.0
    status: str = EscalationStatus.PENDING
    response: str = ""
    response_time: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "title_ru": self.title_ru or self.title,
            "description_ru": self.description_ru or self.description,
            "domain": self.domain,
            "risk_level": self.risk_level,
            "proposed_action": self.proposed_action,
            "alternatives": self.alternatives,
            "status": self.status,
            "timeout": self.timeout,
        }

    def is_expired(self) -> bool:
        """Истёк ли таймаут."""
        return (time.time() - self.timestamp) > self.timeout

    def summary_ru(self) -> str:
        """Краткое описание для пользователя."""
        title = self.title_ru or self.title
        desc = self.description_ru or self.description
        if self.level == EscalationLevel.CONFIRM:
            return f"⚠ {title}\n{desc}\n→ Действие: {self.proposed_action}\nРиск: {self.risk_level}\nПодтвердить? [Да/Нет]"
        elif self.level == EscalationLevel.CHOOSE:
            opts = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(self.alternatives))
            return f"⚠ {title}\n{desc}\nВарианты:\n{opts}"
        elif self.level == EscalationLevel.MANUAL:
            return f"⛔ {title}\n{desc}\nТребуется ручное действие."
        elif self.level == EscalationLevel.CRITICAL:
            return f"🚨 {title}\n{desc}\nКритическая проблема!"
        else:
            return f"ℹ {title}\n{desc}"


# ─── EscalationManager ──────────────────────────────────────────────────────

class EscalationManager:
    """
    Управление эскалациями к пользователю.

    Пример:
        mgr = get_escalation_manager()
        esc = mgr.create_escalation(
            level="confirm",
            title_ru="Перезапуск NetworkManager",
            description_ru="Обнаружена проблема DNS...",
            proposed_action="svc_restart",
            risk_level="medium",
        )
        # UI/CLI показывает esc.summary_ru()
        mgr.resolve(esc.id, confirmed=True)
    """

    def __init__(self) -> None:
        self._pending: Dict[str, EscalationRequest] = {}
        self._max_history = 1000
        self._history: deque = deque(maxlen=self._max_history)
        self._counter = 0
        self._callbacks: List[Callable[[EscalationRequest], None]] = []

    # ── Create ───────────────────────────────────────────

    def create_escalation(self, *,
                          level: str = EscalationLevel.CONFIRM,
                          title: str = "",
                          title_ru: str = "",
                          description: str = "",
                          description_ru: str = "",
                          domain: str = "",
                          risk_level: str = "medium",
                          proposed_action: str = "",
                          proposed_command: str = "",
                          alternatives: Optional[List[str]] = None,
                          alternative_descriptions: Optional[List[str]] = None,
                          timeout: int = 60) -> EscalationRequest:
        """Создать эскалацию."""
        self._counter += 1
        esc_id = f"esc_{self._counter}_{int(time.time())}"

        esc = EscalationRequest(
            id=esc_id, level=level,
            title=title, title_ru=title_ru,
            description=description, description_ru=description_ru,
            domain=domain, risk_level=risk_level,
            proposed_action=proposed_action,
            proposed_command=proposed_command,
            alternatives=alternatives or [],
            alternative_descriptions=alternative_descriptions or [],
            timeout=timeout,
        )

        self._pending[esc_id] = esc
        logger.info("EscalationManager: created %s level=%s domain=%s",
                     esc_id, level, domain)

        # Notify callbacks
        for cb in self._callbacks:
            try:
                cb(esc)
            except Exception as e:
                logger.error("Escalation callback error: %s", e)

        return esc

    # ── Resolve ──────────────────────────────────────────

    def resolve(self, esc_id: str, *,
                confirmed: bool = False,
                alternative_index: int = -1,
                response: str = "") -> Optional[EscalationRequest]:
        """
        Разрешить эскалацию.

        Args:
            esc_id: ID эскалации
            confirmed: подтверждено (для confirm level)
            alternative_index: выбранный вариант (для choose level)
            response: свободный ответ
        """
        esc = self._pending.pop(esc_id, None)
        if not esc:
            return None

        esc.response_time = time.time() - esc.timestamp

        if esc.is_expired():
            esc.status = EscalationStatus.TIMEOUT
        elif confirmed:
            esc.status = EscalationStatus.CONFIRMED
        elif alternative_index >= 0:
            esc.status = EscalationStatus.ALTERNATIVE
            if alternative_index < len(esc.alternatives):
                esc.response = esc.alternatives[alternative_index]
        elif response:
            esc.response = response
            esc.status = EscalationStatus.CONFIRMED
        else:
            esc.status = EscalationStatus.REJECTED

        self._archive(esc)
        logger.info("EscalationManager: resolved %s → %s (%.1fs)",
                     esc_id, esc.status, esc.response_time)
        return esc

    def cancel(self, esc_id: str) -> bool:
        """Отменить эскалацию."""
        esc = self._pending.pop(esc_id, None)
        if esc:
            esc.status = EscalationStatus.CANCELLED
            self._archive(esc)
            return True
        return False

    def expire_old(self) -> int:
        """Пометить просроченные эскалации. Returns: count expired."""
        expired = []
        for esc_id, esc in self._pending.items():
            if esc.is_expired():
                expired.append(esc_id)

        for esc_id in expired:
            esc = self._pending.pop(esc_id)
            esc.status = EscalationStatus.TIMEOUT
            self._archive(esc)

        return len(expired)

    # ── Callbacks ────────────────────────────────────────

    def on_escalation(self, callback: Callable[[EscalationRequest], None]) -> None:
        """Зарегистрировать callback на новую эскалацию."""
        self._callbacks.append(callback)

    # ── Query ────────────────────────────────────────────

    def get_pending(self) -> List[EscalationRequest]:
        """Список ожидающих эскалаций."""
        self.expire_old()
        return list(self._pending.values())

    def get_pending_count(self) -> int:
        """Количество ожидающих."""
        return len(self._pending)

    def get_history(self, limit: int = 30) -> List[Dict[str, Any]]:
        """История эскалаций."""
        return [e.to_dict() for e in list(self._history)[-limit:]]

    # ── Stats ────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Статистика."""
        statuses: Dict[str, int] = {}
        avg_response = 0.0
        count_with_time = 0
        for e in self._history:
            statuses[e.status] = statuses.get(e.status, 0) + 1
            if e.response_time > 0:
                avg_response += e.response_time
                count_with_time += 1

        return {
            "pending": len(self._pending),
            "total_history": len(self._history),
            "statuses": statuses,
            "avg_response_time": round(avg_response / max(1, count_with_time), 1),
        }

    # ── Internal ─────────────────────────────────────────

    def _archive(self, esc: EscalationRequest) -> None:
        """Архивировать эскалацию."""
        self._history.append(esc)  # auto-eviction by deque


# ─── Singleton ─────────────────────────────────────────────────────────────────

_manager: Optional[EscalationManager] = None

def get_escalation_manager() -> EscalationManager:
    """Получить единственный экземпляр EscalationManager."""
    global _manager
    if _manager is None:
        _manager = EscalationManager()
    return _manager
