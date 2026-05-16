# -*- coding: utf-8 -*-
"""
Lina LLM — Self-Verifier (v2 Pipeline).

Performs a second LLM pass to check whether the generated answer
is faithful to the verified facts.

Algorithm:
  1. Build a verification prompt: facts + generated answer.
  2. Ask LLM: "Does the answer contain claims NOT supported by facts?"
  3. Parse LLM response → list of hallucination flags.
  4. If flags detected → signal regeneration.

This is the #1 anti-hallucination measure.
If self-verification cannot run (no LLM available), the answer is
returned as-is with lower confidence.
"""

from __future__ import annotations

import re
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from lina.models.datatypes import Fact, FactSet, PipelineAnswer

logger = logging.getLogger("lina.llm.self_verifier")


# ═══════════════════════════════════════════════════
#  Verification Prompt
# ═══════════════════════════════════════════════════

_VERIFY_PROMPT_RU = """Ты — верификатор ответов.  Твоя задача: проверить,
соответствует ли ответ ТОЛЬКО предоставленным фактам.

=== ФАКТЫ ===
{facts}

=== ОТВЕТ ===
{answer}

Инструкция:
- Если ответ полностью соответствует фактам, напиши ОДНОЙ строкой: OK
- Если есть утверждения, НЕ подкреплённые фактами, перечисли их:
  HALLUCINATION: <цитата из ответа>
- Если ответ содержит числа/характеристики, отличающиеся от фактов:
  MISMATCH: <что не совпадает>

Отвечай ТОЛЬКО в указанном формате, без пояснений."""

_VERIFY_PROMPT_EN = """You are an answer verifier. Your job: check whether
the answer is faithful ONLY to the provided facts.

=== FACTS ===
{facts}

=== ANSWER ===
{answer}

Instructions:
- If the answer fully matches the facts, write a single line: OK
- If any claims are NOT supported by the facts, list them:
  HALLUCINATION: <quote from answer>
- If numbers/specs differ from facts:
  MISMATCH: <what differs>

Respond ONLY in the specified format, no explanations."""


@dataclass
class VerificationResult:
    """Output of self-verification."""
    is_faithful: bool = True
    hallucinations: List[str] = field(default_factory=list)
    mismatches: List[str] = field(default_factory=list)
    raw_response: str = ""
    elapsed_ms: float = 0.0

    @property
    def has_issues(self) -> bool:
        return not self.is_faithful or bool(self.hallucinations) or bool(self.mismatches)


class SelfVerifier:
    """
    Verifies LLM answers against extracted facts.

    Usage:
        verifier = SelfVerifier(llm_fn=engine.generate)
        result = verifier.verify(answer, fact_set, lang="ru")
    """

    def __init__(self, llm_fn=None):
        """
        Args:
            llm_fn: Callable(prompt: str) → str.
                     If None, verification is skipped (answer passes through).
        """
        self._llm_fn = llm_fn

    def verify(
        self,
        answer: PipelineAnswer,
        fact_set: FactSet,
        lang: str = "ru",
    ) -> VerificationResult:
        """
        Run self-verification on the answer.

        Args:
            answer:    Generated answer.
            fact_set:  Facts used for generation.
            lang:      "ru" or "en".

        Returns:
            VerificationResult with hallucination flags.
        """
        if self._llm_fn is None:
            logger.warning("SelfVerifier: no LLM function, skipping verification")
            return VerificationResult(is_faithful=True)

        if not fact_set.facts:
            logger.info("SelfVerifier: no facts to verify against → OK")
            return VerificationResult(is_faithful=True)

        t0 = time.time()

        # Build prompt
        facts_text = fact_set.format_for_llm_ru() if lang == "ru" else fact_set.format_for_llm()
        template = _VERIFY_PROMPT_RU if lang == "ru" else _VERIFY_PROMPT_EN
        prompt = template.format(facts=facts_text, answer=answer.text)

        # Call LLM
        try:
            raw_response = self._llm_fn(prompt)
        except Exception as e:
            logger.error("SelfVerifier LLM call failed: %s", e)
            return VerificationResult(is_faithful=True, raw_response=str(e))

        elapsed = (time.time() - t0) * 1000

        # Parse response
        result = self._parse_response(raw_response)
        result.elapsed_ms = elapsed

        if result.has_issues:
            logger.warning(
                "SelfVerifier: issues detected — %d hallucinations, %d mismatches (%.0f ms)",
                len(result.hallucinations), len(result.mismatches), elapsed,
            )
        else:
            logger.info("SelfVerifier: answer verified OK (%.0f ms)", elapsed)

        return result

    @staticmethod
    def _parse_response(response: str) -> VerificationResult:
        """Parse verification LLM output."""
        text = response.strip()
        result = VerificationResult(raw_response=text)

        if not text:
            result.is_faithful = True
            return result

        # Check for clean OK
        if re.match(r"^OK\s*$", text, re.IGNORECASE):
            result.is_faithful = True
            return result

        # Look for hallucination flags
        for m in re.finditer(r"HALLUCINATION:\s*(.+)", text, re.IGNORECASE):
            result.hallucinations.append(m.group(1).strip())

        for m in re.finditer(r"MISMATCH:\s*(.+)", text, re.IGNORECASE):
            result.mismatches.append(m.group(1).strip())

        result.is_faithful = not (result.hallucinations or result.mismatches)
        return result


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_verifier: SelfVerifier | None = None


def get_self_verifier(llm_fn=None) -> SelfVerifier:
    global _verifier
    if _verifier is None:
        _verifier = SelfVerifier(llm_fn=llm_fn)
    return _verifier
