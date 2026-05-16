# -*- coding: utf-8 -*-
"""
Lina Planning — Планировщик (Planner).

Создаёт планы из целей пользователя:
  1. Template-based — для типовых задач (быстро, без LLM)
  2. LLM-based — для произвольных целей (через модель)

Формат плана:
  {
    "goal": "...",
    "steps": [
      {"id": 1, "description": "...", "type": "shell", "expected_result": "..."},
      ...
    ]
  }

Phase 9 — Controlled Autonomous Runtime.
"""

import json
import logging
import re
from typing import Optional, Dict, Any, List, Callable

from lina.planning.state import Plan, PlanStep, StepType

logger = logging.getLogger("lina.planning.planner")


# ═══════════════════════════════════════════════════════════
#  Промпт для LLM-планировщика
# ═══════════════════════════════════════════════════════════

PLANNER_PROMPT = """Ты — модуль планирования AI-ассистента Lina.
Создай пошаговый план для выполнения задачи.

Цель: {goal}

Допустимые типы шагов:
- shell: системная команда (ls, cat, grep, apt, pip)
- macro: макрос Lina (проверка_системы, полная_индексация)
- rag: поиск по базе знаний
- cv: скриншот / OCR / GUI detection
- llm: вопрос к LLM

Правила:
- Максимум {max_steps} шагов
- Каждый шаг должен быть конкретным и проверяемым
- Для shell указывай точную команду

Ответь в JSON формате:
{{"goal": "...", "steps": [{{"id": 1, "description": "...", "type": "shell", "command": "...", "expected_result": "..."}}]}}"""


# ═══════════════════════════════════════════════════════════
#  Шаблоны типовых планов
# ═══════════════════════════════════════════════════════════

PLAN_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "system_check": {
        "goal": "Проверка состояния системы",
        "steps": [
            {"id": 1, "description": "Проверить свободное место",
             "type": "shell", "command": "df -h /",
             "expected_result": "Таблица разделов"},
            {"id": 2, "description": "Проверить RAM",
             "type": "shell", "command": "free -h",
             "expected_result": "Использование памяти"},
            {"id": 3, "description": "Проверить нагрузку CPU",
             "type": "shell", "command": "uptime",
             "expected_result": "Load average"},
        ],
    },
    "install_package": {
        "goal": "Установка пакета",
        "steps": [
            {"id": 1, "description": "Проверить, установлен ли пакет",
             "type": "shell", "command": "which {package}",
             "expected_result": "Путь или пустой вывод"},
            {"id": 2, "description": "Обновить репозитории",
             "type": "shell", "command": "sudo pacman -Sy",
             "expected_result": "Синхронизация завершена"},
            {"id": 3, "description": "Установить пакет",
             "type": "shell", "command": "sudo pacman -S --noconfirm {package}",
             "expected_result": "Пакет установлен"},
        ],
    },
    "file_analysis": {
        "goal": "Анализ файла",
        "steps": [
            {"id": 1, "description": "Проверить существование файла",
             "type": "shell", "command": "test -f {file} && echo exists",
             "expected_result": "exists"},
            {"id": 2, "description": "Получить информацию о файле",
             "type": "shell", "command": "file {file} && wc -l {file}",
             "expected_result": "Тип и размер файла"},
            {"id": 3, "description": "Показать содержимое",
             "type": "shell", "command": "head -20 {file}",
             "expected_result": "Первые 20 строк"},
        ],
    },
    "network_check": {
        "goal": "Проверка сети",
        "steps": [
            {"id": 1, "description": "Проверить интерфейсы",
             "type": "shell", "command": "ip addr show",
             "expected_result": "Список интерфейсов"},
            {"id": 2, "description": "Проверить DNS",
             "type": "shell", "command": "cat /etc/resolv.conf",
             "expected_result": "DNS серверы"},
            {"id": 3, "description": "Пинг внешнего сервера",
             "type": "shell", "command": "ping -c 2 8.8.8.8",
             "expected_result": "Успешный пинг"},
        ],
    },
    "project_analysis": {
        "goal": "Анализ проекта",
        "steps": [
            {"id": 1, "description": "Структура проекта",
             "type": "shell", "command": "find . -name '*.py' | head -20",
             "expected_result": "Список Python файлов"},
            {"id": 2, "description": "Подсчёт строк кода",
             "type": "shell", "command": "find . -name '*.py' | xargs wc -l | tail -1",
             "expected_result": "Общее количество строк"},
            {"id": 3, "description": "Поиск по базе знаний",
             "type": "rag", "command": "архитектура проекта",
             "expected_result": "Описание архитектуры"},
        ],
    },
}

