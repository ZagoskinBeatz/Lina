"""
TelemetryEngine — анонимная оптовая телеметрия (opt-in).

Собирает ТОЛЬКО:
  - Количество запусков
  - Домены проблем (без данных)
  - Статистику стратегий (success/fail count)
  - Время выполнения (средние)
  - Ошибки модулей (без содержимого)

НЕ собирает:
  - Команды пользователя
  - Содержимое файлов
  - IP адреса
  - Hostname / username

Хранение: ~/.local/share/lina/telemetry.json (локально)
Отправка: ТОЛЬКО если пользователь включил (opt-in)

Phase: GOVERNANCE LAYER / Module 8
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class TelemetryEvent:
    """Одно телеметрическое событие."""
    event_type: str         # session_start, action_exec, strategy_applied, error_detected
    domain: str = ""
    metric: str = ""
    value: float = 0.0
    tags: Dict[str, str] = field(default_factory=dict)
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.event_type,
            "domain": self.domain,
            "metric": self.metric,
            "value": self.value,
            "tags": self.tags,
            "ts": self.timestamp,
        }


@dataclass
class TelemetryConfig:
    """Конфигурация телеметрии."""
    enabled: bool = False        # opt-in only
    session_id: str = ""
    install_id: str = ""         # anonymous persistent ID
    flush_interval: int = 3600   # seconds (1 hour)
    max_events: int = 10000
    data_dir: str = ""

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = hashlib.sha256(
                f"{time.time()}{os.getpid()}".encode()
            ).hexdigest()[:12]
        if not self.data_dir:
            share_dir = os.environ.get(
                "XDG_DATA_HOME",
                str(Path.home() / ".local" / "share")
            )
            self.data_dir = str(Path(share_dir) / "lina")


# ─── Counters ─────────────────────────────────────────────────────────────────

@dataclass
class AggregatedMetrics:
    """Агрегированные метрики за сессию."""
    session_start: float = 0.0
    session_duration: float = 0.0
    total_actions: int = 0
    successful_actions: int = 0
    failed_actions: int = 0
    domains_touched: Dict[str, int] = field(default_factory=dict)
    strategies_applied: int = 0
    strategies_success: int = 0
    escalations: int = 0
    errors: int = 0
    avg_action_time: float = 0.0
    _action_times: deque = field(default_factory=lambda: deque(maxlen=1000))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_start": self.session_start,
            "session_duration": round(self.session_duration, 1),
            "total_actions": self.total_actions,
            "successful_actions": self.successful_actions,
            "failed_actions": self.failed_actions,
            "domains_touched": self.domains_touched,
            "strategies_applied": self.strategies_applied,
            "strategies_success": self.strategies_success,
            "escalations": self.escalations,
            "errors": self.errors,
            "avg_action_time": round(self.avg_action_time, 3),
        }


# ─── TelemetryEngine ────────────────────────────────────────────────────────

class TelemetryEngine:
    """
    Анонимная телеметрия (opt-in).

    Пример:
        telemetry = get_telemetry_engine()
        telemetry.enable()
        telemetry.record_action("svc_restart", domain="service", success=True, duration=1.2)
        telemetry.record_error(domain="network")
    """

    def __init__(self, config: Optional[TelemetryConfig] = None) -> None:
        self._config = config or TelemetryConfig()
        self._events: deque = deque(maxlen=self._config.max_events)
        self._metrics = AggregatedMetrics(session_start=time.time())
        self._load_install_id()

    # ── Enable/Disable ───────────────────────────────────

    def enable(self) -> None:
        """Включить телеметрию (opt-in)."""
        self._config.enabled = True
        self._record(TelemetryEvent(event_type="telemetry_enabled"))
        logger.info("Telemetry: enabled (opt-in)")

    def disable(self) -> None:
        """Выключить телеметрию."""
        self._config.enabled = False
        logger.info("Telemetry: disabled")

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    # ── Recording ────────────────────────────────────────

    def record_action(self, action_id: str, *,
                      domain: str = "",
                      success: bool = True,
                      duration: float = 0.0) -> None:
        """Записать выполнение действия."""
        if not self._config.enabled:
            return

        self._metrics.total_actions += 1
        if success:
            self._metrics.successful_actions += 1
        else:
            self._metrics.failed_actions += 1

        if domain:
            self._metrics.domains_touched[domain] = (
                self._metrics.domains_touched.get(domain, 0) + 1
            )

        if duration > 0:
            self._metrics._action_times.append(duration)
            times = self._metrics._action_times
            self._metrics.avg_action_time = (
                sum(times) / len(times)
            )

        self._record(TelemetryEvent(
            event_type="action_exec",
            domain=domain,
            metric="duration",
            value=duration,
            tags={"status": "ok" if success else "fail"},
        ))

    def record_strategy(self, strategy_id: str, *,
                        domain: str = "",
                        success: bool = True) -> None:
        """Записать применение стратегии."""
        if not self._config.enabled:
            return

        self._metrics.strategies_applied += 1
        if success:
            self._metrics.strategies_success += 1

        self._record(TelemetryEvent(
            event_type="strategy_applied",
            domain=domain,
            tags={"status": "ok" if success else "fail"},
        ))

    def record_error(self, *, domain: str = "",
                     severity: str = "medium") -> None:
        """Записать обнаружение ошибки (только домен + severity)."""
        if not self._config.enabled:
            return

        self._metrics.errors += 1
        self._record(TelemetryEvent(
            event_type="error_detected",
            domain=domain,
            tags={"severity": severity},
        ))

    def record_escalation(self, *, domain: str = "",
                          level: str = "confirm") -> None:
        """Записать эскалацию."""
        if not self._config.enabled:
            return

        self._metrics.escalations += 1
        self._record(TelemetryEvent(
            event_type="escalation",
            domain=domain,
            tags={"level": level},
        ))

    def record_session_start(self) -> None:
        """Записать начало сессии."""
        if not self._config.enabled:
            return
        self._record(TelemetryEvent(event_type="session_start"))

    def record_session_end(self) -> None:
        """Записать конец сессии."""
        if not self._config.enabled:
            return
        self._metrics.session_duration = time.time() - self._metrics.session_start
        self._record(TelemetryEvent(event_type="session_end"))
        self._flush()

    # ── Flush ────────────────────────────────────────────

    def _flush(self) -> None:
        """Сохранить данные локально."""
        if not self._config.enabled or not self._events:
            return
        try:
            data_dir = Path(self._config.data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)
            path = data_dir / "telemetry.json"

            existing: List[Dict] = []
            if path.exists():
                try:
                    existing = json.loads(path.read_text())
                except (json.JSONDecodeError, OSError):
                    existing = []

            # Append current session summary
            session_data = {
                "install_id": self._config.install_id,
                "session_id": self._config.session_id,
                "metrics": self._metrics.to_dict(),
                "event_count": len(self._events),
                "timestamp": time.time(),
            }
            existing.append(session_data)

            # Keep last 100 sessions
            if len(existing) > 100:
                existing = existing[-100:]

            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
            os.replace(str(tmp_path), str(path))
            logger.debug("Telemetry: flushed %d events to %s", len(self._events), path)
        except Exception as e:
            logger.error("Telemetry flush error: %s", e)

    # ── Internal ─────────────────────────────────────────

    def _record(self, event: TelemetryEvent) -> None:
        """Записать событие."""
        self._events.append(event)  # auto-eviction by deque

    def _load_install_id(self) -> None:
        """Загрузить или создать install_id."""
        data_dir = Path(self._config.data_dir)
        id_path = data_dir / ".install_id"
        try:
            if id_path.exists():
                self._config.install_id = id_path.read_text().strip()
            else:
                self._config.install_id = hashlib.sha256(
                    uuid.uuid4().bytes
                ).hexdigest()[:16]
                data_dir.mkdir(parents=True, exist_ok=True)
                id_path.write_text(self._config.install_id)
        except Exception:
            self._config.install_id = "unknown"

    # ── Accessors ────────────────────────────────────────

    def get_metrics(self) -> Dict[str, Any]:
        """Текущие метрики сессии."""
        self._metrics.session_duration = time.time() - self._metrics.session_start
        return self._metrics.to_dict()

    def get_stats(self) -> Dict[str, Any]:
        """Статистика."""
        return {
            "enabled": self._config.enabled,
            "session_id": self._config.session_id,
            "events_count": len(self._events),
            "metrics": self._metrics.to_dict(),
        }


# ─── Singleton ─────────────────────────────────────────────────────────────────

_engine: Optional[TelemetryEngine] = None

def get_telemetry_engine() -> TelemetryEngine:
    """Получить единственный экземпляр TelemetryEngine."""
    global _engine
    if _engine is None:
        _engine = TelemetryEngine()
    return _engine
