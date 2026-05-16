# -*- coding: utf-8 -*-
"""
Lina Core — Application Resolver (Phase 27).

Универсальная система обнаружения и запуска приложений.

Архитектура:
  1. Сканирование ВСЕХ источников (.desktop, Flatpak, Snap, PATH, AppImage)
  2. Построение индекса (кэшируется)
  3. Fuzzy matching с ранжированием (Levenshtein + token similarity)
  4. Определение команды запуска (приоритет: .desktop Exec → flatpak run → snap run → binary)
  5. Асинхронный запуск
  6. Верификация (PID alive, no crash)
  7. Fallback: package manager → Flatpak remotes → web search

Гарантии:
  - Никогда не путает "Курсор" (валюта) и "Cursor" (редактор) при intent=OPEN_APPLICATION
  - Возвращает top-3 кандидата с confidence
  - Если confidence < 0.5 → уточнение у пользователя
  - Проверка запуска через PID
"""

import os
import re
import glob
import shlex
import shutil
import subprocess
import logging
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
from configparser import ConfigParser

logger = logging.getLogger("lina.core.application_resolver")


# ═══════════════════════════════════════════════════════════════════════════════
#  Data Models
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AppInfo:
    """Полная информация о приложении."""
    display_name: str                       # Человекочитаемое имя
    exec_command: str                       # Команда запуска
    source: str                             # desktop / flatpak / snap / binary / appimage
    desktop_file: str = ""                  # Путь к .desktop
    package_name: str = ""                  # Имя пакета (flatpak ID, snap name, etc.)
    keywords: List[str] = field(default_factory=list)
    icon: str = ""
    categories: List[str] = field(default_factory=list)
    wm_class: str = ""                      # StartupWMClass

    def to_dict(self) -> Dict[str, Any]:
        return {
            "display_name": self.display_name,
            "exec_command": self.exec_command,
            "source": self.source,
            "desktop_file": self.desktop_file,
            "package_name": self.package_name,
            "keywords": self.keywords,
            "icon": self.icon,
        }


@dataclass
class AppCandidate:
    """Кандидат из fuzzy matching."""
    app: AppInfo
    confidence: float = 0.0    # 0.0–1.0
    match_reason: str = ""     # Почему совпало

    def __repr__(self):
        return (f"AppCandidate({self.app.display_name!r}, "
                f"conf={self.confidence:.2f}, src={self.app.source})")


@dataclass
class LaunchResult:
    """Результат запуска приложения."""
    success: bool = False
    message: str = ""
    pid: Optional[int] = None
    command: str = ""
    app_name: str = ""
    verified: bool = False      # PID проверен через 1-2 секунды

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "pid": self.pid,
            "command": self.command,
            "app_name": self.app_name,
            "verified": self.verified,
        }


@dataclass
class InstallSuggestion:
    """Подсказка по установке."""
    method: str         # pacman / apt / dnf / flatpak / snap / web
    command: str        # Команда установки
    package_name: str   # Имя пакета
    source: str = ""    # Откуда (flathub, AUR, etc.)
    url: str = ""       # URL для веб-варианта
    note: str = ""      # Дополнительная информация (из веб-поиска и т.д.)


# ═══════════════════════════════════════════════════════════════════════════════
#  Русские алиасы — маппинг «что говорит пользователь» → «что искать»
# ═══════════════════════════════════════════════════════════════════════════════

_RU_ALIASES: Dict[str, List[str]] = {
    # Браузеры
    "хром": ["chrome", "chromium", "google-chrome", "google chrome"],
    "гугл": ["chrome", "google-chrome", "google chrome"],
    "гугл хром": ["chrome", "google-chrome", "google chrome"],
    "браузер": ["chrome", "firefox", "chromium", "brave", "yandex-browser"],
    "фаерфокс": ["firefox"],
    "яндекс браузер": ["yandex", "yandex-browser"],
    # Файловые менеджеры
    "проводник": ["dolphin", "nautilus", "thunar", "nemo", "files", "pcmanfm"],
    "файлы": ["dolphin", "nautilus", "thunar", "nemo", "files"],
    "файловый менеджер": ["dolphin", "nautilus", "thunar", "nemo", "files"],
    # Терминалы
    "терминал": ["konsole", "alacritty", "kitty", "gnome-terminal", "xterm", "terminal"],
    "консоль": ["konsole", "alacritty", "kitty", "gnome-terminal"],
    # Редакторы текста
    "текстовый редактор": ["kate", "kwrite", "gedit", "mousepad", "xed"],
    "редактор": ["kate", "kwrite", "gedit"],
    "блокнот": ["kate", "kwrite", "gedit", "mousepad", "xed"],
    # IDE / Code editors
    "код": ["code", "vscodium", "visual studio code"],
    "вскод": ["code", "vscodium", "visual studio code"],
    "vscode": ["code", "visual studio code"],
    "курсор": ["cursor"],
    # Мессенджеры
    "телеграм": ["telegram", "telegram-desktop", "ayugram", "64gram"],
    "дискорд": ["discord"],
    "вотсап": ["whatsapp"],
    "скайп": ["skype"],
    "зум": ["zoom"],
    # Медиа
    "видеоплеер": ["vlc", "mpv", "totem", "celluloid", "haruna"],
    "музыка": ["spotify", "rhythmbox", "elisa", "strawberry", "audacious"],
    "спотифай": ["spotify"],
    #Офис
    "ворд": ["libreoffice writer", "writer"],
    "таблицы": ["libreoffice calc", "calc"],
    "презентации": ["libreoffice impress", "impress"],
    "офис": ["libreoffice", "onlyoffice"],
    # Графика
    "фоторедактор": ["gimp", "krita", "pinta"],
    "рисование": ["krita", "inkscape"],
    # Системные
    "настройки": ["systemsettings", "gnome-control-center", "settings"],
    "системный монитор": ["ksysguard", "plasma-systemmonitor", "gnome-system-monitor"],
    "монитор": ["plasma-systemmonitor", "gnome-system-monitor", "ksysguard"],
    # Игры
    "стим": ["steam"],
    "лутрис": ["lutris"],
}


