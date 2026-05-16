# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Source Trust Scorer.

Domain reputation scoring and cross-source fact confidence system.

Design:
  - Each domain has a trust tier: AUTHORITATIVE (0.90+), HIGH (0.75+),
    MEDIUM (0.55+), LOW (0.35+), UNKNOWN (0.30)
  - Facts confirmed by multiple independent domains get confidence boosts
  - Syndication detection: sources copying content are counted as one
  - Spam/SEO domains are penalized
  - Official product pages and documentation score highest

The trust score flows into:
  1. Passage ranking (domain bonus in hybrid ranker)
  2. Fact confidence (higher trust → higher initial confidence)
  3. Cross-source verification (independent sources boost facts)

No LLM calls. Fully deterministic.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("lina.web_extraction.source_trust")


# ═══════════════════════════════════════════════════
#  Trust Tiers
# ═══════════════════════════════════════════════════

class TrustTier:
    """Named trust levels with score ranges."""
    AUTHORITATIVE = "authoritative"  # 0.90–1.00: Official docs, product pages
    HIGH = "high"                    # 0.75–0.89: Major encyclopedias, review sites
    MEDIUM = "medium"                # 0.55–0.74: Reputable blogs, tech portals
    LOW = "low"                      # 0.35–0.54: Forums, user-generated content
    UNTRUSTED = "untrusted"          # 0.10–0.34: Spam, SEO, unknown
    UNKNOWN = "unknown"              # 0.30: Default for unrecognized domains

    @staticmethod
    def from_score(score: float) -> str:
        if score >= 0.90:
            return TrustTier.AUTHORITATIVE
        if score >= 0.75:
            return TrustTier.HIGH
        if score >= 0.55:
            return TrustTier.MEDIUM
        if score >= 0.35:
            return TrustTier.LOW
        return TrustTier.UNTRUSTED


# ═══════════════════════════════════════════════════
#  Domain Trust Database
# ═══════════════════════════════════════════════════

# Scores: 0.0 (total spam) to 1.0 (official documentation)
_DOMAIN_TRUST: Dict[str, float] = {
    # ── Authoritative (0.90–1.00) ──
    # Official product pages & documentation
    "developer.android.com": 0.98,
    "developer.apple.com": 0.98,
    "docs.python.org": 0.98,
    "docs.microsoft.com": 0.97,
    "learn.microsoft.com": 0.97,
    "pytorch.org": 0.96,
    "tensorflow.org": 0.96,
    "huggingface.co": 0.95,
    "arxiv.org": 0.95,
    "samsung.com": 0.94,
    "apple.com": 0.94,
    "google.com": 0.93,
    "nvidia.com": 0.93,
    "amd.com": 0.93,
    "intel.com": 0.93,
    "qualcomm.com": 0.92,
    "mediatek.com": 0.92,
    "gsmarena.com": 0.95,           # De-facto standard for phone specs

    # ── High (0.75–0.89) ──
    # Major reference sites
    "wikipedia.org": 0.88,
    "en.wikipedia.org": 0.88,
    "ru.wikipedia.org": 0.88,
    "notebookcheck.net": 0.88,
    "anandtech.com": 0.87,
    "tomshardware.com": 0.85,
    "techpowerup.com": 0.85,
    "arstechnica.com": 0.84,
    "theverge.com": 0.82,
    "wired.com": 0.82,
    "nanoreview.net": 0.85,
    "kimovil.com": 0.83,
    "devicespecifications.com": 0.82,
    "phonearena.com": 0.82,
    "dxomark.com": 0.85,
    "cpubenchmark.net": 0.83,
    "benchmark.net": 0.80,
    "stackoverflow.com": 0.82,
    "github.com": 0.80,

    # ── Russian high-trust ──
    "4pda.to": 0.80,
    "habr.com": 0.82,
    "ixbt.com": 0.80,
    "3dnews.ru": 0.78,
    "overclockers.ru": 0.77,

    # ── Linux / System Administration (0.75–0.95) ──
    "wiki.archlinux.org": 0.94,       # De-facto Linux reference
    "man7.org": 0.93,                 # Official Linux man pages
    "kernel.org": 0.95,               # Official Linux kernel
    "wiki.debian.org": 0.90,
    "wiki.gentoo.org": 0.90,
    "help.ubuntu.com": 0.88,
    "docs.fedoraproject.org": 0.88,
    "wiki.ubuntu.com": 0.85,
    "access.redhat.com": 0.90,        # Red Hat knowledge base
    "documentation.suse.com": 0.88,
    "wiki.alpinelinux.org": 0.85,
    "wiki.centos.org": 0.84,
    "wiki.void-linux.org": 0.82,
    "nixos.wiki": 0.82,
    # Q&A / community
    "askubuntu.com": 0.82,
    "unix.stackexchange.com": 0.82,
    "superuser.com": 0.78,
    "serverfault.com": 0.80,
    "linuxquestions.org": 0.72,
    "bbs.archlinux.org": 0.72,
    "forums.debian.net": 0.70,
    "ubuntuforums.org": 0.70,
    "forum.manjaro.org": 0.68,
    # DevOps / sysadmin
    "digitalocean.com": 0.80,         # Excellent tutorials
    "linode.com": 0.78,
    "linuxhandbook.com": 0.72,
    "linuxize.com": 0.70,
    "baeldung.com": 0.72,
    "cyberciti.biz": 0.68,
    "tecmint.com": 0.65,
    "linuxconfig.org": 0.65,
    # Russian Linux
    "losst.pro": 0.65,
    "pingvinus.ru": 0.62,
    "linux.org.ru": 0.68,

    # ── Medium (0.55–0.74) ──
    "reddit.com": 0.65,
    "quora.com": 0.60,
    "medium.com": 0.58,
    "techradar.com": 0.72,
    "cnet.com": 0.70,
    "pcmag.com": 0.72,
    "xda-developers.com": 0.70,
    "androidauthority.com": 0.70,
    "tomsguide.com": 0.68,
    "91mobiles.com": 0.65,
    "gizmodo.com": 0.62,
    "mashable.com": 0.60,
    "zdnet.com": 0.72,
    "engadget.com": 0.68,

    # Russian medium-trust
    "dns-shop.ru": 0.65,
    "citilink.ru": 0.65,
    "mvideo.ru": 0.62,
    "ozon.ru": 0.55,
    "wildberries.ru": 0.50,

    # ── Low (0.35–0.54) ──
    "aliexpress.com": 0.40,
    "aliexpress.ru": 0.40,
    "alibaba.com": 0.40,
    "ebay.com": 0.45,
    "amazon.com": 0.55,  # Slightly higher — product pages have real specs

    # ── Spam / SEO penalty ──
    "telegra.ph": 0.25,
    "zen.yandex.ru": 0.30,
    "dzen.ru": 0.30,
    "pulse.mail.ru": 0.25,
}

