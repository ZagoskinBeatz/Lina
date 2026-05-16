# -*- coding: utf-8 -*-
"""
Lina Core — Состояние рантайма (Runtime State).

Единое общее состояние для всех компонентов конвейера:
  - Текущий запрос
  - Контекст (RAG, runtime, history)
  - Безопасность (verdict, decision)
  - Планирование (активный план)
  - Метрики

Phase 9 — Controlled Autonomous Runtime.
"""

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List


class RequestPhase(str, Enum):
    """Фаза обработки запроса в конвейере."""
    RECEIVED = "received"          # Запрос получен
    INTENT_DETECTED = "intent"     # Намерение определено
    CONTEXT_BUILT = "context"      # Контекст собран
    BUDGET_CHECKED = "budget"      # Бюджет проверен
    MODEL_SELECTED = "model"       # Модель выбрана
    SAFETY_CHECKED = "safety"      # Безопасность проверена
    GENERATED = "generated"        # Ответ сгенерирован
    PLANNED = "planned"            # План создан (если нужно)
    EXECUTED = "executed"          # Выполнено
    COMPLETED = "completed"        # Завершено
    ERROR = "error"                # Ошибка в конвейере


class IntentType(str, Enum):
    """Тип намерения пользователя."""
    COMMAND = "command"            # Системная команда
    QUESTION = "question"          # Вопрос к LLM
    RAG_QUERY = "rag_query"        # Поиск по базе знаний
    MACRO = "macro"                # Макрос
    CHAIN = "chain"                # Цепочка команд
    META = "meta"                  # Мета-команда
    PLANNING = "planning"          # Многошаговая задача
    CV = "cv"                      # Компьютерное зрение


@dataclass
class RequestContext:
    """Контекст одного запроса (передаётся через конвейер).

    Attributes:
        request_id: Уникальный ID запроса.
        raw_input: Исходный ввод пользователя.
        intent: Определённое намерение.
        phase: Текущая фаза обработки.
        model_tier: Выбранная модель.
        rag_context: RAG-контекст.
        runtime_context: Runtime-информация.
        prompt: Собранный промпт.
        response: Ответ LLM.
        safety_verdict: Вердикт безопасности.
        plan_active: Активен ли план.
        metadata: Произвольные данные.
        created_at: Время создания.
        elapsed: Общее время обработки.
        errors: Список ошибок.
    """
    request_id: str = ""
    raw_input: str = ""
    intent: Optional[IntentType] = None
    phase: RequestPhase = RequestPhase.RECEIVED
    model_tier: str = "full"
    rag_context: str = ""
    runtime_context: str = ""
    prompt: str = ""
    response: str = ""
    safety_verdict: Optional[Dict[str, Any]] = None
    plan_active: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    elapsed: float = 0.0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "request_id": self.request_id,
            "raw_input": self.raw_input[:200],
            "intent": self.intent.value if self.intent else None,
            "phase": self.phase.value,
            "model_tier": self.model_tier,
            "rag_context_len": len(self.rag_context),
            "runtime_context_len": len(self.runtime_context),
            "prompt_len": len(self.prompt),
            "response_len": len(self.response),
            "safety": self.safety_verdict,
            "plan_active": self.plan_active,
            "elapsed": round(self.elapsed, 3),
            "errors": self.errors,
        }


class RuntimeState:
    """Общее состояние рантайма Lina.

    Хранит глобальную информацию о:
      - Текущем запросе (RequestContext)
      - Истории запросов
      - Активном плане
      - Конфигурации конвейера

    Attributes:
        current_request: Текущий контекст запроса.
        request_history: История последних запросов.
        active_plan_id: ID активного плана (если есть).
        pipeline_config: Конфигурация конвейера.
        _request_counter: Счётчик запросов.
    """

    def __init__(self, max_history: int = 100):
        """Инициализация состояния.

        Args:
            max_history: Максимальный размер истории.
        """
        self._lock = threading.Lock()
        self.current_request: Optional[RequestContext] = None
        self.request_history: List[Dict[str, Any]] = []
        self.active_plan_id: Optional[str] = None
        self._max_history = max_history
        self._request_counter: int = 0

        # Конфигурация конвейера
        self.pipeline_config: Dict[str, Any] = {
            "safety_enabled": True,
            "planning_enabled": True,
            "metrics_enabled": True,
            "rag_enabled": True,
            "cache_enabled": True,
        }

    # ───────────────────────────────────────────────────────
    #  Управление запросами
    # ───────────────────────────────────────────────────────

    def new_request(self, raw_input: str) -> RequestContext:
        """Создаёт новый контекст запроса.

        Args:
            raw_input: Ввод пользователя.

        Returns:
            Новый RequestContext.
        """
        with self._lock:
            self._request_counter += 1
            ctx = RequestContext(
                request_id=f"req_{self._request_counter:06d}",
                raw_input=raw_input,
            )
            self.current_request = ctx
        return ctx

    def complete_request(self) -> None:
        """Завершает текущий запрос и сохраняет в историю."""
        with self._lock:
            if self.current_request is None:
                return

            self.current_request.phase = RequestPhase.COMPLETED
            self.current_request.elapsed = (
                time.time() - self.current_request.created_at
            )

            # Сохраняем в историю
            self.request_history.append(
                self.current_request.to_dict()
            )

            # Ограничиваем размер истории
            if len(self.request_history) > self._max_history:
                self.request_history = (
                    self.request_history[-self._max_history:]
                )

            self.current_request = None

    # ───────────────────────────────────────────────────────
    #  Конфигурация
    # ───────────────────────────────────────────────────────

    def enable_feature(self, feature: str) -> None:
        """Включает фичу конвейера.

        Args:
            feature: Название фичи (safety, planning, metrics, rag, cache).
        """
        key = f"{feature}_enabled"
        if key in self.pipeline_config:
            self.pipeline_config[key] = True

    def disable_feature(self, feature: str) -> None:
        """Отключает фичу конвейера.

        Args:
            feature: Название фичи.
        """
        key = f"{feature}_enabled"
        if key in self.pipeline_config:
            self.pipeline_config[key] = False

    def is_enabled(self, feature: str) -> bool:
        """Проверяет, включена ли фича.

        Args:
            feature: Название фичи.

        Returns:
            True если фича включена.
        """
        key = f"{feature}_enabled"
        return self.pipeline_config.get(key, False)

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    @property
    def request_count(self) -> int:
        """Общее количество обработанных запросов."""
        return self._request_counter

    def get_recent_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Возвращает последние запросы.

        Args:
            limit: Максимальное количество.

        Returns:
            Список словарей с историей.
        """
        return self.request_history[-limit:]

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация состояния.

        Returns:
            Словарь с полным состоянием.
        """
        return {
            "request_count": self._request_counter,
            "current_request": (
                self.current_request.to_dict()
                if self.current_request else None
            ),
            "active_plan_id": self.active_plan_id,
            "pipeline_config": dict(self.pipeline_config),
            "history_size": len(self.request_history),
        }

    def format_status(self) -> str:
        """Форматированный статус.

        Returns:
            Строка со статусом рантайма.
        """
        lines = [
            "🔧 Runtime State:",
            f"   Запросов: {self._request_counter}",
            f"   Активный план: {self.active_plan_id or 'нет'}",
            f"   История: {len(self.request_history)} записей",
        ]

        enabled_features = [
            k.replace("_enabled", "")
            for k, v in self.pipeline_config.items()
            if v
        ]
        lines.append(f"   Features: {', '.join(enabled_features)}")

        return "\n".join(lines)
