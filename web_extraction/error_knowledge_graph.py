# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Error Knowledge Graph.

Structured database of Linux errors and their known solutions.

Data model per entry:
  - error_pattern: regex matching the error string
  - description: human-readable description
  - causes: list of possible causes
  - solutions: list of Solution objects (steps + commands + confidence)
  - sources: list of URLs where this solution was verified
  - trust_score: aggregated trust from sources

Lookup flow:
  1. User query or page text → ErrorDetector → normalized error strings
  2. Normalized strings → ErrorKnowledgeGraph.lookup()
  3. If match found with high confidence → return solutions directly
  4. If no match → proceed to web retrieval pipeline
  5. After web retrieval → learn new solutions into the graph

The graph is stored as a JSON file on disk and loaded lazily on first query.
New entries are added atomically to avoid corruption.

No LLM calls. Fully deterministic.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set

logger = logging.getLogger("lina.web_extraction.error_knowledge_graph")


# ═══════════════════════════════════════════════════
#  Data Model
# ═══════════════════════════════════════════════════

@dataclass
class KnownSolution:
    """A verified solution for a known error."""
    description: str                         # Solution description
    commands: List[str] = field(default_factory=list)  # Commands to execute
    steps: List[str] = field(default_factory=list)     # Ordered steps
    confidence: float = 0.5                  # How reliable is this solution
    applicable_distros: List[str] = field(default_factory=list)  # ["ubuntu", "debian", "arch", ...]
    sources: List[str] = field(default_factory=list)   # URLs where confirmed
    times_confirmed: int = 1                 # How many times confirmed
    last_confirmed: float = 0.0              # Unix timestamp
    added_at: float = 0.0                    # When first added

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "KnownSolution":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ErrorEntry:
    """A known error with its solutions."""
    error_id: str                            # Unique ID (slugified error type)
    pattern: str                             # Regex pattern matching this error
    description: str                         # Human-readable description
    category: str = ""                       # pkg, service, network, fs, driver, kernel, etc.
    causes: List[str] = field(default_factory=list)
    solutions: List[KnownSolution] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)  # Alternative error strings
    trust_score: float = 0.0                 # Aggregated trust
    times_queried: int = 0                   # Usage counter
    last_queried: float = 0.0

    @property
    def has_verified_solutions(self) -> bool:
        return any(s.confidence >= 0.60 for s in self.solutions)

    @property
    def best_solution(self) -> Optional[KnownSolution]:
        if not self.solutions:
            return None
        return max(self.solutions, key=lambda s: s.confidence)

    @property
    def total_commands(self) -> int:
        return sum(len(s.commands) for s in self.solutions)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["solutions"] = [s.to_dict() for s in self.solutions]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ErrorEntry":
        solutions_data = data.pop("solutions", [])
        entry = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        entry.solutions = [KnownSolution.from_dict(s) for s in solutions_data]
        return entry


@dataclass
class LookupResult:
    """Result of an Error Knowledge Graph lookup."""
    found: bool = False
    entry: Optional[ErrorEntry] = None
    match_quality: float = 0.0          # How well the query matched
    can_answer_directly: bool = False    # True if high-confidence solutions exist
    suggested_commands: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════
#  Built-in Error Knowledge Base
# ═══════════════════════════════════════════════════

