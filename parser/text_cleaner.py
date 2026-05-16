# -*- coding: utf-8 -*-
"""
text_cleaner.py — Preprocessing pipeline for extracted web text.

Adapted from Parcer/search_cli/text_cleaner.py for lina integration.

Sits between extract_text() and the LLM summarisation pipeline.
Removes boilerplate, citation blocks, author signatures, duplicate lines,
embedded URLs, leftover HTML, and other noise that confuses small LLMs.
"""

import re
from typing import List, Optional, Dict

# ---------------------------------------------------------------------------
# Terminal artifact patterns for query cleaning
# ---------------------------------------------------------------------------

_TERMINAL_ARTIFACTS: list[re.Pattern] = [
    re.compile(p)
    for p in [
        r"❯",
        r"^\$\s",
        r"^#\s",
        r"^%\s",
        r"^>\s",
        r"\[sudo\]",
        r"password for \S+",
        r"^sudo\s+",
        r"^su\s+-?c?\s",
        r"\S+@\S+[:\$#%>]",            # user@host:~$
        r"^\w+\$\s",                    # bash$
        r"~[\\/]?\$",                   # ~/$ or ~$
    ]
]

_STRIP_WORDS = {
    "sudo", "su", "--", "-y", "-f", "apt-get", "apt",
    "dnf", "pacman", "yum", "zypper", "emerge",
    "-S", "-Syu", "install",
}


def clean_query(query: str) -> str:
    """Remove terminal artifacts and shell noise from a user query."""
    if not query:
        return query

    for pat in _TERMINAL_ARTIFACTS:
        query = pat.sub(" ", query)

    words = query.split()
    words = [w for w in words if w.lower() not in _STRIP_WORDS and not w.startswith("-")]
    query = " ".join(words)
    query = re.sub(r"\s{2,}", " ", query).strip()
    return query


# ---------------------------------------------------------------------------
# Technical command keywords — lines containing these are always preserved
# ---------------------------------------------------------------------------

_TECHNICAL_KEYWORDS = {
    "sudo", "apt", "apt-get", "pacman", "dnf", "yum", "brew",
    "pip", "pip3", "npm", "npx", "yarn", "pnpm", "cargo", "go",
    "docker", "kubectl", "systemctl", "journalctl", "git",
    "wget", "curl", "chmod", "chown", "make", "cmake", "gcc",
    "rustc", "python", "python3", "node", "ruby", "java",
    "zypper", "emerge", "snap", "flatpak", "nix",
}


def _is_technical_line(line: str) -> bool:
    """Return True if *line* looks like a shell command or code example."""
    words = set(line.strip().lower().split())
    return bool(words & _TECHNICAL_KEYWORDS)


# ---------------------------------------------------------------------------
# Boilerplate / noise patterns (case-insensitive)
# ---------------------------------------------------------------------------

_BOILERPLATE_PHRASES: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        # Citation / sharing blocks
        r"cite this article",
        r"cite this as",
        r"share this article",
        r"share this post",
        r"share on (facebook|twitter|linkedin|reddit|whatsapp|email)",
        r"related articles?",
        r"related posts?",
        r"read more\s*[:\.]?\s*$",
        r"read also",
        r"see also",
        r"you may also like",
        r"you might also like",
        r"recommended for you",
        r"more from",
        r"^\s*advertisement\s*$",
        r"^\s*sponsored\s*$",
        r"^\s*ad\s*$",
        r"^\s*promoted\s*$",
        # Author / publication signatures
        r"^by [A-Z][a-z]+ [A-Z][a-z]+",
        r"^written by\b",
        r"^published on\b",
        r"^posted on\b",
        r"^updated on\b",
        r"^last (updated|modified|edited)\b",
        r"^author:\s",
        r"^source:\s",
        # Footer / legal
        r"all rights reserved",
        r"©\s*\d{4}",
        r"copyright\s+\d{4}",
        r"terms (of|and) (service|use)",
        r"privacy policy",
        r"cookie (policy|settings|preferences)",
        r"subscribe to (our|the) newsletter",
        r"sign up for",
        r"join our mailing list",
        r"follow us on",
        r"contact us",
        r"leave a (comment|reply)",
        r"comments?\s*\(\d+\)",
        r"^\d+ comments?$",
        # Misc noise
        r"click here",
        r"tap (here|to)",
        r"download (the )?app",
        r"get the app",
        r"^\s*tags?:\s",
        r"^\s*categories?:\s",
        r"^\s*filed under\b",
        r"table of contents",
        r"skip to (content|main)",
        # Russian equivalents
        r"поделить(ся|ь)",
        r"подписаться",
        r"читайте также",
        r"смотрите также",
        r"похожие (статьи|материалы|записи)",
        r"все права защищены",
        r"автор:\s",
        r"источник:\s",
        r"опубликовано\s",
        r"оставить комментарий",
        r"реклама",
    ]
]

# Regex to detect standalone URLs embedded in text.
_URL_RE = re.compile(
    r"https?://[^\s)<>\"\']+"
    r"|www\.[^\s)<>\"\']+"
)

# Leftover HTML fragments: tags, entities.
_HTML_TAG_RE = re.compile(r"<[^>]{1,200}>")
_HTML_ENTITY_RE = re.compile(r"&(?:#\d+|#x[\da-fA-F]+|[a-zA-Z]+);")


# ---------------------------------------------------------------------------
# Linux distribution family detection
# ---------------------------------------------------------------------------

