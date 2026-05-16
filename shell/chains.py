"""
Lina — Цепочки команд и макросы.

Позволяет выполнять несколько действий одной командой:
  «собери проект → проверь ошибки → дай рекомендации»

Функции:
  - Парсинг цепочек из строки (→ или ; или |)
  - Пошаговое исполнение с передачей контекста
  - Маршрутизация промежуточных шагов через full LLM,
    финальный анализ — через full LLM
  - Сохранение / загрузка макросов из файлов
  - Встроенные макросы для частых сценариев
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import List, Optional, Callable, Dict

from lina.config import config, KNOWLEDGE_DIR


MACROS_DIR = KNOWLEDGE_DIR / "macros"
MACROS_DIR.mkdir(parents=True, exist_ok=True)

# Разделители цепочек
CHAIN_SEPARATORS = re.compile(r'\s*(?:→|->|=>|;\s*затем\s+|;\s*потом\s+|;)\s*')

logger = logging.getLogger(__name__)


class ChainStep:
    """Один шаг цепочки."""

    def __init__(
        self,
        command: str,
        tier: Optional[str] = None,
        label: str = "",
    ):
        self.command = command.strip()
        self.tier = tier  # "full", None=авто
        self.label = label or self.command[:40]
        self.result: Optional[str] = None
        self.success: bool = False
        self.elapsed: float = 0.0

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "tier": self.tier,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChainStep":
        return cls(
            command=d["command"],
            tier=d.get("tier"),
            label=d.get("label", ""),
        )


class CommandChain:
    """Цепочка команд для последовательного выполнения."""

    def __init__(self, name: str = "", steps: Optional[List[ChainStep]] = None):
        self.name = name
        self.steps: List[ChainStep] = steps or []
        self.created_at = time.time()

    @classmethod
    def parse(cls, text: str) -> "CommandChain":
        """
        Парсит цепочку из строки.

        Поддерживаемые разделители:
          - → (стрелка)
          - -> (ASCII стрелка)
          - => (двойная стрелка)
          - ; затем / ; потом / ;

        Пример:
          "покажи файлы . → статус системы → объясни результаты"
        """
        parts = CHAIN_SEPARATORS.split(text)
        steps = [ChainStep(command=p) for p in parts if p.strip()]

        # Последний шаг — финальный анализ → full модель
        if len(steps) > 1:
            steps[-1].tier = "full"

        return cls(name=text[:50], steps=steps)

    def is_chain(self) -> bool:
        """Является ли это цепочкой (>1 шаг)."""
        return len(self.steps) > 1

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CommandChain":
        chain = cls(
            name=d["name"],
            steps=[ChainStep.from_dict(s) for s in d.get("steps", [])],
        )
        chain.created_at = d.get("created_at", time.time())
        return chain


class ChainExecutor:
    """
    Выполняет цепочки команд.

    Архитектура:
      Шаг 1 (full) → результат1 → Шаг 2 (full) → результат2 → ... → Шаг N (full, анализ)

    - Все шаги обрабатываются полной моделью
    - Каждый последующий шаг получает контекст предыдущих результатов
    """

    def __init__(self, process_fn: Callable[[str], str]):
        """
        Args:
            process_fn: Функция обработки одной команды (Commander.process).
        """
        self._process = process_fn

    def execute(
        self,
        chain: CommandChain,
        verbose: bool = False,
    ) -> str:
        """
        Выполняет цепочку команд.

        Args:
            chain: Цепочка для выполнения.
            verbose: Подробный вывод.

        Returns:
            Объединённый результат всех шагов.
        """
        if not chain.steps:
            return "Пустая цепочка."

        results = []
        accumulated_context = ""

        total = len(chain.steps)

        for i, step in enumerate(chain.steps, 1):
            step_header = f"[Шаг {i}/{total}]"

            if verbose:
                logger.debug("⚡ %s %s", step_header, step.command)

            start = time.time()

            # Формируем команду с контекстом предыдущих шагов
            if accumulated_context and i > 1:
                # Для LLM-запросов — добавляем контекст
                enriched_cmd = (
                    f"{step.command}\n\n"
                    f"Контекст предыдущих шагов:\n{accumulated_context[-1500:]}"
                )
            else:
                enriched_cmd = step.command

            # Выполняем
            try:
                result = self._process(enriched_cmd)
                step.success = True

                # Обработка служебных ответов
                if result == "__EXIT__":
                    results.append(f"{step_header} ⏹ Выход")
                    break
            except Exception as e:
                logger.error("Chain step %d failed: %s", i, e, exc_info=True)
                result = "❌ Внутренняя ошибка при выполнении шага"
                step.success = False

            step.elapsed = time.time() - start
            step.result = result

            # Собираем результаты
            results.append(f"{step_header} {step.command}\n{result}")
            accumulated_context += f"\n{step_header} {step.command}: {result[:500]}\n"

        # Форматируем итог
        separator = "\n" + "─" * 40 + "\n"
        output = separator.join(results)

        summary_time = sum(s.elapsed for s in chain.steps)
        ok = sum(1 for s in chain.steps if s.success)
        output += f"\n\n📊 Цепочка завершена: {ok}/{total} шагов, {summary_time:.1f} сек."

        return output


class MacroManager:
    """
    Менеджер макросов — сохранённых цепочек команд.

    Макросы хранятся в knowledge/macros/ как JSON-файлы
    и могут быть вызваны по имени.
    """

    # Встроенные макросы
    BUILTIN_MACROS: Dict[str, List[str]] = {
        "проверка_системы": [
            "статус системы",
            "процессы",
            "статус модели",
        ],
        "полная_индексация": [
            "индексируй",
            "статус базы знаний",
        ],
        "диагностика": [
            "статус системы",
            "статус модели",
            "статус базы знаний",
        ],

        # ── Предустановочные макросы ──

        "system_overview": [
            "обзор системы",
        ],
        "partition_assist": [
            "анализ разделов",
        ],
        "network_setup": [
            "сетевая диагностика",
        ],
        "package_suggestions": [
            "рекомендации пакетов",
        ],
        "pre_install_check": [
            "проверка готовности",
        ],
        "installation_guide": [
            "гид установки",
        ],
        "auto_faq_update": [
            "faq установки",
        ],
        "post_install_tune": [
            "тюнинг после установки",
        ],

        # ── CV макросы (Computer Vision) ──

        "pre_install_monitor": [
            "скриншот экрана",
            "распознай текст",
            "найди ошибки на экране",
        ],
        "error_detector": [
            "скриншот экрана",
            "распознай текст",
            "найди ошибки на экране",
            "анализ gui элементов",
        ],
        "auto_click_helper": [
            "скриншот экрана",
            "анализ gui элементов",
            "распознай текст",
        ],
        "gui_assistant": [
            "скриншот экрана",
            "анализ gui элементов",
            "распознай текст",
            "найди ошибки на экране",
        ],
        "install_completion_monitor": [
            "скриншот экрана",
            "распознай текст",
            "найди прогресс",
        ],
        "cv_полный_анализ": [
            "скриншот экрана",
            "распознай текст",
            "найди ошибки на экране",
            "найди прогресс",
            "анализ gui элементов",
        ],
        "cv_статус": [
            "статус cv",
        ],

        # ── Системные и интеграционные макросы ──

        "analyze_project": [
            "дерево каталога .",
            "git статус",
            "статус базы знаний",
        ],
        "generate_docs": [
            "дерево каталога .",
            "статус базы знаний",
            "faq",
        ],
        "faq_builder": [
            "faq",
            "faq установки",
            "статистика обучения",
        ],
        "system_report": [
            "статус системы",
            "процессы",
            "обзор системы",
            "сетевая диагностика",
        ],
        "полная_предустановка": [
            "обзор системы",
            "анализ разделов",
            "сетевая диагностика",
            "проверка готовности",
            "рекомендации пакетов",
            "гид установки",
        ],
        "security_audit": [
            "статус системы",
            "процессы",
            "аудит",
        ],
        "knowledge_rebuild": [
            "очисти базу",
            "индексируй",
            "статус базы знаний",
        ],
        "daily_report": [
            "статус системы",
            "статус модели",
            "статус базы знаний",
            "статистика обучения",
            "аудит 5",
        ],
        "full_diagnostic": [
            "статус системы",
            "процессы",
            "статус модели",
            "статус базы знаний",
            "сетевая диагностика",
            "обзор системы",
        ],
        "quick_check": [
            "статус системы",
            "статус модели",
        ],
        "run_project": [
            "дерево каталога .",
            "git статус",
        ],
        "maintenance": [
            "очисти кэш",
            "индексируй",
            "статус базы знаний",
            "статус системы",
        ],
        "cv_install_watch": [
            "скриншот экрана",
            "распознай текст",
            "найди прогресс",
            "найди ошибки на экране",
        ],
        "setup_helper": [
            "обзор системы",
            "проверка готовности",
            "гид установки",
            "рекомендации пакетов",
        ],
        "отчёт_обучения": [
            "статистика обучения",
            "faq",
            "экспорт знаний",
        ],
        "web_status": [
            "статус системы",
            "инструменты",
        ],
        "hardware_deep_scan": [
            "обзор системы",
            "анализ разделов",
            "проверка готовности",
        ],
        "network_full_check": [
            "сетевая диагностика",
            "проверка готовности",
        ],
        "smart_install": [
            "обзор системы",
            "сетевая диагностика",
            "проверка готовности",
            "гид установки",
            "faq установки",
        ],
        "cv_error_scan": [
            "скриншот экрана",
            "распознай текст",
            "найди ошибки на экране",
        ],
        "backup_check": [
            "дерево каталога .",
            "статус системы",
            "git статус",
            "аудит 10",
        ],
        "fresh_start": [
            "очисти кэш",
            "очисти базу",
            "индексируй",
            "статус базы знаний",
            "статус системы",
        ],
    }

    # Regex for safe macro names: letters (incl. Cyrillic), digits, underscore, hyphen
    _SAFE_MACRO_NAME = re.compile(r'^[a-zA-Zа-яА-ЯёЁ0-9_-]{1,64}$')

    def __init__(self):
        self._macros: Dict[str, CommandChain] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Ленивая загрузка макросов."""
        if not self._loaded:
            self._load_all()
            self._loaded = True

    def get(self, name: str) -> Optional[CommandChain]:
        """Получает макрос по имени."""
        self._ensure_loaded()

        # Сначала пользовательские
        if name in self._macros:
            return self._macros[name]

        # Встроенные
        if name in self.BUILTIN_MACROS:
            steps = [ChainStep(cmd) for cmd in self.BUILTIN_MACROS[name]]
            return CommandChain(name=name, steps=steps)

        return None

    def save_macro(self, name: str, chain: CommandChain) -> None:
        """Сохраняет макрос в файл."""
        if not self._SAFE_MACRO_NAME.match(name):
            raise ValueError(f"Недопустимое имя макроса: {name!r}")
        chain.name = name
        self._macros[name] = chain

        try:
            path = MACROS_DIR / f"{name}.json"
            # Verify resolved path stays inside MACROS_DIR
            if not str(path.resolve()).startswith(str(MACROS_DIR.resolve()) + os.sep):
                raise ValueError("Path traversal detected")
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(chain.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(str(tmp), str(path))
        except IOError as e:
            logger.error("Не удалось сохранить макрос: %s", e)

    def delete_macro(self, name: str) -> bool:
        """Удаляет макрос."""
        if not self._SAFE_MACRO_NAME.match(name):
            return False
        if name in self._macros:
            del self._macros[name]
        path = MACROS_DIR / f"{name}.json"
        if not str(path.resolve()).startswith(str(MACROS_DIR.resolve()) + os.sep):
            return False
        if path.exists():
            path.unlink()
            return True
        return False

    def list_macros(self) -> List[str]:
        """Возвращает список всех доступных макросов."""
        self._ensure_loaded()
        names = set(self._macros.keys())
        names.update(self.BUILTIN_MACROS.keys())
        return sorted(names)

    def format_list(self) -> str:
        """Форматирует список макросов."""
        macros = self.list_macros()
        if not macros:
            return "Нет доступных макросов."

        lines = ["📋 Доступные макросы:"]
        for name in macros:
            is_builtin = name in self.BUILTIN_MACROS
            icon = "📌" if is_builtin else "📎"
            chain = self.get(name)
            steps = len(chain.steps) if chain else 0
            lines.append(f"  {icon} {name} ({steps} шагов)")

        lines.append("\nИспользование: /макрос <имя>")
        return "\n".join(lines)

    def _load_all(self) -> None:
        """Загружает все макросы из файлов."""
        try:
            for path in MACROS_DIR.glob("*.json"):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    chain = CommandChain.from_dict(data)
                    self._macros[path.stem] = chain
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Corrupt macro file %s: %s", path.name, e)
        except OSError as e:
            logger.error("Failed to load macros directory: %s", e)
