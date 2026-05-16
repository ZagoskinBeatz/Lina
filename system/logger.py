"""
Lina — Структурированное логирование и аудит действий.

Функции:
  - Логирование всех действий Lina (команды, LLM, RAG, система)
  - Ротация лог-файлов
  - Структурированный JSON-формат для анализа
  - Уровни: DEBUG, INFO, WARNING, ERROR, AUDIT
"""

import json
import time
import os
import logging
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler

from lina.config import LOGS_DIR


# ─── Ротация логов ────────────────────────────────────────────────────────────

LOG_FILE = LOGS_DIR / "lina.log"
AUDIT_FILE = LOGS_DIR / "audit.log"
MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 MB
BACKUP_COUNT = 3


class LinaLogger:
    """
    Центральный логгер Lina.

    Два лог-потока:
      1. lina.log — общий лог (DEBUG+)
      2. audit.log — аудит действий (INFO+, JSON-формат)
    """

    def __init__(self):
        self._logger = self._setup_logger()
        self._audit_logger = self._setup_audit_logger()

    def _setup_logger(self) -> logging.Logger:
        """Настройка основного логгера."""
        logger = logging.getLogger("lina")
        if logger.handlers:
            return logger  # Уже настроен

        logger.setLevel(logging.DEBUG)

        # Файловый handler с ротацией
        handler = RotatingFileHandler(
            str(LOG_FILE),
            maxBytes=MAX_LOG_SIZE,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)

        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Не пропускать DEBUG/INFO в root logger (консоль).
        # Console handler добавляется только при --verbose (runtime.py).
        logger.propagate = False

        return logger

    def _setup_audit_logger(self) -> logging.Logger:
        """Настройка логгера аудита (JSON)."""
        logger = logging.getLogger("lina.audit")
        if logger.handlers:
            return logger

        logger.setLevel(logging.INFO)

        handler = RotatingFileHandler(
            str(AUDIT_FILE),
            maxBytes=MAX_LOG_SIZE,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)

        # JSON-формат
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        return logger

    # ── Основное логирование ──

    def debug(self, msg: str, *args, **kwargs) -> None:
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self._logger.error(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        self._logger.exception(msg, *args, **kwargs)

    # ── Аудит действий ──

    def audit(
        self,
        action: str,
        details: Optional[dict] = None,
        user_command: str = "",
        success: bool = True,
    ) -> None:
        """
        Записывает аудит-запись.

        Args:
            action: Тип действия (command, llm_generate, rag_search, etc.)
            details: Дополнительные поля.
            user_command: Исходная команда пользователя.
            success: Успешно ли.
        """
        entry = {
            "timestamp": time.time(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "success": success,
            "command": user_command,
        }
        if details:
            entry["details"] = details

        try:
            self._audit_logger.info(json.dumps(entry, ensure_ascii=False))
        except Exception:
            pass

    def audit_command(self, command: str, response_len: int, elapsed: float) -> None:
        """Аудит обработки команды."""
        self.audit(
            "command",
            user_command=command,
            details={
                "response_length": response_len,
                "elapsed_seconds": round(elapsed, 3),
            },
        )

    def audit_llm(
        self,
        tier: str,
        query_len: int,
        response_len: int,
        elapsed: float,
        cached: bool = False,
    ) -> None:
        """Аудит LLM-генерации."""
        self.audit(
            "llm_generate",
            details={
                "tier": tier,
                "query_length": query_len,
                "response_length": response_len,
                "elapsed_seconds": round(elapsed, 3),
                "cached": cached,
            },
        )

    def audit_security(self, event: str, command: str = "", blocked: bool = True) -> None:
        """Аудит событий безопасности."""
        self.audit(
            "security",
            user_command=command,
            success=not blocked,
            details={"event": event, "blocked": blocked},
        )

    # ── Анализ логов ──

    def get_recent_audit(self, n: int = 20) -> list:
        """Возвращает последние N записей аудита."""
        entries = []
        try:
            if AUDIT_FILE.exists():
                with open(AUDIT_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
        except IOError:
            pass
        return entries[-n:]

    def get_stats(self) -> dict:
        """Статистика из аудит-лога."""
        entries = self.get_recent_audit(1000)
        if not entries:
            return {"total_actions": 0}

        actions = {}
        for e in entries:
            act = e.get("action", "unknown")
            actions[act] = actions.get(act, 0) + 1

        return {
            "total_actions": len(entries),
            "actions_by_type": actions,
            "log_file": str(LOG_FILE),
            "audit_file": str(AUDIT_FILE),
        }


# Глобальный экземпляр
logger = LinaLogger()
