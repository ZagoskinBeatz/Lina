"""
Lina — Модуль изоляции вывода (Output Isolation).

Определяет режим работы (TTY / Pipe / CI) и предоставляет
безопасный вывод, совместимый со всеми оболочками (bash, fish, zsh).

Решает проблему:
  Fish shell интерпретирует emoji (🟢, ⚠) в stdout как команды,
  если Lina запускается через eval, source, или pipe.

Архитектура:
  - OutputMode: enum режимов (TTY, PIPE, CI)
  - detect_output_mode(): автодетекция среды
  - SafePrinter: обёртка print() с фильтрацией emoji в небезопасных режимах
  - format_status(): безопасное форматирование статусных строк
"""

import os
import sys
import logging
import threading
from enum import Enum, auto
from typing import Optional, TextIO

logger = logging.getLogger("lina.core.output")


# ─── Режимы вывода ─────────────────────────────────────────────────────────────

class OutputMode(Enum):
    """
    Режим вывода Lina.

    TTY  — интерактивный терминал (emoji разрешены).
    PIPE — stdout перенаправлен в pipe/файл (emoji заменяются на ASCII).
    CI   — CI/CD окружение (минимальный вывод, JSON-совместимый).
    """
    TTY = auto()
    PIPE = auto()
    CI = auto()


# ─── Emoji → ASCII маппинг для безопасного режима ──────────────────────────────

_EMOJI_TO_ASCII = {
    "🟢": "[OK]",
    "🔵": "[INFO]",
    "⚠": "[WARN]",
    "❌": "[ERR]",
    "✅": "[OK]",
    "⏳": "[...]",
    "♻": "[UNLOAD]",
    "🔄": "[SWITCH]",
    "🗑": "[CLEAR]",
    "💻": "[SYS]",
    "📁": "[DIR]",
    "📚": "[INDEX]",
    "🐧": "[LINUX]",
    "👁": "[CV]",
    "🌐": "[WEB]",
    "🔔": "[NOTIFY]",
    "👋": "[BYE]",
    "ℹ": "[i]",
}

# Pre-compiled single-pass regex for emoji sanitization (O(n) instead of O(n*m))
import re as _re
_EMOJI_PATTERN = _re.compile("|".join(_re.escape(k) for k in _EMOJI_TO_ASCII))


def detect_output_mode() -> OutputMode:
    """
    Определяет режим вывода на основе окружения.

    Логика:
      1. Если установлена переменная CI/GITHUB_ACTIONS/JENKINS_URL → CI
      2. Если stdout не TTY (pipe/file redirect) → PIPE
      3. Иначе → TTY (интерактивный терминал)

    Returns:
        OutputMode — текущий режим вывода.
    """
    # CI/CD-среды
    ci_vars = ("CI", "GITHUB_ACTIONS", "JENKINS_URL", "GITLAB_CI",
               "TRAVIS", "CIRCLECI", "LINA_CI")
    for var in ci_vars:
        if os.environ.get(var):
            logger.debug("Output mode: CI (env var %s detected)", var)
            return OutputMode.CI

    # Принудительный режим через переменную окружения
    forced = os.environ.get("LINA_OUTPUT_MODE", "").upper()
    if forced == "CI":
        return OutputMode.CI
    if forced == "PIPE":
        return OutputMode.PIPE
    if forced == "TTY":
        return OutputMode.TTY

    # Автодетекция по isatty()
    if not sys.stdout.isatty():
        logger.debug("Output mode: PIPE (stdout is not a TTY)")
        return OutputMode.PIPE

    logger.debug("Output mode: TTY (interactive terminal)")
    return OutputMode.TTY


def sanitize_text(text: str, mode: OutputMode) -> str:
    """
    Очищает текст от emoji для небезопасных режимов.

    В режиме TTY — текст не изменяется.
    В режимах PIPE/CI — emoji заменяются на ASCII-эквиваленты.

    Args:
        text: Исходный текст с возможными emoji.
        mode: Текущий режим вывода.

    Returns:
        Безопасный текст.
    """
    if mode == OutputMode.TTY:
        return text

    return _EMOJI_PATTERN.sub(lambda m: _EMOJI_TO_ASCII[m.group()], text)


