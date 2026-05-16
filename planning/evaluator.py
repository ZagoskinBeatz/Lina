# -*- coding: utf-8 -*-
"""
Lina Planning — Оценщик результатов (Evaluator).

Анализирует результат каждого шага и принимает решение:
  - CONTINUE — шаг выполнен, продолжаем
  - REPLAN   — шаг не достиг цели, нужно перепланировать
  - STOP     — цель достигнута досрочно
  - FAIL     — критическая ошибка, прекращаем

Методы оценки:
  1. Rule-based — по статусу и ключевым словам
  2. LLM-based — через модель (опционально)

Phase 9 — Controlled Autonomous Runtime.
"""

import logging
from typing import Optional, Dict, Any, Callable, List

from lina.planning.state import (
    PlanStep,
    StepResult,
    StepStatus,
    EvalDecision,
)

logger = logging.getLogger("lina.planning.evaluator")


# ═══════════════════════════════════════════════════════════
#  Промпт для LLM-оценщика
# ═══════════════════════════════════════════════════════════

EVALUATOR_PROMPT = """Ты — модуль оценки плана AI-ассистента.
Оцени результат выполнения шага.

Цель плана: {goal}
Шаг {step_id}: {step_description}
Ожидаемый результат: {expected_result}
Фактический результат: {actual_result}

Решение (одно слово):
CONTINUE — шаг выполнен, продолжай
REPLAN — шаг не достиг цели, нужно перепланировать
STOP — цель полностью достигнута
FAIL — критическая ошибка

Ответь: DECISION:<решение> CONFIDENCE:<0.0-1.0> REASON:<причина>"""


# ═══════════════════════════════════════════════════════════
#  EvalResult
# ═══════════════════════════════════════════════════════════

class EvalResult:
    """Результат оценки шага.

    Attributes:
        decision: Решение (CONTINUE/REPLAN/STOP/FAIL).
        confidence: Уверенность в решении (0.0-1.0).
        reason: Причина решения.
        suggestions: Рекомендации для перепланирования.
    """

    def __init__(
        self,
        decision: EvalDecision,
        confidence: float = 1.0,
        reason: str = "",
        suggestions: Optional[List[str]] = None,
    ):
        self.decision = decision
        self.confidence = min(max(confidence, 0.0), 1.0)
        self.reason = reason
        self.suggestions = suggestions or []

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "decision": self.decision.value,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "suggestions": self.suggestions,
        }


# ═══════════════════════════════════════════════════════════
#  Evaluator
# ═══════════════════════════════════════════════════════════

