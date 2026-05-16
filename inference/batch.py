# -*- coding: utf-8 -*-
"""
Lina Inference — Группировка запросов (Batch Manager).

Группирует несколько запросов для оптимизации:
  1. Очередь запросов с приоритетами
  2. Дедупликация одинаковых запросов
  3. Пакетная обработка (если llama-cpp поддерживает)
  4. Метрики пакетов

Примечание: llama-cpp-python на данный момент не поддерживает
настоящий batch inference (continuous batching). Этот модуль
подготавливает инфраструктуру и реализует очередь + дедупликацию.

Phase 10 — AI Runtime v2.
"""

import logging
import time
import threading
from dataclasses import dataclass, field, replace as _dc_replace
from enum import IntEnum
from typing import Dict, Any, Optional, List, Callable
from collections import deque

logger = logging.getLogger("lina.inference.batch")


# ═══════════════════════════════════════════════════════════
#  Приоритет запроса
# ═══════════════════════════════════════════════════════════

class RequestPriority(IntEnum):
    """Приоритет запроса в очереди."""
    HIGH = 1       # Интерактивный (пользователь ждёт)
    NORMAL = 2     # Обычный
    LOW = 3        # Фоновый (предзагрузка, batch)
    BACKGROUND = 4 # Самый низкий


# ═══════════════════════════════════════════════════════════
#  Запрос в очереди
# ═══════════════════════════════════════════════════════════

@dataclass
class BatchRequest:
    """Один запрос в очереди.

    Attributes:
        request_id: Уникальный идентификатор.
        query: Текст запроса.
        context: Контекст (RAG/runtime).
        tier: Модель.
        priority: Приоритет.
        created_at: Время создания.
        response: Ответ (заполняется после обработки).
        processed: Обработан ли.
        deduplicated: Дедуплицирован (ответ из другого запроса).
    """
    request_id: str = ""
    query: str = ""
    context: str = ""
    tier: str = "full"
    priority: RequestPriority = RequestPriority.NORMAL
    created_at: float = field(default_factory=time.time)
    response: Optional[str] = None
    processed: bool = False
    deduplicated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "request_id": self.request_id,
            "query": self.query[:100],
            "tier": self.tier,
            "priority": self.priority.name,
            "processed": self.processed,
            "deduplicated": self.deduplicated,
            "wait_time": round(time.time() - self.created_at, 3)
            if not self.processed else 0.0,
        }


# ═══════════════════════════════════════════════════════════
#  Статистика
# ═══════════════════════════════════════════════════════════

@dataclass
class BatchStats:
    """Статистика пакетного менеджера.

    Attributes:
        total_submitted: Всего отправлено.
        total_processed: Обработано.
        total_deduplicated: Дедуплицировано.
        queue_size: Текущий размер очереди.
        avg_wait_time: Среднее время ожидания.
    """
    total_submitted: int = 0
    total_processed: int = 0
    total_deduplicated: int = 0
    queue_size: int = 0
    avg_wait_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "total_submitted": self.total_submitted,
            "total_processed": self.total_processed,
            "total_deduplicated": self.total_deduplicated,
            "queue_size": self.queue_size,
            "avg_wait_time_ms": round(self.avg_wait_time * 1000, 1),
        }


# ═══════════════════════════════════════════════════════════
#  BatchManager
# ═══════════════════════════════════════════════════════════