class SafePrinter:
    """
    Безопасный принтер с автоматической фильтрацией emoji.

    Заменяет стандартный print() в контексте Lina.
    В TTY-режиме — полный вывод с emoji.
    В PIPE/CI — emoji заменяются на ASCII, flush=True.

    Использование:
        printer = SafePrinter()
        printer.print("🟢 Модель загружена")
        # TTY:  "🟢 Модель загружена"
        # PIPE: "[OK] Модель загружена"

        printer.status("OK", "Модель загружена")
        # TTY:  "  🟢 Модель загружена"
        # PIPE: "  [OK] Модель загружена"
    """

    def __init__(self, mode: Optional[OutputMode] = None,
                 stream: Optional[TextIO] = None):
        """
        Args:
            mode: Режим вывода (None = автодетекция).
            stream: Поток вывода (None = sys.stdout).
        """
        self._mode = mode if mode is not None else detect_output_mode()
        self._stream = stream or sys.stdout
        self._quiet = False  # Подавление вывода (для тестов/CI)

    @property
    def mode(self) -> OutputMode:
        """Текущий режим вывода."""
        return self._mode

    @mode.setter
    def mode(self, value: OutputMode) -> None:
        """Устанавливает режим вывода."""
        self._mode = value

    @property
    def quiet(self) -> bool:
        """Флаг тихого режима (без вывода)."""
        return self._quiet

    @quiet.setter
    def quiet(self, value: bool) -> None:
        """Устанавливает тихий режим."""
        self._quiet = value

    @property
    def is_tty(self) -> bool:
        """True если вывод идёт в интерактивный терминал."""
        return self._mode == OutputMode.TTY

    @property
    def is_pipe(self) -> bool:
        """True если stdout перенаправлен."""
        return self._mode == OutputMode.PIPE

    @property
    def is_ci(self) -> bool:
        """True если запущен в CI/CD."""
        return self._mode == OutputMode.CI

    def print(self, *args, **kwargs) -> None:
        """
        Безопасный print() с фильтрацией emoji.

        Аргументы аналогичны встроенному print().
        В PIPE/CI-режимах: emoji → ASCII, flush=True.
        """
        if self._quiet:
            return

        # Принудительный flush в небезопасных режимах
        if self._mode != OutputMode.TTY:
            kwargs.setdefault("flush", True)

        # Преобразуем аргументы
        safe_args = []
        for arg in args:
            text = str(arg)
            safe_args.append(sanitize_text(text, self._mode))

        # Устанавливаем поток вывода
        kwargs.setdefault("file", self._stream)

        print(*safe_args, **kwargs)

    def status(self, icon: str, message: str, indent: int = 2) -> None:
        """
        Выводит статусную строку с иконкой.

        Args:
            icon: Emoji-иконка (🟢, ❌, ⚠, ...).
            message: Текст сообщения.
            indent: Отступ (пробелы).
        """
        prefix = " " * indent
        safe_icon = sanitize_text(icon, self._mode)
        self.print(f"{prefix}{safe_icon} {message}")

    def banner(self, text: str) -> None:
        """
        Выводит баннер (ASCII-арт). В CI-режиме — подавляется.

        Args:
            text: Текст баннера (многострочный).
        """
        if self._mode == OutputMode.CI:
            return
        self.print(text)

    def separator(self, char: str = "─", width: int = 55) -> None:
        """Выводит разделительную линию."""
        if self._mode == OutputMode.CI:
            return
        self.print(char * width)

    def prompt_text(self, label: str = "Lina") -> str:
        """
        Возвращает безопасный текст приглашения ввода.

        В TTY-режиме — с emoji.
        В PIPE/CI — без emoji.

        Args:
            label: Имя в приглашении.

        Returns:
            Строка приглашения.
        """
        if self._mode == OutputMode.TTY:
            return f"\n🟢 {label}> "
        return f"\n{label}> "


# ─── Глобальный экземпляр ──────────────────────────────────────────────────────

# Создаётся при первом импорте. Модули используют его вместо print().
_printer: Optional[SafePrinter] = None
_printer_lock = threading.Lock()


def get_printer() -> SafePrinter:
    """
    Возвращает глобальный SafePrinter (singleton, thread-safe).

    Создаёт экземпляр при первом вызове с автодетекцией режима.

    Returns:
        SafePrinter — глобальный безопасный принтер.
    """
    global _printer
    if _printer is None:
        with _printer_lock:
            if _printer is None:
                _printer = SafePrinter()
    return _printer


def reset_printer(mode: Optional[OutputMode] = None) -> SafePrinter:
    """
    Пересоздаёт глобальный SafePrinter (для тестов).

    Args:
        mode: Принудительный режим (None = автодетекция).

    Returns:
        Новый SafePrinter.
    """
    global _printer
    with _printer_lock:
        _printer = SafePrinter(mode=mode)
    return _printer
