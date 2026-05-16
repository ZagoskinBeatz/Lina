"""
Lina GUI — Chat History Persistence.

Сохраняет / загружает / удаляет сессии чата.
Хранение: ~/.local/share/lina/chats/*.json

Каждый файл:
{
    "id": "uuid",
    "title": "Первое сообщение пользователя",
    "created": 1700000000.0,
    "updated": 1700000000.0,
    "messages": [
        {"role": "user", "content": "...", "timestamp": ..., "status": "complete", "message_id": 1},
        ...
    ]
}
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from pathlib import Path

logger = logging.getLogger("lina.gui.history")

_CHATS_DIR = Path.home() / ".local" / "share" / "lina" / "chats"
_SAFE_SESSION_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')


@dataclass
class ChatSession:
    """Одна сессия чата."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = "Новый чат"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    messages: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created": self.created,
            "updated": self.updated,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatSession":
        return cls(
            id=data.get("id", uuid.uuid4().hex[:12]),
            title=data.get("title", "Новый чат"),
            created=data.get("created", time.time()),
            updated=data.get("updated", time.time()),
            messages=data.get("messages", []),
        )

    def preview(self) -> str:
        """Краткий текст для отображения в списке."""
        if not self.messages:
            return "Пустой чат"
        first_user = next(
            (m["content"] for m in self.messages if m.get("role") == "user"),
            "Пустой чат",
        )
        return first_user[:60] + ("…" if len(first_user) > 60 else "")


class ChatHistoryManager:
    """Менеджер истории чатов: CRUD + группировка по дате."""

    def __init__(self, chats_dir: Optional[Path] = None):
        self._dir = chats_dir or _CHATS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sessions: Dict[str, ChatSession] = {}
        self._load_all()

    def _file_path(self, session_id: str) -> Path:
        if not _SAFE_SESSION_ID_RE.match(session_id):
            raise ValueError(f"Invalid session ID: {session_id!r}")
        return self._dir / f"{session_id}.json"

    def _load_all(self):
        """Загружает все сессии с диска."""
        self._sessions.clear()
        if not self._dir.exists():
            return
        for path in sorted(self._dir.glob("*.json"), reverse=True):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                session = ChatSession.from_dict(data)
                self._sessions[session.id] = session
            except Exception as e:
                logger.warning("Не удалось загрузить чат %s: %s", path.name, e)

    # ── CRUD ──

    def create_session(self, title: str = "Новый чат") -> ChatSession:
        """Создаёт новую пустую сессию."""
        session = ChatSession(title=title)
        self._sessions[session.id] = session
        self._save(session)
        logger.info("Создана сессия: %s (%s)", session.id, title)
        return session

    def save_session(self, session: ChatSession) -> None:
        """Сохраняет сессию на диск."""
        session.updated = time.time()
        self._sessions[session.id] = session
        self._save(session)

    def _save(self, session: ChatSession) -> None:
        try:
            target = self._file_path(session.id)
            tmp = target.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(str(tmp), str(target))
        except Exception as e:
            logger.error("Ошибка сохранения чата %s: %s", session.id, e)

    def load_session(self, session_id: str) -> Optional[ChatSession]:
        """Загружает сессию по ID."""
        if session_id in self._sessions:
            return self._sessions[session_id]
        path = self._file_path(session_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                session = ChatSession.from_dict(data)
                self._sessions[session.id] = session
                return session
            except Exception as e:
                logger.error("Ошибка загрузки чата %s: %s", session_id, e)
        return None

    def delete_session(self, session_id: str) -> bool:
        """Удаляет сессию."""
        self._sessions.pop(session_id, None)
        path = self._file_path(session_id)
        try:
            if path.exists():
                path.unlink()
            logger.info("Удалена сессия: %s", session_id)
            return True
        except Exception as e:
            logger.error("Ошибка удаления чата %s: %s", session_id, e)
            return False

    def rename_session(self, session_id: str, new_title: str) -> bool:
        """Переименовывает сессию."""
        session = self._sessions.get(session_id)
        if session:
            session.title = new_title
            self._save(session)
            return True
        return False

    # ── Listing ──

    def list_sessions(self) -> List[ChatSession]:
        """Возвращает все сессии, отсортированные по дате обновления (новые первые)."""
        return sorted(
            self._sessions.values(),
            key=lambda s: s.updated,
            reverse=True,
        )

    def list_grouped(self) -> Dict[str, List[ChatSession]]:
        """Группирует сессии: Сегодня, Вчера, Ранее."""
        import datetime

        now = datetime.datetime.now()
        today_start = datetime.datetime(now.year, now.month, now.day).timestamp()
        yesterday_start = today_start - 86400

        groups: Dict[str, List[ChatSession]] = {
            "Сегодня": [],
            "Вчера": [],
            "Ранее": [],
        }
        for session in self.list_sessions():
            if session.updated >= today_start:
                groups["Сегодня"].append(session)
            elif session.updated >= yesterday_start:
                groups["Вчера"].append(session)
            else:
                groups["Ранее"].append(session)
        return groups

    def session_count(self) -> int:
        return len(self._sessions)

    # ── Message helpers ──

    def add_message_to_session(
        self,
        session_id: str,
        role: str,
        content: str,
        message_id: int = 0,
        status: str = "complete",
    ) -> None:
        """Добавляет сообщение в сессию и авто-сохраняет."""
        session = self._sessions.get(session_id)
        if not session:
            return
        msg = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
            "status": status,
            "message_id": message_id,
        }
        session.messages.append(msg)
        # Авто-заголовок из первого пользовательского сообщения
        if role == "user" and session.title == "Новый чат":
            session.title = content[:50] + ("…" if len(content) > 50 else "")
        self._save(session)

    def update_message_in_session(
        self,
        session_id: str,
        message_id: int,
        content: str,
        status: str = "complete",
    ) -> None:
        """Обновляет сообщение в сессии (для streaming)."""
        session = self._sessions.get(session_id)
        if not session:
            return
        for msg in reversed(session.messages):
            if msg.get("message_id") == message_id:
                msg["content"] = content
                msg["status"] = status
                break
        self._save(session)


# ── Singleton ──

_manager: Optional[ChatHistoryManager] = None


def get_history_manager() -> ChatHistoryManager:
    """Возвращает глобальный ChatHistoryManager."""
    global _manager
    if _manager is None:
        _manager = ChatHistoryManager()
    return _manager
