# -*- coding: utf-8 -*-
"""
Lina — Безопасный редактор системных конфигураций.

Чтение, анализ и модификация системных конфигов с обязательным:
  - Backup перед изменением
  - Diff-превью
  - Whitelist / blacklist директорий
  - Поддержка: INI, conf (key=value), JSON, YAML, TOML

Все мутирующие операции НЕ выполняются без явного подтверждения.
Генерирует diff и команды — решение за пользователем.

Типичные конфиги:
  /etc/fstab, /etc/hosts, /etc/sysctl.conf,
  /etc/pacman.conf, /etc/apt/sources.list,
  /etc/NetworkManager/..., ~/.config/...
"""

import configparser
import difflib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("lina.system.config_editor")


# ─── Константы ──────────────────────────────────────────────────────────────

# Директории, в которых РАЗРЕШЕНО читать конфиги
ALLOWED_READ_DIRS: List[str] = [
    "/etc",
    "/usr/share",
    "/usr/lib/systemd",
    os.path.expanduser("~/.config"),
    os.path.expanduser("~/.local"),
    os.path.expanduser("~"),
]

# Директории, в которых РАЗРЕШЕНА запись (подмножество чтения)
ALLOWED_WRITE_DIRS: List[str] = [
    "/etc",
    os.path.expanduser("~/.config"),
    os.path.expanduser("~/.local"),
]

# Файлы, в которые ЗАПРЕЩЕНА запись (критические для безопасности)
BLACKLISTED_FILES: List[str] = [
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/passwd",
    "/etc/group",
    "/etc/sudoers",
    "/etc/ssh/sshd_config",
    "/etc/pam.d/su",
    "/etc/pam.d/sudo",
    "/etc/security/limits.conf",
    "/etc/crypttab",
    "/etc/fstab",  # слишком опасно без проверки синтаксиса
]

# Расширение backup-файлов
BACKUP_SUFFIX = ".lina-bak"

# Максимальный размер читаемого файла (10 МБ)
MAX_FILE_SIZE = 10 * 1024 * 1024
_MAX_CONFIG_CACHE = 100


# ─── Модели данных ──────────────────────────────────────────────────────────


class ConfigFormat(Enum):
    """Поддерживаемые форматы конфигурации."""
    INI = "ini"           # .ini, .conf (секционный)
    KEYVALUE = "keyvalue"  # key=value (без секций, sysctl.conf)
    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    PLAIN = "plain"       # plain text (hosts, fstab)
    UNKNOWN = "unknown"


@dataclass
class ConfigValue:
    """Одно значение из конфигурации."""
    key: str = ""
    value: str = ""
    section: str = ""     # Для INI: имя секции
    line_number: int = 0
    comment: str = ""     # Inline комментарий
    raw_line: str = ""


@dataclass
class ConfigDiff:
    """Предпросмотр изменения."""
    path: str = ""
    key: str = ""
    old_value: str = ""
    new_value: str = ""
    section: str = ""
    unified_diff: str = ""   # Unified diff text
    safe: bool = True
    reason: str = ""


@dataclass
class ConfigEditResult:
    """Результат применения изменения."""
    success: bool = False
    path: str = ""
    backup_path: str = ""
    message: str = ""
    needs_sudo: bool = False
    command: str = ""      # Команда для применения (если sudo)


@dataclass
class ConfigParseResult:
    """Результат парсинга конфигурации."""
    path: str = ""
    format: ConfigFormat = ConfigFormat.UNKNOWN
    values: List[ConfigValue] = field(default_factory=list)
    sections: List[str] = field(default_factory=list)
    raw_content: str = ""
    error: str = ""
    readable: bool = False


# ─── Вспомогательные функции ────────────────────────────────────────────────


