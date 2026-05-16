"""
PolicyEngine — движок политик на основе TOML конфигурации.

Каждое действие перед выполнением проходит через PolicyEngine:
  1. Проверка allowed_domains
  2. Проверка blacklist
  3. Проверка max_auto_risk
  4. Проверка rate_limit
  5. Проверка require_confirmation

Файл политик: ~/.config/lina/policy.toml

Phase: GOVERNANCE LAYER / Module 2
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Попытка импорта tomllib (Python 3.11+) ──────────────────────────────────

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


# ─── Enums ────────────────────────────────────────────────────────────────────

class PolicyDecision(str, Enum):
    """Результат проверки политики."""
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"
    RATE_LIMITED = "rate_limited"


# ─── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class PolicyConfig:
    """Конфигурация политик."""
    # General
    max_auto_risk: str = "medium"
    require_confirmation_above: str = "high"
    always_block_critical: bool = True
    dry_run_default: bool = True

    # Domains
    allowed_domains: List[str] = field(default_factory=lambda: [
        "service", "package", "network", "disk", "config",
        "user", "boot", "display", "audio", "security", "installer",
        "desktop", "system", "safety", "general",
    ])
    blocked_domains: List[str] = field(default_factory=list)

    # Actions
    blocked_actions: List[str] = field(default_factory=list)
    always_confirm_actions: List[str] = field(default_factory=lambda: [
        "pkg_remove", "pkg_update", "boot_grub_install",
        "boot_systemd_install", "inst_pacstrap",
    ])

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_window: int = 60       # seconds
    rate_limit_max_actions: int = 20  # per window
    rate_limit_per_action: int = 5    # per action per window

    # Audit
    audit_all: bool = True
    audit_path: str = ""

    # Network
    allow_internet: bool = False
    allowed_urls: List[str] = field(default_factory=list)

    # Install mode
    installer_mode: bool = False
    installer_allowed_extra: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PolicyCheckResult:
    """Результат проверки политики для действия."""
    action_id: str
    decision: str
    reason: str
    risk_level: str = ""
    domain: str = ""
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "decision": self.decision,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "domain": self.domain,
            "timestamp": self.timestamp,
        }


# ─── Risk ordering ───────────────────────────────────────────────────────────

_RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

def _risk_ge(level: str, threshold: str) -> bool:
    """Проверить что risk level >= threshold."""
    return _RISK_ORDER.get(level, 0) >= _RISK_ORDER.get(threshold, 0)


# ─── Default TOML ────────────────────────────────────────────────────────────

DEFAULT_POLICY_TOML = """\
# ═══════════════════════════════════════════════════════════
# Lina Policy Configuration
# ═══════════════════════════════════════════════════════════

[general]
max_auto_risk = "medium"
require_confirmation_above = "high"
always_block_critical = true
dry_run_default = true

[domains]
allowed = [
    "service", "package", "network", "disk", "config",
    "user", "boot", "display", "audio", "security", "installer",
    "desktop", "system", "safety", "general",
]
blocked = []

[actions]
blocked = []
always_confirm = [
    "pkg_remove", "pkg_update",
    "boot_grub_install", "boot_systemd_install",
    "inst_pacstrap",
]

[rate_limit]
enabled = true
window = 60
max_actions = 20
per_action = 5

[audit]
enabled = true
path = ""

[network]
allow_internet = false
allowed_urls = []

