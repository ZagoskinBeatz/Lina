"""
Intent Types — структуры данных для Intent API.

Intent — строгая структура, описывающая желание пользователя.
UI генерирует Intent. Governance решает и выполняет.

Примеры:
    Intent(type=IntentType.OPEN_APP, domain="desktop", action="open_app",
           params={"app": "firefox"})
    Intent(type=IntentType.DIAGNOSE, domain="network",
           action="net_diagnose_dns")
    Intent(type=IntentType.SYSTEM_ACTION, domain="service",
           action="svc_restart", params={"service": "NetworkManager"})

Phase: CONTROL PLANE / Intent Layer
"""

from __future__ import annotations

import time
import uuid
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Intent Type ──────────────────────────────────────────────────────────────

class IntentType(str, Enum):
    """Тип намерения пользователя."""

    # User-level
    OPEN_APP = "open_app"               # Запуск приложения
    DIAGNOSE = "diagnose"               # Диагностика проблемы
    QUERY = "query"                     # Информационный запрос
    SET_MODE = "set_mode"               # Переключение режима
    SEARCH = "search"                   # Поиск по KB

    # Power-level
    SYSTEM_ACTION = "system_action"     # Системное действие (systemctl, pacman...)
    CONFIGURE = "configure"             # Изменение конфигурации
    PACKAGE_OP = "package_op"           # Операция с пакетами

    # Admin-level
    DISK_OP = "disk_op"                 # Дисковые операции
    INSTALLER = "installer"             # Установка ОС
    LOW_LEVEL = "low_level"             # Низкоуровневые операции

    # Internal
    CHAT = "chat"                       # Обычный разговор (LLM fallback)
    ESCALATION = "escalation"           # Эскалация от другого модуля
    UNKNOWN = "unknown"                 # Не удалось распознать


# ─── Intent ───────────────────────────────────────────────────────────────────

@dataclass
class Intent:
    """
    Структура, описывающая желание пользователя.

    UI не выполняет действия — UI генерирует Intent.
    Governance проверяет и выполняет.

    Attributes:
        type:        Тип намерения (IntentType)
        domain:      Домен (network, audio, service, package, disk, ...)
        action:      ID действия из ActionRegistry (или пустой для CHAT/QUERY)
        params:      Параметры действия
        source:      Источник (ui, cli, dbus, hotkey, internal)
        user_text:   Оригинальный текст пользователя
        confidence:  Уверенность в распознавании (0.0–1.0)
        id:          Уникальный ID intent
        timestamp:   Время создания
        metadata:    Дополнительные данные
    """
    type: IntentType = IntentType.UNKNOWN
    domain: str = ""
    action: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    source: str = "ui"
    user_text: str = ""
    confidence: float = 1.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Phase 5: Zero-trust validation constants
    _MAX_USER_TEXT = 4096
    _MAX_DOMAIN = 64
    _MAX_ACTION = 128
    _MAX_SOURCE = 32
    _MAX_PARAMS_KEYS = 32

    def __post_init__(self) -> None:
        """Phase 5: Validate and clamp fields at creation time."""
        # Truncate oversized strings (defense in depth — validator should catch first)
        if isinstance(self.user_text, str) and len(self.user_text) > self._MAX_USER_TEXT:
            self.user_text = self.user_text[:self._MAX_USER_TEXT]
            logger.warning("Intent: user_text truncated to %d chars", self._MAX_USER_TEXT)
        if isinstance(self.domain, str) and len(self.domain) > self._MAX_DOMAIN:
            self.domain = self.domain[:self._MAX_DOMAIN]
        if isinstance(self.action, str) and len(self.action) > self._MAX_ACTION:
            self.action = self.action[:self._MAX_ACTION]
        if isinstance(self.source, str) and len(self.source) > self._MAX_SOURCE:
            self.source = self.source[:self._MAX_SOURCE]
        # Clamp confidence to [0.0, 1.0]
        if not isinstance(self.confidence, (int, float)):
            self.confidence = 0.0
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        # Limit params size
        if isinstance(self.params, dict) and len(self.params) > self._MAX_PARAMS_KEYS:
            logger.warning("Intent: params truncated to %d keys", self._MAX_PARAMS_KEYS)
            keys = list(self.params.keys())[:self._MAX_PARAMS_KEYS]
            self.params = {k: self.params[k] for k in keys}

    def requires_action(self) -> bool:
        """Требуется ли выполнение действия (не просто ответ)."""
        return self.type not in (IntentType.CHAT, IntentType.QUERY,
                                  IntentType.SEARCH, IntentType.UNKNOWN)

    def is_admin(self) -> bool:
        """Требуется ли admin-уровень."""
        return self.type in (IntentType.DISK_OP, IntentType.INSTALLER,
                              IntentType.LOW_LEVEL)

    def is_power(self) -> bool:
        """Требуется ли power-уровень."""
        return self.type in (IntentType.SYSTEM_ACTION, IntentType.CONFIGURE,
                              IntentType.PACKAGE_OP)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, Enum) else self.type,
            "domain": self.domain,
            "action": self.action,
            "params": self.params,
            "source": self.source,
            "user_text": self.user_text,
            "confidence": round(self.confidence, 3),
            "timestamp": self.timestamp,
        }

    def __repr__(self) -> str:
        return (f"Intent(type={self.type.value}, domain={self.domain!r}, "
                f"action={self.action!r}, source={self.source!r})")


# ─── Intent Result ────────────────────────────────────────────────────────────

class IntentStatus(str, Enum):
    """Статус обработки intent."""
    SUCCESS = "success"
    DENIED = "denied"
    NEEDS_CONFIRM = "needs_confirm"
    FAILED = "failed"
    ESCALATED = "escalated"
    NOT_FOUND = "not_found"
    CHAT_RESPONSE = "chat_response"


@dataclass
class IntentResult:
    """
    Результат обработки Intent через governance.

    Attributes:
        intent_id:       ID исходного intent
        status:          Статус обработки
        response_text:   Текст ответа для пользователя
        action_result:   Результат выполнения действия (если было)
        policy_decision: Решение PolicyEngine
        escalation_id:   ID запроса эскалации (если нужно подтверждение)
        duration_ms:     Время обработки (мс)
        metadata:        Дополнительные данные
    """
    intent_id: str = ""
    status: IntentStatus = IntentStatus.SUCCESS
    response_text: str = ""
    action_result: Optional[Dict[str, Any]] = None
    policy_decision: str = ""
    escalation_id: str = ""
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "status": self.status.value,
            "response_text": self.response_text,
            "policy_decision": self.policy_decision,
            "escalation_id": self.escalation_id,
            "duration_ms": round(self.duration_ms, 1),
        }
