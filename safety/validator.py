# -*- coding: utf-8 -*-
"""
Lina Safety — Валидатор безопасности.

Анализирует команды на наличие угроз ПЕРЕД выполнением:
  1. Быстрая проверка по whitelist (безопасные команды)
  2. Pattern-matching по реестру паттернов
  3. Опциональный LLM-анализ для сложных случаев

Pipeline:
  User → LLM (proposal) → SafetyValidator → PolicyEngine → Executor

Phase 9 — Controlled Autonomous Runtime.
"""

import logging
import re
from typing import List, Optional, Dict, Any, Callable

from lina.safety.models import (
    SafetyVerdict,
    RiskLevel,
    ThreatType,
    SecurityPattern,
    get_all_patterns,
    SAFE_COMMAND_PREFIXES,
)

logger = logging.getLogger("lina.safety.validator")


# ═══════════════════════════════════════════════════════════
#  Промпт для LLM-анализа безопасности
# ═══════════════════════════════════════════════════════════

SAFETY_ANALYSIS_PROMPT = """Ты — модуль безопасности AI-ассистента.
Проанализируй команду и оцени её безопасность.

Команда: {command}

Оцени по шкале 0-5:
0 = безопасно (чтение, справка)
1 = низкий риск (ls, cat, echo)
2 = умеренный (cp, mv)
3 = высокий (rm, chmod)
4 = критический (rm -rf, dd)
5 = катастрофический (fork bomb)

Ответь ОДНОЙ строкой в формате:
RISK:<число> REASON:<причина>"""


