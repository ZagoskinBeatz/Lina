"""
SignatureCollector — сбор и нормализация сигнатур ошибок.

ErrorSignature содержит:
  timestamp, stage, command, exit_code, stdout, stderr,
  log_snippet, system_state, domain, severity, tags

Сигнатуры используются для:
  1. Поиска в Knowledge Base
  2. Fuzzy-matching с известными паттернами
  3. Обучения (запись результатов)
  4. Диагностики (передача в Decision Graph)

Phase: GOVERNANCE LAYER / Module 5
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ─── Signature Dataclass ─────────────────────────────────────────────────────

@dataclass
class ErrorSignature:
    """Сигнатура ошибки — структурированное описание проблемы."""
    timestamp: float = 0.0
    stage: str = ""               # boot, runtime, installer, diagnosing
    command: str = ""             # команда, вызвавшая ошибку
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    log_snippet: str = ""         # фрагмент лога
    system_state: Dict[str, str] = field(default_factory=dict)
    domain: str = ""              # network, audio, package, boot, ...
    severity: str = "medium"      # low, medium, high, critical
    tags: List[str] = field(default_factory=list)
    patterns_matched: List[str] = field(default_factory=list)
    fingerprint: str = ""         # SHA256 хеш нормализованной сигнатуры

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.fingerprint:
            self.fingerprint = self._compute_fingerprint()

    def _compute_fingerprint(self) -> str:
        """Вычислить уникальный fingerprint сигнатуры."""
        norm = self._normalize()
        return hashlib.sha256(norm.encode()).hexdigest()[:16]

    def _normalize(self) -> str:
        """Нормализовать сигнатуру для сравнения."""
        parts = [
            self.domain,
            self.stage,
            _normalize_text(self.stderr or self.stdout),
            ",".join(sorted(self.tags)),
        ]
        return "|".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        """Краткое описание сигнатуры."""
        cmd = self.command[:50] if self.command else "—"
        err = (self.stderr or self.stdout)[:80]
        return f"[{self.domain}] cmd='{cmd}' rc={self.exit_code} err='{err}'"


# ─── Known error patterns ───────────────────────────────────────────────────

@dataclass
class ErrorPattern:
    """Известный паттерн ошибки."""
    id: str
    pattern: str          # regex
    domain: str
    severity: str = "medium"
    tags: List[str] = field(default_factory=list)
    description: str = ""
    description_ru: str = ""

_BUILTIN_PATTERNS: List[ErrorPattern] = [
    # ─── Network ──────────────────────────────────
    ErrorPattern(
        id="net_dns_fail", pattern=r"(SERVFAIL|NXDOMAIN|Name.+not.+resolved|no.+servers.+reached)",
        domain="network", severity="medium",
        tags=["dns", "resolution", "failure"],
        description_ru="Ошибка DNS разрешения",
    ),
    ErrorPattern(
        id="net_timeout", pattern=r"(Connection timed out|timed?\s*out|ETIMEDOUT)",
        domain="network", severity="medium",
        tags=["network", "timeout", "connection"],
        description_ru="Таймаут соединения",
    ),
    ErrorPattern(
        id="net_refused", pattern=r"(Connection refused|ECONNREFUSED)",
        domain="network", severity="low",
        tags=["network", "refused", "port"],
        description_ru="Соединение отклонено",
    ),
    ErrorPattern(
        id="net_no_route", pattern=r"(No route to host|Network is unreachable|ENETUNREACH)",
        domain="network", severity="high",
        tags=["network", "routing", "unreachable"],
        description_ru="Нет маршрута к хосту",
    ),
    ErrorPattern(
        id="net_wifi_fail", pattern=r"(No suitable network|WiFi.*disconnect|Wireless.*disabled)",
        domain="network", severity="medium",
        tags=["wifi", "disconnect", "wireless"],
        description_ru="Проблема WiFi",
    ),
    # ─── Package ──────────────────────────────────
    ErrorPattern(
        id="pkg_conflict", pattern=r"(conflicting files|file exists in filesystem|conflict)",
        domain="package", severity="medium",
        tags=["package", "conflict", "filesystem"],
        description_ru="Конфликт файлов пакетов",
    ),
    ErrorPattern(
        id="pkg_dep_fail", pattern=r"(dependency|depends on|required by|unresolvable)",
        domain="package", severity="medium",
        tags=["package", "dependency", "unresolvable"],
        description_ru="Проблема зависимостей",
    ),
    ErrorPattern(
        id="pkg_gpg_fail", pattern=r"(GPGME error|invalid.*signature|key.*unknown|marginal trust|keyring)",
        domain="package", severity="high",
        tags=["package", "gpg", "signature", "keyring"],
        description_ru="Ошибка подписи пакета",
    ),
    ErrorPattern(
        id="pkg_db_locked", pattern=r"(database is locked|lock file|unable to lock)",
        domain="package", severity="low",
        tags=["package", "lock", "database"],
        description_ru="БД пакетного менеджера заблокирована",
    ),
    # ─── Audio ────────────────────────────────────
    ErrorPattern(
        id="audio_no_sink", pattern=r"(no sinks|Sink.*not found|PA.*connection refused|pactl.*failure)",
        domain="audio", severity="medium",
        tags=["audio", "sink", "pulseaudio", "pipewire"],
        description_ru="Аудио выход не найден",
    ),
    ErrorPattern(
        id="audio_permission", pattern=r"(audio group|access denied.*audio|Permission.*snd)",
        domain="audio", severity="low",
        tags=["audio", "permission", "group"],
        description_ru="Нет доступа к аудио",
    ),
    # ─── Disk ─────────────────────────────────────
    ErrorPattern(
        id="disk_full", pattern=r"(No space left|disk full|ENOSPC|100%.*use)",
        domain="disk", severity="high",
        tags=["disk", "full", "space", "enospc"],
        description_ru="Диск переполнен",
    ),
    ErrorPattern(
        id="disk_readonly", pattern=r"(Read-only file system|EROFS|mount.*ro\b)",
        domain="disk", severity="high",
        tags=["disk", "readonly", "filesystem"],
        description_ru="Файловая система только для чтения",
    ),
    ErrorPattern(
        id="disk_io_error", pattern=r"(I/O error|input/output error|EIO|blk_update_request)",
        domain="disk", severity="critical",
        tags=["disk", "io", "hardware", "failure"],
        description_ru="Ошибка ввода-вывода диска",
    ),
    # ─── Boot ─────────────────────────────────────
    ErrorPattern(
        id="boot_grub_fail", pattern=r"(grub-install.*error|grub.*not found|no.*bootloader)",
        domain="boot", severity="critical",
        tags=["boot", "grub", "bootloader", "install"],
        description_ru="Ошибка установки GRUB",
    ),
    ErrorPattern(
        id="boot_initramfs", pattern=r"(mkinitcpio.*error|dracut.*fail|initramfs.*missing)",
        domain="boot", severity="high",
        tags=["boot", "initramfs", "mkinitcpio"],
        description_ru="Ошибка initramfs",
    ),
    # ─── GPU / Display ────────────────────────────
    ErrorPattern(
        id="gpu_nvidia_fail", pattern=r"(NVIDIA.*error|nvidia-smi.*fail|NVRM|nouveau.*error)",
        domain="display", severity="medium",
        tags=["gpu", "nvidia", "driver"],
        description_ru="Ошибка драйвера NVIDIA",
    ),
    ErrorPattern(
        id="display_no_output", pattern=r"(no displays found|no screens|Cannot open display|Xlib.*connection)",
        domain="display", severity="high",
        tags=["display", "xorg", "wayland", "screen"],
        description_ru="Экран не найден",
    ),
    # ─── Service ──────────────────────────────────
    ErrorPattern(
        id="svc_failed", pattern=r"(service.*failed|Active: failed|status=1/FAILURE)",
        domain="service", severity="medium",
        tags=["service", "systemd", "failed"],
        description_ru="Сервис в ошибке",
    ),
    ErrorPattern(
        id="svc_timeout", pattern=r"(start.*timed out|TimeoutStartSec|Job.*timed out)",
        domain="service", severity="medium",
        tags=["service", "timeout", "systemd"],
        description_ru="Таймаут запуска сервиса",
    ),
    # ─── Security ─────────────────────────────────
    ErrorPattern(
        id="sec_permission", pattern=r"(Permission denied|EACCES|Operation not permitted|EPERM)",
        domain="security", severity="low",
        tags=["permission", "denied", "access"],
        description_ru="Отказано в доступе",
    ),
]

# Pre-compile patterns
_COMPILED_PATTERNS = [
    (ep, re.compile(ep.pattern, re.IGNORECASE))
    for ep in _BUILTIN_PATTERNS
]


# ─── Text normalization ──────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Нормализовать текст ошибки для сравнения."""
    text = re.sub(r"\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}", "<TS>", text)
    text = re.sub(r"/tmp/[a-zA-Z0-9._-]+", "<TMPFILE>", text)
    text = re.sub(r"\b\d+\.\d+\.\d+\.\d+\b", "<IP>", text)
    text = re.sub(r"\b[0-9a-f]{8,}\b", "<HEX>", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()[:500]


# ─── SignatureCollector ──────────────────────────────────────────────────────

class SignatureCollector:
    """
    Собирает и классифицирует сигнатуры ошибок.

    Пример:
        collector = get_signature_collector()
        sig = collector.collect(
            command="systemctl restart NetworkManager",
            exit_code=1, stderr="Failed to restart...",
        )
        # sig.domain == "service"
        # sig.tags == ["service", "systemd", "failed"]
    """

    def __init__(self) -> None:
        self._signatures: List[ErrorSignature] = []
        self._max_signatures = 5000
        self._custom_patterns: List[ErrorPattern] = []

    def collect(self, *,
                command: str = "",
                exit_code: int = -1,
                stdout: str = "",
                stderr: str = "",
                log_snippet: str = "",
                stage: str = "runtime",
                system_state: Optional[Dict[str, str]] = None,
                domain_hint: str = "") -> ErrorSignature:
        """
        Собрать и классифицировать сигнатуру ошибки.

        Автоматически:
          - Определяет домен по паттернам
          - Присваивает теги
          - Определяет severity
          - Вычисляет fingerprint
        """
        text = f"{stdout}\n{stderr}\n{log_snippet}"

        # Match patterns
        matched_patterns: List[ErrorPattern] = []
        for ep, compiled in _COMPILED_PATTERNS:
            if compiled.search(text):
                matched_patterns.append(ep)

        # Custom patterns
        for ep in self._custom_patterns:
            if re.search(ep.pattern, text, re.IGNORECASE):
                matched_patterns.append(ep)

        # Determine domain
        domain = domain_hint
        if not domain and matched_patterns:
            domain = matched_patterns[0].domain

        # Determine severity
        severity = "medium"
        if matched_patterns:
            severity_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            severity = max(
                (p.severity for p in matched_patterns),
                key=lambda s: severity_order.get(s, 0),
            )

        # Collect tags
        all_tags: Set[str] = set()
        for p in matched_patterns:
            all_tags.update(p.tags)
        if domain and domain not in all_tags:
            all_tags.add(domain)

        sig = ErrorSignature(
            stage=stage,
            command=command,
            exit_code=exit_code,
            stdout=stdout[:2000],
            stderr=stderr[:2000],
            log_snippet=log_snippet[:1000],
            system_state=system_state or {},
            domain=domain,
            severity=severity,
            tags=sorted(all_tags),
            patterns_matched=[p.id for p in matched_patterns],
        )

        self._store(sig)
        logger.debug("SignatureCollector: collected %s [%s] domain=%s tags=%s",
                      sig.fingerprint, sig.severity, sig.domain, sig.tags)
        return sig

    def add_pattern(self, pattern: ErrorPattern) -> None:
        """Добавить пользовательский паттерн."""
        self._custom_patterns.append(pattern)

    # ── Query ────────────────────────────────────────────

    def find_by_fingerprint(self, fingerprint: str) -> Optional[ErrorSignature]:
        """Найти сигнатуру по fingerprint."""
        for sig in reversed(self._signatures):
            if sig.fingerprint == fingerprint:
                return sig
        return None

    def find_by_domain(self, domain: str, limit: int = 20) -> List[ErrorSignature]:
        """Найти сигнатуры по домену."""
        result = [s for s in self._signatures if s.domain == domain]
        return result[-limit:]

    def find_by_tags(self, tags: List[str], limit: int = 20) -> List[ErrorSignature]:
        """Найти сигнатуры по тегам (любой из тегов)."""
        tag_set = set(tags)
        result = [s for s in self._signatures if tag_set & set(s.tags)]
        return result[-limit:]

    def recent(self, limit: int = 20) -> List[ErrorSignature]:
        """Последние N сигнатур."""
        return self._signatures[-limit:]

    # ── Storage ──────────────────────────────────────────

    def _store(self, sig: ErrorSignature) -> None:
        """Сохранить сигнатуру."""
        self._signatures.append(sig)
        if len(self._signatures) > self._max_signatures:
            self._signatures = self._signatures[-self._max_signatures:]

    def clear(self) -> int:
        """Очистить все сигнатуры. Returns: count cleared."""
        c = len(self._signatures)
        self._signatures.clear()
        return c

    # ── Stats ────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Статистика."""
        domains: Dict[str, int] = {}
        severities: Dict[str, int] = {}
        for s in self._signatures:
            domains[s.domain] = domains.get(s.domain, 0) + 1
            severities[s.severity] = severities.get(s.severity, 0) + 1
        return {
            "total_signatures": len(self._signatures),
            "domains": domains,
            "severities": severities,
            "custom_patterns": len(self._custom_patterns),
        }


# ─── Singleton ─────────────────────────────────────────────────────────────────

_collector: Optional[SignatureCollector] = None

def get_signature_collector() -> SignatureCollector:
    """Получить единственный экземпляр SignatureCollector."""
    global _collector
    if _collector is None:
        _collector = SignatureCollector()
    return _collector
