"""
Lina — Модуль автоматического обучения (Auto-learning).

Цель: Lina учится на вопросах пользователя.

Функциональность:
  1. Логирование вопросов, на которые нет ответа (unknown_questions)
  2. Запись коррекций от пользователя (corrections)
  3. Анализ частых вопросов → рекомендация добавить в базу
  4. Фидбек: пользователь может отметить ответ как полезный/бесполезный
"""

import json
import logging
import threading
import time
from collections import Counter
from pathlib import Path
from typing import List, Optional, Dict

from lina.config import LOGS_DIR, CACHE_DIR

logger = logging.getLogger("lina.rag.auto_learner")


# ─── Файлы хранения ───────────────────────────────────────────────────────

UNKNOWN_QUESTIONS_FILE = LOGS_DIR / "unknown_questions.jsonl"
CORRECTIONS_FILE = LOGS_DIR / "corrections.jsonl"
FEEDBACK_FILE = LOGS_DIR / "feedback.jsonl"
LEARNING_STATS_FILE = CACHE_DIR / "learning_stats.json"


# ─── Утилиты ──────────────────────────────────────────────────────────────

def _append_jsonl(path: Path, record: dict) -> None:
    """Добавляет JSON-запись в .jsonl файл (process-safe)."""
    import os as _os
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _os.open(str(path), _os.O_WRONLY | _os.O_CREAT | _os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        _os.close(fd)


_MAX_JSONL_RECORDS = 50_000


def _read_jsonl(path: Path, limit: int = 0) -> List[dict]:
    """Читает .jsonl файл. limit=0 → все записи (макс _MAX_JSONL_RECORDS)."""
    records: List[dict] = []
    if not path.exists():
        return records
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                    if len(records) >= _MAX_JSONL_RECORDS:
                        logger.warning("JSONL cap reached (%d) for %s", _MAX_JSONL_RECORDS, path.name)
                        break
    except Exception as e:
        logger.warning("Failed to read JSONL %s: %s", path, type(e).__name__)
        return records

    if limit > 0:
        return records[-limit:]
    return records


# ─── AutoLearner ──────────────────────────────────────────────────────────

class AutoLearner:
    """
    Система автоматического обучения Lina.

    Локальное, анонимное логирование для улучшения базы знаний.

    Возможности:
      - Записывает вопросы без ответа (RAG score < порога)
      - Записывает коррекции пользователя
      - Ведёт статистику фидбека (полезно / бесполезно)
      - Анализирует паттерны: частые вопросы, проблемные категории
    """

    # Порог: если RAG score ниже этого — вопрос считается "без ответа"
    LOW_CONFIDENCE_THRESHOLD = 0.15

    def __init__(self):
        self._stats: Optional[dict] = None
        self._stats_lock = threading.Lock()

    # ── Запись: неизвестные вопросы ──

    def log_unknown_question(
        self,
        question: str,
        best_score: float = 0.0,
        best_source: str = "",
        category_hint: str = "",
    ) -> None:
        """
        Записывает вопрос, на который база знаний не дала хорошего ответа.

        Вызывается автоматически когда лучший RAG score < порога.

        Args:
            question: Текст вопроса пользователя.
            best_score: Лучший score из RAG.
            best_source: Лучший источник (если был).
            category_hint: Предполагаемая категория.
        """
        if not question or not question.strip():
            return

        record = {
            "timestamp": time.time(),
            "question": question.strip()[:500],
            "best_score": round(best_score, 4),
            "best_source": best_source,
            "category_hint": category_hint,
        }
        _append_jsonl(UNKNOWN_QUESTIONS_FILE, record)
        self._invalidate_stats()

    def is_low_confidence(self, score: float) -> bool:
        """Проверяет, ниже ли score порога уверенности."""
        return score < self.LOW_CONFIDENCE_THRESHOLD

    # ── Запись: коррекции ──

    def log_correction(
        self,
        original_question: str,
        wrong_answer: str,
        correct_info: str,
        source: str = "user",
    ) -> None:
        """
        Записывает коррекцию от пользователя.

        Вызывается когда пользователь говорит "это неправильно"
        и даёт правильную информацию.

        Args:
            original_question: Исходный вопрос.
            wrong_answer: Неправильный ответ (который дал Lina).
            correct_info: Правильная информация от пользователя.
            source: Источник коррекции.
        """
        if not original_question or not correct_info:
            return

        record = {
            "timestamp": time.time(),
            "question": original_question.strip()[:500],
            "wrong_answer": wrong_answer.strip()[:1000],
            "correct_info": correct_info.strip()[:2000],
            "source": source,
            "applied": False,
        }
        _append_jsonl(CORRECTIONS_FILE, record)
        self._invalidate_stats()

    # ── Запись: фидбек ──

    def log_feedback(
        self,
        question: str,
        answer: str,
        helpful: bool,
        comment: str = "",
    ) -> None:
        """
        Записывает фидбек пользователя на ответ.

        Args:
            question: Вопрос.
            answer: Ответ Lina.
            helpful: Полезный (True) или нет (False).
            comment: Опциональный комментарий.
        """
        record = {
            "timestamp": time.time(),
            "question": question.strip()[:500],
            "answer_preview": answer.strip()[:200],
            "helpful": helpful,
            "comment": comment.strip()[:500] if comment else "",
        }
        _append_jsonl(FEEDBACK_FILE, record)
        self._invalidate_stats()

    # ── Анализ ──

    def get_frequent_unknown(self, top_n: int = 20) -> List[Dict]:
        """
        Анализирует self-most frequent unknown questions.

        Группирует похожие вопросы (по ключевым словам)
        и возвращает топ-N тем, для которых нужен контент.

        Returns:
            [{topic, count, examples, category_hint}, ...]
        """
        records = _read_jsonl(UNKNOWN_QUESTIONS_FILE)
        if not records:
            return []

        # Группируем по нормализованным ключевым словам
        topic_counter: Counter = Counter()
        topic_examples: Dict[str, List[str]] = {}
        topic_categories: Dict[str, Counter] = {}

        for rec in records:
            q = rec.get("question", "")
            key = self._normalize_for_grouping(q)
            if not key:
                continue

            topic_counter[key] += 1
            if key not in topic_examples:
                topic_examples[key] = []
            if len(topic_examples[key]) < 3:
                topic_examples[key].append(q)

            cat = rec.get("category_hint", "")
            if cat:
                if key not in topic_categories:
                    topic_categories[key] = Counter()
                topic_categories[key][cat] += 1

        # Топ-N
        result = []
        for topic, count in topic_counter.most_common(top_n):
            cat = ""
            if topic in topic_categories:
                cat = topic_categories[topic].most_common(1)[0][0]
            result.append({
                "topic": topic,
                "count": count,
                "examples": topic_examples.get(topic, []),
                "category_hint": cat,
            })

        return result

    def get_corrections(self, limit: int = 50, unapplied_only: bool = False) -> List[dict]:
        """Возвращает записанные коррекции."""
        records = _read_jsonl(CORRECTIONS_FILE, limit=limit)
        if unapplied_only:
            records = [r for r in records if not r.get("applied", False)]
        return records

    def get_feedback_summary(self) -> dict:
        """Возвращает сводку фидбека."""
        records = _read_jsonl(FEEDBACK_FILE)
        total = len(records)
        helpful = sum(1 for r in records if r.get("helpful", False))
        unhelpful = total - helpful

        return {
            "total": total,
            "helpful": helpful,
            "unhelpful": unhelpful,
            "helpfulness_rate": round(helpful / total, 2) if total > 0 else 0.0,
        }

    def get_stats(self) -> dict:
        """Возвращает полную статистику автообучения."""
        with self._stats_lock:
            if self._stats is not None:
                return self._stats

        unknown_count = len(_read_jsonl(UNKNOWN_QUESTIONS_FILE))
        correction_count = len(_read_jsonl(CORRECTIONS_FILE))
        feedback = self.get_feedback_summary()
        frequent = self.get_frequent_unknown(top_n=5)

        result = {
            "unknown_questions": unknown_count,
            "corrections": correction_count,
            "feedback": feedback,
            "top_unknown_topics": frequent,
        }
        with self._stats_lock:
            self._stats = result
        return result

    # ── Утилиты ──

    def _normalize_for_grouping(self, text: str) -> str:
        """
        Нормализует вопрос для группировки.

        Извлекает 3-5 ключевых слов, убирает стоп-слова.
        """
        import re
        text = text.lower()
        # Убираем стоп-слова
        stop = {
            "как", "что", "где", "когда", "почему", "какой", "какая", "какие",
            "это", "не", "на", "в", "с", "из", "по", "для", "мне",
            "можно", "нужно", "надо", "ли", "бы", "же", "то",
            "и", "или", "а", "но", "если",
            "the", "a", "is", "in", "on", "to", "how", "what", "why",
        }
        words = re.findall(r'[a-zа-яёA-ZА-ЯЁ0-9]+', text)
        keywords = [w for w in words if w not in stop and len(w) > 2]
        # Берём первые 5 ключевых слов
        return " ".join(sorted(keywords[:5]))

    def _invalidate_stats(self) -> None:
        """Инвалидирует кешированную статистику."""
        with self._stats_lock:
            self._stats = None

    def clear_all(self) -> dict:
        """Очищает все данные автообучения."""
        errors: list[str] = []
        for f in (UNKNOWN_QUESTIONS_FILE, CORRECTIONS_FILE, FEEDBACK_FILE, LEARNING_STATS_FILE):
            try:
                if f.exists():
                    f.unlink()
            except Exception as e:
                logger.warning("Failed to delete %s: %s", f.name, type(e).__name__)
                errors.append(f.name)
        self._invalidate_stats()
        if errors:
            return {"status": "partial", "message": f"Не удалось удалить: {', '.join(errors)}"}
        return {"status": "success", "message": "Данные автообучения очищены."}
