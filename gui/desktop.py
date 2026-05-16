"""
Lina GUI — Desktop File & Autostart Generator.

Генерирует:
  - lina.desktop      → ~/.local/share/applications/
  - lina-autostart.desktop → ~/.config/autostart/
  - Иконку (SVG)

Соответствует FreeDesktop Spec.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger("lina.gui.desktop")


# ─── Шаблоны .desktop-файлов ─────────────────────────────────────────────────

DESKTOP_TEMPLATE = """\
[Desktop Entry]
Version=1.0
Type=Application
Name=Lina AI Assistant
GenericName=AI Assistant
Comment=Локальный ИИ-помощник для Linux
Comment[ru]=Локальный ИИ-помощник для Linux
Comment[en]=Local AI Assistant for Linux
Exec={exec_path} --gui
Icon={icon_path}
Terminal=false
Categories=Utility;System;
Keywords=AI;assistant;Linux;help;lina;
StartupNotify=true
StartupWMClass=lina
"""

AUTOSTART_TEMPLATE = """\
[Desktop Entry]
Version=1.0
Type=Application
Name=Lina AI Assistant
Comment=Lina AI — автозапуск
Exec={exec_path} --gui --minimized
Icon={icon_path}
Terminal=false
X-GNOME-Autostart-enabled=true
Hidden=false
"""

# SVG-иконка Lina (минималистичная буква J в круге)
LINA_ICON_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#89b4fa"/>
      <stop offset="100%" style="stop-color:#74c7ec"/>
    </linearGradient>
  </defs>
  <circle cx="64" cy="64" r="60" fill="url(#bg)"/>
  <text x="64" y="84" text-anchor="middle"
        font-family="Noto Sans,DejaVu Sans,sans-serif"
        font-size="72" font-weight="bold" fill="#1e1e2e">J</text>
</svg>
"""


# ─── Генератор .desktop файлов ────────────────────────────────────────────────

class DesktopFileGenerator:
    """Генерирует и устанавливает .desktop файлы."""

    def __init__(self,
                 exec_path: str = "/usr/bin/lina",
                 icon_path: Optional[str] = None):
        self.exec_path = exec_path
        self.icon_path = icon_path or str(
            Path.home() / ".local" / "share" / "icons" / "lina.svg"
        )
        self._apps_dir = Path.home() / ".local" / "share" / "applications"
        self._autostart_dir = Path.home() / ".config" / "autostart"
        self._icons_dir = Path.home() / ".local" / "share" / "icons"

    # ── Генерация контента ──

    def generate_desktop_entry(self) -> str:
        """Генерирует содержимое lina.desktop."""
        return DESKTOP_TEMPLATE.format(
            exec_path=self.exec_path,
            icon_path=self.icon_path,
        )

    def generate_autostart_entry(self) -> str:
        """Генерирует содержимое lina-autostart.desktop."""
        return AUTOSTART_TEMPLATE.format(
            exec_path=self.exec_path,
            icon_path=self.icon_path,
        )

    def generate_icon_svg(self) -> str:
        """Возвращает SVG-иконку."""
        return LINA_ICON_SVG

    # ── Установка файлов ──

    def install_desktop_file(self) -> Path:
        """Устанавливает lina.desktop в applications.

        Returns:
            Path к установленному файлу.
        """
        self._apps_dir.mkdir(parents=True, exist_ok=True)
        target = self._apps_dir / "lina.desktop"
        target.write_text(self.generate_desktop_entry(), encoding="utf-8")
        target.chmod(0o755)
        logger.info(f"Desktop file установлен: {target}")
        return target

    def install_autostart(self) -> Path:
        """Устанавливает autostart .desktop."""
        self._autostart_dir.mkdir(parents=True, exist_ok=True)
        target = self._autostart_dir / "lina-autostart.desktop"
        target.write_text(self.generate_autostart_entry(), encoding="utf-8")
        logger.info(f"Autostart file установлен: {target}")
        return target

    def remove_autostart(self) -> bool:
        """Удаляет autostart .desktop."""
        target = self._autostart_dir / "lina-autostart.desktop"
        if target.exists():
            target.unlink()
            logger.info("Autostart file удалён")
            return True
        return False

    def install_icon(self) -> Path:
        """Устанавливает SVG-иконку."""
        self._icons_dir.mkdir(parents=True, exist_ok=True)
        target = Path(self.icon_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.generate_icon_svg(), encoding="utf-8")
        logger.info(f"Иконка установлена: {target}")
        return target

    def install_all(self) -> Dict[str, Path]:
        """Полная установка: desktop + autostart + icon."""
        return {
            "desktop": self.install_desktop_file(),
            "autostart": self.install_autostart(),
            "icon": self.install_icon(),
        }

    def uninstall_all(self) -> Dict[str, bool]:
        """Удаляет все установленные файлы."""
        results = {}
        for name, path in [
            ("desktop", self._apps_dir / "lina.desktop"),
            ("autostart", self._autostart_dir / "lina-autostart.desktop"),
            ("icon", Path(self.icon_path)),
        ]:
            if path.exists():
                path.unlink()
                results[name] = True
                logger.info(f"Удалён: {path}")
            else:
                results[name] = False
        return results

    # ── Проверка ──

    def is_installed(self) -> Dict[str, bool]:
        """Проверяет, какие файлы установлены."""
        return {
            "desktop": (self._apps_dir / "lina.desktop").exists(),
            "autostart": (self._autostart_dir / "lina-autostart.desktop").exists(),
            "icon": Path(self.icon_path).exists(),
        }

    def is_autostart_enabled(self) -> bool:
        """Включён ли автозапуск."""
        return (self._autostart_dir / "lina-autostart.desktop").exists()

    # ── Валидация ──

    def validate_desktop_entry(self, content: str) -> Dict[str, bool]:
        """Проверяет корректность .desktop файла."""
        checks = {
            "has_type": "Type=Application" in content,
            "has_name": "Name=" in content,
            "has_exec": "Exec=" in content,
            "has_icon": "Icon=" in content,
            "has_header": content.startswith("[Desktop Entry]"),
            "has_categories": "Categories=" in content,
        }
        return checks

    def get_info(self) -> Dict:
        """Информация о генераторе."""
        return {
            "exec_path": self.exec_path,
            "icon_path": self.icon_path,
            "apps_dir": str(self._apps_dir),
            "autostart_dir": str(self._autostart_dir),
            "installed": self.is_installed(),
        }
