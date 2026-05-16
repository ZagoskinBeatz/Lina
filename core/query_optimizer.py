# -*- coding: utf-8 -*-
"""
Lina Core — Query Optimizer (Phase 28).

Переписывает пользовательский запрос для улучшения качества
веб-поиска. Локальные модели часто получают плохие результаты
из-за «человеческого» формата запроса.

Стратегии:
  1. Транслитерация RU → EN для технических терминов
  2. Добавление ключевых слов (specs, review)
  3. Удаление мусорных слов
  4. Entity-aware формирование запроса

Пример:
  "процессор realme 10"  → "Realme 10 processor specs"
  "какой экран у Galaxy S24" → "Samsung Galaxy S24 display specifications"
"""

from __future__ import annotations

import re
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger("lina.core.query_optimizer")


# ═══════════════════════════════════════════════════════════════════════════════
#  Translation Maps
# ═══════════════════════════════════════════════════════════════════════════════

# Технические термины RU → EN (для улучшения результатов поиска)
_RU_TO_EN = {
    "процессор": "processor",
    "чипсет": "chipset",
    "видеокарта": "GPU",
    "видеокарту": "GPU",
    "оперативная память": "RAM",
    "оперативку": "RAM",
    "оперативной": "RAM",
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
    "сравнение": "comparison",
    "ёмкость": "capacity",
    "разрешение": "resolution",
    "производительность": "performance",
    "тест": "benchmark",
    "бенчмарк": "benchmark",
    "фото": "photo",
    "фотографии": "photos",
}

# Мусорные слова для удаления
_FILLER_WORDS = {
    "какой", "какая", "какое", "какие", "какую",
    "расскажи", "покажи", "скажи", "подскажи",
    "мне", "пожалуйста", "будь", "добра",
    "про", "о", "об", "для", "на", "в", "у",
    "это", "где", "когда", "что",
    "его", "её", "их",
    "нужен", "нужна", "нужно",
    "хочу", "хотел", "знать",
    "мне", "найди", "поищи", "нагугли", "загугли",
    "tell", "me", "about", "show", "find",
}

# Слова, указывающие на тип запроса
_INTENT_HINTS = {
    "сравни": ("vs", "comparison"),
    "сравнение": ("vs", "comparison"),
    "лучше": ("vs", "comparison"),
    "стоит ли": ("worth buying", "review"),
    "купить": ("buy", "price"),
    "альтернатив": ("alternatives", "alternative"),
    "аналог": ("alternatives", "alternative"),
    "скачать": ("download",),
    "как скачать": ("download", "install"),
    "установить": ("install", "setup"),
    "проблем": ("issues", "problems"),
    "ошибк": ("error", "fix"),
    "не работает": ("not working", "fix"),
}


# ═══════════════════════════════════════════════════════════════════════════════
#  Query Optimizer
# ═══════════════════════════════════════════════════════════════════════════════

class QueryOptimizer:
    """
    Оптимизирует поисковый запрос для лучших результатов.

    Usage:
        opt = QueryOptimizer()
        queries = opt.optimize("процессор realme 10", device="Realme 10")
        # → ["Realme 10 processor specs", "realme 10 процессор характеристики"]
    """

    def optimize(
        self,
        query: str,
        device: Optional[str] = None,
        attribute: Optional[str] = None,
    ) -> List[str]:
        """
        Оптимизировать запрос. Возвращает список вариантов (основной + альтернативный).

        Args:
            query: Исходный запрос пользователя (уже очищенный от командных префиксов)
            device: Извлечённое устройство (из EntityParser)
            attribute: Извлечённый атрибут (cpu, gpu, ram, display...)

        Returns:
            [optimized_query, alternative_query] — основной + запасной варианты
        """
        queries = []

        # Основная оптимизация: английский spec-запрос
        en_query = self._build_en_query(query, device, attribute)
        if en_query:
            queries.append(en_query)

        # Запасной вариант: русский запрос с очисткой
        ru_query = self._build_ru_query(query, device, attribute)
        if ru_query and ru_query not in queries:
            queries.append(ru_query)

        # Если ни один вариант не сработал — вернуть исходный
        if not queries:
            queries.append(query)

        return queries[:3]

    def _build_en_query(
        self, query: str, device: Optional[str], attribute: Optional[str],
    ) -> Optional[str]:
        """Построить оптимизированный английский запрос."""
        parts = []

        # Устройство — главная часть
        if device:
            parts.append(device)

        # Атрибут на English
        en_attr = None
        if attribute:
            for ru, en in _RU_TO_EN.items():
                if attribute.lower().startswith(ru[:4]):
                    en_attr = en
                    break
        if en_attr:
            parts.append(en_attr)

        # Если нет device, извлечь из запроса
        if not parts:
            # Оставить только существенные слова
            words = query.split()
            for w in words:
                wl = w.lower()
                if wl in _FILLER_WORDS:
                    continue
                # Транслировать RU → EN
                translated = False
                for ru, en in _RU_TO_EN.items():
                    if wl == ru or wl.startswith(ru[:5]):
                        parts.append(en)
                        translated = True
                        break
                if not translated and len(w) >= 2:
                    parts.append(w)

        if not parts:
            return None

        # Добавить "specs" если запрос про характеристики
        query_lower = query.lower()
        if any(w in query_lower for w in ("характеристик", "спецификац", "specs")):
            if "specifications" not in parts and "specs" not in parts:
                parts.append("specs")

        en_query = " ".join(parts)

        return en_query

    def _build_ru_query(
        self, query: str, device: Optional[str], attribute: Optional[str],
    ) -> Optional[str]:
        """Построить очищенный русский запрос."""
        # Убрать мусорные слова
        words = query.split()
        clean = []
        for w in words:
            if w.lower() in _FILLER_WORDS:
                continue
            clean.append(w)

        if not clean:
            return None

        result = " ".join(clean)

        # Если есть device но его нет в начале — поставить первым
        if device:
            if not result.lower().startswith(device.lower()):
                result = f"{device} {result}"

        return result

    def rewrite_for_followup(
        self,
        current_query: str,
        topic: str,
        attribute: Optional[str] = None,
    ) -> str:
        """
        Переписать follow-up запрос с контекстом.

        "а какой процессор" + topic="Realme 10"
        → "Realme 10 процессор характеристики"
        """
        # Убрать follow-up маркеры
        cleaned = re.sub(
            r"^(а\s+|и\s+ещё\s+|ещё\s+|также\s+|а\s+ещё\s+)",
            "", current_query, flags=re.IGNORECASE,
        ).strip()

        # Убрать местоимения
        cleaned = re.sub(
            r"\b(у\s+него|у\s+неё|его|её|их|этого|этой|этих|там)\b",
            "", cleaned, flags=re.IGNORECASE,
        ).strip()

        # Убрать мусор
        words = cleaned.split()
        clean_words = [w for w in words if w.lower() not in _FILLER_WORDS]

        if clean_words:
            return f"{topic} {' '.join(clean_words)}"
        if attribute:
            return f"{topic} {attribute}"
        return f"{topic} {cleaned}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Conversation State
