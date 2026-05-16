# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Content Extractor.

Production-grade DOM-based main content extraction using text density
analysis, structure scoring, and boilerplate removal.

Algorithm overview:
  1. Parse HTML into DOM tree
  2. Remove known noise subtrees (script, style, nav, aside, etc.)
  3. Calculate text density for each block element
  4. Score blocks by: text density, link density, DOM position, tag semantics
  5. Select highest-scoring content region
  6. Extract text preserving structure (tables → KV, lists → bullets)
  7. Normalize whitespace and remove residual boilerplate

No LLM calls. Fully deterministic.
"""

from __future__ import annotations

import re
import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

logger = logging.getLogger("lina.web_extraction.content_extractor")

# ─────────────────────────────────────────────────
#  Try BeautifulSoup; fall back to regex
# ─────────────────────────────────────────────────
try:
    from bs4 import BeautifulSoup, Tag, NavigableString, Comment
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False
    logger.debug("BeautifulSoup not installed — using regex content extractor")


# ═══════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════

# Tags whose entire subtree is noise
_NOISE_TAGS = frozenset({
    "script", "style", "noscript", "header", "footer", "nav",
    "aside", "form", "iframe", "svg", "meta", "link",
    "button", "input", "select", "textarea", "option",
    "dialog", "template", "slot",
})

# Tags that are block-level content containers
_BLOCK_TAGS = frozenset({
    "div", "section", "article", "main", "p", "blockquote",
    "figure", "figcaption", "details", "summary", "dd", "dt", "dl",
    "td", "th", "li", "tr",
})

# Semantic tags that indicate main content (high priority)
_MAIN_CONTENT_TAGS = frozenset({"article", "main"})
_MAIN_CONTENT_ROLES = frozenset({"main", "article"})

# Classes/IDs commonly used for main content
_CONTENT_CLASS_RE = re.compile(
    r"(?:article|content|post|entry|story|text|body|main)"
    r"(?:[-_](?:body|text|content|inner|main|wrap))?",
    re.IGNORECASE,
)

# Classes/IDs commonly used for boilerplate
_BOILERPLATE_CLASS_RE = re.compile(
    r"(?:sidebar|widget|menu|nav|footer|header|breadcrumb|"
    r"comment|share|social|related|recommend|popup|modal|"
    r"banner|advert|sponsor|promo|newsletter|signup|"
    r"cookie|consent|gdpr|notice|alert|search-form|"
    r"pagination|pager|tag-cloud|author-bio)",
    re.IGNORECASE,
)

# Bot protection / blocked page markers
_BOT_MARKERS = [
    "cloudflare", "ray id", "cf-browser-verification",
    "please verify you are human", "verify you are human",
    "checking your browser", "enable javascript and cookies",
    "just a moment", "attention required", "access denied",
    "error 1015", "error 1020", "sorry, you have been blocked",
    "please turn javascript on", "bot protection", "ddos protection",
    "security check", "captcha", "hcaptcha", "recaptcha",
    "challenge-platform", "page not found", "404 not found",
    "403 forbidden",
]

# Boilerplate line patterns (post-extraction cleanup)
_BOILERPLATE_LINE_PATTERNS = [
    re.compile(r"(?:cookie|cookies?)\s*(?:polic|settings|consent|notice)", re.I),
    re.compile(r"(?:privacy\s+policy|terms\s+of\s+(?:service|use))", re.I),
    re.compile(r"(?:subscribe|newsletter|sign\s*up)\s*(?:to|for|now)", re.I),
    re.compile(r"(?:all\s+rights?\s+reserved|©\s*\d{4})", re.I),
    re.compile(r"(?:follow\s+us|share\s+(?:on|this))\s", re.I),
    re.compile(r"(?:advertisement|sponsored|ad\s*block)", re.I),
    re.compile(r"(?:мы\s+используем\s+cookie|политика\s+конфиденциальности)", re.I),
    re.compile(r"(?:read\s+more|показать\s+ещё|загрузить\s+ещё)\s*\.{0,3}$", re.I),
    re.compile(r"^\s*(?:prev|next|previous|назад|далее|вперёд)\s*$", re.I),
    re.compile(r"(?:sign\s*in|log\s*in|войти|регистрация)\s*$", re.I),
]

# Whitespace normalization
_WS_INLINE = re.compile(r"[ \t]+")
_WS_MULTI_NL = re.compile(r"\n{3,}")
_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_ENTITY_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;")


# ═══════════════════════════════════════════════════
#  Data classes
# ═══════════════════════════════════════════════════

@dataclass
class ContentBlock:
    """A scored content block from the DOM."""
    text: str
    tag_name: str = ""
    text_density: float = 0.0     # chars of text / total chars including tags
    link_density: float = 0.0     # link text chars / total text chars
    word_count: int = 0
    sentence_count: int = 0
    dom_depth: int = 0
    score: float = 0.0
    is_structured: bool = False   # table / list (preserve atomically)

    def __post_init__(self):
        if self.word_count == 0 and self.text:
            self.word_count = len(self.text.split())


@dataclass
class ExtractionResult:
    """Result of content extraction from one page."""
    title: str = ""
    main_text: str = ""
    structured_blocks: List[str] = field(default_factory=list)
    word_count: int = 0
    content_quality: float = 0.0       # [0-1] estimated quality
    is_bot_page: bool = False
    extraction_method: str = "unknown"  # "semantic" | "article_tag" | "density" | "regex"

    @property
    def is_usable(self) -> bool:
        """Minimum quality check for downstream processing."""
        return (
            not self.is_bot_page
            and self.word_count >= 30
            and self.content_quality >= 0.2
        )


# ═══════════════════════════════════════════════════
#  Content Extractor
# ═══════════════════════════════════════════════════

class ContentExtractor:
    """
    Production-grade content extractor using text density analysis.

    Extraction strategy (in priority order):
      1. Semantic tags: <article>, <main>, role="main"
      2. Content-class heuristic: div.content, div.article, etc.
      3. Text density analysis: find highest-density subtree
      4. Body fallback with aggressive boilerplate removal
      5. Regex fallback (no BS4)

    Each strategy produces a candidate; the best candidate wins
    based on word count, sentence density, and link density.

    Usage:
        extractor = ContentExtractor()
        result = extractor.extract(html)
        if result.is_usable:
            process(result.main_text)
    """

    def __init__(
        self,
        min_text_length: int = 50,
        max_output_length: int = 80_000,
        min_content_quality: float = 0.15,
    ):
        self._min_text_len = min_text_length
        self._max_output_len = max_output_length
        self._min_quality = min_content_quality

    def extract(self, html: str) -> ExtractionResult:
        """
        Extract main content from raw HTML.

        Strategy (priority order):
          0. readability-lxml (from lina.parser) — best quality
          1. BS4 multi-strategy (semantic/density/class heuristic) — fallback
          2. Regex — last resort

        Args:
            html: Raw HTML string.

        Returns:
            ExtractionResult with cleaned text and quality metadata.
        """
        if not html or len(html.strip()) < 20:
            return ExtractionResult(is_bot_page=True)

        # ── Strategy 0: readability-lxml via lina.parser ──
        try:
            from lina.parser.page_parser import extract_text as _parser_extract
            from lina.parser.text_cleaner import clean_extracted_text
            text = _parser_extract(html)
            if text:
                text = clean_extracted_text(text)
            if text and len(text.split()) >= 30:
                title = self.extract_title(html)
                word_count = len(text.split())
                # Trim to max output length
                if len(text) > self._max_output_len:
                    text = text[:self._max_output_len]
                return ExtractionResult(
                    title=title,
                    main_text=text,
                    word_count=word_count,
                    content_quality=0.85,  # readability usually yields good quality
                    extraction_method="readability",
                )
        except ImportError:
            pass  # readability-lxml not installed, fall through
        except Exception as e:
            logger.debug("readability extraction failed: %s — falling back to BS4", e)

        # ── Strategy 1-4: BS4 / regex fallbacks ──
        if _HAS_BS4:
            return self._extract_bs4(html)
        else:
            return self._extract_regex(html)

    def extract_title(self, html: str) -> str:
        """Extract page title from HTML."""
        if _HAS_BS4:
            try:
                soup = BeautifulSoup(html, "html.parser")
                tag = soup.find("title")
                return tag.get_text(strip=True) if tag else ""
            except Exception:
                pass
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""

    # ═══════════════════════════════════════════════
    #  BS4-based extraction (primary path)
    # ═══════════════════════════════════════════════

    def _extract_bs4(self, html: str) -> ExtractionResult:
        """Multi-strategy BS4 extraction with best-candidate selection."""
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.debug("BS4 parse failed: %s", e)
            return self._extract_regex(html)

        title = ""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

        # Remove noise subtrees first (saves processing for all strategies)
        self._remove_noise_subtrees(soup)

        # Remove HTML comments
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        # ── Strategy 1: Semantic tags ──
        candidate_semantic = self._try_semantic_tags(soup)

        # ── Strategy 2: Content-class heuristic ──
        candidate_class = self._try_content_classes(soup)

        # ── Strategy 3: Text density analysis ──
        candidate_density = self._try_density_analysis(soup)

        # ── Strategy 4: Body fallback ──
        candidate_body = self._try_body_fallback(soup)

        # ── Select best candidate ──
        candidates = [
            (candidate_semantic, "semantic", 1.3),     # bonus for semantic tags
            (candidate_class, "class_heuristic", 1.1),
            (candidate_density, "density", 1.0),
            (candidate_body, "body_fallback", 0.7),
        ]

        best_text = ""
        best_score = -1.0
        best_method = "none"

        for text, method, bonus in candidates:
            if not text or len(text.strip()) < self._min_text_len:
                continue
            score = self._score_candidate(text) * bonus
            if score > best_score:
                best_score = score
                best_text = text
                best_method = method

        if not best_text:
            return ExtractionResult(
                title=title,
                is_bot_page=self._is_bot_page(soup.get_text()),
                extraction_method="none",
            )

        # ── Post-processing ──
        cleaned = self._normalize_text(best_text)
        cleaned = self._remove_boilerplate_lines(cleaned)

        # Truncate
        if len(cleaned) > self._max_output_len:
            cleaned = cleaned[:self._max_output_len] + "…"

        # Quality assessment
        wc = len(cleaned.split())
        quality = self._assess_quality(cleaned)
        is_bot = self._is_bot_page(cleaned)

        return ExtractionResult(
            title=title,
            main_text=cleaned,
            word_count=wc,
            content_quality=quality,
            is_bot_page=is_bot,
            extraction_method=best_method,
        )

    # ── Strategy implementations ──

    def _score_candidate(self, text: str) -> float:
        """Score an extraction candidate by text quality indicators.

        Combines word count, sentence density, and link-text ratio
        into a single score for comparing extraction strategies.
        """
        words = text.split()
        wc = len(words)
        if wc < 10:
            return 0.0

        # Word count factor (log scale)
        wc_factor = math.log2(wc + 1)

        # Sentence count
        import re
        sentences = len(re.findall(r'[.!?…]\s', text)) + 1
        sent_factor = min(sentences / 5.0, 2.0)

        return wc_factor * sent_factor

    def _try_semantic_tags(self, soup) -> str:
        """Strategy 1: Find <article>, <main>, role=main."""
        for finder in [
            lambda: soup.find("article"),
            lambda: soup.find("main"),
            lambda: soup.find(attrs={"role": "main"}),
        ]:
            tag = finder()
            if tag:
                text = self._extract_structured_text(tag)
                if text and len(text.strip()) > self._min_text_len:
                    return text
        return ""

    def _try_content_classes(self, soup) -> str:
        """Strategy 2: Find divs with content-related class/id names."""
        for attr in ("class", "id"):
            candidates = soup.find_all(
                ["div", "section"],
                attrs={attr: _CONTENT_CLASS_RE},
            )
            if candidates:
                # Pick the largest one
                best = max(
                    candidates,
                    key=lambda t: len(t.get_text(strip=True)),
                )
                text = self._extract_structured_text(best)
                if text and len(text.strip()) > self._min_text_len:
                    return text
        return ""

    def _try_density_analysis(self, soup) -> str:
        """
        Strategy 3: Text density analysis.

        Algorithm:
          1. Find all block-level elements
          2. Calculate text density = text chars / (text chars + tag chars)
          3. Calculate link density = link text / total text
          4. Score = text_density × (1 - link_density) × log(word_count + 1)
          5. Select highest-scoring block

        Blocks with high link density (navigation) or very short text
        are penalized. Blocks with many sentences are boosted.
        """
        body = soup.find("body") or soup
        blocks = self._collect_content_blocks(body)

        if not blocks:
            return ""

        # Score and rank
        for block in blocks:
            block.score = self._compute_block_score(block)

        blocks.sort(key=lambda b: b.score, reverse=True)

        # Take top block + adjacent high-scoring blocks
        if not blocks or blocks[0].score <= 0:
            return ""

        # Threshold: blocks within 40% of top score
        threshold = blocks[0].score * 0.40
        selected = [b for b in blocks if b.score >= threshold]

        # Combine texts
        parts = [b.text for b in selected if b.text.strip()]
        return "\n\n".join(parts)

    def _try_body_fallback(self, soup) -> str:
        """Strategy 4: Body with boilerplate class removal."""
        body = soup.find("body") or soup

        # Remove elements with boilerplate classes
        for tag in body.find_all(True):
            classes = " ".join(tag.get("class", []))
            tag_id = tag.get("id", "")
            combined = f"{classes} {tag_id}"
            if _BOILERPLATE_CLASS_RE.search(combined):
                tag.decompose()

        return self._extract_structured_text(body)

    # ═══════════════════════════════════════════════
    #  Text density computation
    # ═══════════════════════════════════════════════

    def _collect_content_blocks(self, root, depth: int = 0) -> List[ContentBlock]:
        """Recursively collect block-level elements with density metrics."""
        if not _HAS_BS4:
            return []

        blocks: List[ContentBlock] = []

        for child in root.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            tag_name = (child.name or "").lower()
            if tag_name in _NOISE_TAGS:
                continue

            # Get text content
            all_text = child.get_text(separator=" ", strip=True)
            if not all_text or len(all_text) < 20:
                continue

            # Calculate link text
            link_text_len = sum(
                len(a.get_text(strip=True))
                for a in child.find_all("a")
            )
            total_text_len = len(all_text)

            # Text density: ratio of visible text to total element size
            html_len = len(str(child))
            text_density = total_text_len / max(html_len, 1)

            # Link density
            link_density = link_text_len / max(total_text_len, 1)

            # Sentence count (rough)
            sentence_count = len(re.findall(r'[.!?…]\s', all_text)) + 1

            word_count = len(all_text.split())

            block = ContentBlock(
                text=self._extract_structured_text(child) if tag_name in _BLOCK_TAGS else all_text,
                tag_name=tag_name,
                text_density=text_density,
                link_density=link_density,
                word_count=word_count,
                sentence_count=sentence_count,
                dom_depth=depth,
                is_structured=(tag_name in ("table", "ul", "ol", "dl")),
            )
            blocks.append(block)

            # Recurse into block-level children
            if tag_name in ("div", "section", "main", "article", "body"):
                child_blocks = self._collect_content_blocks(child, depth + 1)
                blocks.extend(child_blocks)

        return blocks

    def _compute_block_score(self, block: ContentBlock) -> float:
        """
        Score a content block for main-content likelihood.

        Scoring formula:
          score = text_density × (1 - link_density)²
                  × log2(word_count + 1)
                  × sentence_bonus
                  × tag_bonus
                  - depth_penalty

        High link density (navs) → low score.
        Many sentences (article text) → high score.
        Semantic tags (article/main) → boosted.
        Deep nesting → slight penalty.
        """
        if block.word_count < 10:
            return 0.0

        # Base: text density × inverse link density
        base = block.text_density * (1.0 - block.link_density) ** 2

        # Word count factor (log scale to avoid mega-block domination)
        wc_factor = math.log2(block.word_count + 1)

        # Sentence bonus: more sentences = more likely real content
        sent_bonus = 1.0 + min(block.sentence_count / 10.0, 1.0)

        # Tag-specific bonuses
        tag_bonus = 1.0
        if block.tag_name in ("article", "main"):
            tag_bonus = 2.0
        elif block.tag_name in ("section", "p"):
            tag_bonus = 1.3
        elif block.tag_name == "table":
            # Tables are often spec sheets (high value for RAG)
            tag_bonus = 1.5 if block.word_count > 30 else 0.8

        # Depth penalty (deeper = more likely sidebar/widget)
        depth_penalty = block.dom_depth * 0.05

        score = base * wc_factor * sent_bonus * tag_bonus - depth_penalty
        return max(score, 0.0)

    # ═══════════════════════════════════════════════
    #  Structured text extraction
    # ═══════════════════════════════════════════════

    def _extract_structured_text(self, element) -> str:
        """
        Extract text preserving semantic structure.

        Tables → Key: Value lines (spec sheets)
        Lists → • Item lines
        Code → ```block```
        Headings → ## Heading
        """
        if not _HAS_BS4 or element is None:
            return ""

        parts: List[str] = []

        for child in element.children:
            if isinstance(child, NavigableString):
                text = child.strip()
                if text:
                    parts.append(text)
                continue
            if not isinstance(child, Tag):
                continue

            tag_name = (child.name or "").lower()

            if tag_name in _NOISE_TAGS:
                continue
            if tag_name == "table":
                table_text = self._extract_table(child)
                if table_text:
                    parts.append(table_text)
                continue
            if tag_name in ("ul", "ol"):
                list_text = self._extract_list(child)
                if list_text:
                    parts.append(list_text)
                continue
            if tag_name in ("pre", "code"):
                code = child.get_text(strip=True)
                if code and len(code) > 5:
                    parts.append(f"```\n{code}\n```")
                continue
            if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                heading = child.get_text(strip=True)
                if heading:
                    parts.append(f"\n## {heading}")
                continue
            if tag_name in _BLOCK_TAGS:
                inner = self._extract_structured_text(child)
                if inner:
                    parts.append(inner)
                continue

            # Inline elements
            text = child.get_text(separator=" ", strip=True)
            if text:
                parts.append(text)

        return "\n".join(parts)

    def _extract_table(self, table_tag) -> str:
        """Convert HTML table to structured KV text."""
        if not _HAS_BS4:
            return ""

        rows = table_tag.find_all("tr")
        if not rows:
            return ""

        matrix: List[List[str]] = []
        for row in rows:
            cells = row.find_all(["td", "th"])
            cell_texts = [c.get_text(strip=True) for c in cells]
            if any(cell_texts):
                matrix.append(cell_texts)

        if not matrix:
            return ""

        max_cols = max(len(r) for r in matrix)

        # 2-column spec table: Label: Value
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

        # Multi-column: detect headers
        lines = []
        headers: List[str] = []
        first_row_tag = rows[0]
        th_cells = first_row_tag.find_all("th")
        if th_cells:
            headers = [th.get_text(strip=True) for th in th_cells]

        for i, row_cells in enumerate(matrix):
            if i == 0 and headers:
                continue
            if headers and len(row_cells) == len(headers):
                pairs = [f"{h}: {v}" for h, v in zip(headers, row_cells) if h and v]
                if pairs:
                    lines.append(" | ".join(pairs))
            else:
                joined = " | ".join(c for c in row_cells if c)
                if joined:
                    lines.append(joined)

        return "\n".join(lines)

    def _extract_list(self, list_tag) -> str:
        """Convert HTML list to bulleted text."""
        if not _HAS_BS4:
            return ""
        items = list_tag.find_all("li", recursive=False)
        lines = []
        for item in items:
            text = item.get_text(separator=" ", strip=True)
            if text:
                lines.append(f"• {text}")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════
    #  Noise removal
    # ═══════════════════════════════════════════════

    def _remove_noise_subtrees(self, soup) -> None:
        """Remove known noise tags and boilerplate-class elements."""
        # Remove noise tags
        for tag in soup.find_all(list(_NOISE_TAGS)):
            tag.decompose()

        # Remove hidden elements
        for tag in soup.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
            tag.decompose()
        for tag in soup.find_all(attrs={"hidden": True}):
            tag.decompose()
        for tag in soup.find_all(attrs={"aria-hidden": "true"}):
            # Skip if it contains substantial text (false positive)
            if len(tag.get_text(strip=True)) < 50:
                tag.decompose()

    # ═══════════════════════════════════════════════
    #  Text normalization
    # ═══════════════════════════════════════════════

    def _normalize_text(self, text: str) -> str:
        """Normalize whitespace, fix line breaks, merge orphan lines."""
        if not text:
            return ""

        lines = text.split("\n")
        normalized: List[str] = []

        for line in lines:
            # Collapse inline whitespace
            line = _WS_INLINE.sub(" ", line).strip()
            normalized.append(line)

        text = "\n".join(normalized)

        # Collapse 3+ consecutive blank lines to 2
        text = _WS_MULTI_NL.sub("\n\n", text)

        # Merge orphan short lines (< 40 chars not ending with punctuation)
        # with the next line — these are usually broken paragraphs
        lines = text.split("\n")
        merged: List[str] = []
        buffer = ""

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if buffer:
                    merged.append(buffer)
                    buffer = ""
                merged.append("")
                continue

            if buffer:
                # If buffer is a short non-terminal line, merge
                if (len(buffer) < 40
                        and buffer[-1] not in ".!?…:;•"
                        and not buffer.startswith("##")
                        and not buffer.startswith("```")):
                    buffer = buffer + " " + stripped
                else:
                    merged.append(buffer)
                    buffer = stripped
            else:
                buffer = stripped

        if buffer:
            merged.append(buffer)

        return "\n".join(merged).strip()

    def _remove_boilerplate_lines(self, text: str) -> str:
        """Remove lines matching boilerplate patterns."""
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                cleaned.append("")
                continue
            if len(stripped) < 200:
                if any(p.search(stripped) for p in _BOILERPLATE_LINE_PATTERNS):
                    continue
            cleaned.append(line)
        return "\n".join(cleaned)

    # ═══════════════════════════════════════════════
    #  Quality assessment
    # ═══════════════════════════════════════════════

    def _assess_quality(self, text: str) -> float:
        """
        Estimate content quality on [0, 1] scale.

        Factors:
          - Word count (more is better, diminishing returns)
          - Sentence density (real articles have many sentences)
          - Average word length (gibberish tends to be short or very long)
          - Repeated line ratio (duplication = low quality)
        """
        if not text:
            return 0.0

        words = text.split()
        wc = len(words)
        if wc < 20:
            return 0.1

        # Word count factor: log scale, cap at 1.0
        wc_score = min(math.log10(wc + 1) / 3.0, 1.0)  # 1000 words → 1.0

        # Sentence density
        sentences = len(re.findall(r'[.!?…]\s', text)) + 1
        sent_density = sentences / max(wc / 20.0, 1.0)  # sentences per 20 words
        sent_score = min(sent_density, 1.0)

        # Average word length (3-8 is normal for natural language)
        avg_wl = sum(len(w) for w in words) / max(wc, 1)
        wl_score = 1.0 if 3.0 <= avg_wl <= 10.0 else 0.5

        # Repetition detection: ratio of unique lines
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if lines:
            unique_ratio = len(set(lines)) / len(lines)
        else:
            unique_ratio = 1.0
        repeat_score = unique_ratio

        quality = (
            0.35 * wc_score
            + 0.25 * sent_score
            + 0.15 * wl_score
            + 0.25 * repeat_score
        )
        return round(min(quality, 1.0), 3)

    def _is_bot_page(self, text: str) -> bool:
        """Detect bot protection / error pages."""
        if not text:
            return True
        lower = text.lower()
        if len(lower) > 3000:
            return False
        hits = sum(1 for m in _BOT_MARKERS if m in lower)
        return hits >= 2

    # ═══════════════════════════════════════════════
    #  Regex fallback (no BS4)
    # ═══════════════════════════════════════════════

    def _extract_regex(self, html: str) -> ExtractionResult:
        """Fallback extraction using regex when BS4 is unavailable."""
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()

        # Remove script/style blocks
        text = re.sub(
            r"<(script|style|noscript|nav|header|footer|aside)[^>]*>.*?</\1>",
            "", html, flags=re.S | re.I,
        )
        text = _TAG_RE.sub(" ", text)
        text = text.replace("&nbsp;", " ")
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&quot;", '"')
        text = _ENTITY_RE.sub("", text)

        text = self._normalize_text(text)
        text = self._remove_boilerplate_lines(text)

        if len(text) > self._max_output_len:
            text = text[:self._max_output_len] + "…"

        wc = len(text.split())
        quality = self._assess_quality(text)

        return ExtractionResult(
            title=title,
            main_text=text,
            word_count=wc,
            content_quality=quality * 0.7,  # Regex is lower quality
            is_bot_page=self._is_bot_page(text),
            extraction_method="regex",
        )


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_extractor: ContentExtractor | None = None


def get_content_extractor() -> ContentExtractor:
    global _extractor
    if _extractor is None:
        _extractor = ContentExtractor()
    return _extractor
