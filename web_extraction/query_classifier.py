# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Query Classifier.

Deterministic heuristic classifier that routes user queries into one of
three processing modes:

  GENERAL   — standard web search + RAG (default)
  LINUX     — Linux troubleshooting mode (enhanced extraction)
  ERROR     — error-centric mode (Error Knowledge Graph lookup first)

Classification is performed WITHOUT any LLM call — purely via keyword
detection, regex patterns, and scoring heuristics.

Algorithm:
  1. Detect Linux keywords, command patterns, package manager invocations
  2. Detect error strings (standard prefixes, exit codes, errno, etc.)
  3. Compute weighted score for each mode
  4. Classify by highest score with confidence threshold

Decision table:
  score_linux > 0.40  AND  has_error  →  ERROR mode
  score_linux > 0.40                  →  LINUX mode
  has_error  AND  error looks Linux   →  ERROR mode
  else                                →  GENERAL mode
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple, Set

logger = logging.getLogger("lina.web_extraction.query_classifier")


# ═══════════════════════════════════════════════════
#  Query Mode Enum
# ═══════════════════════════════════════════════════

class QueryMode(Enum):
    """Processing mode for a user query."""
    GENERAL = "general"
    LINUX = "linux"
    ERROR = "error"


# ═══════════════════════════════════════════════════
#  Classification Result
# ═══════════════════════════════════════════════════

@dataclass
class QueryClassification:
    """Result of query classification."""
    mode: QueryMode = QueryMode.GENERAL
    confidence: float = 0.0

    # Detected signals
    linux_keywords: List[str] = field(default_factory=list)
    linux_commands: List[str] = field(default_factory=list)
    error_strings: List[str] = field(default_factory=list)
    package_managers: List[str] = field(default_factory=list)

    # Scores
    linux_score: float = 0.0
    error_score: float = 0.0

    @property
    def is_linux(self) -> bool:
        return self.mode in (QueryMode.LINUX, QueryMode.ERROR)

    @property
    def is_error(self) -> bool:
        return self.mode == QueryMode.ERROR

    @property
    def has_commands(self) -> bool:
        return bool(self.linux_commands)


# ═══════════════════════════════════════════════════
#  Keyword & Pattern Databases
# ═══════════════════════════════════════════════════

