# -*- coding: utf-8 -*-
"""
Lina Safety — Движок политик (Policy Engine).

CANONICAL: lina.governance.policy_engine
Этот файл — compat shim. Вся новая логика политик — в governance.
Существующий API сохранён для обратной совместимости.

DEPRECATED (Phase 2): Используйте lina.governance.policy_engine.PolicyEngine.
Этот модуль будет удалён в v1.0. Все вызовы делегируются governance.

Принимает SafetyVerdict от валидатора и применяет правила:
  - Governance veto (check_content_safety) — первый проход
  - Блокировка если risk_level >= 3
  - Блокировка если confidence < 0.6
  - Блокировка если команда вне sandbox
  - Настраиваемые правила через PolicyRule

Каждое решение логируется для аудита.
"""

import logging
import warnings
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Callable

from lina.safety.models import (
    SafetyVerdict,
    PolicyDecision,
    RiskLevel,
)

logger = logging.getLogger("lina.safety.policy")

# Phase 2: Deprecation notice on module import
warnings.warn(
    "lina.safety.policy is deprecated. "
    "Use lina.governance.policy_engine.PolicyEngine instead. "
    "This module will be removed in v1.0.",
    DeprecationWarning,
    stacklevel=2,
)


# ═══════════════════════════════════════════════════════════
#  Правила политик
# ═══════════════════════════════════════════════════════════

@dataclass
class PolicyRule:
    """Одно правило политики безопасности.

    Attributes:
        name: Название правила (уникальное).
        description: Описание правила.
        check_fn: Функция проверки (SafetyVerdict, command) → bool.
                   True = правило нарушено (блокировка).
        priority: Приоритет (меньше = проверяется раньше).
        enabled: Правило активно.
    """
    name: str
    description: str
    check_fn: Callable[[SafetyVerdict, str], bool]
    priority: int = 100
    enabled: bool = True


# ═══════════════════════════════════════════════════════════
#  Встроенные правила
# ═══════════════════════════════════════════════════════════

def _rule_risk_threshold(verdict: SafetyVerdict, command: str) -> bool:
    """Блокировка по уровню риска >= 3 (HIGH)."""
    return verdict.risk_level >= RiskLevel.HIGH


def _rule_low_confidence(verdict: SafetyVerdict, command: str) -> bool:
    """Блокировка при низкой уверенности (< 0.6)."""
    return verdict.confidence < 0.6


def _rule_not_safe(verdict: SafetyVerdict, command: str) -> bool:
    """Блокировка если валидатор пометил как unsafe."""
    return not verdict.safe


def _rule_multiple_threats(verdict: SafetyVerdict, command: str) -> bool:
    """Блокировка при множественных угрозах (>= 2 разных типа)."""
    return len(verdict.threats) >= 2


DEFAULT_RULES: List[PolicyRule] = [
    PolicyRule(
        name="risk_threshold",
        description="Блокировка при risk_level >= 3 (HIGH)",
        check_fn=_rule_risk_threshold,
        priority=10,
    ),
    PolicyRule(
        name="low_confidence",
        description="Блокировка при confidence < 0.6",
        check_fn=_rule_low_confidence,
        priority=20,
    ),
    PolicyRule(
        name="validator_unsafe",
        description="Блокировка если валидатор пометил unsafe",
        check_fn=_rule_not_safe,
        priority=30,
    ),
    PolicyRule(
        name="multiple_threats",
        description="Блокировка при 2+ разных типах угроз",
        check_fn=_rule_multiple_threats,
        priority=40,
    ),
]


# ═══════════════════════════════════════════════════════════
#  PolicyEngine
# ═══════════════════════════════════════════════════════════

