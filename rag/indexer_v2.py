"""
Lina — Улучшенный индексатор базы знаний v2.

Расширяет базовый KnowledgeIndexer:
  - Метаданные: категория, дистрибутив, теги (автоматически из пути и контента)
  - Инкрементальная индексация: хеширование файлов, только изменённые
  - Оптимизированный chunking: Markdown-aware разбиение
  - Дедупликация чанков
"""

import hashlib
import json
import re
import time
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple

from lina.config import config, KNOWLEDGE_DIR, CHROMA_DIR, CACHE_DIR
from lina.rag.vectorstore import VectorStore, VECTOR_INDEX_FILE
from lina.rag.history import CommandHistory


# ─── Файл манифеста (хеши файлов для инкрементальной индексации) ───────────

MANIFEST_FILE = CACHE_DIR / "knowledge_manifest.json"


# ─── Маппинг категорий и тегов по директориям ──────────────────────────────

_DIR_CATEGORY_MAP: Dict[str, str] = {
    "linux_core": "linux_core",
    "desktop": "desktop",
    "package_managers": "package_manager",
    "troubleshooting": "troubleshooting",
    "software": "software",
    "security": "security",
    "distros": "distro",
    "commands": "commands",
}

_DIR_TAGS_MAP: Dict[str, List[str]] = {
    "linux_core": ["linux", "core", "system"],
    "desktop": ["desktop", "gui", "графика"],
    "package_managers": ["пакеты", "установка", "обновление"],
    "troubleshooting": ["проблемы", "решение", "диагностика"],
    "software": ["программы", "приложения"],
    "security": ["безопасность", "защита"],
    "distros": ["дистрибутив"],
    "commands": ["команды", "cli", "терминал"],
}

# Теги по имени файла
_FILE_TAGS_MAP: Dict[str, List[str]] = {
    "filesystem": ["файлы", "права", "ext4", "btrfs", "монтирование", "fhs"],
    "processes": ["процессы", "ps", "kill", "systemd", "systemctl", "cgroups"],
    "networking": ["сеть", "ip", "dns", "firewall", "wifi", "vpn"],
    "users": ["пользователи", "sudo", "группы", "pam", "useradd"],
    "pacman": ["pacman", "arch", "aur", "makepkg"],
    "apt": ["apt", "dpkg", "debian", "ubuntu", "ppa"],
    "dnf": ["dnf", "rpm", "fedora", "rhel", "copr"],
    "wifi_issues": ["wifi", "wireless", "wlan", "rfkill", "networkmanager"],
    "no_sound": ["звук", "audio", "pipewire", "pulseaudio", "alsa"],
    "gpu_drivers": ["gpu", "nvidia", "amd", "intel", "драйвер", "видеокарта"],
    "boot_failure": ["загрузка", "grub", "initramfs", "kernel", "boot"],
    "essential": ["команды", "ls", "cp", "mv", "grep", "find", "chmod"],
}

# Маппинг файлов → дистрибутивы (для фильтрации)
_FILE_DISTRO_MAP: Dict[str, List[str]] = {
    "pacman": ["arch", "cachyos", "manjaro", "endeavouros"],
    "apt": ["debian", "ubuntu", "mint", "pop_os"],
    "dnf": ["fedora", "rhel", "centos", "rocky"],
    "arch": ["arch", "cachyos", "manjaro"],
    "cachyos": ["cachyos", "arch"],
    "ubuntu": ["ubuntu", "debian"],
    "fedora": ["fedora", "rhel"],
    "debian": ["debian", "ubuntu"],
    "opensuse": ["opensuse", "suse"],
}


# ─── Markdown-aware чанкер ─────────────────────────────────────────────────

