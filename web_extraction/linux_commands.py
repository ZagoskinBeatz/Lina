# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Linux Command Extractor.

Deterministic extraction of Linux commands from web page text.

Targets:
  - Commands inside code blocks (```...```, <pre>, <code>)
  - Inline commands (backtick `command`)
  - Bare commands in running text (sudo apt install ...)
  - Multi-line command sequences (pipe chains, && chains)
  - Heredoc patterns

Each extracted command has:
  - Raw text
  - Normalized form (whitespace cleaned)
  - Command type (package, service, file, network, build, etc.)
  - Whether it requires root (sudo/su)
  - Risk level (SAFE / CAUTION / DANGEROUS)

No LLM calls. Fully deterministic.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Set, Tuple, Optional

logger = logging.getLogger("lina.web_extraction.linux_commands")


# ═══════════════════════════════════════════════════
#  Risk Levels
# ═══════════════════════════════════════════════════

class CommandRisk(Enum):
    """Risk assessment of a Linux command."""
    SAFE = "safe"           # Read-only, informational
    CAUTION = "caution"     # Modifies system state but reversible
    DANGEROUS = "dangerous" # Potentially destructive, hard to reverse


# ═══════════════════════════════════════════════════
#  Command Types
# ═══════════════════════════════════════════════════

class CommandType(Enum):
    """Classification of the command's purpose."""
    PACKAGE = "package"       # Package install/remove/update
    SERVICE = "service"       # systemctl, service management
    FILE = "file"             # File operations (chmod, chown, cp, mv, rm)
    NETWORK = "network"       # Network config (ip, iptables, nmcli, ufw)
    DISK = "disk"             # Disk/mount operations (fdisk, mount, mkfs)
    CONFIG = "config"         # Config editing (sed, echo >>)
    BUILD = "build"           # Building from source (make, configure)
    DIAGNOSTIC = "diagnostic" # Information gathering (dmesg, lsblk, cat)
    PROCESS = "process"       # Process management (kill, pkill)
    USER = "user"             # User management (useradd, passwd)
    KERNEL = "kernel"         # Kernel modules (modprobe, sysctl)
    OTHER = "other"


# ═══════════════════════════════════════════════════
#  Extracted Command
# ═══════════════════════════════════════════════════

@dataclass
class LinuxCommand:
    """A single extracted Linux command with metadata."""
    raw: str                               # Original text
    normalized: str = ""                   # Cleaned version
    command_type: CommandType = CommandType.OTHER
    risk: CommandRisk = CommandRisk.CAUTION
    requires_root: bool = False
    base_command: str = ""                 # First word (e.g., "apt", "systemctl")
    args: List[str] = field(default_factory=list)
    source_context: str = ""               # Surrounding text (for relevance)
    from_code_block: bool = False          # Was inside a code block?

    @property
    def display(self) -> str:
        """Human-readable display form."""
        prefix = "# " if self.risk == CommandRisk.DANGEROUS else ""
        root = "sudo " if self.requires_root and "sudo" not in self.normalized else ""
        return f"{prefix}{root}{self.normalized}"


# ═══════════════════════════════════════════════════
#  Command Pattern Matching
# ═══════════════════════════════════════════════════