def _detect_format(path: str) -> ConfigFormat:
    """Определить формат конфигурации по расширению и содержимому."""
    ext = Path(path).suffix.lower()

    # По расширению
    ext_map = {
        ".ini": ConfigFormat.INI,
        ".cfg": ConfigFormat.INI,
        ".json": ConfigFormat.JSON,
        ".yaml": ConfigFormat.YAML,
        ".yml": ConfigFormat.YAML,
        ".toml": ConfigFormat.TOML,
    }
    if ext in ext_map:
        return ext_map[ext]

    # Известные файлы
    name = Path(path).name
    known_ini = {
        "pacman.conf", "makepkg.conf", "mkinitcpio.conf",
        "kdeglobals", "kwinrc", "plasmashellrc",
    }
    known_kv = {
        "sysctl.conf", "environment", "locale.conf", "vconsole.conf",
        "hostname",
    }
    known_plain = {
        "hosts", "fstab", "crypttab", "sources.list", "resolv.conf",
        "nsswitch.conf", "hostname",
    }

    if name in known_ini or ext == ".conf":
        return ConfigFormat.INI
    if name in known_kv:
        return ConfigFormat.KEYVALUE
    if name in known_plain:
        return ConfigFormat.PLAIN

    # Эвристика по содержимому
    try:
        head = Path(path).read_text(errors="replace")[:2048]
    except (IOError, PermissionError):
        return ConfigFormat.UNKNOWN

    # JSON?
    stripped = head.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return ConfigFormat.JSON
    # INI? (есть секции [section])
    if re.search(r"^\[.+\]", head, re.MULTILINE):
        return ConfigFormat.INI
    # Key=value? (key=value строки)
    kv_lines = re.findall(r"^[a-zA-Z_][\w.-]*\s*=", head, re.MULTILINE)
    if len(kv_lines) >= 2:
        return ConfigFormat.KEYVALUE

    return ConfigFormat.PLAIN


def _is_path_allowed(path: str, dirs: List[str]) -> bool:
    """Проверить, что путь попадает в whitelist."""
    real = os.path.realpath(path)
    for d in dirs:
        try:
            rd = os.path.realpath(d)
            if real.startswith(rd + os.sep) or real == rd:
                return True
        except (OSError, ValueError):
            continue
    return False


def _is_blacklisted(path: str) -> bool:
    """Проверить, что файл в blacklist."""
    real = os.path.realpath(path)
    for bl in BLACKLISTED_FILES:
        try:
            if os.path.realpath(bl) == real:
                return True
        except (OSError, ValueError):
            continue
    return False


def _needs_sudo(path: str) -> bool:
    """Определить, нужен ли sudo для записи."""
    if os.access(path, os.W_OK):
        return False
    parent = str(Path(path).parent)
    if not os.path.exists(path) and os.access(parent, os.W_OK):
        return False
    return True


# ─── Парсеры ────────────────────────────────────────────────────────────────


def _parse_ini(content: str, path: str) -> ConfigParseResult:
    """Парсить INI/conf формат."""
    result = ConfigParseResult(path=path, format=ConfigFormat.INI, readable=True)
    result.raw_content = content

    parser = configparser.ConfigParser(
        interpolation=None,
        allow_no_value=True,
        strict=False,
        comment_prefixes=("#", ";"),
    )
    # Сохраняем регистр ключей
    parser.optionxform = str  # type: ignore

    try:
        parser.read_string(content)
    except configparser.Error as e:
        # Попробуем как key=value
        return _parse_keyvalue(content, path)

    result.sections = parser.sections()

    for section in parser.sections():
        for key, value in parser.items(section):
            # Найти номер строки
            line_num = 0
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(f"{key}") and "=" in stripped:
                    line_num = i
                    break

            result.values.append(ConfigValue(
                key=key,
                value=value or "",
                section=section,
                line_number=line_num,
                raw_line=f"{key} = {value}" if value else key,
            ))

    return result


def _parse_keyvalue(content: str, path: str) -> ConfigParseResult:
    """Парсить key=value формат (без секций)."""
    result = ConfigParseResult(
        path=path, format=ConfigFormat.KEYVALUE, readable=True,
    )
    result.raw_content = content

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue

        # key=value или key = value
        match = re.match(r"^([a-zA-Z_][\w.-]*)\s*=\s*(.*)", stripped)
        if match:
            key, value = match.group(1), match.group(2)
            # Inline комментарий
            comment = ""
            if "#" in value:
                parts = value.split("#", 1)
                value = parts[0].strip()
                comment = parts[1].strip()

            result.values.append(ConfigValue(
                key=key,
                value=value,
                line_number=i,
                comment=comment,
                raw_line=stripped,
            ))

    return result


def _parse_json(content: str, path: str) -> ConfigParseResult:
    """Парсить JSON формат."""
    result = ConfigParseResult(path=path, format=ConfigFormat.JSON, readable=True)
    result.raw_content = content

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        result.error = f"JSON parse error: {e}"
        return result

    def _flatten(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, (dict, list)):
                    _flatten(v, full_key)
                else:
                    result.values.append(ConfigValue(
                        key=full_key,
                        value=str(v),
                    ))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _flatten(item, f"{prefix}[{i}]")

    _flatten(data)
    return result


