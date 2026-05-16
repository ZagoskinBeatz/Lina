# -*- coding: utf-8 -*-
"""
Lina LLM — Fact-Mode Prompt Templates (v3).

Centralized prompts for fact-based generation and verification.

Principle: LLM works ONLY with verified facts, never with raw web text.
"""

from typing import List, Optional
import logging
import re

from lina.models.datatypes import Fact, FactSet, QueryUnderstanding

logger = logging.getLogger("lina.llm.fact_prompt")


# ── Generation prompt ───────────────────────────────────────────────────────────

_GENERATE_RU = """\
Ты Lina — точный информационный ассистент.
Твоя задача — ответить на вопрос пользователя, используя ТОЛЬКО предоставленные факты.

=== ПРАВИЛА ===
1. Используй ТОЛЬКО факты ниже.  Если факта нет — скажи «Нет данных».
2. НЕ придумывай, НЕ дополняй, НЕ предполагай.
3. НЕ выдумывай характеристики, числа, модели, даты, цены.
4. Указывай конкретные значения (числа, названия) из фактов.
5. Если запрос — сравнение, представь данные в виде таблицы.
6. Отвечай на языке пользователя.
7. НЕ генерируй bash/shell-команды, код или терминальный вывод. Только текст.
8. Если раздел ФАКТЫ содержит «нет фактов» или пуст — ответь:
   «К сожалению, мне не удалось найти достоверную информацию.»
9. Если фактов мало (1-2) — перечисли только их и честно скажи,
   что полной информации нет.

=== ФАКТЫ ===
{facts_block}

=== ВОПРОС ===
{query}

=== ОТВЕТ ==="""

_GENERATE_EN = """\
You are Lina — a precise information assistant.
Answer the user's question using ONLY the provided facts.

=== RULES ===
1. Use ONLY the facts below.  If no fact exists — say "No data available".
2. Do NOT invent, do NOT add, do NOT assume.
3. Do NOT fabricate specifications, numbers, models, dates, or prices.
4. Cite specific values (numbers, names) from the facts.
5. For comparison queries, present data as a table.
6. Answer in the user's language.
7. Do NOT generate bash/shell commands, code, or terminal output. Plain text only.
8. If the FACTS section says "no facts" or is empty — respond:
   "Unfortunately, I couldn't find reliable information."
9. If there are only 1-2 facts — list only those and honestly say
   that complete information is not available.

=== FACTS ===
{facts_block}

=== QUESTION ===
{query}

=== ANSWER ==="""


# ── Verification prompt ─────────────────────────────────────────────────────────

_VERIFY_RU = """\
Сравни ОТВЕТ с ФАКТАМИ.

=== ФАКТЫ ===
{facts_block}

=== ОТВЕТ ===
{answer}

Проверь:
1. Есть ли в ответе утверждения, которых НЕТ в фактах? (hallucination)
2. Есть ли числа/названия, которые ОТЛИЧАЮТСЯ от фактов? (mismatch)

Формат ответа (строго JSON):
{{"faithful": true/false, "hallucinations": [...], "mismatches": [...]}}"""

_VERIFY_EN = """\
Compare ANSWER against FACTS.

=== FACTS ===
{facts_block}

=== ANSWER ===
{answer}

Check:
1. Are there claims in the answer NOT present in the facts? (hallucination)
2. Are there numbers/names that DIFFER from the facts? (mismatch)

Response format (strict JSON):
{{"faithful": true/false, "hallucinations": [...], "mismatches": [...]}}"""


# ── Helpers ─────────────────────────────────────────────────────────────────────


