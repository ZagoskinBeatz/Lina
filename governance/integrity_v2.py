"""
IntegrityCheckV2 — проверка целостности файлов Lina при запуске.

Алгоритм:
  1. При первом запуске — генерация SHA256 манифеста
  2. При каждом запуске Runtime Mode — проверка манифеста
  3. При несоответствии → SAFE_MODE

Манифест: ~/.local/share/lina/integrity_manifest.json
Формат: {"files": {"path": {"sha256": "...", "size": N, "mtime": T}}}

Phase: GOVERNANCE LAYER / Module 9
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ─── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class FileRecord:
    """Запись о файле в манифесте."""
    path: str
    sha256: str
    size: int
    mtime: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sha256": self.sha256,
            "size": self.size,
            "mtime": self.mtime,
        }


@dataclass
class IntegrityViolation:
    """Нарушение целостности."""
    path: str
    violation_type: str   # modified, deleted, new, size_mismatch
    expected_hash: str = ""
    actual_hash: str = ""
    expected_size: int = 0
    actual_size: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "type": self.violation_type,
            "expected_hash": self.expected_hash[:16] if self.expected_hash else "",
            "actual_hash": self.actual_hash[:16] if self.actual_hash else "",
        }


@dataclass
class IntegrityResult:
    """Результат проверки целостности."""
    passed: bool = True
    violations: List[IntegrityViolation] = field(default_factory=list)
    total_files: int = 0
    checked_files: int = 0
    duration: float = 0.0
    manifest_exists: bool = True
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
            "total_files": self.total_files,
            "checked_files": self.checked_files,
            "duration": round(self.duration, 2),
            "manifest_exists": self.manifest_exists,
        }

    def summary(self) -> str:
        if self.passed:
            return f"Integrity OK: {self.checked_files}/{self.total_files} files checked ({self.duration:.1f}s)"
        return f"Integrity FAILED: {len(self.violations)} violations in {self.checked_files} files"


# ─── IntegrityCheckV2 ────────────────────────────────────────────────────────

class IntegrityCheckV2:
    """
    Проверка целостности файлов Lina.

    Пример:
        checker = get_integrity_checker()
        result = checker.verify()
        if not result.passed:
            # → transition to SAFE_MODE
    """

    def __init__(self, *,
                 lina_root: Optional[str] = None,
                 manifest_path: Optional[str] = None) -> None:
        self._lina_root = Path(lina_root) if lina_root else self._detect_root()
        self._manifest_path = Path(manifest_path) if manifest_path else self._default_manifest_path()
        self._manifest: Dict[str, FileRecord] = {}
        self._exclude_patterns: Set[str] = {
            "__pycache__", ".pyc", ".pyo", ".log", ".tmp",
            "cache/", "logs/", "chroma_db/", ".git",
            "telemetry.json", "integrity_manifest.json",
        }
        self._load_manifest()

    # ── Generate ─────────────────────────────────────────

    def generate_manifest(self) -> int:
        """
        Генерация манифеста из текущего состояния файлов.
        Returns: количество файлов в манифесте.
        """
        self._manifest.clear()
        count = 0

        for py_file in self._lina_root.rglob("*.py"):
            rel = str(py_file.relative_to(self._lina_root))
            if self._should_exclude(rel):
                continue
            try:
                record = self._hash_file(py_file, rel)
                self._manifest[rel] = record
                count += 1
            except Exception as e:
                logger.warning("IntegrityV2: can't hash %s: %s", rel, e)

        self._save_manifest()
        logger.info("IntegrityV2: generated manifest with %d files", count)
        return count

    # ── Verify ───────────────────────────────────────────

    def verify(self) -> IntegrityResult:
        """
        Проверить целостность по манифесту.
        Returns: IntegrityResult
        """
        t0 = time.monotonic()

        if not self._manifest:
            return IntegrityResult(
                passed=True, manifest_exists=False,
                duration=time.monotonic() - t0,
            )

        violations: List[IntegrityViolation] = []
        checked = 0

        for rel_path, expected in self._manifest.items():
            full_path = self._lina_root / rel_path
            checked += 1

            if not full_path.exists():
                violations.append(IntegrityViolation(
                    path=rel_path, violation_type="deleted",
                    expected_hash=expected.sha256,
                ))
                continue

            try:
                actual = self._hash_file(full_path, rel_path)

                if actual.sha256 != expected.sha256:
                    violations.append(IntegrityViolation(
                        path=rel_path, violation_type="modified",
                        expected_hash=expected.sha256,
                        actual_hash=actual.sha256,
                        expected_size=expected.size,
                        actual_size=actual.size,
                    ))
                elif actual.size != expected.size:
                    violations.append(IntegrityViolation(
                        path=rel_path, violation_type="size_mismatch",
                        expected_hash=expected.sha256,
                        actual_hash=actual.sha256,
                        expected_size=expected.size,
                        actual_size=actual.size,
                    ))
            except Exception as e:
                violations.append(IntegrityViolation(
                    path=rel_path, violation_type="error",
                ))

        # Check for new files
        for py_file in self._lina_root.rglob("*.py"):
            rel = str(py_file.relative_to(self._lina_root))
            if self._should_exclude(rel):
                continue
            if rel not in self._manifest:
                violations.append(IntegrityViolation(
                    path=rel, violation_type="new",
                ))

        duration = time.monotonic() - t0
        passed = len(violations) == 0

        result = IntegrityResult(
            passed=passed, violations=violations,
            total_files=len(self._manifest), checked_files=checked,
            duration=duration, manifest_exists=True,
        )

        if not passed:
            logger.warning("IntegrityV2: %d violations detected", len(violations))
        else:
            logger.info("IntegrityV2: OK (%d files, %.2fs)", checked, duration)

        return result

    # ── Update ───────────────────────────────────────────

    def update_file(self, rel_path: str) -> bool:
        """Обновить запись одного файла в манифесте."""
        full_path = self._lina_root / rel_path
        if not full_path.exists():
            self._manifest.pop(rel_path, None)
            self._save_manifest()
            return True
        try:
            self._manifest[rel_path] = self._hash_file(full_path, rel_path)
            self._save_manifest()
            return True
        except Exception:
            return False

    # ── Internal ─────────────────────────────────────────

    @staticmethod
    def _hash_file(path: Path, rel_path: str) -> FileRecord:
        """Хешировать файл."""
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        stat = path.stat()
        return FileRecord(
            path=rel_path, sha256=sha,
            size=stat.st_size, mtime=stat.st_mtime,
        )

    def _should_exclude(self, rel_path: str) -> bool:
        """Проверить исключения."""
        for pattern in self._exclude_patterns:
            if pattern in rel_path:
                return True
        return False

    def _load_manifest(self) -> None:
        """Загрузить манифест из JSON."""
        if not self._manifest_path.exists():
            return
        try:
            data = json.loads(self._manifest_path.read_text())
            for rel_path, info in data.get("files", {}).items():
                self._manifest[rel_path] = FileRecord(
                    path=rel_path,
                    sha256=info["sha256"],
                    size=info["size"],
                    mtime=info.get("mtime", 0),
                )
            logger.debug("IntegrityV2: loaded manifest with %d files",
                         len(self._manifest))
        except Exception as e:
            logger.error("IntegrityV2: failed to load manifest: %s", e)

    def _save_manifest(self) -> None:
        """Сохранить манифест в JSON."""
        try:
            self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "generated": time.time(),
                "lina_root": str(self._lina_root),
                "files": {
                    path: rec.to_dict()
                    for path, rec in sorted(self._manifest.items())
                },
            }
            self._manifest_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False)
            )
        except Exception as e:
            logger.error("IntegrityV2: failed to save manifest: %s", e)

    def _detect_root(self) -> Path:
        """Определить корень Lina."""
        # Relative to this file
        here = Path(__file__).resolve().parent  # governance/
        return here.parent  # lina/

    @staticmethod
    def _default_manifest_path() -> Path:
        """Путь по умолчанию для манифеста."""
        share_dir = os.environ.get(
            "XDG_DATA_HOME",
            str(Path.home() / ".local" / "share"),
        )
        return Path(share_dir) / "lina" / "integrity_manifest.json"

    # ── Accessors ────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            "manifest_files": len(self._manifest),
            "lina_root": str(self._lina_root),
            "manifest_path": str(self._manifest_path),
        }


# ─── Singleton ─────────────────────────────────────────────────────────────────

_checker: Optional[IntegrityCheckV2] = None

def get_integrity_checker() -> IntegrityCheckV2:
    """Получить единственный экземпляр IntegrityCheckV2."""
    global _checker
    if _checker is None:
        _checker = IntegrityCheckV2()
    return _checker
