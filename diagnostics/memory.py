"""
ContextMemoryEngine — память о проблемах и исправлениях.

Запоминает:
- Какие ошибки возникали
- Какие фиксы применялись
- Что сработало / что нет
- Конфигурацию системы
- Формирует профиль конкретной машины

Хранение: JSON-файлы в ~/.local/share/lina/memory/

Phase: PROBLEM TERMINATOR / Module 5
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Dataclasses — записи памяти
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FixRecord:
    """Запись об одном применённом исправлении."""
    timestamp: str                     # ISO 8601
    category: str                      # Категория ошибки
    problem: str                       # Описание проблемы
    diagnosis: str                     # Диагноз
    actions_taken: List[str]           # Что было сделано
    outcome: str                       # "success" / "failed" / "partial" / "rolled_back"
    verified: bool = False             # Подтверждён ли результат
    notes: str = ""                    # Дополнительные заметки
    search_query: str = ""             # Что искали онлайн
    web_source: str = ""               # Источник решения

    @property
    def fingerprint(self) -> str:
        return hashlib.md5(f"{self.category}:{self.problem[:100]}".encode()).hexdigest()[:12]


@dataclass
class SystemProfile:
    """Профиль системы — постоянные характеристики машины."""
    hostname: str = ""
    distro: str = ""
    kernel: str = ""
    de: str = ""
    display_server: str = ""
    pkg_manager: str = ""
    gpu: str = ""
    cpu: str = ""
    ram_mb: int = 0
    disk_total: str = ""
    last_updated: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryStats:
    """Статистика памяти."""
    total_records: int = 0
    successful_fixes: int = 0
    failed_fixes: int = 0
    categories: Dict[str, int] = field(default_factory=dict)
    recurring_problems: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
#  ContextMemoryEngine
# ═══════════════════════════════════════════════════════════════════

class ContextMemoryEngine:
    """
    Запоминает историю проблем и исправлений.

    Позволяет:
    - Записывать фиксы и их результаты
    - Искать прошлые решения по категории/проблеме
    - Определять повторяющиеся проблемы
    - Хранить профиль системы
    - Рекомендовать проверенные решения
    """

    _BASE_DIR = Path.home() / ".local" / "share" / "lina" / "memory"
    _RECORDS_FILE = "fix_records.json"
    _PROFILE_FILE = "system_profile.json"
    _MAX_RECORDS = 1000  # Ротация

    def __init__(self) -> None:
        self._BASE_DIR.mkdir(parents=True, exist_ok=True)
        self._records: List[FixRecord] = []
        self._profile: Optional[SystemProfile] = None
        self._load()

    # ─── Загрузка / Сохранение ────────────────────────────────

    def _load(self) -> None:
        # Records
        path = self._BASE_DIR / self._RECORDS_FILE
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self._records = [FixRecord(**r) for r in data]
            except Exception as e:
                logger.warning("Memory load error: %s", e)
                self._records = []
        # Profile
        ppath = self._BASE_DIR / self._PROFILE_FILE
        if ppath.exists():
            try:
                data = json.loads(ppath.read_text())
                self._profile = SystemProfile(**data)
            except Exception:
                self._profile = None

    def _save_records(self) -> None:
        path = self._BASE_DIR / self._RECORDS_FILE
        try:
            data = [asdict(r) for r in self._records[-self._MAX_RECORDS:]]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning("Memory save error: %s", e)

    def _save_profile(self) -> None:
        path = self._BASE_DIR / self._PROFILE_FILE
        try:
            if self._profile:
                path.write_text(json.dumps(
                    self._profile.to_dict(), ensure_ascii=False, indent=2
                ))
        except Exception as e:
            logger.warning("Profile save error: %s", e)

    # ─── Запись фикса ─────────────────────────────────────────

    def record_fix(
        self,
        category: str,
        problem: str,
        diagnosis: str,
        actions: List[str],
        outcome: str,
        verified: bool = False,
        notes: str = "",
        search_query: str = "",
        web_source: str = "",
    ) -> None:
        """Записывает результат исправления в память."""
        record = FixRecord(
            timestamp=datetime.datetime.now().isoformat(),
            category=category,
            problem=problem,
            diagnosis=diagnosis,
            actions_taken=actions,
            outcome=outcome,
            verified=verified,
            notes=notes,
            search_query=search_query,
            web_source=web_source,
        )
        self._records.append(record)
        self._save_records()
        logger.debug("Memory: recorded fix for %s (%s)", category, outcome)

    # ─── Поиск в памяти ──────────────────────────────────────

    def find_similar(self, category: str, problem: str = "", limit: int = 5) -> List[FixRecord]:
        """Ищет похожие записи по категории и описанию."""
        results = []
        problem_lower = problem.lower()
        for record in reversed(self._records):  # Свежие первыми
            if record.category == category:
                results.append(record)
            elif problem_lower and any(
                w in record.problem.lower()
                for w in problem_lower.split()
                if len(w) > 3
            ):
                results.append(record)
            if len(results) >= limit:
                break
        return results

    def find_successful(self, category: str) -> List[FixRecord]:
        """Ищет успешные фиксы для данной категории."""
        return [
            r for r in reversed(self._records)
            if r.category == category and r.outcome == "success"
        ][:5]

    def find_failed(self, category: str) -> List[FixRecord]:
        """Ищет неудачные фиксы — чтобы не повторять."""
        return [
            r for r in reversed(self._records)
            if r.category == category and r.outcome in ("failed", "rolled_back")
        ][:5]

    def get_recommendation(self, category: str) -> Optional[str]:
        """Рекомендует действие на основе прошлого опыта."""
        successful = self.find_successful(category)
        failed = self.find_failed(category)

        if successful:
            # Рекомендуем то, что работало раньше
            best = successful[0]
            actions_str = ", ".join(best.actions_taken[:3])
            return f"✅ Ранее помогло: {actions_str} ({best.timestamp[:10]})"

        if failed:
            # Предупреждаем о неудачах
            worst = failed[0]
            actions_str = ", ".join(worst.actions_taken[:3])
            return f"⚠️ Ранее НЕ помогло: {actions_str} ({worst.timestamp[:10]})"

        return None

    # ─── Профиль системы ──────────────────────────────────────

    def update_profile(self, state: Any) -> None:
        """Обновляет профиль системы из SystemState."""
        self._profile = SystemProfile(
            hostname=getattr(state, "hostname", ""),
            distro=getattr(state, "distro", ""),
            kernel=getattr(state, "kernel", ""),
            de=getattr(state, "de", ""),
            display_server=getattr(state, "display_server", ""),
            pkg_manager=getattr(state, "packages", None)
                and state.packages.details.get("manager", "") or "",
            gpu=getattr(state, "gpu", None)
                and (state.gpu.details.get("gpus", [""])[0][:60]
                     if state.gpu.details.get("gpus") else "") or "",
            cpu=getattr(state, "cpu", None)
                and state.cpu.details.get("name", "")[:60] or "",
            ram_mb=getattr(state, "ram", None)
                and state.ram.details.get("total_mb", 0) or 0,
            disk_total=getattr(state, "disk", None)
                and state.disk.details.get("total", "") or "",
            last_updated=datetime.datetime.now().isoformat(),
        )
        self._save_profile()

    def get_profile(self) -> Optional[SystemProfile]:
        return self._profile

    # ─── Статистика ───────────────────────────────────────────

    def get_stats(self) -> MemoryStats:
        """Статистика памяти."""
        stats = MemoryStats(total_records=len(self._records))
        cat_counts: Dict[str, int] = {}
        for r in self._records:
            if r.outcome == "success":
                stats.successful_fixes += 1
            elif r.outcome in ("failed", "rolled_back"):
                stats.failed_fixes += 1
            cat_counts[r.category] = cat_counts.get(r.category, 0) + 1
        stats.categories = cat_counts

        # Повторяющиеся проблемы (>= 3 раз)
        for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
            if count >= 3:
                stats.recurring_problems.append(f"{cat}: {count}x")

        return stats

    def get_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Последние N записей для отображения."""
        return [asdict(r) for r in self._records[-limit:]]

    def clear(self) -> None:
        """Очистка памяти (для тестов)."""
        self._records = []
        self._profile = None
        self._save_records()

    # ─── OVERLORD: расширенная аналитика ──────────────────────

    def get_success_rate(self, category: str = "") -> float:
        """Процент успешных фиксов (0.0 - 1.0).

        Args:
            category: Если указано — для конкретной категории.
                      Если пусто — общий по всем.
        """
        relevant = self._records
        if category:
            relevant = [r for r in self._records if r.category == category]
        if not relevant:
            return 0.0
        successes = sum(1 for r in relevant if r.outcome == "success")
        return successes / len(relevant)

    def get_time_to_recovery(self, category: str = "") -> Optional[float]:
        """Среднее время восстановления (минуты) для данной категории.

        Считает интервалы между последовательными записями
        (failed → success) как time-to-recovery.
        """
        relevant = self._records
        if category:
            relevant = [r for r in self._records if r.category == category]
        if len(relevant) < 2:
            return None

        recovery_times: List[float] = []
        prev_failed_ts: Optional[str] = None

        for r in relevant:
            if r.outcome in ("failed", "rolled_back"):
                prev_failed_ts = r.timestamp
            elif r.outcome == "success" and prev_failed_ts:
                try:
                    t_fail = datetime.datetime.fromisoformat(prev_failed_ts)
                    t_ok = datetime.datetime.fromisoformat(r.timestamp)
                    delta = (t_ok - t_fail).total_seconds() / 60.0
                    if 0 < delta < 1440:  # До 24 часов
                        recovery_times.append(delta)
                except (ValueError, TypeError):
                    pass
                prev_failed_ts = None

        return sum(recovery_times) / len(recovery_times) if recovery_times else None

    def get_recurring_categories(self, min_count: int = 3) -> List[Dict[str, Any]]:
        """Категории с повторяющимися проблемами."""
        cat_data: Dict[str, Dict[str, int]] = {}
        for r in self._records:
            if r.category not in cat_data:
                cat_data[r.category] = {"total": 0, "success": 0, "failed": 0}
            cat_data[r.category]["total"] += 1
            if r.outcome == "success":
                cat_data[r.category]["success"] += 1
            elif r.outcome in ("failed", "rolled_back"):
                cat_data[r.category]["failed"] += 1

        result = []
        for cat, counts in cat_data.items():
            if counts["total"] >= min_count:
                result.append({
                    "category": cat,
                    "total": counts["total"],
                    "success": counts["success"],
                    "failed": counts["failed"],
                    "success_rate": counts["success"] / counts["total"] if counts["total"] else 0,
                })
        return sorted(result, key=lambda x: -x["total"])


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_engine: Optional[ContextMemoryEngine] = None


def get_memory() -> ContextMemoryEngine:
    global _engine
    if _engine is None:
        _engine = ContextMemoryEngine()
    return _engine
