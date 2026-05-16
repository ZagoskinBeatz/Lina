    # -*- coding: utf-8 -*-
"""
Lina Core — Query Rewriter (v2 Pipeline).

Transforms a single user query into 3–5 optimised search queries.
This dramatically improves web-search recall because natural-language
questions are almost always bad search queries.

Algorithm:
  1. Parse entities (brand, model, attribute) via EntityParser.
  2. Translate RU tech terms → EN (most web results are in English).
  3. Generate query variants:
     a) Original cleaned query.
     b) Entity + attribute + "specs" suffix.
     c) English transliteration.
     d) Brand + model + attribute (if known).
     e) Comparison / review variant (if intent matches).

Design: zero side-effects, deterministic, no LLM needed.
"""

from __future__ import annotations

import re
import logging
from typing import List, Optional, Dict

from lina.models.datatypes import QueryPlan

logger = logging.getLogger("lina.core.query_rewriter")


# ═══════════════════════════════════════════════════
#  RU → EN Translation Table (tech terms)
# ═══════════════════════════════════════════════════

_RU_EN: Dict[str, str] = {
    "процессор": "processor",
    "чипсет": "chipset",
    "видеокарта": "GPU",
    "видеокарту": "GPU",
    "оперативная память": "RAM",
    "оперативку": "RAM",
    "озу": "RAM",
    "памят": "memory",
    "экран": "display",
    "дисплей": "display",
    "аккумулятор": "battery",
    "батарея": "battery",
    "камера": "camera",
    "камеру": "camera",
    "характеристики": "specifications",
    "характеристик": "specifications",
    "спецификации": "specifications",
    "обзор": "review",
    "цена": "price",
    "стоимость": "price",
    "сравнение": "comparison vs",
    "ёмкость": "capacity",
    "разрешение": "resolution",
    "производительность": "performance",
    "автономность": "battery life",
    "тест": "benchmark",
    "бенчмарк": "benchmark",
    "размер": "size dimensions",
    "вес": "weight",
    "зарядка": "charging",
    "быстрая зарядка": "fast charging",
    "частота обновления": "refresh rate",
    "яркость": "brightness",
    "защита": "IP rating protection",
    "nfc": "NFC",
    "динамик": "speaker",
    "звук": "audio sound",
    "bluetooth": "Bluetooth",
    "wifi": "WiFi",
    "сенсор": "sensor",
    "датчик": "sensor",
    "гироскоп": "gyroscope",
    "сканер отпечатков": "fingerprint scanner",
    "отпечаток": "fingerprint",
    "разблокировка": "unlock face ID",
    "слот": "slot",
    "карта памяти": "memory card microSD",
    "сим": "SIM",
    "версия андроид": "Android version",
}

# Filler words to strip (RU/EN)
_FILLERS = {
    "какой", "какая", "какое", "какие", "какую", "каков",
    "расскажи", "покажи", "скажи", "подскажи",
    "мне", "пожалуйста", "будь", "добра", "добр",
    "про", "о", "об", "для", "на", "в", "у", "а",
    "это", "его", "её", "их",
    "нужен", "нужна", "нужно",
    "хочу", "хотел", "знать",
    "найди", "поищи", "нагугли", "загугли",
    "характеристики", "характеристик",
    "спецификации", "спецификация",
    "tell", "me", "about", "show", "find", "what", "is", "the",
}

# Intent-specific suffixes
_INTENT_SUFFIXES: Dict[str, List[str]] = {
    "сравни": ["vs", "comparison"],
    "сравнение": ["vs", "comparison"],
    "лучше": ["vs", "comparison"],
    "стоит ли": ["review", "worth buying"],
    "купить": ["price", "buy"],
    "альтернатив": ["alternatives"],
    "аналог": ["alternatives"],
    "проблем": ["issues", "problems", "fix"],
    "ошибк": ["error", "fix", "troubleshoot"],
    "не работает": ["not working", "fix"],
}


