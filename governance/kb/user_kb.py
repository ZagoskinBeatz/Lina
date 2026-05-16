"""
UserKB — пользовательская база знаний (read-write).

Обучается на success/fail пользовательских действий.
Хранение: ~/.local/share/lina/user_kb.json

Phase: GOVERNANCE LAYER / Knowledge Base
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .kb_entry import KBEntry, KBSearchResult

logger = logging.getLogger(__name__)


class UserKB:
    """
    Пользовательская база знаний.

    Учится на опыте: каждый success/fail обновляет confidence.
    Записи сохраняются в JSON.

    Пример:
        ukb = get_user_kb()
        ukb.add_entry(KBEntry(id="user_fix_net_1", domain="network", ...))
        ukb.record_outcome("user_fix_net_1", success=True)
    """

    def __init__(self, data_path: Optional[str] = None) -> None:
        self._entries: Dict[str, KBEntry] = {}
        self._by_domain: Dict[str, List[str]] = {}
        self._by_tag: Dict[str, Set[str]] = {}
        self._by_fingerprint: Dict[str, str] = {}
        self._data_path = Path(data_path) if data_path else self._default_path()
        self._dirty = False
        self._load()

    # ── Write ────────────────────────────────────────────

    def add_entry(self, entry: KBEntry) -> None:
        """Добавить или обновить запись."""
        entry.source = "user"
        entry.updated = time.time()
        self._entries[entry.id] = entry
        self._index(entry)
        self._dirty = True

    def remove_entry(self, entry_id: str) -> bool:
        """Удалить запись."""
        if entry_id in self._entries:
            entry = self._entries.pop(entry_id)
            self._deindex(entry)
            self._dirty = True
            return True
        return False

    def record_outcome(self, entry_id: str, success: bool) -> bool:
        """Записать результат применения."""
        entry = self._entries.get(entry_id)
        if not entry:
            return False
        entry.record_attempt(success)
        # Adjust confidence based on success rate
        if entry.total_attempts >= 3:
            entry.confidence = 0.5 + (entry.success_rate * 0.5)
        self._dirty = True
        return True

    def learn_from_action(self, *,
                          domain: str,
                          tags: List[str],
                          symptom_ru: str,
                          actions: List[str],
                          action_params: Optional[List[Dict[str, str]]] = None,
                          success: bool = True,
                          fingerprint: str = "") -> KBEntry:
        """
        Автоматически создать/обновить запись из результата действия.
        """
        # Check if similar entry exists
        existing = self._find_similar(domain, tags)
        if existing:
            existing.record_attempt(success)
            if fingerprint and fingerprint not in existing.fingerprints:
                existing.fingerprints.append(fingerprint)
            self._dirty = True
            return existing

        # Create new entry
        entry_id = f"user_{domain}_{int(time.time())}_{len(self._entries)}"
        entry = KBEntry(
            id=entry_id, domain=domain, tags=tags,
            symptom_ru=symptom_ru, actions=actions,
            action_params=action_params or [],
            source="user", confidence=0.6 if success else 0.3,
            success_rate=1.0 if success else 0.0,
            total_attempts=1, total_successes=1 if success else 0,
            fingerprints=[fingerprint] if fingerprint else [],
        )
        self.add_entry(entry)
        logger.info("UserKB: learned new entry %s domain=%s", entry_id, domain)
        return entry

    # ── Query ────────────────────────────────────────────

    def get(self, entry_id: str) -> Optional[KBEntry]:
        return self._entries.get(entry_id)

    def search(self, *,
               domain: str = "",
               tags: Optional[List[str]] = None,
               fingerprint: str = "",
               limit: int = 10) -> List[KBSearchResult]:
        """Поиск в UserKB (аналогично LocalKB)."""
        results: List[KBSearchResult] = []

        # By fingerprint
        if fingerprint and fingerprint in self._by_fingerprint:
            eid = self._by_fingerprint[fingerprint]
            entry = self._entries.get(eid)
            if entry:
                return [KBSearchResult(entry=entry, score=1.0,
                                        match_type="exact", source="user")]

        # By tags + domain
        query_tags = set(tags or [])
        candidates: List[KBEntry] = []

        if domain and domain in self._by_domain:
            for eid in self._by_domain[domain]:
                e = self._entries.get(eid)
                if e:
                    candidates.append(e)
        elif query_tags:
            cids: Set[str] = set()
            for tag in query_tags:
                if tag in self._by_tag:
                    cids.update(self._by_tag[tag])
            for eid in cids:
                e = self._entries.get(eid)
                if e:
                    candidates.append(e)
        else:
            candidates = list(self._entries.values())

        for entry in candidates:
            entry_tags = set(entry.tags)
            if query_tags and entry_tags:
                union = query_tags | entry_tags
                jaccard = len(query_tags & entry_tags) / len(union) if union else 0
            else:
                jaccard = 0.3

            score = jaccard * 0.5 + entry.confidence * 0.3 + entry.success_rate * 0.2
            if domain and entry.domain == domain:
                score += 0.05

            results.append(KBSearchResult(
                entry=entry, score=score,
                match_type="tags" if query_tags else "domain",
                source="user",
            ))

        results.sort(key=lambda r: -r.score)
        return results[:limit]

    def count(self) -> int:
        return len(self._entries)

    # ── Persistence ──────────────────────────────────────

    def save(self) -> bool:
        """Сохранить в JSON."""
        if not self._dirty:
            return True
        try:
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "updated": time.time(),
                "entries": [e.to_dict() for e in self._entries.values()],
            }
            self._data_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self._dirty = False
            logger.debug("UserKB: saved %d entries to %s",
                         len(self._entries), self._data_path)
            return True
        except Exception as e:
            logger.error("UserKB: save error: %s", e)
            return False

    def _load(self) -> None:
        """Загрузить из JSON."""
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text(encoding="utf-8"))
            for entry_data in data.get("entries", []):
                entry = KBEntry(**entry_data)
                self._entries[entry.id] = entry
                self._index(entry)
            logger.info("UserKB: loaded %d entries from %s",
                        len(self._entries), self._data_path)
        except Exception as e:
            logger.error("UserKB: load error: %s", e)

    # ── Index ────────────────────────────────────────────

    def _index(self, entry: KBEntry) -> None:
        if entry.domain not in self._by_domain:
            self._by_domain[entry.domain] = []
        if entry.id not in self._by_domain[entry.domain]:
            self._by_domain[entry.domain].append(entry.id)

        for tag in entry.tags:
            if tag not in self._by_tag:
                self._by_tag[tag] = set()
            self._by_tag[tag].add(entry.id)

        for fp in entry.fingerprints:
            self._by_fingerprint[fp] = entry.id

    def _deindex(self, entry: KBEntry) -> None:
        if entry.domain in self._by_domain:
            self._by_domain[entry.domain] = [
                e for e in self._by_domain[entry.domain] if e != entry.id
            ]
        for tag in entry.tags:
            if tag in self._by_tag:
                self._by_tag[tag].discard(entry.id)
        for fp in entry.fingerprints:
            if self._by_fingerprint.get(fp) == entry.id:
                del self._by_fingerprint[fp]

    def _find_similar(self, domain: str, tags: List[str]) -> Optional[KBEntry]:
        """Найти похожую запись (Jaccard > 0.6)."""
        query_tags = set(tags)
        for eid in self._by_domain.get(domain, []):
            entry = self._entries.get(eid)
            if not entry:
                continue
            entry_tags = set(entry.tags)
            if not entry_tags:
                continue
            union = query_tags | entry_tags
            jaccard = len(query_tags & entry_tags) / len(union) if union else 0
            if jaccard >= 0.6:
                return entry
        return None

    @staticmethod
    def _default_path() -> Path:
        share_dir = os.environ.get(
            "XDG_DATA_HOME",
            str(Path.home() / ".local" / "share"),
        )
        return Path(share_dir) / "lina" / "user_kb.json"

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_entries": len(self._entries),
            "domains": {d: len(ids) for d, ids in self._by_domain.items()},
            "path": str(self._data_path),
            "dirty": self._dirty,
        }


# ─── Singleton ─────────────────────────────────────────────────────────────────

_user_kb: Optional[UserKB] = None

def get_user_kb() -> UserKB:
    """Получить единственный экземпляр UserKB."""
    global _user_kb
    if _user_kb is None:
        _user_kb = UserKB()
    return _user_kb
