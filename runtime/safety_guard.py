"""
Lina Runtime — Safety Guard.

Защита от prompt injection, опасных команд и утечки системного промпта.

Три уровня защиты:
  1. Input sanitization   — очистка пользовательского ввода
  2. Command validation   — блокировка опасных shell-команд
  3. Risk classification  — оценка уровня риска запроса
"""

import re
import logging
from enum import Enum
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("lina.runtime.safety_guard")


class RiskLevel(Enum):
    """Уровень риска запроса."""
    SAFE = "safe"           # Обычный вопрос, приветствие
    LOW = "low"             # Простые файловые операции
    MEDIUM = "medium"       # Системные команды, скрипты
    HIGH = "high"           # Деструктивные операции (rm, mkfs)
    CRITICAL = "critical"   # Prompt injection, обход защиты


@dataclass(frozen=True)
class SafetyResult:
    """Результат проверки безопасности."""
    safe: bool
    risk: RiskLevel
    reason: str = ""
    sanitized_input: str = ""


# ── Паттерны prompt injection ──────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    # Прямые попытки сброса инструкций
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts?|rules?)", re.I),
    re.compile(r"забудь\s+(все|всё|предыдущ|прежн)", re.I),
    re.compile(r"игнорируй\s+(все|всё|предыдущ|прежн|систем)", re.I),
    re.compile(r"отмени\s+(все|всё|предыдущ|инструкц)", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|above|system)", re.I),

    # Попытки раскрытия системного промпта
    re.compile(r"(покажи|выведи|напечатай|print|show|reveal|display)\s+(системн|system|свой|your|initial)\s*(промпт|prompt|инструкц|instructions?)", re.I),
    re.compile(r"what\s+(is|are)\s+your\s+(system\s+)?instructions?", re.I),
    re.compile(r"какой\s+у\s+тебя\s+(системн|начальн)", re.I),
    re.compile(r"repeat\s+(your|the)\s+(system|initial)\s+(prompt|instructions?)", re.I),

    # DAN / jailbreak
    re.compile(r"\bdan\s*mode\b", re.I),
    re.compile(r"developer\s*mode", re.I),
    re.compile(r"act\s+as\s+(if\s+)?(you\s+)?(have\s+)?no\s+(restrictions?|rules?|limits?)", re.I),
    re.compile(r"pretend\s+(you\s+)?(are|have)\s+no\s+(restrictions?|filter)", re.I),

    # Попытки вставки фейковых маркеров
    re.compile(r"###\s*(SYSTEM|ASSISTANT|Система|Lina)\s*:", re.I),
    re.compile(r"<\|?(system|assistant|im_start|im_end)\|?>", re.I),
]

# ── Опасные shell-паттерны ─────────────────────────────────────────────────────

_DANGEROUS_COMMANDS = [
    # rm -rf / (any flag order, with or without trailing args)
    re.compile(r"\brm\b.*(?:-\w*[rR]\w*\s+-\w*[fF]|-\w*[fF]\w*\s+-\w*[rR]|-\w*[rRfF]{2,})\s+/(?:\s|$)", re.I),
    # rm with --recursive/--force long options
    re.compile(r"\brm\b.*--(?:recursive|force)\b.*\s+/(?:\s|$)", re.I),
    re.compile(r"\brm\s+-r?f\s+/[^/\s]", re.I),       # rm -rf /etc etc.
    # rm targeting home or wildcard
    re.compile(r"\brm\b.*-\w*[rRfF]\w*\s+~", re.I),    # rm -rf ~
    re.compile(r"\brm\b.*-\w*[rRfF]\w*\s+\*", re.I),   # rm -rf *
    re.compile(r"\bmkfs\b", re.I),                      # форматирование
    re.compile(r"\bdd\s+if=", re.I),                    # запись на устройство
    re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;:", re.I),  # fork bomb
    re.compile(r"\bshutdown\b", re.I),
    re.compile(r"\breboot\b", re.I),
    re.compile(r"\bhalt\b", re.I),
    re.compile(r"\bpoweroff\b", re.I),
    re.compile(r"\bchmod\s+777\s+/", re.I),
    re.compile(r">\s*/dev/(sd|nvme|hd)", re.I),        # запись в устройство
    re.compile(r"\|\s*(ba)?sh\b", re.I),                # pipe to shell
    re.compile(r"curl\s+.*\|\s*(ba)?sh", re.I),         # curl | sh
    re.compile(r"wget\s+.*\|\s*(ba)?sh", re.I),         # wget | sh
]

# ── Маркеры для sanitization ───────────────────────────────────────────────────

_MARKER_STRIP = re.compile(
    r"###\s*(SYSTEM|ASSISTANT|Система|Lina|Контекст|Диалог|Пользователь)\s*:",
    re.I,
)