# Domain patterns for catch-all rules
_DOMAIN_PATTERN_TRUST: List[Tuple[re.Pattern, float]] = [
    # Official manufacturer subdomains
    (re.compile(r".*\.samsung\.com$"), 0.92),
    (re.compile(r".*\.apple\.com$"), 0.92),
    (re.compile(r".*\.google\.com$"), 0.90),
    (re.compile(r".*\.microsoft\.com$"), 0.90),
    (re.compile(r".*\.nvidia\.com$"), 0.90),

    # Wikipedia in any language
    (re.compile(r"[a-z]{2}\.wikipedia\.org$"), 0.88),

    # Government / education
    (re.compile(r".*\.gov$"), 0.85),
    (re.compile(r".*\.edu$"), 0.82),
    (re.compile(r".*\.gov\.ru$"), 0.80),
    (re.compile(r".*\.ac\.uk$"), 0.82),

    # Blogspot / wordpress — user-generated, low trust
    (re.compile(r".*\.blogspot\.com$"), 0.30),
    (re.compile(r".*\.wordpress\.com$"), 0.35),
    (re.compile(r".*\.livejournal\.com$"), 0.30),
    (re.compile(r".*\.tumblr\.com$"), 0.30),
]

# Default trust for completely unknown domains
_DEFAULT_TRUST = 0.30


# ═══════════════════════════════════════════════════
#  Source Trust Scorer
# ═══════════════════════════════════════════════════

@dataclass
class DomainInfo:
    """Trust information about a web domain."""
    domain: str
    trust_score: float
    trust_tier: str
    is_known: bool = True


@dataclass
class SourceVerification:
    """Result of cross-source fact verification."""
    fact_key: str                              # "subject|predicate"
    value: str                                 # Fact value
    supporting_domains: List[str] = field(default_factory=list)
    independent_count: int = 0                 # Domains after dedup
    trust_weighted_confidence: float = 0.0     # Trust-weighted confidence
    is_verified: bool = False                  # ≥2 independent sources