# ═══════════════════════════════════════════════════════════════════════════════

class ConversationState:
    """
    Контекст диалога для multi-turn web_search.

    Хранит стек последних 3 тем + сущности + вопросы.
    Заменяет примитивный _last_intent[0].
    """

    MAX_TURNS = 3

    def __init__(self):
        self._turns: List[dict] = []

    def push(
        self,
        intent: str,
        query: str,
        topic: Optional[str] = None,
        entities: Optional[List[str]] = None,
    ) -> None:
        """Сохранить текущий ход диалога."""
        self._turns.append({
            "intent": intent,
            "query": query,
            "topic": topic or "",
            "entities": entities or [],
        })
        # Ограничить глубину
        if len(self._turns) > self.MAX_TURNS:
            self._turns = self._turns[-self.MAX_TURNS:]

    @property
    def last_intent(self) -> str:
        return self._turns[-1]["intent"] if self._turns else ""

    @property
    def last_topic(self) -> str:
        """Последняя упомянутая тема."""
        for turn in reversed(self._turns):
            if turn["topic"]:
                return turn["topic"]
        return ""

    @property
    def last_entities(self) -> List[str]:
        """Последние упомянутые сущности."""
        for turn in reversed(self._turns):
            if turn["entities"]:
                return turn["entities"]
        return []

    @property
    def recent_topics(self) -> List[str]:
        """Все темы из последних ходов (для контекста)."""
        return [t["topic"] for t in self._turns if t["topic"]]

    def is_web_followup(self, text: str) -> bool:
        """Проверить, является ли запрос follow-up после web_search."""
        if not self._turns:
            return False

        # Проверить последние N ходов (не только последний)
        has_web = any(
            t["intent"] == "web_search"
            for t in self._turns[-2:]
        )
        if not has_web:
            return False

        # Паттерны follow-up
        _FOLLOWUP_PATS = [
            r"^а\s+(сколько|как|что|какой|какая|какое|какие|где|когда)\b",
            r"^(и\s+ещё|ещё|также|а\s+ещё)\b",
            r"^(а\s+)?что\s+(насчёт|на\s*счёт|по\s+поводу)\b",
            r"\b(у\s+него|у\s+неё|его|её|их|этого|этой|этих|там)\b",
        ]
        q = text.strip()
        for pat in _FOLLOWUP_PATS:
            if re.search(pat, q, re.IGNORECASE):
                return True
        return False

    def clear(self) -> None:
        """Очистить состояние."""
        self._turns.clear()

    def to_dict(self) -> dict:
        return {
            "turns": self._turns.copy(),
            "last_intent": self.last_intent,
            "last_topic": self.last_topic,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════════════════

_optimizer: Optional[QueryOptimizer] = None


def get_query_optimizer() -> QueryOptimizer:
    """Получить (или создать) экземпляр QueryOptimizer."""
    global _optimizer
    if _optimizer is None:
        _optimizer = QueryOptimizer()
    return _optimizer
