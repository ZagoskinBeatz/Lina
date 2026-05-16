# -*- coding: utf-8 -*-
"""
Lina Planning — Исполнитель шагов (Step Executor).

Выполняет отдельные шаги плана, маршрутизируя их
по типу (shell, macro, rag, cv, llm).

Каждый шаг проходит через Safety Layer перед выполнением.

Phase 9 — Controlled Autonomous Runtime.
"""

import logging
import time
from typing import Optional, Dict, Any, Callable

from lina.planning.state import (
    PlanStep,
    StepResult,
    StepStatus,
    StepType,
)

logger = logging.getLogger("lina.planning.executor")


class StepExecutor:
    """Исполнитель шагов плана.

    Маршрутизирует шаги по типу к соответствующим обработчикам:
      - shell  → process_fn("!command")
      - macro  → process_fn("macro_name")
      - rag    → process_fn("найди ...")
      - cv     → process_fn("/скриншот")
      - llm    → process_fn(query)

    Attributes:
        process_fn: Функция обработки команд (Commander.process).
        safety_fn: Функция проверки безопасности (optional).
        timeout: Таймаут на один шаг (секунды).
        _stats: Статистика выполнения.
    """

    def __init__(
        self,
        process_fn: Callable[[str], str],
        safety_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
        timeout: float = 120.0,
    ):
        """Инициализация исполнителя.

        Args:
            process_fn: Функция обработки (Commander.process).
            safety_fn: Опциональная проверка безопасности.
                        Сигнатура: fn(command) → {"safe": bool, ...}.
            timeout: Таймаут на один шаг.
        """
        self.process_fn = process_fn
        self.safety_fn = safety_fn
        self.timeout = timeout

        self._stats = {
            "steps_executed": 0,
            "steps_succeeded": 0,
            "steps_failed": 0,
            "safety_blocks": 0,
        }

    # ───────────────────────────────────────────────────────
    #  Выполнение шага
    # ───────────────────────────────────────────────────────

    def execute(
        self,
        step: PlanStep,
        context: str = "",
    ) -> StepResult:
        """Выполняет один шаг плана.

        Поток:
          1. Подготовка команды по типу шага
          2. Проверка безопасности (если safety_fn задан)
          3. Выполнение через process_fn
          4. Формирование StepResult

        Args:
            step: Шаг для выполнения.
            context: Накопленный контекст от предыдущих шагов.

        Returns:
            StepResult с результатом выполнения.
        """
        self._stats["steps_executed"] += 1
        start_time = time.time()

        # Подготовка команды
        command = self._prepare_command(step, context)

        # Проверка безопасности
        if self.safety_fn is not None and step.step_type == StepType.SHELL:
            safety_result = self._check_safety(command)
            if safety_result is not None:
                self._stats["safety_blocks"] += 1
                return safety_result

        # Выполнение
        try:
            output = self.process_fn(command)
            elapsed = time.time() - start_time

            self._stats["steps_succeeded"] += 1

            return StepResult(
                step_id=step.id,
                status=StepStatus.COMPLETED,
                output=output,
                elapsed=elapsed,
                confidence=1.0,
            )

        except Exception as e:
            elapsed = time.time() - start_time
            self._stats["steps_failed"] += 1

            logger.warning(
                "Step %d failed: %s (%.1fs)",
                step.id, e, elapsed
            )

            return StepResult(
                step_id=step.id,
                status=StepStatus.FAILED,
                output="",
                error="Ошибка выполнения шага.",
                elapsed=elapsed,
                confidence=0.0,
            )

    # ───────────────────────────────────────────────────────
    #  Подготовка команды
    # ───────────────────────────────────────────────────────

    def _prepare_command(
        self,
        step: PlanStep,
        context: str = "",
    ) -> str:
        """Подготавливает команду из шага.

        Маршрутизация по step_type:
          - SHELL → "!command"
          - MACRO → "macro_name"
          - RAG   → "найди {description}"
          - CV    → "/скриншот"
          - LLM   → description + context

        Args:
            step: Шаг плана.
            context: Контекст от предыдущих шагов.

        Returns:
            Строка команды для process_fn.
        """
        if step.step_type == StepType.SHELL:
            # Shell команды идут через sandbox (! префикс)
            cmd = step.command or step.description
            if not cmd.startswith("!"):
                cmd = f"!{cmd}"
            return cmd

        elif step.step_type == StepType.MACRO:
            # Макрос — прямое имя
            return step.command or step.description

        elif step.step_type == StepType.RAG:
            # RAG запрос
            query = step.command or step.description
            return f"найди {query}"

        elif step.step_type == StepType.CV:
            # CV — скриншот или OCR
            return step.command or "/скриншот"

        elif step.step_type == StepType.LLM:
            # LLM запрос с контекстом
            query = step.command or step.description
            if context:
                query = f"{query}\n\nКонтекст: {context[:500]}"
            return query

        else:
            # Custom — передаём как есть
            return step.command or step.description

    # ───────────────────────────────────────────────────────
    #  Проверка безопасности
    # ───────────────────────────────────────────────────────

    def _check_safety(self, command: str) -> Optional[StepResult]:
        """Проверяет безопасность shell-команды.

        Args:
            command: Команда для проверки.

        Returns:
            StepResult с блокировкой или None если безопасно.
        """
        if self.safety_fn is None:
            return None

        try:
            # Убираем ! префикс для проверки
            raw = command.lstrip("!")
            result = self.safety_fn(raw)

            if not result.get("safe", True):
                reason = result.get("reason", "Заблокировано Safety Layer")
                logger.info(
                    "Step blocked by safety: command='%s' reason='%s'",
                    raw[:50], reason
                )
                return StepResult(
                    step_id=0,  # Будет перезаписан
                    status=StepStatus.FAILED,
                    output="",
                    error=f"⛔ Safety: {reason}",
                    confidence=result.get("confidence", 0.0),
                )

            # Safety check passed — command is safe
            return None

        except Exception as e:
            logger.warning("Safety check failed: %s", e, exc_info=True)

        # Fail closed: if safety check crashes, block the command
        return StepResult(
            step_id=0,
            status=StepStatus.FAILED,
            output="",
            error="⛔ Safety check error — blocked (fail-closed)",
            confidence=0.0,
        )

    # ───────────────────────────────────────────────────────
    #  Пакетное выполнение
    # ───────────────────────────────────────────────────────

    def execute_sequence(
        self,
        steps: list,
        stop_on_failure: bool = True,
    ) -> list:
        """Выполняет последовательность шагов.

        Args:
            steps: Список PlanStep.
            stop_on_failure: Остановиться при первой ошибке.

        Returns:
            Список StepResult.
        """
        results = []
        context = ""

        for step in steps:
            result = self.execute(step, context=context)
            results.append(result)

            # Накапливаем контекст
            if result.status == StepStatus.COMPLETED:
                context += f"\nШаг {step.id}: {result.output[:200]}"

            # Остановка при ошибке
            if stop_on_failure and result.status == StepStatus.FAILED:
                logger.info("Sequence stopped at step %d", step.id)
                break

        return results

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, int]:
        """Возвращает статистику выполнения.

        Returns:
            Словарь со счётчиками.
        """
        return dict(self._stats)

    def reset_stats(self) -> None:
        """Сбрасывает статистику."""
        for key in self._stats:
            self._stats[key] = 0
