# -*- coding: utf-8 -*-
"""
Lina Agent — Агентный исполнитель (Agent Executor).

Обёртка над planning.StepExecutor с усиленной безопасностью:
  1. Все команды — через sandbox (SubprocessSandbox)
  2. Safety gate — проверка перед каждым шагом
  3. Confidence threshold — отказ при низкой уверенности
  4. Полное логирование каждого действия

Phase 10 — AI Runtime v2.
"""

import logging
import time
from collections import deque
from typing import Optional, Dict, Any, Callable, List

from lina.planning.executor import StepExecutor
from lina.planning.state import (
    Plan,
    PlanStep,
    StepResult,
    StepStatus,
    StepType,
    EvalDecision,
)

logger = logging.getLogger("lina.agent.executor")


# ═══════════════════════════════════════════════════════════
#  Конфигурация
# ═══════════════════════════════════════════════════════════

# Минимальный confidence для выполнения шага
MIN_CONFIDENCE = 0.3

# Максимальное время на один шаг
MAX_STEP_TIMEOUT = 120.0


# ═══════════════════════════════════════════════════════════
#  Лог выполнения
# ═══════════════════════════════════════════════════════════

class ExecutionLog:
    """Журнал выполнения плана.

    Хранит все результаты шагов с метаданными.

    Attributes:
        entries: Записи лога.
    """

    def __init__(self, maxlen: int = 500):
        self.entries: deque = deque(maxlen=maxlen)

    def add(
        self,
        step: PlanStep,
        result: StepResult,
        safety_checked: bool = True,
    ) -> None:
        """Добавляет запись в лог.

        Args:
            step: Шаг плана.
            result: Результат выполнения.
            safety_checked: Прошёл ли safety check.
        """
        self.entries.append({
            "step_id": step.id,
            "description": step.description,
            "type": step.step_type.value,
            "command": step.command,
            "status": result.status.value,
            "output": result.output[:500] if result.output else "",
            "error": result.error,
            "elapsed": round(result.elapsed, 3),
            "confidence": round(result.confidence, 2),
            "safety_checked": safety_checked,
            "timestamp": time.time(),
        })

    def to_list(self) -> List[Dict[str, Any]]:
        """Возвращает все записи."""
        return list(self.entries)

    @property
    def size(self) -> int:
        """Количество записей."""
        return len(self.entries)

    def get_accumulated_context(self) -> str:
        """Собирает контекст из всех успешных шагов.

        Returns:
            Объединённый вывод всех выполненных шагов.
        """
        parts = []
        for entry in self.entries:
            if entry["status"] == StepStatus.COMPLETED.value and entry["output"]:
                parts.append(
                    f"Шаг {entry['step_id']}: {entry['output'][:200]}"
                )
        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════
#  AgentExecutor
# ═══════════════════════════════════════════════════════════

class AgentExecutor:
    """Агентный исполнитель с safety enforcement.

    Расширяет StepExecutor:
      - Safety gate перед каждым шагом
      - Confidence threshold
      - Полный execution log
      - Накопление контекста между шагами

    Attributes:
        executor: Базовый StepExecutor.
        min_confidence: Минимальный confidence.
        log: Журнал выполнения.
        _stats: Статистика.
    """

    def __init__(
        self,
        process_fn: Callable[[str], str],
        safety_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
        min_confidence: float = MIN_CONFIDENCE,
        timeout: float = MAX_STEP_TIMEOUT,
    ):
        """Инициализация.

        Args:
            process_fn: Функция обработки (Commander.process).
            safety_fn: Функция проверки безопасности.
            min_confidence: Минимальный confidence для выполнения.
            timeout: Таймаут на шаг.
        """
        self.executor = StepExecutor(
            process_fn=process_fn,
            safety_fn=safety_fn,
            timeout=timeout,
        )
        self.min_confidence = min_confidence
        self.log = ExecutionLog()

        self._stats = {
            "steps_executed": 0,
            "steps_succeeded": 0,
            "steps_failed": 0,
            "steps_blocked": 0,
            "safety_rejections": 0,
        }

    # ───────────────────────────────────────────────────────
    #  Выполнение шага
    # ───────────────────────────────────────────────────────

    def execute_step(
        self,
        step: PlanStep,
        confidence: float = 1.0,
    ) -> StepResult:
        """Выполняет шаг с safety enforcement.

        Поток:
          1. Проверяем confidence >= min_confidence
          2. Выполняем через StepExecutor
          3. Записываем в лог
          4. Обновляем статистику

        Args:
            step: Шаг для выполнения.
            confidence: Уверенность перед выполнением.

        Returns:
            StepResult с результатом.
        """
        self._stats["steps_executed"] += 1

        # 1. Confidence gate
        if confidence < self.min_confidence:
            result = StepResult(
                step_id=step.id,
                status=StepStatus.SKIPPED,
                error=(
                    f"Confidence {confidence:.0%} < "
                    f"threshold {self.min_confidence:.0%}"
                ),
                confidence=confidence,
                eval_decision=EvalDecision.FAIL,
            )
            self._stats["steps_blocked"] += 1
            self.log.add(step, result, safety_checked=False)

            logger.warning(
                "Step %d blocked: low confidence %.2f",
                step.id, confidence,
            )
            return result

        # 2. Выполняем через base executor
        context = self.log.get_accumulated_context()
        result = self.executor.execute(step, context)

        # 3. Статистика
        if result.status == StepStatus.COMPLETED:
            self._stats["steps_succeeded"] += 1
        elif result.status == StepStatus.FAILED:
            self._stats["steps_failed"] += 1

        # 4. Лог
        self.log.add(step, result, safety_checked=True)

        logger.info(
            "Step %d: %s (%.1fs) — %s",
            step.id, result.status.value,
            result.elapsed, step.description[:50],
        )

        return result

    # ───────────────────────────────────────────────────────
    #  Выполнение всего плана
    # ───────────────────────────────────────────────────────

    def execute_plan(
        self,
        plan: Plan,
        eval_fn: Optional[Callable] = None,
    ) -> List[StepResult]:
        """Выполняет все шаги плана последовательно.

        Args:
            plan: План для выполнения.
            eval_fn: Опциональная функция оценки (step, result) → EvalDecision.

        Returns:
            Список StepResult для всех шагов.
        """
        results = []

        for step in plan.steps:
            result = self.execute_step(step)
            results.append(result)

            # Если есть evaluator — проверяем решение
            if eval_fn is not None and result.status == StepStatus.COMPLETED:
                decision = eval_fn(step, result)
                if decision == EvalDecision.STOP:
                    logger.info("Plan stopped early at step %d", step.id)
                    break
                elif decision == EvalDecision.FAIL:
                    logger.warning("Plan failed at step %d", step.id)
                    break
                elif decision == EvalDecision.REPLAN:
                    logger.info("Replan requested at step %d", step.id)
                    result.eval_decision = EvalDecision.REPLAN
                    break

            # Если шаг провалился — останавливаемся
            if result.status == StepStatus.FAILED:
                break

        return results

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    def reset_log(self) -> None:
        """Сбрасывает лог выполнения."""
        self.log = ExecutionLog()

    def get_stats(self) -> Dict[str, Any]:
        """Статистика."""
        return dict(self._stats)

    def format_stats(self) -> str:
        """Форматированная статистика."""
        s = self._stats
        return (
            f"Agent Executor: "
            f"{s['steps_executed']} executed, "
            f"{s['steps_succeeded']} ok, "
            f"{s['steps_failed']} fail, "
            f"{s['steps_blocked']} blocked"
        )