def _score_fact_relevance(fact: Fact, query: str, entities: List[str],
                           attributes: List[str]) -> float:
    """Score a fact's relevance to the user query.

    Combines:
      - predicate/attribute match: does the fact's predicate match a requested attribute?
      - entity match: does the fact mention a queried entity?
      - keyword overlap: Jaccard of significant words between fact and query
      - confidence: original fact confidence as a baseline signal

    Returns a float in [0, 1] — higher means more relevant.
    """
    score = 0.0
    query_low = query.lower()
    pred_low = fact.predicate.lower()
    val_low = fact.object_value.lower()
    subj_low = fact.subject.lower()

    # Normalize predicate via synonym map for cross-language matching
    pred_norm = _PRED_SYNONYMS.get(pred_low, pred_low)

    # ── Signal 1: Predicate matches a requested attribute (weight: 0.40)
    if attributes:
        for attr in attributes:
            attr_low = attr.lower()
            attr_norm = _PRED_SYNONYMS.get(attr_low, attr_low)
            # Exact synonym match
            if attr_norm == pred_norm:
                score += 0.40
                break
            if attr_low in pred_low or pred_low in attr_low:
                score += 0.40
                break
            # Fuzzy: shared significant words
            attr_words = set(attr_low.split()) - _STOP_WORDS
            pred_words = set(pred_low.split()) - _STOP_WORDS
            if attr_words and attr_words & pred_words:
                score += 0.30
                break
    else:
        # No explicit attributes — check if predicate words appear in query
        pred_words = set(pred_low.split()) - _STOP_WORDS
        query_words = set(query_low.split()) - _STOP_WORDS
        # Also check normalized forms
        pred_norm_words = set(pred_norm.split()) - _STOP_WORDS
        if (pred_words and pred_words & query_words) or \
           (pred_norm_words and pred_norm_words & query_words):
            score += 0.35

    # ── Signal 2: Entity match (weight: 0.25)
    if entities:
        for ent in entities:
            if ent.lower() in subj_low or subj_low in ent.lower():
                score += 0.25
                break
    else:
        # Check subject overlap with query
        subj_words = set(subj_low.split()) - _STOP_WORDS
        query_words = set(query_low.split()) - _STOP_WORDS
        if subj_words and subj_words & query_words:
            score += 0.20

    # ── Signal 3: Keyword overlap between fact content and query (weight: 0.15)
    fact_text = f"{subj_low} {pred_low} {val_low}"
    fact_words = set(re.findall(r'\w{3,}', fact_text)) - _STOP_WORDS
    query_words_all = set(re.findall(r'\w{3,}', query_low)) - _STOP_WORDS
    if fact_words and query_words_all:
        jaccard = len(fact_words & query_words_all) / len(fact_words | query_words_all)
        score += 0.15 * jaccard

    # ── Signal 4: Confidence baseline (weight: 0.20)
    conf = getattr(fact, 'confidence', 0.5)
    score += 0.20 * min(conf, 1.0)

    return min(score, 1.0)


# Stop words for relevance scoring (RU + EN common fillers)
_STOP_WORDS = frozenset({
    # RU
    "и", "в", "на", "с", "для", "по", "из", "у", "к", "от", "за", "о",
    "что", "это", "как", "не", "да", "нет", "а", "но", "то", "же",
    "его", "её", "их", "мой", "мне", "ты", "мы", "вы", "он", "она",
    "какой", "какая", "какое", "какие", "этот", "эта", "эти",
    "быть", "был", "была", "были", "есть", "будет",
    # EN
    "the", "is", "in", "at", "of", "on", "for", "to", "with", "and",
    "or", "but", "not", "this", "that", "what", "which", "how",
    "has", "have", "had", "was", "were", "are", "been", "its",
})

# RU↔EN predicate synonym map for cross-language matching
# Maps any variant to a canonical English form
_PRED_SYNONYMS: dict[str, str] = {
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
    "разрешение": "resolution",
    "фронтальная камера": "front camera", "selfie camera": "front camera",
    "операционная система": "os", "ос": "os",
}


def _select_facts_by_relevance(
    facts: List[Fact],
    query: str,
    max_facts: int,
    max_chars: int,
    entities: List[str] | None = None,
    attributes: List[str] | None = None,
) -> List[Fact]:
    """Select facts by relevance to query, not just confidence.

    Scores each fact by:  relevance_to_query × 0.7 + confidence × 0.3
    Then greedily packs into budget (max_facts, max_chars).

    Returns facts in relevance order (most relevant first).
    """
    if not facts:
        return []

    ents = entities or []
    attrs = attributes or []

    # Score each fact
    scored = []
    for f in facts:
        relevance = _score_fact_relevance(f, query, ents, attrs)
        conf = getattr(f, 'confidence', 0.5)
        # Combined score: 70% relevance, 30% confidence
        combined = 0.70 * relevance + 0.30 * min(conf, 1.0)
        scored.append((combined, relevance, f))

    # Sort by combined score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # Greedy packing with budget
    selected: List[Fact] = []
    total_chars = 0
    for _score, _rel, fact in scored:
        if len(selected) >= max_facts:
            break
        # Estimate line length
        line_len = len(f"{fact.subject} → {fact.predicate}: {fact.object_value}") + 20
        if max_chars > 0 and total_chars + line_len > max_chars:
            break
        selected.append(fact)
        total_chars += line_len

    return selected


