# -*- coding: utf-8 -*-
"""
Lina Core — Query Understanding (v3).

Deep semantic analysis of a user query.
Combines IntentRouter + EntityParser output into a unified
QueryUnderstanding object that drives all downstream stages.

Pipeline:
  raw query
    → language detection
    → entity extraction (EntityParser)
    → attribute detection
    → intent classification
    → query type (factual / opinion / comparison)
    → need_web_search flag
    → QueryUnderstanding

This is the single source of truth about "what the user wants".
"""

from __future__ import annotations

import re
import logging
from typing import List, Optional

from lina.models.datatypes import QueryUnderstanding

logger = logging.getLogger("lina.core.query_understanding")


# ═══════════════════════════════════════════════════
#  Language Detection
# ═══════════════════════════════════════════════════

_RU_RE = re.compile(r"[а-яёА-ЯЁ]")

def _detect_language(text: str) -> str:
    """Detect language: 'ru' or 'en'."""
    ru_chars = len(_RU_RE.findall(text))
    total = max(len(text), 1)
    return "ru" if ru_chars / total > 0.3 else "en"


# ═══════════════════════════════════════════════════
#  Attribute Maps
# ═══════════════════════════════════════════════════

_ATTRIBUTE_KEYWORDS: dict[str, str] = {
    # RU
    "процессор": "processor", "чипсет": "processor", "чип": "processor",
    "сок": "processor", "цпу": "processor",
    "оперативн": "ram", "озу": "ram", "памят": "ram",
    "батаре": "battery", "аккумулятор": "battery", "ёмкост": "battery",
    "экран": "display", "дисплей": "display",
    "камер": "camera", "фото": "camera",
    "хранилищ": "storage", "пзу": "storage", "встроенн": "storage",
    "цен": "price", "стоимост": "price", "сколько стоит": "price",
    "вес": "weight", "масс": "weight",
    "размер": "dimensions", "габарит": "dimensions",
    "зарядк": "charging", "быстр": "charging",
    "частот": "refresh_rate", "обновлен": "refresh_rate",
    "разрешен": "resolution",
    "видеокарт": "gpu", "график": "gpu",
    "операцион": "os", "андроид": "os",
    "защит": "protection",
    # EN
    "processor": "processor", "chipset": "processor", "cpu": "processor",
    "soc": "processor",
    "ram": "ram", "memory": "ram",
    "battery": "battery",
    "display": "display", "screen": "display",
    "camera": "camera",
    "storage": "storage", "rom": "storage",
    "price": "price", "cost": "price",
    "weight": "weight",
    "dimension": "dimensions", "size": "dimensions",
    "charg": "charging",
    "refresh": "refresh_rate",
    "resolution": "resolution",
    "gpu": "gpu", "graphic": "gpu",
    "os": "os", "android": "os", "ios": "os",
    "protection": "protection",
}

# ═══════════════════════════════════════════════════
#  Intent Detection
# ═══════════════════════════════════════════════════

