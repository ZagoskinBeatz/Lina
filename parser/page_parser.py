# -*- coding: utf-8 -*-
"""
page_parser.py — Parallel page downloading, smart text extraction, and chunking.

Adapted from Parcer/search_cli/page_parser.py for lina integration.

Responsibilities:
  - Shared HTTP headers
  - Parallel page downloads via ThreadPoolExecutor
  - Smart text extraction: readability-lxml (main content) → BeautifulSoup fallback
  - Splitting combined text into LLM-safe chunks
  - Single-page fetcher for the interactive viewer
"""

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Optional

import requests
from bs4 import BeautifulSoup

from lina.parser.text_cleaner import clean_extracted_text

logger = logging.getLogger("lina.parser.page_parser")

# readability-lxml: extracts the main article content from any page,
# automatically stripping nav, footer, ads, sidebars, etc.
try:
    from readability import Document as ReadabilityDocument
    _READABILITY_OK = True
except ImportError:
    _READABILITY_OK = False
    logger.info(
        "readability-lxml not installed — using BeautifulSoup fallback. "
        "Install with: pip install readability-lxml"
    )


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# HTML tags that should always be stripped even inside readability output.
_NOISE_TAGS = [
    "script", "style", "nav", "footer", "header", "aside",
    "form", "button", "noscript", "iframe", "svg",
]

# Characters per page extracted for the LLM pipeline.
_LLM_PAGE_CHARS = 800

# Characters per page for single-page viewer.
_PREVIEW_CHARS = 1500

# How many worker threads to use for parallel downloading.
_DOWNLOAD_WORKERS = 5

# Chunk size for LLM inference (chars).
_CHUNK_CHARS = 1400

# Hard cap on combined text sent to LLM.
_TOTAL_TEXT_LIMIT = 3000

