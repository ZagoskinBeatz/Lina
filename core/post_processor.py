# -*- coding: utf-8 -*-
"""
Lina Core — Post-Processor (Phase 22).

Очистка ответов LLM перед выдачей пользователю.
Вырезает: debug-маркеры, системные промпты, внутренние JSON,
           tool-raw-output, служебные метки.

PostProcessor ТОЛЬКО фильтрует — НИКОГДА не генерирует.

Поток:
  LLM_RAW_RESPONSE → PostProcessor.process() → CLEAN_RESPONSE

Если обнаружена утечка → response блокируется (returns None)
  и вызывающий слой должен перегенерировать.
"""

import re
import logging
import threading
from dataclasses import dataclass
from typing import Optional, List

logger = logging.getLogger("lina.core.post_processor")


# ═══════════════════════════════════════════════════════════
#  Leak patterns
# ═══════════════════════════════════════════════════════════

# System prompt leaks
_SYSTEM_PROMPT_LEAKS = [
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"<\|user\|>", re.IGNORECASE),
    re.compile(r"<\|assistant\|>", re.IGNORECASE),
    re.compile(r"\[INST\].*?\[/INST\]", re.DOTALL),
    re.compile(r"<<SYS>>.*?<</SYS>>", re.DOTALL),
    re.compile(r"###\s*(System|Instruction)\s*:", re.IGNORECASE),
    re.compile(r"Ты\s*—?\s*Lina.*?ассистент", re.IGNORECASE),
]

# Debug markers
_DEBUG_MARKERS = [
    re.compile(r"\[DEBUG\].*?$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"\[ROUTER_DECISION\].*?$", re.MULTILINE),
    re.compile(r"\[LLM BUDGET\].*?$", re.MULTILINE),
    re.compile(r"\[SAFETY_NET\].*?$", re.MULTILINE),
    re.compile(r"\[CONTEXT_BUDGET\].*?$", re.MULTILINE),
    re.compile(r"ROUTER_DECISION:.*?$", re.MULTILINE),
    re.compile(r"LLM BUDGET REPORT:.*?$", re.MULTILINE),
]

# Internal JSON structures (raw tool output / planning artifacts)
# Capped quantifier {0,500} prevents catastrophic backtracking (ReDoS).
_INTERNAL_JSON = re.compile(
    r'\{\s*"(error|status|result|debug|internal'
    r'|action|intent|plan|tool|confidence'
    r'|primary_path|step|reasoning)"\s*:\s*["\[{][^}]{0,500}\}',
)

# Tool raw output markers
_TOOL_RAW = [
    re.compile(r"```tool_output\n.*?```", re.DOTALL),
    re.compile(r"<tool_result>.*?</tool_result>", re.DOTALL),
    re.compile(r"<function_output>.*?</function_output>", re.DOTALL),
]


# ═══════════════════════════════════════════════════════════
#  Processing Result
# ═══════════════════════════════════════════════════════════

@dataclass
class ProcessingResult:
    """Результат пост-обработки."""
    text: Optional[str]            # None = blocked (requires regeneration)
    blocked: bool = False          # True if leak or critical issue found
    modifications: int = 0         # Number of modifications applied
    leak_found: bool = False       # System prompt or internal structure leaked
    details: List[str] = None      # List of things found/cleaned

    def __post_init__(self):
        if self.details is None:
            self.details = []


# ═══════════════════════════════════════════════════════════
#  PostProcessor
# ═══════════════════════════════════════════════════════════

class PostProcessor:
    """Очищает ответ LLM от debug, leaks, raw tool output.

    ТОЛЬКО фильтрует — НИКОГДА не генерирует и не исполняет.
    Изолирован от всех engine-ов.
    """

    def __init__(self, strict: bool = False):
        """
        Args:
            strict: В strict-режиме любая утечка → block.
                    В мягком режиме — strip + warning.
        """
        self.strict = strict
        self._stats = {"processed": 0, "blocked": 0, "cleaned": 0}
        self._stats_lock = threading.Lock()

    def process(self, response: str) -> ProcessingResult:
        """Обрабатывает ответ LLM.

        Args:
            response: Сырой ответ LLM.

        Returns:
            ProcessingResult — очищенный текст или blocked=True.
        """
        with self._stats_lock:
            self._stats["processed"] += 1

        if not response or not response.strip():
            return ProcessingResult(text="", blocked=False)

        text = response
        mods = 0
        details: List[str] = []

        # 1. Check for system prompt leaks (CRITICAL)
        for pat in _SYSTEM_PROMPT_LEAKS:
            if pat.search(text):
                details.append(f"LEAK: system prompt pattern")
                if self.strict:
                    with self._stats_lock:
                        self._stats["blocked"] += 1
                    logger.warning("POST_PROCESSOR: BLOCKED — system prompt leak detected")
                    return ProcessingResult(
                        text=None, blocked=True, leak_found=True,
                        details=details,
                    )
                else:
                    text = pat.sub("", text)
                    mods += 1

        # 2. Strip debug markers
        for pat in _DEBUG_MARKERS:
            if pat.search(text):
                text = pat.sub("", text)
                mods += 1
                details.append("stripped debug marker")

        # 3. Strip tool raw output
        for pat in _TOOL_RAW:
            if pat.search(text):
                text = pat.sub("", text)
                mods += 1
                details.append("stripped tool raw output")

        # 4. Strip stray internal JSON (only at top-level, not user-requested)
        # Be careful: user might ask to generate JSON → skip if text is mostly JSON
        matches = list(_INTERNAL_JSON.finditer(text))
        if matches:
            # Character ratio: how much of the text is internal JSON?
            json_chars = sum(m.end() - m.start() for m in matches)
            json_ratio = json_chars / max(len(text), 1)
            if json_ratio < 0.5:
                # Remove matches in reverse order to preserve offsets
                for m in reversed(matches):
                    text = text[:m.start()] + text[m.end():]
                    mods += 1
                    details.append("stripped internal JSON")

        # 5. Clean up excessive whitespace from removals
        if mods > 0:
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text.strip()

        if mods > 0:
            with self._stats_lock:
                self._stats["cleaned"] += 1
            logger.debug(
                "POST_PROCESSOR: cleaned response, %d modifications: %s",
                mods, details,
            )

        return ProcessingResult(
            text=text, blocked=False,
            modifications=mods,
            leak_found=bool(details and any("LEAK" in d for d in details)),
            details=details,
        )

    def get_stats(self):
        with self._stats_lock:
            return dict(self._stats)

    def reset_stats(self):
        with self._stats_lock:
            for k in self._stats:
                self._stats[k] = 0
