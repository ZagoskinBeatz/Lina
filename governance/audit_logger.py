"""
AuditLogger — централизованный аудит-лог для governance.

Записывает каждый Intent lifecycle event в структурированный JSONL файл:
  ~/.local/share/lina/audit.jsonl

Каждая запись содержит:
  - timestamp (ISO 8601)
  - event_type (intent_created, access_checked, policy_checked, executed, denied, confirm_requested)
  - intent_id
  - intent_type, domain, action, source
  - decision (allow, deny, confirm, rate_limited)
  - duration_ms
  - metadata (без PII)

НЕ записывает:
  - user_text (может содержать PII)
  - params (может содержать чувствительные данные)
  - IP адреса, пользовательские данные

Phase: GOVERNANCE LAYER / Audit
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Audit Event Types ───────────────────────────────────────────────────────

class AuditEvent:
    """Типы аудит-событий."""
    INTENT_CREATED = "intent_created"
    ACCESS_CHECKED = "access_checked"
    POLICY_CHECKED = "policy_checked"
    EXECUTED = "executed"
    DENIED = "denied"
    CONFIRM_REQUESTED = "confirm_requested"
    CONFIRM_RESOLVED = "confirm_resolved"
    ESCALATED = "escalated"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    HEALTH_CHECK = "health_check"


# ─── Audit Record ────────────────────────────────────────────────────────────

@dataclass
class AuditRecord:
    """Одна запись аудита."""
    event_type: str
    intent_id: str = ""
    intent_type: str = ""
    domain: str = ""
    action: str = ""
    source: str = ""
    decision: str = ""
    access_level: str = ""
    duration_ms: float = 0.0
    success: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Удаляем пустые поля для компактности
        return {k: v for k, v in d.items() if v or k in ("success",)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


# ─── AuditLogger ─────────────────────────────────────────────────────────────

class AuditLogger:
    """
    Централизованный аудит-лог. Записывает JSONL в файл.

    Все governance компоненты (IntentRouter, PolicyEngine, AccessResolver)
    делегируют аудит сюда. Единое место для всех записей.

    Пример:
        logger = get_audit_logger()
        logger.log_intent(intent, "intent_created")
        logger.log_decision(intent_id, "allow", access_level="user")
        logger.log_execution(intent_id, success=True, duration_ms=42.5)
    """

    def __init__(self, audit_path: Optional[str] = None,
                 max_memory: int = 1000,
                 max_file_size: int = 10 * 1024 * 1024) -> None:
        self._path = Path(audit_path) if audit_path else self._default_path()
        self._memory: deque = deque(maxlen=max_memory)
        self._max_file_size = max_file_size  # Phase 5: 10MB default
        self._total_written = 0
        self._file_handle = None
        self._enabled = True
        self._lock_enabled = False  # Phase 5: prevents disable after lock

    @staticmethod
    def _default_path() -> Path:
        """Default: ~/.local/share/lina/audit.jsonl."""
        data_dir = os.environ.get(
            "XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        return Path(data_dir) / "lina" / "audit.jsonl"

    # ── Core Log Methods ─────────────────────────────────

    def log(self, record: AuditRecord) -> None:
        """Записать аудит-запись."""
        if not self._enabled:
            return

        self._memory.append(record)

        self._write_to_file(record)
        self._total_written += 1

    def log_intent(self, intent: Any, event_type: str = AuditEvent.INTENT_CREATED) -> None:
        """Записать Intent event (без PII — без user_text)."""
        self.log(AuditRecord(
            event_type=event_type,
            intent_id=getattr(intent, "id", ""),
            intent_type=getattr(intent, "type", ""),
            domain=getattr(intent, "domain", ""),
            action=getattr(intent, "action", ""),
            source=getattr(intent, "source", ""),
        ))

    def log_decision(self, intent_id: str, decision: str, *,
                     access_level: str = "",
                     domain: str = "",
                     action: str = "",
                     source: str = "",
                     event_type: str = AuditEvent.ACCESS_CHECKED,
                     metadata: Optional[Dict] = None) -> None:
        """Записать решение (access check, policy check)."""
        self.log(AuditRecord(
            event_type=event_type,
            intent_id=intent_id,
            decision=decision,
            access_level=access_level,
            domain=domain,
            action=action,
            source=source,
            metadata=metadata or {},
        ))

    def log_execution(self, intent_id: str, *,
                      success: bool = True,
                      duration_ms: float = 0.0,
                      action: str = "",
                      domain: str = "",
                      metadata: Optional[Dict] = None) -> None:
        """Записать результат выполнения."""
        self.log(AuditRecord(
            event_type=AuditEvent.EXECUTED if success else AuditEvent.FAILED,
            intent_id=intent_id,
            action=action,
            domain=domain,
            duration_ms=duration_ms,
            success=success,
            metadata=metadata or {},
        ))

    def log_session(self, event_type: str, metadata: Optional[Dict] = None) -> None:
        """Записать session event."""
        self.log(AuditRecord(
            event_type=event_type,
            metadata=metadata or {},
        ))

    # ── File I/O ─────────────────────────────────────────

    def _write_to_file(self, record: AuditRecord) -> None:
        """Append JSONL line to file. Phase 5: auto-rotate on size limit."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Phase 5: Rotate if file exceeds max size
            if self._path.exists():
                try:
                    size = self._path.stat().st_size
                    if size > self._max_file_size:
                        self._rotate()
                except OSError:
                    pass
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(record.to_json() + "\n")
        except Exception as e:
            logger.debug("AuditLogger: write failed: %s", e)

    def _rotate(self) -> None:
        """Phase 5: Rotate audit log — rename current to .1, keep max 3 rotations."""
        try:
            for i in range(3, 0, -1):
                src = self._path.with_suffix(f".jsonl.{i}")
                dst = self._path.with_suffix(f".jsonl.{i + 1}")
                if src.exists():
                    if i == 3:
                        src.unlink()  # Remove oldest
                    else:
                        src.rename(dst)
            if self._path.exists():
                self._path.rename(self._path.with_suffix(".jsonl.1"))
        except Exception as e:
            logger.debug("AuditLogger: rotate failed: %s", e)

    def flush(self) -> None:
        """Force flush (no-op for append mode, here for API compat)."""
        pass

    # ── Query ────────────────────────────────────────────

    def get_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Последние записи из памяти."""
        return [r.to_dict() for r in list(self._memory)[-limit:]]

    def get_stats(self) -> Dict[str, Any]:
        """Статистика аудита."""
        events: Dict[str, int] = {}
        for r in self._memory:
            events[r.event_type] = events.get(r.event_type, 0) + 1
        return {
            "total_written": self._total_written,
            "in_memory": len(self._memory),
            "enabled": self._enabled,
            "path": str(self._path),
            "events": events,
        }

    def set_enabled(self, enabled: bool) -> None:
        """Включить/выключить аудит.
        Phase 5: Once lock_enabled() is called, audit cannot be disabled.
        """
        if self._lock_enabled and not enabled:
            logger.warning("AuditLogger: attempt to disable locked audit log")
            # Log the attempt itself
            self.log(AuditRecord(
                event_type="security_violation",
                metadata={"attempt": "disable_audit", "blocked": True},
            ))
            return
        self._enabled = enabled

    def lock_enabled(self) -> None:
        """Phase 5: Lock audit — cannot be disabled after this call.

        INVARIANT: Once locked, audit CANNOT be disabled for the lifetime
        of this process. Any attempt to disable logs a security_violation event.
        This ensures accountability even if attacker gains code execution.
        """
        self._lock_enabled = True
        self._enabled = True
        logger.info("AuditLogger: audit locked — cannot be disabled")

    @property
    def locked(self) -> bool:
        """Phase 5: Whether audit is locked (cannot be disabled)."""
        return self._lock_enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def path(self) -> Path:
        return self._path


# ─── Singleton ────────────────────────────────────────────────────────────────

_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Получить единственный AuditLogger."""
    global _logger
    if _logger is None:
        _logger = AuditLogger()
    return _logger
