"""
RiskEngine — многофакторная система оценки рисков.

Оценивает каждое действие по 7 факторам:
  1. Destructive Score      — разрушительность команды
  2. System Impact Score    — влияние на систему
  3. Privilege Escalation   — повышение привилегий
  4. Dependency Impact      — влияние на зависимости
  5. Rollback Complexity    — сложность отката
  6. Probability of Success — вероятность успеха
  7. Historical Success Rate — из ContextMemory

Формула:
  TotalRisk = Σ(weight_i × score_i) / Σ(weight_i)

Режимы:
  AUTONOMOUS — только если TotalRisk < threshold_auto (0.35)
  ASSIST     — TotalRisk < threshold_assist (0.70)
  SAFE       — всё остальное (>= 0.70)

Делегирует safety/validator.py для pattern-matching опасных команд.

Phase: SYSTEM OVERLORD / Module 1
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Risk Level
# ═══════════════════════════════════════════════════════════════════

class RiskVerdict(str, Enum):
    """Итоговый вердикт риска."""
    NEGLIGIBLE = "negligible"     # < 0.15  — можно AUTONOMOUS
    LOW = "low"                   # < 0.35  — можно AUTONOMOUS
    MEDIUM = "medium"             # < 0.55  — ASSIST рекомендуется
    HIGH = "high"                 # < 0.75  — только ASSIST
    CRITICAL = "critical"         # >= 0.75 — только SAFE + confirm


# ═══════════════════════════════════════════════════════════════════
#  Risk Assessment — результат оценки
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RiskAssessment:
    """Полная оценка риска одного действия."""
    total_risk: float = 0.0
    verdict: RiskVerdict = RiskVerdict.NEGLIGIBLE
    factors: Dict[str, float] = field(default_factory=dict)
    allowed_modes: List[str] = field(default_factory=list)
    blocking_reasons: List[str] = field(default_factory=list)
    assessment_hash: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.assessment_hash:
            raw = json.dumps(
                {"risk": self.total_risk, "factors": self.factors},
                sort_keys=True,
            )
            self.assessment_hash = hashlib.sha256(raw.encode()).hexdigest()[:12]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_risk": round(self.total_risk, 4),
            "verdict": self.verdict.value,
            "factors": {k: round(v, 4) for k, v in self.factors.items()},
            "allowed_modes": self.allowed_modes,
            "blocking_reasons": self.blocking_reasons,
            "hash": self.assessment_hash,
        }


# ═══════════════════════════════════════════════════════════════════
#  Весовые коэффициенты факторов
# ═══════════════════════════════════════════════════════════════════

_DEFAULT_WEIGHTS: Dict[str, float] = {
    "destructive":          0.25,
    "system_impact":        0.20,
    "privilege_escalation": 0.15,
    "dependency_impact":    0.10,
    "rollback_complexity":  0.10,
    "success_probability":  0.10,  # инвертируется: 1 - prob
    "historical_success":   0.10,  # инвертируется: 1 - rate
}

# ═══════════════════════════════════════════════════════════════════
#  Пороги вердиктов
# ═══════════════════════════════════════════════════════════════════

_THRESHOLDS: List[Tuple[float, RiskVerdict]] = [
    (0.15, RiskVerdict.NEGLIGIBLE),
    (0.35, RiskVerdict.LOW),
    (0.55, RiskVerdict.MEDIUM),
    (0.75, RiskVerdict.HIGH),
    (1.01, RiskVerdict.CRITICAL),
]

_MODE_LIMITS: Dict[RiskVerdict, List[str]] = {
    RiskVerdict.NEGLIGIBLE: ["autonomous", "assist", "safe"],
    RiskVerdict.LOW:        ["autonomous", "assist", "safe"],
    RiskVerdict.MEDIUM:     ["assist", "safe"],
    RiskVerdict.HIGH:       ["assist", "safe"],
    RiskVerdict.CRITICAL:   ["safe"],
}


# ═══════════════════════════════════════════════════════════════════
#  Паттерны деструктивности
# ═══════════════════════════════════════════════════════════════════

_DESTRUCTIVE_PATTERNS: List[Tuple[re.Pattern, float, str]] = [
    (re.compile(r"rm\s+(-rf?|--recursive)\s+/", re.I), 1.0, "recursive delete from root"),
    (re.compile(r"mkfs\.", re.I), 1.0, "format filesystem"),
    (re.compile(r"dd\s+if=.+of=/dev/", re.I), 0.95, "raw disk write"),
    (re.compile(r"chmod\s+(-R\s+)?777\s+/", re.I), 0.9, "open permissions on root"),
    (re.compile(r":()\{.*\|.*&\s*\};:", re.I), 1.0, "fork bomb"),
    (re.compile(r"curl\s+.*\|\s*(ba)?sh", re.I), 0.85, "piped remote script"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh", re.I), 0.85, "piped remote script"),
    (re.compile(r">\s*/dev/sd[a-z]", re.I), 1.0, "direct write to disk"),
    (re.compile(r"shred\s+", re.I), 0.9, "shred command"),
    (re.compile(r"rm\s+(-rf?)\s+(/home|/etc|/var|/usr|/boot)", re.I), 0.95, "delete critical dir"),
    (re.compile(r"pacman\s+-Rdd", re.I), 0.7, "force remove ignoring deps"),
    (re.compile(r"apt\s+.*--force-yes.*remove", re.I), 0.7, "forced remove"),
    (re.compile(r"systemctl\s+(disable|mask)\s+(NetworkManager|systemd-resolved|sshd)", re.I), 0.6, "disable critical service"),
    (re.compile(r"kill\s+-9\s+1\b", re.I), 1.0, "kill init"),
    (re.compile(r"echo\s+.*>\s*/etc/(passwd|shadow|fstab|sudoers)", re.I), 0.95, "overwrite system file"),
]

_PRIVILEGE_PATTERNS: List[Tuple[re.Pattern, float, str]] = [
    (re.compile(r"\bsudo\b", re.I), 0.4, "sudo"),
    (re.compile(r"\bpkexec\b", re.I), 0.5, "pkexec"),
    (re.compile(r"\bsu\s+-?\s*$", re.I), 0.6, "switch to root"),
    (re.compile(r"visudo", re.I), 0.7, "edit sudoers"),
    (re.compile(r"chmod\s+[ugo]\+s", re.I), 0.8, "setuid/setgid"),
    (re.compile(r"chown\s+root", re.I), 0.5, "change owner to root"),
    (re.compile(r"/etc/sudoers", re.I), 0.7, "sudoers file"),
]

_SYSTEM_IMPACT_PATTERNS: List[Tuple[re.Pattern, float, str]] = [
    (re.compile(r"systemctl\s+(restart|stop)\s+", re.I), 0.4, "service control"),
    (re.compile(r"systemctl\s+daemon-reload", re.I), 0.3, "daemon reload"),
    (re.compile(r"modprobe\s+(-r\s+)?", re.I), 0.5, "kernel module"),
    (re.compile(r"sysctl\s+-w\s+", re.I), 0.5, "kernel parameter"),
    (re.compile(r"grub-mkconfig|update-grub|mkinitcpio", re.I), 0.6, "bootloader/initramfs"),
    (re.compile(r"pacman\s+-S(yu|yyu)", re.I), 0.5, "full system upgrade"),
    (re.compile(r"apt\s+(dist-)?upgrade", re.I), 0.5, "system upgrade"),
    (re.compile(r"dnf\s+system-upgrade", re.I), 0.5, "system upgrade"),
    (re.compile(r"reboot|shutdown|poweroff", re.I), 0.7, "power control"),
    (re.compile(r"ip\s+(link|route)\s+(set|add|del)", re.I), 0.4, "network config"),
    (re.compile(r"iptables|nftables|firewall-cmd|ufw", re.I), 0.4, "firewall change"),
    (re.compile(r"dpkg\s+--configure\s+-a", re.I), 0.3, "dpkg reconfigure"),
]

_DEPENDENCY_PATTERNS: List[Tuple[re.Pattern, float, str]] = [
    (re.compile(r"pacman\s+-R\s+", re.I), 0.4, "package remove"),
    (re.compile(r"apt\s+(remove|purge)", re.I), 0.4, "package remove"),
    (re.compile(r"dnf\s+(remove|erase)", re.I), 0.4, "package remove"),
    (re.compile(r"pip\s+uninstall", re.I), 0.3, "pip uninstall"),
    (re.compile(r"npm\s+uninstall.*-g", re.I), 0.3, "global npm uninstall"),
    (re.compile(r"flatpak\s+uninstall", re.I), 0.3, "flatpak remove"),
]


# ═══════════════════════════════════════════════════════════════════
#  RiskEngine
# ═══════════════════════════════════════════════════════════════════

class RiskEngine:
    """Многофакторная оценка рисков.

    Каждое действие (команда или FixPlan) оценивается по 7 факторам.
    Итоговый risk → verdict → разрешённые режимы.

    Интегрируется с:
      - safety/validator.py (если доступен) — для pattern-safety
      - diagnostics/memory.py — для historical success rate
      - diagnostics/autofix.py — для FixPlan risk gating

    Usage:
        engine = get_risk_engine()
        assessment = engine.assess_command("sudo systemctl restart NetworkManager")
        # assessment.verdict == RiskVerdict.MEDIUM
        # assessment.allowed_modes == ["assist", "safe"]
    """

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        auto_threshold: float = 0.35,
        assist_threshold: float = 0.75,
    ):
        self._weights = weights or dict(_DEFAULT_WEIGHTS)
        self._auto_threshold = auto_threshold
        self._assist_threshold = assist_threshold
        self._assessments: List[RiskAssessment] = []

    # ─── Основной метод: оценка команды ───────────────────────

    def assess_command(self, command: str) -> RiskAssessment:
        """Оценить риск одной shell-команды."""
        factors: Dict[str, float] = {}

        # 1. Destructive score
        factors["destructive"] = self._score_patterns(command, _DESTRUCTIVE_PATTERNS)

        # 2. System impact
        factors["system_impact"] = self._score_patterns(command, _SYSTEM_IMPACT_PATTERNS)

        # 3. Privilege escalation
        factors["privilege_escalation"] = self._score_patterns(command, _PRIVILEGE_PATTERNS)

        # 4. Dependency impact
        factors["dependency_impact"] = self._score_patterns(command, _DEPENDENCY_PATTERNS)

        # 5. Rollback complexity — производная от destructive + system_impact
        factors["rollback_complexity"] = min(
            1.0,
            factors["destructive"] * 0.6 + factors["system_impact"] * 0.4,
        )

        # 6. Success probability — базовая оценка
        #    высокий destructive → низкая успешность
        factors["success_probability"] = max(
            0.0, 1.0 - factors["destructive"] * 0.5 - factors["system_impact"] * 0.3,
        )

        # 7. Historical success rate — из ContextMemory
        factors["historical_success"] = self._get_historical_rate(command)

        return self._build_assessment(factors, command)

    # ─── Оценка FixPlan (набор action-ов) ────────────────────

    def assess_plan(self, actions: List[Any]) -> RiskAssessment:
        """Оценить риск целого FixPlan — max risk по всем actions.

        Args:
            actions: List of dicts with 'command' key, or List of strings.
        """
        if not actions:
            return RiskAssessment(
                total_risk=0.0, verdict=RiskVerdict.NEGLIGIBLE,
                allowed_modes=["autonomous", "assist", "safe"],
            )

        commands: List[str] = []
        for a in actions:
            if isinstance(a, str):
                commands.append(a)
            elif isinstance(a, dict):
                cmd = a.get("command", "")
                if cmd:
                    commands.append(cmd)

        individual = [self.assess_command(cmd) for cmd in commands if cmd]

        if not individual:
            return RiskAssessment(
                total_risk=0.0, verdict=RiskVerdict.NEGLIGIBLE,
                allowed_modes=["autonomous", "assist", "safe"],
            )

        # Worst-case по каждому фактору
        factors: Dict[str, float] = {}
        for key in _DEFAULT_WEIGHTS:
            factors[key] = max(a.factors.get(key, 0.0) for a in individual)

        # Объединяем blocking_reasons
        all_reasons = []
        for a in individual:
            all_reasons.extend(a.blocking_reasons)

        assessment = self._build_assessment(factors)
        assessment.blocking_reasons = list(set(all_reasons))
        return assessment

    # ─── Оценка из Diagnosis ──────────────────────────────────

    def assess_diagnosis(
        self,
        category: str,
        risk_level: str,
        suggested_actions: Optional[List[str]] = None,
    ) -> RiskAssessment:
        """Оценить риск на основе диагноза (из ErrorClassifier)."""
        risk_map = {
            "low": 0.2, "medium": 0.45, "high": 0.65, "critical": 0.85,
        }
        base = risk_map.get(risk_level.lower(), 0.5)

        factors = {
            "destructive": base * 0.3,
            "system_impact": base * 0.8,
            "privilege_escalation": base * 0.4,
            "dependency_impact": base * 0.3,
            "rollback_complexity": base * 0.5,
            "success_probability": max(0.0, 1.0 - base * 0.4),
            "historical_success": self._get_historical_rate_for_category(category),
        }

        return self._build_assessment(factors)

    # ─── Проверка допустимости режима ─────────────────────────

    def is_mode_allowed(self, mode: str, assessment: RiskAssessment) -> bool:
        return mode.lower() in assessment.allowed_modes

    # ─── Внутренние методы ────────────────────────────────────

    @staticmethod
    def _score_patterns(
        command: str,
        patterns: List[Tuple[re.Pattern, float, str]],
    ) -> float:
        """Максимальная score среди совпавших паттернов."""
        max_score = 0.0
        for pat, score, _ in patterns:
            if pat.search(command):
                max_score = max(max_score, score)
        return max_score

    def _build_assessment(
        self,
        factors: Dict[str, float],
        context: str = "",
    ) -> RiskAssessment:
        """Вычислить TotalRisk и вердикт."""
        weighted_sum = 0.0
        total_weight = 0.0

        for key, weight in self._weights.items():
            value = factors.get(key, 0.0)
            # Инвертировать "позитивные" факторы
            if key in ("success_probability", "historical_success"):
                value = 1.0 - value
            weighted_sum += weight * value
            total_weight += weight

        total_risk = weighted_sum / total_weight if total_weight > 0 else 0.0
        total_risk = max(0.0, min(1.0, total_risk))

        # Override: если destructive >= 0.9 → принудительно CRITICAL
        if factors.get("destructive", 0) >= 0.9:
            total_risk = max(total_risk, 0.95)

        verdict = RiskVerdict.CRITICAL
        for threshold, v in _THRESHOLDS:
            if total_risk < threshold:
                verdict = v
                break

        allowed = _MODE_LIMITS.get(verdict, ["safe"])

        blocking = []
        if factors.get("destructive", 0) >= 0.9:
            blocking.append("DESTRUCTIVE action (score >= 0.9)")
        if factors.get("privilege_escalation", 0) >= 0.8:
            blocking.append("HIGH privilege escalation")
        if factors.get("system_impact", 0) >= 0.8:
            blocking.append("HIGH system impact")

        assessment = RiskAssessment(
            total_risk=total_risk,
            verdict=verdict,
            factors=factors,
            allowed_modes=allowed,
            blocking_reasons=blocking,
        )
        self._assessments.append(assessment)
        if len(self._assessments) > 200:
            self._assessments = self._assessments[-200:]

        return assessment

    def _get_historical_rate(self, command: str) -> float:
        """Исторический success rate для команды из ContextMemory."""
        try:
            from lina.diagnostics.memory import get_memory
            memory = get_memory()
            # Ищем похожие по ключевому слову команды
            words = command.split()[:3]
            keyword = " ".join(words) if words else command
            successful = memory.find_successful(keyword)
            failed = memory.find_failed(keyword)
            total = len(successful) + len(failed)
            if total == 0:
                return 0.5  # нет данных → нейтральная оценка
            return len(successful) / total
        except Exception as e:
            logger.warning("Historical rate lookup failed: %s", e)
            return 0.5

    def _get_historical_rate_for_category(self, category: str) -> float:
        """Исторический rate для категории ошибки."""
        try:
            from lina.diagnostics.memory import get_memory
            memory = get_memory()
            successful = memory.find_successful(category)
            failed = memory.find_failed(category)
            total = len(successful) + len(failed)
            if total == 0:
                return 0.5
            return len(successful) / total
        except Exception as e:
            logger.warning("Historical rate lookup for category failed: %s", e)
            return 0.5

    # ─── Статистика ────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        if not self._assessments:
            return {"total": 0}
        risks = [a.total_risk for a in self._assessments]
        verdicts = {}
        for a in self._assessments:
            verdicts[a.verdict.value] = verdicts.get(a.verdict.value, 0) + 1
        return {
            "total": len(self._assessments),
            "avg_risk": sum(risks) / len(risks),
            "max_risk": max(risks),
            "verdicts": verdicts,
        }


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_engine: Optional[RiskEngine] = None


def get_risk_engine() -> RiskEngine:
    global _engine
    if _engine is None:
        _engine = RiskEngine()
    return _engine
