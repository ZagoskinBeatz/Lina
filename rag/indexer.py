"""
Lina — Модуль индексации документов для RAG.

Загружает текстовые документы, разбивает на чанки,
строит BM25 + n-gram индекс через VectorStore.

Поддерживает:
  - Текстовые файлы, Markdown, Python, конфиги
  - Автоматическую индексацию истории команд
  - Инкрементальное обновление индекса
"""

import hashlib
import logging
import re
from pathlib import Path
from typing import List, Optional

from lina.config import config, KNOWLEDGE_DIR, CHROMA_DIR
from lina.rag.vectorstore import VectorStore, VECTOR_INDEX_FILE
from lina.rag.history import CommandHistory

logger = logging.getLogger(__name__)


# Обратная совместимость — старый TF-IDF индекс
INDEX_FILE = CHROMA_DIR / "tfidf_index.json"


class TextChunker:
    """Разбивает текст на чанки с перекрытием."""

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ):
        if chunk_overlap >= chunk_size:
            raise ValueError(
                f"chunk_overlap ({chunk_overlap}) must be < chunk_size ({chunk_size})"
            )
        self.chunk_size = max(1, chunk_size)
        self.chunk_overlap = max(0, chunk_overlap)

    def split(self, text: str) -> List[str]:
        """Разбивает текст на чанки."""
        if not text or not text.strip():
            return []

        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = start + self.chunk_size

            # Ищем конец предложения/абзаца
            if end < text_len:
                for sep in ["\n\n", "\n", ". ", "! ", "? "]:
                    pos = text.rfind(sep, start + self.chunk_size // 2, end + 50)
                    if pos != -1:
                        end = pos + len(sep)
                        break

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            prev_start = start
            start = end - self.chunk_overlap
            # Guard: ensure forward progress to prevent infinite loop
            if start <= prev_start:
                start = prev_start + max(1, self.chunk_size // 2)
            if start <= 0 and end >= text_len:
                break
            if start >= text_len:
                break

        return chunks


class DocumentLoader:
    """Загружает документы из директории."""

    SUPPORTED_EXTENSIONS = {
        ".txt", ".md", ".py", ".sh", ".json", ".yaml",
        ".yml", ".toml", ".cfg", ".conf", ".ini", ".log",
        ".csv", ".rst", ".html", ".js", ".ts", ".css",
        ".sql", ".xml", ".dockerfile", ".env",
    }

    def load_directory(
        self, directory: Optional[str] = None, recursive: bool = True
    ) -> List[dict]:
        """Загружает все поддерживаемые документы из директории."""
        dir_path = Path(directory) if directory else KNOWLEDGE_DIR
        documents = []

        if not dir_path.exists():
            return documents

        # Resolve base directory for path traversal check
        base_resolved = dir_path.resolve()

        pattern_fn = dir_path.rglob if recursive else dir_path.glob

        for file_path in pattern_fn("*"):
            if not file_path.is_file():
                continue

            # Path traversal prevention: ensure file is inside base directory
            file_resolved = file_path.resolve()
            if file_resolved != base_resolved and not str(file_resolved).startswith(
                str(base_resolved) + "/"
            ):
                logger.warning("Path traversal blocked: %s", file_path.name)
                continue

            if file_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                # Файлы без расширения, но с текстовым содержимым
                if file_path.suffix:
                    continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                if content.strip():
                    documents.append({
                        "path": str(file_path),
                        "name": file_path.name,
                        "content": content,
                        "hash": hashlib.sha256(content.encode()).hexdigest(),
                    })
            except Exception:
                logger.warning("Не удалось прочитать файл: %s", file_path.name)

        return documents


# ── Обратная совместимость: TFIDFIndex (обёртка над VectorStore) ──

class TFIDFIndex:
    """
    Обратная совместимость — обёртка над VectorStore.
    Searcher импортирует TFIDFIndex, INDEX_FILE — предоставляем эти имена.
    """

    def __init__(self):
        self._store = VectorStore()

    @property
    def chunks(self) -> List[str]:
        return self._store.chunks

    @property
    def vocabulary(self) -> dict:
        return self._store.vocabulary

    def build(self, chunks: List[str], metadata: List[dict]) -> None:
        self._store.build(chunks, metadata)

    def search(self, query: str, top_k: int = 3) -> List[dict]:
        return self._store.search(query, top_k)

    def save(self, path: str) -> None:
        self._store.save(path)

    def load(self, path: str) -> bool:
        # Пробуем новый формат, потом старый
        if self._store.load(str(VECTOR_INDEX_FILE)):
            return True
        # Пробуем загрузить старый TF-IDF
        return self._load_legacy(path)

    def _load_legacy(self, path: str) -> bool:
        """Загрузка старого TF-IDF индекса — для миграции."""
        import json
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            chunks = data.get("chunks", [])
            metadata = data.get("metadata", [])
            if chunks:
                self._store.build(chunks, metadata)
                # Сохраняем в новом формате
                self._store.save()
                return True
            return False
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return False


class KnowledgeIndexer:
    """
    Индексатор базы знаний.

    Загружает документы, разбивает на чанки, строит BM25 + n-gram индекс.
    Поддерживает индексацию истории команд.
    """

    def __init__(self):
        self.rag_config = config.rag
        self.chunker = TextChunker(
            chunk_size=self.rag_config.chunk_size,
            chunk_overlap=self.rag_config.chunk_overlap,
        )
        self.loader = DocumentLoader()
        self._store = VectorStore()
        self._store_loaded = False
        self._history = CommandHistory()

    def _ensure_store(self) -> VectorStore:
        """Загружает индекс с диска если ещё не загружен."""
        if not self._store_loaded:
            self._store.load()
            self._store_loaded = True
        return self._store

    def index_documents(
        self,
        directory: Optional[str] = None,
        include_history: bool = True,
    ) -> dict:
        """
        Индексирует все документы из директории.

        Args:
            directory: Путь к директории с документами.
            include_history: Включать историю команд в индекс.

        Returns:
            Статистика индексации.
        """
        documents = self.loader.load_directory(directory)

        all_chunks: List[str] = []
        all_metadata: List[dict] = []

        for doc in documents:
            chunks = self.chunker.split(doc["content"])
            for i, chunk in enumerate(chunks):
                all_chunks.append(chunk)
                all_metadata.append({
                    "source": doc["path"],
                    "filename": doc["name"],
                    "chunk_index": i,
                    "doc_hash": doc["hash"],
                    "type": "document",
                })

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
            }

        # Строим индекс
        self._store.build(all_chunks, all_metadata)
        self._store.save()
        self._store_loaded = True

        doc_count = len(documents)
        chunk_count = len(all_chunks) - history_count

        msg = f"Проиндексировано {doc_count} документов, {chunk_count} чанков."
        if history_count:
            msg += f" + {history_count} записей истории."

        return {
            "status": "success",
            "message": msg,
            "indexed": doc_count,
            "chunks": chunk_count,
            "history_chunks": history_count,
        }

    def get_store(self) -> VectorStore:
        """Возвращает загруженный VectorStore."""
        return self._ensure_store()

    def get_stats(self) -> dict:
        """Возвращает статистику базы знаний."""
        store = self._ensure_store()
        history_stats = self._history.get_stats()
        return {
            "collection": "vector_index (BM25 + n-gram)",
            "total_chunks": store.total_chunks,
            "vocabulary_size": store.vocab_size,
            "persist_dir": str(CHROMA_DIR),
            "index_file": str(VECTOR_INDEX_FILE),
            "history_entries": history_stats["total"],
        }

    def clear(self) -> dict:
        """Очищает всю базу знаний."""
        self._store = VectorStore()
        self._store_loaded = True
        try:
            if VECTOR_INDEX_FILE.exists():
                VECTOR_INDEX_FILE.unlink()
            # Удаляем и старый индекс
            if INDEX_FILE.exists():
                INDEX_FILE.unlink()
        except Exception as e:
            logger.warning("Failed to delete index files: %s", e)
        return {"status": "success", "message": "База знаний очищена."}