# ── Linux keywords (weighted) ──
# (keyword, weight) — higher weight = stronger Linux signal
_LINUX_KEYWORDS: List[Tuple[str, float]] = [
    # System management
    ("systemctl", 0.90), ("journalctl", 0.90), ("dmesg", 0.85),
    ("kernel", 0.70), ("grub", 0.85), ("initramfs", 0.90),
    ("fstab", 0.90), ("modprobe", 0.90), ("lsmod", 0.85),
    ("udev", 0.85), ("sysctl", 0.85), ("cron", 0.70),
    ("crontab", 0.75), ("systemd", 0.90), ("init", 0.55),

    # Package managers
    ("apt", 0.80), ("apt-get", 0.90), ("dpkg", 0.90),
    ("pacman", 0.85), ("yay", 0.85), ("paru", 0.85),
    ("dnf", 0.85), ("yum", 0.80), ("rpm", 0.75),
    ("zypper", 0.85), ("emerge", 0.85), ("portage", 0.85),
    ("snap", 0.65), ("flatpak", 0.70), ("nix", 0.70),
    ("brew", 0.50), ("pip", 0.40),

    # File system & storage
    ("chmod", 0.85), ("chown", 0.85), ("mount", 0.75),
    ("umount", 0.80), ("fdisk", 0.90), ("lsblk", 0.90),
    ("mkfs", 0.90), ("fsck", 0.90), ("df", 0.60),
    ("du", 0.55), ("ln", 0.55), ("symlink", 0.65),

    # Network
    ("iptables", 0.90), ("nftables", 0.90), ("firewalld", 0.85),
    ("ufw", 0.85), ("nmcli", 0.90), ("networkmanager", 0.85),
    ("ifconfig", 0.80), ("ip addr", 0.85), ("ip link", 0.85),
    ("ss", 0.55), ("netstat", 0.70), ("nmap", 0.65),
    ("tcpdump", 0.80), ("traceroute", 0.65), ("dig", 0.60),
    ("resolv.conf", 0.85), ("dhclient", 0.85), ("wpa_supplicant", 0.90),

    # Services & processes
    ("nginx", 0.65), ("apache", 0.60), ("sshd", 0.75),
    ("openssh", 0.80), ("ssh", 0.50), ("docker", 0.55),
    ("podman", 0.65), ("lxc", 0.70),

    # Users & permissions
    ("sudo", 0.65), ("su", 0.45), ("passwd", 0.70),
    ("useradd", 0.85), ("usermod", 0.85), ("visudo", 0.90),
    ("sudoers", 0.90), ("polkit", 0.85),

    # Hardware & drivers
    ("nvidia", 0.50), ("nouveau", 0.80), ("mesa", 0.75),
    ("alsa", 0.80), ("pulseaudio", 0.80), ("pipewire", 0.80),
    ("xrandr", 0.85), ("xorg", 0.85), ("wayland", 0.75),
    ("driver", 0.45), ("firmware", 0.55),

    # Distro-specific
    ("ubuntu", 0.70), ("debian", 0.70), ("arch", 0.55),
    ("archlinux", 0.80), ("fedora", 0.70), ("centos", 0.70),
    ("rhel", 0.70), ("opensuse", 0.70), ("manjaro", 0.70),
    ("mint", 0.55), ("gentoo", 0.75), ("nixos", 0.80),
    ("void", 0.60), ("alpine", 0.65), ("kali", 0.60),

    # Shells & scripting
    ("bash", 0.55), ("zsh", 0.60), ("fish", 0.60),
    ("/bin/sh", 0.80), ("/etc/", 0.75), ("/var/log", 0.80),
    ("/proc/", 0.80), ("/sys/", 0.80), ("/dev/", 0.70),

    # Desktop environments
    ("gnome", 0.65), ("kde", 0.65), ("xfce", 0.70),
    ("i3wm", 0.80), ("sway", 0.80), ("hyprland", 0.80),

    # General Linux terms
    ("linux", 0.60), ("gnu", 0.65), ("posix", 0.55),
    ("tty", 0.70), ("terminal", 0.35), ("shell", 0.35),
    ("daemon", 0.65), ("service", 0.35), ("процесс", 0.25),
    ("пакет", 0.30), ("зависимость", 0.30), ("репозиторий", 0.35),
]

# Build a fast lookup set for quick checks (lowercase)
_LINUX_KW_SET: Set[str] = {kw.lower() for kw, _ in _LINUX_KEYWORDS}
_LINUX_KW_WEIGHTS: dict[str, float] = {kw.lower(): w for kw, w in _LINUX_KEYWORDS}

# ── Package manager command patterns ──
_PKG_MANAGER_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\b(?:sudo\s+)?apt(?:-get)?\s+(?:install|remove|update|upgrade|purge|autoremove|search|list)\b', re.I), "apt"),
    (re.compile(r'\b(?:sudo\s+)?dpkg\s+(?:-i|-r|-l|--configure|--install|--remove)\b', re.I), "dpkg"),
    (re.compile(r'\b(?:sudo\s+)?pacman\s+-[SRQUFDT][a-z]*\b', re.I), "pacman"),
    (re.compile(r'\b(?:yay|paru)\s+-[SRQa-z]*\b', re.I), "yay/paru"),
    (re.compile(r'\b(?:sudo\s+)?dnf\s+(?:install|remove|update|upgrade|search|list|info)\b', re.I), "dnf"),
    (re.compile(r'\b(?:sudo\s+)?yum\s+(?:install|remove|update|search|list)\b', re.I), "yum"),
    (re.compile(r'\b(?:sudo\s+)?zypper\s+(?:install|remove|update|search|in|rm)\b', re.I), "zypper"),
    (re.compile(r'\b(?:sudo\s+)?emerge\s+', re.I), "emerge"),
    (re.compile(r'\b(?:sudo\s+)?snap\s+(?:install|remove|refresh|list)\b', re.I), "snap"),
    (re.compile(r'\b(?:sudo\s+)?flatpak\s+(?:install|remove|update|list|run)\b', re.I), "flatpak"),
]

