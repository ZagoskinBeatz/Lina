# -*- coding: utf-8 -*-
"""
Lina Core — Budget Governor (Phase 23).

Контролирует ресурсы сессии:
  - Общий токен-бюджет сессии
  - Средняя длина ответа
  - Пиковая длина
  - Tool output cap

Если средняя длина > порога → автоматическое снижение max_tokens.
BudgetGovernor ТОЛЬКО рекомендует — НИКОГДА не блокирует напрямую.
"""

import logging
import threading
from collections import deque
from typing import Dict, Any, Optional

logger = logging.getLogger("lina.core.budget_governor")


class BudgetGovernor:
    """Управление ресурсным бюджетом сессии (Phase 23).

    Отслеживает:
      - session_tokens_total  — общее потребление за сессию
      - per-response lengths  — длины последних N ответов
      - peak length           — максимальная длина ответа
      - tool output sizes     — размеры вывода tool-ов

    Автокоррекция:
      Если avg_response_tokens > avg_threshold → рекомендует снижение cap.

    Usage:
        gov = BudgetGovernor(session_budget=100_000)
        gov.record_response(tokens_prompt=200, tokens_generated=150)
        cap = gov.recommended_max_tokens(current_cap=512)
    """

    def __init__(
        self, *,
        session_budget: int = 100_000,
        avg_threshold: int = 400,
        window_size: int = 20,
        tool_output_cap: int = 300,
    ):
        """
        Args:
            session_budget: Макс. токенов за сессию.
            avg_threshold: Порог средней длины ответа (токены).
            window_size: Размер скользящего окна для avg.
            tool_output_cap: Макс. токенов вывода tool-а.
        """
        self.session_budget = session_budget
        self.avg_threshold = avg_threshold
        self.tool_output_cap = tool_output_cap

        self._window: deque = deque(maxlen=window_size)
        self._session_prompt_tokens: int = 0
        self._session_gen_tokens: int = 0
        self._peak_tokens: int = 0
        self._request_count: int = 0
        self._tool_outputs: deque = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def record_response(
        self, tokens_prompt: int = 0, tokens_generated: int = 0,
    ) -> None:
        """Записывает метрики ответа."""
        with self._lock:
            self._window.append(tokens_generated)
            self._session_prompt_tokens += tokens_prompt
            self._session_gen_tokens += tokens_generated
            self._request_count += 1

            if tokens_generated > self._peak_tokens:
                self._peak_tokens = tokens_generated

    def record_tool_output(self, tokens: int) -> None:
        """Записывает размер вывода tool."""
        with self._lock:
            self._tool_outputs.append(tokens)

    @property
    def session_tokens_used(self) -> int:
        """Общее потребление токенов за сессию."""
        with self._lock:
            return self._session_prompt_tokens + self._session_gen_tokens

    @property
    def session_remaining(self) -> int:
        """Оставшийся бюджет сессии."""
        with self._lock:
            used = self._session_prompt_tokens + self._session_gen_tokens
            return max(0, self.session_budget - used)

    @property
    def avg_response_tokens(self) -> float:
        """Средняя длина ответа (по окну)."""
        with self._lock:
            if not self._window:
                return 0.0
            return sum(self._window) / len(self._window)

    @property
    def peak_tokens(self) -> int:
        """Максимальная длина ответа за сессию."""
        with self._lock:
            return self._peak_tokens

    def is_budget_exhausted(self) -> bool:
        """Бюджет сессии исчерпан?"""
        return self.session_tokens_used >= self.session_budget

    def recommended_max_tokens(self, current_cap: int = 512) -> int:
        """Рекомендуемый max_tokens с учётом текущего потребления.

        Если avg > threshold → снижаем на 25%.
        Если budget < 2*current_cap → снижаем до budget/2.
        """
        cap = current_cap

        # Правило 1: средняя выше порога → снизить
        if self.avg_response_tokens > self.avg_threshold and len(self._window) >= 3:
            reduced = int(current_cap * 0.75)
            cap = max(64, reduced)
            logger.debug(
                "BUDGET_GOV: avg=%.0f > threshold=%d → cap %d→%d",
                self.avg_response_tokens, self.avg_threshold,
                current_cap, cap,
            )

        # Правило 2: бюджет заканчивается
        remaining = self.session_remaining
        if remaining < 2 * cap:
            cap = max(32, remaining // 2)
            logger.debug(
                "BUDGET_GOV: remaining=%d → cap=%d",
                remaining, cap,
            )

        return cap

    def check_tool_output(self, tokens: int) -> bool:
        """Проверяет, не превышает ли tool output cap.

        Returns:
            True если в пределах лимита.
        """
        return tokens <= self.tool_output_cap

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            prompt_total = self._session_prompt_tokens
            gen_total = self._session_gen_tokens
            peak = self._peak_tokens
            req_count = self._request_count
            avg_resp = sum(self._window) / len(self._window) if self._window else 0.0
        used = prompt_total + gen_total
        remaining = max(0, self.session_budget - used)
        return {
            "session_budget": self.session_budget,
            "session_used": used,
            "session_remaining": remaining,
            "prompt_total": prompt_total,
            "gen_total": gen_total,
            "avg_response": round(avg_resp, 1),
            "peak_response": peak,
            "requests": req_count,
            "avg_threshold": self.avg_threshold,
            "tool_output_cap": self.tool_output_cap,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для SystemControl."""
        return self.to_dict()
