# -*- coding: utf-8 -*-
"""
Lina Core — State Drift Detector (Phase 23).

Обнаруживает незапланированные изменения:
  - Изменение system prompt
  - Изменение версии модели
  - Тихий override конфига
  - Неожиданные intent-ы

Если обнаружено → warning + trace log.
DriftDetector ТОЛЬКО обнаруживает — НИКОГДА не исправляет.
"""

import hashlib
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Set

logger = logging.getLogger("lina.core.drift_detector")


# ═══════════════════════════════════════════════════════════
#  Drift Event
# ═══════════════════════════════════════════════════════════

@dataclass
class DriftEvent:
    """Обнаруженное отклонение."""
    category: str = ""        # prompt | model | config | intent
    description: str = ""
    old_value: str = ""
    new_value: str = ""
    severity: str = "warning"  # info | warning | critical

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "description": self.description,
            "severity": self.severity,
            "old": self.old_value,
            "new": self.new_value,
        }


# ═══════════════════════════════════════════════════════════
#  State Drift Detector
# ═══════════════════════════════════════════════════════════

class StateDriftDetector:
    """Детектор дрифта состояния (Phase 23).

    Запоминает baseline (initial state) и сравнивает при check().

    Usage:
        dd = StateDriftDetector()
        dd.set_baseline(
            system_prompt_hash=StateDriftDetector.hash_text(prompt),
            model_version="full.V1_Q_M",
            config_snapshot={"max_tokens": 512, ...},
            known_intents={"chat", "meta", "system_command", ...},
        )
        events = dd.check(
            current_prompt_hash=...,
            current_model=...,
            current_config=...,
            current_intents=...,
        )
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._baseline_prompt_hash: Optional[str] = None
        self._baseline_model: Optional[str] = None
        self._baseline_config: Dict[str, Any] = {}
        self._known_intents: Set[str] = set()
        self._events: deque = deque(maxlen=200)
        self._check_count: int = 0

    def set_baseline(
        self, *,
        system_prompt_hash: Optional[str] = None,
        model_version: Optional[str] = None,
        config_snapshot: Optional[Dict[str, Any]] = None,
        known_intents: Optional[Set[str]] = None,
    ) -> None:
        """Устанавливает baseline для сравнения."""
        if system_prompt_hash is not None:
            self._baseline_prompt_hash = system_prompt_hash
        if model_version is not None:
            self._baseline_model = model_version
        if config_snapshot is not None:
            self._baseline_config = dict(config_snapshot)
        if known_intents is not None:
            self._known_intents = set(known_intents)

        logger.debug("DRIFT: baseline set (prompt=%s model=%s config_keys=%d intents=%d)",
                      self._baseline_prompt_hash, self._baseline_model,
                      len(self._baseline_config), len(self._known_intents))

    def check(
        self, *,
        current_prompt_hash: Optional[str] = None,
        current_model: Optional[str] = None,
        current_config: Optional[Dict[str, Any]] = None,
        current_intents: Optional[Set[str]] = None,
    ) -> List[DriftEvent]:
        """Проверяет на дрифт.

        Returns:
            Список обнаруженных отклонений.
        """
        self._check_count += 1
        events: List[DriftEvent] = []

        # 1. System prompt drift
        if (current_prompt_hash is not None
                and self._baseline_prompt_hash is not None
                and current_prompt_hash != self._baseline_prompt_hash):
            e = DriftEvent(
                category="prompt",
                description="System prompt hash changed",
                old_value=self._baseline_prompt_hash,
                new_value=current_prompt_hash,
                severity="critical",
            )
            events.append(e)
            logger.warning("DRIFT: system prompt changed! old=%s new=%s",
                           self._baseline_prompt_hash, current_prompt_hash)

        # 2. Model version drift
        if (current_model is not None
                and self._baseline_model is not None
                and current_model != self._baseline_model):
            e = DriftEvent(
                category="model",
                description="Model version changed",
                old_value=self._baseline_model,
                new_value=current_model,
                severity="warning",
            )
            events.append(e)
            logger.warning("DRIFT: model changed! old=%s new=%s",
                           self._baseline_model, current_model)

        # 3. Config silent override
        if current_config is not None and self._baseline_config:
            for key, baseline_val in self._baseline_config.items():
                current_val = current_config.get(key)
                if current_val is not None and current_val != baseline_val:
                    e = DriftEvent(
                        category="config",
                        description=f"Config '{key}' silently changed",
                        old_value=str(baseline_val)[:200],
                        new_value=str(current_val)[:200],
                        severity="warning",
                    )
                    events.append(e)
                    logger.warning("DRIFT: config '%s' changed: %s → %s",
                                   key, str(baseline_val)[:100], str(current_val)[:100])

        # 4. Unexpected intents
        if current_intents is not None and self._known_intents:
            unknown = current_intents - self._known_intents
            if unknown:
                e = DriftEvent(
                    category="intent",
                    description=f"Unknown intents appeared: {unknown}",
                    old_value=str(self._known_intents),
                    new_value=str(current_intents),
                    severity="warning",
                )
                events.append(e)
                logger.warning("DRIFT: unknown intents: %s", unknown)

        self._events.extend(events)
        return events

    def get_events(self, limit: int = 10) -> List[DriftEvent]:
        """Последние N событий дрифта."""
        return self._events[-limit:]

    def format_events(self, limit: int = 5) -> str:
        """Форматированные события для /system drift."""
        events = self.get_events(limit)
        if not events:
            return "═══ DRIFT ═══\n  Отклонений не обнаружено ✓"
        lines = ["═══ DRIFT EVENTS ═══"]
        for e in events:
            lines.append(
                f"  [{e.severity}] {e.category}: {e.description}"
            )
        return "\n".join(lines)

    def clear(self) -> None:
        """Сброс истории событий."""
        self._events.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Статистика."""
        return {
            "checks_performed": self._check_count,
            "total_events": len(self._events),
            "has_baseline": self._baseline_prompt_hash is not None,
            "recent_events": [e.to_dict() for e in self._events[-3:]],
        }

    @staticmethod
    def hash_text(text: str) -> str:
        """Вычисляет hash текста для сравнения."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
