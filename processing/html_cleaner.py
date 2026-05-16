# -*- coding: utf-8 -*-
"""
Lina Processing — HTML Cleaner (v3).

Clean HTML → plain text extraction for passage splitting.

v3 re-exports the v2 implementation and adds boilerplate removal heuristics.
"""

from __future__ import annotations

import re
import logging
from typing import Optional

# Re-export v2 core functions
from lina.utils.html_cleaner import (
    clean_html,
    extract_title,
    extract_main_content,
)

logger = logging.getLogger("lina.processing.html_cleaner")


# ── Bot-protection / blocked page detection ──

# Markers that, when multiple co-occur, indicate a bot-protection page
# (Cloudflare, Akamai, PerimeterX, DataDome, etc.)
_BOT_PROTECTION_MARKERS = [
    "cloudflare",
    "ray id",
    "cf-browser-verification",
    "please verify you are human",
    "verify you are human",
    "checking your browser",
    "enable javascript and cookies",
    "just a moment",
    "attention required",
    "access denied",
    "error 1015",
    "error 1020",
    "sorry, you have been blocked",
    "please turn javascript on",
    "bot protection",
    "ddos protection",
    "security check",
    "captcha",
    "hcaptcha",
    "recaptcha",
    "challenge-platform",
    "перимерикс",           # PerimeterX (RU)
    "please wait while we verify",
    "your ip",              # Cloudflare "Your IP: click to reveal"
    "page not found",       # Error pages masquerading as content
    "404 not found",
    "403 forbidden",
]

# Minimum number of markers that must co-occur to flag a page
_BOT_MARKER_THRESHOLD = 2


def is_bot_protection_page(text: str) -> bool:
    """Detect bot-protection / blocked / error pages.

    Returns True if the text appears to come from a Cloudflare-style
    challenge page, CAPTCHA, or generic HTTP error page rather than
    real content.

    The detection requires at least _BOT_MARKER_THRESHOLD marker
    matches AND the text must be short (< 3000 chars) — real content
    pages with incidental marker words won't trigger this.
    """
    if not text:
        return True
    lower = text.lower()
    # Long pages with real content are unlikely to be bot pages
    if len(lower) > 3000:
        return False
    hits = sum(1 for m in _BOT_PROTECTION_MARKERS if m in lower)
    return hits >= _BOT_MARKER_THRESHOLD


def clean_page(html: str, max_length: int = 80000) -> str:
    """
    v3 enhanced page cleaning: boilerplate removal + main content extraction.

    Tries extract_main_content first (article/main tags), falls back to
    full clean_html. Removes common boilerplate patterns.

    Args:
        html: Raw HTML string.
        max_length: Truncate output.

    Returns:
        Clean text.
    """
    if not html:
        return ""

    # Try structured extraction first
    text = extract_main_content(html)
    if text and len(text) > 100:
        text = _remove_boilerplate(text)
        if is_bot_protection_page(text):
            logger.debug("Bot-protection page detected (main content), skipping")
            return ""
        return text[:max_length]

    # Fallback to full clean
    text = clean_html(html, max_length=max_length)
    text = _remove_boilerplate(text)
    if is_bot_protection_page(text):
        logger.debug("Bot-protection page detected (full clean), skipping")
        return ""
    return text[:max_length]


# ── Boilerplate patterns ──

_BOILERPLATE_PATTERNS = [
    re.compile(r"(?:cookie|cookies?)\s*(?:polic|settings|consent|notice)", re.IGNORECASE),
    re.compile(r"(?:privacy\s+policy|terms\s+of\s+(?:service|use))", re.IGNORECASE),
    re.compile(r"(?:subscribe|newsletter|sign\s*up)\s*(?:to|for|now)", re.IGNORECASE),
    re.compile(r"(?:all\s+rights?\s+reserved|©\s*\d{4})", re.IGNORECASE),
    re.compile(r"(?:follow\s+us|share\s+(?:on|this))\s", re.IGNORECASE),
    re.compile(r"(?:advertisement|sponsored|ad\s*block)", re.IGNORECASE),
    re.compile(r"(?:мы\s+используем\s+cookie|политика\s+конфиденциальности)", re.IGNORECASE),
]


def _remove_boilerplate(text: str) -> str:
    """Remove common boilerplate lines from text."""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append("")
            continue
        # Skip short boilerplate lines
        if len(stripped) < 200:
            is_boilerplate = any(p.search(stripped) for p in _BOILERPLATE_PATTERNS)
            if is_boilerplate:
                continue
        cleaned.append(line)
    return "\n".join(cleaned)


__all__ = [
    "clean_html", "extract_title", "extract_main_content", "clean_page",
    "is_bot_protection_page",
]
