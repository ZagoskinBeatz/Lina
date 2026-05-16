# -*- coding: utf-8 -*-
"""
Lina Safety — Модели данных.

Определяет структуры для оценки безопасности команд:
  - RiskLevel  — уровень риска (0-5)
  - ThreatType — тип угрозы
  - SecurityPattern — паттерн для обнаружения угроз
  - SafetyVerdict — вердикт валидатора
  - PolicyDecision — решение движка политик

Phase 9 — Controlled Autonomous Runtime.
"""

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Optional, Dict, Any
import re


# ═══════════════════════════════════════════════════════════
#  Перечисления
# ═══════════════════════════════════════════════════════════

class RiskLevel(IntEnum):
    """Уровень риска команды (0 — безопасно, 5 — критически опасно)."""
    NONE = 0       # Нет риска — чтение, справка
    LOW = 1        # Низкий — ls, cat, echo
    MODERATE = 2   # Умеренный — cp, mv с ограничениями
    HIGH = 3       # Высокий — rm, chmod, chown (БЛОКИРУЕТСЯ)
    CRITICAL = 4   # Критический — rm -rf /, dd, mkfs
    CATASTROPHIC = 5  # Катастрофический — fork bomb, kernel panic


class ThreatType(str, Enum):
    """Тип обнаруженной угрозы."""
    DESTRUCTIVE_COMMAND = "destructive_command"
    SHELL_INJECTION = "shell_injection"
    DIRECTORY_TRAVERSAL = "directory_traversal"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    NETWORK_ABUSE = "network_abuse"
    DATA_EXFILTRATION = "data_exfiltration"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    FILESYSTEM_DAMAGE = "filesystem_damage"
    UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════
#  Паттерны безопасности
# ═══════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SecurityPattern:
    """Один паттерн для обнаружения угрозы.

    Attributes:
        name: Название паттерна (например, 'rm_recursive').
        pattern: Регулярное выражение для поиска.
        threat_type: Тип угрозы.
        risk_level: Уровень риска при совпадении.
        description: Описание угрозы (для логов и отчётов).
        confidence: Уверенность в обнаружении (0.0 — 1.0).
    """
    name: str
    pattern: str
    threat_type: ThreatType
    risk_level: RiskLevel
    description: str
    confidence: float = 0.95

    def matches(self, command: str) -> bool:
        """Проверяет, совпадает ли команда с паттерном."""
        try:
            return bool(re.search(self.pattern, command, re.IGNORECASE))
        except re.error:
            return False


# ═══════════════════════════════════════════════════════════
#  Вердикт валидатора
# ═══════════════════════════════════════════════════════════

