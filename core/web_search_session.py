# -*- coding: utf-8 -*-
"""
Lina Core — Web Search Session (интеграция с парсером из Parcer/search_cli).

Управляет состоянием веб-поиска между запросами:
  - Отслеживание посещённых URL (не скачиваем повторно)
  - Контекст разговора (follow-up вопросы используют предыдущие ответы)
  - Объединение новых и старых сводок для целостного ответа
  - Определение языка запроса

Адаптировано из Parcer/search_cli/conversation.py + search.py.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

logger = logging.getLogger("lina.core.web_search_session")


# ---------------------------------------------------------------------------
# Follow-up detection patterns
# ---------------------------------------------------------------------------

_FOLLOWUP_PATTERNS: list = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^tell me more",
        r"^расскажи (ещё|еще|подробнее|больше)",
        r"^подробнее",
        r"^ещ[её]",
        r"^more( about| on| details)?$",
        r"^what about\b",
        r"^а что (насчёт|насчет|с )\b",
        r"^how about\b",
        r"^compare",
        r"^сравни",
        r"^and\b",
        r"^а\b",
        r"^also\b",
        r"^также\b",
        r"^а сколько\b",
        r"^а какой\b",
        r"^а какая\b",
        r"^а какие\b",
        r"^и ещё\b",
        r"^и еще\b",
    ]
]

# Filler words to strip from follow-up queries when building effective search
_FILLER_RE = re.compile(
    r"^(tell me more about |what about |а что (насчёт|насчет|с |на ?счёт )|"
    r"расскажи (ещё |еще |подробнее |больше )?(о |об |про )?|"
    r"подробнее (о |об |про )?|how about |compare (it )?(with |to )?|"
    r"сравни (с |его с )?|and |а |also |также |"
    r"а сколько |а какой |а какая |а какие |и ещё |и еще )",
    re.IGNORECASE,
)


@dataclass
class Message:
    """Одно сообщение в истории разговора."""
    role: str          # "user" or "assistant"
    content: str


@dataclass
class WebSearchSession:
    """
    Состояние веб-поиска для одного CLI-сеанса.

    Отслеживает:
      - Историю разговора (последние N пар сообщений)
      - Посещённые URL (не скачиваем повторно)
      - Текущую тему (для follow-up запросов)
      - Последнюю суммаризацию (для объединения с новыми данными)
      - Язык запроса
    """
    history: List[Message] = field(default_factory=list)
    visited_urls: Set[str] = field(default_factory=set)
    last_summary: str = ""
    current_topic: str = ""
    language: str = "ru"

    # Максимум пар (user+assistant) в истории
    _MAX_TURNS: int = 4

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def add_user(self, content: str) -> None:
        """Добавить сообщение пользователя."""
        self.history.append(Message(role="user", content=content))
        self._trim()

    def add_assistant(self, content: str) -> None:
        """Добавить ответ ассистента."""
        self.history.append(Message(role="assistant", content=content))
        self._trim()

    def _trim(self) -> None:
        """Оставить только последние _MAX_TURNS * 2 сообщений."""
        max_msgs = self._MAX_TURNS * 2
        if len(self.history) > max_msgs:
            self.history = self.history[-max_msgs:]

    def get_history_text(self, max_chars: int = 600) -> str:
        """
        Вернуть историю в текстовом виде для промпта LLM.

        Идём с конца, чтобы самые свежие сообщения всегда попали.
        """
        parts: list = []
        total = 0
        for msg in reversed(self.history):
            line = f"{msg.role.upper()}: {msg.content}"
            if total + len(line) > max_chars:
                break
            parts.append(line)
            total += len(line)
        parts.reverse()
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # URL tracking
    # ------------------------------------------------------------------

    def mark_urls(self, urls: List[str]) -> None:
        """Пометить URL как посещённые."""
        self.visited_urls.update(urls)

    def filter_new_urls(self, urls: List[str]) -> List[str]:
        """Вернуть только ещё не посещённые URL."""
        return [u for u in urls if u not in self.visited_urls]

    # ------------------------------------------------------------------
    # Follow-up detection
    # ------------------------------------------------------------------

    def is_followup(self, query: str) -> bool:
        """
        Определить, является ли запрос follow-up вопросом о текущей теме.

        Критерии:
          - Совпадает один из шаблонов follow-up, ИЛИ
          - Есть активная тема И запрос короткий (≤ 6 слов)
        """
        stripped = query.strip()
        if not self.current_topic:
            return False

        for pat in _FOLLOWUP_PATTERNS:
            if pat.search(stripped):
                return True

        # Короткие запросы при наличии топика — скорее всего уточнение
        if len(stripped.split()) <= 6:
            return True

        return False

    def build_effective_query(self, raw_query: str) -> str:
        """
        Если запрос — follow-up, добавить текущую тему для контекста.

        Пример: topic="Realme 10", query="а что с камерой?"
          → "Realme 10 камера"
        """
        if not self.is_followup(raw_query) or not self.current_topic:
            # Новая тема
            self.current_topic = raw_query
            return raw_query

        # Убираем слова-заполнители и комбинируем с темой
        cleaned = _FILLER_RE.sub("", raw_query).strip() or raw_query
        combined = f"{self.current_topic} {cleaned}"
        return combined

    # ------------------------------------------------------------------
    # Summary merging (для follow-up)
    # ------------------------------------------------------------------

    def merge_with_previous(
        self,
        new_summary: str,
        query: str,
    ) -> str:
        """
        Объединить новую суммаризацию с предыдущей через mini-LLM.

        Если mini-LLM недоступен, просто конкатенирует тексты.
        """
        if not self.last_summary:
            return new_summary

        try:
            from lina.parser.web_llm import merge_web_summaries
            merged = merge_web_summaries(
                query=query,
                old_summary=self.last_summary,
                new_text=new_summary,
                language=self.language,
            )
            if merged and len(merged.strip()) > 50:
                return merged
        except Exception as e:
            logger.debug("Mini-LLM merge unavailable: %s", e)

        # Fallback: простая конкатенация
        return f"{self.last_summary}\n\n--- Дополнительно ---\n{new_summary}"

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Сбросить сессию."""
        self.history.clear()
        self.visited_urls.clear()
        self.last_summary = ""
        self.current_topic = ""
        self.language = "ru"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_session: Optional[WebSearchSession] = None


def get_web_search_session() -> WebSearchSession:
    """Вернуть глобальную сессию веб-поиска (singleton)."""
    global _session
    if _session is None:
        _session = WebSearchSession()
    return _session
