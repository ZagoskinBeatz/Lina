"""
Lina — История команд и автоматическая индексация.

Записывает все команды пользователя и результаты действий,
автоматически индексирует их в RAG для будущих запросов.

Данные:
  - command_history.json: полная история команд
  - автоматически добавляется в векторный индекс
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import List, Optional, Dict
from collections import deque

from lina.config import config, CACHE_DIR, LOGS_DIR

logger = logging.getLogger(__name__)


HISTORY_FILE = CACHE_DIR / "command_history.json"
MAX_HISTORY = 1000  # макс записей в истории


class HistoryEntry:
    """Одна запись истории."""

    def __init__(
        self,
        command: str,
        response: str,
        timestamp: float = 0,
        success: bool = True,
        category: str = "general",
    ):
        self.command = command
        self.response = response[:2000]  # ограничиваем длину
        self.timestamp = timestamp or time.time()
        self.success = success
        self.category = category

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "response": self.response,
            "timestamp": self.timestamp,
            "success": self.success,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryEntry":
        return cls(
            command=d["command"],
            response=d.get("response", ""),
            timestamp=d.get("timestamp", 0),
            success=d.get("success", True),
            category=d.get("category", "general"),
        )

    def to_text(self) -> str:
        """Конвертирует в текст для индексации."""
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))
        status = "OK" if self.success else "FAIL"
        return (
            f"[{ts}] [{status}] Команда: {self.command}\n"
            f"Результат: {self.response[:500]}"
        )


class CommandHistory:
    """
    Менеджер истории команд.

    Функции:
      - Запись всех команд и ответов
      - Поиск по истории
      - Экспорт для индексации в RAG
      - Статистика использования
    """

    def __init__(self):
        self._entries: deque = deque(maxlen=MAX_HISTORY)
        self._loaded = False
        self._lock = threading.Lock()
        self._dirty = False
        self._save_timer: Optional[threading.Timer] = None
        self._SAVE_DEBOUNCE = 2.0  # секунд

    def _ensure_loaded(self) -> None:
        """Ленивая загрузка истории."""
        if not self._loaded:
            self._load()
            self._loaded = True

    def add(
        self,
        command: str,
        response: str,
        success: bool = True,
        category: str = "general",
    ) -> None:
        """Добавляет запись в историю."""
        with self._lock:
            self._ensure_loaded()

            # Пропускаем пустые и служебные
            if not command.strip() or command.startswith("/"):
                return

            entry = HistoryEntry(
                command=command,
                response=response,
                success=success,
                category=category,
            )
            self._entries.append(entry)
            self._schedule_save()

    def get_recent(self, n: int = 20) -> List[HistoryEntry]:
        """Возвращает последние N записей."""
        with self._lock:
            self._ensure_loaded()
            entries = list(self._entries)
        return entries[-n:]

    def search(self, query: str, limit: int = 10) -> List[HistoryEntry]:
        """Поиск по истории (простой текстовый поиск)."""
        with self._lock:
            self._ensure_loaded()
            query_lower = query.lower()
            results = []
            for entry in reversed(list(self._entries)):
                if query_lower in entry.command.lower() or query_lower in entry.response.lower():
                    results.append(entry)
                    if len(results) >= limit:
                        break
        return results

    def get_chunks_for_indexing(self) -> tuple:
        """
        Экспортирует историю как чанки для индексации в RAG.

        Returns:
            (chunks: List[str], metadata: List[dict])
        """
        with self._lock:
            self._ensure_loaded()
            snapshot = list(self._entries)

        chunks = []
        metadata = []
        for entry in snapshot:
            text = entry.to_text()
            if len(text) > 50:  # пропускаем слишком короткие
                chunks.append(text)
                metadata.append({
                    "source": "command_history",
                    "filename": "история_команд",
                    "category": entry.category,
                    "timestamp": entry.timestamp,
                    "success": entry.success,
                })

        return chunks, metadata

    def get_stats(self) -> dict:
        """Возвращает статистику истории."""
        with self._lock:
            self._ensure_loaded()
            entries = list(self._entries)
        return {
            "total": len(entries),
            "successful": sum(1 for e in entries if e.success),
            "failed": sum(1 for e in entries if not e.success),
            "categories": dict(
                sorted(
                    {
                        cat: sum(1 for e in entries if e.category == cat)
                        for cat in set(e.category for e in entries)
                    }.items(),
                    key=lambda x: x[1],
                    reverse=True,
                )
            ),
        }

    def clear(self) -> None:
        """Очищает историю."""
        with self._lock:
            self._entries.clear()
            self._save()

    def format_recent(self, n: int = 10) -> str:
        """Форматирует последние записи для вывода."""
        recent = self.get_recent(n)
        if not recent:
            return "История команд пуста."

        lines = [f"📜 Последние {len(recent)} команд:"]
        for entry in recent:
            ts = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
            icon = "✅" if entry.success else "❌"
            cmd = entry.command[:60]
            lines.append(f"  {ts} {icon} {cmd}")

        return "\n".join(lines)

    # ── Персистенция ──

    def _schedule_save(self) -> None:
        """Отложенное сохранение (debounce) — вызывать под self._lock."""
        self._dirty = True
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(self._SAVE_DEBOUNCE, self._flush)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _flush(self) -> None:
        """Сброс на диск (вызывается таймером или явно)."""
        with self._lock:
            if self._dirty:
                self._save()
                self._dirty = False

    def flush(self) -> None:
        """Явный сброс на диск (для graceful shutdown)."""
        self._flush()

    def _load(self) -> None:
        """Загружает историю из файла."""
        try:
            if HISTORY_FILE.exists():
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for d in data:
                    self._entries.append(HistoryEntry.from_dict(d))
        except (json.JSONDecodeError, IOError, KeyError) as e:
            logger.warning("Failed to load command history: %s", e)

    def _save(self) -> None:
        """Сохраняет историю в файл (атомарно)."""
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = [e.to_dict() for e in self._entries]
            tmp_path = HISTORY_FILE.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(str(tmp_path), str(HISTORY_FILE))
        except IOError as e:
            logger.error("Failed to save command history: %s", e)