class MarkdownChunker:
    """
    Разбивает Markdown-документы на чанки с учётом структуры.

    Стратегия:
      1. Разбить по заголовкам ## и ### (секции)
      2. Если секция слишком большая — разбить по ```code blocks```
      3. Если всё ещё большая — разбить по абзацам
      4. Перекрытие: к каждому чанку добавляется заголовок родительской секции
    """

    def __init__(
        self,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        min_chunk_size: int = 50,
    ):
        if chunk_overlap >= chunk_size:
            raise ValueError(
                f"chunk_overlap ({chunk_overlap}) must be < chunk_size ({chunk_size})"
            )
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size

    def split(self, text: str, doc_title: str = "") -> List[Dict[str, str]]:
        """
        Разбивает markdown на чанки с метаданными.

        Returns:
            [{"text": ..., "section": ..., "has_code": bool}, ...]
        """
        if not text or not text.strip():
            return []

        sections = self._split_by_headers(text)
        chunks = []

        for section in sections:
            section_title = section.get("title", doc_title)
            section_text = section["text"]
            has_code = "```" in section_text

            if len(section_text) <= self.chunk_size:
                if len(section_text.strip()) >= self.min_chunk_size:
                    chunks.append({
                        "text": self._prepend_context(section_text, section_title, doc_title),
                        "section": section_title,
                        "has_code": has_code,
                    })
            else:
                # Разбиваем большую секцию на подчанки
                sub_chunks = self._split_large_section(section_text)
                for sc in sub_chunks:
                    if len(sc.strip()) >= self.min_chunk_size:
                        chunks.append({
                            "text": self._prepend_context(sc, section_title, doc_title),
                            "section": section_title,
                            "has_code": "```" in sc,
                        })

        return chunks

    def _split_by_headers(self, text: str) -> List[dict]:
        """Разбивает текст по заголовкам ## и ###."""
        lines = text.split("\n")
        sections = []
        current_title = ""
        current_lines: List[str] = []

        for line in lines:
            if re.match(r'^#{1,3}\s+', line):
                # Сохраняем предыдущую секцию
                if current_lines:
                    sections.append({
                        "title": current_title,
                        "text": "\n".join(current_lines).strip(),
                    })
                current_title = line.lstrip("#").strip()
                current_lines = [line]
            else:
                current_lines.append(line)

        # Последняя секция
        if current_lines:
            sections.append({
                "title": current_title,
                "text": "\n".join(current_lines).strip(),
            })

        # Если нет заголовков — весь текст как одна секция
        if not sections:
            sections = [{"title": "", "text": text.strip()}]

        return sections

    def _split_large_section(self, text: str) -> List[str]:
        """Разбивает большую секцию на подчанки."""
        # Сначала пробуем по блокам кода
        parts = re.split(r'(```[\s\S]*?```)', text)
        if len(parts) > 1:
            chunks = []
            current = ""
            for part in parts:
                if len(current) + len(part) > self.chunk_size and current.strip():
                    chunks.append(current.strip())
                    # Добавляем перекрытие
                    overlap_start = max(0, len(current) - self.chunk_overlap)
                    current = current[overlap_start:] + part
                else:
                    current += part
            if current.strip():
                chunks.append(current.strip())
            if chunks:
                return chunks

        # Разбиваем по абзацам
        paragraphs = text.split("\n\n")
        chunks = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 2 > self.chunk_size and current.strip():
                chunks.append(current.strip())
                overlap_start = max(0, len(current) - self.chunk_overlap)
                current = current[overlap_start:] + "\n\n" + para
            else:
                current = current + "\n\n" + para if current else para
        if current.strip():
            chunks.append(current.strip())

        # Финальная проверка: если один чанк слишком большой — дробим по позиции
        final = []
        safe_overlap = min(self.chunk_overlap, self.chunk_size // 3)
        for chunk in chunks:
            if len(chunk) <= self.chunk_size * 2:
                final.append(chunk)
            else:
                # Принудительное разбиение по позиции
                pos = 0
                while pos < len(chunk):
                    end = min(pos + self.chunk_size, len(chunk))
                    # Ищем границу слова
                    if end < len(chunk):
                        search_start = pos + self.chunk_size // 2
                        space = chunk.rfind(" ", search_start, end + 20)
                        if space > pos:
                            end = space + 1
                    piece = chunk[pos:end].strip()
                    if piece:
                        final.append(piece)
                    # Гарантируем продвижение вперёд
                    next_pos = end - safe_overlap
                    if next_pos <= pos:
                        next_pos = pos + max(self.chunk_size // 2, 1)
                    pos = next_pos
                    if end >= len(chunk):
                        break

        return final if final else [text]

    def _prepend_context(self, text: str, section: str, doc_title: str) -> str:
        """Добавляет контекст заголовка к чанку (если его нет)."""
        # Если чанк уже начинается с заголовка — не дублируем
        if text.lstrip().startswith("#"):
            return text
        prefix = ""
        if doc_title and doc_title not in text[:100]:
            prefix = f"# {doc_title}\n"
        if section and section not in text[:100]:
            prefix += f"## {section}\n"
        if prefix:
            return prefix + text
        return text


# ─── Извлечение метаданных ─────────────────────────────────────────────────

def extract_metadata(file_path: Path, content: str) -> dict:
    """
    Извлекает метаданные из пути файла и содержимого.

    Returns:
        {category, tags, distros, title, has_code, word_count}
    """
    parts = file_path.relative_to(KNOWLEDGE_DIR).parts if _is_under(file_path, KNOWLEDGE_DIR) else file_path.parts

    # Категория из директории
    category = "general"
    dir_tags: List[str] = []
    if len(parts) >= 2:
        parent_dir = parts[-2] if len(parts) >= 2 else ""
        category = _DIR_CATEGORY_MAP.get(parent_dir, "general")
        dir_tags = _DIR_TAGS_MAP.get(parent_dir, [])

    # Теги из имени файла
    stem = file_path.stem.lower()
    file_tags = _FILE_TAGS_MAP.get(stem, [])

    # Дистрибутивы
    distros = _FILE_DISTRO_MAP.get(stem, ["all"])

    # Заголовок из первой строки контента
    title = ""
    first_line = content.strip().split("\n")[0] if content.strip() else ""
    if first_line.startswith("#"):
        title = first_line.lstrip("#").strip()

    # Извлечение дополнительных тегов из контента
    content_tags = _extract_content_tags(content)

    # Объединение тегов (без дубликатов)
    all_tags = list(dict.fromkeys(dir_tags + file_tags + content_tags))

    return {
        "category": category,
        "tags": all_tags,
        "distros": distros,
        "title": title or stem,
        "has_code": "```" in content,
        "word_count": len(content.split()),
    }


def _extract_content_tags(content: str) -> List[str]:
    """Извлекает теги из контента (ключевые термины)."""
    tags = []
    content_lower = content.lower()

    # Проверяем наличие ключевых терминов
    term_map = {
        "systemd": "systemd", "systemctl": "systemd",
        "journalctl": "journalctl", "grub": "grub",
        "btrfs": "btrfs", "ext4": "ext4", "xfs": "xfs",
        "lvm": "lvm", "luks": "luks",
        "pipewire": "pipewire", "pulseaudio": "pulseaudio",
        "wayland": "wayland", "x11": "x11", "xorg": "x11",
        "docker": "docker", "flatpak": "flatpak", "snap": "snap",
        "networkmanager": "networkmanager", "nmcli": "networkmanager",
        "iptables": "iptables", "firewalld": "firewalld", "ufw": "ufw",
        "nvidia": "nvidia", "amdgpu": "amd", "intel": "intel",
    }

    seen: Set[str] = set()
    for trigger, tag in term_map.items():
        if trigger in content_lower and tag not in seen:
            tags.append(tag)
            seen.add(tag)

    return tags


def _is_under(path: Path, parent: Path) -> bool:
    """Проверяет, находится ли path внутри parent."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


# ─── Манифест (хеши файлов) ───────────────────────────────────────────────

class FileManifest:
    """
    Хранит хеши файлов для инкрементальной индексации.

    Позволяет определить, какие файлы изменились с последней индексации.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = path or MANIFEST_FILE
        self._hashes: Dict[str, str] = {}  # filepath → md5 hash
        self._timestamps: Dict[str, float] = {}  # filepath → mod time
        self._loaded = False

    def load(self) -> None:
        """Загружает манифест с диска."""
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._hashes = data.get("hashes", {})
                self._timestamps = data.get("timestamps", {})
        except (json.JSONDecodeError, KeyError):
            self._hashes = {}
            self._timestamps = {}
        self._loaded = True

    def save(self) -> None:
        """Сохраняет манифест на диск."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "hashes": self._hashes,
            "timestamps": self._timestamps,
            "saved_at": time.time(),
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def is_changed(self, file_path: str, content_hash: str) -> bool:
        """Проверяет, изменился ли файл."""
        if not self._loaded:
            self.load()
        return self._hashes.get(file_path) != content_hash

    def update(self, file_path: str, content_hash: str) -> None:
        """Обновляет хеш файла."""
        self._hashes[file_path] = content_hash
        self._timestamps[file_path] = time.time()

    def remove(self, file_path: str) -> None:
        """Удаляет файл из манифеста."""
        self._hashes.pop(file_path, None)
        self._timestamps.pop(file_path, None)

    def get_all_paths(self) -> Set[str]:
        """Возвращает все известные пути."""
        if not self._loaded:
            self.load()
        return set(self._hashes.keys())

    def clear(self) -> None:
        """Очищает манифест."""
        self._hashes.clear()
        self._timestamps.clear()
        if self.path.exists():
            self.path.unlink()


# ─── KnowledgeIndexerV2 ───────────────────────────────────────────────────

class KnowledgeIndexerV2:
    """
    Улучшенный индексатор базы знаний.

    Улучшения над KnowledgeIndexer (v1):
      - Markdown-aware chunking (секции по заголовкам)
      - Автоматические метаданные: категория, теги, дистрибутив
      - Инкрементальная индексация (хеширование, только изменённые)
      - Дедупликация чанков
      - Статистика по категориям
    """

    SUPPORTED_EXTENSIONS = {
        ".txt", ".md", ".py", ".sh", ".json", ".yaml",
        ".yml", ".toml", ".cfg", ".conf", ".ini",
        ".rst",
    }

    def __init__(self):
        self.rag_config = config.rag
        self.chunker = MarkdownChunker(
            chunk_size=self.rag_config.chunk_size,
            chunk_overlap=self.rag_config.chunk_overlap,
        )
        self.manifest = FileManifest()
        self._store = VectorStore()
        self._store_loaded = False
        self._history = CommandHistory()

    def _ensure_store(self) -> VectorStore:
        """Загружает индекс с диска если ещё не загружен."""
        if not self._store_loaded:
            self._store.load()
            self._store_loaded = True
        return self._store

    def index_all(
        self,
        directory: Optional[str] = None,
        include_history: bool = True,
        force: bool = False,
    ) -> dict:
        """
        Полная индексация всех документов.

        Args:
            directory: Путь к директории (по умолчанию KNOWLEDGE_DIR).
            include_history: Включать историю команд.
            force: Принудительная переиндексация (игнорировать манифест).

        Returns:
            Статистика: {status, indexed, chunks, skipped, categories, ...}
        """
        dir_path = Path(directory) if directory else KNOWLEDGE_DIR

        if not dir_path.exists():
            return {
                "status": "no_directory",
                "message": f"Директория {dir_path} не существует.",
                "indexed": 0,
            }

        self.manifest.load()

        all_chunks: List[str] = []
        all_metadata: List[dict] = []
        indexed_files = 0
        skipped_files = 0
        category_stats: Dict[str, int] = {}
        seen_hashes: Set[str] = set()  # для дедупликации чанков

        # Собираем все файлы
        for file_path in sorted(dir_path.rglob("*")):
            if not file_path.is_file():
                continue
            # Skip symlinks to prevent path traversal
            if file_path.is_symlink():
                continue
            if file_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                continue
            # Пропускаем скрытые и __pycache__
            if any(part.startswith(".") or part == "__pycache__" for part in file_path.parts):
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            if not content.strip():
                continue

            # Хеш содержимого
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            file_key = str(file_path)

            # Инкрементальная проверка
            if not force and not self.manifest.is_changed(file_key, content_hash):
                skipped_files += 1
                continue

            # Извлечение метаданных
            doc_meta = extract_metadata(file_path, content)

            # Разбиение на чанки (Markdown-aware)
            if file_path.suffix.lower() == ".md":
                chunk_dicts = self.chunker.split(content, doc_meta["title"])
            else:
                # Для не-Markdown файлов — простое разбиение
                chunk_dicts = self._split_plain(content, doc_meta["title"])

            for i, cd in enumerate(chunk_dicts):
                chunk_text = cd["text"]

                # Дедупликация
                chunk_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                if chunk_hash in seen_hashes:
                    continue
                seen_hashes.add(chunk_hash)

                all_chunks.append(chunk_text)
                all_metadata.append({
                    "source": str(file_path),
                    "filename": file_path.name,
                    "chunk_index": i,
                    "doc_hash": content_hash,
                    "type": "document",
                    "category": doc_meta["category"],
                    "tags": doc_meta["tags"],
                    "distros": doc_meta["distros"],
                    "title": doc_meta["title"],
                    "section": cd.get("section", ""),
                    "has_code": cd.get("has_code", False),
                })

            # Обновляем манифест
            self.manifest.update(file_key, content_hash)
            indexed_files += 1

            # Статистика по категориям
            cat = doc_meta["category"]
            category_stats[cat] = category_stats.get(cat, 0) + 1

        # Добавляем историю команд
        history_count = 0
        if include_history:
            h_chunks, h_metadata = self._history.get_chunks_for_indexing()
            all_chunks.extend(h_chunks)
            all_metadata.extend(h_metadata)
            history_count = len(h_chunks)

        if not all_chunks:
            return {
                "status": "no_documents",
                "message": "Документов для индексации не найдено.",
                "indexed": 0,
                "skipped": skipped_files,
            }

        # Строим индекс
        self._store.build(all_chunks, all_metadata)
        self._store.save()
        self._store_loaded = True

        # Сохраняем манифест
        self.manifest.save()

        chunk_count = len(all_chunks) - history_count
        msg = (
            f"Проиндексировано {indexed_files} файлов, {chunk_count} чанков."
        )
        if skipped_files:
            msg += f" Пропущено {skipped_files} (без изменений)."
        if history_count:
            msg += f" + {history_count} записей истории."

        return {
            "status": "success",
            "message": msg,
            "indexed": indexed_files,
            "skipped": skipped_files,
            "chunks": chunk_count,
            "history_chunks": history_count,
            "categories": category_stats,
            "dedup_removed": len(seen_hashes) - chunk_count - history_count
            if len(seen_hashes) > chunk_count + history_count else 0,
        }

    def index_incremental(
        self,
        directory: Optional[str] = None,
        include_history: bool = True,
    ) -> dict:
        """
        Инкрементальная индексация — только изменённые файлы.

        Использует манифест хешей для определения изменений.
        Если манифест пуст — делает полную индексацию.
        """
        return self.index_all(
            directory=directory,
            include_history=include_history,
            force=False,
        )

    def _split_plain(self, content: str, title: str = "") -> List[Dict[str, str]]:
        """Простое разбиение для не-Markdown файлов."""
        chunks = []
        start = 0
        text_len = len(content)

        while start < text_len:
            end = start + self.chunker.chunk_size

            if end < text_len:
                # Ищем границу абзаца/предложения
                for sep in ["\n\n", "\n", ". ", "! ", "? "]:
                    pos = content.rfind(sep, start + self.chunker.chunk_size // 2, end + 50)
                    if pos != -1:
                        end = pos + len(sep)
                        break

            chunk = content[start:end].strip()
            if chunk and len(chunk) >= self.chunker.min_chunk_size:
                chunks.append({
                    "text": chunk,
                    "section": title,
                    "has_code": False,
                })

            start = end - self.chunker.chunk_overlap
            if start <= 0 and end >= text_len:
                break
            if start >= text_len:
                break

        return chunks

    def get_store(self) -> VectorStore:
        """Возвращает загруженный VectorStore."""
        return self._ensure_store()

    def get_stats(self) -> dict:
        """Расширенная статистика базы знаний."""
        store = self._ensure_store()
        history_stats = self._history.get_stats()

        # Статистика по категориям из метаданных
        category_stats: Dict[str, int] = {}
        tag_stats: Dict[str, int] = {}
        for meta in store.metadata:
            cat = meta.get("category", "unknown")
            category_stats[cat] = category_stats.get(cat, 0) + 1
            for tag in meta.get("tags", []):
                tag_stats[tag] = tag_stats.get(tag, 0) + 1

        return {
            "collection": "vector_index_v2 (BM25 + n-gram)",
            "total_chunks": store.total_chunks,
            "vocabulary_size": store.vocab_size,
            "persist_dir": str(CHROMA_DIR),
            "index_file": str(VECTOR_INDEX_FILE),
            "history_entries": history_stats["total"],
            "categories": category_stats,
            "top_tags": dict(sorted(tag_stats.items(), key=lambda x: x[1], reverse=True)[:20]),
        }

    def clear(self) -> dict:
        """Очищает всю базу знаний и манифест."""
        self._store = VectorStore()
        self._store_loaded = True
        self.manifest.clear()
        try:
            if VECTOR_INDEX_FILE.exists():
                VECTOR_INDEX_FILE.unlink()
        except Exception:
            pass
        return {"status": "success", "message": "База знаний v2 очищена."}
