"""
Lina — Продвинутая векторная база знаний (VectorStore).

Замена TF-IDF на BM25 + символьные n-граммы для семантического поиска.
Использует numpy для быстрого вычисления сходства.

Преимущества:
  - BM25: лучше TF-IDF для поиска, учитывает длину документа
  - N-граммы: находит частичные совпадения (опечатки, словоформы)
  - Синонимы: базовое расширение запроса для русского языка
  - numpy: быстрые матричные операции
"""

import json
import logging
import math
import re
from pathlib import Path
from collections import Counter
from typing import List, Dict, Optional, Tuple

import numpy as np

from lina.config import config, CHROMA_DIR

logger = logging.getLogger(__name__)

_MAX_CHUNKS = 50_000

# ─── Путь к индексу ────────────────────────────────────────────────────────────

VECTOR_INDEX_FILE = CHROMA_DIR / "vector_index.json"


# ─── Русский мини-стеммер ──────────────────────────────────────────────────────

# Окончания для простого стемминга (сортировка по длине — сначала длинные)
_RU_SUFFIXES = sorted([
    "ость", "ение", "ание", "ство", "ного", "тель", "ский", "ская",
    "ские", "ским", "ских", "ному", "ными", "ного", "ной", "ных",
    "ную", "ные", "ным", "ого", "ому", "ами", "ыми", "ать",
    "ить", "уть", "еть", "ует", "ает", "ить", "ённ", "ова",
    "ева", "ого", "ему", "его", "ой", "ём", "ую", "ий",
    "ые", "ых", "ей", "ов", "ам", "ом", "ем", "ах",
    "ая", "яя", "ие", "ия", "ую", "ой", "ый", "ая",
    "ое", "ые", "ую", "ей", "ём", "ов", "ев", "ям",
], key=len, reverse=True)


def _stem_ru(word: str) -> str:
    """Простой русский стеммер — отсекает окончания."""
    if len(word) <= 4:
        return word
    for suffix in _RU_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


# ─── Синонимы (базовый набор) ──────────────────────────────────────────────────

_SYNONYM_GROUPS = [
    {"файл", "документ", "файла", "документа"},
    {"каталог", "директория", "папка", "каталога", "директории", "папки"},
    {"удалить", "удалить", "стереть", "убрать"},
    {"создать", "сделать", "добавить"},
    {"запустить", "выполнить", "запуск", "старт", "run"},
    {"ошибка", "баг", "bug", "проблема", "error"},
    {"компиляция", "сборка", "build", "билд", "собрать"},
    {"установить", "поставить", "инсталл", "install"},
    {"обновить", "обновление", "update", "апдейт"},
    {"настроить", "конфиг", "config", "настройка", "конфигурация"},
    {"помощь", "help", "справка"},
    {"поиск", "найти", "искать", "search", "find"},
    {"система", "system", "ос", "операционная"},
    {"память", "ram", "озу", "memory"},
    {"процессор", "cpu", "проц"},
    {"сеть", "network", "интернет", "net"},
    {"код", "code", "скрипт", "программа", "script"},
    {"python", "питон", "пайтон"},
    {"linux", "линукс"},
]

_SYNONYM_MAP: Dict[str, set] = {}
for group in _SYNONYM_GROUPS:
    for word in group:
        _SYNONYM_MAP[word.lower()] = group


def _expand_synonyms(tokens: List[str]) -> List[str]:
    """Расширяет список токенов синонимами."""
    expanded = list(tokens)
    for token in tokens:
        if token in _SYNONYM_MAP:
            for syn in _SYNONYM_MAP[token]:
                if syn not in expanded:
                    expanded.append(syn)
    return expanded


# ─── Токенизация ───────────────────────────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """Токенизация: слова + нижний регистр."""
    text = text.lower()
    tokens = re.findall(r'[a-zа-яёA-ZА-ЯЁ0-9]+', text)
    return [t for t in tokens if len(t) > 1]


def _char_ngrams(text: str, n: int = 3) -> List[str]:
    """Генерирует символьные n-граммы из текста."""
    text = text.lower().strip()
    text = re.sub(r'\s+', ' ', text)
    ngrams = []
    for i in range(len(text) - n + 1):
        ng = text[i:i + n]
        if not ng.isspace():
            ngrams.append(ng)
    return ngrams


# ─── BM25 + N-gram VectorStore ─────────────────────────────────────────────────