_COMPARISON_RE = re.compile(
    r"(?:сравни|сравнение|vs\.?|versus|или|лучше|compared?|better|worse|differ)",
    re.IGNORECASE,
)
_REVIEW_RE = re.compile(
    r"(?:отзыв|обзор|review|opinion|мнение|стоит\s*ли|worth)",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(
    r"(?:цена|стоимость|price|cost|сколько\s*стоит|купить|buy|where\s+to\s+buy)",
    re.IGNORECASE,
)
_SPEC_RE = re.compile(
    r"(?:характеристик|specs?|specification|параметр|тех\w*\s*данн)",
    re.IGNORECASE,
)

# Queries that DON'T need web search
_CHAT_RE = re.compile(
    r"^(?:привет|здравствуй|добр\w+\s*(?:утро|день|вечер)|hi|hello|спасибо|thanks?|"
    r"как дела|how are you|кто ты|who are you|что умеешь)",
    re.IGNORECASE,
)
_MATH_RE = re.compile(
    r"(?:^[\d\s+\-*/().^%=?]+$|sqrt|sin|cos|tan|log|factorial|"
    r"посчитай|вычисли|calculate)",
    re.IGNORECASE,
)
_SYSTEM_RE = re.compile(
    r"(?:^[!>]|открой|запусти|закрой|выключи|выключить|перезагру|"
    r"установи|удали\b|обнови\b|"
    r"run\s|launch\s|kill\s|stop\s|shutdown|reboot|"
    r"install\s|uninstall\s|remove\s|update\s|upgrade\s|"
    r"terminal|sudo|apt\s|pacman\s|dnf\s)",
    re.IGNORECASE,
)


class QueryUnderstandingEngine:
    """
    Turns a raw user query into a structured QueryUnderstanding.

    Usage:
        engine = QueryUnderstandingEngine()
        qu = engine.analyze("какой процессор у realme 10")
        # qu.intent = "product_spec"
        # qu.entities = ["Realme 10"]
        # qu.attributes = ["processor"]
        # qu.language = "ru"
    """

    def __init__(self):
        try:
            from lina.core.entity_parser import EntityParser
            self._parser = EntityParser()
        except Exception:
            self._parser = None

    def analyze(self, query: str) -> QueryUnderstanding:
        """Perform deep query analysis."""
        if not query or not query.strip():
            return QueryUnderstanding(raw_query=query or "")

        text = query.strip()
        lang = _detect_language(text)

        # Extract entities via EntityParser
        entities: list[str] = []
        attributes: list[str] = []

        if self._parser:
            try:
                parsed = self._parser.parse(text)
                if parsed.device:
                    entities.append(parsed.device)
                if parsed.brand and parsed.brand not in entities:
                    entities.append(parsed.brand)
                if parsed.attribute:
                    attributes.append(parsed.attribute)
            except Exception as e:
                logger.debug("EntityParser failed: %s", e)

        # Detect attributes from keywords
        text_lower = text.lower()
        for keyword, attr in _ATTRIBUTE_KEYWORDS.items():
            if keyword in text_lower and attr not in attributes:
                attributes.append(attr)

        # Detect intent
        intent = self._classify_intent(text, entities, attributes)

        # Detect query type
        query_type = self._classify_query_type(text)

        # Need web search?
        need_web = intent in (
            "product_spec", "comparison", "price", "review",
            "general_info", "web_search",
        )

        confidence = self._estimate_confidence(entities, attributes, intent)

        qu = QueryUnderstanding(
            raw_query=text,
            intent=intent,
            entities=entities,
            attributes=attributes,
            language=lang,
            query_type=query_type,
            need_web_search=need_web,
            confidence=confidence,
        )

        logger.info(
            "QueryUnderstanding: intent=%s, entities=%s, attrs=%s, "
            "type=%s, web=%s, conf=%.2f",
            intent, entities[:3], attributes[:3], query_type, need_web, confidence,
        )
        return qu

    def _classify_intent(
        self, text: str, entities: list, attributes: list,
    ) -> str:
        """Determine the user's intent."""
        # Priority: system > math > chat > comparison > price > review > spec > general
        if _SYSTEM_RE.search(text):
            return "system_command"
        if _MATH_RE.search(text):
            return "math"
        if _CHAT_RE.search(text):
            return "chat"
        if _COMPARISON_RE.search(text):
            return "comparison"
        if _PRICE_RE.search(text):
            return "price"
        if _REVIEW_RE.search(text):
            return "review"
        if entities and attributes:
            return "product_spec"
        if _SPEC_RE.search(text):
            return "product_spec"
        if entities:
            return "general_info"
        return "general_info"

    @staticmethod
    def _classify_query_type(text: str) -> str:
        """Determine query type: factual, opinion, or comparison."""
        if _COMPARISON_RE.search(text):
            return "comparison"
        if _REVIEW_RE.search(text):
            return "opinion"
        return "factual"

    @staticmethod
    def _estimate_confidence(
        entities: list, attributes: list, intent: str,
    ) -> float:
        """Estimate how well we understood the query."""
        conf = 0.3
        if entities:
            conf += 0.3
        if attributes:
            conf += 0.2
        if intent not in ("general_info",):
            conf += 0.1
        return min(conf, 1.0)


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_engine: QueryUnderstandingEngine | None = None

def get_query_understanding() -> QueryUnderstandingEngine:
    global _engine
    if _engine is None:
        _engine = QueryUnderstandingEngine()
    return _engine
