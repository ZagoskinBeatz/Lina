# -*- coding: utf-8 -*-
"""
Lina Agent — Агентный планировщик (Agent Planner).

Обёртка над planning.Planner с агентной автономией:
  1. Классифицирует цель (intent + complexity)
  2. Создаёт план (template / LLM)
  3. Управляет зависимостями шагов
  4. Поддерживает перепланирование (replan)

Phase 10 — AI Runtime v2.
"""

import copy
import logging
import time
from typing import Optional, Dict, Any, Callable, List

from lina.planning.planner import Planner
from lina.planning.state import Plan, PlanStep, PlanStatus, StepType

logger = logging.getLogger("lina.agent.planner")


# ═══════════════════════════════════════════════════════════
#  Конфигурация
# ═══════════════════════════════════════════════════════════

MAX_REPLANS = 3          # Максимум перепланирований
MAX_AGENT_STEPS = 10     # Максимум шагов в агентном режиме


# ═══════════════════════════════════════════════════════════
#  AgentPlanner
# ═══════════════════════════════════════════════════════════

class AgentPlanner:
    """Планировщик для агентного слоя.

    Расширяет базовый planning.Planner:
      - Автоматическое перепланирование при REPLAN
      - Dependency tracking между шагами
      - Лимит перепланирований (MAX_REPLANS)
      - Статистика и логирование

    Attributes:
        planner: Базовый Planning.Planner.
        max_replans: Максимум перепланирований.
        max_steps: Максимум шагов.
        _replan_count: Счётчик перепланирований.
        _stats: Статистика.
    """

    def __init__(
        self,
        llm_fn: Optional[Callable[[str], str]] = None,
        max_replans: int = MAX_REPLANS,
        max_steps: int = MAX_AGENT_STEPS,
    ):
        """Инициализация.

        Args:
            llm_fn: Функция LLM для генерации планов.
            max_replans: Максимум перепланирований.
            max_steps: Максимум шагов.
        """
        self.planner = Planner(llm_fn=llm_fn, max_steps=max_steps)
        self.max_replans = max_replans
        self.max_steps = max_steps
        self._replan_count = 0
        self._current_plan: Optional[Plan] = None

        self._stats = {
            "plans_created": 0,
            "replans": 0,
            "replans_exhausted": 0,
            "total_steps_planned": 0,
        }

    # ───────────────────────────────────────────────────────
    #  Создание плана
    # ───────────────────────────────────────────────────────

    def create_plan(
        self,
        goal: str,
        context: str = "",
        force_template: Optional[str] = None,
    ) -> Plan:
        """Создаёт план для цели.

        Args:
            goal: Цель пользователя.
            context: Дополнительный контекст.
            force_template: Принудительный шаблон (None = авто).

        Returns:
            Plan с шагами.
        """
        self._replan_count = 0

        if force_template:
            plan = self.planner.create_from_template(
                force_template, goal=goal,
            )
        else:
            plan = self.planner.create_plan(goal)

        self._current_plan = plan
        self._stats["plans_created"] += 1
        self._stats["total_steps_planned"] += plan.step_count

        logger.info(
            "Agent plan created: %s (%d steps)",
            goal[:50], plan.step_count,
        )

        return plan

    # ───────────────────────────────────────────────────────
    #  Перепланирование
    # ───────────────────────────────────────────────────────

    def replan(
        self,
        original_plan: Plan,
        failed_step_id: int,
        error_context: str = "",
    ) -> Optional[Plan]:
        """Перепланирует после неудачного шага.

        Args:
            original_plan: Исходный план.
            failed_step_id: ID провалившегося шага.
            error_context: Контекст ошибки.

        Returns:
            Новый Plan или None если лимит перепланирований.
        """
        self._replan_count += 1
        self._stats["replans"] += 1

        if self._replan_count > self.max_replans:
            self._stats["replans_exhausted"] += 1
            logger.warning(
                "Replan limit reached (%d/%d) for: %s",
                self._replan_count, self.max_replans,
                original_plan.goal[:50],
            )
            return None

        # Формируем обновлённую цель с контекстом ошибки
        replan_goal = (
            f"{original_plan.goal}\n"
            f"[REPLAN #{self._replan_count}] "
            f"Шаг {failed_step_id} провалился: {error_context[:200]}"
        )

        # Оставляем успешные шаги, перепланируем остальные
        completed_steps = [
            copy.copy(s) for s in original_plan.steps
            if s.id < failed_step_id
        ]

        new_plan = self.planner.create_plan(replan_goal)

        # Вставляем выполненные шаги в начало
        if completed_steps:
            for i, step in enumerate(completed_steps):
                step.id = i + 1
            offset = len(completed_steps)
            for step in new_plan.steps:
                old_id = step.id
                step.id = old_id + offset
                # Remap depends_on references to match renumbered IDs
                if hasattr(step, 'depends_on') and step.depends_on:
                    step.depends_on = [dep + offset for dep in step.depends_on]
            new_plan.steps = completed_steps + new_plan.steps

        new_plan.metadata["replan_count"] = self._replan_count
        new_plan.metadata["original_goal"] = original_plan.goal

        self._current_plan = new_plan
        self._stats["total_steps_planned"] += new_plan.step_count

        logger.info(
            "Replan #%d: %d steps (was %d)",
            self._replan_count, new_plan.step_count,
            original_plan.step_count,
        )

        return new_plan

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    @property
    def current_plan(self) -> Optional[Plan]:
        """Текущий активный план."""
        return self._current_plan

    @property
    def can_replan(self) -> bool:
        """Можно ли ещё перепланировать."""
        return self._replan_count < self.max_replans

    @property
    def replan_count(self) -> int:
        """Текущий счётчик перепланирований."""
        return self._replan_count

    def get_stats(self) -> Dict[str, Any]:
        """Статистика."""
        return dict(self._stats)

    def format_stats(self) -> str:
        """Форматированная статистика."""
        s = self._stats
        return (
            f"Agent Planner: "
            f"{s['plans_created']} plans, "
            f"{s['replans']} replans, "
            f"{s['total_steps_planned']} total steps"
        )