_DISTRO_KEYWORDS: Dict[str, List[str]] = {
    "ARCH_BASED": [
        "pacman", "arch", "manjaro", "cachyos", "endeavouros",
        "garuda", "artix", "makepkg", "yay", "paru", "aur",
    ],
    "DEBIAN_BASED": [
        "apt", "apt-get", "dpkg", "ubuntu", "debian", "mint",
        "pop!_os", "pop_os", "elementary", "kali", "deb ",
    ],
    "REDHAT_BASED": [
        "dnf", "yum", "fedora", "centos", "rhel", "red hat",
        "rocky", "alma", "rpm ",
    ],
}


def detect_linux_family(text: str) -> Optional[str]:
    """
    Scan *text* for Linux distro keywords.

    Returns one of 'ARCH_BASED', 'DEBIAN_BASED', 'REDHAT_BASED', or None.
    """
    lower = text.lower()
    scores: Dict[str, int] = {}
    for family, keywords in _DISTRO_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score > 0:
            scores[family] = score
    if not scores:
        return None
    return max(scores, key=scores.get)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Command extraction from scraped text
# ---------------------------------------------------------------------------

_COMMAND_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*[\$#]\s+(.+)", re.MULTILINE),
    re.compile(r"(sudo\s+\S+(?:\s+\S+){0,8})", re.MULTILINE),
    re.compile(
        r"((?:apt|apt-get|dnf|yum|pacman|pip|pip3|npm|cargo|flatpak)\s+"
        r"(?:install|remove|update|upgrade|search|-S|-R)\s+\S+(?:\s+\S+){0,5})",
        re.MULTILINE,
    ),
    re.compile(
        r"(systemctl\s+(?:start|stop|restart|enable|disable|status)\s+\S+)",
        re.MULTILINE,
    ),
    re.compile(
        r"(git\s+(?:clone|pull|push|checkout|commit|merge|rebase|init)\s+\S+(?:\s+\S+){0,3})",
        re.MULTILINE,
    ),
    re.compile(
        r"((?:chmod|chown|mkdir|cp|mv|ln|curl|wget)\s+\S+(?:\s+\S+){0,5})",
        re.MULTILINE,
    ),
]


def extract_commands(text: str) -> List[str]:
    """Extract shell commands from scraped web-page text."""
    commands: List[str] = []
    seen: set = set()

    for pat in _COMMAND_PATTERNS:
        for match in pat.finditer(text):
            cmd = match.group(1) if pat.groups else match.group(0)
            cmd = cmd.strip()
            if len(cmd) < 5 or len(cmd) > 200:
                continue
            key = cmd.lower()
            if key not in seen:
                seen.add(key)
                commands.append(cmd)

    return commands


# ---------------------------------------------------------------------------
# Public API: full text cleaning pipeline
# ---------------------------------------------------------------------------

def clean_extracted_text(text: str) -> str:
    """
    Full cleaning pipeline for text just extracted from HTML.

    Steps:
      1. Strip leftover HTML tags and entities.
      2. Remove standalone URLs.
      3. Remove boilerplate / noise lines.
      4. Remove very short lines (< 15 chars AND < 3 words).
      5. Remove excessively long lines (> 500 chars).
      6. Deduplicate identical lines.
      7. Normalize whitespace.
    """
    if not text or not text.strip():
        return ""

    original_text = text

    text = _strip_html_leftovers(text)
    text = _remove_inline_urls(text)

    lines = text.split("\n")
    lines = _remove_boilerplate_lines(lines)
    lines = _remove_short_lines(lines)
    lines = _remove_long_lines(lines, max_len=500)
    lines = _deduplicate_lines(lines)

    text = "\n".join(lines)
    text = normalize_whitespace(text)

    # Fallback: if cleaning was too aggressive, use the original text
    if len(text) < 500 and len(original_text.strip()) >= 500:
        fallback = _strip_html_leftovers(original_text)
        fb_lines = _deduplicate_lines(fallback.split("\n"))
        text = normalize_whitespace("\n".join(fb_lines))

    return text


def remove_duplicate_lines(text: str) -> str:
    """Remove duplicate lines, keeping only the first occurrence."""
    lines = text.split("\n")
    return "\n".join(_deduplicate_lines(lines))


def normalize_whitespace(text: str) -> str:
    """Collapse excessive whitespace while preserving paragraph structure."""
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _strip_html_leftovers(text: str) -> str:
    """Remove residual HTML tags and entities that survived extraction."""
    text = _HTML_TAG_RE.sub("", text)
    text = _HTML_ENTITY_RE.sub(" ", text)
    return text


def _remove_inline_urls(text: str) -> str:
    """Replace standalone URLs with empty string."""
    return _URL_RE.sub("", text)


def _remove_boilerplate_lines(lines: List[str]) -> List[str]:
    """Drop lines that match any known boilerplate pattern."""
    result: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append(line)
            continue
        if any(pat.search(stripped) for pat in _BOILERPLATE_PHRASES):
            continue
        result.append(line)
    return result


def _remove_short_lines(lines: List[str]) -> List[str]:
    """
    Remove lines that are both very short AND lack substance.

    Technical command lines are always preserved regardless of length.
    Blank lines are kept to preserve paragraph structure.
    """
    result: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == "":
            result.append(line)
            continue
        if _is_technical_line(stripped):
            result.append(line)
            continue
        word_count = len(stripped.split())
        if len(stripped) < 15 and word_count < 3:
            continue
        result.append(line)
    return result


def _remove_long_lines(lines: List[str], max_len: int = 500) -> List[str]:
    """Remove lines exceeding *max_len* characters (likely minified code)."""
    return [
        line for line in lines
        if len(line.strip()) <= max_len or line.strip() == ""
    ]


def _deduplicate_lines(lines: List[str]) -> List[str]:
    """Keep only the first occurrence of each non-blank line."""
    seen: set = set()
    result: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == "":
            result.append(line)
            continue
        key = stripped.lower()
        if key not in seen:
            seen.add(key)
            result.append(line)
    return result
