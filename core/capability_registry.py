# -*- coding: utf-8 -*-
"""
Lina Core — Capability Registry (Phase 24).

Реестр возможностей системы.
Отслеживает какие capabilities:
  - активны (enabled)
  - отключены режимом (disabled)
  - временно заблокированы деградацией (blocked)

Оркестратор выполнения работает через registry,
а НЕ напрямую через engine-ы.

CapabilityRegistry — ТОЛЬКО реестр.
Не выполняет и не решает.
"""

import time
import logging
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional

logger = logging.getLogger("lina.core.capability_registry")


# ═══════════════════════════════════════════════════════════
#  Capability Status
# ═══════════════════════════════════════════════════════════

class CapabilityStatus(str, Enum):
    """Статус возможности."""
    ACTIVE = "active"
    DISABLED = "disabled"       # отключена конфигурацией/режимом
    BLOCKED = "blocked"         # временно заблокирована деградацией


@dataclass
class CapabilityInfo:
    """Информация о возможности."""
    name: str = ""
    status: CapabilityStatus = CapabilityStatus.ACTIVE
    description: str = ""
    disabled_reason: str = ""
    blocked_reason: str = ""
    blocked_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "status": self.status.value,
        }
        if self.description:
            d["description"] = self.description
        if self.disabled_reason:
            d["disabled_reason"] = self.disabled_reason
        if self.blocked_reason:
            d["blocked_reason"] = self.blocked_reason
        return d


# ═══════════════════════════════════════════════════════════
#  Capability Registry
# ═══════════════════════════════════════════════════════════

class CapabilityRegistry:
    """Реестр возможностей системы (Phase 24).

    Регистрирует и отслеживает состояние каждой capability.
    Стандартные capabilities:
      llm, tool, rag, cv, web, file, system, chain, macro

    Usage:
        reg = CapabilityRegistry()
        reg.register("llm", "Генерация текста LLM")
        reg.register("tool", "Внешние инструменты")
        reg.disable("tool", "safe mode active")
        assert not reg.is_available("tool")
        reg.enable("tool")
        assert reg.is_available("tool")
    """

    # Default capabilities registered on init
    DEFAULT_CAPABILITIES = [
        ("llm", "Генерация текста через LLM"),
        ("tool", "Выполнение внешних инструментов"),
        ("rag", "Поиск по базе знаний"),
        ("cv", "Компьютерное зрение"),
        ("web", "Поиск в интернете (WebSearchEngine)"),
        ("app_launcher", "Обнаружение и запуск приложений (ApplicationResolver)"),
        ("file", "Файловые операции"),
        ("system", "Системные команды"),
        ("chain", "Цепочки действий"),
        ("macro", "Макросы"),
    ]

    def __init__(self, register_defaults: bool = True):
        self._capabilities: Dict[str, CapabilityInfo] = {}
        self._history: deque = deque(maxlen=500)

        if register_defaults:
            for name, desc in self.DEFAULT_CAPABILITIES:
                self.register(name, desc)

    def register(self, name: str, description: str = "") -> None:
        """Регистрирует capability.

        Args:
            name: Уникальное имя (llm, tool, rag, etc.)
            description: Описание.
        """
        self._capabilities[name] = CapabilityInfo(
            name=name,
            status=CapabilityStatus.ACTIVE,
            description=description,
        )
        logger.debug("CAPABILITY: registered '%s'", name)

    def disable(self, name: str, reason: str = "") -> bool:
        """Отключает capability (конфигурацией/режимом).

        Returns:
            True если capability существует.
        """
        cap = self._capabilities.get(name)
        if not cap:
            logger.warning("CAPABILITY: unknown '%s'", name)
            return False

        old = cap.status
        cap.status = CapabilityStatus.DISABLED
        cap.disabled_reason = reason

        self._history.append({
            "action": "disable", "name": name,
            "reason": reason, "from": old.value,
            "time": time.time(),
        })
        logger.info("CAPABILITY: disabled '%s' — %s", name, reason)
        return True

    def block(self, name: str, reason: str = "") -> bool:
        """Временно блокирует capability (деградация).

        Returns:
            True если capability существует.
        """
        cap = self._capabilities.get(name)
        if not cap:
            logger.warning("CAPABILITY: unknown '%s'", name)
            return False

        old = cap.status
        cap.status = CapabilityStatus.BLOCKED
        cap.blocked_reason = reason
        cap.blocked_at = time.time()

        self._history.append({
            "action": "block", "name": name,
            "reason": reason, "from": old.value,
            "time": time.time(),
        })
        logger.info("CAPABILITY: blocked '%s' — %s", name, reason)
        return True

    def enable(self, name: str) -> bool:
        """Включает capability (снимает disable).

        Returns:
            True если capability существует.
        """
        cap = self._capabilities.get(name)
        if not cap:
            return False

        old = cap.status
        cap.status = CapabilityStatus.ACTIVE
        cap.disabled_reason = ""

        self._history.append({
            "action": "enable", "name": name,
            "from": old.value, "time": time.time(),
        })
        logger.debug("CAPABILITY: enabled '%s'", name)
        return True

    def unblock(self, name: str) -> bool:
        """Снимает блокировку capability.

        Returns:
            True если capability существует.
        """
        cap = self._capabilities.get(name)
        if not cap:
            return False

        old = cap.status
        cap.status = CapabilityStatus.ACTIVE
        cap.blocked_reason = ""
        cap.blocked_at = 0.0

        self._history.append({
            "action": "unblock", "name": name,
            "from": old.value, "time": time.time(),
        })
        logger.debug("CAPABILITY: unblocked '%s'", name)
        return True

    def is_available(self, name: str) -> bool:
        """Проверяет доступность capability.

        Returns:
            True только если status == ACTIVE.
        """
        cap = self._capabilities.get(name)
        if not cap:
            return False
        return cap.status == CapabilityStatus.ACTIVE

    def get_active(self) -> List[str]:
        """Список активных capabilities."""
        return [
            name for name, cap in self._capabilities.items()
            if cap.status == CapabilityStatus.ACTIVE
        ]

    def get_disabled(self) -> List[str]:
        """Список отключённых capabilities."""
        return [
            name for name, cap in self._capabilities.items()
            if cap.status == CapabilityStatus.DISABLED
        ]

    def get_blocked(self) -> List[str]:
        """Список заблокированных capabilities."""
        return [
            name for name, cap in self._capabilities.items()
            if cap.status == CapabilityStatus.BLOCKED
        ]

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """Полный статус реестра."""
        return {
            name: cap.to_dict()
            for name, cap in self._capabilities.items()
        }

    def get_history(self) -> List[Dict[str, Any]]:
        """История изменений."""
        return list(self._history)

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для SystemControl."""
        return {
            "total": len(self._capabilities),
            "active": len(self.get_active()),
            "disabled": len(self.get_disabled()),
            "blocked": len(self.get_blocked()),
            "active_list": self.get_active(),
            "disabled_list": self.get_disabled(),
            "blocked_list": self.get_blocked(),
            "changes": len(self._history),
        }
