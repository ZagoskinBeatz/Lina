# -*- coding: utf-8 -*-
"""
Lina Core — Integrity Checker.

CANONICAL FILE INTEGRITY: lina.governance.integrity_v2
Этот файл — execution path integrity checker + compat shim.
Проверка SHA256 файлов делегируется governance IntegrityCheckV2.

Проверка целостности выполнения:

Если фактический execution_path != plan.primary_path
→ CRITICAL ERROR + рекомендация safe mode.

IntegrityChecker — ТОЛЬКО проверяет.
НЕ переключает режим. Возвращает результат.
"""

import time
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

logger = logging.getLogger("lina.core.integrity_checker")


# ═══════════════════════════════════════════════════════════
#  Check Result
# ═══════════════════════════════════════════════════════════

@dataclass
class IntegrityResult:
    """Результат проверки целостности."""
    passed: bool = True
    planned_path: str = ""
    actual_path: str = ""
    severity: str = "ok"           # ok | warning | critical
    message: str = ""
    recommend_safe_mode: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "planned_path": self.planned_path,
            "actual_path": self.actual_path,
            "severity": self.severity,
            "message": self.message,
            "recommend_safe_mode": self.recommend_safe_mode,
        }


# ═══════════════════════════════════════════════════════════
#  Integrity Checker
# ═══════════════════════════════════════════════════════════

class IntegrityChecker:
    """Проверка целостности выполнения.

    CANONICAL (file integrity): lina.governance.integrity_v2
    Этот класс — execution path integrity.
    Для SHA256 file check используйте check_files().

    После выполнения запроса сравнивает:
      plan.primary_path  vs  фактический execution_path

    Если не совпадает → CRITICAL + recommend safe mode.

    Также проверяет:
      - plan hash не изменился mid-execution
      - step sequence выполнена корректно

    Usage:
        ic = IntegrityChecker()
        result = ic.check("LLM", "LLM")
        assert result.passed

        result = ic.check("TOOL", "LLM")
        assert not result.passed
        assert result.recommend_safe_mode
    """

    def __init__(self):
        self._check_count: int = 0
        self._violation_count: int = 0
        self._violations: deque = deque(maxlen=200)
        self._governance_integrity = None  # Lazy init

    def _get_governance_integrity(self):
        """Lazy init governance IntegrityCheckV2."""
        if self._governance_integrity is None:
            try:
                from lina.governance.integrity_v2 import get_integrity_checker
                self._governance_integrity = get_integrity_checker()
            except Exception:
                pass
        return self._governance_integrity

    def check_files(self):
        """
        Проверить целостность ФАЙЛОВ через governance IntegrityCheckV2.

        Returns:
            IntegrityResult от governance (или None если governance недоступен).
        """
        gov = self._get_governance_integrity()
        if gov is None:
            return None
        return gov.check()

    def check(
        self,
        planned_path: str,
        actual_path: str,
        *,
        plan_hash: str = "",
        expected_hash: str = "",
    ) -> IntegrityResult:
        """Проверяет соответствие плана и факта.

        Args:
            planned_path: Запланированный execution path.
            actual_path: Фактический execution path.
            plan_hash: Текущий hash плана.
            expected_hash: Ожидаемый hash (до начала выполнения).

        Returns:
            IntegrityResult.
        """
        self._check_count += 1

        # Path mismatch
        if planned_path.upper() != actual_path.upper():
            self._violation_count += 1
            violation = {
                "type": "path_mismatch",
                "planned": planned_path,
                "actual": actual_path,
                "time": time.time(),
            }
            self._violations.append(violation)
            logger.critical(
                "INTEGRITY VIOLATION: planned=%s actual=%s",
                planned_path, actual_path,
            )
            return IntegrityResult(
                passed=False,
                planned_path=planned_path,
                actual_path=actual_path,
                severity="critical",
                message=f"Execution path mismatch: planned {planned_path}, "
                        f"actual {actual_path}",
                recommend_safe_mode=True,
            )

        # Hash mismatch (plan mutated mid-execution)
        if plan_hash and expected_hash and plan_hash != expected_hash:
            self._violation_count += 1
            violation = {
                "type": "hash_mismatch",
                "expected": expected_hash,
                "actual": plan_hash,
                "time": time.time(),
            }
            self._violations.append(violation)
            logger.critical(
                "INTEGRITY VIOLATION: plan hash changed mid-execution "
                "(expected=%s, actual=%s)", expected_hash, plan_hash,
            )
            return IntegrityResult(
                passed=False,
                planned_path=planned_path,
                actual_path=actual_path,
                severity="critical",
                message="Plan hash changed mid-execution",
                recommend_safe_mode=True,
            )

        return IntegrityResult(
            passed=True,
            planned_path=planned_path,
            actual_path=actual_path,
            severity="ok",
            message="Execution matches plan",
        )

    def check_step_sequence(
        self,
        planned_steps: List[str],
        actual_steps: List[str],
    ) -> IntegrityResult:
        """Проверяет последовательность шагов multi-step плана.

        Args:
            planned_steps: Планируемая последовательность path-ов.
            actual_steps: Фактическая последовательность.

        Returns:
            IntegrityResult.
        """
        self._check_count += 1

        if len(actual_steps) != len(planned_steps):
            self._violation_count += 1
            self._violations.append({
                "type": "step_count_mismatch",
                "planned": len(planned_steps),
                "actual": len(actual_steps),
                "time": time.time(),
            })
            return IntegrityResult(
                passed=False,
                planned_path=",".join(planned_steps),
                actual_path=",".join(actual_steps),
                severity="critical",
                message=f"Step count mismatch: planned {len(planned_steps)}, "
                        f"actual {len(actual_steps)}",
                recommend_safe_mode=True,
            )

        for i, (p, a) in enumerate(zip(planned_steps, actual_steps)):
            if p.upper() != a.upper():
                self._violation_count += 1
                self._violations.append({
                    "type": "step_path_mismatch",
                    "step": i + 1,
                    "planned": p,
                    "actual": a,
                    "time": time.time(),
                })
                return IntegrityResult(
                    passed=False,
                    planned_path=",".join(planned_steps),
                    actual_path=",".join(actual_steps),
                    severity="critical",
                    message=f"Step {i+1} mismatch: planned {p}, actual {a}",
                    recommend_safe_mode=True,
                )

        return IntegrityResult(
            passed=True,
            planned_path=",".join(planned_steps),
            actual_path=",".join(actual_steps),
            severity="ok",
            message="All steps match plan",
        )

    def get_violations(self) -> List[Dict[str, Any]]:
        """Все нарушения."""
        return list(self._violations)

    def clear(self) -> None:
        """Сброс счётчиков."""
        self._violations.clear()
        self._violation_count = 0
        self._check_count = 0

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для SystemControl."""
        return {
            "checks": self._check_count,
            "violations": self._violation_count,
            "last_violations": [
                v for v in self._violations[-5:]
            ],
            "integrity_rate": (
                f"{(self._check_count - self._violation_count) / self._check_count * 100:.1f}%"
                if self._check_count > 0 else "N/A"
            ),
        }
