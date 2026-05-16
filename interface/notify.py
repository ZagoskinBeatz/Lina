"""
Lina — KDE/Wayland уведомления и GUI-виджеты.

Интеграция с рабочим столом:
  - Всплывающие уведомления (notify-send или D-Bus)
  - Иконки статуса (tray icon через Qt/GTK)
  - Визуализация загрузки ресурсов

Работает на:
  - KDE Plasma (Wayland/X11) — через notify-send
  - GNOME — через notify-send
  - i3/sway — через dunst/mako
"""

import re
import subprocess
import shutil
import json
from pathlib import Path
from typing import Optional, Dict

_STRIP_HTML_RE = re.compile(r"<[^>]+>")


class NotifyLevel:
    """Уровни уведомлений."""
    INFO = "normal"
    WARNING = "critical"
    ERROR = "critical"
    SUCCESS = "low"


class DesktopNotifier:
    """
    Менеджер уведомлений рабочего стола.

    Использует notify-send (libnotify) для отправки уведомлений.
    Совместим с KDE Plasma, GNOME, i3/sway + dunst/mako.
    """

    APP_NAME = "Lina AI"
    DEFAULT_ICON = "dialog-information"
    ICONS = {
        "info": "dialog-information",
        "warning": "dialog-warning",
        "error": "dialog-error",
        "success": "emblem-default",
        "llm": "applications-science",
        "rag": "folder-documents",
        "system": "preferences-system",
    }

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._has_notify_send = shutil.which("notify-send") is not None
        self._has_dbus = self._check_dbus()

    def _check_dbus(self) -> bool:
        """Проверяет доступность D-Bus."""
        try:
            result = subprocess.run(
                ["dbus-send", "--session", "--print-reply",
                 "--dest=org.freedesktop.DBus",
                 "/org/freedesktop/DBus",
                 "org.freedesktop.DBus.ListNames"],
                capture_output=True, timeout=2,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @property
    def available(self) -> bool:
        """Доступны ли уведомления."""
        return self.enabled and self._has_notify_send

    def notify(
        self,
        title: str,
        body: str = "",
        urgency: str = NotifyLevel.INFO,
        icon: str = "info",
        timeout_ms: int = 5000,
    ) -> bool:
        """
        Отправляет уведомление на рабочий стол.

        Args:
            title: Заголовок уведомления.
            body: Текст уведомления.
            urgency: Уровень важности.
            icon: Ключ иконки из ICONS.
            timeout_ms: Время показа (мс).

        Returns:
            True если уведомление отправлено.
        """
        if not self.available:
            return False

        icon_name = self.ICONS.get(icon, self.DEFAULT_ICON)

        safe_title = _STRIP_HTML_RE.sub("", title)
        safe_body = _STRIP_HTML_RE.sub("", body) if body else ""

        cmd = [
            "notify-send",
            "--app-name", self.APP_NAME,
            "--urgency", urgency,
            "--icon", icon_name,
            "--expire-time", str(timeout_ms),
            safe_title,
        ]
        if safe_body:
            cmd.append(safe_body[:500])  # Ограничиваем длину

        try:
            subprocess.run(cmd, capture_output=True, timeout=3)
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # ── Удобные методы ──

    def info(self, title: str, body: str = "") -> bool:
        return self.notify(title, body, NotifyLevel.INFO, "info")

    def success(self, title: str, body: str = "") -> bool:
        return self.notify(title, body, NotifyLevel.SUCCESS, "success")

    def warning(self, title: str, body: str = "") -> bool:
        return self.notify(title, body, NotifyLevel.WARNING, "warning")

    def error(self, title: str, body: str = "") -> bool:
        return self.notify(title, body, NotifyLevel.ERROR, "error")

    def llm_loaded(self, tier: str) -> bool:
        """Уведомление о загрузке модели."""
        label = "полная"
        return self.notify(
            f"🤖 Модель загружена",
            f"LLM: {label} модель готова к работе",
            icon="llm",
        )

    def llm_unloaded(self) -> bool:
        """Уведомление о выгрузке модели."""
        return self.notify(
            "♻ Модель выгружена",
            "LLM выгружена из памяти",
            icon="llm",
            urgency=NotifyLevel.SUCCESS,
        )

    def index_complete(self, chunks: int) -> bool:
        """Уведомление о завершении индексации."""
        return self.notify(
            "📚 Индексация завершена",
            f"Проиндексировано {chunks} чанков",
            icon="rag",
        )

    def chain_complete(self, steps: int, ok: int) -> bool:
        """Уведомление о завершении цепочки."""
        return self.notify(
            "⚡ Цепочка завершена",
            f"{ok}/{steps} шагов выполнено",
            icon="success" if ok == steps else "warning",
        )

    def resource_warning(self, message: str) -> bool:
        """Уведомление о проблемах с ресурсами."""
        return self.notify(
            "⚠ Ресурсы",
            message,
            urgency=NotifyLevel.WARNING,
            icon="system",
            timeout_ms=10000,
        )

    def get_status(self) -> dict:
        """Информация о системе уведомлений."""
        return {
            "enabled": self.enabled,
            "notify_send": self._has_notify_send,
            "dbus": self._has_dbus,
            "available": self.available,
        }


class StatusWidget:
    """
    Текстовый виджет статуса для терминала.

    Рисует компактный статус-бар с информацией о состоянии
    всех подсистем Lina.
    """

    @staticmethod
    def render(
        llm_tier: Optional[str] = None,
        rag_chunks: int = 0,
        cpu_percent: float = 0,
        ram_percent: float = 0,
        notifications: bool = False,
        web_active: bool = False,
    ) -> str:
        """
        Рендерит компактный статус-бар.

        Returns:
            Строка статус-бара для отображения.
        """
        parts = []

        # LLM
        if llm_tier == "full":
            parts.append("🔵full")
        elif llm_tier:
            parts.append("🔵full")
        else:
            parts.append("⬜off")

        # RAG
        parts.append(f"📚{rag_chunks}")

        # Resources
        cpu_icon = "🔴" if cpu_percent > 80 else "🟡" if cpu_percent > 50 else "🟢"
        parts.append(f"{cpu_icon}CPU:{cpu_percent:.0f}%")

        ram_icon = "🔴" if ram_percent > 80 else "🟡" if ram_percent > 50 else "🟢"
        parts.append(f"{ram_icon}RAM:{ram_percent:.0f}%")

        # Optional features
        if notifications:
            parts.append("🔔")
        if web_active:
            parts.append("🌐")

        return " │ ".join(parts)