def _build_initial_entries() -> List[ErrorEntry]:
    """Build the initial set of well-known Linux errors and solutions."""
    entries: List[ErrorEntry] = []

    # ── APT: Unable to locate package ──
    entries.append(ErrorEntry(
        error_id="apt_unable_to_locate",
        pattern=r"unable to locate package",
        description="APT cannot find the specified package in configured repositories",
        category="pkg",
        causes=[
            "Package name is misspelled",
            "Repository is not enabled (universe/multiverse on Ubuntu)",
            "Package list is outdated (apt update not run)",
            "Package does not exist for this distro/version",
            "PPA/third-party repo not added",
        ],
        solutions=[
            KnownSolution(
                description="Update package lists and retry",
                commands=["sudo apt update", "sudo apt install <package>"],
                steps=["Run apt update to refresh package lists", "Retry the install command"],
                confidence=0.70,
                applicable_distros=["ubuntu", "debian", "mint"],
                times_confirmed=50,
            ),
            KnownSolution(
                description="Enable universe/multiverse repositories (Ubuntu)",
                commands=[
                    "sudo add-apt-repository universe",
                    "sudo add-apt-repository multiverse",
                    "sudo apt update",
                ],
                steps=["Enable the universe and multiverse repositories", "Update package lists"],
                confidence=0.55,
                applicable_distros=["ubuntu"],
                times_confirmed=20,
            ),
            KnownSolution(
                description="Check package name spelling",
                commands=["apt-cache search <keyword>"],
                steps=["Search for the correct package name using apt-cache search"],
                confidence=0.65,
                applicable_distros=["ubuntu", "debian", "mint"],
                times_confirmed=30,
            ),
        ],
        aliases=["e: unable to locate package", "unable to locate package"],
        trust_score=0.80,
    ))

    # ── APT: Unmet dependencies ──
    entries.append(ErrorEntry(
        error_id="apt_unmet_deps",
        pattern=r"unmet dependencies|dependency .+ is not satisfiable",
        description="Package has unresolvable dependencies in current repository state",
        category="pkg",
        causes=[
            "Partial upgrade left system in inconsistent state",
            "Mixing repositories from different releases",
            "PPA packages conflict with official repos",
            "Broken package installation interrupted",
        ],
        solutions=[
            KnownSolution(
                description="Fix broken packages with apt",
                commands=["sudo apt --fix-broken install", "sudo dpkg --configure -a", "sudo apt update && sudo apt upgrade"],
                steps=["Fix broken installations", "Reconfigure pending packages", "Update and upgrade all packages"],
                confidence=0.75,
                applicable_distros=["ubuntu", "debian", "mint"],
                times_confirmed=40,
            ),
            KnownSolution(
                description="Use aptitude to resolve dependency conflicts",
                commands=["sudo aptitude install <package>"],
                steps=["Use aptitude which has a better dependency resolver"],
                confidence=0.55,
                applicable_distros=["ubuntu", "debian"],
                times_confirmed=15,
            ),
        ],
        trust_score=0.75,
    ))

    # ── Permission denied ──
    entries.append(ErrorEntry(
        error_id="permission_denied",
        pattern=r"permission denied",
        description="Operation requires elevated privileges or file permissions are wrong",
        category="permission",
        causes=[
            "Command needs sudo/root privileges",
            "File/directory has restrictive permissions",
            "User not in required group (docker, sudo, etc.)",
            "SELinux/AppArmor denying access",
            "Filesystem mounted as read-only",
        ],
        solutions=[
            KnownSolution(
                description="Run command with sudo",
                commands=["sudo <command>"],
                steps=["Prefix the command with sudo"],
                confidence=0.60,
                times_confirmed=100,
            ),
            KnownSolution(
                description="Fix file permissions",
                commands=["ls -la <path>", "sudo chmod 755 <path>", "sudo chown $USER:$USER <path>"],
                steps=["Check current permissions", "Set appropriate permissions", "Change ownership if needed"],
                confidence=0.55,
                times_confirmed=30,
            ),
            KnownSolution(
                description="Add user to required group",
                commands=["sudo usermod -aG <group> $USER", "newgrp <group>"],
                steps=["Add user to the group", "Apply group membership without logout"],
                confidence=0.50,
                applicable_distros=["ubuntu", "debian", "arch", "fedora"],
                times_confirmed=25,
            ),
        ],
        trust_score=0.70,
    ))

    # ── Failed to start service ──
    entries.append(ErrorEntry(
        error_id="service_start_fail",
        pattern=r"failed to start|job for .+ failed",
        description="Systemd service failed to start",
        category="service",
        causes=[
            "Configuration file syntax error",
            "Port already in use by another service",
            "Missing dependencies or files",
            "Permission issues on service files",
            "Service binary not found or not executable",
        ],
        solutions=[
            KnownSolution(
                description="Check service status and logs",
                commands=[
                    "systemctl status <service>",
                    "journalctl -xeu <service>",
                    "journalctl -u <service> --since '5 minutes ago'",
                ],
                steps=["Check service status for error details", "Read full journal logs for the service"],
                confidence=0.75,
                times_confirmed=60,
            ),
            KnownSolution(
                description="Check and fix configuration",
                commands=[
                    "sudo <service> -t",
                    "sudo nginx -t",
                    "sudo apachectl configtest",
                ],
                steps=["Test configuration syntax", "Fix any reported errors", "Restart the service"],
                confidence=0.60,
                times_confirmed=25,
            ),
            KnownSolution(
                description="Restart and re-enable service",
                commands=[
                    "sudo systemctl daemon-reload",
                    "sudo systemctl restart <service>",
                    "sudo systemctl enable <service>",
                ],
                steps=["Reload systemd daemon", "Restart the service", "Enable on boot"],
                confidence=0.55,
                times_confirmed=40,
            ),
        ],
        trust_score=0.75,
    ))

    # ── No space left on device ──
    entries.append(ErrorEntry(
        error_id="no_space",
        pattern=r"no space left on device",
        description="Filesystem is full, no free space for write operations",
        category="fs",
        causes=[
            "Disk partition is full",
            "/var/log filled with logs",
            "/tmp full of temporary files",
            "Old kernel/package cache filling /boot",
            "Inode exhaustion (many small files)",
        ],
        solutions=[
            KnownSolution(
                description="Find and clean large files/directories",
                commands=[
                    "df -h",
                    "du -sh /* 2>/dev/null | sort -rh | head -20",
                    "sudo journalctl --vacuum-size=100M",
                    "sudo apt autoremove --purge",
                    "sudo apt clean",
                ],
                steps=[
                    "Check disk usage by partition",
                    "Find largest directories",
                    "Clean system journal logs",
                    "Remove unused packages",
                    "Clean package cache",
                ],
                confidence=0.80,
                applicable_distros=["ubuntu", "debian", "mint"],
                times_confirmed=50,
            ),
            KnownSolution(
                description="Clean old kernels (Ubuntu/Debian) to free /boot",
                commands=[
                    "dpkg --list | grep linux-image | grep -v $(uname -r)",
                    "sudo apt autoremove --purge",
                ],
                steps=["List installed kernels", "Remove old kernels"],
                confidence=0.65,
                applicable_distros=["ubuntu", "debian"],
                times_confirmed=20,
            ),
        ],
        trust_score=0.80,
    ))

    # ── Connection refused ──
    entries.append(ErrorEntry(
        error_id="connection_refused",
        pattern=r"connection refused|connect: connection refused",
        description="Remote host or service actively refused the connection",
        category="network",
        causes=[
            "Service is not running on the target host/port",
            "Firewall blocking the port",
            "Service is listening on different interface (localhost only)",
            "Wrong port number",
        ],
        solutions=[
            KnownSolution(
                description="Check if service is running and listening",
                commands=[
                    "systemctl status <service>",
                    "ss -tlnp | grep <port>",
                    "sudo ufw status",
                    "sudo iptables -L -n",
                ],
                steps=[
                    "Verify the service is running",
                    "Check which ports are open",
                    "Check firewall rules",
                ],
                confidence=0.70,
                times_confirmed=35,
            ),
            KnownSolution(
                description="Start the service and open firewall port",
                commands=[
                    "sudo systemctl start <service>",
                    "sudo ufw allow <port>",
                ],
                steps=["Start the service", "Open the port in firewall"],
                confidence=0.55,
                times_confirmed=20,
            ),
        ],
        trust_score=0.70,
    ))

    # ── DNS resolution failure ──
    entries.append(ErrorEntry(
        error_id="dns_fail",
        pattern=r"name or service not known|could not resolve host|temporary failure in name resolution",
        description="DNS resolution failed — cannot convert hostname to IP address",
        category="network",
        causes=[
            "No network connectivity",
            "DNS server is down or unreachable",
            "/etc/resolv.conf is empty or misconfigured",
            "systemd-resolved service not running",
            "DHCP not providing DNS servers",
        ],
        solutions=[
            KnownSolution(
                description="Check network and DNS configuration",
                commands=[
                    "ping -c 3 8.8.8.8",
                    "cat /etc/resolv.conf",
                    "systemctl status systemd-resolved",
                    "resolvectl status",
                ],
                steps=[
                    "Test basic connectivity (ping by IP)",
                    "Check DNS configuration",
                    "Check systemd-resolved status",
                ],
                confidence=0.70,
                times_confirmed=30,
            ),
            KnownSolution(
                description="Set manual DNS servers",
                commands=[
                    "echo 'nameserver 8.8.8.8' | sudo tee /etc/resolv.conf",
                    "echo 'nameserver 1.1.1.1' | sudo tee -a /etc/resolv.conf",
                ],
                steps=["Set Google and Cloudflare DNS as resolvers"],
                confidence=0.60,
                times_confirmed=25,
            ),
            KnownSolution(
                description="Restart network services",
                commands=[
                    "sudo systemctl restart systemd-resolved",
                    "sudo systemctl restart NetworkManager",
                ],
                steps=["Restart DNS resolver", "Restart NetworkManager"],
                confidence=0.55,
                times_confirmed=20,
            ),
        ],
        trust_score=0.75,
    ))

    # ── Kernel module not found ──
    entries.append(ErrorEntry(
        error_id="module_not_found",
        pattern=r"module .+ not found|fatal:\s*module .+ not found",
        description="Kernel module is not available for the running kernel",
        category="kernel",
        causes=[
            "Module not installed for current kernel version",
            "Kernel headers not installed (needed for DKMS)",
            "Module name is wrong",
            "Module was removed/blacklisted",
        ],
        solutions=[
            KnownSolution(
                description="Install kernel headers and rebuild modules (Debian/Ubuntu)",
                commands=[
                    "uname -r",
                    "sudo apt install linux-headers-$(uname -r)",
                    "sudo dkms autoinstall",
                ],
                steps=["Check kernel version", "Install matching headers", "Rebuild DKMS modules"],
                confidence=0.65,
                applicable_distros=["ubuntu", "debian", "mint"],
                times_confirmed=20,
            ),
            KnownSolution(
                description="Install kernel headers (Arch)",
                commands=[
                    "uname -r",
                    "sudo pacman -S linux-headers",
                ],
                steps=["Check kernel version", "Install kernel headers"],
                confidence=0.65,
                applicable_distros=["arch", "manjaro"],
                times_confirmed=15,
            ),
            KnownSolution(
                description="Check if module is blacklisted",
                commands=[
                    "grep -r <module> /etc/modprobe.d/",
                    "sudo modprobe <module>",
                ],
                steps=["Check blacklist files", "Try loading the module manually"],
                confidence=0.55,
                times_confirmed=10,
            ),
        ],
        trust_score=0.70,
    ))

    # ── Command not found ──
    entries.append(ErrorEntry(
        error_id="cmd_not_found",
        pattern=r"command not found",
        description="Shell cannot find the requested command/executable",
        category="pkg",
        causes=[
            "Package providing the command is not installed",
            "Command is not in PATH",
            "Typo in command name",
            "Binary exists but not executable",
        ],
        solutions=[
            KnownSolution(
                description="Find and install the package providing this command (Debian/Ubuntu)",
                commands=[
                    "apt-file search <command>",
                    "sudo apt install command-not-found && update-command-not-found",
                    "sudo apt install <package>",
                ],
                steps=["Search which package provides the command", "Install the package"],
                confidence=0.65,
                applicable_distros=["ubuntu", "debian"],
                times_confirmed=30,
            ),
            KnownSolution(
                description="Find and install the package (Arch)",
                commands=[
                    "pacman -F <command>",
                    "sudo pacman -S <package>",
                ],
                steps=["Search which package provides the command", "Install the package"],
                confidence=0.65,
                applicable_distros=["arch", "manjaro"],
                times_confirmed=15,
            ),
            KnownSolution(
                description="Check PATH and locate binary",
                commands=[
                    "echo $PATH",
                    "which <command>",
                    "find / -name <command> 2>/dev/null",
                ],
                steps=["Verify PATH includes standard directories", "Search for the binary"],
                confidence=0.50,
                times_confirmed=20,
            ),
        ],
        trust_score=0.70,
    ))

    # ── dpkg interrupted ──
    entries.append(ErrorEntry(
        error_id="dpkg_interrupted",
        pattern=r"dpkg was interrupted|you must manually run.*dpkg.*configure",
        description="dpkg was interrupted and left packages in unconfigured state",
        category="pkg",
        causes=[
            "Previous installation was interrupted (Ctrl+C, power loss, etc.)",
            "System crashed during package operation",
        ],
        solutions=[
            KnownSolution(
                description="Reconfigure pending packages",
                commands=[
                    "sudo dpkg --configure -a",
                    "sudo apt --fix-broken install",
                    "sudo apt update && sudo apt upgrade",
                ],
                steps=["Configure pending packages", "Fix broken installations", "Update system"],
                confidence=0.85,
                applicable_distros=["ubuntu", "debian", "mint"],
                times_confirmed=50,
            ),
        ],
        trust_score=0.85,
    ))

    # ── OOM Killer ──
    entries.append(ErrorEntry(
        error_id="oom_killer",
        pattern=r"out of memory|oom[_-]?killer|killed process",
        description="Kernel OOM killer terminated a process due to memory exhaustion",
        category="kernel",
        causes=[
            "Not enough RAM for running applications",
            "Memory leak in application",
            "No swap or swap too small",
            "Too many processes consuming memory",
        ],
        solutions=[
            KnownSolution(
                description="Check memory usage and identify the culprit",
                commands=[
                    "free -h",
                    "dmesg | grep -i oom",
                    "journalctl -k | grep -i 'out of memory'",
                    "ps aux --sort=-%mem | head -20",
                ],
                steps=["Check current memory usage", "Find OOM events in kernel log", "Identify top memory consumers"],
                confidence=0.70,
                times_confirmed=25,
            ),
            KnownSolution(
                description="Add or increase swap space",
                commands=[
                    "sudo fallocate -l 4G /swapfile",
                    "sudo chmod 600 /swapfile",
                    "sudo mkswap /swapfile",
                    "sudo swapon /swapfile",
                    "echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab",
                ],
                steps=["Create a swap file", "Set permissions", "Format as swap", "Enable swap", "Make persistent"],
                confidence=0.65,
                times_confirmed=20,
            ),
        ],
        trust_score=0.75,
    ))

    return entries


