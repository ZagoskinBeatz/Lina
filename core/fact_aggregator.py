# -*- coding: utf-8 -*-
"""
Lina Core — Fact Aggregator (v2 Pipeline).

Merges duplicate facts from multiple sources, computes cross-source
confidence, and resolves conflicts.

Algorithm:
  1. Group facts by normalized key (subject|predicate).
  2. For each group:
     a) Count unique source URLs.
     b) Merge values (if identical) → boost confidence.
     c) Keep highest-confidence value if conflicting.
  3. Sort by confidence descending.

Design: stateless, deterministic, no LLM needed.
"""

from __future__ import annotations

import re
import logging
from collections import defaultdict
from typing import List, Dict, Set, Tuple
from urllib.parse import urlparse

from lina.models.datatypes import Fact, FactSet

logger = logging.getLogger("lina.core.fact_aggregator")


# ── Single-value predicates (one device = one value) ──
# After aggregation, conflicting values for these predicates
# are resolved by keeping only the highest-confidence entry.
_SINGLE_VALUE_PREDS: frozenset = frozenset({
    "processor", "battery", "ram", "storage", "display",
    "display_size", "display_type", "os", "charging",
    "refresh rate", "resolution", "weight", "dimensions",
    "protection", "price", "gpu", "front camera",
})

# Multi-value predicates (cameras can have multiple modules)
_MULTI_VALUE_MAX = 4  # keep at most 4 variants for multi-value preds


# ── Source Independence Detection ──

def _extract_domain(url: str) -> str:
    """Extract base domain from URL (handles bare domains too)."""
    try:
        # Handle bare domains like "a.com" without scheme
        if url and "://" not in url:
            url = "https://" + url
        host = urlparse(url).hostname or ""
        # Remove www. prefix
        if host.startswith("www."):
            host = host[4:]
        return host.lower()
    except Exception:
        return ""


def _ngram_set(text: str, n: int = 3) -> Set[str]:
    """Compute character n-gram set from text (lowercased)."""
    t = text.lower().strip()
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i+n] for i in range(len(t) - n + 1)}


def _jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union > 0 else 0.0


