# -*- coding: utf-8 -*-
"""
Lina Agent — Оценщик агента (Agent Evaluator).

Обёртка над planning.Evaluator с confidence threshold:
  1. Rule-based + LLM-based оценка (наследуется)
  2. Confidence threshold — отказ при низкой уверенности
  3. Агрегированная оценка плана (не только шагов)
  4. Рекомендации для replan

Phase 10 — AI Runtime v2.
"""

import logging
from collections import deque
from typing import Optional, Dict, Any, Callable, List

from lina.planning.evaluator import Evaluator, EvalResult
from lina.planning.state import (
    Plan,
    PlanStep,
    StepResult,
    StepStatus,
    EvalDecision,
)

logger = logging.getLogger("lina.agent.evaluator")


# ═══════════════════════════════════════════════════════════
#  Конфигурация
# ═══════════════════════════════════════════════════════════

# Порог confidence для агента
AGENT_CONFIDENCE_THRESHOLD = 0.5

# Минимальный % успешных шагов для общего SUCCESS
MIN_SUCCESS_RATE = 0.6


# ═══════════════════════════════════════════════════════════
#  Агрегированная оценка
# ═══════════════════════════════════════════════════════════

class PlanEvaluation:
    """Агрегированная оценка всего плана.

    Attributes:
        success: Общий успех плана.
        success_rate: Процент успешных шагов.
        total_steps: Всего шагов.
        completed_steps: Успешных шагов.
        failed_steps: Провалённых шагов.
        avg_confidence: Средний confidence.
        recommendations: Рекомендации.
    """

    def __init__(self):
        self.success: bool = False
        self.success_rate: float = 0.0
        self.total_steps: int = 0
        self.completed_steps: int = 0
        self.failed_steps: int = 0
        self.avg_confidence: float = 0.0
        self.recommendations: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "success": self.success,
            "success_rate": round(self.success_rate, 2),
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "failed_steps": self.failed_steps,
            "avg_confidence": round(self.avg_confidence, 2),
            "recommendations": self.recommendations,
        }


# ═══════════════════════════════════════════════════════════
#  AgentEvaluator
# ═══════════════════════════════════════════════════════════

class AgentEvaluator:
    """Оценщик для агентного слоя.

    Расширяет planning.Evaluator:
      - Повышенный confidence threshold
      - Агрегированная оценка плана
      - Рекомендации для replan

    Attributes:
        evaluator: Базовый Evaluator.
        confidence_threshold: Порог confidence.
        min_success_rate: Минимальный % успеха.
        _step_evals: Оценки отдельных шагов.
        _stats: Статистика.
    """

    def __init__(
        self,
        llm_fn: Optional[Callable[[str], str]] = None,
        confidence_threshold: float = AGENT_CONFIDENCE_THRESHOLD,
        min_success_rate: float = MIN_SUCCESS_RATE,
    ):
        """Инициализация.

        Args:
            llm_fn: Функция LLM для оценки.
            confidence_threshold: Порог confidence.
            min_success_rate: Минимальный % успешных шагов.
        """
        self.evaluator = Evaluator(
            llm_fn=llm_fn,
            confidence_threshold=confidence_threshold,
        )
        self.confidence_threshold = confidence_threshold
        self.min_success_rate = min_success_rate
        self._step_evals: deque = deque(maxlen=200)

        self._stats = {
            "evaluations": 0,
            "continues": 0,
            "replans": 0,
            "stops": 0,
            "fails": 0,
            "plan_evaluations": 0,
        }

    # ───────────────────────────────────────────────────────
    #  Оценка шага
    # ───────────────────────────────────────────────────────

    def evaluate_step(
        self,
        step: PlanStep,
        result: StepResult,
        goal: str = "",
    ) -> EvalResult:
        """Оценивает результат шага с confidence threshold.

        Args:
            step: Шаг плана.
            result: Результат выполнения.
            goal: Цель плана.

        Returns:
            EvalResult с решением.
        """
        self._stats["evaluations"] += 1

        eval_result = self.evaluator.evaluate(
            step, result, goal=goal,
        )

        # Дополнительная проверка confidence
        if (eval_result.decision == EvalDecision.CONTINUE
                and eval_result.confidence < self.confidence_threshold):
            eval_result.decision = EvalDecision.REPLAN
            eval_result.reason += (
                f" [confidence {eval_result.confidence:.0%} < "
                f"threshold {self.confidence_threshold:.0%}]"
            )

        self._step_evals.append(eval_result)
        # Map decision to known stats key (avoid dynamic key creation)
        _DECISION_STAT = {
            "continue": "continues",
            "replan": "replans",
            "stop": "stops",
            "fail": "fails",
        }
        stat_key = _DECISION_STAT.get(eval_result.decision.value)
        if stat_key and stat_key in self._stats:
            self._stats[stat_key] += 1

        return eval_result

    # ───────────────────────────────────────────────────────
    #  Агрегированная оценка плана
    # ───────────────────────────────────────────────────────

    def evaluate_plan(
        self,
        results: List[StepResult],
    ) -> PlanEvaluation:
        """Оценивает весь план по результатам шагов.

        Args:
            results: Результаты всех шагов.

        Returns:
            PlanEvaluation с агрегированной оценкой.
        """
        self._stats["plan_evaluations"] += 1

        evaluation = PlanEvaluation()
        evaluation.total_steps = len(results)

        if not results:
            evaluation.recommendations.append("План не содержит шагов")
            return evaluation

        # Подсчёт
        confidences = []
        for r in results:
            if r.status == StepStatus.COMPLETED:
                evaluation.completed_steps += 1
            elif r.status == StepStatus.FAILED:
                evaluation.failed_steps += 1
            confidences.append(r.confidence)

        # Метрики
        evaluation.success_rate = (
            evaluation.completed_steps / evaluation.total_steps
            if evaluation.total_steps > 0 else 0.0
        )
        evaluation.avg_confidence = (
            sum(confidences) / len(confidences)
            if confidences else 0.0
        )

        # Решение об успехе
        evaluation.success = (
            evaluation.success_rate >= self.min_success_rate
            and evaluation.avg_confidence >= self.confidence_threshold
        )

        # Рекомендации
        if evaluation.failed_steps > 0:
            evaluation.recommendations.append(
                f"{evaluation.failed_steps} шагов провалилось"
            )
        if evaluation.avg_confidence < self.confidence_threshold:
            evaluation.recommendations.append(
                f"Низкий confidence: {evaluation.avg_confidence:.0%}"
            )
        if not evaluation.success:
            evaluation.recommendations.append(
                "Рекомендуется перепланирование"
            )

        return evaluation

    # ───────────────────────────────────────────────────────
    #  Сброс
    # ───────────────────────────────────────────────────────

    def reset(self) -> None:
        """Сброс оценок для нового плана."""
        self._step_evals.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Статистика."""
        return dict(self._stats)

    def format_stats(self) -> str:
        """Форматированная статистика."""
        s = self._stats
        return (
            f"Agent Evaluator: "
            f"{s['evaluations']} evals, "
            f"{s['plan_evaluations']} plan evals"
        )
