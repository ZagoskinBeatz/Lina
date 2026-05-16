# -*- coding: utf-8 -*-
"""
Lina Core — Lifecycle Manager (Phase 26).

Управление жизненным циклом запроса:

  INIT → ROUTE → PLAN → LOCK → EXECUTE → VALIDATE →
  CONSISTENCY → GUARD → TRACE → COMPLETE

Каждый этап:
  - Имеет чёткий вход/выход
  - Логирует время
  - Может быть пропущен (skipped) по условию
  - Ошибки изолированы

LifecycleManager — ТОЛЬКО управляет порядком.
НЕ содержит бизнес-логику. НЕ хранит состояние.
"""

import time
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional, Callable

logger = logging.getLogger("lina.core.lifecycle")


# ═══════════════════════════════════════════════════════════
#  Pipeline Stages
# ═══════════════════════════════════════════════════════════

class PipelineStage(str, Enum):
    """Этапы конвейера обработки запросов."""
    INIT = "init"
    ROUTE = "route"
    PLAN = "plan"
    LOCK = "lock"
    EXECUTE = "execute"
    VALIDATE = "validate"
    CONSISTENCY = "consistency"
    GUARD = "guard"
    TRACE = "trace"
    COMPLETE = "complete"


# Порядок прохождения
STAGE_ORDER: List[PipelineStage] = [
    PipelineStage.INIT,
    PipelineStage.ROUTE,
    PipelineStage.PLAN,
    PipelineStage.LOCK,
    PipelineStage.EXECUTE,
    PipelineStage.VALIDATE,
    PipelineStage.CONSISTENCY,
    PipelineStage.GUARD,
    PipelineStage.TRACE,
    PipelineStage.COMPLETE,
]


# ═══════════════════════════════════════════════════════════
#  Stage Result
# ═══════════════════════════════════════════════════════════

@dataclass
class StageResult:
    """Результат выполнения одного этапа."""
    stage: str = ""
    status: str = "ok"            # ok | skipped | error | blocked
    duration_ms: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "stage": self.stage,
            "status": self.status,
            "ms": round(self.duration_ms, 1),
        }
        if self.error:
            d["error"] = self.error
        return d


# ═══════════════════════════════════════════════════════════
#  Stage Handler type
# ═══════════════════════════════════════════════════════════

# Handler: (context_dict) → StageResult
StageHandler = Callable[[Dict[str, Any]], StageResult]


# ═══════════════════════════════════════════════════════════
#  Lifecycle Manager
# ═══════════════════════════════════════════════════════════

class LifecycleManager:
    """Управление жизненным циклом запроса (Phase 26).

    Проводит запрос через 10 этапов в строгом порядке.
    Каждый этап — зарегистрированный handler.

    Правила:
      - Этапы выполняются строго последовательно
      - Ошибка на этапе НЕ прерывает пайплайн (логируется)
      - Blocked на GUARD → COMPLETE с blocked=True
      - Каждый handler получает shared context dict

    Usage:
        lm = LifecycleManager()
        lm.register("route", my_route_handler)
        lm.register("plan", my_plan_handler)
        results = lm.run({"user_input": "привет"})
    """

    def __init__(self):
        self._handlers: Dict[str, StageHandler] = {}
        self._run_count: int = 0
        self._error_count: int = 0
        self._total_duration: float = 0.0
        self._stats_lock = threading.Lock()

    def register(self, stage: str, handler: StageHandler) -> None:
        """Регистрирует handler для этапа.

        Args:
            stage: Имя этапа (из PipelineStage).
            handler: Callable(context) → StageResult.
        """
        self._handlers[stage] = handler
        logger.debug("LIFECYCLE: registered handler for '%s'", stage)

    def run(self, context: Dict[str, Any]) -> List[StageResult]:
        """Прогоняет запрос через все этапы.

        Args:
            context: Shared dict, каждый handler может читать/писать.

        Returns:
            Список StageResult для каждого этапа.
        """
        with self._stats_lock:
            self._run_count += 1
        results: List[StageResult] = []
        run_start = time.time()

        for stage in STAGE_ORDER:
            handler = self._handlers.get(stage.value)

            if handler is None:
                results.append(StageResult(
                    stage=stage.value, status="skipped",
                ))
                continue

            t0 = time.time()
            try:
                result = handler(context)
                result.stage = stage.value
                result.duration_ms = (time.time() - t0) * 1000
            except Exception as e:
                with self._stats_lock:
                    self._error_count += 1
                result = StageResult(
                    stage=stage.value,
                    status="error",
                    duration_ms=(time.time() - t0) * 1000,
                    error=f"stage error: {type(e).__name__}: {str(e)[:200]}",
                )
                logger.error(
                    "LIFECYCLE: stage '%s' error: %s", stage.value, e,
                    exc_info=True,
                )

            results.append(result)

            # If blocked at GUARD → skip remaining stages (only log)
            if result.status == "blocked":
                context["blocked"] = True
                context["blocked_reason"] = result.error or "guard blocked"
                logger.warning(
                    "LIFECYCLE: blocked at stage '%s' — skipping remaining stages",
                    stage.value,
                )
                break

        total_ms = (time.time() - run_start) * 1000
        with self._stats_lock:
            self._total_duration += total_ms

        context["lifecycle_results"] = [r.to_dict() for r in results]
        context["lifecycle_duration_ms"] = round(total_ms, 1)

        logger.debug("LIFECYCLE: run complete — %.1fms", total_ms)
        return results

    def get_registered_stages(self) -> List[str]:
        """Список зарегистрированных этапов."""
        return list(self._handlers.keys())

    def get_stage_order(self) -> List[str]:
        """Полный порядок этапов."""
        return [s.value for s in STAGE_ORDER]

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для SystemControl."""
        with self._stats_lock:
            return {
                "runs": self._run_count,
                "errors": self._error_count,
                "registered_stages": len(self._handlers),
                "total_stages": len(STAGE_ORDER),
                "avg_duration_ms": (
                    round(self._total_duration / self._run_count, 1)
                    if self._run_count > 0 else 0
                ),
                "stages": self.get_registered_stages(),
            }
