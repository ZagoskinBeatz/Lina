"""
HotkeyManager — поддержка горячих клавиш для DE.

Регистрация глобальных хоткеев:
  - Meta+L → открыть Lina
  - Meta+Shift+L → быстрая диагностика
  - Через dbus / gsettings / KDE custom shortcuts

Phase: GOVERNANCE LAYER / Hotkey Support
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class HotkeyBinding:
    """Определение горячей клавиши."""
    id: str
    name: str
    name_ru: str = ""
    keys: str = ""              # "Meta+L", "Meta+Shift+D"
    command: str = ""           # Команда для выполнения (shell fallback)
    intent_action: str = ""     # Intent action ID (Phase 1: in-process dispatch)
    intent_domain: str = ""     # Intent domain
    desktop: str = ""           # kde, gnome, generic
    enabled: bool = True


@dataclass
class HotkeyConfig:
    """Конфигурация горячих клавиш."""
    enabled: bool = True
    desktop_env: str = ""       # auto-detected
    bindings: List[HotkeyBinding] = field(default_factory=list)


# ─── Default bindings ───────────────────────────────────────────────────────

DEFAULT_BINDINGS = [
    HotkeyBinding(
        id="open_lina", name="Open Lina",
        name_ru="Открыть Lina",
        keys="Meta+L",
        command=(
            "qdbus org.lina.Assistant /org/lina/Assistant "
            "org.lina.Assistant.ToggleWindow 2>/dev/null "
            "|| python -m lina.gui.app"
        ),
        intent_action="open_app",
        intent_domain="desktop",
    ),
    HotkeyBinding(
        id="quick_diag", name="Quick Diagnostics",
        name_ru="Быстрая диагностика",
        keys="Meta+Shift+D",
        command=(
            "qdbus org.lina.Assistant /org/lina/Assistant "
            "org.lina.Assistant.Query 'диагностика системы' 2>/dev/null "
            "|| python -m lina.core.cli --oneshot 'диагностика системы'"
        ),
        intent_action="diagnose",
        intent_domain="system",
    ),
    HotkeyBinding(
        id="safe_mode", name="Enter Safe Mode",
        name_ru="Безопасный режим",
        keys="Meta+Shift+S",
        command="python -m lina.core.cli --oneshot 'безопасный режим'",
        intent_action="set_mode",
        intent_domain="safety",
    ),
]


# ─── HotkeyManager ──────────────────────────────────────────────────────────

class HotkeyManager:
    """
    Управление горячими клавишами для различных DE.

    Поддерживает:
      - KDE Plasma 5/6 (через kwriteconfig6/kwriteconfig5 / dbus)
      - GNOME (через gsettings)
      - Generic (через xdotool / kdotool / ydotool)

    Пример:
        mgr = get_hotkey_manager()
        mgr.detect_desktop()
        mgr.register_all()
    """

    def __init__(self, config: Optional[HotkeyConfig] = None) -> None:
        self._config = config or HotkeyConfig()
        if not self._config.bindings:
            self._config.bindings = list(DEFAULT_BINDINGS)
        if not self._config.desktop_env:
            self._config.desktop_env = self._detect_desktop()
        self._kwriteconfig = self._detect_kwriteconfig()

    @staticmethod
    def _detect_kwriteconfig() -> str:
        """Определить доступную версию kwriteconfig (Plasma 6 → 5 fallback)."""
        import shutil
        for cmd in ("kwriteconfig6", "kwriteconfig5"):
            if shutil.which(cmd):
                return cmd
        return "kwriteconfig6"  # default, will fail gracefully

    # ── Desktop Detection ────────────────────────────────

    @staticmethod
    def _detect_desktop() -> str:
        """Определить окружение рабочего стола."""
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        session = os.environ.get("DESKTOP_SESSION", "").lower()

        if "kde" in desktop or "plasma" in desktop or "kde" in session:
            return "kde"
        elif "gnome" in desktop or "gnome" in session:
            return "gnome"
        elif "xfce" in desktop:
            return "xfce"
        elif "sway" in desktop or "hyprland" in desktop:
            return "wayland_wm"
        else:
            return "generic"

    def detect_desktop(self) -> str:
        """Определить и установить DE."""
        self._config.desktop_env = self._detect_desktop()
        logger.info("HotkeyManager: detected desktop: %s",
                     self._config.desktop_env)
        return self._config.desktop_env

    # ── Register ─────────────────────────────────────────

    def register_all(self) -> Dict[str, bool]:
        """Зарегистрировать все горячие клавиши."""
        results = {}
        for binding in self._config.bindings:
            if binding.enabled:
                ok = self.register(binding)
                results[binding.id] = ok
        return results

    def register(self, binding: HotkeyBinding) -> bool:
        """Зарегистрировать одну горячую клавишу."""
        de = self._config.desktop_env
        if de == "kde":
            return self._register_kde(binding)
        elif de == "gnome":
            return self._register_gnome(binding)
        else:
            return self._register_generic(binding)

    def unregister_all(self) -> Dict[str, bool]:
        """Отключить все горячие клавиши."""
        results = {}
        for binding in self._config.bindings:
            ok = self.unregister(binding)
            results[binding.id] = ok
        return results

    def unregister(self, binding: HotkeyBinding) -> bool:
        """Отключить горячую клавишу."""
        de = self._config.desktop_env
        if de == "kde":
            return self._unregister_kde(binding)
        elif de == "gnome":
            return self._unregister_gnome(binding)
        return True

    # ── KDE ──────────────────────────────────────────────

    def _register_kde(self, binding: HotkeyBinding) -> bool:
        """Зарегистрировать хоткей в KDE Plasma 5/6."""
        try:
            kw = self._kwriteconfig
            group = f"lina-{binding.id}"
            cmds = [
                [
                    kw, "--file", "kglobalshortcutsrc",
                    "--group", group,
                    "--key", "_k_friendly_name",
                    binding.name_ru or binding.name,
                ],
                [
                    kw, "--file", "kglobalshortcutsrc",
                    "--group", group,
                    "--key", binding.id,
                    f"{binding.keys},none,{binding.name}",
                ],
            ]

            # Also create a .desktop file for the shortcut action
            self._create_desktop_entry(binding)

            for cmd in cmds:
                r = subprocess.run(cmd, capture_output=True, timeout=5)
                if r.returncode != 0:
                    return False

            # Reload shortcuts
            subprocess.run(
                ["dbus-send", "--type=signal", "--dest=org.kde.kglobalaccel",
                 "/kglobalaccel", "org.kde.kglobalaccel.reloadConfig"],
                capture_output=True, timeout=5,
            )
            logger.info("HotkeyManager(KDE): registered %s = %s",
                         binding.id, binding.keys)
            return True
        except Exception as e:
            logger.error("HotkeyManager(KDE): register error: %s", e)
            return False

    def _unregister_kde(self, binding: HotkeyBinding) -> bool:
        try:
            kw = self._kwriteconfig
            group = f"lina-{binding.id}"
            subprocess.run(
                [kw, "--file", "kglobalshortcutsrc",
                 "--group", group, "--key", binding.id, "--delete"],
                capture_output=True, timeout=5,
            )
            return True
        except Exception:
            return False

    # ── GNOME ────────────────────────────────────────────

    def _register_gnome(self, binding: HotkeyBinding) -> bool:
        """Зарегистрировать хоткей в GNOME."""
        try:
            # Read current custom keybindings
            r = subprocess.run(
                ["gsettings", "get", "org.gnome.settings-daemon.plugins.media-keys",
                 "custom-keybindings"],
                capture_output=True, text=True, timeout=5,
            )
            existing = r.stdout.strip()
            if existing == "@as []":
                existing = "[]"

            # Create new binding
            path = f"/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/lina-{binding.id}/"
            schema = f"org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{path}"

            subprocess.run(
                ["gsettings", "set", schema, "name", binding.name],
                capture_output=True, timeout=5,
            )
            subprocess.run(
                ["gsettings", "set", schema, "command", binding.command],
                capture_output=True, timeout=5,
            )
            subprocess.run(
                ["gsettings", "set", schema, "binding", binding.keys],
                capture_output=True, timeout=5,
            )

            logger.info("HotkeyManager(GNOME): registered %s = %s",
                         binding.id, binding.keys)
            return True
        except Exception as e:
            logger.error("HotkeyManager(GNOME): register error: %s", e)
            return False

    @staticmethod
    def _unregister_gnome(binding: HotkeyBinding) -> bool:
        # GNOME custom keybindings removal is more complex
        return True

    # ── Generic ──────────────────────────────────────────

    def _register_generic(self, binding: HotkeyBinding) -> bool:
        """Fallback: создать .desktop файл."""
        return self._create_desktop_entry(binding)

    @staticmethod
    def _create_desktop_entry(binding: HotkeyBinding) -> bool:
        """Создать .desktop файл для хоткея."""
        try:
            apps_dir = Path.home() / ".local" / "share" / "applications"
            apps_dir.mkdir(parents=True, exist_ok=True)
            desktop_path = apps_dir / f"lina-{binding.id}.desktop"

            content = f"""[Desktop Entry]
