# -*- coding: utf-8 -*-
"""
Lina Planning — Состояние плана (State Machine).

Модели данных для многошаговых планов:
  - StepType    — тип шага (shell, macro, rag, cv, llm)
  - StepStatus  — статус выполнения шага
  - PlanStatus  — статус плана
  - PlanStep    — один шаг плана
  - StepResult  — результат выполнения шага
  - Plan        — полный план задачи
  - PlanState   — машина состояний для управления планом

Phase 9 — Controlled Autonomous Runtime.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


# ═══════════════════════════════════════════════════════════
#  Перечисления
# ═══════════════════════════════════════════════════════════

class StepType(str, Enum):
    """Тип шага плана."""
    SHELL = "shell"     # Системная команда
    MACRO = "macro"     # Макрос Lina
    RAG = "rag"         # Поиск по базе знаний
    CV = "cv"           # Компьютерное зрение
    LLM = "llm"         # Вопрос к LLM
    CUSTOM = "custom"   # Пользовательский тип


class StepStatus(str, Enum):
    """Статус выполнения шага."""
    PENDING = "pending"       # Ожидает выполнения
    RUNNING = "running"       # Выполняется
    COMPLETED = "completed"   # Выполнен успешно
    FAILED = "failed"         # Ошибка выполнения
    SKIPPED = "skipped"       # Пропущен


class PlanStatus(str, Enum):
    """Статус всего плана."""
    DRAFT = "draft"           # Черновик (создан, не запущен)
    RUNNING = "running"       # Выполняется
    COMPLETED = "completed"   # Завершён успешно
    FAILED = "failed"         # Провален
    REPLANNED = "replanned"   # Перепланирован
    ABORTED = "aborted"       # Отменён (max_steps / пользователь)


class EvalDecision(str, Enum):
    """Решение evaluator'а после шага."""
    CONTINUE = "continue"     # Продолжить к следующему шагу
    REPLAN = "replan"         # Перепланировать
    STOP = "stop"             # Остановить (цель достигнута)
    FAIL = "fail"             # План провален


# ═══════════════════════════════════════════════════════════
#  Шаг плана
# ═══════════════════════════════════════════════════════════

@dataclass
class PlanStep:
    """Один шаг плана задачи.

    Attributes:
        id: Уникальный номер шага (1-based).
        description: Описание действия.
        step_type: Тип шага (shell, macro, rag, cv, llm).
        expected_result: Ожидаемый результат (для evaluator'а).
        command: Конкретная команда (если step_type=shell).
        depends_on: ID шагов, от которых зависит этот шаг.
    """
    id: int
    description: str
    step_type: StepType = StepType.SHELL
    expected_result: str = ""
    command: str = ""
    depends_on: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "id": self.id,
            "description": self.description,
            "type": self.step_type.value,
            "expected_result": self.expected_result,
            "command": self.command,
            "depends_on": self.depends_on,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanStep":
        """Десериализация из словаря."""
        try:
            step_type = StepType(data.get("type", "shell"))
        except ValueError:
            step_type = StepType.LLM  # Safe fallback for invalid types
        return cls(
            id=data.get("id", 0),
            description=data.get("description", ""),
            step_type=step_type,
            expected_result=data.get("expected_result", ""),
            command=data.get("command", ""),
            depends_on=data.get("depends_on", []),
        )


# ═══════════════════════════════════════════════════════════
#  Результат шага
# ═══════════════════════════════════════════════════════════

@dataclass
class StepResult:
    """Результат выполнения одного шага.

    Attributes:
        step_id: ID шага.
        status: Статус выполнения.
        output: Выходные данные.
        error: Сообщение об ошибке (если failed).
        elapsed: Время выполнения (секунды).
        confidence: Уверенность evaluator'а (0.0-1.0).
        eval_decision: Решение evaluator'а.
    """
    step_id: int
    status: StepStatus
    output: str = ""
    error: str = ""
    elapsed: float = 0.0
    confidence: float = 1.0
    eval_decision: Optional[EvalDecision] = None

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "output": self.output[:500],  # Ограничиваем размер
            "error": self.error,
            "elapsed": round(self.elapsed, 3),
            "confidence": round(self.confidence, 2),
            "eval_decision": (
                self.eval_decision.value if self.eval_decision else None
            ),
        }


