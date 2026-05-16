"""
PredictiveMonitor — предиктивный мониторинг и раннее предупреждение.

.. warning:: EXPERIMENTAL
   Предиктивная аналитика требует достаточного количества исторических данных.
   Точность прогнозов не валидирована в production-среде.

Расширяет system/monitor.py (SystemMonitor) предиктивной аналитикой:
  - Moving average + trend detection
  - Threshold deviation alerting
  - Historical anomaly detection
  - Предсказание: переполнение диска, перегрев, swap storm, error growth
  - Автоматический запуск DiagnosePipeline при обнаружении угрозы

Работает поверх SystemStateScanner (scan каждые 30-60с).

Phase: SYSTEM OVERLORD / Module 3
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Alert Level
# ═══════════════════════════════════════════════════════════════════

class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class PredictiveAlert:
    """Предупреждение от PredictiveMonitor."""
    level: AlertLevel
    subsystem: str
    metric: str
    current_value: float
    predicted_value: float
    threshold: float
    time_to_breach: float       # секунд до пробивания порога (0 = уже)
    message: str
    timestamp: float = 0.0
    trend: str = ""             # "rising", "falling", "stable"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "subsystem": self.subsystem,
            "metric": self.metric,
            "current": round(self.current_value, 2),
            "predicted": round(self.predicted_value, 2),
            "threshold": round(self.threshold, 2),
            "ttb_sec": round(self.time_to_breach),
            "trend": self.trend,
            "message": self.message,
        }


# ═══════════════════════════════════════════════════════════════════
#  Metric Series — скользящий буфер метрики
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MetricSample:
    value: float
    timestamp: float


class MetricSeries:
    """Скользящий буфер для одной метрики с трендом."""

    def __init__(self, name: str, max_samples: int = 120):
        self.name = name
        self._data: deque[MetricSample] = deque(maxlen=max_samples)

    def add(self, value: float, ts: Optional[float] = None) -> None:
        self._data.append(MetricSample(value, ts or time.time()))

    @property
    def count(self) -> int:
        return len(self._data)

    @property
    def last(self) -> Optional[float]:
        return self._data[-1].value if self._data else None

    def moving_average(self, window: int = 10) -> float:
        if not self._data:
            return 0.0
        samples = list(self._data)[-window:]
        return sum(s.value for s in samples) / len(samples)

    def trend_slope(self) -> float:
        """Линейный тренд (наклон). >0 = растёт, <0 = падает."""
        if len(self._data) < 3:
            return 0.0
        samples = list(self._data)
        n = len(samples)
        t0 = samples[0].timestamp
        xs = [s.timestamp - t0 for s in samples]
        ys = [s.value for s in samples]
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den = sum((x - mean_x) ** 2 for x in xs)
        if den == 0:
            return 0.0
        return num / den

    def predict(self, seconds_ahead: float = 300) -> float:
        """Предсказать значение через seconds_ahead секунд."""
        if not self._data:
            return 0.0
        slope = self.trend_slope()
        current = self._data[-1].value
        return current + slope * seconds_ahead

    def time_to_threshold(self, threshold: float) -> float:
        """Секунд до пробивания threshold. -1 если тренд ведёт от порога."""
        if not self._data:
            return -1
        current = self._data[-1].value
        if current >= threshold:
            return 0  # уже пробит
        slope = self.trend_slope()
        if slope <= 0:
            return -1  # тренд вниз или flat
        return (threshold - current) / slope

    def deviation_from_mean(self) -> float:
        """Отклонение последнего значения от средней (в σ)."""
        if len(self._data) < 5:
            return 0.0
        values = [s.value for s in self._data]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = variance ** 0.5
        if std < 0.01:
            return 0.0
        return (values[-1] - mean) / std

    def trend_direction(self) -> str:
        slope = self.trend_slope()
        if abs(slope) < 0.001:
            return "stable"
        return "rising" if slope > 0 else "falling"


# ═══════════════════════════════════════════════════════════════════
#  Thresholds — пороги предупреждений
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ThresholdConfig:
    metric: str
    subsystem: str
    warning: float
    critical: float
    emergency: float
    predict_window: float = 300   # секунд вперёд для предсказания
    description: str = ""


_DEFAULT_THRESHOLDS: List[ThresholdConfig] = [
    ThresholdConfig("cpu_load", "cpu", 0.8, 0.95, 1.0,
                    description="CPU load (0-1 normalized)"),
    ThresholdConfig("ram_percent", "ram", 80, 95, 99,
                    description="RAM usage %"),
    ThresholdConfig("swap_percent", "swap", 30, 70, 90,
                    description="Swap usage %"),
    ThresholdConfig("disk_percent", "disk", 80, 90, 95,
                    description="Root disk usage %"),
    ThresholdConfig("temperature", "thermal", 75, 90, 100,
                    description="Max temperature °C"),
    ThresholdConfig("error_rate", "logs", 5, 15, 50,
                    predict_window=600,
                    description="Errors per minute in journal"),
    ThresholdConfig("failed_services", "services", 1, 3, 5,
                    description="Number of failed systemd units"),
]


# ═══════════════════════════════════════════════════════════════════
#  PredictiveMonitor
# ═══════════════════════════════════════════════════════════════════

class PredictiveMonitor:
    """Предиктивный мониторинг с trend detection и alerting.

    Поток:
      1. collect() — собрать текущие метрики (из SystemStateScanner)
      2. analyze() — проверить текущие + предиктивные пороги
      3. alerts   — список активных предупреждений

    Integration:
      - Вызывается периодически (30-60с) или по событию
      - При CRITICAL/EMERGENCY alert → trigger DiagnosePipeline
    """

    def __init__(
        self,
        thresholds: Optional[List[ThresholdConfig]] = None,
        max_alert_history: int = 200,
    ):
        self._thresholds = thresholds or list(_DEFAULT_THRESHOLDS)
        self._series: Dict[str, MetricSeries] = {}
        self._active_alerts: List[PredictiveAlert] = []
        self._alert_history: List[PredictiveAlert] = []
        self._max_history = max_alert_history
        self._callbacks: List[Callable[[PredictiveAlert], None]] = []
        self._last_collect: float = 0
        self._collect_count: int = 0

    # ─── Callbacks ─────────────────────────────────────────────

    def on_alert(self, callback: Callable[[PredictiveAlert], None]) -> None:
        """Регистрация callback для алертов."""
        self._callbacks.append(callback)

    # ─── Collect — собрать метрики из Scanner ──────────────────

    def collect(self) -> None:
        """Собрать текущие метрики из SystemStateScanner."""
        try:
            from lina.diagnostics.scanner import get_scanner
            scanner = get_scanner()
            state = scanner.scan()
            now = time.time()

            # CPU load (normalized)
            if state.cpu.details:
                load = state.cpu.details.get("load_1m", 0)
                cores = state.cpu.details.get("cores", 1)
                self._record("cpu_load", load / max(cores, 1), now)

            # RAM
            if state.ram.details:
                self._record("ram_percent", state.ram.details.get("percent", 0), now)

            # Swap
            if state.ram.details:
                swap_total = state.ram.details.get("swap_total_mb", 0)
                swap_used = state.ram.details.get("swap_used_mb", 0)
                if swap_total > 0:
                    self._record("swap_percent", (swap_used / swap_total) * 100, now)

            # Disk
            if state.disk.details:
                self._record("disk_percent", state.disk.details.get("root_percent", 0), now)

            # Temperature
            if state.temperatures.details:
                temps = state.temperatures.details.get("readings", {})
                if temps:
                    max_temp = max(temps.values()) if isinstance(temps, dict) else 0
                    self._record("temperature", max_temp, now)

            # Failed services
            if state.services.details:
                failed = state.services.details.get("failed_count", 0)
                self._record("failed_services", failed, now)

            # Error rate (from log analysis)
            self._collect_error_rate(now)

            self._last_collect = now
            self._collect_count += 1

        except Exception as e:
            logger.error("PredictiveMonitor.collect failed: %s", e)

    def _collect_error_rate(self, now: float) -> None:
        """Подсчёт error rate из журнала (за последнюю минуту)."""
        try:
            import subprocess
            r = subprocess.run(
                "journalctl --since '1 min ago' -p err --no-pager -q 2>/dev/null | wc -l",
                shell=True, capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                count = int(r.stdout.strip() or "0")
                self._record("error_rate", count, now)
        except Exception:
            pass

    def _record(self, metric: str, value: float, ts: float) -> None:
        if metric not in self._series:
            self._series[metric] = MetricSeries(metric)
        self._series[metric].add(value, ts)

    # ─── Analyze — проверить пороги и тренды ──────────────────

    def analyze(self) -> List[PredictiveAlert]:
        """Анализ всех метрик → список алертов."""
        new_alerts = []

        for threshold in self._thresholds:
            series = self._series.get(threshold.metric)
            if series is None or series.count < 2:
                continue

            current = series.last
            if current is None:
                continue

            predicted = series.predict(threshold.predict_window)
            trend = series.trend_direction()
            ttb = series.time_to_threshold(threshold.critical)

            # 1. Текущее значение уже превышает порог
            if current >= threshold.emergency:
                alert = PredictiveAlert(
                    level=AlertLevel.EMERGENCY,
                    subsystem=threshold.subsystem,
                    metric=threshold.metric,
                    current_value=current,
                    predicted_value=predicted,
                    threshold=threshold.emergency,
                    time_to_breach=0,
                    trend=trend,
                    message=f"🚨 EMERGENCY: {threshold.metric}={current:.1f} "
                            f"≥ {threshold.emergency} ({threshold.description})",
                )
                new_alerts.append(alert)
            elif current >= threshold.critical:
                alert = PredictiveAlert(
                    level=AlertLevel.CRITICAL,
                    subsystem=threshold.subsystem,
                    metric=threshold.metric,
                    current_value=current,
                    predicted_value=predicted,
                    threshold=threshold.critical,
                    time_to_breach=0,
                    trend=trend,
                    message=f"🔴 CRITICAL: {threshold.metric}={current:.1f} "
                            f"≥ {threshold.critical} ({threshold.description})",
                )
                new_alerts.append(alert)
            elif current >= threshold.warning:
                alert = PredictiveAlert(
                    level=AlertLevel.WARNING,
                    subsystem=threshold.subsystem,
                    metric=threshold.metric,
                    current_value=current,
                    predicted_value=predicted,
                    threshold=threshold.warning,
                    time_to_breach=ttb,
                    trend=trend,
                    message=f"⚠️ WARNING: {threshold.metric}={current:.1f} "
                            f"≥ {threshold.warning} ({threshold.description})",
                )
                new_alerts.append(alert)

            # 2. Предиктивный алерт — тренд ведёт к порогу
            elif trend == "rising" and predicted >= threshold.critical:
                if 0 < ttb < threshold.predict_window:
                    alert = PredictiveAlert(
                        level=AlertLevel.WARNING,
                        subsystem=threshold.subsystem,
                        metric=threshold.metric,
                        current_value=current,
                        predicted_value=predicted,
                        threshold=threshold.critical,
                        time_to_breach=ttb,
                        trend=trend,
                        message=f"📈 PREDICTION: {threshold.metric} will reach "
                                f"{threshold.critical} in ~{ttb:.0f}s "
                                f"(current={current:.1f}, trend={trend})",
                    )
                    new_alerts.append(alert)

            # 3. Anomaly detection — отклонение > 3σ
            deviation = series.deviation_from_mean()
            if abs(deviation) > 3.0 and series.count >= 10:
                alert = PredictiveAlert(
                    level=AlertLevel.WARNING,
                    subsystem=threshold.subsystem,
                    metric=threshold.metric,
                    current_value=current,
                    predicted_value=predicted,
                    threshold=threshold.warning,
                    time_to_breach=-1,
                    trend=trend,
                    message=f"📊 ANOMALY: {threshold.metric}={current:.1f}, "
                            f"deviation={deviation:.1f}σ from mean",
                )
                new_alerts.append(alert)

        # Fire callbacks
        self._active_alerts = new_alerts
        for alert in new_alerts:
            self._alert_history.append(alert)
            for cb in self._callbacks:
                try:
                    cb(alert)
                except Exception as e:
                    logger.error("Alert callback error: %s", e)

        if len(self._alert_history) > self._max_history:
            self._alert_history = self._alert_history[-self._max_history:]

        return new_alerts

    # ─── Collect + Analyze в одном вызове ─────────────────────

    def tick(self) -> List[PredictiveAlert]:
        """Один цикл: collect → analyze → alerts."""
        self.collect()
        return self.analyze()

    # ─── API ──────────────────────────────────────────────────

    def get_active_alerts(self) -> List[PredictiveAlert]:
        return list(self._active_alerts)

    def get_alert_history(self, limit: int = 30) -> List[PredictiveAlert]:
        return self._alert_history[-limit:]

    def get_series_summary(self) -> Dict[str, Dict[str, Any]]:
        """Сводка всех метрик."""
        result = {}
        for name, series in self._series.items():
            result[name] = {
                "count": series.count,
                "last": series.last,
                "avg": round(series.moving_average(), 2),
                "trend": series.trend_direction(),
                "slope": round(series.trend_slope(), 6),
            }
        return result

    def format_report(self) -> str:
        """Текстовый отчёт состояния мониторинга."""
        lines = ["═══ Predictive Monitor ═══"]
        lines.append(f"  Samples collected: {self._collect_count}")
        lines.append(f"  Active alerts: {len(self._active_alerts)}")
        lines.append("")

        for name, info in self.get_series_summary().items():
            icon = {"rising": "📈", "falling": "📉", "stable": "➡️"}.get(info["trend"], "?")
            lines.append(
                f"  {icon} {name:20s} last={info['last']:.1f}  "
                f"avg={info['avg']:.1f}  trend={info['trend']}"
            )

        if self._active_alerts:
            lines.append("")
            lines.append("  Alerts:")
            for a in self._active_alerts:
                lines.append(f"    {a.message}")

        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "collect_count": self._collect_count,
            "series_count": len(self._series),
            "active_alerts": len(self._active_alerts),
            "total_alerts": len(self._alert_history),
            "last_collect": self._last_collect,
        }


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_monitor: Optional[PredictiveMonitor] = None


def get_predictor() -> PredictiveMonitor:
    global _monitor
    if _monitor is None:
        _monitor = PredictiveMonitor()
    return _monitor
