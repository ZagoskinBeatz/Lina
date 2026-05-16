"""
LogIntelligenceEngine — интеллектуальный анализ системных логов.

Читает: journalctl, dmesg, /var/log/*, Xorg, NetworkManager,
PipeWire/PulseAudio, Snap/Flatpak.

Функции:
- Выявление ошибок по severity
- Группировка повторяющихся
- Определение root cause
- Выявление деградации (нарастающие ошибки)
- Structured output для ErrorClassifier

Phase: PROBLEM TERMINATOR / Module 2
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Dataclasses
# ═══════════════════════════════════════════════════════════════════

class Severity(IntEnum):
    DEBUG = 0
    INFO = 1
    NOTICE = 2
    WARNING = 3
    ERROR = 4
    CRITICAL = 5
    ALERT = 6
    EMERGENCY = 7


@dataclass
class LogEntry:
    """Одна запись лога."""
    timestamp: str = ""
    source: str = ""       # journalctl, dmesg, syslog, xorg, etc.
    unit: str = ""         # systemd unit / name
    severity: Severity = Severity.INFO
    message: str = ""
    raw_line: str = ""

    @property
    def fingerprint(self) -> str:
        """Дедупликационный хеш (без timestamp)."""
        # Убираем числа для обобщения повторяющихся ошибок
        normalized = re.sub(r"\d+", "N", self.message)
        return hashlib.md5(
            f"{self.source}:{self.unit}:{self.severity.value}:{normalized}".encode()
        ).hexdigest()[:12]


@dataclass
class LogCluster:
    """Группа похожих записей."""
    fingerprint: str
    sample: LogEntry
    count: int = 1
    first_seen: str = ""
    last_seen: str = ""
    severity: Severity = Severity.INFO

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "count": self.count,
            "severity": self.severity.name,
            "source": self.sample.source,
            "unit": self.sample.unit,
            "message": self.sample.message[:200],
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass
class LogReport:
    """Отчёт по анализу логов."""
    total_entries: int = 0
    errors: List[LogCluster] = field(default_factory=list)        # severity >= ERROR
    warnings: List[LogCluster] = field(default_factory=list)      # severity == WARNING
    critical: List[LogCluster] = field(default_factory=list)      # severity >= CRITICAL
    recurring: List[LogCluster] = field(default_factory=list)     # count > 3
    degradation: List[str] = field(default_factory=list)          # нарастающие ошибки
    root_causes: List[str] = field(default_factory=list)          # предполагаемые причины
    scan_duration: float = 0.0

    def format_summary(self) -> str:
        lines = ["═══ Анализ логов ═══"]
        lines.append(f"  Записей: {self.total_entries} | Время: {self.scan_duration:.1f}с")
        if self.critical:
            lines.append(f"  🔴 Критических: {len(self.critical)}")
            for c in self.critical[:5]:
                lines.append(f"     [{c.count}x] {c.sample.unit}: {c.sample.message[:80]}")
        if self.errors:
            lines.append(f"  ❌ Ошибок: {len(self.errors)}")
            for e in self.errors[:5]:
                lines.append(f"     [{e.count}x] {e.sample.unit}: {e.sample.message[:80]}")
        if self.warnings:
            lines.append(f"  ⚠️  Предупреждений: {len(self.warnings)}")
        if self.recurring:
            lines.append(f"  🔁 Повторяющихся: {len(self.recurring)}")
            for r in self.recurring[:3]:
                lines.append(f"     [{r.count}x] {r.sample.message[:80]}")
        if self.degradation:
            lines.append("  📉 Деградация:")
            for d in self.degradation[:3]:
                lines.append(f"     • {d}")
        if self.root_causes:
            lines.append("  🎯 Возможные причины:")
            for rc in self.root_causes[:5]:
                lines.append(f"     • {rc}")
        if not any([self.critical, self.errors, self.warnings]):
            lines.append("  ✅ Проблем не обнаружено")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Утилиты
# ═══════════════════════════════════════════════════════════════════

def _run(cmd: str, timeout: int = 15) -> str:
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, env={**os.environ, "LANG": "C.UTF-8"},
        )
        return r.stdout.strip()
    except Exception:
        return ""


# Journalctl severity mapping
_JOURNAL_PRIO = {
    "0": Severity.EMERGENCY, "emerg": Severity.EMERGENCY,
    "1": Severity.ALERT, "alert": Severity.ALERT,
    "2": Severity.CRITICAL, "crit": Severity.CRITICAL,
    "3": Severity.ERROR, "err": Severity.ERROR,
    "4": Severity.WARNING, "warning": Severity.WARNING,
    "5": Severity.NOTICE, "notice": Severity.NOTICE,
    "6": Severity.INFO, "info": Severity.INFO,
    "7": Severity.DEBUG, "debug": Severity.DEBUG,
}

# Паттерны известных root-cause
_ROOT_CAUSE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"out of memory|oom.killer|killed process", re.I),
     "OOM Killer — системе не хватает оперативной памяти"),
    (re.compile(r"segfault|segmentation fault", re.I),
     "Segfault — ошибка сегментации в процессе"),
    (re.compile(r"disk.*full|no space left", re.I),
     "Диск заполнен — нет свободного места"),
    (re.compile(r"dependency.*failed|unmet dependencies", re.I),
     "Сломанные зависимости пакетов"),
    (re.compile(r"permission denied|access denied|operation not permitted", re.I),
     "Проблема прав доступа"),
    (re.compile(r"connection refused|network.*unreachable|no route to host", re.I),
     "Сетевая ошибка — нет соединения"),
    (re.compile(r"dns.*fail|name.*resolution|could not resolve", re.I),
     "DNS не работает"),
    (re.compile(r"gpu.*hang|gpu.*reset|drm.*error", re.I),
     "GPU зависание / ошибка драйвера"),
    (re.compile(r"kernel.*panic|BUG:|Oops:", re.I),
     "Kernel panic / Bug"),
    (re.compile(r"usb.*reset|usb.*error|device descriptor", re.I),
     "Ошибка USB устройства"),
    (re.compile(r"failed.*start|failed.*mount|failed.*load", re.I),
     "Сервис не смог запуститься"),
    (re.compile(r"temperature.*critical|thermal.*shutdown|overheating", re.I),
     "Перегрев компонентов"),
    (re.compile(r"ACPI.*error|firmware.*bug", re.I),
     "ACPI / Firmware ошибка"),
    (re.compile(r"corrupt|broken.*pipe|i/o error", re.I),
     "Повреждение данных / I/O ошибка"),
    (re.compile(r"pipewire.*error|pulseaudio.*fail|alsa.*error", re.I),
     "Ошибка аудио стека"),
    (re.compile(r"bluetooth.*fail|hci.*error", re.I),
     "Ошибка Bluetooth"),
    (re.compile(r"nvidia.*error|nouveau.*error|amdgpu.*error", re.I),
     "Ошибка GPU драйвера"),
    (re.compile(r"watchdog.*timeout|hardware.*error|mce:", re.I),
     "Аппаратная ошибка"),
]


# ═══════════════════════════════════════════════════════════════════
#  LogIntelligenceEngine
# ═══════════════════════════════════════════════════════════════════

class LogIntelligenceEngine:
    """
    Интеллектуальный анализатор системных логов.

    Объединяет journalctl, dmesg, /var/log/* в единый поток,
    кластеризует ошибки, выявляет root cause и деградацию.
    """

    # Максимум строк на один источник (защита от переполнения)
    _MAX_LINES = 500

    def __init__(self) -> None:
        self._last_report: Optional[LogReport] = None

    # ─── Главный метод ────────────────────────────────────────

    def analyze(
        self,
        minutes: int = 60,
        min_severity: Severity = Severity.WARNING,
        sources: Optional[List[str]] = None,
    ) -> LogReport:
        """
        Полный анализ логов за последние N минут.

        Args:
            minutes: Глубина анализа (по умолчанию 1 час)
            min_severity: Минимальный уровень для отчёта
            sources: Список источников (None = все)
        """
        start = time.time()
        entries: List[LogEntry] = []

        # Определяем какие источники читать
        all_sources = sources or [
            "journalctl", "dmesg", "syslog", "xorg",
            "networkmanager", "pipewire", "flatpak",
        ]

        readers = {
            "journalctl": self._read_journalctl,
            "dmesg": self._read_dmesg,
            "syslog": self._read_syslog,
            "xorg": self._read_xorg,
            "networkmanager": self._read_networkmanager,
            "pipewire": self._read_pipewire,
            "flatpak": self._read_flatpak,
        }

        for src in all_sources:
            reader = readers.get(src)
            if reader:
                try:
                    new_entries = reader(minutes)
                    entries.extend(new_entries)
                except Exception as e:
                    logger.debug("Log reader %s failed: %s", src, e)

        # Фильтруем по severity
        filtered = [e for e in entries if e.severity >= min_severity]

        # Кластеризуем
        report = self._cluster_and_analyze(filtered)
        report.total_entries = len(entries)
        report.scan_duration = time.time() - start

        self._last_report = report
        return report

    def analyze_for_problem(self, problem: str, minutes: int = 30) -> LogReport:
        """Целевой анализ логов для конкретной проблемы."""
        # Анализируем все логи
        report = self.analyze(minutes=minutes, min_severity=Severity.WARNING)

        # Дополнительно ищем ключевые слова проблемы в INFO
        keywords = self._extract_keywords(problem)
        if keywords:
            all_entries = self._read_journalctl(minutes, severity="info")
            relevant = [
                e for e in all_entries
                if any(kw in e.message.lower() for kw in keywords)
            ]
            if relevant:
                extra_clusters = self._cluster_entries(relevant)
                for fp, cluster in extra_clusters.items():
                    if cluster.count > 1:
                        report.recurring.append(cluster)

        return report

    def get_recent_errors(self, minutes: int = 10) -> List[LogEntry]:
        """Быстрый список свежих ошибок."""
        entries = self._read_journalctl(minutes, severity="err")
        return entries[:20]

    @property
    def last_report(self) -> Optional[LogReport]:
        return self._last_report

    # ─── Чтение источников ────────────────────────────────────

    def _read_journalctl(
        self, minutes: int, severity: str = "warning"
    ) -> List[LogEntry]:
        """Читает journalctl с фильтрацией по severity."""
        cmd = (
            f"journalctl --no-pager --since '{minutes} min ago' "
            f"-p {severity} -o short-precise 2>/dev/null | tail -{self._MAX_LINES}"
        )
        out = _run(cmd, timeout=20)
        entries = []
        for line in out.splitlines():
            if not line.strip():
                continue
            entry = self._parse_journalctl_line(line)
            if entry:
                entries.append(entry)
        return entries

    def _read_dmesg(self, minutes: int) -> List[LogEntry]:
        """Читает dmesg (kernel ring buffer)."""
        out = _run(f"dmesg --time-format iso -l warn,err,crit,alert,emerg 2>/dev/null | tail -{self._MAX_LINES}")
        entries = []
        for line in out.splitlines():
            if not line.strip():
                continue
            entry = LogEntry(source="dmesg", raw_line=line)
            # Парсим severity из dmesg
            if any(w in line.lower() for w in ("emerg", "panic", "BUG:")):
                entry.severity = Severity.EMERGENCY
            elif any(w in line.lower() for w in ("crit", "critical")):
                entry.severity = Severity.CRITICAL
            elif any(w in line.lower() for w in ("error", "fail")):
                entry.severity = Severity.ERROR
            else:
                entry.severity = Severity.WARNING
            entry.message = re.sub(r"^\S+\s+", "", line).strip()  # remove timestamp
            entry.unit = "kernel"
            entries.append(entry)
        return entries

    def _read_syslog(self, minutes: int) -> List[LogEntry]:
        """Читает /var/log/syslog или /var/log/messages."""
        entries = []
        for path in ("/var/log/syslog", "/var/log/messages"):
            if os.path.isfile(path) and os.access(path, os.R_OK):
                out = _run(f"tail -{self._MAX_LINES} {path} 2>/dev/null")
                for line in out.splitlines():
                    if not line.strip():
                        continue
                    entry = LogEntry(
                        source="syslog", raw_line=line,
                        message=line, severity=Severity.INFO,
                    )
                    # Определяем severity из содержимого
                    lower = line.lower()
                    if any(w in lower for w in ("error", "fail", "crit")):
                        entry.severity = Severity.ERROR
                    elif "warn" in lower:
                        entry.severity = Severity.WARNING
                    entries.append(entry)
                break
        return entries

    def _read_xorg(self, minutes: int) -> List[LogEntry]:
        """Читает Xorg.0.log."""
        entries = []
        xorg_path = os.path.expanduser("~/.local/share/xorg/Xorg.0.log")
        if not os.path.isfile(xorg_path):
            xorg_path = "/var/log/Xorg.0.log"
        if not os.path.isfile(xorg_path) or not os.access(xorg_path, os.R_OK):
            return entries
        out = _run(f"grep -E '\\(EE\\)|\\(WW\\)' {xorg_path} 2>/dev/null | tail -100")
        for line in out.splitlines():
            sev = Severity.ERROR if "(EE)" in line else Severity.WARNING
            entries.append(LogEntry(
                source="xorg", message=line, severity=sev,
                unit="Xorg", raw_line=line,
            ))
        return entries

    def _read_networkmanager(self, minutes: int) -> List[LogEntry]:
        """Читает логи NetworkManager."""
        out = _run(
            f"journalctl -u NetworkManager --no-pager --since '{minutes} min ago' "
            f"-p warning -o short-precise 2>/dev/null | tail -100"
        )
        entries = []
        for line in out.splitlines():
            entry = self._parse_journalctl_line(line)
            if entry:
                entry.source = "networkmanager"
                entry.unit = "NetworkManager"
                entries.append(entry)
        return entries

    def _read_pipewire(self, minutes: int) -> List[LogEntry]:
        """Читает логи PipeWire / PulseAudio."""
        entries = []
        for unit in ("pipewire", "pipewire-pulse", "pulseaudio"):
            out = _run(
                f"journalctl --user -u {unit} --no-pager --since '{minutes} min ago' "
                f"-p warning -o short-precise 2>/dev/null | tail -50"
            )
            for line in out.splitlines():
                entry = self._parse_journalctl_line(line)
                if entry:
                    entry.source = "pipewire"
                    entries.append(entry)
        return entries

    def _read_flatpak(self, minutes: int) -> List[LogEntry]:
        """Читает логи Flatpak / Snap."""
        entries = []
        # Flatpak system log
        out = _run(
            "journalctl --no-pager --since '60 min ago' "
            "-p warning -o short-precise 2>/dev/null | grep -i flatpak | tail -50"
        )
        for line in out.splitlines():
            entry = self._parse_journalctl_line(line)
            if entry:
                entry.source = "flatpak"
                entries.append(entry)
        return entries

    # ─── Парсеры ──────────────────────────────────────────────

    _JOURNAL_RE = re.compile(
        r"^(\w{3}\s+\d+\s+[\d:.]+)\s+(\S+)\s+(\S+?)(?:\[\d+\])?:\s*(.*)"
    )

    def _parse_journalctl_line(self, line: str) -> Optional[LogEntry]:
        """Разбирает строку journalctl."""
        m = self._JOURNAL_RE.match(line)
        if not m:
            # Fallback — берём как есть
            if line.strip():
                entry = LogEntry(source="journalctl", raw_line=line, message=line)
                entry.severity = self._guess_severity(line)
                return entry
            return None

        timestamp, _host, unit, message = m.groups()
        entry = LogEntry(
            timestamp=timestamp,
            source="journalctl",
            unit=unit.rstrip(":"),
            message=message,
            raw_line=line,
        )
        entry.severity = self._guess_severity(message)
        return entry

    @staticmethod
    def _guess_severity(text: str) -> Severity:
        """Определяет severity из текста сообщения."""
        lower = text.lower()
        if any(w in lower for w in ("emerg", "panic", "kernel panic")):
            return Severity.EMERGENCY
        if any(w in lower for w in ("alert",)):
            return Severity.ALERT
        if any(w in lower for w in ("crit", "critical", "fatal")):
            return Severity.CRITICAL
        if any(w in lower for w in ("error", "err ", "fail", "segfault", "exception")):
            return Severity.ERROR
        if any(w in lower for w in ("warn", "deprecated")):
            return Severity.WARNING
        if "notice" in lower:
            return Severity.NOTICE
        return Severity.INFO

    # ─── Кластеризация и анализ ───────────────────────────────

    def _cluster_entries(self, entries: List[LogEntry]) -> Dict[str, LogCluster]:
        clusters: Dict[str, LogCluster] = {}
        for e in entries:
            fp = e.fingerprint
            if fp in clusters:
                clusters[fp].count += 1
                clusters[fp].last_seen = e.timestamp
                if e.severity > clusters[fp].severity:
                    clusters[fp].severity = e.severity
            else:
                clusters[fp] = LogCluster(
                    fingerprint=fp, sample=e, count=1,
                    first_seen=e.timestamp, last_seen=e.timestamp,
                    severity=e.severity,
                )
        return clusters

    def _cluster_and_analyze(self, entries: List[LogEntry]) -> LogReport:
        """Кластеризует записи и строит отчёт."""
        report = LogReport()
        clusters = self._cluster_entries(entries)

        # Сортируем по severity desc, count desc
        sorted_clusters = sorted(
            clusters.values(),
            key=lambda c: (c.severity.value, c.count),
            reverse=True,
        )

        for c in sorted_clusters:
            if c.severity >= Severity.CRITICAL:
                report.critical.append(c)
            if c.severity >= Severity.ERROR:
                report.errors.append(c)
            elif c.severity == Severity.WARNING:
                report.warnings.append(c)
            if c.count >= 3:
                report.recurring.append(c)

        # Root cause analysis
        seen_causes = set()
        for e in entries:
            for pattern, cause in _ROOT_CAUSE_PATTERNS:
                if pattern.search(e.message) and cause not in seen_causes:
                    report.root_causes.append(cause)
                    seen_causes.add(cause)

        # Degradation detection — нарастание ошибок от одного unit
        unit_counts = Counter(e.unit for e in entries if e.severity >= Severity.ERROR)
        for unit, cnt in unit_counts.most_common(5):
            if cnt >= 5:
                report.degradation.append(
                    f"{unit}: {cnt} ошибок — возможная деградация"
                )

        return report

    @staticmethod
    def _extract_keywords(problem: str) -> List[str]:
        """Извлекает ключевые слова из описания проблемы."""
        # Карта: русское описание → английские ключевые слова для логов
        keyword_map = {
            "интернет": ["network", "connection", "dns", "dhcp", "NetworkManager"],
            "сеть": ["network", "interface", "link", "route"],
            "звук": ["audio", "pipewire", "pulseaudio", "alsa", "snd"],
            "видео": ["gpu", "drm", "display", "nvidia", "amdgpu", "mesa"],
            "диск": ["disk", "io", "mount", "filesystem", "ext4", "btrfs"],
            "память": ["memory", "oom", "swap"],
            "bluetooth": ["bluetooth", "hci", "bluez"],
            "загрузк": ["boot", "grub", "initramfs", "systemd"],
            "обновлени": ["update", "upgrade", "pacman", "apt"],
            "пакет": ["package", "dependency", "dpkg", "pacman"],
            "принтер": ["cups", "printer", "print"],
            "usb": ["usb", "device"],
        }
        result = []
        lower = problem.lower()
        for ru_key, en_words in keyword_map.items():
            if ru_key in lower:
                result.extend(en_words)
        return [w.lower() for w in result] if result else []


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_engine: Optional[LogIntelligenceEngine] = None


def get_log_engine() -> LogIntelligenceEngine:
    global _engine
    if _engine is None:
        _engine = LogIntelligenceEngine()
    return _engine
