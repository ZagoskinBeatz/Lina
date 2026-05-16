# -*- coding: utf-8 -*-
"""
Lina Core — Legacy Pipeline (DEPRECATED).

.. deprecated:: 1.0.0
   Use :class:`lina.core.main_pipeline.MainPipeline` instead.
   This module is retained only for backward compatibility with tests.
   It will be removed in a future release.

Phase 9 — Controlled Autonomous Runtime (superseded by Phase 22-26).
"""

import logging
import time
import warnings
from typing import Optional, Dict, Any, Callable

from lina.core.runtime_state import (
    RuntimeState,
    RequestContext,
    RequestPhase,
    IntentType,
)
from lina.core.context import ContextBuilder
from lina.core.model_router import ModelRouter
from lina.metrics.profiler import RuntimeProfiler
from lina.safety.validator import SafetyValidator
from lina.safety.policy import PolicyEngine

logger = logging.getLogger("lina.core.pipeline")


# ═══════════════════════════════════════════════════════════
#  Результат конвейера
# ═══════════════════════════════════════════════════════════

class PipelineResult:
    """Результат обработки запроса конвейером.

    Attributes:
        response: Ответ для пользователя.
        context: Контекст запроса.
        safety_blocked: Была ли блокировка безопасностью.
        plan_created: Был ли создан план.
        from_cache: Ответ из кэша.
        model_tier: Использованная модель.
        elapsed: Время обработки.
        metadata: Дополнительные данные.
    """

    def __init__(
        self,
        response: str = "",
        context: Optional[RequestContext] = None,
        safety_blocked: bool = False,
        plan_created: bool = False,
        from_cache: bool = False,
        model_tier: str = "full",
        elapsed: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.response = response
        self.context = context
        self.safety_blocked = safety_blocked
        self.plan_created = plan_created
        self.from_cache = from_cache
        self.model_tier = model_tier
        self.elapsed = elapsed
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "response_length": len(self.response),
            "safety_blocked": self.safety_blocked,
            "plan_created": self.plan_created,
            "from_cache": self.from_cache,
            "model_tier": self.model_tier,
            "elapsed": round(self.elapsed, 3),
            "context": (
                self.context.to_dict() if self.context else None
            ),
        }


# ═══════════════════════════════════════════════════════════
#  CorePipeline
# ═══════════════════════════════════════════════════════════