class BatchManager:
    """Менеджер пакетной обработки запросов.

    Предоставляет:
      - submit(query, context, tier, priority) → request_id
      - process_next(inference_fn) → BatchRequest
      - process_all(inference_fn) → List[BatchRequest]
      - stats → BatchStats

    Примечание: на данный момент llama-cpp не поддерживает
    true batch inference, поэтому запросы обрабатываются
    последовательно, но с дедупликацией и приоритетами.

    Attributes:
        max_queue_size: Максимальный размер очереди.
        enable_dedup: Включена ли дедупликация.
        _queue: Очередь запросов.
        _dedup_map: Карта дедупликации (query_hash → response).
        _lock: Мьютекс.
        _counter: Счётчик запросов.
        _stats: Статистика.
        _wait_times: Времена ожидания для avg.
    """

    def __init__(
        self,
        max_queue_size: int = 50,
        enable_dedup: bool = True,
    ):
        """Инициализация менеджера.

        Args:
            max_queue_size: Максимальный размер очереди.
            enable_dedup: Включить дедупликацию.
        """
        self.max_queue_size = max_queue_size
        self.enable_dedup = enable_dedup
        self._queue: deque = deque()
        self._dedup_map: Dict[str, str] = {}
        self._dedup_max_size: int = 500
        self._lock = threading.Lock()
        self._counter = 0
        self._stats = BatchStats()
        self._wait_times: List[float] = []

    # ───────────────────────────────────────────────────────
    #  Отправка
    # ───────────────────────────────────────────────────────

    def submit(
        self,
        query: str,
        context: str = "",
        tier: str = "full",
        priority: RequestPriority = RequestPriority.NORMAL,
    ) -> BatchRequest:
        """Добавляет запрос в очередь.

        Args:
            query: Текст запроса.
            context: Контекст.
            tier: Модель.
            priority: Приоритет.

        Returns:
            BatchRequest (может быть уже дедуплицирован).

        Raises:
            ValueError: Очередь переполнена.
        """
        with self._lock:
            # Проверяем размер очереди
            if len(self._queue) >= self.max_queue_size:
                raise ValueError(
                    f"Batch queue full ({self.max_queue_size})"
                )

            self._counter += 1
            self._stats.total_submitted += 1

            req = BatchRequest(
                request_id=f"batch_{self._counter:06d}",
                query=query,
                context=context,
                tier=tier,
                priority=priority,
            )

            # Дедупликация
            if self.enable_dedup:
                import hashlib
                dedup_key = hashlib.md5(
                    f"{query.strip().lower()}|{context[:100]}".encode()
                ).hexdigest()

                if dedup_key in self._dedup_map:
                    req.response = self._dedup_map[dedup_key]
                    req.processed = True
                    req.deduplicated = True
                    self._stats.total_deduplicated += 1
                    self._stats.total_processed += 1
                    return req

            # Вставляем по приоритету
            inserted = False
            for i, existing in enumerate(self._queue):
                if req.priority < existing.priority:
                    self._queue.insert(i, req)
                    inserted = True
                    break
            if not inserted:
                self._queue.append(req)

            self._stats.queue_size = len(self._queue)
            return req

    # ───────────────────────────────────────────────────────
    #  Обработка
    # ───────────────────────────────────────────────────────

    def process_next(
        self,
        inference_fn: Callable[[str, str, str], str],
    ) -> Optional[BatchRequest]:
        """Обрабатывает следующий запрос из очереди.

        Args:
            inference_fn: Функция inference (query, context, tier) → str.

        Returns:
            Обработанный BatchRequest или None если очередь пуста.
        """
        with self._lock:
            if not self._queue:
                return None
            req = self._queue.popleft()
            self._stats.queue_size = len(self._queue)

        # Inference (без лока — может быть долгим)
        try:
            response = inference_fn(req.query, req.context, req.tier)
            req.response = response
            req.processed = True

            # Обновляем dedup map
            if self.enable_dedup:
                import hashlib
                dedup_key = hashlib.md5(
                    f"{req.query.strip().lower()}|{req.context[:100]}".encode()
                ).hexdigest()
                with self._lock:
                    self._dedup_map[dedup_key] = response
                    # Ограничиваем рост dedup-кэша
                    if len(self._dedup_map) > self._dedup_max_size:
                        # Удаляем ~20% самых старых
                        to_drop = len(self._dedup_map) - self._dedup_max_size
                        for k in list(self._dedup_map)[:to_drop]:
                            del self._dedup_map[k]

        except Exception as e:
            req.response = f"⚠ Batch error: {e}"
            req.processed = True
            logger.error("Batch inference error: %s", e)

        # Статистика
        wait_time = time.time() - req.created_at
        with self._lock:
            self._stats.total_processed += 1
            self._wait_times.append(wait_time)
            if len(self._wait_times) > 1000:
                self._wait_times = self._wait_times[-500:]
            if self._wait_times:
                self._stats.avg_wait_time = (
                    sum(self._wait_times) / len(self._wait_times)
                )

        return req

    def process_all(
        self,
        inference_fn: Callable[[str, str, str], str],
    ) -> List[BatchRequest]:
        """Обрабатывает все запросы в очереди.

        Args:
            inference_fn: Функция inference (query, context, tier) → str.

        Returns:
            Список обработанных BatchRequest.
        """
        results = []
        while True:
            req = self.process_next(inference_fn)
            if req is None:
                break
            results.append(req)
        return results

    # ───────────────────────────────────────────────────────
    #  Очередь
    # ───────────────────────────────────────────────────────

    @property
    def queue_size(self) -> int:
        """Текущий размер очереди."""
        with self._lock:
            return len(self._queue)

    def clear(self) -> int:
        """Очищает очередь.

        Returns:
            Количество удалённых элементов.
        """
        with self._lock:
            count = len(self._queue)
            self._queue.clear()
            self._dedup_map.clear()
            self._stats.queue_size = 0
            return count

    # ───────────────────────────────────────────────────────
    #  Статистика
    # ───────────────────────────────────────────────────────

    @property
    def stats(self) -> BatchStats:
        """Текущая статистика (возвращает копию)."""
        with self._lock:
            self._stats.queue_size = len(self._queue)
            return _dc_replace(self._stats)

    def get_stats_dict(self) -> Dict[str, Any]:
        """Статистика в виде словаря."""
        return self.stats.to_dict()

    def format_stats(self) -> str:
        """Форматированная статистика для CLI."""
        s = self.stats.to_dict()
        lines = [
            f"Batch Manager:",
            f"  Queue: {s['queue_size']}",
            f"  Submitted: {s['total_submitted']}",
            f"  Processed: {s['total_processed']}",
            f"  Deduplicated: {s['total_deduplicated']}",
            f"  Avg wait: {s['avg_wait_time_ms']:.0f}ms",
        ]
        return "\n".join(lines)