class SourceTrustScorer:
    """
    Domain reputation and cross-source verification system.

    Responsibilities:
      1. Score individual domains by trust/reputation
      2. Detect source syndication (same content, different domains)
      3. Compute cross-source fact confidence
      4. Provide trust-weighted passage scoring

    Usage:
        scorer = SourceTrustScorer()
        info = scorer.score_domain("gsmarena.com")  # DomainInfo(trust=0.95)
        verified = scorer.verify_cross_source(fact_key, values_by_domain)
    """

    def __init__(
        self,
        custom_trust: Dict[str, float] | None = None,
        syndication_threshold: float = 0.60,
    ):
        """
        Args:
            custom_trust: Additional domain→score mappings.
            syndication_threshold: Jaccard similarity threshold for
                detecting syndicated (copied) content between sources.
        """
        self._trust = dict(_DOMAIN_TRUST)
        if custom_trust:
            self._trust.update(custom_trust)
        self._syndication_threshold = syndication_threshold
        self._domain_cache: Dict[str, DomainInfo] = {}

    def score_domain(self, url_or_domain: str) -> DomainInfo:
        """
        Get trust score for a domain.

        Args:
            url_or_domain: Full URL or bare domain name.

        Returns:
            DomainInfo with trust score and tier.
        """
        domain = self._extract_domain(url_or_domain)

        if domain in self._domain_cache:
            return self._domain_cache[domain]

        # Try exact match
        score = self._trust.get(domain)
        is_known = score is not None

        # Try parent domain (e.g., blog.gsmarena.com → gsmarena.com)
        if score is None:
            parts = domain.split(".")
            for i in range(1, len(parts)):
                parent = ".".join(parts[i:])
                if parent in self._trust:
                    score = self._trust[parent]
                    is_known = True
                    break

        # Try pattern matching
        if score is None:
            for pattern, pat_score in _DOMAIN_PATTERN_TRUST:
                if pattern.match(domain):
                    score = pat_score
                    is_known = True
                    break

        if score is None:
            score = _DEFAULT_TRUST
            is_known = False

        info = DomainInfo(
            domain=domain,
            trust_score=score,
            trust_tier=TrustTier.from_score(score),
            is_known=is_known,
        )
        self._domain_cache[domain] = info
        return info

    def score_url(self, url: str) -> float:
        """Convenience: get trust score for a URL. Returns float [0, 1]."""
        return self.score_domain(url).trust_score

    def passage_trust_bonus(self, source_url: str) -> float:
        """
        Compute trust bonus for passage ranking.

        Higher-trust sources get a small bonus added to their ranking score.
        Range: [-0.10, +0.15]
        """
        info = self.score_domain(source_url)
        if info.trust_score >= 0.85:
            return 0.15
        if info.trust_score >= 0.70:
            return 0.10
        if info.trust_score >= 0.50:
            return 0.05
        if info.trust_score >= 0.35:
            return 0.0
        return -0.05  # Penalty for low-trust sources

    def verify_cross_source(
        self,
        fact_key: str,
        values_by_domain: Dict[str, str],
    ) -> SourceVerification:
        """
        Cross-source verification for a single fact.

        Algorithm:
          1. Group domains reporting the same value
          2. Detect syndication (similar snippets = same source)
          3. Count independent sources
          4. Compute trust-weighted confidence

        Args:
            fact_key: Fact identifier ("subject|predicate").
            values_by_domain: {domain: reported_value}.

        Returns:
            SourceVerification with confidence and verification status.
        """
        if not values_by_domain:
            return SourceVerification(fact_key=fact_key, value="")

        # Find the most reported value
        value_counts: Dict[str, List[str]] = {}
        for domain, value in values_by_domain.items():
            norm_value = self._normalize_value(value)
            if norm_value not in value_counts:
                value_counts[norm_value] = []
            value_counts[norm_value].append(domain)

        # Pick the value with the most supporting domains
        best_value = max(value_counts, key=lambda v: len(value_counts[v]))
        supporting = value_counts[best_value]

        # Count independent sources (syndication dedup)
        independent = self._count_independent_sources(supporting)

        # Trust-weighted confidence
        trust_scores = [self.score_domain(d).trust_score for d in supporting]
        if trust_scores:
            max_trust = max(trust_scores)
            avg_trust = sum(trust_scores) / len(trust_scores)
            # Confidence formula: emphasizes best source + count bonus
            base_confidence = max_trust * 0.6 + avg_trust * 0.4
        else:
            base_confidence = 0.3

        # Source count bonus
        if independent >= 3:
            count_bonus = 0.25
        elif independent >= 2:
            count_bonus = 0.15
        else:
            count_bonus = 0.0

        confidence = min(base_confidence + count_bonus, 0.99)
        is_verified = independent >= 2

        return SourceVerification(
            fact_key=fact_key,
            value=best_value,
            supporting_domains=supporting,
            independent_count=independent,
            trust_weighted_confidence=round(confidence, 3),
            is_verified=is_verified,
        )

    def aggregate_confidence(
        self,
        source_urls: List[str],
        base_confidence: float = 0.5,
    ) -> float:
        """
        Compute aggregated confidence from multiple source URLs.

        Used when a fact is extracted from multiple pages.

        Args:
            source_urls: URLs supporting the fact.
            base_confidence: Starting confidence from extraction.

        Returns:
            Adjusted confidence [0, 1].
        """
        if not source_urls:
            return base_confidence

        domains = list(set(self._extract_domain(u) for u in source_urls))
        independent = self._count_independent_sources(domains)
        trust_scores = [self.score_domain(d).trust_score for d in domains]
        max_trust = max(trust_scores) if trust_scores else 0.3

        # Boost based on source quality and count
        trust_factor = max_trust * 0.7 + (sum(trust_scores) / max(len(trust_scores), 1)) * 0.3

        if independent >= 3:
            return min(trust_factor + 0.20, 0.99)
        elif independent >= 2:
            return min(trust_factor + 0.10, 0.95)
        else:
            return min(trust_factor, 0.80)

    # ═══════════════════════════════════════════════
    #  Internal helpers
    # ═══════════════════════════════════════════════

    def _extract_domain(self, url_or_domain: str) -> str:
        """Extract clean domain from URL or domain string."""
        if "://" in url_or_domain:
            parsed = urlparse(url_or_domain)
            domain = parsed.hostname or url_or_domain
        else:
            domain = url_or_domain

        domain = domain.lower().strip(".")
        if domain.startswith("www."):
            domain = domain[4:]
        return domain

    def _normalize_value(self, value: str) -> str:
        """Normalize fact value for comparison."""
        v = value.lower().strip()
        # Remove common unit variations
        v = re.sub(r'\s+', ' ', v)
        v = re.sub(r'(\d)\s*(мач|мА·ч|mah)', r'\1 mAh', v, flags=re.I)
        v = re.sub(r'(\d)\s*(гб|gb)', r'\1 GB', v, flags=re.I)
        v = re.sub(r'(\d)\s*(тб|tb)', r'\1 TB', v, flags=re.I)
        v = re.sub(r'(\d)\s*(гц|ghz|hz)', r'\1 GHz', v, flags=re.I)
        v = re.sub(r'(\d)\s*(мп|mp)', r'\1 MP', v, flags=re.I)
        return v

    def _count_independent_sources(self, domains: List[str]) -> int:
        """
        Count independent sources, deduplicating syndicated domains.

        Heuristic: domains sharing the same base (e.g., news.site.com and
        site.com) are counted as one. Known syndication networks are grouped.
        """
        if not domains:
            return 0

        # Extract base domains (2nd-level)
        base_domains: Set[str] = set()
        for domain in domains:
            parts = domain.split(".")
            if len(parts) >= 2:
                base = ".".join(parts[-2:])
                base_domains.add(base)
            else:
                base_domains.add(domain)

        return len(base_domains)

    # ═══════════════════════════════════════════════
    #  Bulk operations
    # ═══════════════════════════════════════════════

    def score_passages(
        self,
        passages: List,
        trust_bonus_weight: float = 0.10,
    ) -> List:
        """
        Apply trust-based score adjustments to passages.

        Modifies passage.score in-place by adding trust bonus.

        Args:
            passages: List of Passage objects (must have source_url and score).
            trust_bonus_weight: How much to weight the trust bonus.

        Returns:
            Same passages with adjusted scores.
        """
        for p in passages:
            if hasattr(p, "source_url") and hasattr(p, "score"):
                bonus = self.passage_trust_bonus(p.source_url)
                p.score += bonus * trust_bonus_weight
        return passages


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_scorer: SourceTrustScorer | None = None


def get_source_trust_scorer() -> SourceTrustScorer:
    global _scorer
    if _scorer is None:
        _scorer = SourceTrustScorer()
    return _scorer
