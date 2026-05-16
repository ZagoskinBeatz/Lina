# -*- coding: utf-8 -*-
"""
Lina Utils — HTML Cleaner.

Extracts readable text from raw HTML.  Uses BeautifulSoup when available,
falls back to regex stripping for zero-dependency operation.

Design: stateless functions, no side effects.
"""

from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger("lina.utils.html_cleaner")

# ── Try BeautifulSoup ──
try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False
    logger.debug("BeautifulSoup not installed — using regex cleaner")


# Tags whose entire subtree is noise
_NOISE_TAGS = {
    "script", "style", "noscript", "header", "footer", "nav",
    "aside", "form", "iframe", "svg", "meta", "link",
    "button", "input", "select", "textarea",
}

# Regex fallback patterns
_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_ENTITY_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;")
_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n{3,}")


def clean_html(html: str, max_length: int = 80_000) -> str:
    """
    Convert raw HTML to clean text.

    Args:
        html: Raw HTML string.
        max_length: Maximum output length in characters.

    Returns:
        Cleaned, readable plain text.
    """
    if not html:
        return ""

    if _HAS_BS4:
        text = _clean_bs4(html)
    else:
        text = _clean_regex(html)

    # Normalize whitespace
    text = _WS_RE.sub(" ", text)
    text = _NL_RE.sub("\n\n", text)
    text = text.strip()

    if len(text) > max_length:
        text = text[:max_length] + "…"

    return text


def extract_title(html: str) -> str:
    """Extract <title> from HTML."""
    if _HAS_BS4:
        try:
            soup = BeautifulSoup(html, "html.parser")
            tag = soup.find("title")
            return tag.get_text(strip=True) if tag else ""
        except Exception:
            pass

    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""


def extract_main_content(html: str) -> str:
    """
    Try to extract the main article body (ignores sidebars, navs, etc.).
    Preserves table structure as Key: Value lines and list structure.
    Falls back to full clean_html if no main content detected.
    """
    if not _HAS_BS4:
        return clean_html(html)

    try:
        soup = BeautifulSoup(html, "html.parser")

        # Remove noise subtrees
        for tag in soup.find_all(_NOISE_TAGS):
            tag.decompose()

        # Try <article>, <main>, or role="main"
        main = (
            soup.find("article")
            or soup.find("main")
            or soup.find(attrs={"role": "main"})
            or soup.find("div", class_=re.compile(r"content|article|post", re.I))
        )

        target = main if main else (soup.find("body") or soup)

        text = _extract_structured_text(target)
        if text and len(text.strip()) > 20:
            return text

        # Fallback: plain get_text
        return target.get_text(separator="\n", strip=True)

    except Exception as e:
        logger.debug("BS4 main-content extraction failed: %s", e)
        return clean_html(html)


def _extract_structured_text(element) -> str:
    """Extract text preserving table rows as KV pairs and list structure.

    Tables:  <tr><td>Label</td><td>Value</td></tr> → "Label: Value"
    Lists:   <li>Item</li> → "• Item"
    Pre/code: preserved verbatim with markers
    Everything else: get_text with newline separator
    """
    if element is None:
        return ""

    from bs4 import Tag, NavigableString

    parts: list[str] = []

    for child in element.children:
        if isinstance(child, NavigableString):
            text = child.strip()
            if text:
                parts.append(text)
            continue

        if not isinstance(child, Tag):
            continue

        tag_name = child.name.lower() if child.name else ""

        # ── Tables → structured KV lines ──
        if tag_name == "table":
            table_text = _extract_table(child)
            if table_text:
                parts.append(table_text)
            continue

        # ── Lists → bulleted lines ──
        if tag_name in ("ul", "ol"):
            list_text = _extract_list(child)
            if list_text:
                parts.append(list_text)
            continue

        # ── Code blocks → verbatim ──
        if tag_name in ("pre", "code"):
            code = child.get_text(strip=True)
            if code and len(code) > 5:
                parts.append(f"```\n{code}\n```")
            continue

        # ── Skip noise tags that weren't removed ──
        if tag_name in _NOISE_TAGS:
            continue

        # ── Headings → marked as headings ──
        if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            heading = child.get_text(strip=True)
            if heading:
                parts.append(f"\n## {heading}")
            continue

        # ── Block elements → recurse ──
        if tag_name in ("div", "section", "article", "main", "p",
                         "blockquote", "figure", "figcaption",
                         "details", "summary", "dd", "dt", "dl"):
            inner = _extract_structured_text(child)
            if inner:
                parts.append(inner)
            continue

        # ── Inline elements → get text ──
        text = child.get_text(separator=" ", strip=True)
        if text:
            parts.append(text)

    return "\n".join(parts)


def _extract_table(table_tag) -> str:
    """Convert an HTML table to structured text.

    For 2-column tables (spec sheets):  "Header: Value" per row.
    For multi-column tables: "Header1, Header2, ..." header line + "Val1, Val2, ..." rows.
    """
    from bs4 import Tag

    rows = table_tag.find_all("tr")
    if not rows:
        return ""

    # Collect all rows as cell lists
    matrix: list[list[str]] = []
    for row in rows:
        cells = row.find_all(["td", "th"])
        cell_texts = [c.get_text(strip=True) for c in cells]
        if any(cell_texts):
            matrix.append(cell_texts)

    if not matrix:
        return ""

    # Detect table type
    # 2-column tables (common spec sheets): treat as Key: Value
    max_cols = max(len(r) for r in matrix)

    if max_cols == 2:
        lines = []
        for row_cells in matrix:
            if len(row_cells) >= 2:
                label = row_cells[0].strip()
                value = row_cells[1].strip()
                if label and value:
                    lines.append(f"{label}: {value}")
                elif label:
                    lines.append(label)
            elif len(row_cells) == 1 and row_cells[0].strip():
                lines.append(row_cells[0].strip())
        return "\n".join(lines)

    # Multi-column: detect header row, format as structured lines
    lines = []
    headers: list[str] = []

    # First row with <th> elements = headers
    first_row_tag = rows[0]
    th_cells = first_row_tag.find_all("th")
    if th_cells:
        headers = [th.get_text(strip=True) for th in th_cells]

    for i, row_cells in enumerate(matrix):
        if i == 0 and headers:
            # Skip header row (already captured)
            continue
        if headers and len(row_cells) == len(headers):
            # Format as "Header: Value" pairs
            pairs = []
            for h, v in zip(headers, row_cells):
                if h and v:
                    pairs.append(f"{h}: {v}")
            if pairs:
                lines.append(" | ".join(pairs))
        else:
            # No headers or mismatched — join cells
            joined = " | ".join(c for c in row_cells if c)
            if joined:
                lines.append(joined)

    return "\n".join(lines)


def _extract_list(list_tag) -> str:
    """Convert HTML list to bulleted text."""
    items = list_tag.find_all("li", recursive=False)
    lines = []
    for item in items:
        text = item.get_text(separator=" ", strip=True)
        if text:
            lines.append(f"• {text}")
    return "\n".join(lines)


# ── Internal ──

def _clean_bs4(html: str) -> str:
    """Clean HTML using BeautifulSoup."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(_NOISE_TAGS):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception as e:
        logger.debug("BS4 cleaning failed, falling back to regex: %s", e)
        return _clean_regex(html)


def _clean_regex(html: str) -> str:
    """Clean HTML using regex (fallback)."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "",
                  html, flags=re.S | re.I)
    # Remove all tags
    text = _TAG_RE.sub(" ", text)
    # Decode common entities
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = _ENTITY_RE.sub("", text)
    return text