# ── Linux command patterns ──
_LINUX_CMD_PATTERNS: List[re.Pattern] = [
    # systemctl / journalctl commands
    re.compile(r'\b(?:sudo\s+)?(?:systemctl|journalctl)\s+\S+', re.I),
    # Service management
    re.compile(r'\b(?:sudo\s+)?service\s+\w+\s+(?:start|stop|restart|status|reload)\b', re.I),
    # chmod/chown
    re.compile(r'\b(?:sudo\s+)?(?:chmod|chown)\s+[\w.:+-]+\s+\S+', re.I),
    # mount/umount
    re.compile(r'\b(?:sudo\s+)?(?:u?mount)\s+\S+', re.I),
    # ip command
    re.compile(r'\bip\s+(?:addr|link|route|neigh)\s+', re.I),
    # iptables / nft
    re.compile(r'\b(?:sudo\s+)?(?:iptables|nft|nftables)\s+', re.I),
    # grep / find / sed / awk on system paths
    re.compile(r'\b(?:grep|find|sed|awk)\s+.*?/(?:etc|var|proc|sys|dev)/', re.I),
    # cat/less/tail on log/config files
    re.compile(r'\b(?:cat|less|tail|head|nano|vim|vi)\s+/(?:etc|var|proc|sys)/', re.I),
    # kill / killall / pkill
    re.compile(r'\b(?:sudo\s+)?(?:kill|killall|pkill)\s+', re.I),
    # modprobe / rmmod / insmod
    re.compile(r'\b(?:sudo\s+)?(?:modprobe|rmmod|insmod)\s+\S+', re.I),
    # dd command
    re.compile(r'\b(?:sudo\s+)?dd\s+if=', re.I),
    # make / configure (build from source)
    re.compile(r'\b(?:\./configure|make\s+(?:install|clean|all))\b', re.I),
]

# ── Error string patterns ──
# These detect typical Linux/system error messages in the query
_ERROR_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Package manager errors
    (re.compile(r'unable to locate package\b', re.I), "pkg_not_found"),
    (re.compile(r'unmet dependencies', re.I), "unmet_deps"),
    (re.compile(r'dependency .+ is not satisfiable', re.I), "dep_not_satisfiable"),
    (re.compile(r'broken packages', re.I), "broken_pkg"),
    (re.compile(r'E:\s*(?:Unable|Could not|Failed)', re.I), "apt_error"),
    (re.compile(r'dpkg:\s*error\b', re.I), "dpkg_error"),
    (re.compile(r'dpkg:\s*(?:warning|dependency problems)', re.I), "dpkg_warning"),
    (re.compile(r'dpkg.*--configure.*-a\b', re.I), "dpkg_configure"),
    (re.compile(r'error:\s*target not found', re.I), "pacman_not_found"),
    (re.compile(r'error:\s*failed to (?:commit|prepare) transaction', re.I), "pacman_transaction"),
    (re.compile(r'conflicting files', re.I), "file_conflict"),
    (re.compile(r'No match for argument', re.I), "dnf_no_match"),

    # Permission / access errors
    (re.compile(r'permission denied', re.I), "permission_denied"),
    (re.compile(r'operation not permitted', re.I), "not_permitted"),
    (re.compile(r'access denied', re.I), "access_denied"),
    (re.compile(r'authentication failure', re.I), "auth_failure"),
    (re.compile(r'incorrect password', re.I), "wrong_password"),

    # Service / systemd errors
    (re.compile(r'failed to start\b', re.I), "service_start_fail"),
    (re.compile(r'(?:unit|service)\s+\S+\s+(?:not found|could not be found)', re.I), "unit_not_found"),
    (re.compile(r'(?:status|state)[=: ]+failed', re.I), "service_failed"),
    (re.compile(r'Job for .+ failed', re.I), "job_failed"),
    (re.compile(r'(?:inactive|dead)\s*\(', re.I), "service_inactive"),
    (re.compile(r'Main process exited, code=exited, status=\d', re.I), "process_exited"),

    # Network errors
    (re.compile(r'(?:connection|connect)\s+(?:refused|timed?\s*out|reset)', re.I), "connection_error"),
    (re.compile(r'network (?:is )?unreachable', re.I), "net_unreachable"),
    (re.compile(r'name or service not known', re.I), "dns_fail"),
    (re.compile(r'could not resolve host', re.I), "dns_resolve"),
    (re.compile(r'no route to host', re.I), "no_route"),
    (re.compile(r'temporary failure in name resolution', re.I), "dns_temp_fail"),

    # Filesystem errors
    (re.compile(r'no space left on device', re.I), "no_space"),
    (re.compile(r'read-only file system', re.I), "readonly_fs"),
    (re.compile(r'input/output error', re.I), "io_error"),
    (re.compile(r'no such file or directory', re.I), "file_not_found"),
    (re.compile(r'is a directory', re.I), "is_directory"),
    (re.compile(r'not a directory', re.I), "not_directory"),
    (re.compile(r'device or resource busy', re.I), "device_busy"),
    (re.compile(r'(?:bad|corrupt)\s+(?:super|magic)\s*block', re.I), "bad_superblock"),

    # Device / driver errors
    (re.compile(r'(?:device|hardware)\s+not found', re.I), "device_not_found"),
    (re.compile(r'no (?:such )?device', re.I), "no_device"),
    (re.compile(r'(?:failed to|cannot)\s+(?:load|find)\s+(?:module|driver|firmware)', re.I), "driver_fail"),
    (re.compile(r'module .+ not found', re.I), "module_not_found"),

    # Kernel / boot errors
    (re.compile(r'kernel panic', re.I), "kernel_panic"),
    (re.compile(r'BUG:\s*(?:soft|hard)\s+lockup', re.I), "lockup"),
    (re.compile(r'(?:oops|segfault|segmentation fault)', re.I), "segfault"),
    (re.compile(r'out of memory', re.I), "oom"),
    (re.compile(r'oom[_-]?killer', re.I), "oom_killer"),

    # Generic error patterns
    (re.compile(r'(?:error|err|fatal|critical|panic)\s*[:(]\s*.{5,}', re.I), "generic_error"),
    (re.compile(r'exit (?:code|status)\s*[=: ]+(?:[1-9]\d*|255)', re.I), "nonzero_exit"),
    (re.compile(r'errno\s*[=: ]+\d+', re.I), "errno"),
    (re.compile(r'command not found', re.I), "cmd_not_found"),
    (re.compile(r'(?:syntax error|unexpected token)', re.I), "syntax_error"),

    # X11 / display errors
    (re.compile(r'cannot open display', re.I), "no_display"),
    (re.compile(r'(?:Xlib|X11):\s*\S+', re.I), "x11_error"),
    (re.compile(r'(?:EE|Fatal)\s+server error', re.I), "xorg_fatal"),
]


