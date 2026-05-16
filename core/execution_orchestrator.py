# -*- coding: utf-8 -*-
"""
Lina Core — Execution Orchestrator (Phase 24).

Слой стратегического принятия решений.
Отделяет «определение намерения» от «способа исполнения».

Router ТОЛЬКО классифицирует.
Orchestrator решает КАК исполнять.

Принимает:
  intent, confidence, runtime_state, mode, config, trace_context

Возвращает:
  ExecutionPlan {
    primary_path, fallback_path, regeneration_allowed,
    tool_allowed, max_tokens_override, validation_policy,
    priority_level, plan_hash, steps
  }

Правила:
  - Одинаковый input + state = одинаковый plan (детерминизм)
  - hash(plan) добавляется в trace
  - Multi-step: каждый step проходит guard + validation
  - Orchestrator работает через capability_info,
    а НЕ напрямую через engine-ы
"""

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger("lina.core.execution_orchestrator")


# ═══════════════════════════════════════════════════════════
#  Execution Step (multi-step plan)
# ═══════════════════════════════════════════════════════════

@dataclass
class ExecutionStep:
    """Один шаг multi-step плана."""
    step_number: int = 1
    path: str = "LLM"             # LLM | TOOL | SYSTEM
    requires_guard: bool = True
    requires_validation: bool = True
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step_number,
            "path": self.path,
            "guard": self.requires_guard,
            "validation": self.requires_validation,
        }


# ═══════════════════════════════════════════════════════════
#  Execution Plan
# ═══════════════════════════════════════════════════════════