class QueryRewriter:
    """
    Generates 3–5 optimised search queries from a human query.

    Usage:
        rw = QueryRewriter()
        plan = rw.rewrite("какой процессор у realme 10")
        # plan.queries == [
        #   "realme 10 processor",
        #   "realme 10 chipset specs",
        #   "realme 10 mediatek helio specifications",
        # ]
    """

    def __init__(self, max_queries: int = 5, min_queries: int = 3):
        self._max = max_queries
        self._min = min_queries

    def rewrite(self, query: str) -> QueryPlan:
        """
        Rewrite user query into a QueryPlan with multiple search variants.

        Args:
            query: Raw user query in any language.

        Returns:
            QueryPlan with .queries list.
        """
        plan = QueryPlan(original=query)

        if not query or not query.strip():
            plan.queries = [query]
            return plan

        # ── 1. Parse entities ──
        device, brand, attribute = self._parse_entities(query)
        plan.detected_entities = [x for x in [device, brand, attribute] if x]
        plan.detected_intent = self._detect_intent_hint(query)

        # ── 2. Clean: remove fillers ──
        cleaned = self._remove_fillers(query)

        # ── 3. Generate variants ──
        variants: List[str] = []

        # Variant A: cleaned original
        if cleaned and len(cleaned) > 3:
            variants.append(cleaned)

        # Variant B: EN translation
        en_query = self._translate_to_en(cleaned or query)
        if en_query and en_query.lower() != cleaned.lower():
            variants.append(en_query)

        # Variant C: device + attribute + "specs"
        if device:
            if attribute:
                en_attr = _RU_EN.get(attribute.lower(), attribute)
                variants.append(f"{device} {en_attr} specs")
            else:
                variants.append(f"{device} specifications")

        # Variant D: brand + model + "review" / specific suffix
        if device and brand:
            intent_suffix = self._get_intent_suffix(query)
            if intent_suffix:
                variants.append(f"{brand} {device} {intent_suffix}")
            else:
                variants.append(f"{device} review")

        # Variant E: site-specific (gsmarena, notebookcheck, etc.)
        if device:
            variants.append(f"{device} gsmarena")

        # ── 4. Deduplicate and cap ──
        seen: set = set()
        unique: List[str] = []
        for v in variants:
            v_stripped = v.strip()
            key = v_stripped.lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(v_stripped)

        # Ensure minimum
        if len(unique) < self._min:
            if cleaned not in seen:
                unique.append(cleaned)
            if query.strip() not in seen:
                unique.append(query.strip())

        plan.queries = unique[:self._max]
        return plan

    # ── Internal helpers ──

    def _parse_entities(self, query: str):
        """Extract device, brand, attribute from query using EntityParser."""
        try:
            from lina.core.entity_parser import get_entity_parser
            parsed = get_entity_parser().parse(query)
            return parsed.device, parsed.brand, parsed.attribute
        except Exception:
            return None, None, None

    def _remove_fillers(self, query: str) -> str:
        """Remove filler / stop words and trailing punctuation from query."""
        words = query.split()
        result = [w for w in words if w.lower().strip(".,!?¿¡") not in _FILLERS]
        cleaned = " ".join(result).strip()
        # Strip trailing question marks / punctuation
        cleaned = cleaned.rstrip("?!.,;:")
        return cleaned.strip()

    def _translate_to_en(self, query: str) -> str:
        """Translate known RU tech terms to EN in-place."""
        result = query
        # Sort by length desc to avoid partial matches
        for ru, en in sorted(_RU_EN.items(), key=lambda x: -len(x[0])):
            result = re.sub(
                r'\b' + re.escape(ru) + r'\w*\b',
                en,
                result,
                flags=re.IGNORECASE,
            )
        return result.strip()

    def _detect_intent_hint(self, query: str) -> str:
        """Detect query intent (comparison, buy, review, etc.)."""
        ql = query.lower()
        for hint, _ in _INTENT_SUFFIXES.items():
            if hint in ql:
                return hint
        return ""

    def _get_intent_suffix(self, query: str) -> str:
        """Get EN suffix matching the detected intent."""
        ql = query.lower()
        for hint, suffixes in _INTENT_SUFFIXES.items():
            if hint in ql:
                return suffixes[0]
        return ""


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_rewriter: QueryRewriter | None = None


def get_query_rewriter() -> QueryRewriter:
    global _rewriter
    if _rewriter is None:
        _rewriter = QueryRewriter()
    return _rewriter
