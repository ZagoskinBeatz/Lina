# -*- coding: utf-8 -*-
"""
Lina Core — Production Guard (Phase 23).

Финальный рубеж проверки перед выдачей ответа пользователю.
Гарантии:

1. Никогда не отдавать debug пользователю.
2. Никогда не смешивать tool raw output.
3. Никогда не показывать внутренний state.
4. Никогда не деградировать молча.
5. Всегда логировать execution_path.

ProductionGuard — самый последний фильтр в пайплайне.
ТОЛЬКО блокирует — НИКОГДА не модифицирует ответ.
"""

import re
import logging
import threading
from dataclasses import dataclass
from typing import Optional, List

logger = logging.getLogger("lina.core.production_guard")


# ═══════════════════════════════════════════════════════════
#  Guard patterns
# ═══════════════════════════════════════════════════════════

# Debug markers that MUST NEVER reach user
_FORBIDDEN_PATTERNS = [
    # Internal debug — anchored to line-start to avoid false positives
    re.compile(r"^\s*\[?ROUTER_DECISION\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?LLM BUDGET REPORT\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?CONTEXT_BUDGET\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?SAFETY_NET\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?TRACE\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?STATE_CHANGE\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?DEGRADATION\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?DRIFT\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?BUDGET_GOV\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?MODE_SWITCH\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?POST_PROCESSOR\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?VALIDATOR\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?CONFIG_SET\]?:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\[?TOOL_ENGINE\]?:", re.IGNORECASE | re.MULTILINE),
    # System prompt leaks
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"<\|user\|>", re.IGNORECASE),
    re.compile(r"<\|assistant\|>", re.IGNORECASE),
    re.compile(r"<<SYS>>", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
    # Internal state structures
    re.compile(r'"consecutive_failures":\s*\d+'),
    re.compile(r'"regeneration_count":\s*\d+'),
    re.compile(r'"execution_path":\s*"'),
    re.compile(r'"safe_mode":\s*(true|false)', re.IGNORECASE),
    re.compile(r'"_state":\s*\{'),
    # Raw tool output
    re.compile(r"<tool_result>", re.IGNORECASE),
    re.compile(r"<function_output>", re.IGNORECASE),
    re.compile(r"```tool_output", re.IGNORECASE),
]


# ═══════════════════════════════════════════════════════════
#  Guard Result
# ═══════════════════════════════════════════════════════════

@dataclass
class GuardResult:
    """Результат проверки Production Guard."""
    passed: bool = True
    violations: List[str] = None
    blocked: bool = False

    def __post_init__(self):
        if self.violations is None:
            self.violations = []


# ═══════════════════════════════════════════════════════════
#  Production Guard
# ═══════════════════════════════════════════════════════════

class ProductionGuard:
    """Финальный рубеж перед выдачей ответа (Phase 23).

    ТОЛЬКО блокирует — НИКОГДА не модифицирует.
    Если обнаружена утечка → blocked=True, caller решает.

    Usage:
        guard = ProductionGuard()
        result = guard.check(response_text)
        if result.blocked:
            # regenerate or return generic error
    """

    def __init__(self):
        self._stats = {"checks": 0, "blocked": 0, "passed": 0}
        self._stats_lock = threading.Lock()

    def check(self, response: str) -> GuardResult:
        """Проверяет ответ на недопустимый контент.

        Args:
            response: Финальный ответ перед выдачей пользователю.

        Returns:
            GuardResult — passed=True если безопасно.
        """
        with self._stats_lock:
            self._stats["checks"] += 1

        if not response:
            with self._stats_lock:
                self._stats["passed"] += 1
            return GuardResult(passed=True)

        violations = []

        for pat in _FORBIDDEN_PATTERNS:
            if pat.search(response):
                violations.append(pat.pattern)

        if violations:
            with self._stats_lock:
                self._stats["blocked"] += 1
            logger.warning(
                "PRODUCTION_GUARD: BLOCKED — %d violation(s): %s",
                len(violations), violations[:3],
            )
            return GuardResult(
                passed=False,
                violations=violations,
                blocked=True,
            )

        with self._stats_lock:
            self._stats["passed"] += 1
        return GuardResult(passed=True)

    def get_stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)

    def reset_stats(self) -> None:
        with self._stats_lock:
            for k in self._stats:
                self._stats[k] = 0
