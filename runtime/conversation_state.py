"""
Lina Runtime — Conversation State.

Управление состоянием диалога.

Функции:
  - Хранение последних N обменов (user, assistant)
  - Обрезка длинных ответов в истории
  - Сброс при смене темы
  - Подготовка истории для промпта
"""

import time
import logging
from collections import deque
from typing import List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger("lina.runtime.conversation_state")


# Максимальное количество хранимых обменов
MAX_TURNS = 10

# Максимальная длина одной записи в истории (символов)
MAX_ENTRY_LENGTH = 300


@dataclass
class Turn:
    """Один обмен в диалоге."""
    user: str
    assistant: str
    timestamp: float
    tier: str = "full"  # Какая модель ответила

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


class ConversationState:
    """
    Хранилище состояния диалога.

    Хранит последние MAX_TURNS обменов.
    Предоставляет историю для PromptBuilder.

    Использование:
        state = ConversationState()
        state.add("привет", "Привет! Как дела?")
        history = state.get_history()
        # → [("привет", "Привет! Как дела?")]
    """

    def __init__(self, max_turns: int = MAX_TURNS):
        self._turns: deque = deque(maxlen=max_turns)

    def add(self, user: str, assistant: str, tier: str = "full") -> None:
        """
        Добавляет обмен в историю.

        Args:
            user: Запрос пользователя.
            assistant: Ответ ассистента.
            tier: Модель, которая ответила.
        """
        self._turns.append(Turn(
            user=user,
            assistant=assistant,
            timestamp=time.time(),
            tier=tier,
        ))

    def get_history(self, max_turns: Optional[int] = None) -> List[Tuple[str, str]]:
        """
        Возвращает историю для PromptBuilder.

        Каждый ответ обрезается до MAX_ENTRY_LENGTH.

        Args:
            max_turns: Максимум обменов (default: все).

        Returns:
            Список пар (user_msg, assistant_msg).
        """
        turns = list(self._turns)
        if max_turns:
            turns = turns[-max_turns:]

        result = []
        for turn in turns:
            user = turn.user[:MAX_ENTRY_LENGTH]
            assistant = turn.assistant[:MAX_ENTRY_LENGTH]
            if len(turn.assistant) > MAX_ENTRY_LENGTH:
                assistant += "..."
            result.append((user, assistant))

        return result

    def get_last_response(self) -> Optional[str]:
        """Возвращает последний ответ (для контекста)."""
        if self._turns:
            return self._turns[-1].assistant
        return None

    def get_last_tier(self) -> Optional[str]:
        """Tier последнего ответа."""
        if self._turns:
            return self._turns[-1].tier
        return None

    def clear(self) -> None:
        """Очищает историю диалога."""
        self._turns.clear()
        logger.debug("Conversation history cleared")

    @property
    def turn_count(self) -> int:
        """Количество сохранённых обменов."""
        return len(self._turns)

    @property
    def turns(self) -> List[Turn]:
        """Все обмены (для диагностики)."""
        return list(self._turns)