# ═══════════════════════════════════════════════════
#  Query Classifier
# ═══════════════════════════════════════════════════

class QueryClassifier:
    """
    Deterministic query classifier for routing to processing modes.

    Classifies queries into GENERAL / LINUX / ERROR based on keyword
    detection, command pattern matching, and error string recognition.

    No LLM calls. Fully deterministic.

    Usage:
        classifier = QueryClassifier()
        result = classifier.classify("sudo apt install nginx fails with permission denied")
        # result.mode == QueryMode.ERROR
        # result.linux_commands == ["sudo apt install nginx"]
        # result.error_strings == ["permission denied"]
    """

    def __init__(
        self,
        linux_threshold: float = 0.40,
        error_threshold: float = 0.30,
    ):
        """
        Args:
            linux_threshold: Linux score threshold for LINUX mode.
            error_threshold: Error score threshold for ERROR mode.
        """
        self._linux_threshold = linux_threshold
        self._error_threshold = error_threshold

    def classify(self, query: str) -> QueryClassification:
        """
        Classify a user query into a processing mode.

        Algorithm:
          1. Normalize and tokenize query
          2. Detect Linux keywords with weighted scoring
          3. Detect package manager invocations
          4. Detect Linux command patterns
          5. Detect error strings
          6. Compute mode scores and select mode

        Args:
            query: User's raw query text.

        Returns:
            QueryClassification with mode, scores, and detected signals.
        """
        if not query or not query.strip():
            return QueryClassification()

        result = QueryClassification()
        query_lower = query.lower().strip()
        query_words = set(re.findall(r'[\w./:-]+', query_lower))

        # ── Step 1: Linux keyword detection ──
        linux_score = 0.0
        matched_keywords: List[str] = []

        for word in query_words:
            weight = _LINUX_KW_WEIGHTS.get(word)
            if weight is not None:
                linux_score += weight
                matched_keywords.append(word)

        # Also check multi-word patterns in the full query
        for kw, weight in _LINUX_KEYWORDS:
            if " " in kw and kw.lower() in query_lower:
                if kw.lower() not in matched_keywords:
                    linux_score += weight
                    matched_keywords.append(kw.lower())

        result.linux_keywords = matched_keywords

        # ── Step 2: Package manager commands ──
        for pattern, pkg_mgr in _PKG_MANAGER_PATTERNS:
            matches = pattern.findall(query)
            if matches:
                result.package_managers.append(pkg_mgr)
                result.linux_commands.extend(matches)
                linux_score += 0.50  # Strong Linux signal

        # ── Step 3: Linux command patterns ──
        for pattern in _LINUX_CMD_PATTERNS:
            matches = pattern.findall(query)
            for m in matches:
                cmd = m.strip()
                if cmd and cmd not in result.linux_commands:
                    result.linux_commands.append(cmd)
                    linux_score += 0.30

        # ── Step 4: Path detection ──
        paths = re.findall(r'/(?:etc|var|proc|sys|dev|usr|opt|home|tmp|boot)/[\w./+-]*', query)
        if paths:
            linux_score += 0.25 * min(len(paths), 3)

        # ── Step 5: Error string detection ──
        error_score = 0.0
        error_strings: List[str] = []

        for pattern, error_type in _ERROR_PATTERNS:
            match = pattern.search(query)
            if match:
                error_strings.append(match.group(0).strip())
                error_score += 0.50

        result.error_strings = error_strings

        # ── Normalize scores ──
        # Cap linux_score at 1.0 for classification, but keep raw for diagnostics
        normalized_linux = min(linux_score / 2.0, 1.0)  # 2.0 = full score
        normalized_error = min(error_score / 1.0, 1.0)

        result.linux_score = round(normalized_linux, 3)
        result.error_score = round(normalized_error, 3)

        # ── Mode selection ──
        if normalized_error >= self._error_threshold and normalized_linux >= self._linux_threshold:
            # Both Linux and error detected → ERROR mode (most specific)
            result.mode = QueryMode.ERROR
            result.confidence = round(min(0.5 * normalized_linux + 0.5 * normalized_error, 0.99), 3)
        elif normalized_error >= self._error_threshold and error_strings:
            # Error detected, even without strong Linux signal
            # Check if error looks Linux-specific
            linux_error_types = {"pkg_not_found", "unmet_deps", "service_start_fail",
                                 "unit_not_found", "pacman_not_found", "pacman_transaction",
                                 "cmd_not_found", "kernel_panic", "oom_killer",
                                 "module_not_found", "driver_fail", "xorg_fatal",
                                 "permission_denied", "not_permitted", "dpkg_error",
                                 "dpkg_warning", "dpkg_configure", "apt_error",
                                 "broken_pkg", "no_space", "segfault", "lockup",
                                 "oom", "io_error", "bad_superblock",
                                 "file_conflict", "dep_not_satisfiable"}
            detected_types = set()
            for pattern, etype in _ERROR_PATTERNS:
                if pattern.search(query):
                    detected_types.add(etype)
            if detected_types & linux_error_types:
                result.mode = QueryMode.ERROR
                result.confidence = round(normalized_error * 0.9, 3)
            elif normalized_linux > 0.20:
                result.mode = QueryMode.ERROR
                result.confidence = round(0.4 * normalized_linux + 0.6 * normalized_error, 3)
            else:
                result.mode = QueryMode.GENERAL
                result.confidence = round(1.0 - normalized_error * 0.3, 3)
        elif normalized_linux >= self._linux_threshold:
            result.mode = QueryMode.LINUX
            result.confidence = round(normalized_linux, 3)
        else:
            result.mode = QueryMode.GENERAL
            result.confidence = round(max(1.0 - normalized_linux - normalized_error * 0.5, 0.5), 3)

        logger.info(
            "QueryClassifier: mode=%s conf=%.2f linux=%.2f error=%.2f "
            "kw=%d cmd=%d err=%d",
            result.mode.value, result.confidence,
            result.linux_score, result.error_score,
            len(result.linux_keywords), len(result.linux_commands),
            len(result.error_strings),
        )

        return result


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_classifier: QueryClassifier | None = None


def get_query_classifier() -> QueryClassifier:
    """Get or create singleton query classifier."""
    global _classifier
    if _classifier is None:
        _classifier = QueryClassifier()
    return _classifier
