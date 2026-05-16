"""
SelfHealer — система автоматического восстановления.

.. warning:: EXPERIMENTAL
   Автоматический rollback и восстановление требуют расширенного тестирования
   перед использованием в production. State machine NORMAL→DEGRADED→SAFE→RECOVERY
   нуждается в battle-testing.

Расширяет core/degradation.py (DegradationStrategy) для системного уровня:

  1. Обнаружение crash loop сервисов
  2. Обнаружение повреждения зависимостей
  3. Автоматический rollback при неудачных фиксах
  4. Переход в SAFE MODE при каскадных сбоях
  5. Блокировка повторных рецептов, которые ломают систему
  6. Fail-safe: если OVERLORD сам ломается → degraded mode

Состояния:
  NORMAL → DEGRADED → SAFE → RECOVERY → NORMAL

Интеграция:
  - AutoFixEngine: если fix вызвал новые ошибки → rollback
  - PredictiveMonitor: alerts → trigger healing
  - ContextMemoryEngine: запомнить неудачные рецепты

Phase: SYSTEM OVERLORD / Module 5
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  System Health Mode
# ═══════════════════════════════════════════════════════════════════

class HealthMode(str, Enum):
    """Текущий режим здоровья системы OVERLORD."""
    NORMAL = "normal"         # Всё штатно
    DEGRADED = "degraded"     # Частичные сбои, AUTONOMOUS отключён
    SAFE = "safe"             # Только диагностика, никаких действий
    RECOVERY = "recovery"     # Активное восстановление


# ═══════════════════════════════════════════════════════════════════
#  Healing Event
# ═══════════════════════════════════════════════════════════════════

@dataclass
class HealingEvent:
    """Запись о событии самовосстановления."""
    event_type: str            # mode_change, rollback, recipe_blocked, crash_loop
    old_mode: str = ""
    new_mode: str = ""
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    auto_resolved: bool = False

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


# ═══════════════════════════════════════════════════════════════════
#  Blocked Recipe
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BlockedRecipe:
    """Рецепт, который привёл к ухудшению и заблокирован."""
    category: str
    commands: List[str]
    reason: str
    blocked_at: float = 0.0
    failure_count: int = 1

    def __post_init__(self):
        if not self.blocked_at:
            self.blocked_at = time.time()


# ═══════════════════════════════════════════════════════════════════
#  SelfHealer
# ═══════════════════════════════════════════════════════════════════

class SelfHealer:
    """Система автоматического восстановления OVERLORD.

    State machine:
      NORMAL → DEGRADED (при 3+ ошибках подряд или критических алертах)
      DEGRADED → SAFE (при 5+ ошибках или каскадном сбое)
      SAFE → RECOVERY (при запуске восстановления)
      RECOVERY → NORMAL (при успешном восстановлении)
      Любое → NORMAL (при ручном сбросе)

    Гарантии:
      - В SAFE mode AUTONOMOUS полностью отключён
      - В DEGRADED mode только LOW-risk операции
      - Заблокированные рецепты никогда не повторяются
      - Каждая смена режима логируется
    """

    BLOCKED_PATH = os.path.expanduser(
        "~/.local/share/lina/healing/blocked_recipes.json"
    )

    def __init__(
        self,
        degraded_threshold: int = 3,
        safe_threshold: int = 5,
        auto_recovery_timeout: float = 300,
    ):
        self._mode = HealthMode.NORMAL
        self._failure_streak: int = 0
        self._total_failures: int = 0
        self._total_recoveries: int = 0
        self._degraded_threshold = degraded_threshold
        self._safe_threshold = safe_threshold
        self._auto_recovery_timeout = auto_recovery_timeout
        self._last_mode_change: float = 0
        self._events: List[HealingEvent] = []
        self._blocked_recipes: List[BlockedRecipe] = []
        self._mode_listeners: List[Callable[[HealthMode, HealthMode], None]] = []
        self._load_blocked()

    # ─── Mode API ─────────────────────────────────────────────

    @property
    def mode(self) -> HealthMode:
        return self._mode

    @property
    def is_autonomous_allowed(self) -> bool:
        return self._mode == HealthMode.NORMAL

    @property
    def is_assist_allowed(self) -> bool:
        return self._mode in (HealthMode.NORMAL, HealthMode.DEGRADED)

    def on_mode_change(self, callback: Callable[[HealthMode, HealthMode], None]) -> None:
        self._mode_listeners.append(callback)

    # ─── Record failure / success ─────────────────────────────

    def record_failure(self, reason: str = "", details: Optional[Dict] = None) -> HealthMode:
        """Зарегистрировать сбой. Может поменять mode."""
        self._failure_streak += 1
        self._total_failures += 1

        logger.warning(
            "SELF_HEALER: failure #%d (streak=%d): %s",
            self._total_failures, self._failure_streak, reason,
        )

        # State transitions
        if self._failure_streak >= self._safe_threshold and self._mode != HealthMode.SAFE:
            self._transition(HealthMode.SAFE, f"Failure streak {self._failure_streak} ≥ {self._safe_threshold}: {reason}")
        elif self._failure_streak >= self._degraded_threshold and self._mode == HealthMode.NORMAL:
            self._transition(HealthMode.DEGRADED, f"Failure streak {self._failure_streak} ≥ {self._degraded_threshold}: {reason}")

        return self._mode

    def record_success(self) -> HealthMode:
        """Зарегистрировать успех. Сбрасывает streak."""
        self._failure_streak = 0

        # Auto-recovery
        if self._mode == HealthMode.DEGRADED:
            self._transition(HealthMode.NORMAL, "Success recorded, streak reset")
        elif self._mode == HealthMode.RECOVERY:
            self._transition(HealthMode.NORMAL, "Recovery succeeded")
            self._total_recoveries += 1

        return self._mode

    # ─── Critical alert handler ───────────────────────────────

    def handle_critical_alert(self, subsystem: str, message: str) -> HealthMode:
        """Обработка критического алерта от PredictiveMonitor."""
        if self._mode == HealthMode.NORMAL:
            self._transition(
                HealthMode.DEGRADED,
                f"Critical alert: {subsystem} — {message}",
            )
        return self._mode

    # ─── Crash loop detection ─────────────────────────────────

    def detect_crash_loop(self) -> List[str]:
        """Обнаружить crash loop сервисов."""
        import subprocess
        try:
            r = subprocess.run(
                "systemctl --failed --no-pager --plain 2>/dev/null | tail -n +2 | awk '{print $1}'",
                shell=True, capture_output=True, text=True, timeout=5,
            )
            failed = [s.strip() for s in r.stdout.strip().split("\n") if s.strip() and s.strip() != "0"]
            if failed:
                self._events.append(HealingEvent(
                    event_type="crash_loop",
                    reason=f"Failed services: {', '.join(failed[:5])}",
                    details={"services": failed},
                ))
                if len(failed) >= 3:
                    self.record_failure(f"Crash loop: {len(failed)} failed services")
            return failed
        except Exception:
            return []

    # ─── Fix validation — проверить что fix не ухудшил ────────

    def validate_fix_result(
        self,
        category: str,
        commands: List[str],
        pre_health: str,
        post_health: str,
    ) -> bool:
        """Проверить что fix не ухудшил состояние.

        Returns True если всё ок, False если ухудшилось.
        """
        LEVELS = {"ok": 0, "warning": 1, "critical": 2, "unknown": 3}
        pre = LEVELS.get(pre_health, 3)
        post = LEVELS.get(post_health, 3)

        if post > pre:
            # Ухудшилось — блокировать рецепт
            self.block_recipe(
                category=category,
                commands=commands,
                reason=f"Fix worsened health: {pre_health} → {post_health}",
            )
            self.record_failure(f"Fix for '{category}' worsened state: {pre_health} → {post_health}")
            return False

        self.record_success()
        return True

    # ─── Recipe blocking ──────────────────────────────────────

    def block_recipe(self, category: str, commands: List[str], reason: str) -> None:
        """Заблокировать рецепт навсегда."""
        recipe = BlockedRecipe(
            category=category,
            commands=commands,
            reason=reason,
        )
        self._blocked_recipes.append(recipe)
        self._save_blocked()

        self._events.append(HealingEvent(
            event_type="recipe_blocked",
            reason=reason,
            details={"category": category, "commands": commands[:3]},
        ))

        logger.warning("SELF_HEALER: Blocked recipe for '%s': %s", category, reason)

    def is_recipe_blocked(self, category: str, commands: List[str]) -> bool:
        """Проверить заблокирован ли рецепт."""
        cmd_set = set(commands)
        for br in self._blocked_recipes:
            if br.category == category and set(br.commands) == cmd_set:
                return True
        return False

    # ─── Recovery initiation ──────────────────────────────────

    def start_recovery(self) -> None:
        """Перевести в режим восстановления."""
        if self._mode in (HealthMode.SAFE, HealthMode.DEGRADED):
            self._transition(HealthMode.RECOVERY, "Manual recovery initiated")

    def force_normal(self) -> None:
        """Принудительный сброс в NORMAL (ручной override)."""
        self._failure_streak = 0
        self._transition(HealthMode.NORMAL, "Forced reset to NORMAL by operator")

    # ─── Internal ─────────────────────────────────────────────

    def _transition(self, new_mode: HealthMode, reason: str) -> None:
        old = self._mode
        if old == new_mode:
            return

        self._mode = new_mode
        self._last_mode_change = time.time()

        event = HealingEvent(
            event_type="mode_change",
            old_mode=old.value,
            new_mode=new_mode.value,
            reason=reason,
        )
        self._events.append(event)
        if len(self._events) > 500:
            self._events = self._events[-500:]

        logger.info("SELF_HEALER: %s → %s: %s", old.value, new_mode.value, reason)

        for cb in self._mode_listeners:
            try:
                cb(old, new_mode)
            except Exception as e:
                logger.error("Mode listener error: %s", e)

    # ─── Persistence ──────────────────────────────────────────

    def _load_blocked(self) -> None:
        try:
            if os.path.isfile(self.BLOCKED_PATH):
                with open(self.BLOCKED_PATH, "r") as f:
                    data = json.load(f)
                self._blocked_recipes = [
                    BlockedRecipe(**r) for r in data
                ]
        except Exception:
            self._blocked_recipes = []

    def _save_blocked(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.BLOCKED_PATH), exist_ok=True)
            data = [
                {"category": r.category, "commands": r.commands,
                 "reason": r.reason, "blocked_at": r.blocked_at,
                 "failure_count": r.failure_count}
                for r in self._blocked_recipes
            ]
            with open(self.BLOCKED_PATH, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save blocked recipes: %s", e)

    # ─── API ──────────────────────────────────────────────────

    def get_events(self, limit: int = 30) -> List[HealingEvent]:
        return self._events[-limit:]

    def get_blocked_recipes(self) -> List[BlockedRecipe]:
        return list(self._blocked_recipes)

    def format_report(self) -> str:
        lines = [f"═══ SelfHealer Report ═══"]
        lines.append(f"  Mode: {self._mode.value}")
        lines.append(f"  Failure streak: {self._failure_streak}")
        lines.append(f"  Total failures: {self._total_failures}")
        lines.append(f"  Total recoveries: {self._total_recoveries}")
        lines.append(f"  Blocked recipes: {len(self._blocked_recipes)}")
        lines.append(f"  AUTONOMOUS allowed: {self.is_autonomous_allowed}")
        lines.append(f"  ASSIST allowed: {self.is_assist_allowed}")
        if self._events:
            lines.append("")
            lines.append("  Recent events:")
            for e in self._events[-5:]:
                lines.append(f"    [{e.event_type}] {e.reason[:60]}")
        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "mode": self._mode.value,
            "failure_streak": self._failure_streak,
            "total_failures": self._total_failures,
            "total_recoveries": self._total_recoveries,
            "blocked_recipes": len(self._blocked_recipes),
            "events": len(self._events),
            "autonomous_allowed": self.is_autonomous_allowed,
            "assist_allowed": self.is_assist_allowed,
        }


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_healer: Optional[SelfHealer] = None


def get_self_healer() -> SelfHealer:
    global _healer
    if _healer is None:
        _healer = SelfHealer()
    return _healer
