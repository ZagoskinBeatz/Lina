# -*- coding: utf-8 -*-
"""
Lina — Трекер неизвестных вопросов и пользовательских коррекций.

Функциональность:
  1. Логирование вопросов, на которые Lina не нашла ответ в RAG
  2. Логирование пользовательских коррекций ("это неправильно")
  3. Анализ частых неизвестных вопросов → приоритеты для базы знаний
  4. Экспорт коррекций для fine-tuning / улучшения промптов

Хранение: JSONL файлы в logs/ (атомарная запись, ротация).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from lina.config import LOGS_DIR, CACHE_DIR
from lina.system.logger import logger

UNKNOWN_QUESTIONS_FILE = LOGS_DIR / "unknown_questions.jsonl"
CORRECTIONS_FILE = LOGS_DIR / "user_corrections.jsonl"
TRACKER_STATS_FILE = CACHE_DIR / "tracker_stats.json"

MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB per file
MAX_ENTRIES_ANALYZE = 5000


class UnknownQuestionTracker:
    """
    Отслеживает вопросы без ответа и коррекции пользователя.

    Вопрос считается «неизвестным» если:
      - RAG не нашёл релевантных документов
      - LLM дал «не знаю» ответ
      - Confidence ответа ниже порога

    Использование:
        tracker = get_tracker()
        tracker.record_unknown("Как настроить Waydroid?", reason="no_rag_results")
        tracker.record_correction("Как обновить систему?",
                                  wrong_answer="pacman -S",
                                  correct_info="Правильно: pacman -Syu")
        report = tracker.analyze_gaps()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._unknown_count: int = 0
        self._correction_count: int = 0
        self._load_stats()

    def _load_stats(self) -> None:
        """Загрузить счётчики из кэша."""
        try:
            if TRACKER_STATS_FILE.exists():
                data = json.loads(TRACKER_STATS_FILE.read_text("utf-8"))
                self._unknown_count = data.get("unknown_count", 0)
                self._correction_count = data.get("correction_count", 0)
        except Exception:
            pass

    def _save_stats(self) -> None:
        """Сохранить счётчики (вызывать под lock)."""
        try:
            data = {
                "unknown_count": self._unknown_count,
                "correction_count": self._correction_count,
                "updated_at": time.time(),
            }
            tmp = TRACKER_STATS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(str(tmp), str(TRACKER_STATS_FILE))
        except Exception as e:
            logger.debug("TrackerStats save error: %s", e)

    @staticmethod
    def _append_jsonl(filepath: Path, entry: dict) -> None:
        """Атомарная запись строки в JSONL с file-lock."""
        try:
            # Ротация если файл слишком большой
            if filepath.exists() and filepath.stat().st_size > MAX_LOG_SIZE:
                rotated = filepath.with_suffix(".jsonl.old")
                if rotated.exists():
                    rotated.unlink()
                filepath.rename(rotated)

            filepath.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with open(filepath, "a", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.write(line)
                fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            logger.debug("JSONL append error (%s): %s", filepath.name, e)

    # ── Запись неизвестных вопросов ──────────────────────

    def record_unknown(
        self,
        question: str,
        *,
        reason: str = "no_answer",
        intent: str = "",
        rag_score: float = 0.0,
        context: str = "",
    ) -> None:
        """Записать вопрос, на который не найден ответ.

        Args:
            question: Текст вопроса пользователя.
            reason: Причина (no_rag_results, low_confidence,
                    llm_deflected, no_knowledge).
            intent: Определённый intent.
            rag_score: Максимальный score из RAG.
            context: Дополнительный контекст.
        """
        if not question or len(question.strip()) < 3:
            return

        entry = {
            "question": question.strip()[:500],
            "reason": reason,
            "intent": intent,
            "rag_score": round(rag_score, 3),
            "context": context[:200] if context else "",
            "timestamp": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        self._append_jsonl(UNKNOWN_QUESTIONS_FILE, entry)

        with self._lock:
            self._unknown_count += 1
            self._save_stats()

        logger.debug("Unknown question logged: %s (reason=%s)",
                     question[:60], reason)

    # ── Запись коррекций пользователя ────────────────────

    def record_correction(
        self,
        question: str,
        *,
        wrong_answer: str = "",
        correct_info: str = "",
        user_feedback: str = "",
    ) -> None:
        """Записать коррекцию от пользователя.

        Args:
            question: Исходный вопрос.
            wrong_answer: Неправильный ответ Lina (обрезается).
            correct_info: Правильная информация от пользователя.
            user_feedback: Обратная связь ("это неправильно", etc.).
        """
        if not question:
            return

        entry = {
            "question": question.strip()[:500],
            "wrong_answer": wrong_answer[:1000] if wrong_answer else "",
            "correct_info": correct_info[:1000] if correct_info else "",
            "user_feedback": user_feedback[:500] if user_feedback else "",
            "timestamp": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        self._append_jsonl(CORRECTIONS_FILE, entry)

        with self._lock:
            self._correction_count += 1
            self._save_stats()

        logger.info("User correction logged for: %s", question[:60])

    # ── Анализ пробелов в знаниях ────────────────────────

    def analyze_gaps(self, top_n: int = 20) -> Dict:
        """Анализ частых неизвестных вопросов для приоритизации базы знаний.

        Returns:
            dict с top_unknown, top_reasons, recommendations.
        """
        questions: List[str] = []
        reasons: List[str] = []

        try:
            if UNKNOWN_QUESTIONS_FILE.exists():
                with open(UNKNOWN_QUESTIONS_FILE, "r", encoding="utf-8") as f:
                    for i, line in enumerate(f):
                        if i >= MAX_ENTRIES_ANALYZE:
                            break
                        line = line.strip()
                        if line:
                            try:
                                entry = json.loads(line)
                                q_norm = self._normalize(entry.get("question", ""))
                                if q_norm:
                                    questions.append(q_norm)
                                reasons.append(entry.get("reason", "unknown"))
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            logger.debug("Gap analysis read error: %s", e)

        q_freq = Counter(questions).most_common(top_n)
        r_freq = Counter(reasons).most_common(10)

        # Кластеризация по категориям
        categories: Dict[str, int] = {}
        category_patterns = {
            "networking": r"сет[ьи]|wifi|интернет|vpn|dns|ip|firewall|порт",
            "packages": r"установ|пакет|pacman|apt|dnf|flatpak|snap|удали",
            "desktop": r"kde|gnome|тема|обои|панел|виджет|wayland|x11",
            "audio": r"звук|аудио|микрофон|наушник|pipewire|pulse",
            "gpu": r"gpu|видеокарт|драйвер|nvidia|amd|mesa|vulkan",
            "boot": r"загруз|grub|boot|uefi|initram",
            "storage": r"диск|раздел|монтир|fstab|lvm|btrfs|ext4",
            "security": r"пароль|шифрован|ssh|gpg|firewall|selinux",
            "services": r"systemd|сервис|служб|systemctl|журнал|journalctl",
            "dev_tools": r"python|git|docker|компиляц|gcc|node|rust",
        }

        for q_norm, count in q_freq:
            matched = False
            for cat, pattern in category_patterns.items():
                if re.search(pattern, q_norm, re.IGNORECASE):
                    categories[cat] = categories.get(cat, 0) + count
                    matched = True
                    break
            if not matched:
                categories["other"] = categories.get("other", 0) + count

        return {
            "total_unknown": len(questions),
            "total_corrections": self._correction_count,
            "top_unknown_questions": [
                {"question": q, "count": c} for q, c in q_freq
            ],
            "top_reasons": [
                {"reason": r, "count": c} for r, c in r_freq
            ],
            "knowledge_gaps_by_category": dict(
                sorted(categories.items(), key=lambda x: x[1], reverse=True)
            ),
            "recommendation": self._generate_recommendations(categories, q_freq),
        }

    def _generate_recommendations(
        self,
        categories: Dict[str, int],
        top_questions: list,
    ) -> List[str]:
        """Генерирует рекомендации для расширения базы знаний."""
        recs = []
        top_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:5]
        for cat, count in top_cats:
            if count >= 3:
                recs.append(
                    f"Расширить knowledge/{cat}/ — {count} неотвеченных вопросов"
                )
        if len(top_questions) > 0 and top_questions[0][1] >= 5:
            recs.append(
                f"Приоритетный FAQ: «{top_questions[0][0]}» ({top_questions[0][1]} раз)"
            )
        return recs

    @staticmethod
    def _normalize(q: str) -> str:
        """Нормализация вопроса для подсчёта частоты."""
        return re.sub(r"[^\w\s]", "", q.lower().strip())[:120]

    # ── Экспорт коррекций ────────────────────────────────

    def export_corrections(self) -> List[Dict]:
        """Экспорт всех пользовательских коррекций."""
        corrections = []
        try:
            if CORRECTIONS_FILE.exists():
                with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                corrections.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            logger.debug("Corrections export error: %s", e)
        return corrections

    # ── Статистика ───────────────────────────────────────

    def get_stats(self) -> Dict:
        """Общая статистика."""
        with self._lock:
            return {
                "unknown_questions_logged": self._unknown_count,
                "corrections_logged": self._correction_count,
                "unknown_file_exists": UNKNOWN_QUESTIONS_FILE.exists(),
                "corrections_file_exists": CORRECTIONS_FILE.exists(),
            }


# ── Singleton ─────────────────────────────────────────────

_tracker: Optional[UnknownQuestionTracker] = None
_tracker_lock = threading.Lock()


def get_tracker() -> UnknownQuestionTracker:
    """Получить единственный экземпляр трекера."""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = UnknownQuestionTracker()
    return _tracker
