# -*- coding: utf-8 -*-
"""
Lina Core — Mode Control (Phase 23).

Режимы работы системы:

  normal      — стандартный режим
  strict      — повышенная валидация, строгий постпроцессор
  safe        — минимальные привилегии, ограниченные tool-ы
  diagnostic  — расширенное логирование, trace в ответах
  minimal     — только LLM, без tool/rag/cv

Каждый режим меняет:
  router_threshold, tool_execution, rag_limit,
  regeneration_policy, max_tokens_cap

ModeControl ТОЛЬКО переключает — engine-ы читают через get_profile().
"""

import copy
import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, Optional

logger = logging.getLogger("lina.core.mode_control")


# ═══════════════════════════════════════════════════════════
#  Operating Modes
# ═══════════════════════════════════════════════════════════

class OperatingMode(str, Enum):
    """Режимы работы Lina."""
    NORMAL = "normal"
    STRICT = "strict"
    SAFE = "safe"
    DIAGNOSTIC = "diagnostic"
    MINIMAL = "minimal"


# ═══════════════════════════════════════════════════════════
#  Mode Profile
# ═══════════════════════════════════════════════════════════

@dataclass
class ModeProfile:
    """Профиль режима — конкретные значения параметров."""
    router_threshold: float = 0.5
    tool_execution: str = "normal"     # normal | restricted | disabled
    rag_limit: int = 500               # макс. RAG-токенов
    regeneration_policy: str = "auto"  # auto | manual | disabled
    max_tokens_cap: int = 512
    strict_validation: bool = False
    strict_post_processing: bool = False
    debug_output: bool = False
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "router_threshold": self.router_threshold,
            "tool_execution": self.tool_execution,
            "rag_limit": self.rag_limit,
            "regeneration_policy": self.regeneration_policy,
            "max_tokens_cap": self.max_tokens_cap,
            "strict_validation": self.strict_validation,
            "strict_post_processing": self.strict_post_processing,
            "debug_output": self.debug_output,
        }


# ═══════════════════════════════════════════════════════════
#  Predefined Mode Profiles
# ═══════════════════════════════════════════════════════════

MODE_PROFILES: Dict[OperatingMode, ModeProfile] = {
    OperatingMode.NORMAL: ModeProfile(
        router_threshold=0.5,
        tool_execution="normal",
        rag_limit=500,
        regeneration_policy="auto",
        max_tokens_cap=512,
        strict_validation=False,
        strict_post_processing=False,
        debug_output=False,
        description="Стандартный режим работы",
    ),
    OperatingMode.STRICT: ModeProfile(
        router_threshold=0.7,
        tool_execution="restricted",
        rag_limit=300,
        regeneration_policy="auto",
        max_tokens_cap=384,
        strict_validation=True,
        strict_post_processing=True,
        debug_output=False,
        description="Повышенная валидация, строгий контроль",
    ),
    OperatingMode.SAFE: ModeProfile(
        router_threshold=0.8,
        tool_execution="disabled",
        rag_limit=200,
        regeneration_policy="disabled",
        max_tokens_cap=256,
        strict_validation=True,
        strict_post_processing=True,
        debug_output=False,
        description="Безопасный режим, минимальные привилегии",
    ),
    OperatingMode.DIAGNOSTIC: ModeProfile(
        router_threshold=0.3,
        tool_execution="normal",
        rag_limit=500,
        regeneration_policy="manual",
        max_tokens_cap=512,
        strict_validation=False,
        strict_post_processing=False,
        debug_output=True,
        description="Диагностика, расширенное логирование",
    ),
    OperatingMode.MINIMAL: ModeProfile(
        router_threshold=0.9,
        tool_execution="disabled",
        rag_limit=0,
        regeneration_policy="disabled",
        max_tokens_cap=256,
        strict_validation=False,
        strict_post_processing=False,
        debug_output=False,
        description="Только LLM, без tool/rag/cv",
    ),
}


# ═══════════════════════════════════════════════════════════
#  Mode Controller
# ═══════════════════════════════════════════════════════════

class ModeController:
    """Контроллер режимов (Phase 23).

    Переключает режимы системы.
    Engine-ы читают параметры через get_profile().

    Usage:
        mc = ModeController()
        mc.switch(OperatingMode.STRICT)
        profile = mc.get_profile()
        # profile.router_threshold == 0.7
    """

    def __init__(self, initial: OperatingMode = OperatingMode.NORMAL):
        self._mode = initial
        self._profile = copy.copy(MODE_PROFILES[initial])
        self._history: deque = deque(maxlen=100)

    @property
    def mode(self) -> OperatingMode:
        """Текущий режим."""
        return self._mode

    def switch(self, mode: OperatingMode, reason: str = "") -> ModeProfile:
        """Переключает режим.

        Args:
            mode: Новый режим.
            reason: Причина переключения (для логов).

        Returns:
            ModeProfile нового режима.
        """
        old = self._mode
        self._mode = mode
        self._profile = copy.copy(MODE_PROFILES[mode])
        self._history.append({
            "from": old.value, "to": mode.value,
            "reason": reason,
        })

        logger.info(
            "MODE_SWITCH: %s → %s (reason: %s)",
            old.value, mode.value, reason or "manual",
        )

        return self._profile

    def switch_by_name(self, name: str, reason: str = "") -> Optional[ModeProfile]:
        """Переключает режим по имени строки.

        Returns:
            ModeProfile или None если имя невалидно.
        """
        try:
            mode = OperatingMode(name.lower())
        except ValueError:
            logger.warning("MODE: unknown mode '%s'", name)
            return None
        return self.switch(mode, reason)

    def get_profile(self) -> ModeProfile:
        """Текущий профиль режима (копия)."""
        return copy.copy(self._profile)

    def get_all_modes(self) -> Dict[str, str]:
        """Все доступные режимы с описанием."""
        return {
            m.value: MODE_PROFILES[m].description
            for m in OperatingMode
        }

    def get_history(self) -> list:
        """История переключений."""
        return list(self._history)

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для SystemControl."""
        return {
            "current_mode": self._mode.value,
            "description": self._profile.description,
            "switches": len(self._history),
            "profile": self._profile.to_dict(),
        }
