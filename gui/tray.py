"""
Lina GUI — System Tray Icon.

Значок в системном трее с меню:
  - Открыть чат
  - Статус
  - Настройки
  - Выход

Совместим с KDE Plasma 6, GNOME, XFCE.
Требует PyQt6 или PySide6.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Callable, List, Dict, Any

logger = logging.getLogger("lina.gui.tray")


# ─── Модель данных (без зависимости от Qt) ────────────────────────────────────

@dataclass
class TrayMenuItem:
    """Элемент меню трея."""
    label: str
    action: Optional[str] = None      # action id для dispatch
    callback: Optional[Callable] = None
    separator_after: bool = False
    enabled: bool = True
    checkable: bool = False
    checked: bool = False
    icon_name: Optional[str] = None


@dataclass
class TrayStatus:
    """Данные для tooltip трея."""
    model_loaded: bool = False
    model_name: str = "не загружена"
    ram_usage_mb: int = 0
    queries_today: int = 0
    uptime_minutes: int = 0

    def format_tooltip(self) -> str:
        """Форматирует tooltip для значка."""
        status = "✓ готов" if self.model_loaded else "○ ожидание"
        lines = [
            f"Lina AI — {status}",
            f"Модель: {self.model_name}",
            f"RAM: {self.ram_usage_mb} MB",
            f"Запросов сегодня: {self.queries_today}",
        ]
        if self.uptime_minutes > 0:
            h, m = divmod(self.uptime_minutes, 60)
            lines.append(f"Аптайм: {h}ч {m}м")
        return "\n".join(lines)


@dataclass
class TrayConfig:
    """Конфигурация System Tray."""
    icon_path: Optional[str] = None        # путь к иконке (None → встроенная)
    show_notifications: bool = True
    notification_timeout_ms: int = 5000
    menu_items: List[TrayMenuItem] = field(default_factory=list)

    def get_default_menu(self) -> List[TrayMenuItem]:
        """Возвращает стандартное меню трея."""
        return [
            TrayMenuItem(label="Открыть чат", action="open_chat",
                         icon_name="chat"),
            TrayMenuItem(label="Обзор системы", action="system_overview",
                         separator_after=True),
            TrayMenuItem(label="Настройки", action="open_settings",
                         icon_name="settings"),
            TrayMenuItem(label="О программе", action="about",
                         separator_after=True),
            TrayMenuItem(label="Выход", action="quit", icon_name="exit"),
        ]


class TrayIconController:
    """Контроллер логики System Tray (без прямой зависимости от Qt).

    Управляет состоянием, меню и событиями.
    Qt-виджет создаётся через create_qt_tray() если Qt доступен.
    """

    def __init__(self, config: Optional[TrayConfig] = None):
        self.config = config or TrayConfig()
        self.status = TrayStatus()
        self._menu_items = self.config.menu_items or self.config.get_default_menu()
        self._callbacks: Dict[str, Callable] = {}
        self._visible = False
        self._notifications: deque = deque(maxlen=200)
        self._qt_tray = None  # QtWidgets.QSystemTrayIcon — устанавливается извне
        logger.info("TrayIconController создан")

    # ── Регистрация обработчиков ──

    def register_action(self, action_id: str, callback: Callable) -> None:
        """Регистрирует обработчик для действия меню."""
        self._callbacks[action_id] = callback
        logger.debug(f"Зарегистрировано действие: {action_id}")

    def register_actions(self, actions: Dict[str, Callable]) -> None:
        """Регистрирует несколько обработчиков."""
        self._callbacks.update(actions)

    # ── Управление меню ──

    def get_menu_items(self) -> List[TrayMenuItem]:
        """Возвращает текущие элементы меню."""
        return list(self._menu_items)

    def set_menu_items(self, items: List[TrayMenuItem]) -> None:
        """Устанавливает новые элементы меню."""
        self._menu_items = items

    def add_menu_item(self, item: TrayMenuItem) -> None:
        """Добавляет элемент в конец меню (перед 'Выход')."""
        # Вставляем перед последним элементом (Выход)
        if self._menu_items and self._menu_items[-1].action == "quit":
            self._menu_items.insert(len(self._menu_items) - 1, item)
        else:
            self._menu_items.append(item)

    # ── Обработка событий ──

    def handle_action(self, action_id: str) -> bool:
        """Обрабатывает действие из меню.

        Phase 1: Системные действия → Intent Bridge → governance.
        UI-действия (open_chat, about, quit) → прямые колбэки.

        Возвращает True если обработано.
        """
        # UI-only actions: не требуют governance
        _UI_ONLY = {"open_chat", "about", "quit", "open_settings"}

        if action_id in _UI_ONLY:
            callback = self._callbacks.get(action_id)
            if callback:
                try:
                    callback()
                    logger.info(f"Выполнено UI действие: {action_id}")
                    return True
                except Exception as e:
                    logger.error(f"Ошибка в UI действии {action_id}: {e}")
                    return False
            logger.warning(f"Нет обработчика для UI действия: {action_id}")
            return False

        # Системные действия → Intent Bridge
        return self._dispatch_intent(action_id)

    def _dispatch_intent(self, action_id: str) -> bool:
        """Dispatch tray action through IntentBridge (Phase 1)."""
        try:
            from lina.intent.bridge import get_intent_bridge
            from lina.intent.types import IntentStatus

            bridge = get_intent_bridge()

            # Map tray actions to domains
            _ACTION_DOMAINS = {
                "system_overview": "system",
                "quick_diag": "system",
                "network_diag": "network",
                "audio_diag": "audio",
            }

            domain = _ACTION_DOMAINS.get(action_id, "")

            if action_id in ("system_overview", "quick_diag",
                             "network_diag", "audio_diag"):
                result = bridge.from_diagnose(
                    domain=domain,
                    source="ui",
                    user_text=f"tray: {action_id}",
                )
            else:
                result = bridge.from_action(
                    action_id=action_id,
                    domain=domain,
                    source="ui",
                )

            if result.status == IntentStatus.DENIED:
                self.notify("Lina", f"Отказано: {result.response_text}",
                            icon="warning")
                return False
            elif result.status == IntentStatus.NEEDS_CONFIRM:
                self.notify("Lina", f"Требуется подтверждение: "
                            f"{result.response_text}", icon="info")
                return True
            elif result.status == IntentStatus.SUCCESS:
                logger.info(f"Tray intent выполнен: {action_id}")
                return True
            else:
                logger.warning(f"Tray intent: {action_id} → {result.status.value}")
                return False

        except ImportError:
            # Phase 3: NO bypass — governance unavailable = deny
            logger.error(
                "IntentBridge not available for tray action %s — blocked",
                action_id)
            self.notify("Lina", "Governance pipeline недоступен. "
                        "Действие заблокировано.", icon="warning")
            return False
        except Exception as e:
            logger.error(f"Tray intent dispatch error: {e}")
            return False

    def handle_click(self) -> None:
        """Обработка клика по значку (открывает чат)."""
        self.handle_action("open_chat")

    def handle_double_click(self) -> None:
        """Двойной клик — тоже открывает чат."""
        self.handle_action("open_chat")

    # ── Статус ──

    def update_status(self, **kwargs) -> None:
        """Обновляет статус. Принимает поля TrayStatus."""
        for key, value in kwargs.items():
            if hasattr(self.status, key):
                setattr(self.status, key, value)
        logger.debug(f"Статус обновлён: {kwargs}")

    def get_tooltip(self) -> str:
        """Возвращает текст tooltip-а."""
        return self.status.format_tooltip()

    # ── Уведомления ──

    def notify(self, title: str, message: str,
               icon: str = "info", timeout_ms: Optional[int] = None) -> Dict:
        """Показывает уведомление через системный трей.

        Args:
            title: Заголовок
            message: Текст
            icon: 'info', 'warning', 'error'
            timeout_ms: Время отображения (None → из конфига)

        Returns:
            Dict с информацией об уведомлении.
        """
        if not self.config.show_notifications:
            return {"shown": False, "reason": "notifications_disabled"}

        notification = {
            "title": title,
            "message": message,
            "icon": icon,
            "timeout_ms": timeout_ms or self.config.notification_timeout_ms,
            "shown": True,
        }
        self._notifications.append(notification)
        logger.info(f"Уведомление: {title}")
        return notification

    def get_notifications(self) -> List[Dict]:
        """Возвращает историю уведомлений."""
        return list(self._notifications)

    def clear_notifications(self) -> None:
        """Очищает историю уведомлений."""
        self._notifications.clear()

    # ── Видимость ──

    def show(self) -> None:
        """Показывает значок в трее."""
        self._visible = True
        logger.info("Значок трея показан")

    def hide(self) -> None:
        """Скрывает значок из трея."""
        self._visible = False
        logger.info("Значок трея скрыт")

    def is_visible(self) -> bool:
        """Виден ли значок в трее."""
        return self._visible

    # ── Сериализация ──

    def to_dict(self) -> Dict:
        """Полное состояние для отладки."""
        return {
            "visible": self._visible,
            "status": {
                "model_loaded": self.status.model_loaded,
                "model_name": self.status.model_name,
                "ram_usage_mb": self.status.ram_usage_mb,
                "queries_today": self.status.queries_today,
                "uptime_minutes": self.status.uptime_minutes,
            },
            "menu_items": [
                {"label": m.label, "action": m.action, "enabled": m.enabled}
                for m in self._menu_items
            ],
            "registered_actions": list(self._callbacks.keys()),
            "notifications_count": len(self._notifications),
        }


def create_qt_tray(controller: TrayIconController, app=None):
    """Создаёт Qt-виджет QSystemTrayIcon из контроллера.

    Вызывает исключение если Qt недоступен.

    Args:
        controller: TrayIconController с конфигурацией
        app: QApplication (создаётся если None)

    Returns:
        (QSystemTrayIcon, QApplication)
    """
    from lina.gui import get_qt_modules
    QtWidgets, QtCore, QtGui = get_qt_modules()

    if app is None:
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication([])

    # Создаём иконку
    if controller.config.icon_path:
        icon = QtGui.QIcon(controller.config.icon_path)
    else:
        # Генерируем простую иконку программно
        pixmap = QtGui.QPixmap(64, 64)
        pixmap.fill(QtGui.QColor("#89b4fa"))
        painter = QtGui.QPainter(pixmap)
        painter.setPen(QtGui.QColor("#1e1e2e"))
        font = QtGui.QFont("Noto Sans", 32, QtGui.QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "J")
        painter.end()
        icon = QtGui.QIcon(pixmap)

    tray = QtWidgets.QSystemTrayIcon(icon, app)
    tray.setToolTip(controller.get_tooltip())

    # Создаём меню
    menu = QtWidgets.QMenu()
    for item in controller.get_menu_items():
        if item.action:
            action = menu.addAction(item.label)
            action.setEnabled(item.enabled)
            action_id = item.action
            action.triggered.connect(
                lambda checked, aid=action_id: controller.handle_action(aid)
            )
        if item.separator_after:
            menu.addSeparator()

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: controller.handle_click()
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger
        else None
    )

    controller._qt_tray = tray
    tray.show()
    controller._visible = True

    return tray, app