def _parse_yaml(content: str, path: str) -> ConfigParseResult:
    """Парсить YAML формат (без внешних зависимостей)."""
    result = ConfigParseResult(path=path, format=ConfigFormat.YAML, readable=True)
    result.raw_content = content

    # Простой парсер для key: value (один уровень + вложенность через точку)
    current_section = ""
    indent_stack: List[Tuple[int, str]] = []

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        match = re.match(r"^([\w.-]+)\s*:\s*(.*)", stripped)
        if not match:
            continue

        key, value = match.group(1), match.group(2).strip()

        # Обновить стек отступов
        while indent_stack and indent_stack[-1][0] >= indent:
            indent_stack.pop()

        if value and not value.startswith("{") and not value.startswith("["):
            # Leaf value
            prefix = ".".join(s[1] for s in indent_stack)
            full_key = f"{prefix}.{key}" if prefix else key
            result.values.append(ConfigValue(
                key=full_key,
                value=value.strip("'\""),
                line_number=i,
                raw_line=stripped,
            ))
        else:
            # Section / nested
            indent_stack.append((indent, key))

    return result


def _parse_plain(content: str, path: str) -> ConfigParseResult:
    """Парсить как plain text (по строкам)."""
    result = ConfigParseResult(path=path, format=ConfigFormat.PLAIN, readable=True)
    result.raw_content = content

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        result.values.append(ConfigValue(
            key=f"line_{i}",
            value=stripped,
            line_number=i,
            raw_line=line,
        ))

    return result


# ─── Основной класс ────────────────────────────────────────────────────────


