"""
Lina Runtime — Model Manager.

Phase 20.1: Single Heavy Model.
Управление единственной full-моделью LLM.

Ответственности:
  - Отслеживание загруженной модели
  - Lazy load / auto unload
  - Ограничение потоков и контекста

Принцип: одна full-модель в памяти.
"""

import copy
import time
import logging
from typing import Optional, Literal
from dataclasses import dataclass

from lina.config import config, ModelProfile

logger = logging.getLogger("lina.runtime.model_manager")

# Phase 20.1: only "full" tier
ModelTier = Literal["full"]


@dataclass
class ModelState:
    """Состояние загруженной модели."""
    tier: ModelTier
    loaded_at: float
    last_used: float
    request_count: int = 0

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_used

    def touch(self) -> None:
        self.last_used = time.time()
        self.request_count += 1


# ── Параметры управления ───────────────────────────────────────────────────────

MAX_THREADS = 8          # Жёсткий лимит потоков CPU
MAX_CONTEXT = 4096       # Жёсткий лимит контекста
IDLE_UNLOAD = 300        # Выгрузить после 5 мин простоя


class ModelManager:
    """
    Менеджер модели — Phase 20.1 (Single Heavy Model).

    Всегда возвращает "full".
    Нет mini. Нет routing. Нет switching.

    Использование:
        manager = ModelManager()
        tier = manager.select_tier(query, context)  # always "full"
        manager.record_use(tier)
    """

    def __init__(self):
        self._state: Optional[ModelState] = None
        self._last_switch_time: float = 0.0

    @property
    def current_tier(self) -> Optional[ModelTier]:
        """Текущий tier загруженной модели."""
        return self._state.tier if self._state else None

    @property
    def current_state(self) -> Optional[ModelState]:
        """Текущее состояние модели."""
        return self._state

    def select_tier(
        self,
        query: str,
        context: str = "",
        forced: Optional[ModelTier] = None,
    ) -> str:
        """Phase 20.1: always returns 'full'."""
        return "full"

    def should_switch(self, target_tier: str = "full") -> bool:
        """Phase 20.1: switch only if not loaded yet."""
        if not self._state:
            return True
        return self._state.tier != target_tier

    def record_load(self, tier: ModelTier) -> None:
        """Записывает факт загрузки модели."""
        now = time.time()
        self._state = ModelState(
            tier=tier, loaded_at=now, last_used=now,
        )
        self._last_switch_time = now
        logger.info("Model loaded: %s", tier)

    def record_use(self, tier: ModelTier) -> None:
        """Записывает факт использования модели."""
        if self._state and self._state.tier == tier:
            self._state.touch()

    def record_unload(self) -> None:
        """Записывает факт выгрузки модели."""
        if self._state:
            logger.info(
                "Model unloaded: %s (was loaded %.0fs, %d requests)",
                self._state.tier,
                time.time() - self._state.loaded_at,
                self._state.request_count,
            )
        self._state = None

    def should_idle_unload(self) -> bool:
        """Проверяет, нужно ли выгрузить по idle."""
        if not self._state:
            return False
        return self._state.idle_seconds > IDLE_UNLOAD

    def get_profile(self, tier: str = "full") -> ModelProfile:
        """Возвращает профиль модели (всегда full)."""
        profile = copy.copy(config.llm.full)
        if profile.n_threads > MAX_THREADS:
            profile.n_threads = MAX_THREADS
        return profile

    def get_status(self) -> dict:
        """Статус менеджера для диагностики."""
        return {
            "current_tier": self.current_tier,
            "state": {
                "tier": self._state.tier if self._state else None,
                "idle_seconds": round(self._state.idle_seconds) if self._state else None,
                "request_count": self._state.request_count if self._state else 0,
            } if self._state else None,
            "idle_unload": IDLE_UNLOAD,
            "max_threads": MAX_THREADS,
        }