# ═══════════════════════════════════════════════════════════════════════════════
#  Levenshtein distance (без зависимостей)
# ═══════════════════════════════════════════════════════════════════════════════

def _levenshtein(s1: str, s2: str) -> int:
    """Расстояние Левенштейна (edit distance)."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row.append(min(
                curr_row[j] + 1,
                prev_row[j + 1] + 1,
                prev_row[j] + cost,
            ))
        prev_row = curr_row
    return prev_row[-1]


def _levenshtein_similarity(s1: str, s2: str) -> float:
    """Нормализованное сходство (0.0–1.0, 1.0 = идентичны)."""
    if not s1 and not s2:
        return 1.0
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    dist = _levenshtein(s1, s2)
    return 1.0 - dist / max_len


def _token_similarity(query: str, target: str) -> float:
    """Сходство по токенам (пересечение слов)."""
    q_tokens = set(query.lower().split())
    t_tokens = set(target.lower().split())
    if not q_tokens or not t_tokens:
        return 0.0
    intersection = q_tokens & t_tokens
    union = q_tokens | t_tokens
    return len(intersection) / len(union)  # Jaccard


def _normalized_contains(query: str, target: str) -> float:
    """Проверка вхождения с нормализацией."""
    q = query.lower().strip()
    t = target.lower().strip()
    if q == t:
        return 1.0
    if t.startswith(q) or q.startswith(t):
        return 0.9
    if q in t:
        return 0.7 + 0.2 * (len(q) / len(t))
    if t in q:
        return 0.6
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Desktop File Parser
# ═══════════════════════════════════════════════════════════════════════════════

def _get_desktop_dirs() -> List[str]:
    """Все стандартные каталоги .desktop файлов."""
    dirs = []
    xdg_data = os.environ.get("XDG_DATA_DIRS", "/usr/share:/usr/local/share")
    for d in xdg_data.split(":"):
        p = os.path.join(d, "applications")
        if os.path.isdir(p):
            dirs.append(p)
    home = Path.home()
    user_dirs = [
        home / ".local" / "share" / "applications",
        home / ".local" / "share" / "flatpak" / "exports" / "share" / "applications",
        Path("/var/lib/flatpak/exports/share/applications"),
    ]
    for d in user_dirs:
        if d.is_dir():
            dirs.append(str(d))
    return dirs


def _parse_desktop_file(filepath: str) -> Optional[AppInfo]:
    """Парсит .desktop файл в AppInfo."""
    try:
        cp = ConfigParser(interpolation=None, strict=False)
        cp.read(filepath, encoding="utf-8")
        if not cp.has_section("Desktop Entry"):
            return None
        e = dict(cp["Desktop Entry"])
        if e.get("nodisplay", "").lower() == "true":
            return None
        if e.get("hidden", "").lower() == "true":
            return None
        if e.get("type", "Application").lower() not in ("application", ""):
            return None
        exec_cmd = e.get("exec", "").strip()
        if not exec_cmd:
            return None
        # Убираем %u %U %f %F и другие placeholder-ы
        exec_cmd = re.sub(r'\s+%[a-zA-Z]', '', exec_cmd).strip()
        name = e.get("name", os.path.basename(filepath).replace(".desktop", ""))

        # Собираем все ключевые слова
        keywords = []
        kw_str = e.get("keywords", "")
        if kw_str:
            keywords.extend([k.strip().lower() for k in kw_str.split(";") if k.strip()])
        # Name[ru] и другие локализации тоже keywords
        for key in e:
            if key.startswith("name["):
                val = e[key].strip()
                if val:
                    keywords.append(val.lower())
            if key.startswith("genericname"):
                val = e[key].strip()
                if val:
                    keywords.append(val.lower())

        # Категории
        categories = []
        cat_str = e.get("categories", "")
        if cat_str:
            categories = [c.strip() for c in cat_str.split(";") if c.strip()]

        wm_class = e.get("startupwmclass", "").strip()
        icon = e.get("icon", "").strip()

        return AppInfo(
            display_name=name,
            exec_command=exec_cmd,
            source="desktop",
            desktop_file=filepath,
            package_name="",
            keywords=keywords,
            icon=icon,
            categories=categories,
            wm_class=wm_class,
        )
    except Exception as exc:
        logger.debug("Ошибка парсинга %s: %s", filepath, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  ApplicationResolver
# ═══════════════════════════════════════════════════════════════════════════════

class ApplicationResolver:
    """
    Универсальная система обнаружения и запуска приложений.

    Методы:
      find_installed_apps()     → полный список установленных
      match_app(user_input)     → top-N кандидатов с confidence
      resolve_launch_command()  → определение команды запуска
      launch(user_input)        → найти + запустить + проверить
      verify_launch(pid)        → проверка PID через 1-2 сек
      suggest_installation()    → подсказки установки

    Индекс кэшируется на 5 минут.
    """

    CACHE_TTL = 300.0     # 5 минут
    CONFIDENCE_THRESHOLD = 0.5
    VERIFY_DELAY = 1.5    # секунды до проверки PID

    def __init__(self):
        self._app_index: List[AppInfo] = []
        self._index_time: float = 0.0
        self._launch_history: deque = deque(maxlen=100)
        self._distro: Optional[str] = None

    # ──────────────────────────────────────────────────
    #  ЭТАП 1: Сканирование установленных приложений
    # ──────────────────────────────────────────────────

    def find_installed_apps(self, force_refresh: bool = False) -> List[AppInfo]:
        """
        Сканирует ВСЕ источники и строит индекс.

        Источники:
          1. .desktop файлы (XDG dirs)
          2. Flatpak (flatpak list --app)
          3. Snap (snap list)
          4. AppImage (~/.local/bin, ~/Applications, ~/Downloads)

        Returns:
            Полный список AppInfo.
        """
        now = time.time()
        if (not force_refresh
                and self._app_index
                and (now - self._index_time) < self.CACHE_TTL):
            return self._app_index

        apps: List[AppInfo] = []
        seen_execs = set()

        # 1. .desktop файлы
        for d in _get_desktop_dirs():
            for f in glob.glob(os.path.join(d, "**", "*.desktop"), recursive=True):
                info = _parse_desktop_file(f)
                if info and info.exec_command not in seen_execs:
                    apps.append(info)
                    seen_execs.add(info.exec_command)

        # 2. Flatpak
        for fp in self._scan_flatpak():
            if fp.exec_command not in seen_execs:
                apps.append(fp)
                seen_execs.add(fp.exec_command)

        # 3. Snap
        for sp in self._scan_snap():
            if sp.exec_command not in seen_execs:
                apps.append(sp)
                seen_execs.add(sp.exec_command)

        # 4. AppImage
        for ai in self._scan_appimages():
            if ai.exec_command not in seen_execs:
                apps.append(ai)
                seen_execs.add(ai.exec_command)

        self._app_index = apps
        self._index_time = now
        logger.info("Индекс приложений: %d записей", len(apps))
        return apps

    @staticmethod
    def _scan_flatpak() -> List[AppInfo]:
        """Сканирование Flatpak приложений."""
        if not shutil.which("flatpak"):
            return []
        try:
            r = subprocess.run(
                ["flatpak", "list", "--app", "--columns=application,name"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return []
            apps = []
            for line in r.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    app_id, name = parts[0].strip(), parts[1].strip()
                elif len(parts) == 1 and parts[0].strip():
                    app_id = parts[0].strip()
                    name = app_id.split(".")[-1]
                else:
                    continue
                apps.append(AppInfo(
                    display_name=name,
                    exec_command=f"flatpak run {app_id}",
                    source="flatpak",
                    package_name=app_id,
                    keywords=[
                        name.lower(),
                        app_id.lower(),
                        app_id.split(".")[-1].lower(),
                    ],
                ))
            return apps
        except Exception as e:
            logger.debug("Flatpak scan error: %s", e)
            return []

    @staticmethod
    def _scan_snap() -> List[AppInfo]:
        """Сканирование Snap приложений."""
        if not shutil.which("snap"):
            return []
        try:
            r = subprocess.run(
                ["snap", "list"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return []
            skip = {"core", "core18", "core20", "core22", "core24",
                     "snapd", "bare", "gnome-3-38-2004", "gtk-common-themes"}
            apps = []
            for line in r.stdout.strip().splitlines()[1:]:
                parts = line.split()
                if parts and parts[0] not in skip:
                    name = parts[0]
                    apps.append(AppInfo(
                        display_name=name,
                        exec_command=f"snap run {name}",
                        source="snap",
                        package_name=name,
                        keywords=[name.lower()],
                    ))
            return apps
        except Exception as e:
            logger.debug("Snap scan error: %s", e)
            return []

    @staticmethod
    def _scan_appimages() -> List[AppInfo]:
        """Сканирование AppImage файлов."""
        home = Path.home()
        search_dirs = [
            home / "Applications",
            home / ".local" / "bin",
            home / "Загрузки",
            home / "Downloads",
            home / "Desktop",
            home / "Рабочий стол",
        ]
        apps = []
        for d in search_dirs:
            if not d.is_dir():
                continue
            for f in d.iterdir():
                name_lower = f.name.lower()
                if name_lower.endswith(".appimage") or (
                    f.is_file() and os.access(str(f), os.X_OK) and "appimage" in name_lower
                ):
                    app_name = re.sub(
                        r'[-_.]appimage$', '', f.stem, flags=re.IGNORECASE
                    )
                    app_name = re.sub(
                        r'[-_]x86_64$|[-_]amd64$|[-_]\d+\.\d+.*$', '', app_name, flags=re.IGNORECASE
                    )
                    apps.append(AppInfo(
                        display_name=app_name,
                        exec_command=str(f),
                        source="appimage",
                        keywords=[app_name.lower(), f.stem.lower()],
                    ))
        return apps

    # ──────────────────────────────────────────────────
    #  ЭТАП 2: Fuzzy Matching с ранжированием
    # ──────────────────────────────────────────────────

    def match_app(self, user_input: str, top_n: int = 3) -> List[AppCandidate]:
        """
        Fuzzy-поиск приложения.

        Алгоритм:
          1. Нормализация запроса
          2. Расширение через русские алиасы
          3. Для каждого AppInfo: multi-signal scoring
          4. Ранжирование → top-N

        Args:
            user_input: Что сказал пользователь ("Krita", "хром", "Happ")
            top_n: Сколько кандидатов вернуть

        Returns:
            Список AppCandidate, отсортированных по confidence (desc).
        """
        apps = self.find_installed_apps()
        query = self._normalize_input(user_input)
        search_terms = self._expand_aliases(query)

        candidates: List[AppCandidate] = []

        for app in apps:
            conf, reason = self._score_app(query, search_terms, app)
            if conf > 0.1:
                candidates.append(AppCandidate(
                    app=app,
                    confidence=conf,
                    match_reason=reason,
                ))

        # Сортировка: confidence desc
        candidates.sort(key=lambda c: c.confidence, reverse=True)

        # Дедупликация по exec_command (первый = лучший)
        seen: Dict[str, bool] = {}
        deduped: List[AppCandidate] = []
        for c in candidates:
            key = os.path.basename(c.app.exec_command.split()[0]).lower()
            if key not in seen:
                seen[key] = True
                deduped.append(c)

        return deduped[:top_n]

    def _score_app(self, query: str, search_terms: List[str],
                   app: AppInfo) -> Tuple[float, str]:
        """
        Многосигнальное скоринг-приложение.

        Сигналы:
          - display_name match
          - keywords match
          - exec binary match
          - WM class match
          - Levenshtein distance
          - Token similarity

        Returns:
            (confidence 0.0-1.0, reason_string)
        """
        best_conf = 0.0
        best_reason = ""

        # Все строки для сопоставления из AppInfo
        app_names = [app.display_name.lower()]
        app_names.extend(app.keywords)
        if app.wm_class:
            app_names.append(app.wm_class.lower())
        # Базовое имя бинарника
        exec_base = os.path.basename(app.exec_command.split()[0]).lower()
        # Для flatpak: последняя часть ID тоже
        if app.source == "flatpak" and app.package_name:
            app_names.append(app.package_name.split(".")[-1].lower())

        is_webapp = ("--app-id=" in app.exec_command
                     or "--profile-directory=" in app.exec_command)

        for term in search_terms:
            # 1. Прямое совпадение имени
            for name in app_names:
                c = _normalized_contains(term, name)
                if c > best_conf:
                    best_conf = c
                    best_reason = f"name match: '{term}' ~ '{name}'"

                # Levenshtein
                lev = _levenshtein_similarity(term, name)
                if lev > 0.7 and lev > best_conf:
                    best_conf = lev
                    best_reason = f"levenshtein: '{term}' ~ '{name}' ({lev:.2f})"

            # 2. Exec binary match (пониженный вес)
            exec_conf = _normalized_contains(term, exec_base)
            # Для web-app только exec-match → штраф
            if is_webapp:
                exec_conf *= 0.3
            else:
                exec_conf *= 0.8
            if exec_conf > best_conf:
                best_conf = exec_conf
                best_reason = f"exec match: '{term}' ~ '{exec_base}'"

            # 3. Token similarity (для многословных)
            if " " in term or " " in app.display_name.lower():
                tok = _token_similarity(term, app.display_name.lower())
                if tok > 0.3 and tok > best_conf:
                    best_conf = tok
                    best_reason = f"token sim: '{term}' ~ '{app.display_name}'"

        return min(best_conf, 1.0), best_reason

    # ── Шумовые слова: описывают КАТЕГОРИЮ, но не имя приложения ──
    _INPUT_NOISE_PREFIXES = (
        "приложение ", "программу ", "программа ", "программ ",
        "открой ", "запусти ", "запуск ", "run ", "launch ",
        "open ", "стартуй ", "стартани ",
        # Категории софта (мессенджер Telegram → telegram)
        "мессенджер ", "мессенджера ", "клиент ", "клиента ",
        "браузер ", "браузера ", "редактор ", "редактора ",
        "плеер ", "плеера ", "плеере ",
        "менеджер ", "менеджера ",
        "утилита ", "утилиту ", "сервис ", "сервиса ",
        "пакет ", "пакета ", "пакетов ",
        "среда ", "среду ",
    )

    @staticmethod
    def _normalize_input(text: str) -> str:
        """Нормализация пользовательского ввода.

        Удаляет шумовые префиксы-категории:
          «мессенджер Max» → «max»
          «браузер Яндекс» → «яндекс»
        """
        q = text.lower().strip()
        changed = True
        while changed:
            changed = False
            for prefix in ApplicationResolver._INPUT_NOISE_PREFIXES:
                if q.startswith(prefix):
                    q = q[len(prefix):]
                    changed = True
        return q.strip()

    @staticmethod
    def _expand_aliases(query: str) -> List[str]:
        """Расширяет запрос через русские алиасы."""
        terms = [query]
        for alias, expansions in _RU_ALIASES.items():
            # Точное совпадение или высокое сходство
            if query == alias or _levenshtein_similarity(query, alias) >= 0.8:
                terms.extend(expansions)
        return terms

    # ──────────────────────────────────────────────────
    #  ЭТАП 3: Определение команды запуска
    # ──────────────────────────────────────────────────

    @staticmethod
    def resolve_launch_command(app: AppInfo) -> str:
        """
        Определяет правильную команду запуска.

        Приоритет:
          1. Exec из .desktop
          2. flatpak run ID
          3. snap run name
          4. бинарник из PATH
          5. AppImage путь

        Returns:
            Готовая команда для subprocess.
        """
        cmd = app.exec_command
        # Убираем %u %U %f %F и похожие
        cmd = re.sub(r'\s+%[a-zA-Z]', '', cmd).strip()
        return cmd

    # ──────────────────────────────────────────────────
    #  ЭТАП 4: Запуск
    # ──────────────────────────────────────────────────

    def launch(self, user_input: str) -> LaunchResult:
        """
        Полный цикл: найти → запустить → проверить.

        Args:
            user_input: Что сказал пользователь.

        Returns:
            LaunchResult с PID и статусом.
        """
        candidates = self.match_app(user_input, top_n=3)

        if not candidates:
            # Ничего не найдено → подсказка установки
            suggestions = self.suggest_installation(user_input)
            return LaunchResult(
                success=False,
                message=self._format_not_found(user_input, suggestions),
                app_name=user_input,
            )

        best = candidates[0]

        # Если confidence слишком низкая — уточнить
        if best.confidence < self.CONFIDENCE_THRESHOLD:
            names = [c.app.display_name for c in candidates]
            return LaunchResult(
                success=False,
                message=(
                    f"❓ Не уверен, какое приложение вы имеете в виду.\n"
                    f"Найдены похожие:\n"
                    + "\n".join(f"  • {n}" for n in names)
                    + "\nУточните, какое запустить."
                ),
                app_name=user_input,
            )

        # Запуск
        cmd = self.resolve_launch_command(best.app)
        logger.info(
            "Запуск %s (source=%s, cmd=%s, conf=%.2f)",
            best.app.display_name, best.app.source, cmd, best.confidence,
        )

        try:
            proc = subprocess.Popen(
                ["nohup"] + shlex.split(cmd),
                shell=False,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            pid = proc.pid

            result = LaunchResult(
                success=True,
                message=f"✅ Приложение {best.app.display_name} запущено.",
                pid=pid,
                command=cmd,
                app_name=best.app.display_name,
            )

            # Асинхронная проверка PID через VERIFY_DELAY секунд
            self._schedule_verify(result)

            # Записываем в историю
            self._launch_history.append({
                "app": best.app.display_name,
                "command": cmd,
                "pid": pid,
                "time": time.time(),
                "source": best.app.source,
                "confidence": best.confidence,
            })

            return result

        except Exception as e:
            logger.error("Ошибка запуска %s: %s", cmd, e)

            # Попробовать fallback (если есть другие кандидаты)
            if len(candidates) > 1:
                return self._try_fallback_launch(candidates[1:], user_input)

            return LaunchResult(
                success=False,
                message=f"❌ Ошибка запуска {best.app.display_name}: {e}",
                app_name=best.app.display_name,
            )

    def _try_fallback_launch(self, candidates: List[AppCandidate],
                              user_input: str) -> LaunchResult:
        """Попытка запуска следующего кандидата (fallback)."""
        for cand in candidates:
            if cand.confidence < 0.3:
                break
            cmd = self.resolve_launch_command(cand.app)
            try:
                proc = subprocess.Popen(
                    ["nohup"] + shlex.split(cmd),
                    shell=False,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return LaunchResult(
                    success=True,
                    message=f"✅ Приложение {cand.app.display_name} запущено (fallback).",
                    pid=proc.pid,
                    command=cmd,
                    app_name=cand.app.display_name,
                )
            except Exception:
                continue
        return LaunchResult(
            success=False,
            message=f"❌ Не удалось запустить приложение «{user_input}».",
            app_name=user_input,
        )

    # ──────────────────────────────────────────────────
    #  ЭТАП 5: Верификация запуска
    # ──────────────────────────────────────────────────

    def verify_launch(self, pid: int) -> bool:
        """
        Проверяет что процесс жив и не крашнулся.

        Args:
            pid: PID запущенного процесса.

        Returns:
            True если процесс жив.
        """
        try:
            # Проверяем через /proc (Linux)
            if os.path.exists(f"/proc/{pid}"):
                return True
            # Или через os.kill(0) — не убивает, только проверяет
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except Exception:
            return False

    def _schedule_verify(self, result: LaunchResult):
        """Асинхронная проверка PID через VERIFY_DELAY секунд."""
        if not result.pid:
            return

        def _check():
            time.sleep(self.VERIFY_DELAY)
            alive = self.verify_launch(result.pid)
            result.verified = True
            if not alive:
                logger.warning(
                    "Процесс %d (%s) не найден через %.1f сек — вероятный crash",
                    result.pid, result.app_name, self.VERIFY_DELAY,
                )
            else:
                logger.debug(
                    "Процесс %d (%s) жив (проверен через %.1f сек)",
                    result.pid, result.app_name, self.VERIFY_DELAY,
                )

        t = threading.Thread(target=_check, daemon=True)
        t.start()

    # ──────────────────────────────────────────────────
    #  ЭТАП 6-7: Подсказка установки (package manager + web)
    # ──────────────────────────────────────────────────

    def suggest_installation(self, app_name: str) -> List[InstallSuggestion]:
        """
        Если приложение не найдено — ищет в:
          1. Системный пакетный менеджер (pacman/apt/dnf)
          2. AUR (через yay, если доступен)
          3. Flatpak remotes
          4. Snap store
          5. Реальный веб-поиск (WebSearchEngine)
          6. Web search ссылка (fallback)

        При неудаче с multi-word запросом — пробуем каждое слово отдельно.
        Честно сообщает если пакет НЕ найден (вместо мусорных результатов).

        Returns:
            Список подсказок установки.
        """
        query = self._normalize_input(app_name)
        suggestions: List[InstallSuggestion] = []

        # 1. Пакетный менеджер
        pkg = self._search_package_manager(query)
        if pkg:
            suggestions.append(pkg)

        # 1.5. AUR (yay/paru)
        if not suggestions:
            aur = self._search_aur(query)
            if aur:
                suggestions.append(aur)

        # 2. Flatpak remotes
        fp = self._search_flatpak_remote(query)
        if fp:
            suggestions.append(fp)

        # 3. Snap store
        sp = self._search_snap_store(query)
        if sp:
            suggestions.append(sp)

        # 3.5. Word-by-word fallback: if multi-word query found nothing,
        #       try each word individually (e.g. "мессенджер max" → "max")
        _NOISE_WORDS = {
            "и", "или", "а", "но", "для", "в", "на", "из", "с", "по",
            "мне", "мой", "это", "его", "её", "их", "мы", "вы", "они",
            "через", "потом", "тоже", "ещё", "еще", "все", "весь", "всё",
        }
        has_real = any(s.method != "web" for s in suggestions)
        if not has_real and " " in query:
            words = [w for w in query.split() if len(w) >= 2 and w not in _NOISE_WORDS]
            for word in words:
                pkg2 = self._search_package_manager(word)
                if pkg2:
                    suggestions.insert(0, pkg2)
                    break
                aur2 = self._search_aur(word)
                if aur2:
                    suggestions.insert(0, aur2)
                    break

        # 4. Веб-поиск:
        #    - Если нет реальных пакетов → веб-поиск обязателен
        #    - Если пакеты найдены, но НИ ОДИН не exact match → тоже ищем (для
        #      коротких запросов вроде "Max" пакетные менеджеры часто дают мусор)
        has_real = any(s.method != "web" for s in suggestions)
        has_exact = any(
            s.method != "web" and s.package_name.lower() == query.lower()
            for s in suggestions
        )
        if not has_exact:
            web_result = self._try_web_search_install(query)
            if web_result:
                # Если вообще нет пакетов — ставим web первой
                if not has_real:
                    suggestions.insert(0, web_result)
                else:
                    # Есть неточные пакеты — ставим web перед DDG-fallback (последним)
                    suggestions.insert(0, web_result)

        # 5. Web search ссылка (fallback)
        suggestions.append(InstallSuggestion(
            method="web",
            command="",
            package_name=query,
            url=f"https://duckduckgo.com/?q={_url_encode(query + ' official linux install')}",
            note="Ручной поиск в интернете",
        ))

        return suggestions

    @staticmethod
    def _try_web_search_install(query: str) -> Optional[InstallSuggestion]:
        """Попытка найти инструкцию по установке через WebSearchEngine.

        Стратегия поиска:
          1. Первый запрос: «{query} скачать установить Linux»
          2. Если fail — пробуем: «{query} мессенджер/приложение скачать Linux deb rpm»
          3. Возвращаем InstallSuggestion с summary из поисковика
        """
        try:
            from lina.core.web_search_engine import get_web_search_engine
            engine = get_web_search_engine()

            # Несколько формулировок запроса (от общего к узкому)
            search_queries = [
                f"{query} скачать установить Linux",
                f"{query} Linux deb rpm скачать",
            ]

            for sq in search_queries:
                resp = engine.search(sq)
                if resp.success and resp.summary:
                    # Пытаемся найти наиболее релевантный URL
                    best_url = ""
                    if resp.results:
                        # Предпочитаем: официальный сайт > вики > обзоры
                        for r in resp.results:
                            url_l = r.url.lower()
                            if query.lower().replace(" ", "") in url_l:
                                best_url = r.url
                                break
                        if not best_url:
                            best_url = resp.results[0].url

                    return InstallSuggestion(
                        method="web",
                        command="",
                        package_name=query,
                        note=resp.summary[:800],
                        url=best_url,
                    )
        except Exception as e:
            logger.debug("Web search install fallback failed: %s", e)
        return None

        return suggestions

    def _search_package_manager(self, query: str) -> Optional[InstallSuggestion]:
        """Поиск в системном пакетном менеджере."""
        distro = self._detect_distro()

        if distro == "arch" and shutil.which("pacman"):
            return self._search_pacman(query)
        elif distro == "debian" and shutil.which("apt"):
            return self._search_apt(query)
        elif distro == "fedora" and shutil.which("dnf"):
            return self._search_dnf(query)
        return None

    def _detect_distro(self) -> str:
        """Определяет семейство дистрибутива."""
        if self._distro:
            return self._distro
        try:
            with open("/etc/os-release", "r") as f:
                content = f.read().lower()
            if any(d in content for d in ("arch", "cachyos", "endeavour", "manjaro")):
                self._distro = "arch"
            elif any(d in content for d in ("debian", "ubuntu", "mint", "pop")):
                self._distro = "debian"
            elif any(d in content for d in ("fedora", "centos", "rhel", "rocky")):
                self._distro = "fedora"
            else:
                self._distro = "unknown"
        except Exception:
            self._distro = "unknown"
        return self._distro

    @staticmethod
    def _search_pacman(query: str) -> Optional[InstallSuggestion]:
        """Поиск в pacman с проверкой релевантности.

        Не возвращает первый случайный результат — проверяет, что имя
        пакета действительно соответствует запросу (exact/prefix/contains).
        """
        try:
            r = subprocess.run(
                ["pacman", "-Ss", query],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return None
            lines = r.stdout.strip().splitlines()
            query_low = query.lower().strip()

            # Собираем все пакеты с описаниями для ранжирования
            candidates: list = []  # (pkg_name, pkg_full, description, score)
            i = 0
            while i < len(lines):
                line = lines[i]
                if line.startswith((" ", "\t")):
                    i += 1
                    continue
                if "/" in line:
                    pkg_full = line.split()[0]
                    pkg_name = pkg_full.split("/")[-1].lower()
                    # Описание — следующая строка (с отступом)
                    desc = ""
                    if i + 1 < len(lines) and lines[i + 1].startswith((" ", "\t")):
                        desc = lines[i + 1].strip().lower()

                    # Рассчитываем релевантность
                    score = 0.0
                    if pkg_name == query_low:
                        score = 1.0        # точное совпадение имени
                    elif pkg_name.startswith(query_low + "-"):
                        score = 0.85       # имя вида query-suffix (доп. вариант)
                    elif pkg_name.startswith(query_low):
                        # Prefix match — штрафуем за большой суффикс
                        # "max" vs "maxcso" → ratio 0.5 → score 0.5
                        ratio = len(query_low) / len(pkg_name)
                        score = 0.4 + ratio * 0.4  # 0.4..0.8
                    elif query_low in pkg_name:
                        score = 0.5        # запрос — подстрока имени

                    if score >= 0.65:      # порог: минимум — хорошее совпадение
                        candidates.append((pkg_name, pkg_full, desc, score))
                i += 1

            if not candidates:
                return None

            # Лучший кандидат
            candidates.sort(key=lambda c: c[3], reverse=True)
            best_name, best_full, _desc, _score = candidates[0]
            return InstallSuggestion(
                method="pacman",
                command=f"sudo pacman -S {best_name}",
                package_name=best_name,
                source=best_full.split("/")[0],
            )
        except Exception:
            pass
        return None

    @staticmethod
    def _search_aur(query: str) -> Optional[InstallSuggestion]:
        """Поиск в AUR через yay/paru с проверкой релевантности."""
        helper = shutil.which("yay") or shutil.which("paru")
        if not helper:
            return None
        helper_name = os.path.basename(helper)
        try:
            r = subprocess.run(
                [helper, "-Ss", query],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return None
            lines = r.stdout.strip().splitlines()
            query_low = query.lower().strip()

            # Ранжирование по релевантности (как в _search_pacman)
            candidates: list = []
            i = 0
            while i < len(lines):
                line = lines[i]
                if line.startswith((" ", "\t")):
                    i += 1
                    continue
                if "/" in line:
                    pkg_full = line.split()[0]
                    pkg_name = pkg_full.split("/")[-1].lower()
                    repo = pkg_full.split("/")[0]
                    desc = ""
                    if i + 1 < len(lines) and lines[i + 1].startswith((" ", "\t")):
                        desc = lines[i + 1].strip().lower()

                    score = 0.0
                    if pkg_name == query_low:
                        score = 1.0
                    elif pkg_name.startswith(query_low + "-"):
                        score = 0.85
                    elif pkg_name.startswith(query_low):
                        ratio = len(query_low) / len(pkg_name)
                        score = 0.4 + ratio * 0.4
                    elif query_low in pkg_name:
                        score = 0.5

                    if score >= 0.65:
                        candidates.append((pkg_name, pkg_full, repo, score))
                i += 1

            if not candidates:
                return None
            candidates.sort(key=lambda c: c[3], reverse=True)
            best_name, best_full, repo, _score = candidates[0]
            return InstallSuggestion(
                method="aur" if repo == "aur" else helper_name,
                command=f"{helper_name} -S {best_name}",
                package_name=best_name,
                source=repo,
            )
        except Exception:
            pass
        return None

    @staticmethod
    def _search_apt(query: str) -> Optional[InstallSuggestion]:
        try:
            r = subprocess.run(
                ["apt", "search", query],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "LANG": "C.UTF-8"},
            )
            if r.returncode != 0 or not r.stdout.strip():
                return None
            for line in r.stdout.strip().splitlines():
                if "/" in line and not line.startswith("WARNING"):
                    pkg_name = line.split("/")[0].strip()
                    if pkg_name and query.lower() in line.lower():
                        return InstallSuggestion(
                            method="apt",
                            command=f"sudo apt install {pkg_name}",
                            package_name=pkg_name,
                        )
        except Exception:
            pass
        return None

    @staticmethod
    def _search_dnf(query: str) -> Optional[InstallSuggestion]:
        try:
            r = subprocess.run(
                ["dnf", "search", query],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "LANG": "C.UTF-8"},
            )
            if r.returncode != 0 or not r.stdout.strip():
                return None
            for line in r.stdout.strip().splitlines():
                if ":" in line and "=" not in line and query.lower() in line.lower():
                    pkg_name = line.split(":")[0].split(".")[0].strip()
                    if pkg_name:
                        return InstallSuggestion(
                            method="dnf",
                            command=f"sudo dnf install {pkg_name}",
                            package_name=pkg_name,
                        )
        except Exception:
            pass
        return None

    @staticmethod
    def _search_flatpak_remote(query: str) -> Optional[InstallSuggestion]:
        if not shutil.which("flatpak"):
            return None
        try:
            r = subprocess.run(
                ["flatpak", "search", query],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return None
            lines = r.stdout.strip().splitlines()
            query_low = query.lower()
            for line in lines:
                parts = line.split("\t")
                if len(parts) >= 3:
                    name = parts[0].strip()
                    app_id = parts[2].strip()
                    name_low = name.lower()
                    id_low = app_id.lower()
                    id_last = id_low.split(".")[-1]
                    # Проверяем релевантность: имя/ID точно или как слово
                    if (query_low == name_low or
                        query_low == id_last or
                        query_low in name_low.split() or
                        name_low.startswith(query_low + " ") or
                        name_low.startswith(query_low + "-") or
                        id_last.startswith(query_low + "-")):
                        return InstallSuggestion(
                            method="flatpak",
                            command=f"flatpak install flathub {app_id}",
                            package_name=app_id,
                            source=f"Flathub ({name})",
                        )
        except Exception:
            pass
        return None

    @staticmethod
    def _search_snap_store(query: str) -> Optional[InstallSuggestion]:
        if not shutil.which("snap"):
            return None
        try:
            r = subprocess.run(
                ["snap", "find", query],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return None
            lines = r.stdout.strip().splitlines()
            query_low = query.lower()
            for line in lines[1:]:  # skip header
                parts = line.split()
                if parts:
                    name = parts[0]
                    name_low = name.lower()
                    if (query_low == name_low or
                        name_low.startswith(query_low + "-") or
                        name_low.startswith(query_low)):
                        return InstallSuggestion(
                            method="snap",
                            command=f"sudo snap install {name}",
                            package_name=name,
                        )
        except Exception:
            pass
        return None

    # ──────────────────────────────────────────────────
    #  Formatting
    # ──────────────────────────────────────────────────

    @staticmethod
    def _format_not_found(app_name: str, suggestions: List[InstallSuggestion]) -> str:
        """Форматирование ответа «не найдено»."""
        parts = [f"❌ Приложение «{app_name}» не найдено на этом компьютере."]

        install_parts = []
        for s in suggestions:
            if s.method == "web":
                install_parts.append(
                    f"🔍 Поиск в интернете: {s.url}"
                )
            else:
                label = {
                    "pacman": "pacman (Arch)",
                    "apt": "apt (Debian/Ubuntu)",
                    "dnf": "dnf (Fedora)",
                    "flatpak": "Flatpak",
                    "snap": "Snap",
                }.get(s.method, s.method)
                install_parts.append(f"{label}: {s.command}")
                if s.source:
                    install_parts[-1] += f"  ({s.source})"

        if install_parts:
            parts.append("\n💡 Варианты установки:")
            for ip in install_parts:
                parts.append(f"  • {ip}")

        return "\n".join(parts)

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для диагностики."""
        return {
            "index_size": len(self._app_index),
            "index_age_sec": round(time.time() - self._index_time, 1) if self._index_time else None,
            "launches_total": len(self._launch_history),
            "distro": self._distro or "not detected",
        }


def _url_encode(text: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(text)


# ═══════════════════════════════════════════════════════════════════════════════
#  Синглтон
# ═══════════════════════════════════════════════════════════════════════════════

_resolver: Optional[ApplicationResolver] = None


def get_resolver() -> ApplicationResolver:
    """Получить (или создать) экземпляр ApplicationResolver."""
    global _resolver
    if _resolver is None:
        _resolver = ApplicationResolver()
    return _resolver