class Evaluator:
    """Оценщик результатов шагов плана.

    Анализирует результат выполнения шага и принимает решение
    о дальнейших действиях: продолжить, перепланировать,
    остановить или завершить с ошибкой.

    Attributes:
        llm_fn: Функция LLM для оценки (optional).
        confidence_threshold: Минимальный порог уверенности.
        max_failures: Максимум ошибок подряд перед FAIL.
        _consecutive_failures: Счётчик ошибок подряд.
        _stats: Статистика.
    """

    def __init__(
        self,
        llm_fn: Optional[Callable[[str], str]] = None,
        confidence_threshold: float = 0.5,
        max_failures: int = 2,
    ):
        """Инициализация оценщика.

        Args:
            llm_fn: Функция LLM (query → response). None → rule-based only.
            confidence_threshold: Минимальный confidence для CONTINUE.
            max_failures: Максимум ошибок подряд.
        """
        self.llm_fn = llm_fn
        self.confidence_threshold = confidence_threshold
        self.max_failures = max_failures
        self._consecutive_failures = 0
        self._stats = {
            "evaluations": 0,
            "continues": 0,
            "replans": 0,
            "stops": 0,
            "fails": 0,
        }

    # ───────────────────────────────────────────────────────
    #  Главный метод оценки
    # ───────────────────────────────────────────────────────

    def evaluate(
        self,
        step: PlanStep,
        result: StepResult,
        goal: str = "",
        use_llm: bool = False,
    ) -> EvalResult:
        """Оценивает результат выполнения шага.

        Двухуровневая оценка:
          1. Rule-based (всегда)
          2. LLM-based (если use_llm=True и llm_fn задан)

        Args:
            step: Шаг плана.
            result: Результат выполнения.
            goal: Цель всего плана (для контекста).
            use_llm: Использовать LLM для оценки.

        Returns:
            EvalResult с решением.
        """
        self._stats["evaluations"] += 1

        # Шаг 1: Rule-based оценка
        rule_eval = self._evaluate_rule_based(step, result)

        # Шаг 2: LLM оценка (если нужна и доступна)
        if use_llm and self.llm_fn is not None:
            llm_eval = self._evaluate_with_llm(step, result, goal)
            if llm_eval is not None:
                # Комбинируем: приоритет у LLM при высокой confidence
                if llm_eval.confidence >= self.confidence_threshold:
                    rule_eval = llm_eval

        # Обновляем счётчик ошибок
        if rule_eval.decision == EvalDecision.FAIL:
            self._consecutive_failures += 1
        elif rule_eval.decision == EvalDecision.CONTINUE:
            self._consecutive_failures = 0

        # Проверяем лимит ошибок подряд
        if self._consecutive_failures >= self.max_failures:
            rule_eval = EvalResult(
                decision=EvalDecision.FAIL,
                confidence=0.9,
                reason=f"Превышен лимит ошибок подряд: "
                       f"{self._consecutive_failures}",
            )

        # Обновляем статистику
        self._update_stats(rule_eval.decision)

        logger.debug(
            "Eval step %d: %s (confidence=%.2f) — %s",
            step.id, rule_eval.decision.value,
            rule_eval.confidence, rule_eval.reason
        )

        return rule_eval

    # ───────────────────────────────────────────────────────
    #  Rule-based оценка
    # ───────────────────────────────────────────────────────

    def _evaluate_rule_based(
        self,
        step: PlanStep,
        result: StepResult,
    ) -> EvalResult:
        """Оценка на основе правил.

        Правила:
          1. Status == FAILED → REPLAN (если есть error) / FAIL
          2. Status == SKIPPED → CONTINUE (пропускаем)
          3. Output пустой → REPLAN с низкой confidence
          4. Ожидаемый результат совпадает → CONTINUE с высокой confidence
          5. Иначе → CONTINUE с средней confidence

        Args:
            step: Шаг плана.
            result: Результат.

        Returns:
            EvalResult.
        """
        # Провал шага
        if result.status == StepStatus.FAILED:
            if result.error:
                logger.warning("Step failed: %s", result.error)
                return EvalResult(
                    decision=EvalDecision.REPLAN,
                    confidence=0.7,
                    reason="Шаг провален — см. логи для деталей",
                    suggestions=[
                        "Попробовать альтернативную команду",
                        "Проверить зависимости",
                    ],
                )
            return EvalResult(
                decision=EvalDecision.FAIL,
                confidence=0.8,
                reason="Шаг провален без деталей",
            )

        # Пропущенный шаг
        if result.status == StepStatus.SKIPPED:
            return EvalResult(
                decision=EvalDecision.CONTINUE,
                confidence=0.6,
                reason="Шаг пропущен",
            )

        # Пустой вывод
        if not result.output or not result.output.strip():
            return EvalResult(
                decision=EvalDecision.REPLAN,
                confidence=0.4,
                reason="Пустой вывод — шаг мог не выполниться",
                suggestions=["Проверить команду", "Уточнить параметры"],
            )

        # Проверяем ожидаемый результат
        if step.expected_result:
            match_score = self._check_expected(
                result.output, step.expected_result
            )

            if match_score >= 0.5:
                return EvalResult(
                    decision=EvalDecision.CONTINUE,
                    confidence=min(0.6 + match_score * 0.4, 1.0),
                    reason=f"Ожидаемый результат совпадает "
                           f"({match_score:.0%})",
                )
            else:
                return EvalResult(
                    decision=EvalDecision.REPLAN,
                    confidence=0.5,
                    reason=f"Результат не соответствует ожиданию "
                           f"({match_score:.0%})",
                    suggestions=["Пересмотреть подход"],
                )

        # По умолчанию — продолжаем
        return EvalResult(
            decision=EvalDecision.CONTINUE,
            confidence=0.7,
            reason="Шаг выполнен (нет expected_result для проверки)",
        )

    def _check_expected(self, output: str, expected: str) -> float:
        """Проверяет совпадение вывода с ожиданием.

        Простая проверка по ключевым словам.

        Args:
            output: Фактический вывод.
            expected: Ожидаемый результат.

        Returns:
            Score от 0.0 до 1.0.
        """
        output_lower = output.lower()
        expected_words = expected.lower().split()

        if not expected_words:
            return 1.0

        matched = sum(1 for w in expected_words if w in output_lower)
        return matched / len(expected_words)

    # ───────────────────────────────────────────────────────
    #  LLM-оценка
    # ───────────────────────────────────────────────────────

    def _evaluate_with_llm(
        self,
        step: PlanStep,
        result: StepResult,
        goal: str,
    ) -> Optional[EvalResult]:
        """Оценка через LLM.

        Args:
            step: Шаг плана.
            result: Результат выполнения.
            goal: Цель плана.

        Returns:
            EvalResult или None при ошибке.
        """
        if self.llm_fn is None:
            return None

        prompt = EVALUATOR_PROMPT.format(
            goal=goal,
            step_id=step.id,
            step_description=step.description,
            expected_result=step.expected_result or "Не указан",
            actual_result=result.output[:500],
        )

        try:
            response = self.llm_fn(prompt)
            return self._parse_llm_eval(response)
        except Exception as e:
            logger.warning("LLM evaluation failed: %s", e)
            return None

    def _parse_llm_eval(self, response: str) -> Optional[EvalResult]:
        """Парсит ответ LLM-оценщика.

        Ожидаемый формат:
            DECISION:<решение> CONFIDENCE:<число> REASON:<причина>

        Args:
            response: Ответ LLM.

        Returns:
            EvalResult или None.
        """
        import re

        # Парсим решение
        decision_match = re.search(
            r"DECISION:\s*(CONTINUE|REPLAN|STOP|FAIL)",
            response, re.IGNORECASE
        )
        if not decision_match:
            return None

        decision_map = {
            "CONTINUE": EvalDecision.CONTINUE,
            "REPLAN": EvalDecision.REPLAN,
            "STOP": EvalDecision.STOP,
            "FAIL": EvalDecision.FAIL,
        }
        decision = decision_map.get(
            decision_match.group(1).upper(),
            EvalDecision.CONTINUE,
        )

        # Парсим confidence
        confidence = 0.7
        conf_match = re.search(r"CONFIDENCE:\s*([\d.]+)", response)
        if conf_match:
            try:
                confidence = float(conf_match.group(1))
            except ValueError:
                pass

        # Парсим причину
        reason = "LLM оценка"
        reason_match = re.search(r"REASON:\s*(.+)", response)
        if reason_match:
            reason = reason_match.group(1).strip()

        return EvalResult(
            decision=decision,
            confidence=confidence,
            reason=reason,
        )

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    def _update_stats(self, decision: EvalDecision) -> None:
        """Обновляет статистику по решениям."""
        stat_map = {
            EvalDecision.CONTINUE: "continues",
            EvalDecision.REPLAN: "replans",
            EvalDecision.STOP: "stops",
            EvalDecision.FAIL: "fails",
        }
        key = stat_map.get(decision, "continues")
        self._stats[key] += 1

    def get_stats(self) -> Dict[str, int]:
        """Возвращает статистику оценок.

        Returns:
            Словарь со счётчиками.
        """
        return dict(self._stats)

    def reset(self) -> None:
        """Сбрасывает состояние и статистику."""
        self._consecutive_failures = 0
        for key in self._stats:
            self._stats[key] = 0
