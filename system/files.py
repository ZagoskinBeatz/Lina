"""
Lina — Модуль работы с файловой системой.

Безопасное чтение, поиск, просмотр файлов и директорий.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

from lina.config import config

logger = logging.getLogger(__name__)


class FileManager:
    """Менеджер файловых операций с проверкой безопасности."""

    def __init__(self):
        self.security = config.security
        self.max_size = config.security.max_file_size_mb * 1024 * 1024  # в байтах

    def _check_access(self, path: str) -> None:
        """Проверяет разрешён ли доступ к пути."""
        if not self.security.is_path_allowed(path):
            logger.warning("Access denied to path: %s", path)
            raise PermissionError("Доступ запрещён политикой безопасности.")

    def list_directory(self, path: str = ".") -> List[dict]:
        """
        Возвращает список файлов и папок в директории.

        Returns:
            Список словарей с info о каждом элементе:
            [{"name": "file.txt", "type": "file", "size": 1234}, ...]
        """
        resolved = str(Path(path).resolve())
        self._check_access(resolved)

        items = []
        try:
            for entry in sorted(os.scandir(resolved), key=lambda e: e.name):
                info = {
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "path": entry.path,
                }
                if entry.is_file():
                    try:
                        st = entry.stat()
                        info["size"] = st.st_size
                        info["size_human"] = self._human_size(st.st_size)
                    except OSError:
                        info["size"] = 0
                        info["size_human"] = "?"
                items.append(info)
        except PermissionError:
            raise PermissionError(f"Нет прав для чтения директории: {resolved}")
        except FileNotFoundError:
            raise FileNotFoundError(f"Директория не найдена: {resolved}")

        return items

    def read_file(self, path: str, max_lines: Optional[int] = None) -> str:
        """
        Читает содержимое текстового файла.

        Args:
            path: Путь к файлу.
            max_lines: Максимальное количество строк (None = все).

        Returns:
            Содержимое файла как строка.
        """
        resolved = str(Path(path).resolve())
        self._check_access(resolved)

        if not os.path.isfile(resolved):
            raise FileNotFoundError(f"Файл не найден: {resolved}")

        file_size = os.path.getsize(resolved)
        if file_size > self.max_size:
            raise ValueError(
                f"Файл слишком большой: {self._human_size(file_size)} "
                f"(лимит: {config.security.max_file_size_mb} MB)"
            )

        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                if max_lines:
                    lines = []
                    for i, line in enumerate(f):
                        if i >= max_lines:
                            break
                        lines.append(line)
                    return "".join(lines)
                return f.read()
        except UnicodeDecodeError:
            return f"[Бинарный файл, невозможно прочитать как текст: {resolved}]"

    MAX_SEARCH_RESULTS = 5000

    def search_files(
        self, directory: str = ".", pattern: str = "*", recursive: bool = True,
        max_results: int = MAX_SEARCH_RESULTS,
    ) -> List[str]:
        """
        Ищет файлы по glob-шаблону.

        Args:
            directory: Директория для поиска.
            pattern: Glob-шаблон (например, "*.py", "*.txt").
            recursive: Искать рекурсивно.
            max_results: Максимальное количество результатов.

        Returns:
            Список путей к найденным файлам.
        """
        resolved = Path(directory).resolve()
        self._check_access(str(resolved))

        results = []
        gen = resolved.rglob(pattern) if recursive else resolved.glob(pattern)
        for p in gen:
            if p.is_file():
                results.append(str(p))
                if len(results) >= max_results:
                    break
        return results

    def get_file_info(self, path: str) -> dict:
        """Возвращает информацию о файле."""
        resolved = Path(path).resolve()
        self._check_access(str(resolved))

        if not resolved.exists():
            raise FileNotFoundError(f"Путь не найден: {resolved}")

        stat = resolved.stat()
        return {
            "name": resolved.name,
            "path": str(resolved),
            "type": "dir" if resolved.is_dir() else "file",
            "size": stat.st_size,
            "size_human": self._human_size(stat.st_size),
            "extension": resolved.suffix,
            "modified": stat.st_mtime,
        }

    def get_directory_tree(self, path: str = ".", max_depth: int = 3) -> str:
        """
        Возвращает дерево директорий в текстовом формате.

        Args:
            path: Корневая директория.
            max_depth: Максимальная глубина.

        Returns:
            Текстовое дерево.
        """
        resolved = Path(path).resolve()
        self._check_access(str(resolved))

        lines = [str(resolved.name) + "/"]
        self._build_tree(resolved, "", max_depth, 0, lines)
        return "\n".join(lines)

    MAX_TREE_ITEMS = 2000

    def _build_tree(
        self, directory: Path, prefix: str, max_depth: int, depth: int,
        lines: list
    ) -> None:
        """Рекурсивно строит дерево директорий."""
        if depth >= max_depth or len(lines) >= self.MAX_TREE_ITEMS:
            return

        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda e: (not e.is_dir(), e.name.lower())
            )
        except PermissionError:
            return

        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{prefix}{connector}{entry.name}{suffix}")

            if entry.is_dir():
                extension = "    " if is_last else "│   "
                self._build_tree(
                    entry, prefix + extension, max_depth, depth + 1, lines
                )

    @staticmethod
    def _human_size(size_bytes: int) -> str:
        """Конвертирует байты в человекочитаемый формат."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"
