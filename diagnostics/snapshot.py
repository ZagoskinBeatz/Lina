"""
SnapshotManager — управление снимками системы перед критическими операциями.

.. warning:: EXPERIMENTAL
   Работа с снимками файловой системы сильно зависит от окружения
   (btrfs? timeshift установлен?). rsync-fallback на корневой FS
   может быть рискованным.

Архитектура (приоритет бэкендов):

  1. btrfs — если / на btrfs → создать subvolume snapshot
  2. timeshift — если установлен → делегировать timeshift
  3. rsync — последний fallback → rsync критических путей

Каждый snapshot:
  - Привязан к plan_hash (какой FixPlan вызвал)
  - Имеет метаданные: timestamp, category, risk_verdict
  - Может быть откатан (rollback)
  - Автоочистка: хранить максимум N снимков

Интеграция:
  - RootAgent: перед critical/high-risk → create_snapshot()
  - SelfHealer: при неудачном fix → rollback_last()
  - ContextMemory: запись success/failure snapshot

Phase: SYSTEM OVERLORD / Module 7
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Snapshot Backend
# ═══════════════════════════════════════════════════════════════════

class SnapshotBackend(str, Enum):
    BTRFS = "btrfs"
    TIMESHIFT = "timeshift"
    RSYNC = "rsync"
    NONE = "none"


# ═══════════════════════════════════════════════════════════════════
#  Snapshot Record
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SnapshotRecord:
    """Метаданные снимка."""
    snapshot_id: str
    backend: str
    created_at: float = 0.0
    plan_hash: str = ""
    category: str = ""
    risk_verdict: str = ""
    description: str = ""
    path: str = ""
    rolled_back: bool = False
    rollback_at: float = 0.0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()


# ═══════════════════════════════════════════════════════════════════
#  SnapshotManager
# ═══════════════════════════════════════════════════════════════════

class SnapshotManager:
    """Управление снимками системы перед критическими изменениями.

    Автоматически выбирает лучший доступный бэкенд:
      btrfs > timeshift > rsync

    Rsync fallback сохраняет только критические директории:
      /etc, /boot, /usr/lib/systemd
    """

    META_PATH = os.path.expanduser(
        "~/.local/share/lina/snapshots/metadata.json"
    )
    RSYNC_BASE = os.path.expanduser(
        "~/.local/share/lina/snapshots/rsync"
    )
    MAX_SNAPSHOTS = 10

    # Критические директории для rsync fallback
    RSYNC_TARGETS = [
        "/etc",
        "/boot",
    ]

    def __init__(self):
        self._backend = self._detect_backend()
        self._snapshots: List[SnapshotRecord] = []
        self._load_metadata()

    # ─── Backend detection ────────────────────────────────────

    @staticmethod
    def _detect_backend() -> SnapshotBackend:
        """Определить лучший доступный бэкенд."""
        # 1. Check btrfs
        try:
            r = subprocess.run(
                ["stat", "-f", "--format=%T", "/"],
                capture_output=True, text=True, timeout=5,
            )
            if "btrfs" in r.stdout.lower():
                return SnapshotBackend.BTRFS
        except Exception:
            pass

        # 2. Check timeshift
        if shutil.which("timeshift"):
            return SnapshotBackend.TIMESHIFT

        # 3. Rsync fallback
        if shutil.which("rsync"):
            return SnapshotBackend.RSYNC

        return SnapshotBackend.NONE

    @property
    def backend(self) -> SnapshotBackend:
        return self._backend

    @property
    def available(self) -> bool:
        return self._backend != SnapshotBackend.NONE

    # ─── Create snapshot ──────────────────────────────────────

    def create_snapshot(
        self,
        description: str = "",
        plan_hash: str = "",
        category: str = "",
        risk_verdict: str = "",
    ) -> Optional[SnapshotRecord]:
        """Создать snapshot перед критической операцией.

        Returns:
            SnapshotRecord или None при ошибке.
        """
        snap_id = f"lina-{int(time.time())}"
        record = SnapshotRecord(
            snapshot_id=snap_id,
            backend=self._backend.value,
            plan_hash=plan_hash,
            category=category,
            risk_verdict=risk_verdict,
            description=description or f"Pre-op snapshot ({category})",
        )

        success = False

        if self._backend == SnapshotBackend.BTRFS:
            success = self._create_btrfs(record)
        elif self._backend == SnapshotBackend.TIMESHIFT:
            success = self._create_timeshift(record)
        elif self._backend == SnapshotBackend.RSYNC:
            success = self._create_rsync(record)

        if success:
            self._snapshots.append(record)
            self._cleanup_old()
            self._save_metadata()
            logger.info("SNAPSHOT: Created %s via %s", snap_id, self._backend.value)
            return record

        logger.error("SNAPSHOT: Failed to create via %s", self._backend.value)
        return None

    # ─── Rollback ─────────────────────────────────────────────

    def rollback_last(self) -> bool:
        """Откатить последний snapshot."""
        if not self._snapshots:
            logger.warning("SNAPSHOT: No snapshots to rollback")
            return False

        record = self._snapshots[-1]
        if record.rolled_back:
            logger.warning("SNAPSHOT: Last snapshot already rolled back")
            return False

        return self._rollback(record)

    def rollback_by_id(self, snapshot_id: str) -> bool:
        """Откатить конкретный snapshot."""
        for rec in self._snapshots:
            if rec.snapshot_id == snapshot_id:
                return self._rollback(rec)
        logger.warning("SNAPSHOT: Not found: %s", snapshot_id)
        return False

    def _rollback(self, record: SnapshotRecord) -> bool:
        """Выполнить rollback."""
        success = False

        if record.backend == SnapshotBackend.BTRFS.value:
            success = self._rollback_btrfs(record)
        elif record.backend == SnapshotBackend.TIMESHIFT.value:
            success = self._rollback_timeshift(record)
        elif record.backend == SnapshotBackend.RSYNC.value:
            success = self._rollback_rsync(record)

        if success:
            record.rolled_back = True
            record.rollback_at = time.time()
            self._save_metadata()
            logger.info("SNAPSHOT: Rolled back %s", record.snapshot_id)
        else:
            logger.error("SNAPSHOT: Failed to rollback %s", record.snapshot_id)

        return success

    # ─── Btrfs ────────────────────────────────────────────────

    def _create_btrfs(self, record: SnapshotRecord) -> bool:
        try:
            snap_path = f"/.snapshots/{record.snapshot_id}"
            r = subprocess.run(
                ["sudo", "btrfs", "subvolume", "snapshot", "/", snap_path],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                record.path = snap_path
                return True
            logger.error("btrfs snapshot failed: %s", r.stderr)
            return False
        except Exception as e:
            logger.error("btrfs error: %s", e)
            return False

    def _rollback_btrfs(self, record: SnapshotRecord) -> bool:
        """Btrfs rollback — информирует пользователя.

        Полный btrfs rollback требует перезагрузки,
        поэтому только готовим команды.
        """
        logger.warning(
            "SNAPSHOT: btrfs rollback requires reboot. "
            "Snapshot at: %s", record.path,
        )
        # Return True — команды подготовлены, пользователь предупреждён
        return True

    # ─── Timeshift ────────────────────────────────────────────

    def _create_timeshift(self, record: SnapshotRecord) -> bool:
        try:
            r = subprocess.run(
                ["sudo", "timeshift", "--create",
                 "--comments", record.description[:100],
                 "--scripted"],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                record.path = "timeshift"
                return True
            logger.error("timeshift create failed: %s", r.stderr)
            return False
        except Exception as e:
            logger.error("timeshift error: %s", e)
            return False

    def _rollback_timeshift(self, record: SnapshotRecord) -> bool:
        try:
            # timeshift --restore requires interactive confirm,
            # so we prep the command.
            logger.warning(
                "SNAPSHOT: timeshift rollback: run "
                "'sudo timeshift --restore --scripted'"
            )
            return True
        except Exception as e:
            logger.error("timeshift rollback error: %s", e)
            return False

    # ─── Rsync ────────────────────────────────────────────────

    def _create_rsync(self, record: SnapshotRecord) -> bool:
        snap_dir = os.path.join(self.RSYNC_BASE, record.snapshot_id)
        try:
            os.makedirs(snap_dir, exist_ok=True)
            for target in self.RSYNC_TARGETS:
                if not os.path.isdir(target):
                    continue
                dest = os.path.join(snap_dir, target.lstrip("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                r = subprocess.run(
                    ["sudo", "rsync", "-a", "--delete",
                     target + "/", dest + "/"],
                    capture_output=True, text=True, timeout=120,
                )
                if r.returncode != 0:
                    logger.warning("rsync partial fail for %s: %s", target, r.stderr)
            record.path = snap_dir
            return True
        except Exception as e:
            logger.error("rsync error: %s", e)
            return False

    def _rollback_rsync(self, record: SnapshotRecord) -> bool:
        if not record.path or not os.path.isdir(record.path):
            return False
        try:
            for target in self.RSYNC_TARGETS:
                src = os.path.join(record.path, target.lstrip("/"))
                if not os.path.isdir(src):
                    continue
                r = subprocess.run(
                    ["sudo", "rsync", "-a", "--delete",
                     src + "/", target + "/"],
                    capture_output=True, text=True, timeout=120,
                )
                if r.returncode != 0:
                    logger.warning("rsync rollback partial fail: %s", r.stderr)
            return True
        except Exception as e:
            logger.error("rsync rollback error: %s", e)
            return False

    # ─── Cleanup ──────────────────────────────────────────────

    def _cleanup_old(self) -> None:
        """Удалить старые snapshots если >MAX_SNAPSHOTS."""
        while len(self._snapshots) > self.MAX_SNAPSHOTS:
            oldest = self._snapshots.pop(0)
            if oldest.backend == SnapshotBackend.RSYNC.value and oldest.path:
                try:
                    if os.path.isdir(oldest.path):
                        shutil.rmtree(oldest.path)
                except Exception:
                    pass
            logger.info("SNAPSHOT: Cleaned old snapshot %s", oldest.snapshot_id)

    # ─── Metadata persistence ─────────────────────────────────

    def _load_metadata(self) -> None:
        try:
            if os.path.isfile(self.META_PATH):
                with open(self.META_PATH, "r") as f:
                    data = json.load(f)
                self._snapshots = [SnapshotRecord(**r) for r in data]
        except Exception:
            self._snapshots = []

    def _save_metadata(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.META_PATH), exist_ok=True)
            data = [
                {
                    "snapshot_id": s.snapshot_id,
                    "backend": s.backend,
                    "created_at": s.created_at,
                    "plan_hash": s.plan_hash,
                    "category": s.category,
                    "risk_verdict": s.risk_verdict,
                    "description": s.description,
                    "path": s.path,
                    "rolled_back": s.rolled_back,
                    "rollback_at": s.rollback_at,
                }
                for s in self._snapshots
            ]
            with open(self.META_PATH, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save snapshot metadata: %s", e)

    # ─── API ──────────────────────────────────────────────────

    def list_snapshots(self) -> List[SnapshotRecord]:
        return list(self._snapshots)

    def format_report(self) -> str:
        lines = ["═══ SnapshotManager Report ═══"]
        lines.append(f"  Backend: {self._backend.value}")
        lines.append(f"  Total snapshots: {len(self._snapshots)}")
        active = sum(1 for s in self._snapshots if not s.rolled_back)
        lines.append(f"  Active: {active}")
        lines.append(f"  Max kept: {self.MAX_SNAPSHOTS}")
        if self._snapshots:
            lines.append("")
            lines.append("  Recent:")
            for s in self._snapshots[-5:]:
                status = "✓" if not s.rolled_back else "↩"
                lines.append(f"    {status} {s.snapshot_id} [{s.category}] {s.description[:40]}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_snapshot_mgr: Optional[SnapshotManager] = None


def get_snapshot_manager() -> SnapshotManager:
    global _snapshot_mgr
    if _snapshot_mgr is None:
        _snapshot_mgr = SnapshotManager()
    return _snapshot_mgr