class CorePipeline:
    """Единый конвейер обработки запросов.

    Координирует все компоненты Phase 9:
      - RuntimeState — состояние
      - ContextBuilder — сборка контекста
      - ModelRouter — выбор модели
      - SafetyValidator + PolicyEngine — безопасность
      - RuntimeProfiler — метрики

    Не заменяет Commander — предоставляет structured API
    для интеграции новых компонентов.

    Attributes:
        state: Общее состояние рантайма.
        context_builder: Сборщик контекста.
        model_router: Маршрутизатор моделей.
        safety_validator: Валидатор безопасности.
        policy_engine: Движок политик.
        profiler: Профайлер метрик.
        generate_fn: Функция генерации LLM.
        process_fn: Функция обработки команд.
    """

    def __init__(
        self,
        generate_fn: Optional[Callable] = None,
        process_fn: Optional[Callable[[str], str]] = None,
        rag_fn: Optional[Callable[[str], str]] = None,
        runtime_fn: Optional[Callable[[], str]] = None,
    ):
        """Инициализация конвейера.

        .. deprecated:: 1.0.0
           Use MainPipeline instead.
        """
        warnings.warn(
            "CorePipeline is deprecated since v1.0.0. "
            "Use lina.core.main_pipeline.MainPipeline instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        # Компоненты
        self.state = RuntimeState()
        self.context_builder = ContextBuilder(
            rag_fn=rag_fn,
            runtime_fn=runtime_fn,
        )
        self.model_router = ModelRouter()
        self.safety_validator = SafetyValidator()
        self.policy_engine = PolicyEngine()
        self.profiler = RuntimeProfiler()

        # Внешние функции
        self.generate_fn = generate_fn
        self.process_fn = process_fn

    # ───────────────────────────────────────────────────────
    #  Основной метод
    # ───────────────────────────────────────────────────────

    def process(
        self,
        raw_input: str,
        force_tier: Optional[str] = None,
    ) -> PipelineResult:
        """Обрабатывает запрос через полный конвейер.

        Поток:
          1. Создаём RequestContext
          2. Intent detection
          3. Context building
          4. Model routing
          5. Safety check (для команд)
          6. Generation / Execution
          7. Metrics update
          8. Complete

        Args:
            raw_input: Ввод пользователя.
            force_tier: Принудительная модель.

        Returns:
            PipelineResult с ответом и метаданными.
        """
        start_time = time.time()
        self.profiler.record_request()

        # 1. Создаём контекст
        ctx = self.state.new_request(raw_input)

        try:
            # 2. Intent detection
            ctx.phase = RequestPhase.INTENT_DETECTED
            ctx = self.context_builder.build(ctx)

            # 3. Model routing
            ctx.phase = RequestPhase.MODEL_SELECTED
            ctx.model_tier = self.model_router.route(ctx, force_tier)

            # 4. Safety check (для команд и shell)
            if ctx.intent == IntentType.COMMAND:
                ctx.phase = RequestPhase.SAFETY_CHECKED
                safety_result = self._check_safety(ctx)
                if safety_result is not None:
                    return safety_result

            # 5. Обработка по типу
            response = self._dispatch(ctx)

            # 6. Формируем результат
            elapsed = time.time() - start_time
            result = PipelineResult(
                response=response,
                context=ctx,
                model_tier=ctx.model_tier,
                elapsed=elapsed,
            )

            # 7. Метрики
            self.profiler.latency.record(
                "pipeline_total", elapsed
            )

        except Exception as e:
            elapsed = time.time() - start_time
            self.profiler.record_error()
            logger.exception("Pipeline error: %s", e)

            ctx.phase = RequestPhase.ERROR  # v0.8.0: prevent phase leak
            ctx.errors.append("internal pipeline error")
            result = PipelineResult(
                response="⚠ Произошла внутренняя ошибка. Попробуйте ещё раз.",
                context=ctx,
                elapsed=elapsed,
            )

        finally:
            self.state.complete_request()

        return result

    # ───────────────────────────────────────────────────────
    #  Safety check
    # ───────────────────────────────────────────────────────

    def _check_safety(
        self,
        ctx: RequestContext,
    ) -> Optional[PipelineResult]:
        """Проверяет безопасность для команд.

        Args:
            ctx: Контекст запроса.

        Returns:
            PipelineResult с блокировкой или None если безопасно.
        """
        if not self.state.is_enabled("safety"):
            return None

        # Извлекаем сырую команду (без ! префикса)
        command = ctx.raw_input.lstrip("!")

        with self.profiler.latency.measure("safety_check"):
            verdict = self.safety_validator.validate(command)

        ctx.safety_verdict = verdict.to_dict()

        if verdict.is_blocked:
            decision = self.policy_engine.evaluate(verdict, command)

            if not decision.allowed:
                self.profiler.record_safety_rejection()
                logger.info(
                    "BLOCKED by pipeline safety: %s",
                    command[:50]
                )
                return PipelineResult(
                    response=(
                        f"⛔ Заблокировано Safety Layer.\n"
                        f"   Риск: {verdict.risk_level}/5\n"
                        f"   Причина: {verdict.reason}\n"
                        f"   Confidence: {verdict.confidence:.0%}"
                    ),
                    context=ctx,
                    safety_blocked=True,
                    elapsed=time.time() - ctx.created_at,
                )

        return None

    # ───────────────────────────────────────────────────────
    #  Диспетчеризация
    # ───────────────────────────────────────────────────────

    def _dispatch(self, ctx: RequestContext) -> str:
        """Маршрутизирует запрос к обработчику.

        Args:
            ctx: Контекст запроса.

        Returns:
            Строка ответа.
        """
        # Для мета-команд, цепочек, макросов — через process_fn
        if ctx.intent in (
            IntentType.META,
            IntentType.COMMAND,
            IntentType.CHAIN,
            IntentType.MACRO,
            IntentType.CV,
        ):
            if self.process_fn is not None:
                with self.profiler.latency.measure("command_process"):
                    return self.process_fn(ctx.raw_input)
            return "⚠ process_fn не настроен"

        # Для LLM-запросов — через generate_fn
        if ctx.intent in (
            IntentType.QUESTION,
            IntentType.RAG_QUERY,
            IntentType.PLANNING,
        ):
            if self.generate_fn is not None:
                with self.profiler.latency.measure("llm_generate"):
                    return self.generate_fn(
                        ctx.raw_input,
                        ctx.rag_context,
                        ctx.model_tier,
                    )
            # Fallback к process_fn
            if self.process_fn is not None:
                return self.process_fn(ctx.raw_input)
            return "⚠ generate_fn не настроен"

        # Default
        logger.warning("Unknown intent %s, falling through to process_fn", ctx.intent)
        if self.process_fn is not None:
            return self.process_fn(ctx.raw_input)
        return "⚠ Обработчик не настроен"

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Полный статус конвейера.

        Returns:
            Словарь со статусом всех компонентов.
        """
        return {
            "state": self.state.to_dict(),
            "router": self.model_router.get_stats(),
            "safety": self.safety_validator.get_stats(),
            "policy": self.policy_engine.get_stats(),
            "profiler": self.profiler.get_report(),
        }

    def format_status(self) -> str:
        """Форматированный статус.

        Returns:
            Многострочная строка со статусом.
        """
        lines = [
            "═══ Core Pipeline ═══",
            self.state.format_status(),
            self.model_router.format_status(),
            self.policy_engine.format_status(),
            self.profiler.format_status(),
        ]
        return "\n".join(lines)

    def get_metrics_report(
        self,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Экспортирует метрики.

        Args:
            output_path: Путь для JSON (None → только dict).

        Returns:
            Полный отчёт метрик.
        """
        return self.profiler.export_json(output_path)