[installer]
enabled = false
allowed_extra = []
"""


# ─── PolicyEngine ─────────────────────────────────────────────────────────────

class PolicyEngine:
    """
    Движок политик для проверки действий перед выполнением.

    Пример использования:
        engine = get_policy_engine()
        result = engine.check("svc_restart", domain="service", risk_level="low")
        if result.decision == PolicyDecision.ALLOW:
            ...  # выполнить
    """

    def __init__(self, config: Optional[PolicyConfig] = None) -> None:
        self._config = config or PolicyConfig()
        self._audit: deque = deque(maxlen=5000)
        self._rate_tracker: Dict[str, List[float]] = {}
        self._global_rate: List[float] = []
        self._max_audit = 5000
        if config is None:
            self._load_toml()

    # ── TOML Loading ─────────────────────────────────────

    def _load_toml(self) -> None:
        """Загрузить политику из TOML файла."""
        policy_path = self._get_policy_path()
        if not policy_path.exists():
            self._write_default_toml(policy_path)
            return

        if tomllib is None:
            logger.warning("PolicyEngine: tomllib not available, using defaults")
            return

        try:
            with open(policy_path, "rb") as f:
                data = tomllib.load(f)
            self._apply_toml(data)
            logger.info("PolicyEngine: loaded policy from %s", policy_path)
        except Exception as e:
            logger.error("PolicyEngine: failed to load %s: %s", policy_path, e)

    def _apply_toml(self, data: Dict[str, Any]) -> None:
        """Применить TOML конфигурацию."""
        c = self._config

        gen = data.get("general", {})
        c.max_auto_risk = gen.get("max_auto_risk", c.max_auto_risk)
        c.require_confirmation_above = gen.get("require_confirmation_above",
                                                c.require_confirmation_above)
        c.always_block_critical = gen.get("always_block_critical",
                                          c.always_block_critical)
        c.dry_run_default = gen.get("dry_run_default", c.dry_run_default)

        dom = data.get("domains", {})
        c.allowed_domains = dom.get("allowed", c.allowed_domains)
        c.blocked_domains = dom.get("blocked", c.blocked_domains)

        act = data.get("actions", {})
        c.blocked_actions = act.get("blocked", c.blocked_actions)
        c.always_confirm_actions = act.get("always_confirm",
                                           c.always_confirm_actions)

        rl = data.get("rate_limit", {})
        c.rate_limit_enabled = rl.get("enabled", c.rate_limit_enabled)
        c.rate_limit_window = rl.get("window", c.rate_limit_window)
        c.rate_limit_max_actions = rl.get("max_actions", c.rate_limit_max_actions)
        c.rate_limit_per_action = rl.get("per_action", c.rate_limit_per_action)

        aud = data.get("audit", {})
        c.audit_all = aud.get("enabled", c.audit_all)
        c.audit_path = aud.get("path", c.audit_path)

        net = data.get("network", {})
        c.allow_internet = net.get("allow_internet", c.allow_internet)
        c.allowed_urls = net.get("allowed_urls", c.allowed_urls)

        inst = data.get("installer", {})
        c.installer_mode = inst.get("enabled", c.installer_mode)
        c.installer_allowed_extra = inst.get("allowed_extra",
                                             c.installer_allowed_extra)

    def _write_default_toml(self, path: Path) -> None:
        """Записать дефолтный TOML файл."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_POLICY_TOML, encoding="utf-8")
            logger.info("PolicyEngine: wrote default policy to %s", path)
        except Exception as e:
            logger.debug("PolicyEngine: cannot write default TOML: %s", e)

    @staticmethod
    def _get_policy_path() -> Path:
        """Путь к файлу политик."""
        config_dir = os.environ.get("XDG_CONFIG_HOME",
                                    str(Path.home() / ".config"))
        return Path(config_dir) / "lina" / "policy.toml"

    def reload(self) -> None:
        """Перечитать TOML."""
        self._load_toml()

    # ── Check ────────────────────────────────────────────

    def check(self, action_id: str, *,
              domain: str = "", risk_level: str = "low",
              destructive: bool = False) -> PolicyCheckResult:
        """
        Проверить действие по политике.
        Returns: PolicyCheckResult с decision (allow/deny/confirm/rate_limited)
        """
        c = self._config

        # Phase 3: Normalize empty domain to "general"
        if not domain:
            domain = "general"

        # 1. Blocked actions
        if action_id in c.blocked_actions:
            return self._result(action_id, PolicyDecision.DENY,
                                "action in blocklist", risk_level, domain)

        # 2. Blocked domains
        if domain in c.blocked_domains:
            return self._result(action_id, PolicyDecision.DENY,
                                f"domain '{domain}' blocked", risk_level, domain)

        # 3. Allowed domains
        if domain not in c.allowed_domains:
            return self._result(action_id, PolicyDecision.DENY,
                                f"domain '{domain}' not allowed", risk_level, domain)

        # 4. Critical risk
        if c.always_block_critical and risk_level == "critical":
            return self._result(action_id, PolicyDecision.DENY,
                                "critical risk blocked by policy",
                                risk_level, domain)

        # 5. Rate limit
        if c.rate_limit_enabled and self._rate_limited(action_id):
            return self._result(action_id, PolicyDecision.RATE_LIMITED,
                                "rate limit exceeded", risk_level, domain)

        # 6. Always confirm
        if action_id in c.always_confirm_actions:
            return self._result(action_id, PolicyDecision.CONFIRM,
                                "action requires confirmation",
                                risk_level, domain)

        # 7. Risk threshold → confirmation
        if _risk_ge(risk_level, c.require_confirmation_above):
            return self._result(action_id, PolicyDecision.CONFIRM,
                                f"risk '{risk_level}' >= confirmation threshold",
                                risk_level, domain)

        # 8. Risk above auto threshold → confirmation
        if _risk_ge(risk_level, c.max_auto_risk) and risk_level != c.max_auto_risk:
            return self._result(action_id, PolicyDecision.CONFIRM,
                                f"risk '{risk_level}' above max auto",
                                risk_level, domain)

        # Allow
        return self._result(action_id, PolicyDecision.ALLOW,
                            "passed all checks", risk_level, domain)

    def check_internet(self, url: str = "") -> PolicyCheckResult:
        """Проверить разрешён ли доступ в интернет."""
        c = self._config
        if not c.allow_internet:
            return PolicyCheckResult(
                action_id="internet_access",
                decision=PolicyDecision.DENY,
                reason="internet access disabled by policy",
            )
        if url and c.allowed_urls:
            for allowed in c.allowed_urls:
                if url.startswith(allowed):
                    return PolicyCheckResult(
                        action_id="internet_access",
                        decision=PolicyDecision.ALLOW,
                        reason=f"URL matches allowed: {allowed}",
                    )
            return PolicyCheckResult(
                action_id="internet_access",
                decision=PolicyDecision.DENY,
                reason=f"URL '{url}' not in allowed list",
            )
        return PolicyCheckResult(
            action_id="internet_access",
            decision=PolicyDecision.ALLOW,
            reason="internet access allowed",
        )

    # ── Rate Limiting ────────────────────────────────────

    def _rate_limited(self, action_id: str) -> bool:
        """Проверить rate limit."""
        now = time.time()
        window = self._config.rate_limit_window

        # Global rate
        self._global_rate = [t for t in self._global_rate if now - t < window]
        if len(self._global_rate) >= self._config.rate_limit_max_actions:
            return True

        # Per-action rate
        if action_id not in self._rate_tracker:
            self._rate_tracker[action_id] = []
        self._rate_tracker[action_id] = [
            t for t in self._rate_tracker[action_id] if now - t < window
        ]
        if len(self._rate_tracker[action_id]) >= self._config.rate_limit_per_action:
            return True

        # Record
        self._global_rate.append(now)
        self._rate_tracker[action_id].append(now)

        # Prune empty entries periodically
        if len(self._rate_tracker) > 200:
            self._rate_tracker = {
                k: v for k, v in self._rate_tracker.items() if v
            }

        return False

    # ── Result + Audit ───────────────────────────────────

    def _result(self, action_id: str, decision: PolicyDecision,
                reason: str, risk_level: str, domain: str) -> PolicyCheckResult:
        """Создать результат и записать в аудит."""
        r = PolicyCheckResult(
            action_id=action_id, decision=decision,
            reason=reason, risk_level=risk_level, domain=domain,
        )
        if self._config.audit_all:
            self._audit.append(r)
        if decision == PolicyDecision.DENY:
            logger.warning("PolicyEngine DENY: %s — %s", action_id, reason)
        else:
            logger.debug("PolicyEngine %s: %s — %s", decision, action_id, reason)
        return r

    # ── Accessors ────────────────────────────────────────

    @property
    def config(self) -> PolicyConfig:
        """Текущая конфигурация."""
        return self._config

    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Получить аудит-лог."""
        return [r.to_dict() for r in list(self._audit)[-limit:]]

    # ── Content Safety Check ─────────────────────────────

    def check_content_safety(
        self,
        verdict: Any,
        command: str = "",
        *,
        allow_override: bool = False,
    ) -> PolicyCheckResult:
        """
        Проверить безопасность LLM-контента через governance PolicyEngine.

        Это единый метод, заменяющий safety/policy.py PolicyEngine.evaluate().

        Args:
            verdict: SafetyVerdict (из lina.safety.models).
                     Атрибуты: safe, risk_level (int 0-5), confidence, threats
            command: Исходная команда (для sandbox check).
            allow_override: Разрешить выполнение при нарушениях (если risk < CRITICAL).

        Returns:
            PolicyCheckResult с decision allow/deny/confirm.
        """
        action_id = "content_safety_check"
        risk_level_int = getattr(verdict, 'risk_level', 0)
        is_safe = getattr(verdict, 'safe', True)
        confidence = getattr(verdict, 'confidence', 1.0)
        threats = getattr(verdict, 'threats', [])

        # Map integer risk (0-5) to string risk
        _int_to_str = {0: "none", 1: "low", 2: "medium", 3: "high", 4: "critical", 5: "critical"}
        risk_str = _int_to_str.get(int(risk_level_int), "medium")

        # Rule 1: critical content → always deny
        if risk_level_int >= 4:  # CRITICAL or CATASTROPHIC
            return self._result(action_id, PolicyDecision.DENY,
                                f"Content risk critical ({risk_level_int})",
                                risk_str, "content_safety")

        # Rule 2: high risk → deny (unless override)
        if risk_level_int >= 3:  # HIGH
            if allow_override:
                return self._result(action_id, PolicyDecision.CONFIRM,
                                    f"Content risk high ({risk_level_int}), override requested",
                                    risk_str, "content_safety")
            return self._result(action_id, PolicyDecision.DENY,
                                f"Content risk high ({risk_level_int})",
                                risk_str, "content_safety")

        # Rule 3: low confidence → deny
        if confidence < 0.6:
            return self._result(action_id, PolicyDecision.DENY,
                                f"Low confidence ({confidence:.2f})",
                                risk_str, "content_safety")

        # Rule 4: validator marked unsafe → deny
        if not is_safe:
            return self._result(action_id, PolicyDecision.DENY,
                                "Validator marked content as unsafe",
                                risk_str, "content_safety")

        # Rule 5: multiple threat types → confirm
        if len(threats) >= 2:
            return self._result(action_id, PolicyDecision.CONFIRM,
                                f"Multiple threats detected ({len(threats)})",
                                risk_str, "content_safety")

        # Passed all checks
        return self._result(action_id, PolicyDecision.ALLOW,
                            "Content safety passed",
                            risk_str, "content_safety")

    def get_stats(self) -> Dict[str, Any]:
        """Статистика политик."""
        decisions: Dict[str, int] = {}
        for r in self._audit:
            decisions[r.decision] = decisions.get(r.decision, 0) + 1
        return {
            "audit_entries": len(self._audit),
            "decisions": decisions,
            "config": self._config.to_dict(),
        }


# ─── Singleton ─────────────────────────────────────────────────────────────────

_engine: Optional[PolicyEngine] = None

def get_policy_engine() -> PolicyEngine:
    """Получить единственный экземпляр PolicyEngine."""
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
    return _engine