@dataclass
class ExecutionPlan:
    """План исполнения — результат работы Orchestrator-а.

    Детерминистичен: одинаковый input + state = одинаковый plan.
    """
    primary_path: str = "LLM"     # LLM | TOOL | SYSTEM
    fallback_path: Optional[str] = None
    regeneration_allowed: bool = True
    tool_allowed: bool = True
    max_tokens_override: Optional[int] = None
    validation_policy: str = "normal"   # normal | strict
    priority_level: int = 4             # 1-5
    plan_hash: str = ""
    steps: List[ExecutionStep] = field(default_factory=list)
    intent: str = ""
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.plan_hash:
            self.plan_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Детерминистичный hash плана."""
        data = {
            "primary_path": self.primary_path,
            "fallback_path": self.fallback_path,
            "regeneration_allowed": self.regeneration_allowed,
            "tool_allowed": self.tool_allowed,
            "max_tokens_override": self.max_tokens_override,
            "validation_policy": self.validation_policy,
            "priority_level": self.priority_level,
            "steps": [s.to_dict() for s in self.steps],
        }
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_path": self.primary_path,
            "fallback_path": self.fallback_path,
            "regeneration_allowed": self.regeneration_allowed,
            "tool_allowed": self.tool_allowed,
            "max_tokens_override": self.max_tokens_override,
            "validation_policy": self.validation_policy,
            "priority_level": self.priority_level,
            "plan_hash": self.plan_hash,
            "steps": [s.to_dict() for s in self.steps],
            "intent": self.intent,
            "confidence": self.confidence,
        }

    def is_multi_step(self) -> bool:
        """Является ли план многошаговым."""
        return len(self.steps) > 1


# ═══════════════════════════════════════════════════════════
#  Path mapping
# ═══════════════════════════════════════════════════════════

# Intent → primary execution path
_INTENT_PATH_MAP: Dict[str, str] = {
    "chat": "LLM",
    "math": "LLM",
    "rag": "LLM",
    "system_command": "SYSTEM",
    "meta": "SYSTEM",
    "file_operation": "TOOL",
    "web": "WEB_SEARCH",           # → WebSearchEngine (валюта, новости)
    "web_search": "WEB_SEARCH",      # → WebSearchEngine (общий)
    "weather_query": "WEB_SEARCH",   # → WebSearchEngine.weather
    "install_application": "TOOL",   # → ApplicationResolver.suggest_installation
    "tool_explicit": "TOOL",
    "cv": "TOOL",
    "chain": "TOOL",
    "macro": "TOOL",
    "open_application": "TOOL",     # → ApplicationResolver
    "system_diagnostic": "DIAGNOSTIC",     # → DiagnosticEngine (trees + LLM fallback)
}

# Intent → fallback path
_FALLBACK_MAP: Dict[str, str] = {
    "tool_explicit": "LLM",
    "web": "LLM",
    "web_search": "LLM",
    "weather_query": "LLM",
    "install_application": "LLM",
    "file_operation": "LLM",
    "cv": "LLM",
    "chain": "LLM",
    "macro": "LLM",
    "rag": "LLM",
    "open_application": "TOOL",     # fallback → WEB_SEARCH (not LLM)
    "system_diagnostic": "LLM",     # fallback → LLM
}

# Intent → priority level (1=highest, 5=lowest)
_INTENT_PRIORITY: Dict[str, int] = {
    "system_command": 1,
    "meta": 1,
    "open_application": 3,
    "install_application": 3,
    "weather_query": 3,
    "web_search": 4,
    "web": 4,
    "file_operation": 3,
    "cv": 3,
    "tool_explicit": 3,
    "chain": 3,
    "macro": 3,
    "rag": 4,
    "chat": 5,
    "math": 5,
    "system_diagnostic": 2,
}

# Intent → validation policy override
_INTENT_VALIDATION: Dict[str, str] = {
    "open_application": "strict",
    "install_application": "strict",
    "web_search": "strict",
    "weather_query": "strict",
    "system_diagnostic": "strict",
}

# Intent → regeneration policy
_INTENT_REGEN: Dict[str, bool] = {
    "open_application": False,     # не перезапускать приложения повторно
    "install_application": False,
    "web_search": True,
    "weather_query": True,
    "system_diagnostic": False,  # не повторять \u0434иагностику автоматом
}


# ═══════════════════════════════════════════════════════════
#  Execution Orchestrator
# ═══════════════════════════════════════════════════════════

class ExecutionOrchestrator:
    """Стратегический оркестратор выполнения (Phase 24).

    Router ТОЛЬКО классифицирует.
    Orchestrator решает КАК исполнять.

    Работает через capability_info (dict), а НЕ через
    engine-ы или registry напрямую.

    Гарантии:
      - Детерминизм: одинаковый input + state → одинаковый plan
      - hash(plan) всегда доступен для trace
      - Multi-step: guard + validation на каждом шаге

    Usage:
        orch = ExecutionOrchestrator()
        plan = orch.create_plan(
            intent="chat",
            confidence=0.85,
            runtime_state={"mode": "normal", "safe_mode": False},
            capability_info={"llm": True, "tool": True},
        )
        # plan.primary_path == "LLM"
        # plan.plan_hash == "abc123..."
    """

    def __init__(self):
        self._plan_count: int = 0
        self._multi_step_count: int = 0
        self._stats_lock = threading.Lock()

    def create_plan(
        self,
        intent: str,
        confidence: float = 0.5,
        *,
        runtime_state: Optional[Dict[str, Any]] = None,
        capability_info: Optional[Dict[str, bool]] = None,
        mode_profile: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        priority_level: Optional[int] = None,
        trace_context: Optional[Dict[str, Any]] = None,
    ) -> ExecutionPlan:
        """Создаёт план исполнения.

        Args:
            intent: Намерение (от Router).
            confidence: Уверенность (от Router).
            runtime_state: Текущее состояние (от governance).
            capability_info: Доступность capabilities {name: bool}.
            mode_profile: Профиль режима (от ModeControl).
            config: Конфигурация (от ConfigManager).
            priority_level: Уровень приоритета (1-5, от resolver-а).
            trace_context: Контекст трассировки.

        Returns:
            ExecutionPlan — детерминистичный план.
        """
        with self._stats_lock:
            self._plan_count += 1

        state = runtime_state or {}
        caps = capability_info or {}
        profile = mode_profile or {}
        cfg = config or {}

        safe_mode = state.get("safe_mode", False)
        current_mode = state.get("mode", "normal")
        tool_mode = state.get("tool_mode", "normal")

        # ─── Determine primary path ────────────────────
        primary = _INTENT_PATH_MAP.get(intent, "LLM")
        fallback = _FALLBACK_MAP.get(intent)

        # ─── Tool availability check ──────────────────
        tool_allowed = (
            tool_mode != "disabled"
            and caps.get("tool", True)
            and not safe_mode
        )

        # If tool path but tools disabled → fallback to LLM
        if primary == "TOOL" and not tool_allowed:
            if fallback:
                primary = fallback
                fallback = None
            else:
                primary = "LLM"

        # ─── Regeneration policy ──────────────────────
        regen_policy = profile.get("regeneration_policy", "auto")
        # Per-intent override
        intent_regen = _INTENT_REGEN.get(intent)
        if intent_regen is not None:
            regeneration_allowed = intent_regen and not safe_mode
        else:
            regeneration_allowed = regen_policy != "disabled" and not safe_mode

        # ─── Validation policy ────────────────────────
        strict_val = profile.get("strict_validation", False)
        intent_val = _INTENT_VALIDATION.get(intent)
        if intent_val:
            validation_policy = intent_val
        elif strict_val or safe_mode or current_mode == "strict":
            validation_policy = "strict"
        else:
            validation_policy = "normal"

        # ─── Max tokens override ──────────────────────
        max_tokens_override: Optional[int] = None
        profile_cap = profile.get("max_tokens_cap")
        if profile_cap and isinstance(profile_cap, int):
            max_tokens_override = profile_cap

        if safe_mode and (max_tokens_override is None
                          or max_tokens_override > 256):
            max_tokens_override = 256

        # ─── Priority — intent-specific override ─────
        prio = priority_level if priority_level is not None else _INTENT_PRIORITY.get(intent, 4)

        # ─── Build single-step plan ──────────────────
        steps = [
            ExecutionStep(
                step_number=1,
                path=primary,
                requires_guard=True,
                requires_validation=True,
            )
        ]

        plan = ExecutionPlan(
            primary_path=primary,
            fallback_path=fallback,
            regeneration_allowed=regeneration_allowed,
            tool_allowed=tool_allowed,
            max_tokens_override=max_tokens_override,
            validation_policy=validation_policy,
            priority_level=prio,
            steps=steps,
            intent=intent,
            confidence=confidence,
        )

        logger.debug(
            "ORCHESTRATOR: plan created — intent=%s path=%s prio=%d hash=%s",
            intent, primary, prio, plan.plan_hash,
        )

        return plan

    def create_multi_step_plan(
        self,
        step_definitions: List[Dict[str, Any]],
        *,
        intent: str = "",
        confidence: float = 0.5,
        runtime_state: Optional[Dict[str, Any]] = None,
        capability_info: Optional[Dict[str, bool]] = None,
        mode_profile: Optional[Dict[str, Any]] = None,
        priority_level: Optional[int] = None,
    ) -> ExecutionPlan:
        """Создаёт multi-step план.

        Каждый step проходит guard + validation.

        Args:
            step_definitions: Список шагов:
                [{"path": "TOOL"}, {"path": "LLM"}]
            intent: Общее намерение.
            confidence: Уверенность.
            runtime_state: Состояние.
            capability_info: Capabilities.
            mode_profile: Профиль режима.
            priority_level: Приоритет.

        Returns:
            ExecutionPlan с несколькими шагами.
        """
        with self._stats_lock:
            self._plan_count += 1
            self._multi_step_count += 1

        state = runtime_state or {}
        caps = capability_info or {}
        profile = mode_profile or {}
        safe_mode = state.get("safe_mode", False)
        tool_mode = state.get("tool_mode", "normal")

        tool_allowed = (
            tool_mode != "disabled"
            and caps.get("tool", True)
            and not safe_mode
        )

        steps: List[ExecutionStep] = []
        for i, sdef in enumerate(step_definitions, 1):
            path = sdef.get("path", "LLM")

            # If this step needs tool but tool disabled → LLM
            if path == "TOOL" and not tool_allowed:
                path = "LLM"

            steps.append(ExecutionStep(
                step_number=i,
                path=path,
                requires_guard=sdef.get("guard", True),
                requires_validation=sdef.get("validation", True),
                description=sdef.get("description", ""),
            ))

        primary = steps[0].path if steps else "LLM"
        fallback_path = None
        if len(steps) == 1:
            fallback_path = _FALLBACK_MAP.get(intent)

        # Validation & regen policies
        regen_policy = profile.get("regeneration_policy", "auto")
        regeneration_allowed = regen_policy != "disabled" and not safe_mode
        strict_val = profile.get("strict_validation", False)
        validation_policy = "strict" if (strict_val or safe_mode) else "normal"

        max_tokens_override: Optional[int] = None
        profile_cap = profile.get("max_tokens_cap")
        if profile_cap and isinstance(profile_cap, int):
            max_tokens_override = profile_cap

        prio = priority_level if priority_level is not None else 4

        plan = ExecutionPlan(
            primary_path=primary,
            fallback_path=fallback_path,
            regeneration_allowed=regeneration_allowed,
            tool_allowed=tool_allowed,
            max_tokens_override=max_tokens_override,
            validation_policy=validation_policy,
            priority_level=prio,
            steps=steps,
            intent=intent,
            confidence=confidence,
        )

        logger.debug(
            "ORCHESTRATOR: multi-step plan — %d steps, hash=%s",
            len(steps), plan.plan_hash,
        )

        return plan

    def verify_determinism(
        self,
        plan_a: "ExecutionPlan",
        plan_b: "ExecutionPlan",
    ) -> bool:
        """Проверяет детерминизм: два плана с одинаковым input
        должны иметь одинаковый hash.

        Returns:
            True если хэши совпадают.
        """
        return plan_a.plan_hash == plan_b.plan_hash

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для SystemControl."""
        with self._stats_lock:
            pc = self._plan_count
            mc = self._multi_step_count
        return {
            "plans_created": pc,
            "multi_step_plans": mc,
            "single_step_plans": pc - mc,
        }