Type=Application
Name={binding.name}
Comment={binding.name_ru}
Exec={binding.command}
Icon=utilities-terminal
Terminal=false
Categories=System;Utility;
Keywords=lina;assistant;diagnostics;
"""
            desktop_path.write_text(content, encoding="utf-8")
            return True
        except Exception as e:
            logger.error("HotkeyManager: desktop entry error: %s", e)
            return False

    # ── Query ────────────────────────────────────────────

    def dispatch_intent(self, binding: HotkeyBinding) -> Optional[Dict[str, Any]]:
        """Dispatch hotkey as Intent through governance (Phase 1).

        Если binding имеет intent_action → создаём Intent → IntentBridge.
        Если нет intent_action → fallback на shell command.

        Returns:
            IntentResult.to_dict() или None если shell fallback.
        """
        if not binding.intent_action:
            return None  # Shell fallback

        try:
            from lina.intent.bridge import get_intent_bridge

            bridge = get_intent_bridge()

            if binding.intent_action == "diagnose":
                result = bridge.from_diagnose(
                    domain=binding.intent_domain,
                    source="hotkey",
                    user_text=f"hotkey: {binding.name}",
                )
            else:
                result = bridge.from_action(
                    action_id=binding.intent_action,
                    domain=binding.intent_domain,
                    params={"hotkey_id": binding.id},
                    source="hotkey",
                )

            logger.info("HotkeyManager: intent dispatched: %s → %s",
                         binding.id, result.status.value)
            return result.to_dict()
        except Exception as e:
            logger.error("HotkeyManager: intent dispatch error: %s", e)
            return None

    def list_bindings(self) -> List[Dict[str, Any]]:
        """Список горячих клавиш."""
        return [
            {"id": b.id, "name": b.name_ru or b.name,
             "keys": b.keys, "enabled": b.enabled}
            for b in self._config.bindings
        ]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "desktop_env": self._config.desktop_env,
            "total_bindings": len(self._config.bindings),
            "enabled_bindings": sum(1 for b in self._config.bindings if b.enabled),
        }


# ─── Singleton ─────────────────────────────────────────────────────────────────

_manager: Optional[HotkeyManager] = None

def get_hotkey_manager() -> HotkeyManager:
    """Получить единственный экземпляр HotkeyManager."""
    global _manager
    if _manager is None:
        _manager = HotkeyManager()
    return _manager
