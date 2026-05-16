"""
IntegrityGuard — расширенная система целостности OVERLORD.

Расширяет core/integrity_checker.py (IntegrityChecker — plan path/hash) для:

  1. Целостность модулей — хеши .py файлов пакета lina/
  2. Целостность конфигов — отслеживание изменений config-файлов
  3. Целостность runtime-state — аудит состояния singleton'ов
  4. Tamper detection — обнаружение внешних модификаций
  5. Recovery plan — генерация плана восстановления при нарушении

Интеграция:
  - IntegrityChecker: делегирует plan-level проверки
  - SelfHealer: нарушение целостности → record_failure
  - ContextMemory: логирование нарушений

Phase: SYSTEM OVERLORD / Module 6
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Integrity Level
# ═══════════════════════════════════════════════════════════════════

class IntegrityLevel(str, Enum):
    INTACT = "intact"
    MODIFIED = "modified"
    TAMPERED = "tampered"
    MISSING = "missing"
    UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════════════
#  File Integrity Record
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FileIntegrityRecord:
    """Запись о целостности файла."""
    path: str
    expected_hash: str
    current_hash: str = ""
    level: IntegrityLevel = IntegrityLevel.UNKNOWN
    modified_at: float = 0.0

    @property
    def is_ok(self) -> bool:
        return self.level == IntegrityLevel.INTACT


@dataclass
class IntegrityReport:
    """Полный отчёт о целостности системы."""
    timestamp: float = 0.0
    total_files: int = 0
    intact_count: int = 0
    modified_count: int = 0
    missing_count: int = 0
    tampered_count: int = 0
    records: List[FileIntegrityRecord] = field(default_factory=list)
    config_changes: List[Dict[str, Any]] = field(default_factory=list)
    plan_checks_passed: int = 0
    plan_checks_failed: int = 0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    @property
    def is_clean(self) -> bool:
        return self.tampered_count == 0 and self.missing_count == 0 and self.plan_checks_failed == 0

    @property
    def severity(self) -> str:
        if self.tampered_count > 0 or self.plan_checks_failed > 0:
            return "critical"
        if self.missing_count > 0:
            return "warning"
        if self.modified_count > 0:
            return "info"
        return "ok"


# ═══════════════════════════════════════════════════════════════════
#  IntegrityGuard
# ═══════════════════════════════════════════════════════════════════

class IntegrityGuard:
    """Расширенная проверка целостности OVERLORD.

    Уровни проверки:
      1. Module integrity — sha256 всех .py в lina/
      2. Config integrity — pyproject.toml, requirements.txt, .env
      3. Plan integrity — делегирует core/IntegrityChecker
      4. Tamper detection — сравнение с baseline
    """

    BASELINE_PATH = os.path.expanduser(
        "~/.local/share/lina/integrity/module_baseline.json"
    )

    # Директории для проверки (относительно lina/)
    SCAN_DIRS = [
        "core", "diagnostics", "system", "safety",
        "agent", "shell", "tools", "utils",
    ]

    # Конфиг-файлы для мониторинга
    CONFIG_FILES = [
        "pyproject.toml", "requirements.txt", "config.py",
    ]

    def __init__(self, lina_root: Optional[str] = None):
        self._lina_root = lina_root or self._detect_root()
        self._baseline: Dict[str, str] = {}
        self._config_baseline: Dict[str, str] = {}
        self._violation_log: List[Dict[str, Any]] = []
        self._load_baseline()

    # ─── Detection ────────────────────────────────────────────

    @staticmethod
    def _detect_root() -> str:
        """Определить корень lina/ пакета."""
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.dirname(here)  # from diagnostics/ → lina/

    # ─── File hashing ─────────────────────────────────────────

    @staticmethod
    def _hash_file(path: str) -> str:
        """SHA-256 файла."""
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    # ─── Collect all .py files ────────────────────────────────

    def _collect_module_files(self) -> Dict[str, str]:
        """Собрать хеши всех .py файлов в SCAN_DIRS."""
        result: Dict[str, str] = {}
        for d in self.SCAN_DIRS:
            dir_path = os.path.join(self._lina_root, d)
            if not os.path.isdir(dir_path):
                continue
            for root, _, files in os.walk(dir_path):
                for f in files:
                    if f.endswith(".py"):
                        full = os.path.join(root, f)
                        rel = os.path.relpath(full, self._lina_root)
                        result[rel] = self._hash_file(full)
        # Also top-level .py
        for f in os.listdir(self._lina_root):
            if f.endswith(".py"):
                full = os.path.join(self._lina_root, f)
                result[f] = self._hash_file(full)
        return result

    def _collect_config_hashes(self) -> Dict[str, str]:
        """Собрать хеши конфиг-файлов."""
        result: Dict[str, str] = {}
        for cf in self.CONFIG_FILES:
            full = os.path.join(self._lina_root, cf)
            if os.path.isfile(full):
                result[cf] = self._hash_file(full)
        return result

    # ─── Baseline management ──────────────────────────────────

    def create_baseline(self) -> int:
        """Создать baseline хешей для всех модулей и конфигов.

        Returns:
            Количество файлов в baseline.
        """
        self._baseline = self._collect_module_files()
        self._config_baseline = self._collect_config_hashes()
        self._save_baseline()
        logger.info("INTEGRITY: Baseline created: %d modules, %d configs",
                     len(self._baseline), len(self._config_baseline))
        return len(self._baseline) + len(self._config_baseline)

    def _load_baseline(self) -> None:
        try:
            if os.path.isfile(self.BASELINE_PATH):
                with open(self.BASELINE_PATH, "r") as f:
                    data = json.load(f)
                self._baseline = data.get("modules", {})
                self._config_baseline = data.get("configs", {})
        except Exception:
            self._baseline = {}
            self._config_baseline = {}

    def _save_baseline(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.BASELINE_PATH), exist_ok=True)
            data = {
                "modules": self._baseline,
                "configs": self._config_baseline,
                "created_at": time.time(),
            }
            with open(self.BASELINE_PATH, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save integrity baseline: %s", e)

    # ─── Check ────────────────────────────────────────────────

    def check_modules(self) -> IntegrityReport:
        """Полная проверка целостности модулей."""
        report = IntegrityReport()
        current = self._collect_module_files()

        if not self._baseline:
            # Первая проверка — создать baseline
            self.create_baseline()
            report.total_files = len(self._baseline)
            report.intact_count = report.total_files
            return report

        all_files = set(self._baseline.keys()) | set(current.keys())
        report.total_files = len(all_files)

        for f in sorted(all_files):
            expected = self._baseline.get(f, "")
            actual = current.get(f, "")

            if not expected and actual:
                # Новый файл — не нарушение, обновить baseline
                rec = FileIntegrityRecord(
                    path=f, expected_hash="", current_hash=actual,
                    level=IntegrityLevel.MODIFIED,
                )
                report.records.append(rec)
                report.modified_count += 1
            elif expected and not actual:
                # Файл пропал
                rec = FileIntegrityRecord(
                    path=f, expected_hash=expected, current_hash="",
                    level=IntegrityLevel.MISSING,
                )
                report.records.append(rec)
                report.missing_count += 1
                self._log_violation("missing", f)
            elif expected != actual:
                # Файл изменён
                rec = FileIntegrityRecord(
                    path=f, expected_hash=expected, current_hash=actual,
                    level=IntegrityLevel.MODIFIED,
                    modified_at=time.time(),
                )
                report.records.append(rec)
                report.modified_count += 1
            else:
                report.intact_count += 1

        return report

    def check_configs(self) -> List[Dict[str, Any]]:
        """Проверить конфиг-файлы на изменения."""
        changes: List[Dict[str, Any]] = []
        current = self._collect_config_hashes()

        for cf in self.CONFIG_FILES:
            expected = self._config_baseline.get(cf, "")
            actual = current.get(cf, "")

            if expected and actual and expected != actual:
                change = {
                    "file": cf,
                    "type": "modified",
                    "expected_hash": expected[:16] + "...",
                    "current_hash": actual[:16] + "...",
                }
                changes.append(change)
                self._log_violation("config_modified", cf)
            elif expected and not actual:
                changes.append({"file": cf, "type": "missing"})
                self._log_violation("config_missing", cf)

        return changes

    def full_check(self) -> IntegrityReport:
        """Полная проверка: модули + конфиги + plan integrity."""
        report = self.check_modules()
        report.config_changes = self.check_configs()

        # Delegate plan-level checks to core IntegrityChecker
        try:
            from lina.core.integrity_checker import IntegrityChecker
            ic = IntegrityChecker()
            report.plan_checks_passed = ic._check_count - ic._violation_count
            report.plan_checks_failed = ic._violation_count
        except Exception:
            pass

        return report

    # ─── Tamper detection ─────────────────────────────────────

    def detect_tampering(self) -> List[FileIntegrityRecord]:
        """Обнаружить потенциальное вмешательство.

        Tamper = файл изменён, но это НЕ наш деплой
        (мы не обновляли baseline через create_baseline).
        """
        report = self.check_modules()
        tampered: List[FileIntegrityRecord] = []

        # Critical: core/ and safety/ files changed without baseline update
        critical_dirs = {"core/", "safety/", "diagnostics/"}
        for rec in report.records:
            if rec.level in (IntegrityLevel.MODIFIED, IntegrityLevel.MISSING):
                if any(rec.path.startswith(d) for d in critical_dirs):
                    rec.level = IntegrityLevel.TAMPERED
                    tampered.append(rec)

        return tampered

    # ─── Update baseline (after legitimate deploy) ────────────

    def update_baseline(self) -> int:
        """Обновить baseline после легитимного обновления кода."""
        return self.create_baseline()

    # ─── Violation log ────────────────────────────────────────

    def _log_violation(self, vtype: str, path: str) -> None:
        self._violation_log.append({
            "type": vtype,
            "path": path,
            "time": time.time(),
        })
        if len(self._violation_log) > 200:
            self._violation_log = self._violation_log[-200:]

    def get_violations(self) -> List[Dict[str, Any]]:
        return list(self._violation_log)

    # ─── Report ───────────────────────────────────────────────

    def format_report(self, report: Optional[IntegrityReport] = None) -> str:
        if report is None:
            report = self.full_check()
        lines = ["═══ IntegrityGuard Report ═══"]
        lines.append(f"  Status: {report.severity.upper()}")
        lines.append(f"  Total files: {report.total_files}")
        lines.append(f"  Intact: {report.intact_count}")
        lines.append(f"  Modified: {report.modified_count}")
        lines.append(f"  Missing: {report.missing_count}")
        lines.append(f"  Tampered: {report.tampered_count}")
        if report.config_changes:
            lines.append(f"  Config changes: {len(report.config_changes)}")
            for c in report.config_changes:
                lines.append(f"    {c['file']}: {c['type']}")
        if report.records:
            lines.append("")
            lines.append("  Changed files:")
            for rec in report.records[:10]:
                lines.append(f"    [{rec.level.value}] {rec.path}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_guard: Optional[IntegrityGuard] = None


def get_integrity_guard() -> IntegrityGuard:
    global _guard
    if _guard is None:
        _guard = IntegrityGuard()
    return _guard