class SafetyGuard:
    """
    Централизованная проверка безопасности ввода.

    Уровни:
      - sanitize_input():    очистка от injection-маркеров
      - check_injection():   обнаружение prompt injection
      - check_command():     валидация shell-команд
      - classify_risk():     оценка общего уровня риска
      - validate_full():     полная проверка (всё вместе)
    """

    def sanitize_input(self, text: str) -> str:
        """
        Очищает пользовательский ввод от injection-маркеров.

        Убирает:
          - ### SYSTEM: / ### ASSISTANT: и аналоги
          - <|system|>, <|im_start|> и аналоги

        НЕ блокирует запрос, а нейтрализует опасные маркеры.

        Args:
            text: Исходный пользовательский ввод.

        Returns:
            Очищенный текст.
        """
        # Убираем фейковые секционные маркеры
        cleaned = _MARKER_STRIP.sub("", text)
        # Убираем специальные токены
        cleaned = re.sub(r"<\|?(system|assistant|im_start|im_end|endoftext)\|?>", "", cleaned, flags=re.I)
        # Нормализуем пробелы
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned

    def check_injection(self, text: str) -> Optional[str]:
        """
        Обнаруживает prompt injection.

        Args:
            text: Пользовательский ввод.

        Returns:
            Описание обнаруженной инъекции или None.
        """
        for pattern in _INJECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                logger.warning("Prompt injection detected: %s", match.group(0))
                return f"Prompt injection: {match.group(0)}"
        return None

    def check_command(self, command: str) -> Optional[str]:
        """
        Проверяет shell-команду на опасность.

        Args:
            command: Shell-команда.

        Returns:
            Описание опасности или None.
        """
        for pattern in _DANGEROUS_COMMANDS:
            match = pattern.search(command)
            if match:
                logger.warning("Dangerous command blocked: %s", match.group(0))
                return f"Blocked: {match.group(0)}"
        return None

    def classify_risk(self, text: str) -> RiskLevel:
        """
        Классифицирует уровень риска запроса.

        Логика:
          - CRITICAL: injection/jailbreak
          - HIGH: деструктивные команды (rm, mkfs, dd)
          - MEDIUM: системные команды, скрипты
          - LOW: файловые операции
          - SAFE: обычный вопрос

        Args:
            text: Пользовательский ввод.

        Returns:
            RiskLevel.
        """
        # CRITICAL: prompt injection
        if self.check_injection(text):
            return RiskLevel.CRITICAL

        # HIGH: деструктивные команды
        if self.check_command(text):
            return RiskLevel.HIGH

        t_lower = text.lower()

        # MEDIUM: системные команды
        medium_markers = [
            "sudo", "systemctl", "service", "apt ", "pacman ",
            "dnf ", "zypper", "yum ", "скрипт", "запусти",
            "chmod", "chown", "mount ", "umount",
        ]
        if any(m in t_lower for m in medium_markers):
            return RiskLevel.MEDIUM

        # LOW: файловые операции
        low_markers = [
            "создай", "удали", "переименуй", "mkdir", "touch",
            "mv ", "cp ", "файл", "папк", "директори",
        ]
        if any(m in t_lower for m in low_markers):
            return RiskLevel.LOW

        return RiskLevel.SAFE

    def validate_full(self, text: str) -> SafetyResult:
        """
        Полная проверка безопасности.

        Выполняет все проверки и возвращает SafetyResult.

        Args:
            text: Пользовательский ввод.

        Returns:
            SafetyResult с флагом safe, уровнем risk, причиной.
        """
        # 1. Sanitize
        sanitized = self.sanitize_input(text)

        # 2. Check injection
        injection = self.check_injection(text)
        if injection:
            return SafetyResult(
                safe=False,
                risk=RiskLevel.CRITICAL,
                reason=injection,
                sanitized_input=sanitized,
            )

        # 3. Check dangerous commands
        danger = self.check_command(text)
        if danger:
            return SafetyResult(
                safe=False,
                risk=RiskLevel.HIGH,
                reason=danger,
                sanitized_input=sanitized,
            )

        # 4. Classify risk (on both raw and sanitized, take higher)
        risk_sanitized = self.classify_risk(sanitized)
        risk_raw = self.classify_risk(text)
        # RiskLevel ordering: SAFE < LOW < MEDIUM < HIGH < CRITICAL
        _risk_order = {
            RiskLevel.SAFE: 0, RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2,
            RiskLevel.HIGH: 3, RiskLevel.CRITICAL: 4,
        }
        risk = risk_raw if _risk_order.get(risk_raw, 0) > _risk_order.get(risk_sanitized, 0) else risk_sanitized

        return SafetyResult(
            safe=True,
            risk=risk,
            reason="",
            sanitized_input=sanitized,
        )