class FactAggregator:
    """
    Merges and deduplicates facts from multiple sources.

    Confidence boost:
      1 source  → base confidence (from extractor)
      2 sources → +0.15
      3+ sources→ +0.25
    """

    BOOST_2_SOURCES = 0.15
    BOOST_3_SOURCES = 0.25

    # Threshold for source independence: if Jaccard(3-gram) of fact values
    # from two different sources exceeds this, they're likely syndicated
    SYNDICATION_THRESHOLD = 0.60

    def aggregate(
        self,
        facts: List[Fact],
        subject: str = "",
    ) -> FactSet:
        """
        Aggregate a flat list of facts into a FactSet.

        Args:
            facts:   Raw facts (may have duplicates).
            subject: Primary topic entity.

        Returns:
            FactSet with merged, confidence-scored facts.
        """
        if not facts:
            return FactSet(subject=subject, confidence=0.0)

        # ── Step 1: Group by predicate key ──
        groups: Dict[str, List[Fact]] = defaultdict(list)
        for f in facts:
            key = self._norm_key(f.predicate)
            groups[key].append(f)

        # ── Step 1b: Semantic clustering — merge groups with similar keys ──
        # Catches near-duplicates missed by the explicit synonym dictionary
        # (e.g. "camera megapixels" ↔ "main camera resolution").
        groups = self._merge_similar_groups(groups)

        # ── Step 2: Merge each group ──
        merged: List[Fact] = []
        all_sources: Set[str] = set()

        for key, group in groups.items():
            # Sub-group by normalised value
            value_groups: Dict[str, List[Fact]] = defaultdict(list)
            for f in group:
                vkey = self._norm_value(f.object_value)
                value_groups[vkey].append(f)

            for vkey, vgroup in value_groups.items():
                # Collect unique sources
                sources: Set[str] = set()
                for f in vgroup:
                    sources.update(s for s in f.sources if s)
                    all_sources.update(sources)

                # Count independent sources (discount syndicated content)
                src_count = self._count_independent_sources(vgroup, sources)

                # Best base confidence from extractors
                base_conf = max(f.confidence for f in vgroup)

                # Cross-source boost
                if src_count >= 3:
                    conf = min(1.0, base_conf + self.BOOST_3_SOURCES)
                elif src_count >= 2:
                    conf = min(1.0, base_conf + self.BOOST_2_SOURCES)
                else:
                    conf = base_conf

                # Use the longest / most complete value string
                best_value = max(
                    (f.object_value for f in vgroup),
                    key=lambda v: len(v),
                )

                merged.append(Fact(
                    subject=subject or vgroup[0].subject,
                    predicate=vgroup[0].predicate,  # preserve original casing
                    object_value=best_value,
                    sources=sorted(sources),
                    source_count=src_count,
                    confidence=round(conf, 3),
                    verified=(src_count >= 2),
                ))

        # ── Step 3: Conflict resolution for single-value predicates ──
        merged = self._resolve_conflicts(merged)

        # ── Step 4: Sort by confidence ──
        merged.sort(key=lambda f: f.confidence, reverse=True)

        # Overall confidence
        if merged:
            avg_conf = sum(f.confidence for f in merged) / len(merged)
            overall = min(1.0, avg_conf + 0.05 * min(len(all_sources), 5))
        else:
            overall = 0.0

        fact_set = FactSet(
            subject=subject,
            facts=merged,
            total_sources=len(all_sources),
            confidence=round(overall, 3),
        )

        logger.info(
            "FactAggregator: %d raw → %d merged (%d verified), conf=%.2f",
            len(facts), len(merged), fact_set.verified_count, overall,
        )
        return fact_set

    # ── Conflict Resolution ──

    def _resolve_conflicts(self, facts: List[Fact]) -> List[Fact]:
        """Resolve conflicting values for single-value predicates.

        For predicates like 'processor', 'battery', 'display_size' etc.,
        a device has exactly ONE correct value.  If aggregation produced
        multiple different values, keep only the highest-confidence one.

        Multi-value predicates (cameras, features) are capped at
        _MULTI_VALUE_MAX entries.
        """
        # Group by normalised predicate
        pred_groups: Dict[str, List[Fact]] = defaultdict(list)
        for f in facts:
            key = self._norm_key(f.predicate)
            pred_groups[key].append(f)

        result: List[Fact] = []
        for key, group in pred_groups.items():
            if key in _SINGLE_VALUE_PREDS:
                if len(group) > 1:
                    # Sort by confidence desc, then by source_count desc, then by value length desc
                    group.sort(
                        key=lambda f: (f.confidence, f.source_count, len(f.object_value)),
                        reverse=True,
                    )
                    winner = group[0]
                    dropped = group[1:]
                    logger.info(
                        "ConflictResolver: %s — kept %r (conf=%.2f, src=%d), "
                        "dropped %d conflicting values: %s",
                        key, winner.object_value, winner.confidence,
                        winner.source_count, len(dropped),
                        [f.object_value for f in dropped],
                    )
                    result.append(winner)
                else:
                    result.extend(group)
            else:
                # Multi-value: cap total count
                if len(group) > _MULTI_VALUE_MAX:
                    group.sort(key=lambda f: f.confidence, reverse=True)
                    group = group[:_MULTI_VALUE_MAX]
                result.extend(group)

        return result

    # ── Semantic Predicate Clustering ──

    PRED_SIM_THRESHOLD = 0.65  # Jaccard threshold for predicate similarity

    def _merge_similar_groups(
        self,
        groups: Dict[str, List[Fact]],
    ) -> Dict[str, List[Fact]]:
        """Merge groups with semantically similar predicate keys.

        Second pass after synonym-based grouping: uses character n-gram
        Jaccard similarity to detect near-duplicate predicates that weren't
        caught by the explicit synonym dictionary.
        E.g. "camera megapixels" ↔ "main camera resolution".
        """
        keys = list(groups.keys())
        if len(keys) <= 1:
            return groups

        # Build n-gram fingerprints
        key_ngrams = {k: _ngram_set(k, n=3) for k in keys}

        # Union-Find to cluster similar keys
        parent: Dict[str, str] = {k: k for k in keys}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for i, k1 in enumerate(keys):
            for k2 in keys[i + 1:]:
                sim = _jaccard_similarity(key_ngrams[k1], key_ngrams[k2])
                if sim >= self.PRED_SIM_THRESHOLD:
                    union(k1, k2)
                    logger.debug(
                        "Merged similar predicates: '%s' ↔ '%s' (Jaccard=%.2f)",
                        k1, k2, sim,
                    )

        # Rebuild groups by cluster root
        merged: Dict[str, List[Fact]] = defaultdict(list)
        for k in keys:
            root = find(k)
            merged[root].extend(groups[k])

        if len(merged) < len(groups):
            logger.info(
                "Semantic clustering: %d groups → %d clusters",
                len(groups), len(merged),
            )
        return dict(merged)

    # ── Source Independence ──

    def _count_independent_sources(
        self,
        facts_in_group: List[Fact],
        sources: Set[str],
    ) -> int:
        """Count truly independent sources (discount syndicated content).

        Two sources are considered syndicated (non-independent) if:
        1. They are from different domains, AND
        2. The fact values extracted from them have high character n-gram overlap
           (Jaccard > SYNDICATION_THRESHOLD)

        This prevents a press-release republished on 5 blogs from counting
        as 5 independent confirmations.

        Returns at least 1.
        """
        if len(sources) <= 1:
            return max(len(sources), 1)

        # Build domain → fact_texts mapping
        domain_texts: Dict[str, str] = {}
        for f in facts_in_group:
            for src in f.sources:
                domain = _extract_domain(src)
                if domain:
                    # Accumulate all value text from this domain
                    existing = domain_texts.get(domain, "")
                    domain_texts[domain] = (existing + " " + f.object_value).strip()

        domains = list(domain_texts.keys())
        if len(domains) <= 1:
            return max(len(domains), 1)

        # Build n-gram sets for each domain's contribution
        domain_ngrams: Dict[str, Set[str]] = {
            d: _ngram_set(text) for d, text in domain_texts.items()
        }

        # Short values (< 30 chars) like "8 GB" or "5000 mAh" are inherently
        # identical when factually correct — don't flag them as syndicated.
        avg_text_len = sum(len(t) for t in domain_texts.values()) / max(len(domain_texts), 1)
        if avg_text_len < 30:
            return len(domains)

        # Greedy clustering: two domains are "same cluster" if Jaccard > threshold
        independent_count = 0
        used = set()
        for i, d1 in enumerate(domains):
            if d1 in used:
                continue
            independent_count += 1
            used.add(d1)
            ng1 = domain_ngrams[d1]
            for d2 in domains[i+1:]:
                if d2 in used:
                    continue
                ng2 = domain_ngrams[d2]
                sim = _jaccard_similarity(ng1, ng2)
                if sim >= self.SYNDICATION_THRESHOLD:
                    # d2 is syndicated copy of d1 — same cluster
                    used.add(d2)
                    logger.debug(
                        "Syndicated content detected: %s ↔ %s (Jaccard=%.2f)",
                        d1, d2, sim,
                    )

        return max(independent_count, 1)

    # ── Normalisation ──

    # Latin→Cyrillic confusables (lowercase).
    # Handles mixed-script input like "OЗУ" (Latin O + Cyrillic ЗУ).
    _LATIN_TO_CYR = str.maketrans(
        "abekmhopctyx",   # Latin lookalikes
        "абекмнорстух",   # Cyrillic equivalents
    )

    @classmethod
    def _norm_key(cls, predicate: str) -> str:
        """Normalise predicate for grouping."""
        p = predicate.lower().strip()
        # Resolve Latin/Cyrillic confusables when string contains Cyrillic
        if any("\u0400" <= ch <= "\u04ff" for ch in p):
            p = p.translate(cls._LATIN_TO_CYR)
        # Map RU synonyms
        synonyms = {
            "процессор": "processor", "чипсет": "processor", "soc": "processor",
            "chipset": "processor",
            "озу": "ram", "оперативная память": "ram", "оперативн": "ram",
            "memory": "ram",
            "пзу": "storage", "встроенная память": "storage", "rom": "storage",
            "аккумулятор": "battery", "батарея": "battery", "ёмкость": "battery",
            "экран": "display", "дисплей": "display", "screen": "display",
            "основная камера": "main camera", "rear camera": "main camera",
            "камера": "main camera",
            "видеокарта": "gpu", "graphics": "gpu",
            "зарядка": "charging", "быстрая зарядка": "charging",
            "частота обновления": "refresh rate",
            "цена": "price", "стоимость": "price",
            "вес": "weight", "масса": "weight",
            "размеры": "dimensions", "габариты": "dimensions",
            "защита": "protection",
        }
        return synonyms.get(p, p)

    @staticmethod
    def _norm_value(value: str) -> str:
        """Normalise value for deduplication."""
        v = value.lower().strip()
        v = re.sub(r"\s+", " ", v)
        # Normalise common units
        v = re.sub(r"(\d)\s*гб", r"\1 gb", v)
        v = re.sub(r"(\d)\s*мач", r"\1 mah", v)
        v = re.sub(r"(\d)\s*вт", r"\1 w", v)
        v = re.sub(r"(\d)\s*гц", r"\1 hz", v)
        return v


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_aggregator: FactAggregator | None = None


def get_fact_aggregator() -> FactAggregator:
    global _aggregator
    if _aggregator is None:
        _aggregator = FactAggregator()
    return _aggregator
