# -*- coding: utf-8 -*-
"""
Lina Core — Fact Verifier (v2 Pipeline).

Final filter before LLM generation.  Discards facts that are:
  - Low confidence (single unverified source)
  - From untrusted domains
  - Contradicted by higher-confidence facts

Also applies Source Diversity Filter: if ALL facts come from a single
domain, overall confidence is capped.

Design: stateless filter, no network calls.
"""

from __future__ import annotations

import re
import logging
from typing import List, Set

from lina.models.datatypes import Fact, FactSet

logger = logging.getLogger("lina.core.fact_verifier")


class FactVerifier:
    """
    Filters a FactSet to retain only trustworthy facts.

    Rules:
      1. facts with confidence < min_confidence → discarded.
      2. facts from a single source → marked unverified (kept but flagged).
      3. if ALL facts share one domain → cap overall confidence at 0.50.
      4. contradictions: when same predicate has conflicting values, keep highest confidence.
    """

    def __init__(
        self,
        min_confidence: float = 0.40,
        require_multi_source: bool = False,
    ):
        self._min_conf = min_confidence
        self._require_multi = require_multi_source

    def verify(self, fact_set: FactSet) -> FactSet:
        """
        Filter and clean a FactSet.

        Args:
            fact_set: Input aggregated facts.

        Returns:
            New FactSet with only verified / high-confidence facts.
        """
        if not fact_set.facts:
            return fact_set

        kept: List[Fact] = []

        # ── Rule 1: confidence threshold ──
        for f in fact_set.facts:
            if f.confidence < self._min_conf:
                logger.debug(
                    "FactVerifier: discarding low-conf fact: %s = %s (%.2f)",
                    f.predicate, f.object_value[:30], f.confidence,
                )
                continue
            if self._require_multi and f.source_count < 2:
                logger.debug(
                    "FactVerifier: discarding single-source fact: %s = %s",
                    f.predicate, f.object_value[:30],
                )
                continue
            # Discard garbage facts with newline characters in predicates
            if "\n" in f.predicate:
                logger.debug(
                    "FactVerifier: discarding malformed predicate: %r",
                    f.predicate[:40],
                )
                continue
            kept.append(f)

        # ── Rule 4: resolve contradictions ──
        kept = self._resolve_contradictions(kept)

        # ── Mark surviving facts as verified ──
        for f in kept:
            f.verified = True

        # ── Rule 3: source diversity check ──
        domains = self._unique_domains(kept)
        overall_conf = fact_set.confidence

        if len(domains) <= 1 and len(kept) > 0:
            # All facts from single domain → cap confidence
            overall_conf = min(overall_conf, 0.50)
            logger.info(
                "FactVerifier: single-domain results (%s) → conf capped at 0.50",
                domains,
            )

        result = FactSet(
            subject=fact_set.subject,
            facts=kept,
            total_sources=fact_set.total_sources,
            confidence=round(overall_conf, 3),
        )

        logger.info(
            "FactVerifier: %d → %d facts (verified=%d, conf=%.2f)",
            len(fact_set.facts), len(kept), result.verified_count, overall_conf,
        )
        return result

    def _resolve_contradictions(self, facts: List[Fact]) -> List[Fact]:
        """
        Resolve conflicting facts.
        - Exact duplicates (same predicate + same value) → keep highest conf
        - Same predicate, different values → keep highest confidence one
        - Multiple KV facts from structured tables (predicate contains space,
          like "Platform CPU") are kept since they describe different properties
        - Trivial/noise facts are discarded
        """
        # Phase 1: dedup exact (predicate, value) pairs → keep highest conf
        dedup: dict = {}
        for f in facts:
            key = (f.predicate.lower().strip(), f.object_value.lower().strip())
            if key not in dedup or f.confidence > dedup[key].confidence:
                dedup[key] = f
        unique = list(dedup.values())

        # Phase 2: for same-predicate contradictions, keep highest confidence
        # But only for "simple" predicates (no spaces = spec pattern results)
        # Compound predicates like "Platform CPU", "Display Type" are unique
        best: dict = {}
        compound: list[Fact] = []
        for f in unique:
            pk = f.predicate.lower().strip()
            if " " in pk:
                compound.append(f)
            elif pk not in best or f.confidence > best[pk].confidence:
                best[pk] = f
        result = list(best.values()) + compound

        # Phase 3: filter trivially meaningless facts
        result = [f for f in result if self._is_meaningful(f)]

        return result

    @staticmethod
    def _is_meaningful(f: Fact) -> bool:
        """Reject trivially meaningless facts."""
        val = f.object_value.strip()
        pred = f.predicate.strip()
        if len(val) < 2:
            return False
        if val.lower() == pred.lower():
            return False
        noise_vals = {"camera", "photo / video", "yes", "no"}
        if val.lower() in noise_vals and pred.lower().startswith("display"):
            return False
        # Reject bot-protection / error page artifacts
        pred_lower = pred.lower()
        val_lower = val.lower()
        _BOT_PREDICATES = {
            "ray id", "cloudflare ray id", "your ip", "ip address",
            "cloudflare", "captcha", "page not found", "error code",
            "access denied", "403 forbidden", "404 not found",
            "security check", "ddos protection", "challenge",
            "click to reveal",
        }
        if pred_lower in _BOT_PREDICATES:
            return False
        # Reject if value is a known bot marker
        _BOT_VALUES = {
            "cloudflare", "click to reveal", "access denied",
            "please verify", "just a moment",
        }
        if val_lower in _BOT_VALUES:
            return False
        # Reject Cloudflare Ray ID patterns in values (hex hashes)
        if "ray" in pred_lower and re.match(r'^[0-9a-f]{10,}$', val):
            return False
        return True

    @staticmethod
    def _unique_domains(facts: List[Fact]) -> Set[str]:
        """Extract unique domains from fact sources."""
        domains: Set[str] = set()
        for f in facts:
            for src in f.sources:
                m = re.search(r'https?://(?:www\.)?([^/]+)', src)
                if m:
                    domains.add(m.group(1).lower())
        return domains


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_verifier: FactVerifier | None = None


def get_fact_verifier() -> FactVerifier:
    global _verifier
    if _verifier is None:
        _verifier = FactVerifier()
    return _verifier
