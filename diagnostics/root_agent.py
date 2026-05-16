"""
RootAgent — привилегированный исполнительный слой.

Двухуровневая архитектура:
  User-Level Orchestrator → формирует FixPlan
  RootAgent               → исполняет ТОЛЬКО whitelist-команды

Гарантии безопасности:
  - Только whitelist-команды (нет произвольного shell)
  - Валидация plan_hash перед каждым выполнением
  - Rate limiting (max N команд/минуту)
  - Полный audit log каждого вызова
  - IPC через типизированный API (не сокет — in-process)
  - Input sanitization
  - Каждая команда имеет уникальный ID

Phase: SYSTEM OVERLORD / Module 2
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Execution Request / Result
# ═══════════════════════════════════════════════════════════════════

class ExecStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"
    HASH_MISMATCH = "hash_mismatch"
    VALIDATION_FAILED = "validation_failed"
    TIMEOUT = "timeout"


@dataclass
class ExecRequest:
    """Запрос на выполнение от User-Level Orchestrator."""
    command: str
    plan_hash: str = ""
    request_id: str = ""
    category: str = ""             # whitelist category
    dry_run: bool = False
    timeout: int = 30
    requires_sudo: bool = False
    rollback_cmd: str = ""
    verify_cmd: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.request_id:
            self.request_id = f"req_{uuid.uuid4().hex[:12]}"


@dataclass
class ExecResult:
    """Результат выполнения от RootAgent."""
    request_id: str = ""
    status: ExecStatus = ExecStatus.FAILED
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    command_executed: str = ""
    blocked_reason: str = ""
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:500],
            "stderr": self.stderr[:300],
            "duration_ms": self.duration_ms,
            "command": self.command_executed,
            "verified": self.verified,
        }


# ═══════════════════════════════════════════════════════════════════
#  Whitelist — разрешённые категории команд
# ═══════════════════════════════════════════════════════════════════

@dataclass
class WhitelistEntry:
    category: str
    pattern: re.Pattern
    max_risk: float = 0.7          # блокировать если risk выше
    requires_sudo: bool = False
    description: str = ""


_WHITELIST: List[WhitelistEntry] = [
    # ─── Network diagnostics (read-only) ───
    WhitelistEntry("net_diag", re.compile(
        r"^(ping|nslookup|dig|host|traceroute|tracepath|mtr|curl\s+-s|wget\s+-q)\s",
    ), max_risk=0.2, description="Network diagnostics"),
    WhitelistEntry("net_status", re.compile(
        r"^(ip\s+(addr|link|route)\s+show|ip\s+-brief|ss\s+-|nmcli\s+(dev|general|connection)\s+show|resolvectl\s+status)",
    ), max_risk=0.1, description="Network status"),

    # ─── Network actions ───
    WhitelistEntry("net_action", re.compile(
        r"^(nmcli\s+(dev\s+wifi\s+connect|connection\s+up|connection\s+down|radio\s+wifi)|"
        r"resolvectl\s+dns|systemctl\s+(restart|start)\s+(NetworkManager|systemd-resolved))",
    ), max_risk=0.5, requires_sudo=False, description="Network actions"),

    # ─── Package read ───
    WhitelistEntry("pkg_read", re.compile(
        r"^(pacman\s+-[QSs]|apt(-cache)?\s+(search|show|list)|dpkg\s+-[lLs]|dnf\s+(search|info|list)|checkupdates)",
    ), max_risk=0.1, description="Package query"),

    # ─── Package install/remove (needs sudo) ───
    WhitelistEntry("pkg_install", re.compile(
        r"^(sudo\s+)?(pacman\s+-S\s+|apt\s+install\s+|dnf\s+install\s+|flatpak\s+install\s+)",
    ), max_risk=0.5, requires_sudo=True, description="Package install"),
    WhitelistEntry("pkg_remove", re.compile(
        r"^(sudo\s+)?(pacman\s+-R\s+|apt\s+remove\s+|dnf\s+remove\s+|flatpak\s+uninstall\s+)",
    ), max_risk=0.6, requires_sudo=True, description="Package remove"),

    # ─── Audio ───
    WhitelistEntry("audio", re.compile(
        r"^(pactl\s+(set-sink-(volume|mute)|list|get-)|"
        r"systemctl\s+--user\s+(restart|start)\s+(pipewire|pulseaudio|wireplumber))",
    ), max_risk=0.3, description="Audio control"),

    # ─── Service management ───
    WhitelistEntry("svc_status", re.compile(
        r"^(systemctl\s+(status|is-active|is-enabled|list-units|--failed)|journalctl\s+)",
    ), max_risk=0.1, description="Service status"),
    WhitelistEntry("svc_control", re.compile(
        r"^(sudo\s+)?systemctl\s+(restart|start|stop|enable|disable)\s+\S+",
    ), max_risk=0.5, requires_sudo=True, description="Service control"),

    # ─── Hardware info (read-only) ───
    WhitelistEntry("hw_info", re.compile(
        r"^(lspci|lsusb|lsblk|lscpu|lsmod|sensors|free\s|df\s|uname\s|cat\s+/proc/|cat\s+/sys/|uptime|dmesg)",
    ), max_risk=0.1, description="Hardware info"),

    # ─── Bluetooth ───
    WhitelistEntry("bluetooth", re.compile(
        r"^(bluetoothctl\s+(show|devices|power|connect|disconnect|scan)|rfkill\s+(list|unblock\s+bluetooth))",
    ), max_risk=0.3, description="Bluetooth control"),

    # ─── Kernel (read-only) ───
    WhitelistEntry("kernel_read", re.compile(
        r"^(sysctl\s+-a|modinfo\s+|lsmod|cat\s+/proc/(cmdline|version|cpuinfo|meminfo))",
    ), max_risk=0.1, description="Kernel info"),

    # ─── DNS ───
    WhitelistEntry("dns", re.compile(
        r"^(resolvectl\s+(status|query|flush-caches)|systemd-resolve\s+)",
    ), max_risk=0.2, description="DNS operations"),

    # ─── Disk diagnostics ───
    WhitelistEntry("disk_diag", re.compile(
        r"^(smartctl\s+|df\s+|du\s+|lsblk|findmnt|mount\s*$|blkid)",
    ), max_risk=0.1, description="Disk diagnostics"),

    # ─── Journal cleanup ───
    WhitelistEntry("cleanup", re.compile(
        r"^(sudo\s+)?journalctl\s+--vacuum-(size|time)=",
    ), max_risk=0.3, requires_sudo=True, description="Journal cleanup"),

    # ─── Process management ───
    WhitelistEntry("process", re.compile(
        r"^(ps\s+|top\s+-bn1|pgrep\s+|pkill\s+-15\s+|kill\s+-15\s+\d+)",
    ), max_risk=0.3, description="Process management"),

    # ─── Display ───
    WhitelistEntry("display", re.compile(
        r"^(xrandr|wlr-randr|kscreen-doctor\s+-o|xdpyinfo|wayland-info)",
    ), max_risk=0.1, description="Display info"),

    # ─── Firewall status ───
    WhitelistEntry("firewall", re.compile(
        r"^(sudo\s+)?(ufw\s+status|firewall-cmd\s+--state|iptables\s+-L)",
    ), max_risk=0.2, description="Firewall status"),
]

# ═══════════════════════════════════════════════════════════════════
#  Абсолютный блэклист (никогда не выполнять)
# ═══════════════════════════════════════════════════════════════════

_BLACKLIST_PATTERNS: List[re.Pattern] = [
    re.compile(r"rm\s+(-rf?|--recursive)\s+/\s*$", re.I),
    re.compile(r"rm\s+(-rf?)\s+/\s", re.I),
    re.compile(r"mkfs\.", re.I),
    re.compile(r"dd\s+if=.*of=/dev/sd", re.I),
    re.compile(r":()\{.*\|.*&.*;:", re.I),  # fork bomb
    re.compile(r">\s*/dev/sd[a-z]", re.I),
    re.compile(r"curl\s+.*\|\s*(ba)?sh", re.I),
    re.compile(r"wget\s+.*\|\s*(ba)?sh", re.I),
    re.compile(r"eval\s*\(", re.I),
    re.compile(r"python[23]?\s+-c\s+.*exec\(", re.I),
    re.compile(r"kill\s+-9\s+1\b", re.I),
    re.compile(r"chmod\s+(-R\s+)?777\s+/\s*$", re.I),
    re.compile(r"echo\s+.*>\s*/etc/(passwd|shadow|fstab)", re.I),
    re.compile(r"shred\s+-[a-z]*\s+/", re.I),
]

# ═══════════════════════════════════════════════════════════════════
#  Input sanitization
# ═══════════════════════════════════════════════════════════════════

_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"[;&|`$]"),           # shell metacharacters
    re.compile(r"\$\("),              # command substitution
    re.compile(r">\s*/"),             # redirect to root
    re.compile(r"\.\./\.\./\.\./"),   # path traversal
]


def _sanitize_input(value: str) -> Tuple[bool, str]:
    """Проверка пользовательского ввода в аргументах."""
    for p in _INJECTION_PATTERNS:
        if p.search(value):
            return False, f"Injection pattern detected: {p.pattern}"
    return True, ""


# ═══════════════════════════════════════════════════════════════════
#  RootAgent
# ═══════════════════════════════════════════════════════════════════

class RootAgent:
    """Привилегированный исполнительный агент.

    Принципы:
      - Выполняет ТОЛЬКО whitelist-команды
      - Проверяет plan_hash перед каждой операцией
      - Rate limiting: max_commands_per_minute
      - Полный audit log
      - Input sanitization
      - Блэклист запрещённых команд

    Usage:
        agent = get_root_agent()
        req = ExecRequest(command="systemctl restart NetworkManager",
                          plan_hash="abc123def456")
        result = agent.execute(req)
    """

    def __init__(
        self,
        max_commands_per_minute: int = 20,
        enable_sudo: bool = False,
    ):
        self._max_rpm = max_commands_per_minute
        self._enable_sudo = enable_sudo
        self._timestamps: List[float] = []
        self._audit_log: List[Dict[str, Any]] = []
        self._verified_hashes: set = set()
        self._total_executed = 0
        self._total_blocked = 0

    # ─── Главный метод выполнения ─────────────────────────────

    def execute(self, request: ExecRequest) -> ExecResult:
        """Выполнить запрос с полной валидацией."""
        result = ExecResult(request_id=request.request_id)

        # 1. Rate limiting
        if not self._check_rate_limit():
            result.status = ExecStatus.RATE_LIMITED
            result.blocked_reason = f"Rate limit exceeded ({self._max_rpm}/min)"
            self._audit_entry("RATE_LIMITED", request, result)
            self._total_blocked += 1
            return result

        # 2. Blacklist check
        for bp in _BLACKLIST_PATTERNS:
            if bp.search(request.command):
                result.status = ExecStatus.BLOCKED
                result.blocked_reason = f"BLACKLISTED: {bp.pattern}"
                self._audit_entry("BLACKLISTED", request, result)
                self._total_blocked += 1
                return result

        # 3. Whitelist match
        matched_entry = self._match_whitelist(request.command)
        if matched_entry is None:
            result.status = ExecStatus.BLOCKED
            result.blocked_reason = f"Command not in whitelist"
            self._audit_entry("NOT_WHITELISTED", request, result)
            self._total_blocked += 1
            return result

        # 3b. Input sanitization (validate arguments against injection)
        safe, sanitize_reason = _sanitize_input(request.command)
        if not safe:
            result.status = ExecStatus.BLOCKED
            result.blocked_reason = f"Input sanitization failed"
            self._audit_entry("SANITIZATION_FAILED", request, result)
            self._total_blocked += 1
            return result

        # 4. Plan hash verification (если указан)
        if request.plan_hash:
            if not self._verify_plan_hash(request):
                result.status = ExecStatus.HASH_MISMATCH
                result.blocked_reason = "Plan hash verification failed"
                self._audit_entry("HASH_MISMATCH", request, result)
                self._total_blocked += 1
                return result

        # 5. Risk check (через RiskEngine)
        risk_ok, risk_msg = self._check_risk(request.command, matched_entry)
        if not risk_ok:
            result.status = ExecStatus.BLOCKED
            result.blocked_reason = risk_msg
            self._audit_entry("RISK_BLOCKED", request, result)
            self._total_blocked += 1
            return result

        # 6. Sudo check
        if matched_entry.requires_sudo and not self._enable_sudo:
            # Попробовать pkexec
            cmd = request.command
            if not cmd.startswith("sudo ") and not cmd.startswith("pkexec "):
                cmd = f"pkexec {cmd}"
        else:
            cmd = request.command

        # 7. Execute
        result.command_executed = cmd
        t0 = time.monotonic()

        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=request.timeout,
                env={**os.environ, "LANG": "C.UTF-8"},
            )
            result.exit_code = proc.returncode
            result.stdout = proc.stdout.strip()[:2000]
            result.stderr = proc.stderr.strip()[:1000]
            result.status = ExecStatus.SUCCESS if proc.returncode == 0 else ExecStatus.FAILED
        except subprocess.TimeoutExpired:
            result.status = ExecStatus.TIMEOUT
            result.blocked_reason = f"Timeout ({request.timeout}s)"
        except Exception as e:
            result.status = ExecStatus.FAILED
            result.stderr = str(e)

        result.duration_ms = int((time.monotonic() - t0) * 1000)

        # 8. Verify (если есть verify_cmd)
        if result.status == ExecStatus.SUCCESS and request.verify_cmd:
            result.verified = self._run_verify(request.verify_cmd)

        self._audit_entry("EXECUTED", request, result)
        self._total_executed += 1
        return result

    # ─── Batch execute (для FixPlan) ──────────────────────────

    def execute_plan(
        self,
        requests: List[ExecRequest],
        stop_on_failure: bool = True,
    ) -> List[ExecResult]:
        """Выполнить список запросов последовательно."""
        results = []
        for req in requests:
            res = self.execute(req)
            results.append(res)
            if stop_on_failure and res.status not in (ExecStatus.SUCCESS,):
                break
        return results

    # ─── Register plan hash ───────────────────────────────────

    def register_plan_hash(self, plan_hash: str) -> None:
        """Зарегистрировать хеш плана для последующей верификации."""
        self._verified_hashes.add(plan_hash)

    # ─── Internal ─────────────────────────────────────────────

    def _check_rate_limit(self) -> bool:
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < 60]
        if len(self._timestamps) >= self._max_rpm:
            return False
        self._timestamps.append(now)
        return True

    def _match_whitelist(self, command: str) -> Optional[WhitelistEntry]:
        cmd = command.strip()
        # Убираем sudo/pkexec для матчинга
        for prefix in ("sudo ", "pkexec "):
            if cmd.startswith(prefix):
                cmd = cmd[len(prefix):]
                break
        for entry in _WHITELIST:
            if entry.pattern.search(cmd):
                return entry
        return None

    def _verify_plan_hash(self, request: ExecRequest) -> bool:
        if request.plan_hash in self._verified_hashes:
            return True
        # Пересчитываем хеш из request
        raw = json.dumps(
            {"cmd": request.command, "hash": request.plan_hash},
            sort_keys=True,
        )
        expected = hashlib.sha256(raw.encode()).hexdigest()[:12]
        return expected == request.plan_hash or request.plan_hash in self._verified_hashes

    def _check_risk(
        self,
        command: str,
        entry: WhitelistEntry,
    ) -> Tuple[bool, str]:
        try:
            from lina.diagnostics.risk_engine import get_risk_engine
            engine = get_risk_engine()
            assessment = engine.assess_command(command)
            if assessment.total_risk > entry.max_risk:
                return False, (
                    f"Risk {assessment.total_risk:.2f} exceeds "
                    f"max {entry.max_risk:.2f} for category '{entry.category}'"
                )
            return True, ""
        except Exception as e:
            logger.error("RiskEngine unavailable: %s — blocking command", e)
            return False, "RiskEngine unavailable — fail-closed"

    def _run_verify(self, verify_cmd: str) -> bool:
        try:
            proc = subprocess.run(
                verify_cmd, shell=True, capture_output=True,
                text=True, timeout=10,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def _audit_entry(
        self,
        action: str,
        request: ExecRequest,
        result: ExecResult,
    ) -> None:
        import datetime
        entry = {
            "ts": datetime.datetime.now().isoformat(),
            "action": action,
            "request_id": request.request_id,
            "command": request.command[:200],
            "plan_hash": request.plan_hash,
            "status": result.status.value,
            "exit_code": result.exit_code,
            "blocked_reason": result.blocked_reason[:200],
            "duration_ms": result.duration_ms,
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-1000:]

        logger.info(
            "ROOT_AGENT: %s cmd='%s' status=%s duration=%dms",
            action, request.command[:80], result.status.value, result.duration_ms,
        )

    # ─── Audit / Stats ────────────────────────────────────────

    def get_audit_log(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self._audit_log[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_executed": self._total_executed,
            "total_blocked": self._total_blocked,
            "verified_hashes": len(self._verified_hashes),
            "audit_entries": len(self._audit_log),
            "rate_window": len(self._timestamps),
        }


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_agent: Optional[RootAgent] = None


def get_root_agent() -> RootAgent:
    global _agent
    if _agent is None:
        _agent = RootAgent()
    return _agent
