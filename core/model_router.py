# -*- coding: utf-8 -*-
"""
Lina Core — Маршрутизатор моделей (Model Router).

Phase 20.1 — Single Heavy Model.
Всегда возвращает "full". Routing логика убрана.
"""

import logging
import threading
from typing import Optional, Dict, Any, List, Literal

from lina.core.runtime_state import RequestContext, IntentType

logger = logging.getLogger("lina.core.model_router")

ModelTier = Literal["full"]


class ModelRouter:
    """Маршрутизатор выбора модели.

    Phase 20.1: всегда возвращает "full".

    Attributes:
        full_available: Доступна ли full модель.
        _stats: Статистика маршрутизации.
    """

    def __init__(
        self,
        full_keywords: Optional[List[str]] = None,
        full_available: bool = True,
        **kwargs,
    ):
        """Инициализация роутера.

        Args:
            full_keywords: Не используется (сохранён для обратной совместимости).
            full_available: Full модель доступна.
        """
        self.full_available = full_available

        self._stats_lock = threading.Lock()
        self._stats = {
            "total_routes": 0,
            "full_routes": 0,
            "mini_routes": 0,
            "fallbacks": 0,
        }

    # ───────────────────────────────────────────────────────
    #  Маршрутизация
    # ───────────────────────────────────────────────────────

    def route(
        self,
        ctx: RequestContext,
        force_tier: Optional[str] = None,
    ) -> str:
        """Returns model tier, respecting availability.

        Args:
            ctx: Контекст запроса.
            force_tier: Игнорируется.

        Returns:
            "full" if available, otherwise "mini" (degradation).
        """
        with self._stats_lock:
            self._stats["total_routes"] += 1
        if not self.full_available:
            with self._stats_lock:
                self._stats["mini_routes"] += 1
            logger.warning("MODEL_ROUTER: full model unavailable, degrading to mini")
            return "mini"
        with self._stats_lock:
            self._stats["full_routes"] += 1
        return "full"

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    def update_availability(
        self,
        full: Optional[bool] = None,
        **kwargs,
    ) -> None:
        """Обновляет доступность модели.

        Args:
            full: Доступность full модели.
        """
        if full is not None:
            self.full_available = full

    def get_stats(self) -> Dict[str, int]:
        """Возвращает статистику маршрутизации.

        Returns:
            Словарь со счётчиками.
        """
        with self._stats_lock:
            return dict(self._stats)

    def format_status(self) -> str:
        """Форматированный статус.

        Returns:
            Строка со статусом роутера.
        """
        s = self._stats
        return (
            f"🔀 Router: {s['total_routes']} routes "
            f"(full={s['full_routes']}, "
            f"mini={s['mini_routes']}, "
            f"fallbacks={s['fallbacks']})"
        )
