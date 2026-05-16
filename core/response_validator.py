# -*- coding: utf-8 -*-
"""
Lina Core — Response Validator (Phase 22).

Проверяет ответ LLM на:
  - Соответствие запросу (не отвечает на другой вопрос)
  - Утечки системного промпта
  - Логическую целостность (не обрезан на полуслове)
  - Полноту (не пустой, не шаблонный)
  - Наличие артефактов fallback-а

Validator ТОЛЬКО проверяет — НИКОГДА не исправляет.
Если score < threshold → возвращает is_valid=False,
  вызывающий слой решает: перегенерировать (max 2x) или отдать.
"""

import re
import logging
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("lina.core.response_validator")


# ═══════════════════════════════════════════════════════════
#  Validation Result
# ═══════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    """Результат валидации ответа."""
    is_valid: bool = True
    score: float = 1.0           # 0.0–1.0, чем выше — тем лучше
    issues: List[str] = field(default_factory=list)
    can_retry: bool = True       # False = бесполезно перегенерировать

    def to_dict(self):
        return {
            "is_valid": self.is_valid,
            "score": round(self.score, 2),
            "issues": self.issues,
            "can_retry": self.can_retry,
        }


# ═══════════════════════════════════════════════════════════
#  Patterns
# ═══════════════════════════════════════════════════════════

# Шаблонные/fallback-ответы
_FALLBACK_PATTERNS = [
    re.compile(r"^(я\s+не\s+знаю|не\s+могу\s+ответить)\.?$", re.IGNORECASE),
    re.compile(r"^I\s+(don't|can't)\s+(know|answer)", re.IGNORECASE),
    re.compile(r"^(error|ошибка)\s*[:.]", re.IGNORECASE),
]

# Обрезанные ответы (заканчиваются на полуслове)
_TRUNCATION_PATTERN = re.compile(r"\S$")  # no trailing punctuation or space

# Системные промпты
_SYSTEM_LEAK_PATTERNS = [
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"<<SYS>>", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
]


# ═══════════════════════════════════════════════════════════
#  ResponseValidator
# ═══════════════════════════════════════════════════════════

class ResponseValidator:
    """Валидатор ответов LLM (Phase 22).

    ТОЛЬКО проверяет — НИКОГДА не исправляет.
    Изолирован от engine-ов.

    Attributes:
        score_threshold: Минимальный score для is_valid=True.
        max_retries: Рекомендуемое макс. число регенераций (для caller).
    """

    def __init__(self, score_threshold: float = 0.3, max_retries: int = 2):
        self.score_threshold = score_threshold
        self.max_retries = max_retries
        self._stats = {"validated": 0, "failed": 0, "passed": 0}
        self._stats_lock = threading.Lock()

    def validate(
        self, response: str, user_input: str = "",
        context: Optional[str] = None,
    ) -> ValidationResult:
        """Проверяет ответ LLM.

        Args:
            response: Ответ LLM (после пост-обработки).
            user_input: Исходный запрос пользователя.
            context: Контекст (RAG/history), если есть.

        Returns:
            ValidationResult со score и issues.
        """
        with self._stats_lock:
            self._stats["validated"] += 1
        issues: List[str] = []
        score = 1.0

        # 1. Empty/whitespace check
        if not response or not response.strip():
            with self._stats_lock:
                self._stats["failed"] += 1
            return ValidationResult(
                is_valid=False, score=0.0,
                issues=["empty response"],
                can_retry=True,
            )

        text = response.strip()

        # 2. Fallback / template check
        for pat in _FALLBACK_PATTERNS:
            if pat.search(text):
                issues.append("fallback template detected")
                score -= 0.3
                break

        # 3. System leak check
        for pat in _SYSTEM_LEAK_PATTERNS:
            if pat.search(text):
                issues.append("system prompt leak")
                score -= 0.5
                break

        # 4. Truncation check (response > 20 chars, ends without punct)
        if len(text) > 20:
            last_char = text[-1]
            if last_char not in ".!?»\"')]:;…—`}>0123456789%°±·/_-=+#@~\\":
                issues.append("possible truncation")
                score -= 0.2

        # 5. Very short response to non-trivial question
        if user_input and len(user_input) > 30 and len(text) < 10:
            issues.append("suspiciously short response")
            score -= 0.2

        # 6. Repetition check (same phrase repeated 3+ times)
        words = text.lower().split()
        if len(words) > 20:
            bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
            common = Counter(bigrams).most_common(1)
            if common and common[0][1] >= 5:
                issues.append(f"excessive repetition: '{common[0][0]}' x{common[0][1]}")
                score -= 0.3

        # 7. Language mismatch (user wrote in Russian, response in English)
        if user_input:
            user_has_cyrillic = bool(re.search(r"[а-яёА-ЯЁ]", user_input))
            resp_has_cyrillic = bool(re.search(r"[а-яёА-ЯЁ]", text))
            if user_has_cyrillic and not resp_has_cyrillic and len(text) > 50:
                issues.append("language mismatch (user=ru, response=en)")
                score -= 0.15

        # Clamp score
        score = max(0.0, min(1.0, score))

        is_valid = score >= self.score_threshold

        if is_valid:
            with self._stats_lock:
                self._stats["passed"] += 1
        else:
            with self._stats_lock:
                self._stats["failed"] += 1

        if issues:
            logger.debug(
                "VALIDATOR: score=%.2f valid=%s issues=%s",
                score, is_valid, issues,
            )

        return ValidationResult(
            is_valid=is_valid, score=score,
            issues=issues, can_retry=not is_valid,
        )

    def get_stats(self):
        with self._stats_lock:
            return dict(self._stats)

    def reset_stats(self):
        with self._stats_lock:
            for k in self._stats:
                self._stats[k] = 0