# Commands that make sense on their own (strong signal)
_STANDALONE_COMMANDS: Set[str] = {
    # Package managers
    "apt", "apt-get", "apt-cache", "dpkg", "aptitude",
    "pacman", "yay", "paru", "trizen", "makepkg",
    "dnf", "yum", "rpm", "zypper", "emerge", "equery",
    "snap", "flatpak", "nix-env", "nix",

    # System management
    "systemctl", "journalctl", "service", "update-rc.d",
    "timedatectl", "hostnamectl", "localectl", "loginctl",
    "dmesg", "sysctl", "udevadm",

    # File operations
    "chmod", "chown", "chgrp", "mkdir", "rmdir", "cp", "mv",
    "rm", "ln", "install", "rsync",

    # Network
    "iptables", "ip6tables", "nft", "ufw", "firewall-cmd",
    "nmcli", "nmtui", "ifconfig", "iwconfig",
    "ss", "netstat", "nmap", "traceroute", "dig", "nslookup",
    "dhclient", "wpa_cli", "iw",
    "curl", "wget",

    # Disk / FS
    "mount", "umount", "fdisk", "gdisk", "parted", "mkfs",
    "fsck", "lsblk", "blkid", "tune2fs", "resize2fs",
    "dd", "lvm", "pvcreate", "vgcreate", "lvcreate",

    # Process
    "kill", "killall", "pkill", "htop", "top", "ps",
    "nice", "renice", "nohup",

    # User management
    "useradd", "userdel", "usermod", "groupadd", "groupdel",
    "passwd", "chpasswd", "visudo", "adduser", "deluser",

    # Kernel / modules
    "modprobe", "rmmod", "insmod", "lsmod", "depmod",

    # Config editing
    "sed", "awk", "tee",

    # Build
    "make", "cmake", "gcc", "g++", "configure",

    # Info / diagnostic
    "lsblk", "lscpu", "lspci", "lsusb", "lshw",
    "free", "df", "du", "uname", "hostnamectl",
    "cat", "less", "tail", "head", "grep", "find",

    # Grub / boot
    "grub-install", "grub-mkconfig", "update-grub",
    "mkinitcpio", "dracut", "update-initramfs",
}

# Commands that typically need root
_ROOT_COMMANDS: Set[str] = {
    "apt", "apt-get", "dpkg", "pacman", "dnf", "yum", "rpm",
    "zypper", "emerge", "snap", "flatpak",
    "systemctl", "service", "dmesg", "sysctl", "udevadm",
    "mount", "umount", "fdisk", "gdisk", "parted", "mkfs",
    "fsck", "dd", "lvm", "pvcreate", "vgcreate", "lvcreate",
    "iptables", "ip6tables", "nft", "ufw", "firewall-cmd",
    "chmod", "chown", "chgrp",
    "useradd", "userdel", "usermod", "groupadd", "passwd",
    "visudo", "adduser", "deluser",
    "modprobe", "rmmod", "insmod",
    "grub-install", "grub-mkconfig", "update-grub",
    "mkinitcpio", "dracut", "update-initramfs",
    "kill", "killall", "pkill", "renice",
    "tee",
}

# Dangerous commands (data loss risk)
_DANGEROUS_PATTERNS: List[re.Pattern] = [
    re.compile(r'\brm\s+-(rf|fr|r)\b'),
    re.compile(r'\brm\s+.*\s+/\s*$'),
    re.compile(r'\bdd\s+.*of=/dev/'),
    re.compile(r'\bmkfs\b'),
    re.compile(r'\bfdisk\b'),
    re.compile(r'\bgdisk\b'),
    re.compile(r'\bparted\b'),
    re.compile(r'>+\s*/dev/'),
    re.compile(r'\b:>\s*/'),
    re.compile(r'\bchmod\s+-R\s+777\b'),
    re.compile(r'\bchmod\s+777\s+/'),
    re.compile(r'\bkill\s+-9\b'),
    re.compile(r'\bsudo\s+rm\b'),
    re.compile(r'\buserdel\b'),
    re.compile(r'\bgroupdel\b'),
]

# Safe (read-only) commands
_SAFE_COMMANDS: Set[str] = {
    "cat", "less", "head", "tail", "grep", "find",
    "ls", "ll", "dir", "file", "stat", "wc",
    "lsblk", "lscpu", "lspci", "lsusb", "lshw", "lsmod",
    "free", "df", "du", "uname", "uptime", "who", "whoami",
    "id", "groups", "hostname", "hostnamectl",
    "ps", "top", "htop", "pgrep",
    "ip", "ss", "netstat", "dig", "nslookup", "traceroute", "ping",
    "dmesg", "journalctl",
    "dpkg", "apt-cache", "rpm", "pacman", "dnf",  # Query-only modes
    "which", "whereis", "type", "command",
    "echo", "printf",
    "date", "cal", "timedatectl",
    "env", "printenv", "set",
}