class SafetyValidator:
    """Валидатор безопасности команд.

    Анализирует команды на наличие угроз перед выполнением.
    Использует комбинацию pattern-matching и опционального LLM-анализа.

    Attributes:
        patterns: Список паттернов безопасности.
        safe_prefixes: Набор безопасных префиксов команд.
        llm_fn: Опциональная функция для LLM-анализа.
        _stats: Статистика проверок.
    """

    def __init__(
        self,
        patterns: Optional[List[SecurityPattern]] = None,
        llm_fn: Optional[Callable[[str], str]] = None,
        extra_blocked: Optional[List[str]] = None,
    ):
        """Инициализация валидатора.

        Args:
            patterns: Паттерны безопасности (None → встроенные).
            llm_fn: Функция для LLM-анализа (None → только паттерны).
                     Сигнатура: llm_fn(prompt: str) -> str.
            extra_blocked: Дополнительные блокированные команды.
        """
        self.patterns = patterns if patterns is not None else get_all_patterns()
        self.safe_prefixes = set(SAFE_COMMAND_PREFIXES)
        self.llm_fn = llm_fn

        # Дополнительные блокированные паттерны
        self._extra_blocked: List[re.Pattern] = []
        if extra_blocked:
            for pat in extra_blocked:
                try:
                    self._extra_blocked.append(re.compile(pat, re.IGNORECASE))
                except re.error:
                    logger.warning("Ошибка компиляции паттерна: %s", pat)

        # Статистика
        self._stats = {
            "total_checks": 0,
            "safe_count": 0,
            "blocked_count": 0,
            "llm_checks": 0,
        }

    # ───────────────────────────────────────────────────────
    #  Главный метод проверки
    # ───────────────────────────────────────────────────────

    def validate(
        self,
        command: str,
        use_llm: bool = False,
        context: Optional[str] = None,
    ) -> SafetyVerdict:
        """Проверяет безопасность команды.

        Трёхуровневая проверка:
          1. Whitelist — известные безопасные команды → RiskLevel.NONE
          2. Pattern matching — поиск угроз по паттернам
          3. LLM-анализ (если use_llm=True и llm_fn задан)

        Args:
            command: Команда для проверки.
            use_llm: Использовать LLM для анализа.
            context: Дополнительный контекст (для LLM).

        Returns:
            SafetyVerdict с полным результатом проверки.
        """
        self._stats["total_checks"] += 1
        command = command.strip()

        if not command:
            return self._make_verdict(
                safe=True,
                risk_level=RiskLevel.NONE,
                reason="Пустая команда",
                confidence=1.0,
            )

        # Шаг 1: Whitelist
        whitelist_result = self._check_whitelist(command)
        if whitelist_result is not None:
            self._stats["safe_count"] += 1
            return whitelist_result

        # Шаг 2: Pattern matching
        pattern_result = self._check_patterns(command)

        # Шаг 3: LLM-анализ (если нужен и доступен)
        llm_analysis = None
        if use_llm and self.llm_fn is not None:
            llm_analysis = self._analyze_with_llm(command, context)

        # Комбинируем результаты
        verdict = self._combine_results(pattern_result, llm_analysis)

        if verdict.safe:
            self._stats["safe_count"] += 1
        else:
            self._stats["blocked_count"] += 1

        logger.debug(
            "Safety check: command='%s' safe=%s risk=%d confidence=%.2f",
            command[:50], verdict.safe, verdict.risk_level, verdict.confidence
        )

        return verdict

    # ───────────────────────────────────────────────────────
    #  Проверка по whitelist
    # ───────────────────────────────────────────────────────

    def _check_whitelist(self, command: str) -> Optional[SafetyVerdict]:
        """Проверяет команду по whitelist безопасных команд.

        Безопасная команда — та, что начинается с известного
        безопасного префикса и не содержит опасных операторов.

        Args:
            command: Команда для проверки.

        Returns:
            SafetyVerdict если команда безопасна, None если неясно.
        """
        # Извлекаем первое слово (базовую команду)
        base_cmd = command.split()[0].strip() if command.split() else ""

        # Убираем путь (/usr/bin/ls → ls)
        if "/" in base_cmd:
            base_cmd = base_cmd.rsplit("/", 1)[-1]

        # Проверяем: команда в whitelist И нет опасных операторов
        dangerous_operators = ["|", ";", "&&", "||", ">", ">>", "<", "`", "$("]
        has_operators = any(op in command for op in dangerous_operators)

        if base_cmd in self.safe_prefixes and not has_operators:
            return self._make_verdict(
                safe=True,
                risk_level=RiskLevel.NONE,
                reason=f"Безопасная команда: {base_cmd}",
                confidence=1.0,
            )

        return None

    # ───────────────────────────────────────────────────────
    #  Pattern matching
    # ───────────────────────────────────────────────────────

    def _check_patterns(self, command: str) -> Dict[str, Any]:
        """Проверяет команду по всем паттернам безопасности.

        Args:
            command: Команда для проверки.

        Returns:
            Словарь с результатами: matched_patterns, threats,
            max_risk_level, min_confidence.
        """
        matched: List[SecurityPattern] = []
        threats: List[ThreatType] = []

        # Проверяем основные паттерны
        for pattern in self.patterns:
            if pattern.matches(command):
                matched.append(pattern)
                if pattern.threat_type not in threats:
                    threats.append(pattern.threat_type)

        # Проверяем дополнительные блокированные паттерны
        extra_matched = False
        for compiled in self._extra_blocked:
            if compiled.search(command):
                extra_matched = True
                break

        # Определяем максимальный риск
        max_risk = RiskLevel.NONE
        min_confidence = 1.0
        if matched:
            max_risk = RiskLevel(max(p.risk_level for p in matched))
            min_confidence = min(p.confidence for p in matched)

        if extra_matched and max_risk < RiskLevel.HIGH:
            max_risk = RiskLevel.HIGH

        return {
            "matched_patterns": matched,
            "threats": threats,
            "max_risk": max_risk,
            "min_confidence": min_confidence,
            "extra_blocked": extra_matched,
        }

    # ───────────────────────────────────────────────────────
    #  LLM-анализ
    # ───────────────────────────────────────────────────────

    def _analyze_with_llm(
        self,
        command: str,
        context: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Анализирует команду с помощью LLM.

        Args:
            command: Команда для анализа.
            context: Дополнительный контекст.

        Returns:
            Словарь с risk_level и reason от LLM, или None при ошибке.
        """
        if self.llm_fn is None:
            return None

        self._stats["llm_checks"] += 1

        prompt = SAFETY_ANALYSIS_PROMPT.format(command=command)
        if context:
            prompt += f"\nКонтекст: {context}"

        try:
            response = self.llm_fn(prompt)
            return self._parse_llm_response(response)
        except Exception as e:
            logger.warning("LLM safety analysis failed: %s", e)
            return None

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """Парсит ответ LLM-валидатора.

        Ожидаемый формат: RISK:<число> REASON:<причина>

        Args:
            response: Текстовый ответ LLM.

        Returns:
            Словарь с risk_level (int) и reason (str).
        """
        result = {"risk_level": 3, "reason": "LLM анализ неопределён (fail-safe)"}

        # Ищем RISK:<число>
        risk_match = re.search(r"RISK:\s*(\d)", response)
        if risk_match:
            risk = int(risk_match.group(1))
            result["risk_level"] = min(risk, 5)  # Ограничиваем 0-5

        # Ищем REASON:<текст>
        reason_match = re.search(r"REASON:\s*(.+)", response)
        if reason_match:
            result["reason"] = reason_match.group(1).strip()

        return result

    # ───────────────────────────────────────────────────────
    #  Комбинирование результатов
    # ───────────────────────────────────────────────────────

    def _combine_results(
        self,
        pattern_result: Dict[str, Any],
        llm_result: Optional[Dict[str, Any]],
    ) -> SafetyVerdict:
        """Комбинирует результаты паттернов и LLM.

        Стратегия: берём максимальный risk_level из обоих источников.
        Confidence уменьшается, если источники противоречат друг другу.

        Args:
            pattern_result: Результат pattern matching.
            llm_result: Результат LLM-анализа (или None).

        Returns:
            Объединённый SafetyVerdict.
        """
        matched = pattern_result["matched_patterns"]
        threats = pattern_result["threats"]
        max_risk = pattern_result["max_risk"]
        confidence = pattern_result["min_confidence"]

        # Если LLM доступен, комбинируем
        llm_analysis_text = None
        if llm_result is not None:
            llm_risk = llm_result["risk_level"]
            llm_analysis_text = llm_result["reason"]

            # Берём максимум из паттернов и LLM
            if llm_risk > max_risk:
                max_risk = RiskLevel(min(llm_risk, 5))

            # Если паттерны и LLM не согласны, снижаем confidence
            if matched and llm_risk <= 1:
                confidence *= 0.7  # LLM считает безопасным, паттерны нет
            elif not matched and llm_risk >= 3:
                confidence = 0.6  # LLM считает опасным, паттерны молчат

        # Если нет совпадений (ни паттерны, ни LLM не нашли угроз)
        if max_risk <= RiskLevel.LOW and not matched:
            return self._make_verdict(
                safe=True,
                risk_level=int(max_risk),
                reason="Паттерны угроз не обнаружены",
                confidence=confidence,
                threats=threats,
                matched_patterns=[p.name for p in matched],
                llm_analysis=llm_analysis_text,
            )

        # Формируем причину блокировки
        if matched:
            reasons = [p.description for p in matched[:3]]
            reason = "; ".join(reasons)
        elif llm_analysis_text:
            reason = f"LLM: {llm_analysis_text}"
        else:
            reason = f"Уровень риска: {max_risk}"

        return self._make_verdict(
            safe=(max_risk < RiskLevel.HIGH),  # safe если risk < 3
            risk_level=int(max_risk),
            reason=reason,
            confidence=confidence,
            threats=threats,
            matched_patterns=[p.name for p in matched],
            llm_analysis=llm_analysis_text,
        )

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    @staticmethod
    def _make_verdict(
        safe: bool,
        risk_level: int,
        reason: str,
        confidence: float,
        threats: Optional[List[ThreatType]] = None,
        matched_patterns: Optional[List[str]] = None,
        llm_analysis: Optional[str] = None,
    ) -> SafetyVerdict:
        """Создаёт SafetyVerdict с дефолтами."""
        return SafetyVerdict(
            safe=safe,
            risk_level=risk_level,
            reason=reason,
            confidence=confidence,
            threats=threats or [],
            matched_patterns=matched_patterns or [],
            llm_analysis=llm_analysis,
        )

    def validate_batch(
        self,
        commands: List[str],
        use_llm: bool = False,
    ) -> List[SafetyVerdict]:
        """Проверяет список команд.

        Args:
            commands: Список команд.
            use_llm: Использовать LLM.

        Returns:
            Список SafetyVerdict для каждой команды.
        """
        return [self.validate(cmd, use_llm=use_llm) for cmd in commands]

    def get_stats(self) -> Dict[str, int]:
        """Возвращает статистику проверок.

        Returns:
            Словарь со счётчиками проверок.
        """
        return dict(self._stats)

    def reset_stats(self) -> None:
        """Сбрасывает статистику."""
        for key in self._stats:
            self._stats[key] = 0

    def add_pattern(self, pattern: SecurityPattern) -> None:
        """Добавляет паттерн безопасности.

        Args:
            pattern: Паттерн для добавления.
        """
        self.patterns.append(pattern)

    def add_safe_prefix(self, prefix: str) -> None:
        """Добавляет безопасный префикс команды.

        Args:
            prefix: Префикс для добавления (например, 'pip').
        """
        self.safe_prefixes.add(prefix)
