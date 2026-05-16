"""
Lina — Универсальный запуск приложений.

Умный поиск и запуск любого приложения на системе:
  1. Поиск бинарника через PATH (shutil.which)
  2. Поиск .desktop файлов (панель приложений KDE/GNOME)
  3. Flatpak приложения
  4. Snap приложения
  5. AppImage файлы (~/Applications, ~/.local/bin)
  6. Если ничего не найдено — поиск в интернете для установки

Покрывает ВСЕ приложения, которые видны в панели приложений.
"""

import os
import re
import glob
import shlex
import shutil
import subprocess
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from configparser import ConfigParser

logger = logging.getLogger("lina.tools.app_launcher")


# ═══════════════════════════════════════════════════════════════════════════════
#  Результат поиска приложения
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AppMatch:
    """Найденное приложение."""
    name: str                    # Отображаемое имя (из .desktop или бинарник)
    exec_cmd: str                # Команда запуска
    source: str                  # Откуда: 'binary', 'desktop', 'flatpak', 'snap', 'appimage'
    score: int = 0               # Оценка совпадения (выше = лучше)
    icon: str = ""               # Иконка (для уведомлений)
    desktop_file: str = ""       # Путь к .desktop файлу

    def __repr__(self):
        return f"AppMatch({self.name!r}, cmd={self.exec_cmd!r}, src={self.source}, score={self.score})"


# ═══════════════════════════════════════════════════════════════════════════════
#  Кэш .desktop файлов
# ═══════════════════════════════════════════════════════════════════════════════

_desktop_cache: Optional[List[dict]] = None
_desktop_cache_time: float = 0.0
_CACHE_TTL = 300.0  # 5 минут


def _get_desktop_dirs() -> List[str]:
    """Все стандартные каталоги .desktop файлов."""
    dirs = []
    # Стандартные XDG пути
    xdg_data = os.environ.get("XDG_DATA_DIRS", "/usr/share:/usr/local/share")
    for d in xdg_data.split(":"):
        p = os.path.join(d, "applications")
        if os.path.isdir(p):
            dirs.append(p)
    # Пользовательские
    home = Path.home()
    user_dirs = [
        home / ".local" / "share" / "applications",
        home / ".local" / "share" / "flatpak" / "exports" / "share" / "applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        home / "snap",
    ]
    for d in user_dirs:
        if d.is_dir():
            dirs.append(str(d))
    return dirs


def _parse_desktop_file(filepath: str) -> Optional[dict]:
    """Парсит .desktop файл, извлекает Name, Exec, Icon, Keywords."""
    try:
        cp = ConfigParser(interpolation=None, strict=False)
        cp.read(filepath, encoding="utf-8")
        if not cp.has_section("Desktop Entry"):
            return None
        entry = dict(cp["Desktop Entry"])
        # Пропускаем скрытые и NoDisplay
        if entry.get("nodisplay", "").lower() == "true":
            return None
        if entry.get("hidden", "").lower() == "true":
            return None
        if entry.get("type", "").lower() not in ("application", ""):
            return None
        exec_cmd = entry.get("exec", "").strip()
        if not exec_cmd:
            return None
        # Убираем %u %U %f %F и другие placeholder-ы
        exec_cmd = re.sub(r'\s+%[a-zA-Z]', '', exec_cmd).strip()
        name = entry.get("name", "")
        # Собираем все варианты имён для поиска
        names = set()
        if name:
            names.add(name.lower())
        # GenericName, Name[ru], Keywords — всё полезно для поиска
        for key in entry:
            if key.startswith("name[") or key.startswith("genericname"):
                val = entry[key].strip()
                if val:
                    names.add(val.lower())
        keywords = entry.get("keywords", "")
        if keywords:
            for kw in keywords.split(";"):
                kw = kw.strip().lower()
                if kw:
                    names.add(kw)
        # StartupWMClass тоже полезен
        wm_class = entry.get("startupwmclass", "").strip().lower()
        if wm_class:
            names.add(wm_class)
        return {
            "name": name,
            "exec": exec_cmd,
            "icon": entry.get("icon", ""),
            "search_names": names,
            "file": filepath,
        }
    except Exception as e:
        logger.debug("Ошибка парсинга %s: %s", filepath, e)
        return None