class ConfigEditor:
    """Безопасный редактор системных конфигураций.

    Правила:
      1. Чтение — только из ALLOWED_READ_DIRS
      2. Запись — только в ALLOWED_WRITE_DIRS
      3. Запись ЗАПРЕЩЕНА в BLACKLISTED_FILES
      4. Backup ОБЯЗАТЕЛЕН перед любым изменением
      5. Diff-превью показывается ДО применения
      6. sudo — автоматическое определение необходимости

    Usage:
        editor = ConfigEditor()
        parsed = editor.read_config("/etc/pacman.conf")
        val = editor.get_value("/etc/pacman.conf", "Color")
        diff = editor.suggest_change("/etc/sysctl.conf", "vm.swappiness", "10")
        result = editor.apply_change("/etc/sysctl.conf", "vm.swappiness", "10")
    """

    def __init__(self):
        self._cache: Dict[str, ConfigParseResult] = {}
        self._backup_dir = Path.home() / ".local" / "share" / "lina" / "config_backups"
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    # ═══════════════════════════════════════════════════════
    #  Чтение
    # ═══════════════════════════════════════════════════════

    def read_config(self, path: str) -> ConfigParseResult:
        """Прочитать и распарсить конфигурационный файл.

        Args:
            path: Абсолютный путь к файлу.

        Returns:
            ConfigParseResult с разобранными значениями.
        """
        path = os.path.expanduser(path)

        # Проверка whitelist
        if not _is_path_allowed(path, ALLOWED_READ_DIRS):
            return ConfigParseResult(
                path=path,
                error=f"Путь вне разрешённых директорий: {path}",
            )

        # Проверка существования
        if not os.path.isfile(path):
            return ConfigParseResult(
                path=path,
                error=f"Файл не найден: {path}",
            )

        # Проверка размера
        try:
            size = os.path.getsize(path)
            if size > MAX_FILE_SIZE:
                return ConfigParseResult(
                    path=path,
                    error=f"Файл слишком большой: {size} байт (макс {MAX_FILE_SIZE})",
                )
        except OSError:
            pass

        # Чтение
        try:
            content = Path(path).read_text(errors="replace")
        except PermissionError:
            # Попробуем через sudo
            try:
                result = subprocess.run(
                    ["sudo", "-n", "cat", path],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    content = result.stdout
                else:
                    return ConfigParseResult(
                        path=path,
                        error=f"Нет доступа к файлу (нужен sudo): {path}",
                    )
            except Exception:
                return ConfigParseResult(
                    path=path,
                    error=f"Нет доступа к файлу: {path}",
                )
        except Exception as e:
            logger.error("Ошибка чтения %s: %s", path, e, exc_info=True)
            return ConfigParseResult(
                path=path,
                error="Ошибка чтения файла.",
            )

        # Определить формат и распарсить
        fmt = _detect_format(path)
        parsers = {
            ConfigFormat.INI: _parse_ini,
            ConfigFormat.KEYVALUE: _parse_keyvalue,
            ConfigFormat.JSON: _parse_json,
            ConfigFormat.YAML: _parse_yaml,
            ConfigFormat.PLAIN: _parse_plain,
        }
        parser = parsers.get(fmt, _parse_plain)
        parsed = parser(content, path)

        # Кэшировать (лимит записей)
        if len(self._cache) >= _MAX_CONFIG_CACHE:
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[path] = parsed
        return parsed

    def get_value(self, path: str, key: str,
                  section: str = "") -> Optional[str]:
        """Получить значение параметра из конфигурации.

        Args:
            path:    Путь к конфигу.
            key:     Имя параметра.
            section: Имя секции (для INI).

        Returns:
            Значение или None.
        """
        # Используем кэш если есть
        if path not in self._cache:
            self.read_config(path)

        parsed = self._cache.get(path)
        if not parsed or parsed.error:
            return None

        for val in parsed.values:
            if val.key == key:
                if section and val.section != section:
                    continue
                return val.value
        return None

    def list_values(self, path: str,
                    section: str = "") -> List[ConfigValue]:
        """Получить все значения из конфигурации.

        Args:
            path:    Путь к конфигу.
            section: Фильтр по секции (опционально).

        Returns:
            Список ConfigValue.
        """
        if path not in self._cache:
            self.read_config(path)

        parsed = self._cache.get(path)
        if not parsed or parsed.error:
            return []

        if section:
            return [v for v in parsed.values if v.section == section]
        return list(parsed.values)

    def list_sections(self, path: str) -> List[str]:
        """Получить список секций (для INI конфигов)."""
        if path not in self._cache:
            self.read_config(path)

        parsed = self._cache.get(path)
        if not parsed:
            return []
        return list(parsed.sections)

    def search_key(self, path: str, pattern: str) -> List[ConfigValue]:
        """Поиск ключей по паттерну (regex).

        Args:
            path:    Путь к конфигу.
            pattern: Regex для поиска.

        Returns:
            Список совпадений.
        """
        if path not in self._cache:
            self.read_config(path)

        parsed = self._cache.get(path)
        if not parsed or parsed.error:
            return []

        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return []

        return [v for v in parsed.values if rx.search(v.key)]

    # ═══════════════════════════════════════════════════════
    #  Diff-превью (suggest_change)
    # ═══════════════════════════════════════════════════════

    def suggest_change(self, path: str, key: str, new_value: str,
                       section: str = "") -> ConfigDiff:
        """Показать diff изменения БЕЗ записи.

        Args:
            path:      Путь к конфигу.
            key:       Имя параметра.
            new_value: Новое значение.
            section:   Секция (для INI).

        Returns:
            ConfigDiff с unified diff и информацией о безопасности.
        """
        path = os.path.expanduser(path)

        # Проверки безопасности
        if _is_blacklisted(path):
            return ConfigDiff(
                path=path, key=key,
                safe=False,
                reason=f"Файл в чёрном списке: {path}",
            )

        if not _is_path_allowed(path, ALLOWED_WRITE_DIRS):
            return ConfigDiff(
                path=path, key=key,
                safe=False,
                reason=f"Запись запрещена в: {path}",
            )

        # Прочитать текущее содержимое
        if path not in self._cache:
            self.read_config(path)

        parsed = self._cache.get(path)
        if not parsed or parsed.error:
            return ConfigDiff(
                path=path, key=key,
                safe=False,
                reason=parsed.error if parsed else "Не удалось прочитать файл",
            )

        # Найти текущее значение
        old_value = ""
        for val in parsed.values:
            if val.key == key:
                if section and val.section != section:
                    continue
                old_value = val.value
                break

        # Сгенерировать новое содержимое
        old_content = parsed.raw_content
        new_content = self._generate_new_content(
            old_content, parsed.format, key, new_value, section,
        )

        # Unified diff
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{Path(path).name}",
            tofile=f"b/{Path(path).name}",
        ))
        unified = "".join(diff_lines)

        return ConfigDiff(
            path=path,
            key=key,
            old_value=old_value,
            new_value=new_value,
            section=section,
            unified_diff=unified,
            safe=True,
        )

    # ═══════════════════════════════════════════════════════
    #  Применение изменений
    # ═══════════════════════════════════════════════════════

    def apply_change(self, path: str, key: str, new_value: str,
                     section: str = "",
                     confirmed: bool = False) -> ConfigEditResult:
        """Применить изменение (с обязательным backup).

        Args:
            path:      Путь к конфигу.
            key:       Имя параметра.
            new_value: Новое значение.
            section:   Секция (для INI).
            confirmed: Подтверждение (без него — только возвращает команду).

        Returns:
            ConfigEditResult.
        """
        path = os.path.expanduser(path)

        # Проверки безопасности
        if _is_blacklisted(path):
            return ConfigEditResult(
                path=path,
                message=f"❌ Файл в чёрном списке: {path}",
            )

        if not _is_path_allowed(path, ALLOWED_WRITE_DIRS):
            return ConfigEditResult(
                path=path,
                message=f"❌ Запись запрещена в: {path}",
            )

        # Проверить diff
        diff = self.suggest_change(path, key, new_value, section)
        if not diff.safe:
            return ConfigEditResult(
                path=path,
                message=f"❌ Небезопасное изменение: {diff.reason}",
            )

        if not diff.unified_diff:
            return ConfigEditResult(
                success=True,
                path=path,
                message="Значение уже установлено (изменений нет)",
            )

        if not confirmed:
            needs = _needs_sudo(path)
            return ConfigEditResult(
                path=path,
                needs_sudo=needs,
                message=f"Требуется подтверждение. Diff:\n{diff.unified_diff}",
                command=self._build_write_command(path, key, new_value, section),
            )

        # Backup
        backup_result = self.backup(path)
        if not backup_result:
            return ConfigEditResult(
                path=path,
                message="❌ Не удалось создать резервную копию",
            )

        # Генерировать новое содержимое
        parsed = self._cache.get(path)
        if not parsed:
            return ConfigEditResult(
                path=path,
                message="❌ Конфиг не в кэше",
            )

        new_content = self._generate_new_content(
            parsed.raw_content, parsed.format, key, new_value, section,
        )

        # Записать
        needs = _needs_sudo(path)
        if needs:
            # Запись через sudo
            cmd = self._build_write_command(path, key, new_value, section)
            return ConfigEditResult(
                path=path,
                backup_path=backup_result,
                needs_sudo=True,
                command=cmd,
                message=f"Нужен sudo. Команда:\n{cmd}",
            )
        else:
            # Прямая запись (пользовательские конфиги) — атомарная
            try:
                tmp_path = path + ".lina-tmp"
                Path(tmp_path).write_text(new_content)
                os.replace(tmp_path, path)
                # Инвалидировать кэш
                self._cache.pop(path, None)
                return ConfigEditResult(
                    success=True,
                    path=path,
                    backup_path=backup_result,
                    message=f"✅ Изменено: {key} = {new_value}",
                )
            except Exception as e:
                logger.error("Ошибка записи %s: %s", path, e, exc_info=True)
                return ConfigEditResult(
                    path=path,
                    backup_path=backup_result,
                    message="❌ Ошибка записи конфигурации.",
                )

    # ═══════════════════════════════════════════════════════
    #  Backup / Restore
    # ═══════════════════════════════════════════════════════

    def backup(self, path: str) -> str:
        """Создать backup файла.

        Args:
            path: Путь к файлу.

        Returns:
            Путь к backup-файлу или "" при ошибке.
        """
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            return ""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = Path(path).name
        backup_path = str(self._backup_dir / f"{name}.{timestamp}{BACKUP_SUFFIX}")

        try:
            # Пробуем прямое копирование
            shutil.copy2(path, backup_path)
            logger.info("Backup: %s → %s", path, backup_path)
            return backup_path
        except PermissionError:
            # Через sudo
            try:
                result = subprocess.run(
                    ["sudo", "-n", "cp", "-p", path, backup_path],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    logger.info("Backup (sudo): %s → %s", path, backup_path)
                    return backup_path
            except Exception:
                pass
        except Exception as e:
            logger.error("Backup failed: %s — %s", path, e)

        return ""

    def restore(self, path: str, backup_path: str = "") -> ConfigEditResult:
        """Восстановить файл из backup.

        Args:
            path:        Оригинальный путь.
            backup_path: Путь к backup (если пуст — последний).

        Returns:
            ConfigEditResult.
        """
        path = os.path.expanduser(path)

        if not backup_path:
            backup_path = self._find_latest_backup(path)

        if not backup_path or not os.path.isfile(backup_path):
            return ConfigEditResult(
                path=path,
                message="❌ Backup не найден",
            )

        needs = _needs_sudo(path)
        if needs:
            cmd = f"sudo cp -p {shlex.quote(backup_path)} {shlex.quote(path)}"
            return ConfigEditResult(
                path=path,
                backup_path=backup_path,
                needs_sudo=True,
                command=cmd,
                message=f"Нужен sudo для восстановления.\nКоманда: {cmd}",
            )
        else:
            try:
                shutil.copy2(backup_path, path)
                self._cache.pop(path, None)
                return ConfigEditResult(
                    success=True,
                    path=path,
                    backup_path=backup_path,
                    message=f"✅ Восстановлено из: {backup_path}",
                )
            except Exception as e:
                logger.error("Ошибка восстановления %s: %s", path, e, exc_info=True)
                return ConfigEditResult(
                    path=path,
                    message="❌ Ошибка восстановления конфигурации.",
                )

    def list_backups(self, path: str = "") -> List[Dict[str, str]]:
        """Список существующих backup-файлов.

        Args:
            path: Фильтр по имени оригинала (опционально).

        Returns:
            Список {name, path, timestamp, size}.
        """
        backups = []
        name_filter = Path(path).name if path else ""

        for f in sorted(self._backup_dir.iterdir()):
            if not f.name.endswith(BACKUP_SUFFIX):
                continue
            if name_filter and not f.name.startswith(name_filter):
                continue

            try:
                stat = f.stat()
                backups.append({
                    "name": f.name,
                    "path": str(f),
                    "timestamp": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "size": str(stat.st_size),
                })
            except OSError:
                continue

        return backups

    # ═══════════════════════════════════════════════════════
    #  Вспомогательные для LLM-ответов
    # ═══════════════════════════════════════════════════════

    def describe_config(self, path: str) -> str:
        """Текстовое описание конфига для LLM.

        Args:
            path: Путь к конфигу.

        Returns:
            Человекочитаемое описание.
        """
        parsed = self.read_config(path)
        if parsed.error:
            return f"Ошибка чтения {path}: {parsed.error}"

        lines = [f"📄 Конфигурация: {path}"]
        lines.append(f"   Формат: {parsed.format.value}")
        lines.append(f"   Параметров: {len(parsed.values)}")

        if parsed.sections:
            lines.append(f"   Секции: {', '.join(parsed.sections[:10])}")

        if parsed.values:
            lines.append("")
            lines.append("   Ключевые параметры:")
            for v in parsed.values[:20]:
                prefix = f"[{v.section}] " if v.section else ""
                val_preview = v.value[:60] + "…" if len(v.value) > 60 else v.value
                lines.append(f"     {prefix}{v.key} = {val_preview}")

            if len(parsed.values) > 20:
                lines.append(f"     ... ещё {len(parsed.values) - 20} параметров")

        return "\n".join(lines)

    def format_diff_for_user(self, diff: ConfigDiff) -> str:
        """Форматировать diff для вывода пользователю.

        Args:
            diff: Результат suggest_change().

        Returns:
            Текст для отображения.
        """
        if not diff.safe:
            return f"⚠️ Изменение заблокировано: {diff.reason}"

        lines = [f"📝 Изменение в {diff.path}:"]
        if diff.section:
            lines.append(f"   Секция: [{diff.section}]")
        lines.append(f"   Параметр: {diff.key}")
        lines.append(f"   Было: {diff.old_value or '(не задано)'}")
        lines.append(f"   Будет: {diff.new_value}")

        if diff.unified_diff:
            lines.append("")
            lines.append("```diff")
            lines.append(diff.unified_diff.rstrip())
            lines.append("```")

        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════
    #  Private helpers
    # ═══════════════════════════════════════════════════════

    def _generate_new_content(self, old_content: str, fmt: ConfigFormat,
                              key: str, new_value: str,
                              section: str = "") -> str:
        """Сгенерировать новое содержимое файла с изменённым параметром."""
        if fmt == ConfigFormat.INI:
            return self._modify_ini(old_content, key, new_value, section)
        elif fmt == ConfigFormat.KEYVALUE:
            return self._modify_keyvalue(old_content, key, new_value)
        elif fmt == ConfigFormat.JSON:
            return self._modify_json(old_content, key, new_value)
        elif fmt == ConfigFormat.YAML:
            return self._modify_yaml(old_content, key, new_value)
        else:
            return old_content  # Plain text — не изменяем автоматически

    def _modify_ini(self, content: str, key: str, value: str,
                    section: str = "") -> str:
        """Изменить параметр в INI формате."""
        lines = content.splitlines(keepends=True)
        current_section = ""
        found = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            # Определение секции
            m = re.match(r"^\[(.+)\]", stripped)
            if m:
                current_section = m.group(1)
                continue

            # Пропуск комментариев
            if stripped.startswith("#") or stripped.startswith(";"):
                continue

            # Ищем key
            if section and current_section != section:
                continue

            m = re.match(r"^(\s*)(" + re.escape(key) + r")\s*=\s*(.*)", line)
            if m:
                indent = m.group(1)
                lines[i] = f"{indent}{key} = {value}\n"
                found = True
                break

        if not found and section:
            # Добавить в конец секции
            section_end = -1
            in_section = False
            for i, line in enumerate(lines):
                if re.match(r"^\[" + re.escape(section) + r"\]", line.strip()):
                    in_section = True
                    continue
                if in_section:
                    if re.match(r"^\[.+\]", line.strip()):
                        section_end = i
                        break
                    section_end = i + 1

            if section_end > 0:
                lines.insert(section_end, f"{key} = {value}\n")
            else:
                lines.append(f"\n[{section}]\n{key} = {value}\n")

        return "".join(lines)

    def _modify_keyvalue(self, content: str, key: str, value: str) -> str:
        """Изменить key=value строку."""
        lines = content.splitlines(keepends=True)
        found = False

        for i, line in enumerate(lines):
            m = re.match(r"^(\s*)" + re.escape(key) + r"\s*=\s*", line)
            if m:
                indent = m.group(1)
                lines[i] = f"{indent}{key}={value}\n"
                found = True
                break

        if not found:
            lines.append(f"{key}={value}\n")

        return "".join(lines)

    def _modify_json(self, content: str, key: str, value: str) -> str:
        """Изменить значение в JSON."""
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return content

        # Поддержка вложенных ключей через точку
        keys = key.split(".")
        current = data
        for k in keys[:-1]:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return content

        # Попытка типизировать значение
        final_key = keys[-1]
        typed_value = self._type_value(value)
        if isinstance(current, dict):
            current[final_key] = typed_value

        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    def _modify_yaml(self, content: str, key: str, value: str) -> str:
        """Изменить значение в YAML (простой — одноуровневый)."""
        lines = content.splitlines(keepends=True)
        # Для вложенных ключей берём последнюю часть
        leaf_key = key.split(".")[-1] if "." in key else key

        for i, line in enumerate(lines):
            m = re.match(
                r"^(\s*)" + re.escape(leaf_key) + r"\s*:\s*(.*)", line,
            )
            if m:
                indent = m.group(1)
                lines[i] = f"{indent}{leaf_key}: {value}\n"
                return "".join(lines)

        # Не найден — добавить
        lines.append(f"{leaf_key}: {value}\n")
        return "".join(lines)

    def _build_write_command(self, path: str, key: str, value: str,
                             section: str = "") -> str:
        """Сгенерировать безопасную sed/shell команду для записи."""
        escaped_value = value.replace("'", "'\\''") 
        escaped_key = re.escape(key)
        safe_path = shlex.quote(path)

        if section:
            return (
                f"sudo sed -i '/^\\[{section}\\]/,"
                f"/^\\[/s/^{escaped_key}\\s*=.*/{key} = {escaped_value}/' "
                f"{safe_path}"
            )
        else:
            return (
                f"sudo sed -i 's/^{escaped_key}\\s*=.*/{key}={escaped_value}/' "
                f"{safe_path}"
            )

    def _find_latest_backup(self, path: str) -> str:
        """Найти последний backup файла."""
        name = Path(path).name
        candidates = sorted(
            self._backup_dir.glob(f"{name}.*{BACKUP_SUFFIX}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return str(candidates[0]) if candidates else ""

    @staticmethod
    def _type_value(value: str) -> Any:
        """Попытка привести строку к нативному типу."""
        if value.lower() in ("true", "yes"):
            return True
        if value.lower() in ("false", "no"):
            return False
        if value.lower() in ("null", "none"):
            return None
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value