def _format_facts(facts: List[Fact], max_facts: int = 30,
                   max_chars: int = 0) -> str:
    """Format facts into a numbered text block for LLM consumption.

    Args:
        facts:      List of Fact objects.
        max_facts:  Maximum number of facts to include.
        max_chars:  If > 0, truncate the block to fit within this char budget.
    """
    lines = []
    total = 0
    for i, f in enumerate(facts[:max_facts], 1):
        src_count = f.source_count if hasattr(f, "source_count") else 1
        verified = "✓" if getattr(f, "verified", False) else ""
        line = f"{i}. {f.subject} → {f.predicate}: {f.object_value}"
        if verified:
            line += f"  [{verified} ×{src_count}]"
        # Char-budget guard: stop adding facts when limit reached
        if max_chars > 0 and total + len(line) + 1 > max_chars:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines) if lines else "(нет фактов / no facts)"


def build_generation_prompt(
    query: str,
    fact_set: FactSet,
    lang: str = "ru",
    max_facts: int = 30,
    understanding: Optional[QueryUnderstanding] = None,
    max_prompt_chars: int = 0,
) -> str:
    """
    Build a fact-mode prompt for LLM generation.

    Args:
        query:              User's raw question.
        fact_set:           Verified fact set.
        lang:               "ru" or "en".
        max_facts:          Max facts to include.
        understanding:      Optional QueryUnderstanding for context enrichment.
        max_prompt_chars:   If > 0, limit total prompt to this many chars
                            (used to fit mini-model ctx windows).

    Returns:
        Complete prompt string ready for LLM.
    """
    template = _GENERATE_RU if lang == "ru" else _GENERATE_EN

    # Enrich query with understanding context if available
    enriched_query = query
    entities: list[str] = []
    attributes: list[str] = []
    if understanding:
        parts = [query]
        if understanding.entities:
            entities = list(understanding.entities)
            parts.append(f"(entities: {', '.join(understanding.entities)})")
        if understanding.attributes:
            attributes = list(understanding.attributes)
            parts.append(f"(attributes: {', '.join(understanding.attributes)})")
        enriched_query = " ".join(parts)

    # Estimate char budget for facts block
    max_facts_chars = 0
    if max_prompt_chars > 0:
        # Template overhead: everything except {facts_block}
        overhead = len(template) - len("{facts_block}") - len("{query}") + len(enriched_query)
        max_facts_chars = max(max_prompt_chars - overhead, 200)

    # Select facts by relevance to query (not just confidence order)
    selected_facts = _select_facts_by_relevance(
        fact_set.facts, query, max_facts, max_facts_chars,
        entities=entities, attributes=attributes,
    )
    logger.debug(
        "Context distillation: %d → %d facts (query=%s)",
        len(fact_set.facts), len(selected_facts), query[:50],
    )
    facts_block = _format_facts(selected_facts, max_facts, max_chars=max_facts_chars)

    return template.format(facts_block=facts_block, query=enriched_query)


def build_verification_prompt(
    answer: str,
    fact_set: FactSet,
    lang: str = "ru",
    max_facts: int = 30,
) -> str:
    """
    Build a prompt for self-verification of an LLM answer.

    Args:
        answer:     Generated answer text.
        fact_set:   Facts that were used for generation.
        lang:       "ru" or "en".
        max_facts:  Max facts to include.

    Returns:
        Complete verification prompt string.
    """
    facts_block = _format_facts(fact_set.facts, max_facts)
    template = _VERIFY_RU if lang == "ru" else _VERIFY_EN
    return template.format(facts_block=facts_block, answer=answer)


__all__ = [
    "build_generation_prompt",
    "build_verification_prompt",
]
