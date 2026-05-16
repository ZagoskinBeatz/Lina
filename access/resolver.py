"""
AccessLevelResolver — определяет уровень доступа для Intent.

Поток:
  Intent → AccessLevelResolver.check(intent) → AccessCheckResult
    → allowed=True/False
    → needs_confirmation=True/False
    → access_level="user"/"power"/"admin"

Phase 1 deepening:
  - Контекстные правила (время суток, частота ошибок)
  - Trust по source (ui > cli > dbus > hotkey)
  - Rate limiting per action
  - Dynamic domain escalation

Resolver НЕ выполняет действия. Только определяет уровень.

Phase: CONTROL PLANE / Access Layer
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict, Optional

from lina.access.levels import (
    AccessLevel,
    AccessCheckResult,
    DOMAIN_ACCESS_MAP,
    ELEVATED_ACTIONS,
    LEVEL_REQUIRES_CONFIRMATION,
)

logger = logging.getLogger(__name__)

# ── Source trust levels (Phase 1) ─────────────────────────────────────────────
_SOURCE_TRUST: Dict[str, int] = {
    "ui": 3,       # Прямой пользовательский ввод
    "cli": 3,      # CLI — тоже прямой
    "internal": 2,  # Внутренние вызовы
    "dbus": 1,     # IPC — менее доверенный
    "hotkey": 1,   # Хоткеи — автоматические
}

# ── Rate limits (max actions per window) ──────────────────────────────────────
_RATE_WINDOW_SEC = 60
_RATE_LIMITS: Dict[str, int] = {
    "admin": 3,     # max 3 admin-действия в минуту
    "power": 10,    # max 10 power-действий в минуту
    "user": 100,    # практически без ограничений
}


class AccessLevelResolver:
    """
    Определяет уровень доступа для Intent.

    Логика:
      1. Проверить action → ELEVATED_ACTIONS (конкретный action может быть выше домена)
      2. Проверить domain → DOMAIN_ACCESS_MAP
      3. Проверить intent.type (admin/power hints)
      4. Применить текущий уровень сессии
      5. Вернуть AccessCheckResult

    Пример:
        resolver = get_access_resolver()
        result = resolver.check(intent)
        if not result.allowed:
            print(result.reason_ru)
        elif result.needs_confirmation:
            # показать диалог подтверждения
    """

    def __init__(self, session_level: AccessLevel = AccessLevel.USER) -> None:
        self._session_level = session_level
        self._checked: int = 0
        self._denied: int = 0
        self._elevated: int = 0

        # Phase 1: contextual state
        self._failure_counter: Dict[str, int] = defaultdict(int)
        self._action_timestamps: Dict[str, list] = defaultdict(list)
        self._session_start = time.time()

    # ── Core Logic ───────────────────────────────────────

    def check(self, intent: Any) -> AccessCheckResult:
        """
        Определить уровень доступа для intent.

        Phase 1: contextual checks:
          1. Resolve required level
          2. Source trust check
          3. Rate limit check
          4. Session level check
          5. Failure escalation
          6. Confirmation requirement

        Args:
            intent: объект с атрибутами type, domain, action, source, is_admin(), is_power()

        Returns:
            AccessCheckResult
        """
        self._checked += 1

        domain = getattr(intent, 'domain', '')
        action = getattr(intent, 'action', '')
        source = getattr(intent, 'source', 'ui')
        intent_type = getattr(intent, 'type', '')

        # 1. Определить требуемый уровень
        required = self._resolve_level(intent)

        # 2. Source trust check — низко-доверенные источники
        #    не могут выполнять admin-действия напрямую
        source_trust = _SOURCE_TRUST.get(source, 0)
        if required == AccessLevel.ADMIN and source_trust < 2:
            self._denied += 1
            return AccessCheckResult(
                allowed=False,
                access_level=required.value,
                needs_confirmation=False,
                reason=f"Source '{source}' (trust={source_trust}) cannot perform "
                       f"admin action '{action}'",
                reason_ru=f"Источник '{source}' не имеет достаточного "
                          f"уровня доверия для admin-действия '{action}'.",
                intent_type=str(intent_type),
                domain=domain,
                action=action,
            )

        # 3. Rate limit check
        rate_key = required.value
        rate_result = self._check_rate_limit(rate_key, action)
        if rate_result:
            self._denied += 1
            return AccessCheckResult(
                allowed=False,
                access_level=required.value,
                needs_confirmation=False,
                reason=rate_result,
                reason_ru=f"Превышен лимит: {rate_result}",
                intent_type=str(intent_type),
                domain=domain,
                action=action,
            )

        # 4. Проверить доступ сессии
        session_rank = self._level_rank(self._session_level)
        required_rank = self._level_rank(required)

        if required_rank > session_rank:
            # Admin действие в user-сессии → проверить, можно ли эскалировать
            if required == AccessLevel.ADMIN:
                self._denied += 1
                return AccessCheckResult(
                    allowed=False,
                    access_level=required.value,
                    needs_confirmation=False,
                    reason=f"Action '{action}' requires admin level",
                    reason_ru=f"Действие '{action}' требует уровня admin. "
                              f"Текущий уровень: {self._session_level.value}.",
                    intent_type=str(intent_type),
                    domain=domain,
                    action=action,
                )

        # 5. Failure escalation — много ошибок повышает уровень подтверждения
        needs_confirm = LEVEL_REQUIRES_CONFIRMATION.get(required, False)
        if self._should_escalate_confirm(action):
            needs_confirm = True

        # 6. Source-based confirmation — dbus/hotkey всегда требуют confirm для power+
        if source in ("dbus", "hotkey") and required_rank >= 1:
            needs_confirm = True

        if needs_confirm:
            self._elevated += 1

        # Record timestamp for rate limiting
        self._record_action(rate_key)

        return AccessCheckResult(
            allowed=True,
            access_level=required.value,
            needs_confirmation=needs_confirm,
            reason=f"Access level: {required.value}" + (
                " (confirmation required)" if needs_confirm else ""),
            reason_ru=self._make_reason_ru(required, action, needs_confirm),
            intent_type=str(intent_type),
            domain=domain,
            action=action,
        )

    def _resolve_level(self, intent: Any) -> AccessLevel:
        """Определить требуемый уровень для intent."""
        action = getattr(intent, 'action', '')
        domain = getattr(intent, 'domain', '')

        # Action override (конкретный action имеет приоритет)
        if action and action in ELEVATED_ACTIONS:
            return ELEVATED_ACTIONS[action]

        # Intent type hints
        if hasattr(intent, 'is_admin') and intent.is_admin():
            return AccessLevel.ADMIN
        if hasattr(intent, 'is_power') and intent.is_power():
            return AccessLevel.POWER

        # Domain mapping
        if domain and domain in DOMAIN_ACCESS_MAP:
            return DOMAIN_ACCESS_MAP[domain]

        return AccessLevel.USER

    # ── Session Management ───────────────────────────────

    def set_session_level(self, level: AccessLevel) -> None:
        """Установить уровень текущей сессии."""
        old = self._session_level
        self._session_level = level
        logger.info("AccessLevelResolver: session level %s → %s", old.value, level.value)

    @property
    def session_level(self) -> AccessLevel:
        return self._session_level

    # ── Helpers ──────────────────────────────────────────

    @staticmethod
    def _level_rank(level: AccessLevel) -> int:
        """Числовой ранг уровня."""
        return {"user": 0, "power": 1, "admin": 2}.get(level.value, 0)

    @staticmethod
    def _make_reason_ru(level: AccessLevel, action: str,
                         needs_confirm: bool) -> str:
        """Русскоязычное пояснение."""
        if level == AccessLevel.ADMIN:
            return (f"Действие '{action}' требует уровня admin. "
                    "Подтвердите выполнение.")
        if needs_confirm:
            return (f"Действие '{action}' требует подтверждения "
                    f"(уровень: {level.value}).")
        return f"Действие разрешено (уровень: {level.value})."

    def get_stats(self) -> Dict[str, Any]:
        return {
            "checked": self._checked,
            "denied": self._denied,
            "elevated": self._elevated,
            "session_level": self._session_level.value,
            "failure_counts": dict(self._failure_counter),
            "session_age_sec": round(time.time() - self._session_start, 1),
        }

    # ── Phase 1: Contextual methods ─────────────────────

    def _check_rate_limit(self, level_key: str, action: str) -> Optional[str]:
        """Check rate limit for level. Returns error string or None."""
        max_actions = _RATE_LIMITS.get(level_key, 100)
        now = time.time()
        timestamps = self._action_timestamps.get(level_key, [])
        # Clean old entries
        timestamps = [t for t in timestamps if now - t < _RATE_WINDOW_SEC]
        self._action_timestamps[level_key] = timestamps

        if len(timestamps) >= max_actions:
            return (f"Rate limit exceeded: {len(timestamps)}/{max_actions} "
                    f"{level_key} actions in {_RATE_WINDOW_SEC}s")
        return None

    def _record_action(self, level_key: str) -> None:
        """Record action timestamp for rate limiting."""
        self._action_timestamps[level_key].append(time.time())

    def _should_escalate_confirm(self, action: str) -> bool:
        """True если action часто ошибался → требуется дополнительное подтверждение."""
        count = self._failure_counter.get(action, 0)
        return count >= 3  # после 3 ошибок → всегда подтверждение

    def record_failure(self, action: str) -> None:
        """Записать ошибку для action (используется IntentRouter)."""
        self._failure_counter[action] += 1
        logger.debug("AccessResolver: failure recorded for '%s' (total=%d)",
                     action, self._failure_counter[action])

    def reset_failures(self, action: str = "") -> None:
        """Сбросить счётчик ошибок."""
        if action:
            self._failure_counter.pop(action, None)
        else:
            self._failure_counter.clear()


# ─── Singleton ────────────────────────────────────────────────────────────────

_resolver: Optional[AccessLevelResolver] = None


def get_access_resolver() -> AccessLevelResolver:
    """Получить единственный AccessLevelResolver."""
    global _resolver
    if _resolver is None:
        _resolver = AccessLevelResolver()
    return _resolver
