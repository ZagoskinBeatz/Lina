# -*- coding: utf-8 -*-
"""
Lina Parser — Web page parsing pipeline (from Parcer/search_cli).

Readability-lxml based content extraction + text cleaning + mini-LLM summarization.
Replaces the old HTMLParser-based extraction.
"""

from lina.parser.page_parser import (
    extract_text,
    clean_text,
    collect_pages_text,
    download_pages_parallel,
    fetch_page_text,
    chunk_text,
    HEADERS,
)
from lina.parser.text_cleaner import (
    clean_extracted_text,
    clean_query,
    detect_linux_family,
    extract_commands,
    normalize_whitespace,
)

__all__ = [
    "extract_text",
    "clean_text",
    "collect_pages_text",
    "download_pages_parallel",
    "fetch_page_text",
    "chunk_text",
    "clean_extracted_text",
    "clean_query",
    "detect_linux_family",
    "extract_commands",
    "normalize_whitespace",
    "HEADERS",
]
