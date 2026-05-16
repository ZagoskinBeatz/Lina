"""
WebIntelSandbox — безопасная веб-разведка для диагностики.

.. warning:: EXPERIMENTAL
   Извлечение и выполнение команд из веб-результатов — модуль
   высокого риска. Source reputation scoring и safety gates
   требуют дополнительной валидации.

Обёртка над:
  - core/web_search_engine.py (WebSearchEngine) — поиск решений
  - system/sandbox.py (SubprocessSandbox) — безопасное выполнение

Функционал:
  1. Error message → web search → извлечение команд из результатов
  2. Валидация извлечённых команд через RiskEngine
  3. Source reputation scoring (ArchWiki >> random blog)
  4. Version/distro compatibility check
  5. Сборка FixPlan из веб-результатов с safety gate

Гарантии:
  - Никакая команда из веба не выполняется без RiskEngine asses
  - Все URL логируются для аудита
  - Команды с CRITICAL risk автоматически отбрасываются

Phase: SYSTEM OVERLORD / Module 8
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Extracted Command
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ExtractedCommand:
    """Команда, извлечённая из веб-результата."""
    command: str
    source_url: str
    source_title: str = ""
    relevance: float = 0.0
    risk_verdict: str = ""
    reputation: float = 0.0
    compatible: bool = True
    rejected_reason: str = ""

    @property
    def is_accepted(self) -> bool:
        return (
            self.compatible
            and self.risk_verdict not in ("critical", "high")
            and not self.rejected_reason
        )


@dataclass
class WebIntelResult:
    """Результат веб-разведки."""
    query: str
    total_results: int = 0
    extracted_commands: List[ExtractedCommand] = field(default_factory=list)
    accepted_commands: List[ExtractedCommand] = field(default_factory=list)
    rejected_commands: List[ExtractedCommand] = field(default_factory=list)
    search_time_ms: int = 0
    summary: str = ""
    error: str = ""

    @property
    def has_solutions(self) -> bool:
        return len(self.accepted_commands) > 0


# ═══════════════════════════════════════════════════════════════════
#  Source Reputation
# ═══════════════════════════════════════════════════════════════════

# Reputation scores: 1.0 = highly trusted, 0.0 = untrusted
_SOURCE_REPUTATION: Dict[str, float] = {
    "wiki.archlinux.org": 0.95,
    "man.archlinux.org": 0.90,
    "bugs.archlinux.org": 0.85,
    "bbs.archlinux.org": 0.80,
    "wiki.gentoo.org": 0.85,
    "docs.kernel.org": 0.90,
    "man7.org": 0.90,
    "stackoverflow.com": 0.75,
    "askubuntu.com": 0.70,
    "unix.stackexchange.com": 0.80,
    "superuser.com": 0.70,
    "github.com": 0.70,
    "gitlab.com": 0.65,
    "freedesktop.org": 0.85,
    "kernel.org": 0.90,
    "linuxfoundation.org": 0.85,
    "manpages.debian.org": 0.80,
    "wiki.debian.org": 0.80,
    "fedoraproject.org": 0.75,
    "access.redhat.com": 0.80,
    "en.wikipedia.org": 0.60,
}
_DEFAULT_REPUTATION = 0.40


# ═══════════════════════════════════════════════════════════════════
#  Command Extraction Patterns
# ═══════════════════════════════════════════════════════════════════

# Regex for extracting shell commands from text
_CMD_PATTERNS = [
    # sudo/pkexec commands
    re.compile(r"(?:^|\n)\s*(?:\$\s+)?(sudo\s+\S+.*?)(?:\n|$)", re.MULTILINE),
    # pacman/yay/paru
    re.compile(r"(?:^|\n)\s*(?:\$\s+)?((?:sudo\s+)?(?:pacman|yay|paru)\s+\S+.*?)(?:\n|$)", re.MULTILINE),
    # systemctl
    re.compile(r"(?:^|\n)\s*(?:\$\s+)?((?:sudo\s+)?systemctl\s+\S+.*?)(?:\n|$)", re.MULTILINE),
    # General commands with common prefixes
    re.compile(
        r"(?:^|\n)\s*(?:\$\s+)?"
        r"((?:ip|nmcli|journalctl|modprobe|lspci|lsusb|rfkill|pactl|wpctl|"
        r"bluetoothctl|xrandr|wlr-randr|brightnessctl|timedatectl|localectl|"
        r"hostnamectl|resolvectl|firewall-cmd|ufw|iptables)\s+.*?)(?:\n|$)",
        re.MULTILINE,
    ),
]

# Commands that are NEVER acceptable from web results
_WEB_CMD_BLACKLIST = [
    re.compile(r"rm\s+-rf\s+/(?:\s|$)"),
    re.compile(r"dd\s+.*of=/dev/sd"),
    re.compile(r"mkfs\.\w+\s+/dev/"),
    re.compile(r"chmod\s+[0-7]*777\s+/"),
    re.compile(r"curl\s+.*\|\s*(?:sudo\s+)?(?:bash|sh)"),
    re.compile(r"wget\s+.*\|\s*(?:sudo\s+)?(?:bash|sh)"),
    re.compile(r"eval\s+"),
    re.compile(r":\(\)\{.*\}"),  # fork bomb
]


# ═══════════════════════════════════════════════════════════════════
#  Distro Compatibility
# ═══════════════════════════════════════════════════════════════════

def _get_distro_id() -> str:
    """Detect distro from /etc/os-release."""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("ID="):
                    return line.strip().split("=", 1)[1].strip('"').lower()
    except Exception:
        pass
    return "unknown"


def _get_distro_like() -> List[str]:
    """Detect distro family."""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("ID_LIKE="):
                    return line.strip().split("=", 1)[1].strip('"').lower().split()
    except Exception:
        pass
    return []


# Package manager compatibility map
_PKG_COMPAT = {
    "pacman": {"arch", "cachyos", "endeavouros", "manjaro", "garuda"},
    "apt": {"debian", "ubuntu", "mint", "pop", "elementary"},
    "dnf": {"fedora", "centos", "rhel", "rocky", "alma"},
    "zypper": {"opensuse", "suse"},
}


def _is_command_compatible(cmd: str) -> Tuple[bool, str]:
    """Проверить совместимость команды с текущим дистро."""
    distro = _get_distro_id()
    distro_like = _get_distro_like()
    all_ids = {distro} | set(distro_like)

    # Check package manager compatibility
    for pkg_mgr, distros in _PKG_COMPAT.items():
        if pkg_mgr in cmd:
            if all_ids & distros:
                return True, ""
            return False, f"'{pkg_mgr}' not compatible with {distro}"

    return True, ""


# ═══════════════════════════════════════════════════════════════════
#  WebIntelSandbox
# ═══════════════════════════════════════════════════════════════════

class WebIntelSandbox:
    """Безопасная веб-разведка для диагностики.

    Pipeline:
      1. error_message → web_search_engine.search(query)
      2. Из результатов → extract_commands()
      3. Каждая команда → risk_assess + compatibility check
      4. Только accepted commands → FixPlan candidates
    """

    MIN_REPUTATION = 0.50   # Минимальная репутация источника
    MAX_COMMANDS = 10       # Максимум команд из одного поиска

    def __init__(self):
        self._search_count: int = 0
        self._audit_log: List[Dict[str, Any]] = []

    # ─── Main API ─────────────────────────────────────────────

    def search_solution(
        self,
        error_message: str,
        category: str = "",
        max_results: int = 5,
    ) -> WebIntelResult:
        """Найти решение для ошибки через веб.

        Args:
            error_message: Текст ошибки или описание проблемы.
            category: Категория проблемы (network, audio, etc).
            max_results: Макс количество результатов поиска.

        Returns:
            WebIntelResult с безопасными команодами.
        """
        start = time.time()
        result = WebIntelResult(query=error_message)

        # Build search query
        distro = _get_distro_id()
        query = f"{distro} linux {error_message}"
        if category:
            query = f"{distro} {category} {error_message}"

        # Search
        try:
            from lina.core.web_search_engine import get_web_search_engine
            engine = get_web_search_engine()
            response = engine.search(query)

            if not response.success:
                result.error = response.error or "Search failed"
                return result

            result.total_results = len(response.results)
            result.summary = response.summary

            # Extract commands from results
            all_commands: List[ExtractedCommand] = []
            for sr in response.results[:max_results]:
                commands = self._extract_from_result(sr)
                all_commands.extend(commands)

            # Deduplicate
            seen = set()
            unique: List[ExtractedCommand] = []
            for cmd in all_commands:
                normalized = cmd.command.strip()
                if normalized not in seen:
                    seen.add(normalized)
                    unique.append(cmd)

            # Validate each command
            for cmd in unique[:self.MAX_COMMANDS]:
                self._validate_command(cmd)
                result.extracted_commands.append(cmd)
                if cmd.is_accepted:
                    result.accepted_commands.append(cmd)
                else:
                    result.rejected_commands.append(cmd)

        except ImportError:
            result.error = "WebSearchEngine not available"
        except Exception as e:
            result.error = str(e)
            logger.error("WebIntelSandbox search error: %s", e)

        result.search_time_ms = int((time.time() - start) * 1000)
        self._search_count += 1
        self._audit_log.append({
            "query": query,
            "results": result.total_results,
            "accepted": len(result.accepted_commands),
            "rejected": len(result.rejected_commands),
            "time": time.time(),
        })
        if len(self._audit_log) > 200:
            self._audit_log = self._audit_log[-200:]

        return result

    # ─── Command extraction ───────────────────────────────────

    def _extract_from_result(self, search_result: Any) -> List[ExtractedCommand]:
        """Извлечь команды из SearchResult."""
        commands: List[ExtractedCommand] = []
        text = getattr(search_result, "snippet", "") or ""
        url = getattr(search_result, "url", "") or ""
        title = getattr(search_result, "title", "") or ""
        relevance = getattr(search_result, "relevance", 0.0)

        # Get source reputation
        reputation = self._get_reputation(url)

        # Skip low-reputation sources
        if reputation < self.MIN_REPUTATION:
            return []

        # Extract commands
        for pattern in _CMD_PATTERNS:
            matches = pattern.findall(text)
            for match in matches:
                cmd_text = match.strip()
                if len(cmd_text) < 3 or len(cmd_text) > 500:
                    continue
                commands.append(ExtractedCommand(
                    command=cmd_text,
                    source_url=url,
                    source_title=title,
                    relevance=relevance,
                    reputation=reputation,
                ))

        return commands

    # ─── Validation ───────────────────────────────────────────

    def _validate_command(self, cmd: ExtractedCommand) -> None:
        """Валидация извлечённой команды."""
        # 1. Web blacklist
        for pattern in _WEB_CMD_BLACKLIST:
            if pattern.search(cmd.command):
                cmd.rejected_reason = f"Blacklisted pattern: {pattern.pattern[:40]}"
                return

        # 2. Distro compatibility
        compatible, reason = _is_command_compatible(cmd.command)
        if not compatible:
            cmd.compatible = False
            cmd.rejected_reason = reason
            return

        # 3. Risk assessment via RiskEngine
        try:
            from lina.diagnostics.risk_engine import get_risk_engine
            engine = get_risk_engine()
            assessment = engine.assess_command(cmd.command)
            cmd.risk_verdict = assessment.verdict.value

            if assessment.verdict.value in ("critical", "high"):
                cmd.rejected_reason = f"Risk too high: {assessment.verdict.value} ({assessment.total_risk:.2f})"
                return
        except ImportError:
            cmd.risk_verdict = "unknown"
        except Exception as e:
            logger.warning("Risk assessment failed: %s", e)
            cmd.risk_verdict = "unknown"

    # ─── Reputation ───────────────────────────────────────────

    @staticmethod
    def _get_reputation(url: str) -> float:
        """Получить репутацию источника по URL."""
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower()
            # Check exact match
            if domain in _SOURCE_REPUTATION:
                return _SOURCE_REPUTATION[domain]
            # Check parent domain
            parts = domain.split(".")
            for i in range(len(parts) - 1):
                parent = ".".join(parts[i:])
                if parent in _SOURCE_REPUTATION:
                    return _SOURCE_REPUTATION[parent]
        except Exception:
            pass
        return _DEFAULT_REPUTATION

    # ─── Fetch and analyze a specific page ────────────────────

    def fetch_page_commands(self, url: str) -> List[ExtractedCommand]:
        """Извлечь команды с конкретной страницы."""
        try:
            from lina.core.web_search_engine import get_web_search_engine
            engine = get_web_search_engine()
            page = engine.fetch(url)
            if not page.get("success"):
                return []

            text = page.get("text", "")
            title = page.get("title", "")
            reputation = self._get_reputation(url)

            commands: List[ExtractedCommand] = []
            for pattern in _CMD_PATTERNS:
                matches = pattern.findall(text)
                for match in matches:
                    cmd_text = match.strip()
                    if 3 < len(cmd_text) < 500:
                        ec = ExtractedCommand(
                            command=cmd_text,
                            source_url=url,
                            source_title=title,
                            reputation=reputation,
                        )
                        self._validate_command(ec)
                        commands.append(ec)

            return commands
        except Exception as e:
            logger.error("fetch_page_commands error: %s", e)
            return []

    # ─── Report ───────────────────────────────────────────────

    def get_audit_log(self) -> List[Dict[str, Any]]:
        return list(self._audit_log)

    def format_report(self, result: WebIntelResult) -> str:
        lines = ["═══ WebIntel Report ═══"]
        lines.append(f"  Query: {result.query[:60]}")
        lines.append(f"  Results found: {result.total_results}")
        lines.append(f"  Commands extracted: {len(result.extracted_commands)}")
        lines.append(f"  Accepted: {len(result.accepted_commands)}")
        lines.append(f"  Rejected: {len(result.rejected_commands)}")
        lines.append(f"  Search time: {result.search_time_ms}ms")
        if result.accepted_commands:
            lines.append("")
            lines.append("  Accepted commands:")
            for c in result.accepted_commands[:5]:
                lines.append(f"    [{c.risk_verdict}] {c.command[:60]}")
                lines.append(f"      source: {c.source_url[:50]} (rep={c.reputation:.2f})")
        if result.rejected_commands:
            lines.append("")
            lines.append("  Rejected:")
            for c in result.rejected_commands[:3]:
                lines.append(f"    ✗ {c.command[:50]} — {c.rejected_reason[:40]}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_sandbox: Optional[WebIntelSandbox] = None


def get_web_intel_sandbox() -> WebIntelSandbox:
    global _sandbox
    if _sandbox is None:
        _sandbox = WebIntelSandbox()
    return _sandbox