# ═══════════════════════════════════════════════════════════
#  План
# ═══════════════════════════════════════════════════════════

@dataclass
class Plan:
    """Полный план задачи.

    Attributes:
        goal: Цель плана (естественным языком).
        steps: Список шагов.
        max_steps: Максимальное количество шагов.
        created_at: Время создания.
        metadata: Дополнительные метаданные.
    """
    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    max_steps: int = 10
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация плана в JSON-совместимый формат."""
        return {
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "max_steps": self.max_steps,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plan":
        """Десериализация из словаря."""
        return cls(
            goal=data.get("goal", ""),
            steps=[
                PlanStep.from_dict(s) for s in data.get("steps", [])
            ],
            max_steps=data.get("max_steps", 10),
            created_at=data.get("created_at", time.time()),
            metadata=data.get("metadata", {}),
        )

    @property
    def step_count(self) -> int:
        """Количество шагов в плане."""
        return len(self.steps)


# ═══════════════════════════════════════════════════════════
#  Машина состояний плана
# ═══════════════════════════════════════════════════════════

class PlanState:
    """Управление состоянием выполнения плана.

    Отслеживает:
      - Текущий шаг
      - Историю результатов
      - Статус плана
      - Перепланирования
      - Confidence score

    Attributes:
        plan: Текущий план.
        status: Статус плана.
        current_step_index: Индекс текущего шага (0-based).
        results: Результаты выполнения шагов.
        replan_count: Количество перепланирований.
        total_elapsed: Общее время выполнения.
    """

    # Максимальное число перепланирований
    MAX_REPLANS = 3

    def __init__(self, plan: Plan):
        """Инициализация состояния плана.

        Args:
            plan: План для выполнения.
        """
        self.plan = plan
        self.status = PlanStatus.DRAFT
        self.current_step_index: int = 0
        self.results: List[StepResult] = []
        self.replan_count: int = 0
        self.total_elapsed: float = 0.0
        self._start_time: Optional[float] = None
        self._error_log: List[str] = []

    # ───────────────────────────────────────────────────────
    #  Управление состоянием
    # ───────────────────────────────────────────────────────

    def start(self) -> bool:
        """Запускает выполнение плана.

        Returns:
            True если план успешно запущен.
        """
        if self.status not in (PlanStatus.DRAFT, PlanStatus.REPLANNED):
            return False

        if not self.plan.steps:
            self.status = PlanStatus.FAILED
            self._error_log.append("План пуст — нет шагов")
            return False

        self.status = PlanStatus.RUNNING
        self._start_time = time.time()
        return True

    @property
    def current_step(self) -> Optional[PlanStep]:
        """Возвращает текущий шаг или None если план завершён."""
        if 0 <= self.current_step_index < len(self.plan.steps):
            return self.plan.steps[self.current_step_index]
        return None

    def record_result(self, result: StepResult) -> None:
        """Записывает результат выполнения шага.

        Args:
            result: Результат шага.
        """
        self.results.append(result)
        self.total_elapsed += result.elapsed

        if result.error:
            self._error_log.append(
                f"Шаг {result.step_id}: {result.error}"
            )

    def advance(self) -> bool:
        """Переход к следующему шагу.

        Returns:
            True если есть следующий шаг, False если план завершён.
        """
        if self.status != PlanStatus.RUNNING:
            return False

        self.current_step_index += 1

        # Проверяем лимит шагов
        if self.current_step_index >= len(self.plan.steps):
            self.status = PlanStatus.COMPLETED
            return False

        if self.current_step_index >= self.plan.max_steps:
            self.status = PlanStatus.ABORTED
            self._error_log.append(
                f"Превышен лимит шагов: {self.plan.max_steps}"
            )
            return False

        return True

    def replan(self, new_plan: Plan) -> bool:
        """Перепланирование — замена оставшихся шагов.

        Args:
            new_plan: Новый план (заменяет оставшиеся шаги).

        Returns:
            True если перепланирование успешно.
        """
        if self.replan_count >= self.MAX_REPLANS:
            self.status = PlanStatus.FAILED
            self._error_log.append(
                f"Исчерпан лимит перепланирований: {self.MAX_REPLANS}"
            )
            return False

        self.replan_count += 1

        # Сохраняем выполненные шаги, заменяем оставшиеся
        completed_steps = self.plan.steps[:self.current_step_index]
        new_steps = new_plan.steps

        # Перенумеровываем новые шаги
        start_id = len(completed_steps) + 1
        for i, step in enumerate(new_steps):
            step.id = start_id + i

        self.plan.steps = completed_steps + new_steps
        self.plan.goal = new_plan.goal or self.plan.goal
        self.status = PlanStatus.REPLANNED

        return True

    def fail(self, reason: str) -> None:
        """Помечает план как провалившийся.

        Args:
            reason: Причина провала.
        """
        self.status = PlanStatus.FAILED
        self._error_log.append(reason)

    def abort(self, reason: str = "Отменён пользователем") -> None:
        """Отменяет план.

        Args:
            reason: Причина отмены.
        """
        self.status = PlanStatus.ABORTED
        self._error_log.append(reason)

    # ───────────────────────────────────────────────────────
    #  Вычисляемые свойства
    # ───────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """План выполняется."""
        return self.status in (PlanStatus.RUNNING, PlanStatus.REPLANNED)

    @property
    def is_finished(self) -> bool:
        """План завершён (успех, ошибка или отмена)."""
        return self.status in (
            PlanStatus.COMPLETED,
            PlanStatus.FAILED,
            PlanStatus.ABORTED,
        )

    @property
    def progress(self) -> float:
        """Прогресс выполнения (0.0 — 1.0)."""
        if not self.plan.steps:
            return 0.0
        return min(
            self.current_step_index / len(self.plan.steps),
            1.0,
        )

    @property
    def avg_confidence(self) -> float:
        """Средний confidence по всем выполненным шагам."""
        if not self.results:
            return 0.0
        return sum(r.confidence for r in self.results) / len(self.results)

    @property
    def completed_steps(self) -> int:
        """Количество выполненных шагов."""
        return sum(
            1 for r in self.results
            if r.status == StepStatus.COMPLETED
        )

    @property
    def failed_steps(self) -> int:
        """Количество проваленных шагов."""
        return sum(
            1 for r in self.results
            if r.status == StepStatus.FAILED
        )

    # ───────────────────────────────────────────────────────
    #  Сериализация
    # ───────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация полного состояния плана.

        Returns:
            Словарь со всей информацией о плане и его выполнении.
        """
        return {
            "plan": self.plan.to_dict(),
            "status": self.status.value,
            "current_step_index": self.current_step_index,
            "results": [r.to_dict() for r in self.results],
            "replan_count": self.replan_count,
            "total_elapsed": round(self.total_elapsed, 3),
            "progress": round(self.progress, 2),
            "avg_confidence": round(self.avg_confidence, 2),
            "completed_steps": self.completed_steps,
            "failed_steps": self.failed_steps,
            "errors": self._error_log[-10:],
        }

    def format_status(self) -> str:
        """Форматированный статус плана.

        Returns:
            Многострочная строка со статусом.
        """
        lines = [
            f"📋 План: {self.plan.goal}",
            f"   Статус: {self.status.value}",
            f"   Прогресс: {self.progress:.0%} "
            f"({self.completed_steps}/{len(self.plan.steps)} шагов)",
            f"   Время: {self.total_elapsed:.1f}с",
            f"   Confidence: {self.avg_confidence:.2f}",
            f"   Перепланирований: {self.replan_count}/{self.MAX_REPLANS}",
        ]

        if self._error_log:
            lines.append(f"   Ошибки: {len(self._error_log)}")

        return "\n".join(lines)
