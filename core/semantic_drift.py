# -*- coding: utf-8 -*-
"""
Lina Core — Semantic Drift Detector (Phase 25).

Обнаруживает семантический дрейф между шагами:

  - Ответ противоречит предыдущему
  - Меняется основная сущность задачи
  - Меняется выбранная стратегия без причины

3 drift подряд → рекомендация strict mode.

SemanticDriftDetector — ТОЛЬКО обнаруживает.
Не переключает режимы. Возвращает результат.
"""

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Set

logger = logging.getLogger("lina.core.semantic_drift")


# ═══════════════════════════════════════════════════════════
#  Drift Result
# ═══════════════════════════════════════════════════════════

@dataclass
class DriftResult:
    """Результат проверки дрейфа."""
    drift_detected: bool = False
    drift_type: str = ""         # entity | strategy | contradiction | none
    reason: str = ""
    severity: str = "ok"         # ok | warning | critical
    recommend_regenerate: bool = False
    recommend_strict: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "drift": self.drift_detected,
            "type": self.drift_type,
            "reason": self.reason,
            "severity": self.severity,
            "recommend_regenerate": self.recommend_regenerate,
            "recommend_strict": self.recommend_strict,
        }


# ═══════════════════════════════════════════════════════════
#  Semantic Drift Detector
# ═══════════════════════════════════════════════════════════

class SemanticDriftDetector:
    """Обнаружение семантического дрейфа (Phase 25).

    Проверяет согласованность между шагами:
      - entity overlap (должны пересекаться)
      - strategy continuity (не менять без причины)
      - fingerprint stability

    3 drift подряд → recommend_strict = True.

    Usage:
        dd = SemanticDriftDetector()
        r = dd.check(
            prev_entities=["Python"],
            curr_entities=["Java"],      # entity drift!
            prev_strategy="direct",
            curr_strategy="direct",
            prev_fingerprint="abc",
            curr_fingerprint="def",
        )
        assert r.drift_detected
    """

    DRIFT_STREAK_THRESHOLD = 3

    def __init__(self):
        self._stats_lock = threading.Lock()
        self._check_count: int = 0
        self._drift_count: int = 0
        self._consecutive_drifts: int = 0
        self._history: deque = deque(maxlen=200)

    def check(
        self,
        *,
        prev_entities: Optional[List[str]] = None,
        curr_entities: Optional[List[str]] = None,
        prev_strategy: str = "",
        curr_strategy: str = "",
        prev_fingerprint: str = "",
        curr_fingerprint: str = "",
        prev_intent: str = "",
        curr_intent: str = "",
    ) -> DriftResult:
        """Проверяет дрейф между двумя шагами.

        Args:
            prev_entities: Сущности предыдущего шага.
            curr_entities: Сущности текущего шага.
            prev_strategy: Стратегия предыдущего шага.
            curr_strategy: Стратегия текущего шага.
            prev_fingerprint: Fingerprint предыдущего.
            curr_fingerprint: Fingerprint текущего.
            prev_intent: Intent предыдущего шага.
            curr_intent: Intent текущего шага.

        Returns:
            DriftResult.
        """
        with self._stats_lock:
            self._check_count += 1

        prev_ents = set(prev_entities or [])
        curr_ents = set(curr_entities or [])

        # 1. Entity drift — основные сущности полностью изменились
        if prev_ents and curr_ents and not prev_ents.intersection(curr_ents):
            result = DriftResult(
                drift_detected=True,
                drift_type="entity",
                reason=f"Entity set changed completely: "
                       f"{sorted(prev_ents)} → {sorted(curr_ents)}",
                severity="warning",
                recommend_regenerate=True,
            )
            self._record_drift(result)
            return result

        # 2. Strategy drift — стратегия сменилась без смены intent
        if (prev_strategy and curr_strategy
                and prev_strategy != curr_strategy
                and prev_intent == curr_intent):
            result = DriftResult(
                drift_detected=True,
                drift_type="strategy",
                reason=f"Strategy changed without intent change: "
                       f"{prev_strategy} → {curr_strategy}",
                severity="warning",
                recommend_regenerate=True,
            )
            self._record_drift(result)
            return result

        # 3. Fingerprint radical change (with same intent)
        if (prev_fingerprint and curr_fingerprint
                and prev_fingerprint != curr_fingerprint
                and prev_intent and prev_intent == curr_intent
                and prev_ents and curr_ents
                and len(prev_ents.intersection(curr_ents)) < len(prev_ents) * 0.3):
            result = DriftResult(
                drift_detected=True,
                drift_type="contradiction",
                reason="Semantic fingerprint diverged with same intent",
                severity="critical",
                recommend_regenerate=True,
            )
            self._record_drift(result)
            return result

        # No drift
        with self._stats_lock:
            self._consecutive_drifts = 0
        return DriftResult(
            drift_detected=False,
            drift_type="none",
            severity="ok",
        )

    def _record_drift(self, result: DriftResult) -> None:
        """Записывает drift и обновляет streak."""
        with self._stats_lock:
            self._drift_count += 1
            self._consecutive_drifts += 1
            self._history.append(result)
            consecutive = self._consecutive_drifts

        if consecutive >= self.DRIFT_STREAK_THRESHOLD:
            result.recommend_strict = True
            result.severity = "critical"
            logger.warning(
                "SEMANTIC_DRIFT: %d consecutive drifts → strict recommended",
                consecutive,
            )

        logger.debug(
            "SEMANTIC_DRIFT: %s — %s (consecutive=%d)",
            result.drift_type, result.reason, consecutive,
        )

    def reset_streak(self) -> None:
        """Сбрасывает streak при успешном выполнении."""
        self._consecutive_drifts = 0

    def get_consecutive_drifts(self) -> int:
        """Текущая серия дрейфов подряд."""
        return self._consecutive_drifts

    def get_history(self) -> list:
        """Возвращает копию истории drift-результатов."""
        return list(self._history)

    def clear(self) -> None:
        """Полный сброс."""
        self._history.clear()
        self._consecutive_drifts = 0
        self._drift_count = 0
        self._check_count = 0

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для отладки."""
        with self._stats_lock:
            return {
                "checks": self._check_count,
                "total_checks": self._check_count,
                "drifts_detected": self._drift_count,
                "consecutive_drifts": self._consecutive_drifts,
                "drift_rate": (
                    f"{self._drift_count / self._check_count * 100:.1f}%"
                    if self._check_count > 0 else "N/A"
                ),
                "history_size": len(self._history),
            }
