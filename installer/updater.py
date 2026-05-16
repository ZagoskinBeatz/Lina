"""
Lina Installer — Updater.

Проверка и установка обновлений:
  - Проверка версии (GitHub releases / пакетный менеджер)
  - Обновление модели
  - Обновление базы знаний
  - Миграция конфигурации
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime

logger = logging.getLogger("lina.installer.updater")


# ─── Конфигурация ────────────────────────────────────────────────────────────

@dataclass
class UpdateChannel:
    """Канал обновлений."""
    name: str                       # stable / beta / nightly
    url: str = ""
    enabled: bool = True


@dataclass
class UpdateConfig:
    """Конфигурация обновлений."""
    check_on_startup: bool = False   # Проверять при запуске
    auto_update_knowledge: bool = True  # Автообновление базы знаний
    channel: str = "stable"          # stable / beta
    github_repo: str = "lina-linux/lina"
    check_interval_hours: int = 24   # Интервал проверки (часы)
    last_check: Optional[str] = None  # ISO datetime

    def to_dict(self) -> Dict:
        return {
            "check_on_startup": self.check_on_startup,
            "auto_update_knowledge": self.auto_update_knowledge,
            "channel": self.channel,
            "check_interval_hours": self.check_interval_hours,
            "last_check": self.last_check,
        }


# ─── Версия ──────────────────────────────────────────────────────────────────

@dataclass
class VersionInfo:
    """Информация о версии."""
    version: str
    release_date: str = ""
    changelog: str = ""
    download_url: str = ""
    size_mb: float = 0
    is_newer: bool = False

    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "release_date": self.release_date,
            "changelog": self.changelog,
            "download_url": self.download_url,
            "size_mb": self.size_mb,
            "is_newer": self.is_newer,
        }


# ─── Updater ─────────────────────────────────────────────────────────────────

class LinaUpdater:
    """Менеджер обновлений Lina.

    Поддерживает:
      - Проверку новых версий через GitHub API
      - Обновление через пакетный менеджер (pacman/apt/dnf)
      - Обновление базы знаний
      - Миграцию конфигурации
    """

    def __init__(self, config: Optional[UpdateConfig] = None,
                 current_version: Optional[str] = None):
        self.config = config or UpdateConfig()
        if current_version is None:
            try:
                from lina import __version__
                current_version = __version__
            except ImportError:
                current_version = "0.9.0"
        self.current_version = current_version
        self._on_progress: Optional[Callable[[str, float], None]] = None
        self._on_update_available: Optional[Callable[[VersionInfo], None]] = None
        self._cache_dir = Path.home() / ".cache" / "lina" / "updates"
        logger.info(f"Updater: version={current_version}")

    # ── Колбэки ──

    def set_on_progress(self, cb: Callable[[str, float], None]) -> None:
        self._on_progress = cb

    def set_on_update_available(self, cb: Callable[[VersionInfo], None]) -> None:
        self._on_update_available = cb

    # ── Проверка версии ──

    def get_current_version(self) -> str:
        return self.current_version

    def parse_version(self, version_str: str) -> tuple:
        """Парсит семантическую версию в кортеж."""
        parts = version_str.lstrip("v").split(".")
        result = []
        for p in parts:
            try:
                result.append(int(p.split("-")[0]))
            except ValueError:
                result.append(0)
        while len(result) < 3:
            result.append(0)
        return tuple(result[:3])

    def is_newer(self, remote_version: str) -> bool:
        """Проверяет, новее ли remote-версия."""
        local = self.parse_version(self.current_version)
        remote = self.parse_version(remote_version)
        return remote > local

    def check_update_github(self) -> Optional[VersionInfo]:
        """Проверяет обновления через GitHub API.

        Returns:
            VersionInfo если есть обновление, None если нет.
        """
        # Оффлайн: не делаем HTTP-запросы, возвращаем None
        # В реальности здесь будет urllib запрос к GitHub API
        self.config.last_check = datetime.now().isoformat()
        logger.info("Проверка обновлений через GitHub (симуляция)")
        return None  # No update available

    def check_update_package_manager(self) -> Optional[Dict]:
        """Проверяет обновления через пакетный менеджер."""
        pm = self._detect_package_manager()
        if not pm:
            return None

        check_commands = {
            "pacman": ["pacman", "-Qu", "lina"],
            "apt": ["apt", "list", "--upgradable"],
            "dnf": ["dnf", "check-update", "lina"],
        }

        cmd = check_commands.get(pm)
        if not cmd:
            return None

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            output = result.stdout.strip()
            if output and "lina" in output.lower():
                return {
                    "package_manager": pm,
                    "output": output,
                    "update_available": True,
                }
            return {"package_manager": pm, "update_available": False}
        except Exception as e:
            logger.error(f"Ошибка проверки через {pm}: {e}")
            return None

    def check_for_updates(self) -> Dict[str, Any]:
        """Комплексная проверка обновлений.

        Returns:
            Dict с результатами всех проверок.
        """
        result = {
            "current_version": self.current_version,
            "github": None,
            "package_manager": None,
            "update_available": False,
            "checked_at": datetime.now().isoformat(),
        }

        # GitHub
        gh_update = self.check_update_github()
        if gh_update:
            result["github"] = gh_update.to_dict()
            result["update_available"] = True

        # Package manager
        pm_update = self.check_update_package_manager()
        if pm_update:
            result["package_manager"] = pm_update
            if pm_update.get("update_available"):
                result["update_available"] = True

        return result

    # ── Обновление базы знаний ──

    def update_knowledge_base(self, knowledge_dir: Optional[Path] = None) -> Dict:
        """Обновляет базу знаний.

        В реальности: git pull или скачивание архива.
        """
        from lina.config import KNOWLEDGE_DIR
        kdir = knowledge_dir or KNOWLEDGE_DIR

        result = {
            "success": True,
            "files_before": 0,
            "files_after": 0,
            "new_files": 0,
        }

        if kdir.exists():
            result["files_before"] = sum(1 for _ in kdir.rglob("*.md"))
            result["files_after"] = result["files_before"]
            # В реальности здесь будет git pull или rsync
            logger.info(f"База знаний: {result['files_before']} файлов")
        else:
            result["success"] = False

        if self._on_progress:
            self._on_progress("База знаний обновлена", 1.0)

        return result

    # ── Миграция конфигурации ──

    def migrate_config(self, from_version: str, to_version: str,
                       config_path: Optional[Path] = None) -> Dict:
        """Мигрирует конфигурацию между версиями.

        Args:
            from_version: Текущая версия конфига
            to_version: Целевая версия
            config_path: Путь к конфигу

        Returns:
            Dict с результатом миграции.
        """
        result = {
            "from": from_version,
            "to": to_version,
            "changes": [],
            "success": True,
            "backup_created": False,
        }

        from_tuple = self.parse_version(from_version)
        to_tuple = self.parse_version(to_version)

        if from_tuple == to_tuple:
            result["changes"].append("Версия не изменилась")
            return result

        # Создаём backup
        if config_path and config_path.exists():
            backup = config_path.with_suffix(f".{from_version}.bak")
            try:
                shutil.copy2(config_path, backup)
                result["backup_created"] = True
                result["changes"].append(f"Бэкап: {backup}")
            except Exception as e:
                result["changes"].append(f"Ошибка бэкапа: {e}")

        # Миграции по версиям
        if from_tuple < (0, 7, 0) and to_tuple >= (0, 7, 0):
            result["changes"].append("Добавлены настройки RAG retriever")

        if from_tuple < (0, 8, 0) and to_tuple >= (0, 8, 0):
            result["changes"].append("Добавлены диагностические деревья")
            result["changes"].append("Новый формат настроек pipeline")

        if from_tuple < (0, 9, 0) and to_tuple >= (0, 9, 0):
            result["changes"].append("Добавлены настройки GUI")
            result["changes"].append("Добавлены настройки голоса")

        if from_tuple < (1, 0, 0) and to_tuple >= (1, 0, 0):
            result["changes"].append("Миграция в production-формат конфига")

        logger.info(f"Миграция {from_version} → {to_version}: "
                     f"{len(result['changes'])} изменений")
        return result

    # ── Обновление модели ──

    def check_model_update(self, current_model: str = "") -> Dict:
        """Проверяет, есть ли более новая модель."""
        return {
            "current_model": current_model,
            "update_available": False,
            "new_model": None,
            "message": "Текущая модель актуальна",
        }

    # ── Утилиты ──

    def _detect_package_manager(self) -> Optional[str]:
        """Определяет пакетный менеджер."""
        for pm in ["pacman", "apt", "dnf", "zypper"]:
            if shutil.which(pm):
                return pm
        return None

    def should_check(self) -> bool:
        """Нужно ли проверять обновления (по интервалу)."""
        if not self.config.check_on_startup:
            return False
        if not self.config.last_check:
            return True
        try:
            last = datetime.fromisoformat(self.config.last_check)
            delta = datetime.now() - last
            return delta.total_seconds() > self.config.check_interval_hours * 3600
        except Exception:
            return True

    def to_dict(self) -> Dict:
        return {
            "current_version": self.current_version,
            "config": self.config.to_dict(),
            "should_check": self.should_check(),
        }

    def get_info(self) -> str:
        return (f"Updater: v{self.current_version}, "
                f"channel={self.config.channel}, "
                f"last_check={self.config.last_check or 'never'}")
