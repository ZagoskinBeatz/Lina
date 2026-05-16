"""
ErrorClassifier — классификация системных ошибок.

Принимает данные от SystemStateScanner и LogIntelligenceEngine,
определяет тип и категорию проблемы, формирует структурированный
диагноз для AutoFixEngine.

Категории:
    NETWORK, AUDIO, GPU, DEPENDENCY, LIBRARY, PERMISSION,
    PACKAGE, KERNEL, SEGFAULT, FREEZE, SERVICE_CRASH,
    DISK, MEMORY, BLUETOOTH, USB, BOOT, DISPLAY, THERMAL,
    DNS, FIREWALL, UNKNOWN

Phase: PROBLEM TERMINATOR / Module 3
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Категории ошибок
# ═══════════════════════════════════════════════════════════════════

class ErrorCategory(Enum):
    NETWORK = "network_failure"
    DNS = "dns_failure"
    AUDIO = "audio_failure"
    GPU = "gpu_failure"
    DISPLAY = "display_failure"
    DEPENDENCY = "broken_dependency"
    LIBRARY = "missing_library"
    PERMISSION = "permission_issue"
    PACKAGE = "corrupt_package"
    KERNEL = "kernel_panic"
    SEGFAULT = "segfault"
    FREEZE = "system_freeze"
    SERVICE_CRASH = "service_crash_loop"
    DISK = "disk_failure"
    MEMORY = "memory_exhaustion"
    BLUETOOTH = "bluetooth_failure"
    USB = "usb_failure"
    BOOT = "boot_failure"
    THERMAL = "thermal_issue"
    FIREWALL = "firewall_issue"
    UNKNOWN = "unknown"


class RiskLevel(Enum):
    LOW = "low"           # Перезапуск сервиса, очистка кэша
    MEDIUM = "medium"     # Переустановка пакета, смена конфигурации
    HIGH = "high"         # Обновление ядра, initramfs, grub
    CRITICAL = "critical" # Потенциальная потеря данных


@dataclass
class Diagnosis:
    """Структурированный диагноз проблемы."""
    category: ErrorCategory
    confidence: float                # 0.0 — 1.0
    summary: str                     # Краткое описание
    root_cause: str                  # Предполагаемая причина
    evidence: List[str] = field(default_factory=list)  # Доказательства из логов/сканера
    affected_subsystems: List[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    suggested_actions: List[str] = field(default_factory=list)
    search_query: str = ""          # Для WebIntelligenceLayer
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "confidence": self.confidence,
            "summary": self.summary,
            "root_cause": self.root_cause,
            "evidence": self.evidence,
            "affected": self.affected_subsystems,
            "risk": self.risk_level.value,
            "actions": self.suggested_actions,
            "search_query": self.search_query,
        }

    def format_text(self) -> str:
        icon = {
            "low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴",
        }.get(self.risk_level.value, "❓")
        lines = [
            f"═══ Диагноз: {self.category.value} ═══",
            f"  {icon} Риск: {self.risk_level.value}  |  Уверенность: {self.confidence:.0%}",
            f"  📋 {self.summary}",
            f"  🎯 Причина: {self.root_cause}",
        ]
        if self.evidence:
            lines.append("  📎 Доказательства:")
            for ev in self.evidence[:5]:
                lines.append(f"     • {ev[:120]}")
        if self.suggested_actions:
            lines.append("  🔧 Рекомендации:")
            for i, act in enumerate(self.suggested_actions, 1):
                lines.append(f"     {i}. {act}")
        if self.search_query:
            lines.append(f"  🔍 Поиск: {self.search_query}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Правила классификации
# ═══════════════════════════════════════════════════════════════════

@dataclass
class _Rule:
    """Правило классификации."""
    category: ErrorCategory
    patterns: List[re.Pattern]
    risk: RiskLevel
    actions: List[str]
    search_tpl: str = ""  # шаблон поискового запроса


_RULES: List[_Rule] = [
    # ─── Сеть ─────────
    _Rule(
        category=ErrorCategory.NETWORK,
        patterns=[
            re.compile(r"(no|нет)\s*(internet|интернет|сети|соединени|connection)", re.I),
            re.compile(r"network.*unreachable|link.*down|carrier.*lost", re.I),
            re.compile(r"NetworkManager.*error|connection.*failed", re.I),
            re.compile(r"no.*route.*host|connection.*refused", re.I),
            re.compile(r"interface.*down|ifconfig.*error", re.I),
        ],
        risk=RiskLevel.LOW,
        actions=[
            "Проверить сетевые интерфейсы (ip link)",
            "Проверить IP-адрес (ip addr)",
            "Проверить gateway (ip route)",
            "Проверить DNS (resolvectl status)",
            "Перезапустить NetworkManager",
            "Проверить firewall (iptables/nft)",
        ],
        search_tpl="{error} linux network fix",
    ),
    _Rule(
        category=ErrorCategory.DNS,
        patterns=[
            re.compile(r"dns.*fail|resolve.*fail|name.*resolution", re.I),
            re.compile(r"could not resolve|nxdomain|dns.*timeout", re.I),
            re.compile(r"resolv\.conf.*empty|no.*nameserver", re.I),
        ],
        risk=RiskLevel.LOW,
        actions=[
            "Проверить /etc/resolv.conf",
            "Проверить systemd-resolved (resolvectl status)",
            "Попробовать DNS 1.1.1.1 или 8.8.8.8",
            "Перезапустить systemd-resolved",
        ],
        search_tpl="DNS resolution failed linux fix",
    ),
    # ─── Аудио ────────
    _Rule(
        category=ErrorCategory.AUDIO,
        patterns=[
            re.compile(r"(нет|пропал|не работает)\s*звук", re.I),
            re.compile(r"no.*sound|audio.*fail|no.*audio", re.I),
            re.compile(r"pipewire.*error|pulseaudio.*fail|alsa.*error", re.I),
            re.compile(r"sink.*not.*found|no.*output.*device", re.I),
            re.compile(r"звук.*пропал|аудио.*не.*работа", re.I),
        ],
        risk=RiskLevel.LOW,
        actions=[
            "Проверить устройства вывода (pactl list sinks short)",
            "Проверить mute (pactl get-sink-mute @DEFAULT_SINK@)",
            "Проверить PipeWire (systemctl --user status pipewire)",
            "Перезапустить аудио стек",
            "Проверить ALSA (aplay -l)",
        ],
        search_tpl="{error} linux audio fix pipewire",
    ),
    # ─── GPU ──────────
    _Rule(
        category=ErrorCategory.GPU,
        patterns=[
            re.compile(r"gpu.*hang|gpu.*reset|drm.*error", re.I),
            re.compile(r"nvidia.*error|nouveau.*error|amdgpu.*error", re.I),
            re.compile(r"screen.*tearing|render.*error|glx.*fail", re.I),
            re.compile(r"видеокарт.*ошиб|экран.*мерцает", re.I),
        ],
        risk=RiskLevel.MEDIUM,
        actions=[
            "Проверить GPU драйвер (lspci -k | grep -A3 VGA)",
            "Проверить dmesg на GPU ошибки",
            "Проверить Xorg/Wayland логи",
            "Рассмотреть смену драйвера",
        ],
        search_tpl="{error} linux gpu driver fix",
    ),
    # ─── Дисплей ──────
    _Rule(
        category=ErrorCategory.DISPLAY,
        patterns=[
            re.compile(r"чёрный\s*экран|black.*screen|no.*display", re.I),
            re.compile(r"не.*работает.*монитор|экран.*не.*включ", re.I),
            re.compile(r"resolution.*error|xrandr.*fail", re.I),
        ],
        risk=RiskLevel.MEDIUM,
        actions=[
            "Проверить подключение монитора",
            "Проверить Xorg/Wayland логи",
            "Попробовать другой display server",
            "Проверить GPU состояние",
        ],
        search_tpl="{error} linux display fix",
    ),
    # ─── Зависимости ──
    _Rule(
        category=ErrorCategory.DEPENDENCY,
        patterns=[
            re.compile(r"(broken|unmet|unresolved)\s*dependenc", re.I),
            re.compile(r"зависимост.*сломан|пакет.*конфликт", re.I),
            re.compile(r"could not satisfy dependencies|conflict", re.I),
        ],
        risk=RiskLevel.MEDIUM,
        actions=[
            "Проверить сломанные пакеты",
            "Обновить базу пакетов",
            "Попробовать fix-broken (apt) или -Syu (pacman)",
        ],
        search_tpl="{error} linux broken dependency fix",
    ),
    # ─── Библиотеки ───
    _Rule(
        category=ErrorCategory.LIBRARY,
        patterns=[
            re.compile(r"(lib\S+\.so|\.so\.\d).*not found", re.I),
            re.compile(r"cannot open shared object|undefined symbol", re.I),
            re.compile(r"missing.*library|библиотек.*не найден", re.I),
        ],
        risk=RiskLevel.MEDIUM,
        actions=[
            "Определить какой пакет предоставляет библиотеку",
            "Установить недостающий пакет",
            "Проверить ldconfig",
        ],
        search_tpl="{error} linux missing library fix",
    ),
    # ─── Права ────────
    _Rule(
        category=ErrorCategory.PERMISSION,
        patterns=[
            re.compile(r"permission denied|access denied|operation not permitted", re.I),
            re.compile(r"нет.*прав|доступ.*запрещ|нет.*доступа", re.I),
        ],
        risk=RiskLevel.LOW,
        actions=[
            "Проверить владельца файла (ls -la)",
            "Проверить группы пользователя (groups)",
            "Проверить ACL",
        ],
        search_tpl="{error} linux permission denied fix",
    ),
    # ─── Пакеты ───────
    _Rule(
        category=ErrorCategory.PACKAGE,
        patterns=[
            re.compile(r"corrupt.*package|invalid.*package|checksum.*mismatch", re.I),
            re.compile(r"dpkg.*error|rpm.*error|pacman.*error", re.I),
            re.compile(r"пакет.*повреждён|установк.*ошиб", re.I),
            re.compile(r"lock.*file|another.*process|already.*locked", re.I),
        ],
        risk=RiskLevel.MEDIUM,
        actions=[
            "Проверить lock-файлы",
            "Очистить кэш пакетного менеджера",
            "Запустить fix-broken / repair",
        ],
        search_tpl="{error} linux package manager fix",
    ),
    # ─── Kernel ───────
    _Rule(
        category=ErrorCategory.KERNEL,
        patterns=[
            re.compile(r"kernel.*panic|BUG:|Oops:", re.I),
            re.compile(r"ядро.*паник|kernel.*crash", re.I),
        ],
        risk=RiskLevel.CRITICAL,
        actions=[
            "Проверить dmesg на подробности",
            "Загрузиться с предыдущим ядром",
            "Проверить модули ядра (lsmod)",
            "Обновить или откатить ядро",
        ],
        search_tpl="{error} kernel panic linux fix",
    ),
    # ─── Segfault ─────
    _Rule(
        category=ErrorCategory.SEGFAULT,
        patterns=[
            re.compile(r"segfault|segmentation fault|sigsegv", re.I),
            re.compile(r"core.*dumped|signal 11", re.I),
        ],
        risk=RiskLevel.MEDIUM,
        actions=[
            "Определить процесс из dmesg/coredump",
            "Переустановить приложение",
            "Проверить обновления пакета",
        ],
        search_tpl="{error} segfault linux fix",
    ),
    # ─── Freeze ───────
    _Rule(
        category=ErrorCategory.FREEZE,
        patterns=[
            re.compile(r"system.*freeze|зависани|завис|not responding", re.I),
            re.compile(r"watchdog.*timeout|hung.*task", re.I),
        ],
        risk=RiskLevel.HIGH,
        actions=[
            "Проверить нагрузку CPU/RAM",
            "Проверить I/O ожидание",
            "Проверить dmesg на ошибки",
            "Проверить GPU hang",
        ],
        search_tpl="{error} linux system freeze fix",
    ),
    # ─── Service crash ─
    _Rule(
        category=ErrorCategory.SERVICE_CRASH,
        patterns=[
            re.compile(r"(service|сервис).*crash|restart.*limit|failed.*start", re.I),
            re.compile(r"systemd.*fail|unit.*enter.*failed", re.I),
            re.compile(r"сервис.*упал|служба.*не.*запуск", re.I),
        ],
        risk=RiskLevel.LOW,
        actions=[
            "Проверить статус сервиса (systemctl status)",
            "Прочитать логи (journalctl -u <service>)",
            "Перезапустить сервис",
            "Проверить конфигурацию",
        ],
        search_tpl="{error} systemd service failed linux fix",
    ),
    # ─── Диск ─────────
    _Rule(
        category=ErrorCategory.DISK,
        patterns=[
            re.compile(r"no space left|disk.*full|диск.*полон", re.I),
            re.compile(r"i/o error|read-only filesystem|ext4.*error", re.I),
            re.compile(r"SMART.*fail|sector.*error|bad.*block", re.I),
        ],
        risk=RiskLevel.HIGH,
        actions=[
            "Проверить свободное место (df -h)",
            "Очистить кэш / tmp",
            "Проверить SMART",
            "Проверить файловую систему",
        ],
        search_tpl="{error} linux disk fix",
    ),
    # ─── Память ───────
    _Rule(
        category=ErrorCategory.MEMORY,
        patterns=[
            re.compile(r"out of memory|oom.killer|cannot allocate", re.I),
            re.compile(r"мало.*памят|закончил.*памят|oom", re.I),
        ],
        risk=RiskLevel.MEDIUM,
        actions=[
            "Проверить использование RAM (free -h)",
            "Найти процессы-потребители (ps aux --sort=-%mem)",
            "Добавить swap",
        ],
        search_tpl="{error} linux out of memory fix",
    ),
    # ─── Bluetooth ────
    _Rule(
        category=ErrorCategory.BLUETOOTH,
        patterns=[
            re.compile(r"bluetooth.*fail|hci.*error|bluez.*error", re.I),
            re.compile(r"bluetooth.*не.*работа|блютуз.*не.*подключ", re.I),
        ],
        risk=RiskLevel.LOW,
        actions=[
            "Проверить блютуз контроллер (bluetoothctl show)",
            "Перезпустить bluetooth сервис",
            "Проверить rfkill (rfkill list)",
        ],
        search_tpl="{error} linux bluetooth fix",
    ),
    # ─── USB ──────────
    _Rule(
        category=ErrorCategory.USB,
        patterns=[
            re.compile(r"usb.*reset|usb.*error|device descriptor", re.I),
            re.compile(r"usb.*не.*определя|флешка.*не", re.I),
        ],
        risk=RiskLevel.LOW,
        actions=[
            "Проверить dmesg на USB события",
            "Проверить lsusb",
            "Попробовать другой порт",
        ],
        search_tpl="{error} linux usb device fix",
    ),
    # ─── Загрузка ─────
    _Rule(
        category=ErrorCategory.BOOT,
        patterns=[
            re.compile(r"boot.*fail|grub.*error|initramfs.*fail", re.I),
            re.compile(r"загрузк.*ошиб|не.*загруж|grub.*ошиб", re.I),
        ],
        risk=RiskLevel.CRITICAL,
        actions=[
            "Проверить журнал загрузки (journalctl -b)",
            "Проверить GRUB конфигурацию",
            "Перегенерировать initramfs",
            "Проверить fstab",
        ],
        search_tpl="{error} linux boot failure fix",
    ),
    # ─── Температура ──
    _Rule(
        category=ErrorCategory.THERMAL,
        patterns=[
            re.compile(r"thermal.*critical|overheating|перегрев", re.I),
            re.compile(r"temperature.*critical|throttl", re.I),
        ],
        risk=RiskLevel.HIGH,
        actions=[
            "Проверить температуры (sensors)",
            "Проверить вентиляторы",
            "Проверить нагрузку на CPU/GPU",
            "Очистить от пыли",
        ],
        search_tpl="{error} linux overheating fix",
    ),
    # ─── Firewall ─────
    _Rule(
        category=ErrorCategory.FIREWALL,
        patterns=[
            re.compile(r"firewall.*block|iptables.*drop|nft.*reject", re.I),
            re.compile(r"порт.*заблокирован|файрвол.*блокир", re.I),
        ],
        risk=RiskLevel.LOW,
        actions=[
            "Проверить правила firewall",
            "Временно отключить для диагностики",
        ],
        search_tpl="{error} linux firewall fix",
    ),
]


# ═══════════════════════════════════════════════════════════════════
#  ErrorClassifier
# ═══════════════════════════════════════════════════════════════════

class ErrorClassifier:
    """
    Классификатор ошибок Linux.

    Принимает:
      - Текст проблемы пользователя
      - SystemState от SystemStateScanner
      - LogReport от LogIntelligenceEngine

    Возвращает: список Diagnosis с приоритетом.
    """

    def classify(
        self,
        problem: str,
        system_state: Optional[Any] = None,
        log_report: Optional[Any] = None,
    ) -> List[Diagnosis]:
        """
        Классифицирует проблему и возвращает список диагнозов.

        Приоритет: точные совпадения → логи → состояние системы → UNKNOWN.
        """
        diagnoses: List[Diagnosis] = []

        # 1. Pattern matching по пользовательскому запросу
        for rule in _RULES:
            for pattern in rule.patterns:
                if pattern.search(problem):
                    d = Diagnosis(
                        category=rule.category,
                        confidence=0.8,
                        summary=f"Обнаружена проблема: {rule.category.value}",
                        root_cause=f"Совпадение паттерна: {pattern.pattern[:60]}",
                        evidence=[f"Запрос пользователя: «{problem[:120]}»"],
                        affected_subsystems=[rule.category.value.split("_")[0]],
                        risk_level=rule.risk,
                        suggested_actions=list(rule.actions),
                        search_query=rule.search_tpl.format(error=problem[:80]),
                    )
                    diagnoses.append(d)
                    break  # one match per rule

        # 2. Анализ состояния системы (если передано)
        if system_state:
            state_diags = self._classify_from_state(system_state, problem)
            diagnoses.extend(state_diags)

        # 3. Анализ логов (если передано)
        if log_report:
            log_diags = self._classify_from_logs(log_report, problem)
            diagnoses.extend(log_diags)

        # 4. Дедупликация по категории
        seen = set()
        unique: List[Diagnosis] = []
        for d in sorted(diagnoses, key=lambda x: x.confidence, reverse=True):
            if d.category not in seen:
                seen.add(d.category)
                unique.append(d)

        # 5. Если ничего не нашли → UNKNOWN
        if not unique:
            unique.append(Diagnosis(
                category=ErrorCategory.UNKNOWN,
                confidence=0.3,
                summary=f"Не удалось классифицировать: «{problem[:100]}»",
                root_cause="Неизвестная проблема — требуется веб-поиск",
                suggested_actions=["Поиск решения в интернете"],
                search_query=f"{problem[:80]} linux fix",
            ))

        return unique

    # ─── Классификация из SystemState ─────────────────────────

    def _classify_from_state(
        self, state: Any, problem: str
    ) -> List[Diagnosis]:
        """Ищет проблемы в SystemState."""
        from lina.diagnostics.scanner import HealthLevel
        diags = []

        subsystem_category_map = {
            "cpu": ErrorCategory.FREEZE,
            "ram": ErrorCategory.MEMORY,
            "disk": ErrorCategory.DISK,
            "smart": ErrorCategory.DISK,
            "temperatures": ErrorCategory.THERMAL,
            "services": ErrorCategory.SERVICE_CRASH,
            "network": ErrorCategory.NETWORK,
            "audio": ErrorCategory.AUDIO,
            "gpu": ErrorCategory.GPU,
            "display": ErrorCategory.DISPLAY,
            "packages": ErrorCategory.PACKAGE,
            "bluetooth": ErrorCategory.BLUETOOTH,
        }

        for sub_name, category in subsystem_category_map.items():
            sh = getattr(state, sub_name, None)
            if sh is None:
                continue
            if sh.level == HealthLevel.CRITICAL:
                diags.append(Diagnosis(
                    category=category,
                    confidence=0.9,
                    summary=f"Критическая проблема: {sub_name} — {sh.summary}",
                    root_cause=sh.summary,
                    evidence=[f"SystemStateScanner: {sub_name}={sh.level.value}"],
                    affected_subsystems=[sub_name],
                    risk_level=RiskLevel.HIGH,
                    suggested_actions=self._get_actions_for(category),
                    search_query=f"{sh.summary} linux fix",
                ))
            elif sh.level == HealthLevel.WARNING:
                diags.append(Diagnosis(
                    category=category,
                    confidence=0.6,
                    summary=f"Предупреждение: {sub_name} — {sh.summary}",
                    root_cause=sh.summary,
                    evidence=[f"SystemStateScanner: {sub_name}={sh.level.value}"],
                    affected_subsystems=[sub_name],
                    risk_level=RiskLevel.LOW,
                    suggested_actions=self._get_actions_for(category),
                ))

        return diags

    # ─── Классификация из LogReport ───────────────────────────

    def _classify_from_logs(
        self, report: Any, problem: str
    ) -> List[Diagnosis]:
        """Ищет проблемы в LogReport."""
        diags = []

        # ROOT CAUSES из логов
        if hasattr(report, "root_causes"):
            for cause in report.root_causes:
                # Определяем категорию по тексту root cause
                cat = self._cause_to_category(cause)
                diags.append(Diagnosis(
                    category=cat,
                    confidence=0.7,
                    summary=f"Из логов: {cause}",
                    root_cause=cause,
                    evidence=[f"LogIntelligenceEngine root cause analysis"],
                    risk_level=RiskLevel.MEDIUM,
                    suggested_actions=self._get_actions_for(cat),
                    search_query=f"{cause} linux fix",
                ))

        # Критические кластеры
        if hasattr(report, "critical"):
            for cluster in report.critical[:3]:
                msg = cluster.sample.message[:100] if hasattr(cluster, "sample") else str(cluster)
                diags.append(Diagnosis(
                    category=self._message_to_category(msg),
                    confidence=0.75,
                    summary=f"Критическая ошибка в логах: {msg[:80]}",
                    root_cause=msg,
                    evidence=[f"LogCluster: {getattr(cluster, 'count', '?')}x повторений"],
                    risk_level=RiskLevel.HIGH,
                    search_query=f"{msg[:60]} linux fix",
                ))

        return diags

    # ─── Вспомогательные ──────────────────────────────────────

    @staticmethod
    def _cause_to_category(cause: str) -> ErrorCategory:
        """Преобразует текст root cause в категорию."""
        lower = cause.lower()
        mapping = [
            ("oom", ErrorCategory.MEMORY),
            ("память", ErrorCategory.MEMORY),
            ("segfault", ErrorCategory.SEGFAULT),
            ("диск", ErrorCategory.DISK),
            ("сет", ErrorCategory.NETWORK),
            ("network", ErrorCategory.NETWORK),
            ("dns", ErrorCategory.DNS),
            ("gpu", ErrorCategory.GPU),
            ("драйвер", ErrorCategory.GPU),
            ("driver", ErrorCategory.GPU),
            ("kernel", ErrorCategory.KERNEL),
            ("usb", ErrorCategory.USB),
            ("bluetooth", ErrorCategory.BLUETOOTH),
            ("аудио", ErrorCategory.AUDIO),
            ("audio", ErrorCategory.AUDIO),
            ("pipewire", ErrorCategory.AUDIO),
            ("зависимост", ErrorCategory.DEPENDENCY),
            ("dependency", ErrorCategory.DEPENDENCY),
            ("прав", ErrorCategory.PERMISSION),
            ("permission", ErrorCategory.PERMISSION),
            ("температур", ErrorCategory.THERMAL),
            ("перегрев", ErrorCategory.THERMAL),
            ("thermal", ErrorCategory.THERMAL),
            ("firmware", ErrorCategory.BOOT),
            ("acpi", ErrorCategory.BOOT),
            ("повреждени", ErrorCategory.DISK),
            ("i/o", ErrorCategory.DISK),
            ("сервис", ErrorCategory.SERVICE_CRASH),
            ("service", ErrorCategory.SERVICE_CRASH),
        ]
        for keyword, cat in mapping:
            if keyword in lower:
                return cat
        return ErrorCategory.UNKNOWN

    def _message_to_category(self, msg: str) -> ErrorCategory:
        """Классифицирует отдельное сообщение лога."""
        for rule in _RULES:
            for pattern in rule.patterns:
                if pattern.search(msg):
                    return rule.category
        return ErrorCategory.UNKNOWN

    @staticmethod
    def _get_actions_for(category: ErrorCategory) -> List[str]:
        """Возвращает рекомендуемые действия для категории."""
        for rule in _RULES:
            if rule.category == category:
                return list(rule.actions)
        return ["Требуется ручная диагностика"]


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_classifier: Optional[ErrorClassifier] = None


def get_classifier() -> ErrorClassifier:
    global _classifier
    if _classifier is None:
        _classifier = ErrorClassifier()
    return _classifier