class VectorStore:
    """
    Продвинутая векторная база знаний.

    Комбинирует:
      1. BM25 (улучшенный TF-IDF с учётом длины документа)
      2. Символьные 3-граммы (для частичного/нечёткого совпадения)
      3. Синонимы для расширения запросов
      4. Стемминг для русского языка

    Вектора хранятся как numpy-массивы для быстрого поиска.
    """

    # BM25 параметры
    K1 = 1.5    # насыщение термина (обычно 1.2-2.0)
    B = 0.75    # влияние длины документа (0 = без влияния, 1 = полное)

    def __init__(self):
        self.chunks: List[str] = []
        self.metadata: List[dict] = []
        self.vocabulary: Dict[str, int] = {}
        self.idf: np.ndarray = np.array([])
        self.bm25_matrix: Optional[np.ndarray] = None  # (n_docs, vocab_size)
        self.ngram_vocab: Dict[str, int] = {}
        self.ngram_matrix: Optional[np.ndarray] = None  # (n_docs, ngram_vocab_size)
        self._avg_doc_len: float = 0.0

    def build(self, chunks: List[str], metadata: List[dict]) -> None:
        """
        Строит индекс из чанков.

        Args:
            chunks: Тексты чанков.
            metadata: Метаданные каждого чанка.

        Raises:
            ValueError: если len(chunks) != len(metadata).
        """
        if len(chunks) != len(metadata):
            raise ValueError(
                f"chunks/metadata length mismatch: "
                f"{len(chunks)} chunks vs {len(metadata)} metadata"
            )

        if len(chunks) > _MAX_CHUNKS:
            logger.warning("Truncating chunks from %d to %d", len(chunks), _MAX_CHUNKS)
            chunks = chunks[:_MAX_CHUNKS]
            metadata = metadata[:_MAX_CHUNKS]

        self.chunks = chunks
        self.metadata = metadata

        if not chunks:
            return

        # --- BM25 ---
        tokenized = [_tokenize(chunk) for chunk in chunks]
        stemmed = [[_stem_ru(t) for t in tokens] for tokens in tokenized]

        # Словарь
        vocab_set: set = set()
        for tokens in stemmed:
            vocab_set.update(tokens)
        self.vocabulary = {w: i for i, w in enumerate(sorted(vocab_set))}
        vocab_size = len(self.vocabulary)

        n_docs = len(chunks)
        doc_lens = np.array([len(t) for t in stemmed], dtype=np.float64)
        self._avg_doc_len = float(doc_lens.mean()) if n_docs > 0 else 1.0

        # IDF (BM25 вариант)
        doc_freq = np.zeros(vocab_size, dtype=np.float64)
        for tokens in stemmed:
            seen = set()
            for t in tokens:
                if t in self.vocabulary and t not in seen:
                    doc_freq[self.vocabulary[t]] += 1
                    seen.add(t)

        # IDF = log((N - df + 0.5) / (df + 0.5) + 1)
        self.idf = np.log((n_docs - doc_freq + 0.5) / (doc_freq + 0.5) + 1.0)

        # BM25 матрица
        self.bm25_matrix = np.zeros((n_docs, vocab_size), dtype=np.float32)
        for i, tokens in enumerate(stemmed):
            tf = Counter(tokens)
            dl = doc_lens[i]
            for word, count in tf.items():
                if word in self.vocabulary:
                    j = self.vocabulary[word]
                    # BM25 score = IDF * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl/avgdl))
                    numerator = count * (self.K1 + 1)
                    denominator = count + self.K1 * (1 - self.B + self.B * dl / self._avg_doc_len)
                    self.bm25_matrix[i, j] = float(self.idf[j]) * numerator / denominator

        # --- Символьные N-граммы ---
        all_ngrams_per_doc = [_char_ngrams(chunk, 3) for chunk in chunks]
        ngram_set: set = set()
        for ngrams in all_ngrams_per_doc:
            ngram_set.update(ngrams)
        self.ngram_vocab = {ng: i for i, ng in enumerate(sorted(ngram_set))}
        ngram_size = len(self.ngram_vocab)

        if ngram_size > 0:
            self.ngram_matrix = np.zeros((n_docs, ngram_size), dtype=np.float32)
            for i, ngrams in enumerate(all_ngrams_per_doc):
                tf = Counter(ngrams)
                total = len(ngrams) if ngrams else 1
                for ng, count in tf.items():
                    if ng in self.ngram_vocab:
                        self.ngram_matrix[i, self.ngram_vocab[ng]] = count / total
        else:
            self.ngram_matrix = np.zeros((n_docs, 1), dtype=np.float32)

    def search(
        self,
        query: str,
        top_k: int = 3,
        bm25_weight: float = 0.7,
        ngram_weight: float = 0.3,
    ) -> List[dict]:
        """
        Семантический поиск по базе знаний.

        Комбинирует BM25 и n-gram скоры для лучшего качества.

        Args:
            query: Запрос.
            top_k: Количество результатов.
            bm25_weight: Вес BM25 компоненты.
            ngram_weight: Вес n-gram компоненты.

        Returns:
            Отсортированные результаты [{text, score, source, filename, metadata}]
        """
        if not self.chunks or self.bm25_matrix is None:
            return []

        # --- BM25 скор ---
        query_tokens = _tokenize(query)
        query_stems = [_stem_ru(t) for t in query_tokens]
        # Расширяем синонимами
        expanded = _expand_synonyms(query_stems)

        bm25_scores = np.zeros(len(self.chunks), dtype=np.float64)
        for word in expanded:
            if word in self.vocabulary:
                j = self.vocabulary[word]
                bm25_scores += self.bm25_matrix[:, j]

        # Нормализуем BM25
        bm25_max = bm25_scores.max()
        if bm25_max > 0:
            bm25_scores /= bm25_max

        # --- N-gram скор ---
        query_ngrams = _char_ngrams(query, 3)
        ngram_scores = np.zeros(len(self.chunks), dtype=np.float64)

        if query_ngrams and self.ngram_matrix is not None and len(self.ngram_vocab) > 0:
            query_vec = np.zeros(len(self.ngram_vocab), dtype=np.float32)
            tf = Counter(query_ngrams)
            total = len(query_ngrams)
            for ng, count in tf.items():
                if ng in self.ngram_vocab:
                    query_vec[self.ngram_vocab[ng]] = count / total

            # Cosine similarity
            query_norm = np.linalg.norm(query_vec)
            if query_norm > 0:
                doc_norms = np.linalg.norm(self.ngram_matrix, axis=1)
                doc_norms[doc_norms == 0] = 1.0
                ngram_scores = self.ngram_matrix @ query_vec / (doc_norms * query_norm)

        # --- Комбинированный скор ---
        combined = bm25_weight * bm25_scores + ngram_weight * ngram_scores

        # Топ-K
        if len(combined) <= top_k:
            top_indices = np.argsort(-combined)
        else:
            top_indices = np.argpartition(-combined, top_k)[:top_k]
            top_indices = top_indices[np.argsort(-combined[top_indices])]

        results = []
        for idx in top_indices:
            score = float(combined[idx])
            if score > 0:
                results.append({
                    "text": self.chunks[idx],
                    "score": round(score, 4),
                    "source": self.metadata[idx].get("source", ""),
                    "filename": self.metadata[idx].get("filename", ""),
                    "metadata": self.metadata[idx],
                })

        return results

    # ── Персистенция ──

    def save(self, path: Optional[str] = None) -> None:
        """Сохраняет индекс в JSON (атомарно через tmp + os.replace)."""
        path = path or str(VECTOR_INDEX_FILE)
        data = {
            "version": 2,
            "chunks": self.chunks,
            "metadata": self.metadata,
            "vocabulary": self.vocabulary,
            "idf": self.idf.tolist() if self.idf.size > 0 else [],
            "bm25_matrix": self.bm25_matrix.tolist() if self.bm25_matrix is not None else [],
            "ngram_vocab": self.ngram_vocab,
            "ngram_matrix": self.ngram_matrix.tolist() if self.ngram_matrix is not None else [],
            "avg_doc_len": self._avg_doc_len,
        }
        import os as _os
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = str(target) + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        _os.replace(tmp_path, str(target))

    def load(self, path: Optional[str] = None) -> bool:
        """Загружает индекс из JSON."""
        path = path or str(VECTOR_INDEX_FILE)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("version", 1) < 2:
                return False  # Несовместимая версия

            self.chunks = data["chunks"]
            self.metadata = data["metadata"]
            self.vocabulary = data["vocabulary"]
            self.idf = np.array(data["idf"], dtype=np.float64)
            self.bm25_matrix = np.array(data["bm25_matrix"], dtype=np.float32)
            self.ngram_vocab = data["ngram_vocab"]
            self.ngram_matrix = np.array(data["ngram_matrix"], dtype=np.float32)
            self._avg_doc_len = data.get("avg_doc_len", 1.0)
            return True
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            return False

    def add_chunks(self, new_chunks: List[str], new_metadata: List[dict]) -> None:
        """
        Добавляет новые чанки и пересоздаёт индекс.

        Удобно для инкрементальной индексации (история, новые документы).
        """
        all_chunks = self.chunks + new_chunks
        all_metadata = self.metadata + new_metadata
        self.build(all_chunks, all_metadata)

    @property
    def total_chunks(self) -> int:
        return len(self.chunks)

    @property
    def vocab_size(self) -> int:
        return len(self.vocabulary)