def _load_desktop_cache() -> List[dict]:
    """Загружает и кэширует все .desktop файлы."""
    import time
    global _desktop_cache, _desktop_cache_time

    now = time.time()
    if _desktop_cache is not None and (now - _desktop_cache_time) < _CACHE_TTL:
        return _desktop_cache

    entries = []
    seen_execs = set()
    for d in _get_desktop_dirs():
        for f in glob.glob(os.path.join(d, "**", "*.desktop"), recursive=True):
            parsed = _parse_desktop_file(f)
            if parsed and parsed["exec"] not in seen_execs:
                entries.append(parsed)
                seen_execs.add(parsed["exec"])

    _desktop_cache = entries
    _desktop_cache_time = now
    logger.debug("Загружено %d .desktop файлов", len(entries))
    return entries


# ═══════════════════════════════════════════════════════════════════════════════
#  Flatpak поиск
# ═══════════════════════════════════════════════════════════════════════════════

def _list_flatpak_apps() -> List[dict]:
    """Получает список установленных Flatpak приложений."""
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
                apps.append({
                    "app_id": app_id,
                    "name": name,
                    "search_names": {
                        name.lower(),
                        app_id.lower(),
                        app_id.split(".")[-1].lower(),
                    },
                })
            elif len(parts) == 1 and parts[0].strip():
                app_id = parts[0].strip()
                apps.append({
                    "app_id": app_id,
                    "name": app_id.split(".")[-1],
                    "search_names": {
                        app_id.lower(),
                        app_id.split(".")[-1].lower(),
                    },
                })
        return apps
    except Exception as e:
        logger.debug("Ошибка flatpak list: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
#  Snap поиск
# ═══════════════════════════════════════════════════════════════════════════════

def _list_snap_apps() -> List[dict]:
    """Получает список установленных Snap приложений."""
    if not shutil.which("snap"):
        return []
    try:
        r = subprocess.run(
            ["snap", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return []
        apps = []
        for line in r.stdout.strip().splitlines()[1:]:  # пропускаем заголовок
            parts = line.split()
            if parts:
                name = parts[0]
                if name in ("core", "core18", "core20", "core22", "snapd", "bare", "gnome-3-38-2004"):
                    continue
                apps.append({
                    "name": name,
                    "search_names": {name.lower()},
                })
        return apps
    except Exception as e:
        logger.debug("Ошибка snap list: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
#  AppImage поиск
# ═══════════════════════════════════════════════════════════════════════════════

def _find_appimages() -> List[dict]:
    """Ищет AppImage файлы в типичных местах."""
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
            if name_lower.endswith(".appimage") or (f.is_file() and os.access(str(f), os.X_OK) and "appimage" in name_lower):
                app_name = re.sub(r'[-_.]appimage$', '', f.stem, flags=re.IGNORECASE)
                app_name = re.sub(r'[-_]x86_64$|[-_]amd64$|[-_]\d+\.\d+.*$', '', app_name, flags=re.IGNORECASE)
                apps.append({
                    "name": app_name,
                    "path": str(f),
                    "search_names": {app_name.lower(), f.stem.lower()},
                })
    return apps


# ═══════════════════════════════════════════════════════════════════════════════
#  Алиасы русских имён → паттерны поиска
# ═══════════════════════════════════════════════════════════════════════════════

_RU_ALIASES: Dict[str, List[str]] = {
    # Браузеры
    "хром": ["chrome", "chromium", "google-chrome"],
    "гугл": ["chrome", "google-chrome"],
    "гугл хром": ["chrome", "google-chrome"],
    "браузер": ["chrome", "firefox", "chromium", "brave", "yandex"],
    "фаерфокс": ["firefox"],
    "яндекс браузер": ["yandex", "browser"],
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
    # Код
    "код": ["code", "vscodium", "visual studio"],
    "вскод": ["code", "vscodium"],
    "vscode": ["code", "visual studio"],
    # Мессенджеры
    "телеграм": ["telegram"],
    "дискорд": ["discord"],
    "вотсап": ["whatsapp"],
    "скайп": ["skype"],
    "зум": ["zoom"],
    # Медиа
    "видеоплеер": ["vlc", "mpv", "totem", "celluloid", "haruna"],
    "музыка": ["spotify", "rhythmbox", "elisa", "strawberry", "audacious"],
    "спотифай": ["spotify"],
    # Офис
    "ворд": ["libreoffice writer", "writer"],
    "таблицы": ["libreoffice calc", "calc"],
    "презентации": ["libreoffice impress", "impress"],
    "офис": ["libreoffice", "onlyoffice"],
    # Графика
    "фоторедактор": ["gimp", "krita", "pinta"],
    "рисование": ["krita", "inkscape"],
    # Системные
    "настройки": ["systemsettings", "gnome-control-center", "settings"],
    "системный монитор": ["ksysguard", "plasma-systemmonitor", "gnome-system-monitor", "htop"],
    "монитор": ["plasma-systemmonitor", "gnome-system-monitor", "ksysguard"],
    # Игры
    "стим": ["steam"],
    "лутрис": ["lutris"],
}


# ═══════════════════════════════════════════════════════════════════════════════
#  Фаззи-поиск
# ═══════════════════════════════════════════════════════════════════════════════

def _fuzzy_score(query: str, target: str) -> int:
    """
    Подсчёт релевантности: чем выше, тем лучше.
    0 = нет совпадения.
    """
    query = query.lower().strip()
    target = target.lower().strip()

    if not query or not target:
        return 0

    # Точное совпадение
    if query == target:
        return 1000

    # target начинается с query
    if target.startswith(query):
        return 800

    # query является подстрокой target
    if query in target:
        return 600

    # target содержится в query (для коротких имён)
    if target in query:
        return 500

    # Пословный поиск: все слова query есть в target
    query_words = query.split()
    target_words = target.split()
    if query_words and all(any(qw in tw for tw in target_words) for qw in query_words):
        return 400

    # Частичное совпадение первых символов
    min_len = min(len(query), len(target))
    common_prefix = 0
    for i in range(min_len):
        if query[i] == target[i]:
            common_prefix += 1
        else:
            break
    if common_prefix >= 3:
        return 200 + common_prefix * 10

    return 0


def _normalize_query(query: str) -> str:
    """Нормализация пользовательского запроса."""
    q = query.lower().strip()
    # Убираем типичные prefixes
    for prefix in ("приложение ", "программу ", "программа ", "открой ", "запусти ", "запуск "):
        if q.startswith(prefix):
            q = q[len(prefix):]
    return q.strip()


# ═══════════════════════════════════════════════════════════════════════════════
#  Главный поиск приложений
# ═══════════════════════════════════════════════════════════════════════════════

class AppLauncher:
    """
    Универсальный поиск и запуск приложений.

    Ищет по ВСЕМ источникам:
      - PATH бинарники
      - .desktop файлы (то что видно в панели приложений)
      - Flatpak
      - Snap
      - AppImage
    """

    def find_app(self, query: str) -> List[AppMatch]:
        """
        Ищет приложение по имени. Возвращает список совпадений,
        отсортированный по релевантности (лучшие первые).
        """
        query_norm = _normalize_query(query)
        results: List[AppMatch] = []

        # Собираем все поисковые термины (включая русские алиасы)
        search_terms = [query_norm]
        for alias, patterns in _RU_ALIASES.items():
            if _fuzzy_score(query_norm, alias) >= 500:
                search_terms.extend(patterns)

        logger.debug("Поиск приложения: %r → термины: %s", query, search_terms)

        # 1. Прямой поиск бинарника в PATH
        for term in search_terms:
            binary = shutil.which(term)
            if binary:
                match = AppMatch(
                    name=term,
                    exec_cmd=binary,
                    source="binary",
                    score=_fuzzy_score(query_norm, term) + 50,  # бонус за прямое совпадение
                )
                results.append(match)

        # 2. Поиск по .desktop файлам
        for entry in _load_desktop_cache():
            best_name_score = 0   # Лучший скор по имени/keywords
            best_exec_score = 0   # Лучший скор по бинарнику exec
            for term in search_terms:
                for sn in entry["search_names"]:
                    s = _fuzzy_score(term, sn)
                    best_name_score = max(best_name_score, s)
                # Также ищем в имени команды
                exec_base = os.path.basename(entry["exec"].split()[0]).lower()
                s = _fuzzy_score(term, exec_base)
                best_exec_score = max(best_exec_score, s)
            # Имя приложения важнее, чем совпадение по exec-бинарнику.
            # Это предотвращает выбор Okko (web app in Chrome) вместо Chrome.
            combined = max(best_name_score + 200, best_exec_score)
            # Штраф для веб-приложений (--app-id= или --profile-directory=)
            # которые матчатся только по exec-бинарнику, а не по имени
            if best_name_score == 0 and ("--app-id=" in entry["exec"] or "--profile-directory=" in entry["exec"]):
                combined = max(0, combined - 500)
            if combined > 0:
                results.append(AppMatch(
                    name=entry["name"],
                    exec_cmd=entry["exec"],
                    source="desktop",
                    score=combined + 100,  # бонус .desktop
                    icon=entry.get("icon", ""),
                    desktop_file=entry.get("file", ""),
                ))

        # 3. Flatpak приложения
        for fp in _list_flatpak_apps():
            best_score = 0
            for term in search_terms:
                for sn in fp["search_names"]:
                    s = _fuzzy_score(term, sn)
                    best_score = max(best_score, s)
            if best_score > 0:
                results.append(AppMatch(
                    name=fp["name"],
                    exec_cmd=f"flatpak run {fp['app_id']}",
                    source="flatpak",
                    score=best_score + 80,
                ))

        # 4. Snap приложения
        for sp in _list_snap_apps():
            best_score = 0
            for term in search_terms:
                for sn in sp["search_names"]:
                    s = _fuzzy_score(term, sn)
                    best_score = max(best_score, s)
            if best_score > 0:
                results.append(AppMatch(
                    name=sp["name"],
                    exec_cmd=f"snap run {sp['name']}",
                    source="snap",
                    score=best_score + 70,
                ))

        # 5. AppImage
        for ai in _find_appimages():
            best_score = 0
            for term in search_terms:
                for sn in ai["search_names"]:
                    s = _fuzzy_score(term, sn)
                    best_score = max(best_score, s)
            if best_score > 0:
                results.append(AppMatch(
                    name=ai["name"],
                    exec_cmd=ai["path"],
                    source="appimage",
                    score=best_score + 60,
                ))

        # Дедупликация: если одно и то же exec_cmd найдено несколькими путями,
        # оставляем с наивысшим score. Разные приложения с одним бинарником
        # (например Chrome и Okko-webapp) - НЕ дедуплицируем, это разные apps.
        seen: Dict[str, AppMatch] = {}
        for m in results:
            # Ключ = exec_cmd целиком (не только базовое имя), чтобы
            # не склеивать Chrome и Chrome-webapp c --profile-directory
            key = m.exec_cmd.strip().lower()
            if key not in seen or m.score > seen[key].score:
                seen[key] = m
        results = list(seen.values())

        # Сортируем по score (убывание)
        results.sort(key=lambda m: m.score, reverse=True)

        # Отсекаем слабые совпадения (< 400 = случайные частичные совпадения)
        results = [m for m in results if m.score >= 400]

        return results

    def launch(self, query: str) -> Tuple[bool, str]:
        """
        Найти и запустить приложение.

        Returns:
            (success, message)
        """
        matches = self.find_app(query)

        if not matches:
            # Ничего не найдено — ищем в интернете
            return False, self._suggest_install(query)

        best = matches[0]
        logger.info("Запуск: %s (source=%s, cmd=%s, score=%d)",
                     best.name, best.source, best.exec_cmd, best.score)

        try:
            # Запускаем в фоне, отвязываем от терминала
            cmd = best.exec_cmd
            subprocess.Popen(
                ["nohup"] + shlex.split(cmd),
                shell=False,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            source_ru = {
                "binary": "бинарник в PATH",
                "desktop": ".desktop файл",
                "flatpak": "Flatpak",
                "snap": "Snap",
                "appimage": "AppImage",
            }.get(best.source, best.source)

            return True, f"✅ Запущено: {best.name} ({source_ru})"

        except Exception as e:
            logger.error("Ошибка запуска %s: %s", best.exec_cmd, e)
            return False, f"❌ Ошибка запуска {best.name}: {e}"

    def _suggest_install(self, query: str) -> str:
        """Подсказка по установке ненайденного приложения."""
        query_norm = _normalize_query(query)
        suggestions = []

        # Проверяем, можно ли найти в пакетном менеджере
        pkg_search = self._search_package_manager(query_norm)
        if pkg_search:
            suggestions.append(pkg_search)

        # Проверяем Flatpak remote
        flatpak_search = self._search_flatpak_remote(query_norm)
        if flatpak_search:
            suggestions.append(flatpak_search)

        # Веб-поиск как последнее средство
        suggestions.append(
            f"🔍 Поиск в интернете: https://duckduckgo.com/?q={_url_encode(query_norm + ' linux install')}"
        )

        parts = [f"❌ Приложение «{query}» не найдено на этом компьютере."]
        if suggestions:
            parts.append("\n💡 Варианты установки:")
            for s in suggestions:
                parts.append(f"  • {s}")
        return "\n".join(parts)

    @staticmethod
    def _search_package_manager(query: str) -> Optional[str]:
        """Поиск пакета в системном менеджере."""
        # apt
        if shutil.which("apt"):
            try:
                r = subprocess.run(
                    ["apt", "search", query],
                    capture_output=True, text=True, timeout=15,
                    env={**os.environ, "LANG": "C.UTF-8"},
                )
                if r.returncode == 0 and r.stdout.strip():
                    lines = r.stdout.strip().splitlines()
                    # Берём первые 3 релевантных результата
                    found = []
                    for line in lines[:20]:
                        if "/" in line and not line.startswith("WARNING"):
                            pkg_name = line.split("/")[0].strip()
                            if pkg_name and query.lower() in line.lower():
                                found.append(pkg_name)
                        if len(found) >= 3:
                            break
                    if found:
                        pkgs = ", ".join(found)
                        return f"apt: sudo apt install {found[0]}  (найдено: {pkgs})"
            except Exception:
                pass

        # dnf
        if shutil.which("dnf"):
            try:
                r = subprocess.run(
                    ["dnf", "search", query],
                    capture_output=True, text=True, timeout=15,
                    env={**os.environ, "LANG": "C.UTF-8"},
                )
                if r.returncode == 0 and r.stdout.strip():
                    lines = r.stdout.strip().splitlines()
                    found = []
                    for line in lines:
                        if ":" in line and "=" not in line and query.lower() in line.lower():
                            pkg_name = line.split(":")[0].split(".")[0].strip()
                            if pkg_name:
                                found.append(pkg_name)
                        if len(found) >= 3:
                            break
                    if found:
                        pkgs = ", ".join(found)
                        return f"dnf: sudo dnf install {found[0]}  (найдено: {pkgs})"
            except Exception:
                pass

        # pacman
        if shutil.which("pacman"):
            try:
                r = subprocess.run(
                    ["pacman", "-Ss", query],
                    capture_output=True, text=True, timeout=15,
                )
                if r.returncode == 0 and r.stdout.strip():
                    lines = r.stdout.strip().splitlines()
                    found = []
                    for line in lines:
                        if line.startswith((" ", "\t")):
                            continue
                        if "/" in line:
                            pkg_full = line.split()[0]
                            pkg_name = pkg_full.split("/")[-1]
                            if pkg_name:
                                found.append(pkg_name)
                        if len(found) >= 3:
                            break
                    if found:
                        pkgs = ", ".join(found)
                        return f"pacman: sudo pacman -S {found[0]}  (найдено: {pkgs})"
            except Exception:
                pass

        return None

    @staticmethod
    def _search_flatpak_remote(query: str) -> Optional[str]:
        """Поиск в удалённых Flatpak репозиториях."""
        if not shutil.which("flatpak"):
            return None
        try:
            r = subprocess.run(
                ["flatpak", "search", query],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode == 0 and r.stdout.strip():
                lines = r.stdout.strip().splitlines()
                if lines:
                    # Формат: Name\tDescription\tApplication ID\tVersion\tBranch\tRemotes
                    first = lines[0].split("\t")
                    if len(first) >= 3:
                        name = first[0].strip()
                        app_id = first[2].strip()
                        return f"flatpak: flatpak install {app_id}  ({name})"
                    elif first[0].strip():
                        return f"flatpak: flatpak search {query}  (найдены результаты)"
        except Exception:
            pass
        return None

    def list_installed(self) -> List[AppMatch]:
        """Список всех установленных приложений (для диагностики)."""
        all_apps: List[AppMatch] = []

        # .desktop файлы
        for entry in _load_desktop_cache():
            all_apps.append(AppMatch(
                name=entry["name"],
                exec_cmd=entry["exec"],
                source="desktop",
                icon=entry.get("icon", ""),
                desktop_file=entry.get("file", ""),
            ))

        # Flatpak
        for fp in _list_flatpak_apps():
            all_apps.append(AppMatch(
                name=fp["name"],
                exec_cmd=f"flatpak run {fp['app_id']}",
                source="flatpak",
            ))

        # Snap
        for sp in _list_snap_apps():
            all_apps.append(AppMatch(
                name=sp["name"],
                exec_cmd=f"snap run {sp['name']}",
                source="snap",
            ))

        # AppImage
        for ai in _find_appimages():
            all_apps.append(AppMatch(
                name=ai["name"],
                exec_cmd=ai["path"],
                source="appimage",
            ))

        return all_apps


def _url_encode(text: str) -> str:
    """URL-кодирование для поисковых запросов."""
    from urllib.parse import quote_plus
    return quote_plus(text)


# ═══════════════════════════════════════════════════════════════════════════════
#  Синглтон
# ═══════════════════════════════════════════════════════════════════════════════

_launcher: Optional[AppLauncher] = None


def get_launcher() -> AppLauncher:
    """Получить (или создать) экземпляр AppLauncher."""
    global _launcher
    if _launcher is None:
        _launcher = AppLauncher()
    return _launcher