# Command type classification
_TYPE_MAP: dict[str, CommandType] = {}
for _cmd in ("apt", "apt-get", "apt-cache", "dpkg", "aptitude",
             "pacman", "yay", "paru", "trizen", "makepkg",
             "dnf", "yum", "rpm", "zypper", "emerge",
             "snap", "flatpak", "nix-env", "nix", "pip", "pip3"):
    _TYPE_MAP[_cmd] = CommandType.PACKAGE
for _cmd in ("systemctl", "journalctl", "service", "update-rc.d",
             "timedatectl", "hostnamectl", "localectl", "loginctl"):
    _TYPE_MAP[_cmd] = CommandType.SERVICE
for _cmd in ("chmod", "chown", "chgrp", "cp", "mv", "rm", "ln",
             "mkdir", "rmdir", "rsync", "install"):
    _TYPE_MAP[_cmd] = CommandType.FILE
for _cmd in ("iptables", "ip6tables", "nft", "ufw", "firewall-cmd",
             "nmcli", "nmtui", "ifconfig", "iwconfig", "ip",
             "ss", "netstat", "nmap", "traceroute", "dig",
             "nslookup", "dhclient", "wpa_cli", "iw",
             "curl", "wget", "ping"):
    _TYPE_MAP[_cmd] = CommandType.NETWORK
for _cmd in ("mount", "umount", "fdisk", "gdisk", "parted",
             "mkfs", "fsck", "lsblk", "blkid", "tune2fs",
             "resize2fs", "dd", "lvm", "pvcreate", "vgcreate", "lvcreate"):
    _TYPE_MAP[_cmd] = CommandType.DISK
for _cmd in ("sed", "awk", "tee", "nano", "vim", "vi", "echo"):
    _TYPE_MAP[_cmd] = CommandType.CONFIG
for _cmd in ("make", "cmake", "gcc", "g++", "configure", "cargo", "rustc"):
    _TYPE_MAP[_cmd] = CommandType.BUILD
for _cmd in ("cat", "less", "head", "tail", "grep", "find",
             "lscpu", "lspci", "lsusb", "lshw", "free", "df", "du",
             "uname", "dmesg", "file", "stat", "wc"):
    _TYPE_MAP[_cmd] = CommandType.DIAGNOSTIC
for _cmd in ("kill", "killall", "pkill", "nice", "renice", "nohup"):
    _TYPE_MAP[_cmd] = CommandType.PROCESS
for _cmd in ("useradd", "userdel", "usermod", "groupadd", "groupdel",
             "passwd", "chpasswd", "visudo", "adduser", "deluser"):
    _TYPE_MAP[_cmd] = CommandType.USER
for _cmd in ("modprobe", "rmmod", "insmod", "lsmod", "depmod", "sysctl",
             "grub-install", "grub-mkconfig", "update-grub",
             "mkinitcpio", "dracut", "update-initramfs"):
    _TYPE_MAP[_cmd] = CommandType.KERNEL


# ═══════════════════════════════════════════════════
#  Extraction Patterns
# ═══════════════════════════════════════════════════

# Code block markers
_CODE_BLOCK_RE = re.compile(
    r'```(?:bash|sh|shell|console|terminal|zsh|fish)?\s*\n(.*?)```',
    re.DOTALL,
)
_PRE_BLOCK_RE = re.compile(r'<pre[^>]*>(.*?)</pre>', re.DOTALL | re.I)
_CODE_INLINE_RE = re.compile(r'`([^`]{3,120})`')

# Command line prefix patterns ($ prompt, # prompt)
_PROMPT_RE = re.compile(r'^[\s]*[$#>]\s+(.+)$', re.MULTILINE)

# Bare command at start of line
_BARE_CMD_START = re.compile(
    r'^(?:sudo\s+)?(' + '|'.join(re.escape(c) for c in sorted(_STANDALONE_COMMANDS, key=len, reverse=True)) + r')\s',
    re.MULTILINE,
)


# ═══════════════════════════════════════════════════
#  Linux Command Extractor
# ═══════════════════════════════════════════════════