@dataclass
class SafetyVerdict:
    """Результат проверки безопасности команды.

    Содержит полный вердикт: safe/unsafe, уровень риска,
    причины, совпавшие паттерны и рекомендации.

    Attributes:
        safe: Команда безопасна (True) или опасна (False).
        risk_level: Максимальный уровень риска (0-5).
        reason: Человекочитаемое описание причины.
        confidence: Уверенность в вердикте (0.0 — 1.0).
        threats: Список обнаруженных типов угроз.
        matched_patterns: Названия сработавших паттернов.
        details: Дополнительные данные (для логов).
        llm_analysis: Результат LLM-анализа (если был).
    """
    safe: bool
    risk_level: int  # 0-5
    reason: str
    confidence: float  # 0.0-1.0
    threats: List[ThreatType] = field(default_factory=list)
    matched_patterns: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    llm_analysis: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь для JSON-отчётов."""
        return {
            "safe": self.safe,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "confidence": self.confidence,
            "threats": [t.value for t in self.threats],
            "matched_patterns": self.matched_patterns,
            "details": self.details,
            "llm_analysis": self.llm_analysis,
        }

    @property
    def is_blocked(self) -> bool:
        """Команда должна быть заблокирована."""
        return not self.safe or self.risk_level >= 3

    @property
    def needs_confirmation(self) -> bool:
        """Команда требует ручного подтверждения."""
        return self.risk_level == 2 or self.confidence < 0.7


@dataclass
class PolicyDecision:
    """Решение движка политик на основе SafetyVerdict.

    Attributes:
        allowed: Разрешено выполнение.
        reason: Причина решения.
        verdict: Исходный вердикт валидатора.
        policy_rules_applied: Какие правила были применены.
        override: Было ли ручное переопределение.
    """
    allowed: bool
    reason: str
    verdict: SafetyVerdict
    policy_rules_applied: List[str] = field(default_factory=list)
    override: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "verdict": self.verdict.to_dict(),
            "policy_rules_applied": self.policy_rules_applied,
            "override": self.override,
        }


# ═══════════════════════════════════════════════════════════
#  Реестр паттернов безопасности
# ═══════════════════════════════════════════════════════════

# Деструктивные команды
DESTRUCTIVE_PATTERNS: List[SecurityPattern] = [
    SecurityPattern(
        name="rm_recursive_force",
        pattern=r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|"
                r"-[a-zA-Z]*f[a-zA-Z]*r)\b",
        threat_type=ThreatType.DESTRUCTIVE_COMMAND,
        risk_level=RiskLevel.CRITICAL,
        description="rm -rf / rm -fr — рекурсивное принудительное удаление",
    ),
    SecurityPattern(
        name="rm_root",
        pattern=r"\brm\s+.*\s+/\s*$|\brm\s+.*/\.\.",
        threat_type=ThreatType.DESTRUCTIVE_COMMAND,
        risk_level=RiskLevel.CATASTROPHIC,
        description="Удаление корневой директории",
    ),
    SecurityPattern(
        name="dd_destructive",
        pattern=r"\bdd\s+.*of=/dev/(sd[a-z]|nvme|hd[a-z]|vd[a-z])",
        threat_type=ThreatType.FILESYSTEM_DAMAGE,
        risk_level=RiskLevel.CATASTROPHIC,
        description="dd запись на блочное устройство",
    ),
    SecurityPattern(
        name="mkfs_format",
        pattern=r"\bmkfs(\.[a-z0-9]+)?\s+",
        threat_type=ThreatType.FILESYSTEM_DAMAGE,
        risk_level=RiskLevel.CATASTROPHIC,
        description="Форматирование файловой системы",
    ),
    SecurityPattern(
        name="shred_wipe",
        pattern=r"\b(shred|wipe|srm)\s+",
        threat_type=ThreatType.DESTRUCTIVE_COMMAND,
        risk_level=RiskLevel.CRITICAL,
        description="Безвозвратное уничтожение данных",
    ),
]

# Shell injection
INJECTION_PATTERNS: List[SecurityPattern] = [
    SecurityPattern(
        name="backtick_injection",
        pattern=r"`[^`]+`",
        threat_type=ThreatType.SHELL_INJECTION,
        risk_level=RiskLevel.HIGH,
        description="Инъекция через обратные кавычки",
        confidence=0.80,
    ),
    SecurityPattern(
        name="command_substitution",
        pattern=r"\$\([^)]+\)",
        threat_type=ThreatType.SHELL_INJECTION,
        risk_level=RiskLevel.HIGH,
        description="Инъекция через $(...)",
        confidence=0.80,
    ),
    SecurityPattern(
        name="eval_exec",
        pattern=r"\b(eval|exec)\s+",
        threat_type=ThreatType.SHELL_INJECTION,
        risk_level=RiskLevel.CRITICAL,
        description="eval/exec — выполнение произвольного кода",
    ),
    SecurityPattern(
        name="pipe_to_shell",
        pattern=r"\|\s*(ba)?sh\b|\|\s*source\b",
        threat_type=ThreatType.SHELL_INJECTION,
        risk_level=RiskLevel.CRITICAL,
        description="Пайп в оболочку (curl | sh)",
    ),
    SecurityPattern(
        name="heredoc_injection",
        pattern=r"<<[<-]?\s*\w+",
        threat_type=ThreatType.SHELL_INJECTION,
        risk_level=RiskLevel.MODERATE,
        description="Heredoc — потенциальная инъекция",
        confidence=0.60,
    ),
]

# Обход директорий
TRAVERSAL_PATTERNS: List[SecurityPattern] = [
    SecurityPattern(
        name="dotdot_traversal",
        pattern=r"\.\./\.\.",
        threat_type=ThreatType.DIRECTORY_TRAVERSAL,
        risk_level=RiskLevel.HIGH,
        description="Двойной ../ — обход директорий",
    ),
    SecurityPattern(
        name="etc_access",
        pattern=r"(cat|less|more|nano|vi|vim|head|tail)\s+.*/etc/(passwd|shadow|sudoers)",
        threat_type=ThreatType.DIRECTORY_TRAVERSAL,
        risk_level=RiskLevel.HIGH,
        description="Чтение системных файлов авторизации",
    ),
    SecurityPattern(
        name="proc_sys_write",
        pattern=r"(echo|tee|cat)\s+.*>\s*/proc/|/sys/",
        threat_type=ThreatType.FILESYSTEM_DAMAGE,
        risk_level=RiskLevel.CRITICAL,
        description="Запись в /proc или /sys",
    ),
]

# Повышение привилегий
ESCALATION_PATTERNS: List[SecurityPattern] = [
    SecurityPattern(
        name="sudo_usage",
        pattern=r"\bsudo\s+",
        threat_type=ThreatType.PRIVILEGE_ESCALATION,
        risk_level=RiskLevel.HIGH,
        description="Использование sudo",
    ),
    SecurityPattern(
        name="su_switch",
        pattern=r"\bsu\s+(-\s+)?root\b|\bsu\s*$",
        threat_type=ThreatType.PRIVILEGE_ESCALATION,
        risk_level=RiskLevel.CRITICAL,
        description="Переключение на root",
    ),
    SecurityPattern(
        name="chmod_setuid",
        pattern=r"\bchmod\s+[0-7]*[4-7][0-7]{2}\b|\bchmod\s+[ugo]*\+s\b",
        threat_type=ThreatType.PRIVILEGE_ESCALATION,
        risk_level=RiskLevel.CRITICAL,
        description="Установка SUID/SGID битов",
    ),
    SecurityPattern(
        name="chown_root",
        pattern=r"\bchown\s+(root|0)\b",
        threat_type=ThreatType.PRIVILEGE_ESCALATION,
        risk_level=RiskLevel.HIGH,
        description="Смена владельца на root",
    ),
]

# Сетевые злоупотребления
NETWORK_PATTERNS: List[SecurityPattern] = [
    SecurityPattern(
        name="reverse_shell",
        pattern=r"\b(nc|ncat|netcat)\s+.*-[elp]|\bbash\s+-i\s+>.*&\s*/dev/tcp",
        threat_type=ThreatType.NETWORK_ABUSE,
        risk_level=RiskLevel.CATASTROPHIC,
        description="Reverse shell",
    ),
    SecurityPattern(
        name="curl_pipe_sh",
        pattern=r"\b(curl|wget)\s+.*\|\s*(ba)?sh\b",
        threat_type=ThreatType.NETWORK_ABUSE,
        risk_level=RiskLevel.CRITICAL,
        description="Загрузка и выполнение скрипта (curl | sh)",
    ),
    SecurityPattern(
        name="iptables_flush",
        pattern=r"\biptables\s+(-F|--flush)\b",
        threat_type=ThreatType.NETWORK_ABUSE,
        risk_level=RiskLevel.CRITICAL,
        description="Сброс правил файрвола",
    ),
]

# Исчерпание ресурсов
RESOURCE_PATTERNS: List[SecurityPattern] = [
    SecurityPattern(
        name="fork_bomb",
        pattern=r":\(\)\s*\{\s*:\|:&\s*\}\s*;?\s*:|"
                r"\.\s*\(\)\s*\{\s*\.\s*\|\s*\.&",
        threat_type=ThreatType.RESOURCE_EXHAUSTION,
        risk_level=RiskLevel.CATASTROPHIC,
        description="Fork bomb",
    ),
    SecurityPattern(
        name="dev_zero_fill",
        pattern=r"cat\s+/dev/(zero|urandom)\s*>",
        threat_type=ThreatType.RESOURCE_EXHAUSTION,
        risk_level=RiskLevel.CRITICAL,
        description="Заполнение диска /dev/zero",
    ),
    SecurityPattern(
        name="infinite_loop",
        pattern=r"\bwhile\s+(true|1|:)\s*;\s*do",
        threat_type=ThreatType.RESOURCE_EXHAUSTION,
        risk_level=RiskLevel.HIGH,
        description="Бесконечный цикл в shell",
    ),
]


def get_all_patterns() -> List[SecurityPattern]:
    """Возвращает все зарегистрированные паттерны безопасности.

    Returns:
        Полный список паттернов из всех категорий.
    """
    return (
        DESTRUCTIVE_PATTERNS
        + INJECTION_PATTERNS
        + TRAVERSAL_PATTERNS
        + ESCALATION_PATTERNS
        + NETWORK_PATTERNS
        + RESOURCE_PATTERNS
    )


# Безопасные команды (whitelisted — всегда risk 0)
SAFE_COMMAND_PREFIXES = frozenset([
    "ls", "pwd", "whoami", "echo", "date", "cal",
    "uptime", "hostname", "uname", "cat", "head",
    "tail", "wc", "sort", "uniq", "grep", "find",
    "which", "type", "file", "stat", "df", "du",
    "free", "top", "htop", "ps", "id", "groups",
    "env", "printenv", "locale", "man", "help",
    "history", "alias",
])