# Lock for progress printing from worker threads.
_print_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Normalise whitespace: collapse spaces/tabs/newlines into one space."""
    return " ".join(text.split())


def _collapse_newlines(text: str) -> str:
    """Reduce runs of 3+ newlines to two."""
    return re.sub(r"\n{3,}", "\n\n", text)


def _trim_to(text: str, limit: int) -> str:
    """Trim *text* to *limit* characters, cutting at the last word boundary."""
    if len(text) <= limit:
        return text
    trimmed = text[:limit]
    last_space = trimmed.rfind(" ")
    if last_space > limit * 0.85:
        trimmed = trimmed[:last_space]
    return trimmed + " ..."


# ---------------------------------------------------------------------------
# Smart text extraction (readability → BeautifulSoup fallback)
# ---------------------------------------------------------------------------

def extract_text(html: str, url: str = "") -> str:
    """
    Extract clean, readable plain text from raw HTML.

    Strategy:
        1. Try readability-lxml: it automatically identifies the main article
           body and removes boilerplate (nav, ads, sidebars, footers).
        2. Fall back to BeautifulSoup: strip noise tags manually and collect
           text from <article>, <main>, or all <p> tags.

    Args:
        html: Raw HTML string of the page.
        url:  Source URL (used only for error context / logging).

    Returns:
        Cleaned plain text, or '' if nothing usable was found.
    """
    if not html or len(html.strip()) < 50:
        return ""

    # --- Attempt 1: readability-lxml ---
    if _READABILITY_OK:
        try:
            doc = ReadabilityDocument(html)
            content_html = doc.summary()
            # readability returns cleaned HTML; parse it with BS4 to get plain text
            soup = BeautifulSoup(content_html, "html.parser")
            for tag in soup.find_all(["script", "style"]):
                tag.decompose()
            text = clean_text(soup.get_text(separator=" "))
            if len(text) > 150:     # skip if readability returned near-nothing
                logger.info("readability-lxml: %d симв. из %s", len(text), url[:60] or "html")
                return text
        except Exception:
            pass                    # fall through to BS4

    # --- Attempt 2: BeautifulSoup fallback ---
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ""

    # Strip UI chrome
    for tag_name in _NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    chunks: List[str] = []

    # Prefer semantic wrappers first
    for container_tag in ("article", "main"):
        container = soup.find(container_tag)
        if container:
            for p in container.find_all("p"):
                t = clean_text(p.get_text(separator=" "))
                if t:
                    chunks.append(t)
            break
    else:
        for p in soup.find_all("p"):
            t = clean_text(p.get_text(separator=" "))
            if t:
                chunks.append(t)

    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Parallel page downloader
# ---------------------------------------------------------------------------

def _download_one(index: int, total: int, url: str) -> dict:
    """
    Download a single URL and return a result dict.

    Returns:
        {"index": int, "url": str, "html": str|None, "ok": bool, "error": str}
    """
    logger.info("[%d/%d] Скачиваю: %s", index, total, url)

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=5,
            allow_redirects=True,
        )
        response.raise_for_status()
        return {"index": index, "url": url, "html": response.text,
                "ok": True, "error": ""}
    except requests.exceptions.Timeout:
        err = "timeout"
    except requests.exceptions.HTTPError as exc:
        err = f"HTTP {exc.response.status_code}" if exc.response else "HTTP error"
    except requests.exceptions.ConnectionError:
        err = "connection error"
    except requests.exceptions.RequestException as exc:
        err = str(exc)[:80]

    logger.info("  Пропущен %s (%s)", url, err)
    return {"index": index, "url": url, "html": None, "ok": False, "error": err}


def download_pages_parallel(urls: List[str]) -> List[dict]:
    """
    Download all *urls* concurrently using a thread pool.

    Results are returned in the original URL order regardless of which
    page finished first.
    """
    if not urls:
        return []

    total = len(urls)
    ordered: List[Optional[dict]] = [None] * total

    with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as pool:
        future_map = {
            pool.submit(_download_one, i + 1, total, url): i
            for i, url in enumerate(urls)
        }
        try:
            for future in as_completed(future_map, timeout=30):
                pos = future_map[future]
                try:
                    ordered[pos] = future.result()
                except Exception as e:
                    ordered[pos] = {
                        "index": pos + 1, "url": urls[pos],
                        "html": None, "ok": False, "error": str(e)[:80],
                    }
        except TimeoutError:
            logger.warning("download_pages_parallel: 30s timeout — returning partial results")
            for fut, idx in future_map.items():
                if not fut.done():
                    fut.cancel()

    return [r for r in ordered if r is not None]


# ---------------------------------------------------------------------------
# Text chunking for LLM context management
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_chars: int = _CHUNK_CHARS) -> List[str]:
    """
    Split *text* into non-overlapping chunks of ≤ chunk_chars characters,
    always breaking at word boundaries.
    """
    if len(text) <= chunk_chars:
        return [text]

    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_chars
        if end >= len(text):
            chunks.append(text[start:].strip())
            break
        cut = text.rfind(" ", start, end)
        if cut <= start:
            cut = end
        chunks.append(text[start:cut].strip())
        start = cut + 1

    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# High-level aggregator (used by the LLM pipeline)
# ---------------------------------------------------------------------------

def collect_pages_text(
    urls: List[str],
    per_page_chars: int = _LLM_PAGE_CHARS,
    total_limit: int = _TOTAL_TEXT_LIMIT,
    start_index: int = 1,
) -> Tuple[str, List[str]]:
    """
    Download all *urls* in parallel, extract text, and combine for the LLM.

    Returns:
        (combined_text, successful_urls) — labelled text ready for LLM
        and list of URLs that contributed text.
    """
    raw_results = download_pages_parallel(urls)
    logger.info("Парсер: скачано %d/%d страниц", sum(1 for r in raw_results if r["ok"]), len(urls))

    sections: List[str] = []
    successful_urls: List[str] = []
    src_idx = start_index

    for res in raw_results:
        if not res["ok"] or not res["html"]:
            continue

        text = extract_text(res["html"], res["url"])
        if not text:
            logger.info("Нет текста из %s", res["url"])
            continue

        # ── CAPTCHA/bot-check detection ──
        # Some sites (devicespecifications.com) serve CAPTCHA pages that
        # readability-lxml parses into random specs from other devices.
        # Check only the first 3000 chars of HTML — real CAPTCHA/challenge
        # pages put indicators in the <head>/<title>, not deep in body text.
        # Use word-boundary checks to avoid false positives from
        # camelCase identifiers like "wgConfirmEditCaptchaNeeded".
        _html_head = (res["html"][:3000].lower()) if res["html"] else ""
        _is_captcha = False
        if "verify you are human" in _html_head:
            _is_captcha = True
        elif "checking your browser" in _html_head:
            _is_captcha = True
        elif "just a moment" in _html_head and "cloudflare" in _html_head:
            _is_captcha = True
        elif re.search(r'<title>[^<]*captcha[^<]*</title>', _html_head):
            _is_captcha = True
        if _is_captcha:
            logger.info("CAPTCHA/bot-check detected, skipping: %s", res["url"])
            continue

        # Clean boilerplate, citations, duplicates before feeding to LLM
        text = clean_extracted_text(text)
        if not text or len(text) < 50:
            logger.info("Текст слишком короткий после очистки: %s", res["url"])
            continue

        # Trim each page's contribution individually
        text = _trim_to(text, per_page_chars)

        label = f"[src-{src_idx}]"
        sections.append(f"{label}\n{text}")
        successful_urls.append(res["url"])
        src_idx += 1

    if not sections:
        logger.info("Парсер: 0 страниц дали полезный текст из %d URL", len(urls))
        return "", []

    combined = "\n\n".join(sections)

    # Hard-trim total to avoid overflowing the LLM context window
    if len(combined) > total_limit:
        combined = combined[:total_limit] + " ..."

    logger.info(
        "Парсер: извлечено %d симв. из %d/%d страниц (readability-lxml)",
        len(combined), len(successful_urls), len(urls),
    )

    return combined, successful_urls


# ---------------------------------------------------------------------------
# Single-page fetcher (for interactive viewing)
# ---------------------------------------------------------------------------

def fetch_page_text(url: str, max_chars: int = _PREVIEW_CHARS) -> str:
    """
    Download one page and return a clean text preview.

    Returns:
        Extracted text, or a human-readable error string starting with "Error:".
    """
    try:
        response = requests.get(
            url, headers=HEADERS, timeout=5, allow_redirects=True,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        return "Error: Could not connect. Check your internet connection."
    except requests.exceptions.Timeout:
        return "Error: Request timed out."
    except requests.exceptions.HTTPError as exc:
        code = exc.response.status_code if exc.response else "?"
        return f"Error: HTTP {code} from server."
    except requests.exceptions.RequestException as exc:
        return f"Error: Network problem — {exc}"

    text = extract_text(response.text, url)
    if not text:
        return "Could not extract readable text from this page."

    text = clean_extracted_text(text)
    text = _collapse_newlines(text)
    return _trim_to(text, max_chars)
