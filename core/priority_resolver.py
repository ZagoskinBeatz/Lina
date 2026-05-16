# -*- coding: utf-8 -*-
"""
Lina Core — Priority Resolver (Phase 24).

Разрешение приоритетов выполнения:

  1 — system commands   (наивысший)
  2 — safety
  3 — user explicit tool
  4 — LLM reasoning
  5 — fallback          (низший)

При конфликте — выбирается более высокий (меньший номер).
PriorityResolver — ТОЛЬКО определяет приоритет.
Не выполняет и не маршрутизирует.
"""

import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, Any, Optional, List

logger = logging.getLogger("lina.core.priority_resolver")


# ═══════════════════════════════════════════════════════════
#  Priority Levels
# ═══════════════════════════════════════════════════════════

class PriorityLevel(IntEnum):
    """Уровни приоритета (1 = высший)."""
    SYSTEM = 1       # /system команды
    SAFETY = 2       # Безопасность (safe mode triggers)
    USER_TOOL = 3    # Явный tool-запрос пользователя
    LLM = 4          # LLM reasoning (chat, math)
    FALLBACK = 5     # Fallback, intent unclear


PRIORITY_DESCRIPTIONS: Dict[int, str] = {
    1: "system command — наивысший приоритет",
    2: "safety — критические проверки безопасности",
    3: "user explicit tool — пользователь запросил tool",
    4: "LLM reasoning — стандартная генерация",
    5: "fallback — не удалось определить intent",
}

# Intent → default priority mapping
_INTENT_PRIORITIES: Dict[str, PriorityLevel] = {
    "system_command": PriorityLevel.SYSTEM,
    "meta": PriorityLevel.SYSTEM,
    "safety": PriorityLevel.SAFETY,
    "tool_explicit": PriorityLevel.USER_TOOL,
    "web": PriorityLevel.USER_TOOL,
    "file_operation": PriorityLevel.USER_TOOL,
    "chat": PriorityLevel.LLM,
    "math": PriorityLevel.LLM,
    "rag": PriorityLevel.LLM,
    "cv": PriorityLevel.USER_TOOL,
    "chain": PriorityLevel.USER_TOOL,
    "macro": PriorityLevel.USER_TOOL,
}


# ═══════════════════════════════════════════════════════════
#  Resolution Result
# ═══════════════════════════════════════════════════════════

@dataclass
class PriorityResult:
    """Результат разрешения приоритета."""
    level: int = 5
    description: str = ""
    intent: str = ""
    confidence: float = 0.0
    override_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "level": self.level,
            "description": self.description,
            "intent": self.intent,
        }
        if self.override_reason:
            d["override_reason"] = self.override_reason
        return d


# ═══════════════════════════════════════════════════════════
#  Priority Resolver
# ═══════════════════════════════════════════════════════════

class PriorityResolver:
    """Определяет приоритет выполнения (Phase 24).

    При конфликте — выбирается более высокий (меньшее число).
    Изолирован от engine-ов.

    Usage:
        pr = PriorityResolver()
        result = pr.resolve("chat", confidence=0.8)
        # result.level == 4 (LLM)

        # При safe_mode
        result = pr.resolve("chat", confidence=0.8, safe_mode=True)
        # result.level == 2 (SAFETY override)
    """

    MAX_OVERRIDES = 50

    def __init__(self):
        self._overrides: Dict[str, int] = {}
        self._resolve_count: int = 0
        self._conflict_count: int = 0

    def resolve(
        self,
        intent: str,
        confidence: float = 0.5,
        *,
        safe_mode: bool = False,
        is_system: bool = False,
        is_explicit_tool: bool = False,
    ) -> PriorityResult:
        """Определяет уровень приоритета.

        Args:
            intent: Идентификатор намерения.
            confidence: Уверенность роутера.
            safe_mode: Активен ли safe mode.
            is_system: Является ли системной командой.
            is_explicit_tool: Явный запрос tool-а.

        Returns:
            PriorityResult.
        """
        self._resolve_count += 1

        # 1. System override (highest)
        if is_system or intent in ("system_command", "meta"):
            return PriorityResult(
                level=PriorityLevel.SYSTEM,
                description=PRIORITY_DESCRIPTIONS[1],
                intent=intent,
                confidence=confidence,
            )

        # 2. Safety override
        if safe_mode:
            return PriorityResult(
                level=PriorityLevel.SAFETY,
                description=PRIORITY_DESCRIPTIONS[2],
                intent=intent,
                confidence=confidence,
                override_reason="safe_mode active",
            )

        # 3. Custom override?
        if intent in self._overrides:
            lvl = self._overrides[intent]
            return PriorityResult(
                level=lvl,
                description=PRIORITY_DESCRIPTIONS.get(lvl, "custom"),
                intent=intent,
                confidence=confidence,
                override_reason="custom override",
            )

        # 4. Intent-based mapping
        base_level = _INTENT_PRIORITIES.get(intent, PriorityLevel.FALLBACK)

        # 5. Low confidence → downgrade to fallback
        if confidence < 0.3 and base_level.value > PriorityLevel.SYSTEM:
            self._conflict_count += 1
            return PriorityResult(
                level=PriorityLevel.FALLBACK,
                description=PRIORITY_DESCRIPTIONS[5],
                intent=intent,
                confidence=confidence,
                override_reason=f"low confidence ({confidence:.2f})",
            )

        # 6. Explicit tool hint
        if is_explicit_tool and base_level.value > PriorityLevel.USER_TOOL:
            return PriorityResult(
                level=PriorityLevel.USER_TOOL,
                description=PRIORITY_DESCRIPTIONS[3],
                intent=intent,
                confidence=confidence,
                override_reason="explicit tool flag",
            )

        return PriorityResult(
            level=base_level.value,
            description=PRIORITY_DESCRIPTIONS.get(base_level.value, ""),
            intent=intent,
            confidence=confidence,
        )

    def compare(self, a: int, b: int) -> int:
        """Сравнивает два приоритета.

        Returns:
            Более высокий (меньший номер).
        """
        return min(a, b)

    def set_override(self, intent: str, level: int) -> None:
        """Устанавливает пользовательский override приоритета.

        Args:
            intent: Идентификатор intent.
            level: Уровень приоритета (1-5).
        """
        _MAX_OVERRIDES = self.MAX_OVERRIDES
        if 1 <= level <= 5:
            if intent not in self._overrides and len(self._overrides) >= _MAX_OVERRIDES:
                logger.warning("PRIORITY: override limit reached (%d)", _MAX_OVERRIDES)
                return
            self._overrides[intent] = level
            logger.debug("PRIORITY: override %s → %d", intent, level)

    def clear_overrides(self) -> None:
        """Сброс всех override-ов."""
        self._overrides.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для SystemControl."""
        return {
            "resolve_count": self._resolve_count,
            "conflict_count": self._conflict_count,
            "overrides": dict(self._overrides),
            "levels": {v: PRIORITY_DESCRIPTIONS[v] for v in range(1, 6)},
        }