# Ключевые слова для автоматического выбора шаблона
TEMPLATE_KEYWORDS: Dict[str, List[str]] = {
    "system_check": [
        "провер", "систем", "состояни", "диагност", "статус",
        "здоровье", "health",
    ],
    "install_package": [
        "установ", "install", "пакет", "package",
    ],
    "file_analysis": [
        "файл", "file", "анализ", "прочит", "открой",
    ],
    "network_check": [
        "сет", "network", "интернет", "пинг", "dns",
        "подключен", "connect",
    ],
    "project_analysis": [
        "проект", "project", "код", "code", "структур",
        "архитектур",
    ],
}


# ═══════════════════════════════════════════════════════════
#  Planner
# ═══════════════════════════════════════════════════════════

class Planner:
    """Планировщик многошаговых задач.

    Создаёт планы для целей пользователя:
      1. Сначала проверяет шаблоны (быстро, без LLM)
      2. Если шаблон не найден и есть LLM — генерирует план
      3. Иначе — возвращает базовый план с одним LLM-шагом

    Attributes:
        llm_fn: Функция для генерации плана через LLM.
        max_steps: Максимальное количество шагов.
        templates: Реестр шаблонов.
    """

    def __init__(
        self,
        llm_fn: Optional[Callable[[str], str]] = None,
        max_steps: int = 10,
        templates: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """Инициализация планировщика.

        Args:
            llm_fn: Функция генерации (query → response).
                     Если None — только шаблоны.
            max_steps: Максимальное количество шагов в плане.
            templates: Пользовательские шаблоны (добавляются к встроенным).
        """
        self.llm_fn = llm_fn
        self.max_steps = max_steps
        self.templates = dict(PLAN_TEMPLATES)
        if templates:
            self.templates.update(templates)

        self._stats = {
            "plans_created": 0,
            "template_hits": 0,
            "llm_plans": 0,
            "fallback_plans": 0,
        }

    # ───────────────────────────────────────────────────────
    #  Создание плана
    # ───────────────────────────────────────────────────────

    def create_plan(
        self,
        goal: str,
        params: Optional[Dict[str, str]] = None,
        force_llm: bool = False,
    ) -> Plan:
        """Создаёт план для достижения цели.

        Стратегия выбора:
          1. force_llm=True → LLM-план (если llm_fn доступен)
          2. Шаблон подходит → template-based план
          3. LLM доступен → LLM-план
          4. Fallback → одношаговый LLM-план

        Args:
            goal: Цель пользователя (естественным языком).
            params: Параметры для шаблона (например, {package: "vim"}).
            force_llm: Принудительно использовать LLM.

        Returns:
            Plan с шагами для выполнения.
        """
        self._stats["plans_created"] += 1
        params = params or {}

        # Стратегия 1: Принудительно LLM
        if force_llm and self.llm_fn is not None:
            plan = self._create_llm_plan(goal)
            if plan is not None:
                return plan

        # Стратегия 2: Подбор шаблона
        template_plan = self._match_template(goal, params)
        if template_plan is not None:
            self._stats["template_hits"] += 1
            logger.debug("Template plan matched for goal: %s", goal[:50])
            return template_plan

        # Стратегия 3: LLM-план
        if self.llm_fn is not None and not force_llm:
            plan = self._create_llm_plan(goal)
            if plan is not None:
                return plan

        # Стратегия 4: Fallback
        self._stats["fallback_plans"] += 1
        logger.debug("Fallback plan for goal: %s", goal[:50])
        return self._create_fallback_plan(goal)

    # ───────────────────────────────────────────────────────
    #  Template matching
    # ───────────────────────────────────────────────────────

    def _match_template(
        self,
        goal: str,
        params: Dict[str, str],
    ) -> Optional[Plan]:
        """Ищет подходящий шаблон по ключевым словам.

        Args:
            goal: Цель пользователя.
            params: Параметры для подстановки.

        Returns:
            Plan из шаблона или None.
        """
        goal_lower = goal.lower()

        best_match: Optional[str] = None
        best_score = 0

        for template_name, keywords in TEMPLATE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in goal_lower)
            if score > best_score:
                best_score = score
                best_match = template_name

        if best_match is None or best_score == 0:
            return None

        template = self.templates.get(best_match)
        if template is None:
            return None

        return self._instantiate_template(template, goal, params)

    def _instantiate_template(
        self,
        template: Dict[str, Any],
        goal: str,
        params: Dict[str, str],
    ) -> Plan:
        """Создаёт план из шаблона с подставленными параметрами.

        Args:
            template: Шаблон плана.
            goal: Цель (переопределяет шаблон).
            params: Параметры для подстановки ({package}, {file}).

        Returns:
            Готовый Plan.
        """
        steps = []
        for step_data in template["steps"]:
            # Подставляем параметры в команды и описания
            description = step_data["description"]
            command = step_data.get("command", "")
            expected = step_data.get("expected_result", "")

            for key, value in params.items():
                import shlex
                safe_value = shlex.quote(value)
                description = description.replace(f"{{{key}}}", value)
                command = command.replace(f"{{{key}}}", safe_value)
                expected = expected.replace(f"{{{key}}}", value)

            steps.append(PlanStep(
                id=step_data["id"],
                description=description,
                step_type=StepType(step_data.get("type", "shell")),
                expected_result=expected,
                command=command,
            ))

        return Plan(
            goal=goal,
            steps=steps,
            max_steps=self.max_steps,
        )

    # ───────────────────────────────────────────────────────
    #  LLM Planning
    # ───────────────────────────────────────────────────────

    def _create_llm_plan(self, goal: str) -> Optional[Plan]:
        """Создаёт план через LLM.

        Args:
            goal: Цель пользователя.

        Returns:
            Plan или None при ошибке парсинга.
        """
        if self.llm_fn is None:
            return None

        self._stats["llm_plans"] += 1

        prompt = PLANNER_PROMPT.format(
            goal=goal,
            max_steps=self.max_steps,
        )

        try:
            response = self.llm_fn(prompt)
            return self._parse_llm_plan(response, goal)
        except Exception as e:
            logger.warning("LLM planning failed: %s", e)
            return None

    def _parse_llm_plan(
        self,
        response: str,
        original_goal: str,
    ) -> Optional[Plan]:
        """Парсит ответ LLM в структуру плана.

        Пытается извлечь JSON из ответа LLM.

        Args:
            response: Текст ответа LLM.
            original_goal: Исходная цель (fallback).

        Returns:
            Plan или None при ошибке.
        """
        # Ищем JSON в ответе
        json_match = re.search(r"\{[\s\S]*\}", response)
        if not json_match:
            logger.warning("No JSON found in LLM plan response")
            return None

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.warning("JSON parse error in plan: %s", e)
            return None

        # Извлекаем цель
        goal = data.get("goal", original_goal)

        # Извлекаем шаги
        steps_data = data.get("steps", [])
        if not steps_data:
            logger.warning("Empty steps in LLM plan")
            return None

        steps = []
        for i, step_data in enumerate(steps_data[:self.max_steps]):
            step_type_str = step_data.get("type", "shell")
            try:
                step_type = StepType(step_type_str)
            except ValueError:
                step_type = StepType.LLM

            steps.append(PlanStep(
                id=step_data.get("id", i + 1),
                description=step_data.get("description", f"Шаг {i+1}"),
                step_type=step_type,
                expected_result=step_data.get("expected_result", ""),
                command=step_data.get("command", ""),
            ))

        return Plan(
            goal=goal,
            steps=steps,
            max_steps=self.max_steps,
        )

    # ───────────────────────────────────────────────────────
    #  Fallback
    # ───────────────────────────────────────────────────────

    def _create_fallback_plan(self, goal: str) -> Plan:
        """Создаёт минимальный fallback-план.

        Одношаговый план — запрос к LLM.

        Args:
            goal: Цель пользователя.

        Returns:
            Plan с одним шагом.
        """
        return Plan(
            goal=goal,
            steps=[
                PlanStep(
                    id=1,
                    description=f"Выполнить: {goal}",
                    step_type=StepType.LLM,
                    expected_result="Ответ от LLM",
                    command=goal,
                ),
            ],
            max_steps=self.max_steps,
        )

    # ───────────────────────────────────────────────────────
    #  Утилиты
    # ───────────────────────────────────────────────────────

    def get_template_names(self) -> List[str]:
        """Возвращает список доступных шаблонов.

        Returns:
            Список названий шаблонов.
        """
        return list(self.templates.keys())

    def add_template(
        self,
        name: str,
        template: Dict[str, Any],
    ) -> None:
        """Добавляет пользовательский шаблон.

        Args:
            name: Название шаблона.
            template: Словарь шаблона (goal + steps).
        """
        self.templates[name] = template

    def get_stats(self) -> Dict[str, int]:
        """Возвращает статистику планирования.

        Returns:
            Словарь со счётчиками.
        """
        return dict(self._stats)