class PolicyEngine:
    """Движок политик безопасности.

    Принимает SafetyVerdict от SafetyValidator и применяет
    набор правил (PolicyRule) для финального решения.

    Все решения логируются для аудита.

    Attributes:
        rules: Список правил политик.
        sandbox_paths: Разрешённые пути для выполнения.
        _decisions_log: История решений (для аудита).
        _stats: Статистика.
    """

    def __init__(
        self,
        rules: Optional[List[PolicyRule]] = None,
        sandbox_paths: Optional[List[str]] = None,
        max_log_size: int = 1000,
    ):
        """Инициализация движка политик.

        DEPRECATED: Use lina.governance.policy_engine.PolicyEngine.

        Args:
            rules: Список правил (None → встроенные).
            sandbox_paths: Разрешённые пути (None → без ограничений).
            max_log_size: Максимальный размер лога решений.
        """
        warnings.warn(
            "safety.policy.PolicyEngine is deprecated. "
            "Use governance.policy_engine.PolicyEngine.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.rules = sorted(
            rules if rules is not None else list(DEFAULT_RULES),
            key=lambda r: r.priority,
        )
        self.sandbox_paths = sandbox_paths or []
        self._max_log_size = max_log_size
        self._decisions_log: deque = deque(maxlen=max_log_size)
        self._stats = {
            "total_decisions": 0,
            "allowed_count": 0,
            "blocked_count": 0,
            "overrides": 0,
        }

    # ───────────────────────────────────────────────────────
    #  Главный метод
    # ───────────────────────────────────────────────────────

    def evaluate(
        self,
        verdict: SafetyVerdict,
        command: str,
        allow_override: bool = False,
    ) -> PolicyDecision:
        """Оценивает вердикт и принимает решение.

        Governance pre-check: если governance PolicyEngine блокирует —
        результат сразу DENY (governance имеет veto).

        Затем проверяет все активные правила по приоритету.
        Первое нарушенное правило → блокировка.

        Args:
            verdict: Вердикт SafetyValidator.
            command: Исходная команда.
            allow_override: Разрешить выполнение даже при нарушениях
                            (только для risk < CRITICAL).

        Returns:
            PolicyDecision с финальным решением.
        """
        self._stats["total_decisions"] += 1

        # ── Governance veto (canonical PolicyEngine) ─────
        try:
            from lina.governance.policy_engine import get_policy_engine, PolicyDecision as GovDecision
            gov = get_policy_engine()
            gov_result = gov.check_content_safety(
                verdict, command, allow_override=allow_override,
            )
            if gov_result.decision == "deny":
                decision = PolicyDecision(
                    allowed=False,
                    reason=f"Governance veto: {gov_result.reason}",
                    verdict=verdict,
                    policy_rules_applied=["governance_veto"],
                )
                self._stats["blocked_count"] += 1
                self._log_decision(decision, command)
                return decision
        except Exception as exc:
            logger.error("Governance veto check failed (fail-CLOSED): %s", exc)
            decision = PolicyDecision(
                allowed=False,
                reason="Governance check failed (fail-closed)",
                verdict=verdict,
                policy_rules_applied=["governance_error"],
            )
            self._stats["blocked_count"] += 1
            self._log_decision(decision, command)
            return decision

        violated_rules: List[str] = []
        all_applied: List[str] = []

        # Проверяем все правила по приоритету
        for rule in self.rules:
            if not rule.enabled:
                continue

            all_applied.append(rule.name)

            try:
                if rule.check_fn(verdict, command):
                    violated_rules.append(rule.name)
            except Exception as e:
                logger.error(
                    "Rule '%s' raised exception (fail-closed): %s", rule.name, e
                )
                violated_rules.append(f"{rule.name}_error")

        # Проверяем sandbox paths (если настроены)
        sandbox_violation = self._check_sandbox(command)
        if sandbox_violation:
            violated_rules.append("sandbox_boundary")
            all_applied.append("sandbox_boundary")

        # Формируем решение
        if not violated_rules:
            # Все правила пройдены — разрешаем
            decision = PolicyDecision(
                allowed=True,
                reason="Все правила пройдены",
                verdict=verdict,
                policy_rules_applied=all_applied,
            )
            self._stats["allowed_count"] += 1

        elif allow_override and verdict.risk_level < RiskLevel.CRITICAL:
            # Override разрешён и risk < CRITICAL
            decision = PolicyDecision(
                allowed=True,
                reason=f"Override: нарушены правила [{', '.join(violated_rules)}]",
                verdict=verdict,
                policy_rules_applied=all_applied,
                override=True,
            )
            self._stats["overrides"] += 1
            self._stats["allowed_count"] += 1
            logger.warning(
                "Policy override for command: %s (risk=%d)",
                command[:50], verdict.risk_level
            )

        else:
            # Блокировка
            decision = PolicyDecision(
                allowed=False,
                reason=f"Заблокировано: нарушены правила [{', '.join(violated_rules)}]",
                verdict=verdict,
                policy_rules_applied=all_applied,
            )
            self._stats["blocked_count"] += 1
            logger.info(
                "BLOCKED: command='%s' risk=%d rules=%s",
                command[:80], verdict.risk_level, violated_rules
            )

        # Логируем решение
        self._log_decision(decision, command)

        return decision

    # ───────────────────────────────────────────────────────
    #  Sandbox boundary check
    # ───────────────────────────────────────────────────────

    def _check_sandbox(self, command: str) -> bool:
        """Проверяет, выходит ли команда за границы sandbox.

        Args:
            command: Команда для проверки.

        Returns:
            True если границы нарушены.
        """
        if not self.sandbox_paths:
            return False  # Нет ограничений — не нарушено

        # Ищем абсолютные пути в команде
        import re
        paths = re.findall(r"(?:^|\s)(/[^\s]+)", command)

        for path in paths:
            # Проверяем, что путь внутри разрешённых
            allowed = any(
                path.startswith(sp) for sp in self.sandbox_paths
            )
            if not allowed:
                logger.debug(
                    "Sandbox violation: path '%s' not in allowed paths", path
                )
                return True

        return False

    # ───────────────────────────────────────────────────────
    #  Аудит-лог
    # ───────────────────────────────────────────────────────

    def _log_decision(
        self,
        decision: PolicyDecision,
        command: str,
    ) -> None:
        """Записывает решение в лог аудита.

        Phase 2: Delegates to centralized AuditLogger.

        Args:
            decision: PolicyDecision.
            command: Исходная команда.
        """
        import time
        entry = {
            "timestamp": time.time(),
            "command": command[:200],  # Ограничиваем длину
            "allowed": decision.allowed,
            "reason": decision.reason,
            "risk_level": decision.verdict.risk_level,
            "confidence": decision.verdict.confidence,
            "rules_applied": decision.policy_rules_applied,
            "override": decision.override,
        }
        self._decisions_log.append(entry)

        # Phase 2: Forward to centralized audit
        try:
            from lina.governance.audit_logger import get_audit_logger, AuditRecord
            audit = get_audit_logger()
            audit.log(AuditRecord(
                event_type="policy_checked",
                decision="allow" if decision.allowed else "deny",
                action=command[:200],
                source="safety.policy",
                metadata={"reason": decision.reason, "override": decision.override},
            ))
        except Exception:
            pass  # Graceful: audit not critical

    # ───────────────────────────────────────────────────────
    #  Управление правилами
    # ───────────────────────────────────────────────────────

    def add_rule(self, rule: PolicyRule) -> None:
        """Добавляет правило политики.

        Args:
            rule: Правило для добавления.
        """
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority)

    def remove_rule(self, name: str) -> bool:
        """Удаляет правило по имени.

        Args:
            name: Название правила.

        Returns:
            True если правило найдено и удалено.
        """
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.name != name]
        return len(self.rules) < before

    def enable_rule(self, name: str) -> bool:
        """Включает правило по имени.

        Args:
            name: Название правила.

        Returns:
            True если правило найдено.
        """
        for rule in self.rules:
            if rule.name == name:
                rule.enabled = True
                return True
        return False

    def disable_rule(self, name: str) -> bool:
        """Отключает правило по имени.

        Args:
            name: Название правила.

        Returns:
            True если правило найдено.
        """
        for rule in self.rules:
            if rule.name == name:
                rule.enabled = False
                return True
        return False

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    def get_decisions_log(
        self,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Возвращает последние решения из лога.

        Args:
            limit: Максимальное количество записей.

        Returns:
            Список записей лога.
        """
        return self._decisions_log[-limit:]

    def get_stats(self) -> Dict[str, int]:
        """Возвращает статистику политик.

        Returns:
            Словарь со счётчиками.
        """
        return dict(self._stats)

    def reset_stats(self) -> None:
        """Сбрасывает статистику и лог."""
        for key in self._stats:
            self._stats[key] = 0
        self._decisions_log.clear()

    def get_rules_info(self) -> List[Dict[str, Any]]:
        """Возвращает информацию о правилах.

        Returns:
            Список словарей с описанием правил.
        """
        return [
            {
                "name": r.name,
                "description": r.description,
                "priority": r.priority,
                "enabled": r.enabled,
            }
            for r in self.rules
        ]

    def format_status(self) -> str:
        """Форматированный статус движка политик.

        Returns:
            Строка со статусом.
        """
        stats = self.get_stats()
        lines = [
            "⚖️  Policy Engine",
            f"   Правил: {len(self.rules)} "
            f"(активных: {sum(1 for r in self.rules if r.enabled)})",
            f"   Решений: {stats['total_decisions']}",
            f"   Разрешено: {stats['allowed_count']}",
            f"   Заблокировано: {stats['blocked_count']}",
            f"   Override: {stats['overrides']}",
        ]
        if self.sandbox_paths:
            lines.append(f"   Sandbox: {len(self.sandbox_paths)} путей")
        return "\n".join(lines)