# ═══════════════════════════════════════════════════
#  Error Knowledge Graph
# ═══════════════════════════════════════════════════

class ErrorKnowledgeGraph:
    """
    Structured database of Linux errors and their verified solutions.

    Provides:
      - Fast lookup of known errors by normalized error string
      - Learning: add new solutions from web retrieval results
      - Confidence-based answer: if high enough → skip web search
      - Persistent storage in JSON file

    Usage:
        graph = ErrorKnowledgeGraph()
        result = graph.lookup("unable to locate package nginx")
        if result.can_answer_directly:
            for sol in result.entry.solutions:
                print(sol.description, sol.commands)
        else:
            # Proceed with web search
            ...

        # After web search, learn new solution
        graph.learn(
            error_key="unable to locate package",
            solution=KnownSolution(
                description="Add PPA and retry",
                commands=["sudo add-apt-repository ppa:...", "sudo apt update"],
                confidence=0.50,
                sources=["https://..."],
            ),
        )
    """

    def __init__(
        self,
        data_dir: str | Path | None = None,
        min_confidence_for_direct: float = 0.65,
        min_confirmations_for_direct: int = 2,
    ):
        """
        Args:
            data_dir: Directory for persistent storage. If None, uses
                      lina/cache/error_knowledge_graph.json.
            min_confidence_for_direct: Minimum solution confidence to
                      answer directly without web search.
            min_confirmations_for_direct: Minimum confirmation count.
        """
        self._min_conf_direct = min_confidence_for_direct
        self._min_confirms_direct = min_confirmations_for_direct

        # Storage path
        if data_dir is None:
            base = Path(__file__).resolve().parent.parent / "cache"
        else:
            base = Path(data_dir)
        base.mkdir(parents=True, exist_ok=True)
        self._db_path = base / "error_knowledge_graph.json"

        self._lock = threading.Lock()
        self._entries: Dict[str, ErrorEntry] = {}
        self._pattern_cache: List[Tuple[re.Pattern, str]] = []
        self._loaded = False

    def _ensure_loaded(self):
        """Lazy-load the knowledge graph."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load()
            self._loaded = True

    def _load(self):
        """Load entries from disk + built-in database."""
        # Start with built-in entries
        for entry in _build_initial_entries():
            self._entries[entry.error_id] = entry

        # Overlay with persistent entries from disk
        if self._db_path.exists():
            try:
                with open(self._db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for entry_data in data.get("entries", []):
                    entry = ErrorEntry.from_dict(entry_data)
                    if entry.error_id in self._entries:
                        # Merge: add new solutions from disk to built-in
                        existing = self._entries[entry.error_id]
                        self._merge_solutions(existing, entry)
                    else:
                        self._entries[entry.error_id] = entry
                logger.info(
                    "ErrorKnowledgeGraph: loaded %d persistent entries from %s",
                    len(data.get("entries", [])), self._db_path,
                )
            except Exception as e:
                logger.warning("ErrorKnowledgeGraph: failed to load %s: %s", self._db_path, e)

        # Build pattern cache for fast matching
        self._pattern_cache = []
        for eid, entry in self._entries.items():
            try:
                pat = re.compile(entry.pattern, re.IGNORECASE)
                self._pattern_cache.append((pat, eid))
            except re.error:
                logger.warning("Bad pattern in entry %s: %s", eid, entry.pattern)

        logger.info(
            "ErrorKnowledgeGraph: %d entries, %d patterns ready",
            len(self._entries), len(self._pattern_cache),
        )

    def _merge_solutions(self, existing: ErrorEntry, new: ErrorEntry):
        """Merge solutions from `new` into `existing`."""
        existing_descs = {s.description.lower() for s in existing.solutions}
        for sol in new.solutions:
            if sol.description.lower() not in existing_descs:
                existing.solutions.append(sol)
                existing_descs.add(sol.description.lower())

    # ═══════════════════════════════════════════════
    #  Lookup
    # ═══════════════════════════════════════════════

    def lookup(self, error_text: str) -> LookupResult:
        """
        Look up an error string in the knowledge graph.

        Args:
            error_text: Normalized error string (from ErrorDetector).

        Returns:
            LookupResult with matched entry and answer-direct flag.
        """
        self._ensure_loaded()

        if not error_text:
            return LookupResult()

        normalized = error_text.lower().strip()

        # Match against patterns
        best_match: Optional[str] = None
        best_quality = 0.0

        for pattern, eid in self._pattern_cache:
            if pattern.search(normalized):
                # Compute match quality based on pattern specificity
                quality = len(pattern.pattern) / max(len(normalized), 1)
                quality = min(quality, 1.0)
                if quality > best_quality:
                    best_quality = quality
                    best_match = eid

        # Also check aliases
        if best_match is None:
            for eid, entry in self._entries.items():
                for alias in entry.aliases:
                    if alias.lower() in normalized or normalized in alias.lower():
                        ratio = min(len(alias), len(normalized)) / max(len(alias), len(normalized), 1)
                        if ratio > best_quality:
                            best_quality = ratio
                            best_match = eid

        if best_match is None:
            return LookupResult()

        entry = self._entries[best_match]
        entry.times_queried += 1
        entry.last_queried = time.time()

        # Determine if we can answer directly
        can_direct = (
            entry.has_verified_solutions
            and entry.best_solution is not None
            and entry.best_solution.confidence >= self._min_conf_direct
            and entry.best_solution.times_confirmed >= self._min_confirms_direct
        )

        # Gather commands from top solutions
        suggested_commands: List[str] = []
        for sol in sorted(entry.solutions, key=lambda s: s.confidence, reverse=True)[:3]:
            suggested_commands.extend(sol.commands)

        return LookupResult(
            found=True,
            entry=entry,
            match_quality=round(best_quality, 3),
            can_answer_directly=can_direct,
            suggested_commands=suggested_commands,
        )

    def lookup_multiple(self, error_keys: List[str]) -> List[LookupResult]:
        """
        Look up multiple error strings.

        Args:
            error_keys: List of normalized error strings.

        Returns:
            List of LookupResult for each key (in same order).
        """
        return [self.lookup(key) for key in error_keys]

    # ═══════════════════════════════════════════════
    #  Learning
    # ═══════════════════════════════════════════════

    def learn(
        self,
        error_key: str,
        solution: KnownSolution,
        error_type: str = "",
        category: str = "",
    ):
        """
        Add a new solution to the knowledge graph (from web retrieval).

        If the error already exists, the solution is merged.
        If it's new, a new entry is created.

        Args:
            error_key: Normalized error string.
            solution: The solution to add.
            error_type: Error type ID (from ErrorDetector).
            category: Category for new entries.
        """
        self._ensure_loaded()

        solution.added_at = solution.added_at or time.time()
        solution.last_confirmed = time.time()

        with self._lock:
            # Find matching entry
            result = self.lookup(error_key)
            if result.found and result.entry:
                # Merge into existing entry
                existing_descs = {s.description.lower() for s in result.entry.solutions}
                if solution.description.lower() in existing_descs:
                    # Update existing solution
                    for s in result.entry.solutions:
                        if s.description.lower() == solution.description.lower():
                            s.times_confirmed += 1
                            s.last_confirmed = time.time()
                            s.confidence = min(s.confidence + 0.05, 0.99)
                            s.sources = list(set(s.sources + solution.sources))
                            break
                else:
                    result.entry.solutions.append(solution)
            else:
                # Create new entry
                error_id = error_type or re.sub(r'[^a-z0-9]+', '_', error_key.lower())[:60]
                entry = ErrorEntry(
                    error_id=error_id,
                    pattern=re.escape(error_key.lower()),
                    description=error_key,
                    category=category,
                    solutions=[solution],
                    trust_score=solution.confidence * 0.5,
                )
                self._entries[error_id] = entry
                # Update pattern cache
                try:
                    pat = re.compile(entry.pattern, re.IGNORECASE)
                    self._pattern_cache.append((pat, error_id))
                except re.error:
                    pass

            self._save()

    def _save(self):
        """Persist the knowledge graph to disk (atomically)."""
        try:
            # Only save non-built-in entries and built-in with modifications
            entries_data = []
            for eid, entry in self._entries.items():
                if entry.times_queried > 0 or any(s.added_at > 0 for s in entry.solutions):
                    entries_data.append(entry.to_dict())

            data = {
                "version": 1,
                "updated_at": time.time(),
                "entry_count": len(entries_data),
                "entries": entries_data,
            }

            tmp_path = self._db_path.with_suffix('.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self._db_path)
        except Exception as e:
            logger.warning("ErrorKnowledgeGraph: failed to save: %s", e)

    # ═══════════════════════════════════════════════
    #  Query Interface
    # ═══════════════════════════════════════════════

    def get_entry(self, error_id: str) -> Optional[ErrorEntry]:
        """Get entry by ID."""
        self._ensure_loaded()
        return self._entries.get(error_id)

    def get_all_entries(self) -> List[ErrorEntry]:
        """Get all entries (for admin/debug)."""
        self._ensure_loaded()
        return list(self._entries.values())

    @property
    def entry_count(self) -> int:
        """Number of known error patterns."""
        self._ensure_loaded()
        return len(self._entries)

    @property
    def total_solutions(self) -> int:
        """Total solutions across all entries."""
        self._ensure_loaded()
        return sum(len(e.solutions) for e in self._entries.values())

    def get_stats(self) -> Dict[str, int]:
        """Get knowledge graph statistics."""
        self._ensure_loaded()
        return {
            "entries": len(self._entries),
            "solutions": self.total_solutions,
            "categories": len(set(e.category for e in self._entries.values() if e.category)),
            "verified_entries": sum(1 for e in self._entries.values() if e.has_verified_solutions),
        }


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_graph: ErrorKnowledgeGraph | None = None


def get_error_knowledge_graph() -> ErrorKnowledgeGraph:
    """Get or create singleton Error Knowledge Graph."""
    global _graph
    if _graph is None:
        _graph = ErrorKnowledgeGraph()
    return _graph
