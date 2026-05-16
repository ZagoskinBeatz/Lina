"""
Lina — Сборщик знаний из взаимодействий.

Автоматический сбор фрагментов знаний:
  - Успешные ответы LLM → база знаний
  - Исправления пользователя → обучающие примеры
  - Часто задаваемые вопросы → кэш
  - Обнаруженные факты → knowledge-файлы
"""

import json
import os
import threading
import time
import re
from pathlib import Path
from typing import Optional

from lina.config import KNOWLEDGE_DIR, CACHE_DIR
from lina.system.logger import logger


FRAGMENTS_FILE = CACHE_DIR / "knowledge_fragments.json"
FAQ_FILE = CACHE_DIR / "faq_cache.json"
MAX_FRAGMENTS = 500
MAX_FREQ_ENTRIES = 1000  # Cap on unique question frequency entries
MIN_QUALITY_LENGTH = 50  # Мин. длина ответа для сохранения


class KnowledgeCollector:
    """
    Собирает полезные фрагменты знаний из взаимодействий.

    Три потока:
    1. Фрагменты знаний — успешные Q&A пары
    2. FAQ — часто повторяющиеся вопросы
    3. Экспорт — выгрузка накопленного в knowledge/
    """

    def __init__(self):
        self.fragments: list = []
        self.question_freq: dict = {}  # вопрос -> частота
        self._best_by_question: dict = {}  # q_norm -> best fragment
        self._data_lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        """Загрузка из файла."""
        try:
            if FRAGMENTS_FILE.exists():
                data = json.loads(FRAGMENTS_FILE.read_text(encoding="utf-8"))
                self.fragments = data.get("fragments", [])
                self.question_freq = data.get("frequencies", {})
                # Build reverse index for O(1) FAQ lookup
                self._best_by_question = {}
                for f in self.fragments:
                    q_norm = self._normalize_question(f["question"])
                    existing = self._best_by_question.get(q_norm)
                    if existing is None or f.get("quality", 0) > existing.get("quality", 0):
                        self._best_by_question[q_norm] = f
        except Exception as e:
            logger.warning(f"KnowledgeCollector load error: {e}")

    def _save(self) -> None:
        """Сохранение в файл (атомарная запись)."""
        with self._data_lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        """Сохранение в файл (вызывать только под _data_lock)."""
        try:
            # Cap question_freq to MAX_FREQ_ENTRIES (keep highest freq)
            if len(self.question_freq) > MAX_FREQ_ENTRIES:
                top = sorted(self.question_freq.items(), key=lambda x: x[1], reverse=True)[:MAX_FREQ_ENTRIES]
                self.question_freq = dict(top)

            data = {
                "fragments": self.fragments[-MAX_FRAGMENTS:],
                "frequencies": self.question_freq,
                "saved_at": time.time(),
            }
            tmp_file = FRAGMENTS_FILE.with_suffix('.tmp')
            tmp_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(str(tmp_file), str(FRAGMENTS_FILE))
        except Exception as e:
            logger.error(f"KnowledgeCollector save error: {e}")

    def record_interaction(
        self,
        question: str,
        answer: str,
        source: str = "llm",
        quality: Optional[float] = None,
    ) -> None:
        """
        Записывает взаимодействие Q&A.

        Args:
            question: Вопрос пользователя.
            answer: Ответ Lina.
            source: Источник (llm, rag, cache).
            quality: Оценка качества (0-1), None = автоматически.
        """
        if not answer or len(answer) < MIN_QUALITY_LENGTH:
            return

        # Автоматическая оценка качества
        if quality is None:
            quality = self._estimate_quality(question, answer)

        if quality < 0.3:
            return  # Слишком низкое качество

        # Нормализуем вопрос для частотного подсчёта
        q_norm = self._normalize_question(question)

        fragment = {
            "question": question,
            "answer": answer[:2000],  # Обрезаем длинные ответы
            "source": source,
            "quality": round(quality, 2),
            "timestamp": time.time(),
        }

        with self._data_lock:
            self.question_freq[q_norm] = self.question_freq.get(q_norm, 0) + 1
            fragment["freq"] = self.question_freq[q_norm]

            self.fragments.append(fragment)

            # Update reverse index
            existing = self._best_by_question.get(q_norm)
            if existing is None or fragment["quality"] > existing.get("quality", 0):
                self._best_by_question[q_norm] = fragment

            # Ротация
            if len(self.fragments) > MAX_FRAGMENTS:
                # Оставляем лучшие по качеству
                self.fragments.sort(key=lambda f: f["quality"], reverse=True)
                self.fragments = self.fragments[:MAX_FRAGMENTS]

            self._save_unlocked()

    def _estimate_quality(self, question: str, answer: str) -> float:
        """
        Автоматическая оценка качества ответа.

        Эвристики:
        - Длина ответа (длиннее = лучше, до предела)
        - Наличие ключевых слов из вопроса
        - Структурированность (списки, блоки кода)
        - Отсутствие "не знаю" ответов
        """
        score = 0.5

        # Длина
        if len(answer) > 200:
            score += 0.1
        if len(answer) > 500:
            score += 0.1

        # Ключевые слова из вопроса в ответе (без стоп-слов)
        _stop = {"как", "что", "где", "кто", "в", "на", "с", "и", "а", "но",
                 "или", "не", "это", "по", "для", "из", "за", "от", "до",
                 "is", "the", "a", "an", "to", "in", "of", "for", "and", "or",
                 "how", "what", "where", "who", "why", "do", "does", "can"}
        q_words = set(question.lower().split()) - _stop
        a_words = set(answer.lower().split()) - _stop
        overlap = len(q_words & a_words) / max(len(q_words), 1)
        score += overlap * 0.1

        # Структурированность
        if re.search(r"[\d]+\.", answer):
            score += 0.05  # Нумерованный список
        if "```" in answer:
            score += 0.05  # Блок кода
        if "- " in answer:
            score += 0.05  # Маркированный список

        # Штрафы
        bad_phrases = ["не знаю", "не могу", "извините", "к сожалению"]
        if any(bp in answer.lower() for bp in bad_phrases):
            score -= 0.2

        return max(0.0, min(1.0, score))

    def _normalize_question(self, q: str) -> str:
        """Нормализация вопроса для подсчёта частоты."""
        return re.sub(r"[^\w\s]", "", q.lower().strip())[:100]

    def get_faq(self, min_freq: int = 3) -> list:
        """Возвращает часто задаваемые вопросы (O(N) via reverse index)."""
        faq = []
        with self._data_lock:
            for q_norm, freq in self.question_freq.items():
                if freq >= min_freq:
                    best = self._best_by_question.get(q_norm)
                    if best:
                        faq.append({
                            "question": best["question"],
                            "answer": best["answer"],
                            "frequency": freq,
                            "quality": best["quality"],
                        })
        faq.sort(key=lambda x: x["frequency"], reverse=True)
        return faq

    def export_to_knowledge(self, min_quality: float = 0.7) -> int:
        """
        Экспортирует качественные фрагменты в knowledge/ для RAG.

        Returns:
            Количество экспортированных фрагментов.
        """
        good = [f for f in self.fragments if f["quality"] >= min_quality]
        if not good:
            return 0

        export_dir = KNOWLEDGE_DIR / "auto_learned"
        export_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        export_file = export_dir / f"learned_{timestamp}.md"

        lines = [
            f"# Автоматически собранные знания ({timestamp})\n",
            f"Фрагментов: {len(good)}\n",
        ]

        for i, f in enumerate(good, 1):
            lines.append(f"\n## {i}. {f['question']}\n")
            lines.append(f"{f['answer']}\n")
            lines.append(f"*Качество: {f['quality']}, Источник: {f['source']}*\n")

        export_file.write_text("\n".join(lines), encoding="utf-8")
        logger.audit("knowledge_export", details={
            "fragments": len(good),
            "file": str(export_file),
        })

        return len(good)

    def get_stats(self) -> dict:
        """Статистика коллектора."""
        with self._data_lock:
            faq_count = sum(
                1 for q, freq in self.question_freq.items()
                if freq >= 3 and q in self._best_by_question
            )
            return {
                "total_fragments": len(self.fragments),
                "unique_questions": len(self.question_freq),
                "avg_quality": (
                    round(sum(f["quality"] for f in self.fragments) / len(self.fragments), 2)
                    if self.fragments else 0
                ),
                "faq_count": faq_count,
                "exportable": len([f for f in self.fragments if f["quality"] >= 0.7]),
            }
