# -*- coding: utf-8 -*-
"""
Lina Core — Consistency Engine (Phase 25).

Проверяет логическую и семантическую согласованность:

  - Соответствие ответа intent
  - Соответствие ответа execution_plan
  - Соответствие ответа предыдущему шагу (multi-step)
  - Отсутствие semantic drift

Возвращает:
  consistency_score  0–1
  drift_detected     bool
  reason             string

В trace добавляет:
  consistency_score, drift_flag, regeneration_reason

Consistency Engine — ТОЛЬКО проверяет.
НИКОГДА не выполняет и не модифицирует ответ.
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger("lina.core.consistency_engine")


# ═══════════════════════════════════════════════════════════
#  Consistency Result
# ═══════════════════════════════════════════════════════════

@dataclass
class ConsistencyResult:
    """Результат проверки согласованности."""
    consistency_score: float = 1.0     # 0.0 — полное несоответствие, 1.0 — идеально
    drift_detected: bool = False
    drift_type: str = ""
    reason: str = ""
    regeneration_reason: str = ""
    passed: bool = True
    checks_performed: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.consistency_score, 3),
            "drift": self.drift_detected,
            "drift_type": self.drift_type,
            "reason": self.reason,
            "regeneration_reason": self.regeneration_reason,
            "passed": self.passed,
            "checks": self.checks_performed,
        }

    def for_trace(self) -> Dict[str, Any]:
        """Компактный вид для записи в trace."""
        return {
            "consistency_score": round(self.consistency_score, 3),
            "drift_flag": self.drift_detected,
            "regeneration_reason": self.regeneration_reason,
        }


# ═══════════════════════════════════════════════════════════
#  Consistency Engine
# ═══════════════════════════════════════════════════════════

class ConsistencyEngine:
    """Движок когнитивной согласованности (Phase 25).

    Проверяет 4 аспекта:
      1. intent_match  — ответ соответствует intent
      2. plan_match    — ответ по правильному пути
      3. step_match    — ответ согласован с предыдущим шагом
      4. drift_check   — нет семантического дрейфа

    Каждая проверка даёт score 0-1, финальный score — среднее.

    Usage:
        ce = ConsistencyEngine()
        result = ce.check(
            intent="chat",
            actual_path="LLM",
            planned_path="LLM",
            response_text="Привет! Чем помогу?",
        )
        assert result.passed
        assert result.consistency_score > 0.8
    """

    # Пороги
    PASS_THRESHOLD = 0.5
    DRIFT_ENTITY_MIN_OVERLAP = 0.3

    # Intent → expected response patterns (keywords)
    _INTENT_MARKERS: Dict[str, List[str]] = {
        "system_command": [],       # system output, no markers needed
        "meta": [],                 # meta commands
        "chat": [],                 # free-form
        "math": ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
                 "+", "-", "=", "*", "/", "результат", "ответ"],
    }

    def __init__(self, *, pass_threshold: float = PASS_THRESHOLD):
        self.pass_threshold = pass_threshold
        self._stats_lock = threading.Lock()
        self._check_count: int = 0
        self._pass_count: int = 0
        self._drift_count: int = 0
        self._total_score: float = 0.0

    def check(
        self,
        *,
        intent: str = "",
        actual_path: str = "",
        planned_path: str = "",
        response_text: str = "",
        prev_entities: Optional[List[str]] = None,
        curr_entities: Optional[List[str]] = None,
        prev_strategy: str = "",
        curr_strategy: str = "",
        prev_fingerprint: str = "",
        curr_fingerprint: str = "",
    ) -> ConsistencyResult:
        """Полная проверка согласованности.

        Args:
            intent: Текущий intent.
            actual_path: Фактический execution path.
            planned_path: Запланированный path.
            response_text: Текст ответа.
            prev_entities: Сущности предыдущего шага.
            curr_entities: Сущности текущего шага.
            prev_strategy: Стратегия предыдущего шага.
            curr_strategy: Стратегия текущего шага.
            prev_fingerprint: Fingerprint предыдущего шага.
            curr_fingerprint: Fingerprint текущего шага.

        Returns:
            ConsistencyResult.
        """
        with self._stats_lock:
            self._check_count += 1

        scores: List[float] = []
        checks: List[str] = []
        drift_detected = False
        drift_type = ""
        reason_parts: List[str] = []

        # ─── 1. Intent match ────────────────────────
        if intent:
            intent_score = self._check_intent_match(intent, response_text)
            scores.append(intent_score)
            checks.append("intent_match")
            if intent_score < 0.5:
                reason_parts.append(
                    f"intent mismatch (score={intent_score:.2f})")

        # ─── 2. Path match ─────────────────────────
        if planned_path and actual_path:
            path_score = 1.0 if planned_path.upper() == actual_path.upper() else 0.0
            scores.append(path_score)
            checks.append("path_match")
            if path_score < 1.0:
                reason_parts.append(
                    f"path mismatch: planned={planned_path} actual={actual_path}")

        # ─── 3. Step consistency ────────────────────
        prev_ents = set(prev_entities or [])
        curr_ents = set(curr_entities or [])

        if prev_ents and curr_ents:
            overlap = len(prev_ents.intersection(curr_ents))
            union = len(prev_ents.union(curr_ents))
            entity_score = overlap / union if union > 0 else 1.0
            scores.append(entity_score)
            checks.append("entity_overlap")
            if entity_score < self.DRIFT_ENTITY_MIN_OVERLAP:
                drift_detected = True
                drift_type = "entity"
                reason_parts.append(
                    f"entity drift (overlap={entity_score:.2f})")

        # ─── 4. Strategy consistency ────────────────
        if prev_strategy and curr_strategy:
            strat_score = 1.0 if prev_strategy == curr_strategy else 0.3
            scores.append(strat_score)
            checks.append("strategy_match")
            if strat_score < 1.0:
                drift_detected = True
                drift_type = drift_type or "strategy"
                reason_parts.append(
                    f"strategy changed: {prev_strategy} → {curr_strategy}")

        # ─── 5. Fingerprint ────────────────────────
        if prev_fingerprint and curr_fingerprint:
            fp_score = 1.0 if prev_fingerprint == curr_fingerprint else 0.5
            scores.append(fp_score)
            checks.append("fingerprint")
            if fp_score < 1.0 and prev_ents and curr_ents:
                overlap_ratio = (
                    len(prev_ents.intersection(curr_ents)) / len(prev_ents)
                    if prev_ents else 1.0
                )
                if overlap_ratio < self.DRIFT_ENTITY_MIN_OVERLAP:
                    drift_detected = True
                    drift_type = drift_type or "contradiction"

        # ─── Aggregate ─────────────────────────────
        final_score = sum(scores) / len(scores) if scores else 1.0
        passed = final_score >= self.pass_threshold

        if passed:
            with self._stats_lock:
                self._pass_count += 1
        if drift_detected:
            with self._stats_lock:
                self._drift_count += 1

        with self._stats_lock:
            self._total_score += final_score

        regen_reason = ""
        if not passed:
            regen_reason = "; ".join(reason_parts) or "low consistency"

        result = ConsistencyResult(
            consistency_score=final_score,
            drift_detected=drift_detected,
            drift_type=drift_type,
            reason="; ".join(reason_parts) if reason_parts else "consistent",
            regeneration_reason=regen_reason,
            passed=passed,
            checks_performed=checks,
        )

        logger.debug(
            "CONSISTENCY: score=%.3f drift=%s checks=%s",
            final_score, drift_detected, checks,
        )
        return result

    def check_response_not_empty(self, response: str) -> float:
        """Проверяет что ответ не пустой.

        Returns:
            1.0 если OK, 0.0 если пусто.
        """
        return 1.0 if response and response.strip() else 0.0

    def _check_intent_match(self, intent: str, response: str) -> float:
        """Проверяет соответствие ответа intent-у.

        Базовая эвристика:
          - Пустой ответ → 0.0
          - math intent: ожидаем числа → проверяем наличие
          - Остальные: если ответ не пуст → 0.9

        Returns:
            Score 0-1.
        """
        if not response or not response.strip():
            return 0.0

        markers = self._INTENT_MARKERS.get(intent)
        if markers:
            found = sum(1 for m in markers if m in response)
            if found > 0:
                return min(1.0, 0.7 + found * 0.05)
            return 0.5  # markers expected but not found

        # No markers defined → non-empty response is OK
        return 0.9

    def average_score(self) -> float:
        """Средний consistency score за сессию."""
        with self._stats_lock:
            if self._check_count == 0:
                return 1.0
            return self._total_score / self._check_count

    def stability_rating(self) -> str:
        """Рейтинг стабильности.

        Returns:
            excellent | good | fair | poor
        """
        avg = self.average_score()
        if avg >= 0.9:
            return "excellent"
        if avg >= 0.7:
            return "good"
        if avg >= 0.5:
            return "fair"
        return "poor"

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для SystemControl."""
        with self._stats_lock:
            cc = self._check_count
            pc = self._pass_count
            dc = self._drift_count
            ts = self._total_score
        # Derived values computed outside the lock to avoid deadlock
        avg = ts / cc if cc > 0 else 1.0
        if avg >= 0.9:
            stability = "excellent"
        elif avg >= 0.7:
            stability = "good"
        elif avg >= 0.5:
            stability = "fair"
        else:
            stability = "poor"
        return {
            "checks": cc,
            "total_checks": cc,
            "passed": pc,
            "drifts": dc,
            "avg_score": round(avg, 3),
            "stability": stability,
            "pass_rate": (
                f"{pc / cc * 100:.1f}%"
                if cc > 0 else "N/A"
            ),
        }