class LinuxCommandExtractor:
    """
    Extract Linux commands from web page text.

    Scans for commands in code blocks, inline code, and plain text.
    Classifies each command by type, risk level, and root requirement.

    No LLM calls. Fully deterministic.

    Usage:
        extractor = LinuxCommandExtractor()
        commands = extractor.extract("Run sudo apt install nginx to install nginx")
        # commands[0].normalized == "sudo apt install nginx"
        # commands[0].command_type == CommandType.PACKAGE
        # commands[0].requires_root == True
    """

    def __init__(self, max_commands: int = 50):
        """
        Args:
            max_commands: Maximum commands to extract from one document.
        """
        self._max_commands = max_commands

    def extract(self, text: str) -> List[LinuxCommand]:
        """
        Extract all Linux commands from text.

        Strategy:
          1. Extract from code blocks (highest confidence)
          2. Extract from inline code
          3. Extract from prompt patterns ($ command)
          4. Extract bare commands from text
          5. Deduplicate and classify

        Args:
            text: Page text (output of ContentExtractor).

        Returns:
            List of LinuxCommand objects, deduplicated.
        """
        if not text or not text.strip():
            return []

        commands: List[LinuxCommand] = []
        seen_normalized: Set[str] = set()

        # ── 1. Code blocks ──
        for match in _CODE_BLOCK_RE.finditer(text):
            block = match.group(1)
            for cmd in self._extract_from_block(block, from_code_block=True):
                norm = cmd.normalized
                if norm not in seen_normalized:
                    seen_normalized.add(norm)
                    commands.append(cmd)

        # ── 2. Pre blocks (HTML carried through) ──
        for match in _PRE_BLOCK_RE.finditer(text):
            block = _strip_html_tags(match.group(1))
            for cmd in self._extract_from_block(block, from_code_block=True):
                norm = cmd.normalized
                if norm not in seen_normalized:
                    seen_normalized.add(norm)
                    commands.append(cmd)

        # ── 3. Inline code ──
        for match in _CODE_INLINE_RE.finditer(text):
            inline = match.group(1).strip()
            cmd = self._try_parse_command(inline, from_code_block=True)
            if cmd and cmd.normalized not in seen_normalized:
                seen_normalized.add(cmd.normalized)
                commands.append(cmd)

        # ── 4. Prompt patterns ──
        for match in _PROMPT_RE.finditer(text):
            line = match.group(1).strip()
            cmd = self._try_parse_command(line, from_code_block=False)
            if cmd and cmd.normalized not in seen_normalized:
                seen_normalized.add(cmd.normalized)
                commands.append(cmd)

        # ── 5. Bare commands in text ──
        for match in _BARE_CMD_START.finditer(text):
            # Extract the full command line
            start = match.start()
            # Find the end of the line
            end = text.find('\n', start)
            if end == -1:
                end = len(text)
            line = text[start:end].strip()
            # Trim: stop at sentence-ending punctuation (not typical in commands)
            line = re.split(r'(?<!\w)[.;]\s+', line)[0].strip()
            cmd = self._try_parse_command(line, from_code_block=False)
            if cmd and cmd.normalized not in seen_normalized:
                seen_normalized.add(cmd.normalized)
                commands.append(cmd)

        return commands[:self._max_commands]

    def extract_from_passages(
        self,
        passages: list,
        deduplicate: bool = True,
    ) -> List[LinuxCommand]:
        """
        Extract commands from a list of Passage objects.

        Args:
            passages: List of Passage objects.
            deduplicate: Remove duplicate commands across passages.

        Returns:
            Combined list of extracted commands.
        """
        all_commands: List[LinuxCommand] = []
        seen: Set[str] = set()

        for passage in passages:
            text = passage.text if hasattr(passage, 'text') else str(passage)
            page_commands = self.extract(text)
            for cmd in page_commands:
                if deduplicate and cmd.normalized in seen:
                    continue
                seen.add(cmd.normalized)
                cmd.source_context = text[:200]
                all_commands.append(cmd)

        return all_commands[:self._max_commands]

    # ═══════════════════════════════════════════════
    #  Internals
    # ═══════════════════════════════════════════════

    def _extract_from_block(
        self,
        block: str,
        from_code_block: bool = True,
    ) -> List[LinuxCommand]:
        """Extract commands from a code block."""
        commands: List[LinuxCommand] = []
        lines = block.strip().split('\n')

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Skip empty lines and comments
            if not line or line.startswith('#'):
                i += 1
                continue

            # Remove prompt prefix
            prompt_match = re.match(r'^[$#>]\s+', line)
            if prompt_match:
                line = line[prompt_match.end():]

            # Handle line continuation (\)
            while line.endswith('\\') and i + 1 < len(lines):
                i += 1
                line = line[:-1].strip() + ' ' + lines[i].strip()

            # Handle pipe chains and && chains — keep as single command
            cmd = self._try_parse_command(line, from_code_block=from_code_block)
            if cmd:
                commands.append(cmd)

            i += 1

        return commands

    def _try_parse_command(
        self,
        line: str,
        from_code_block: bool = False,
    ) -> Optional[LinuxCommand]:
        """
        Try to parse a line as a Linux command.

        Returns None if the line doesn't look like a valid command.
        """
        line = line.strip()
        if not line or len(line) < 3 or len(line) > 500:
            return None

        # Skip comments
        if line.startswith('#') or line.startswith('//'):
            return None

        # Skip output lines (typical log/error output patterns)
        if re.match(r'^[\[\(]?\d{4}[-/]\d{2}', line):  # Timestamps
            return None
        if re.match(r'^[A-Z][a-z]+ \d{1,2},? \d{4}', line):  # Date format
            return None

        # Determine the base command
        has_sudo = False
        clean = line

        # Strip sudo/su prefix
        sudo_match = re.match(r'^(?:sudo\s+(?:-[A-Za-z]\s+)?)', clean)
        if sudo_match:
            has_sudo = True
            clean = clean[sudo_match.end():]

        # Get first word as base command
        parts = clean.split()
        if not parts:
            return None

        base = parts[0].strip()

        # Strip path prefix (e.g., /usr/bin/apt → apt)
        if '/' in base:
            base = base.rsplit('/', 1)[-1]

        # Check if it's a known command
        if base not in _STANDALONE_COMMANDS:
            # Check for path-based invocations
            if not re.match(r'^\.?/', parts[0]):
                return None

        # Build the command
        normalized = re.sub(r'\s+', ' ', line).strip()

        cmd = LinuxCommand(
            raw=line,
            normalized=normalized,
            base_command=base,
            args=parts[1:],
            from_code_block=from_code_block,
            requires_root=has_sudo or base in _ROOT_COMMANDS,
            command_type=_TYPE_MAP.get(base, CommandType.OTHER),
            risk=self._assess_risk(base, normalized),
        )

        return cmd

    def _assess_risk(self, base: str, normalized: str) -> CommandRisk:
        """Assess risk level of a command."""
        # Check dangerous patterns
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(normalized):
                return CommandRisk.DANGEROUS

        # Safe commands
        if base in _SAFE_COMMANDS:
            # But some flags make them modify state
            if base in ("grep", "find", "cat", "less", "head", "tail"):
                return CommandRisk.SAFE
            # dpkg -l is safe, dpkg -i is not
            if base in ("dpkg", "rpm", "pacman"):
                if re.search(r'-[lqQs]', normalized):
                    return CommandRisk.SAFE
                return CommandRisk.CAUTION
            # journalctl and dmesg are read-only
            if base in ("journalctl", "dmesg"):
                return CommandRisk.SAFE
            return CommandRisk.SAFE

        # Root-requiring commands default to CAUTION
        if base in _ROOT_COMMANDS:
            return CommandRisk.CAUTION

        return CommandRisk.CAUTION


# ═══════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════

def _strip_html_tags(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r'<[^>]+>', '', text)


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_extractor: LinuxCommandExtractor | None = None


def get_linux_command_extractor() -> LinuxCommandExtractor:
    """Get or create singleton command extractor."""
    global _extractor
    if _extractor is None:
        _extractor = LinuxCommandExtractor()
    return _extractor
