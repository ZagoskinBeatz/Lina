"""
LocalKB — встроенная база знаний (read-only).

Содержит проверенные решения для типовых проблем Linux.
Обновляется только с новыми версиями Lina.

Phase: GOVERNANCE LAYER / Knowledge Base
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from .kb_entry import KBEntry, KBSearchResult

logger = logging.getLogger(__name__)


class LocalKB:
    """
    Встроенная (read-only) база знаний.

    Пример:
        kb = get_local_kb()
        results = kb.search(domain="network", tags=["dns", "timeout"])
    """

    def __init__(self) -> None:
        self._entries: Dict[str, KBEntry] = {}
        self._by_domain: Dict[str, List[str]] = {}
        self._by_tag: Dict[str, Set[str]] = {}
        self._by_fingerprint: Dict[str, str] = {}
        self._populate()

    # ── Query ────────────────────────────────────────────

    def get(self, entry_id: str) -> Optional[KBEntry]:
        """Получить запись по ID."""
        return self._entries.get(entry_id)

    def search(self, *,
               domain: str = "",
               tags: Optional[List[str]] = None,
               fingerprint: str = "",
               limit: int = 10) -> List[KBSearchResult]:
        """
        Поиск в LocalKB.

        Приоритет:
          1. fingerprint (точное)
          2. tags+domain (Jaccard)
          3. domain only
        """
        results: List[KBSearchResult] = []

        # 1. By fingerprint
        if fingerprint and fingerprint in self._by_fingerprint:
            eid = self._by_fingerprint[fingerprint]
            entry = self._entries.get(eid)
            if entry:
                return [KBSearchResult(entry=entry, score=1.0,
                                       match_type="exact", source="local")]

        # 2. By tags + domain
        query_tags = set(tags or [])
        candidates: List[KBEntry] = []

        if domain and domain in self._by_domain:
            for eid in self._by_domain[domain]:
                entry = self._entries.get(eid)
                if entry:
                    candidates.append(entry)
        elif query_tags:
            # Union of entries that match any tag
            candidate_ids: Set[str] = set()
            for tag in query_tags:
                if tag in self._by_tag:
                    candidate_ids.update(self._by_tag[tag])
            for eid in candidate_ids:
                entry = self._entries.get(eid)
                if entry:
                    candidates.append(entry)
        else:
            candidates = list(self._entries.values())

        # Score by Jaccard
        for entry in candidates:
            entry_tags = set(entry.tags)
            if query_tags and entry_tags:
                union = query_tags | entry_tags
                jaccard = len(query_tags & entry_tags) / len(union) if union else 0
            elif query_tags:
                jaccard = 0.0
            else:
                jaccard = 0.5  # no tags → neutral score

            # Boost by confidence and domain match
            score = jaccard * 0.6 + entry.confidence * 0.3
            if domain and entry.domain == domain:
                score += 0.1

            results.append(KBSearchResult(
                entry=entry, score=score,
                match_type="tags" if query_tags else "domain",
                source="local",
            ))

        results.sort(key=lambda r: -r.score)
        return results[:limit]

    def list_domains(self) -> List[str]:
        """Список доменов."""
        return sorted(self._by_domain.keys())

    def count(self) -> int:
        """Количество записей."""
        return len(self._entries)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_entries": len(self._entries),
            "domains": {d: len(ids) for d, ids in self._by_domain.items()},
        }

    # ── Index ────────────────────────────────────────────

    def _index(self, entry: KBEntry) -> None:
        """Проиндексировать запись."""
        self._entries[entry.id] = entry

        if entry.domain not in self._by_domain:
            self._by_domain[entry.domain] = []
        self._by_domain[entry.domain].append(entry.id)

        for tag in entry.tags:
            if tag not in self._by_tag:
                self._by_tag[tag] = set()
            self._by_tag[tag].add(entry.id)

        for fp in entry.fingerprints:
            self._by_fingerprint[fp] = entry.id

    # ── Builtin Entries ──────────────────────────────────

    def _populate(self) -> None:
        """Заполнить встроенными записями."""
        entries = [
            # ═══ NETWORK ═══════════════════════════════════
            KBEntry(
                id="kb_net_dns_fail",
                domain="network",
                tags=["dns", "resolution", "failure", "SERVFAIL", "NXDOMAIN"],
                symptom="DNS resolution failure",
                symptom_ru="Не работает разрешение DNS",
                diagnosis="systemd-resolved or DNS server issue",
                diagnosis_ru="Проблема с systemd-resolved или DNS сервером",
                solution_steps=[
                    "Restart systemd-resolved",
                    "Flush DNS cache",
                    "Check /etc/resolv.conf",
                    "Try alternative DNS (1.1.1.1, 8.8.8.8)",
                ],
                actions=["net_restart_resolved", "net_flush_dns"],
                confidence=0.9, risk_level="low", verified=True,
            ),
            KBEntry(
                id="kb_net_nm_restart",
                domain="network",
                tags=["networkmanager", "disconnect", "wifi", "connection"],
                symptom="Network connectivity lost",
                symptom_ru="Потеря сетевого подключения",
                diagnosis="NetworkManager needs restart",
                diagnosis_ru="NetworkManager нуждается в перезапуске",
                solution_steps=["Restart NetworkManager service"],
                actions=["net_restart_nm"],
                confidence=0.8, risk_level="medium", verified=True,
            ),
            KBEntry(
                id="kb_net_no_route",
                domain="network",
                tags=["routing", "unreachable", "gateway", "no_route"],
                symptom="No route to host / network unreachable",
                symptom_ru="Нет маршрута к хосту / сеть недоступна",
                diagnosis="Default gateway missing or routing table broken",
                diagnosis_ru="Отсутствует шлюз по умолчанию или сломана таблица маршрутизации",
                solution_steps=[
                    "Check default gateway: ip route show default",
                    "Restart NetworkManager",
                    "Check physical connection",
                ],
                actions=["net_check_gw", "net_restart_nm"],
                confidence=0.75, risk_level="low", verified=True,
            ),
            KBEntry(
                id="kb_net_wifi_disconnect",
                domain="network",
                tags=["wifi", "disconnect", "wireless", "scan"],
                symptom="WiFi keeps disconnecting",
                symptom_ru="WiFi постоянно отключается",
                diagnosis="WiFi adapter or driver issue",
                diagnosis_ru="Проблема адаптера или драйвера WiFi",
                solution_steps=[
                    "Rescan WiFi networks",
                    "Restart NetworkManager",
                    "Check dmesg for driver errors",
                ],
                actions=["net_wifi_scan", "net_restart_nm"],
                confidence=0.7, risk_level="low", verified=True,
            ),

            # ═══ PACKAGE ═══════════════════════════════════
            KBEntry(
                id="kb_pkg_db_locked",
                domain="package",
                tags=["lock", "database", "pacman", "locked"],
                symptom="Package database locked",
                symptom_ru="БД pacman заблокирована",
                diagnosis="Stale lock file from previous pacman process",
                diagnosis_ru="Остаточный lock-файл от предыдущего процесса pacman",
                solution_steps=[
                    "Check if pacman is running: ps aux | grep pacman",
                    "Remove lock: rm /var/lib/pacman/db.lck",
                ],
                actions=[],  # Manual action — too risky for auto
                confidence=0.95, risk_level="low", verified=True,
            ),
            KBEntry(
                id="kb_pkg_gpg_key",
                domain="package",
                tags=["gpg", "keyring", "signature", "trust"],
                symptom="GPG key/signature error during package install",
                symptom_ru="Ошибка GPG ключа/подписи при установке пакета",
                diagnosis="Outdated keyring or missing GPG keys",
                diagnosis_ru="Устаревший keyring или отсутствующие GPG ключи",
                solution_steps=[
                    "Update keyring: pacman -S archlinux-keyring",
                    "Initialize: pacman-key --init",
                    "Populate: pacman-key --populate archlinux",
                ],
                actions=["pkg_install"],
                action_params=[{"package": "archlinux-keyring"}],
                confidence=0.85, risk_level="medium", verified=True,
            ),
            KBEntry(
                id="kb_pkg_conflict",
                domain="package",
                tags=["conflict", "filesystem", "file_exists"],
                symptom="File conflict during package install",
                symptom_ru="Конфликт файлов при установке пакета",
                diagnosis="Files already exist from another package",
                diagnosis_ru="Файлы уже существуют от другого пакета",
                solution_steps=[
                    "Identify conflicting package",
                    "Use --overwrite if safe",
                    "Or remove conflicting package first",
                ],
                actions=[],
                confidence=0.7, risk_level="medium", verified=True,
            ),

            # ═══ AUDIO ════════════════════════════════════
            KBEntry(
                id="kb_audio_no_sound",
                domain="audio",
                tags=["audio", "sink", "no_sound", "pipewire", "pulseaudio"],
                symptom="No audio output",
                symptom_ru="Нет звука",
                diagnosis="PipeWire/PulseAudio sink issue",
                diagnosis_ru="Проблема аудио выхода PipeWire/PulseAudio",
                solution_steps=[
                    "Check sinks: pactl list sinks short",
                    "Restart PipeWire: systemctl --user restart pipewire",
                    "Set default sink if needed",
                ],
                actions=["audio_list_sinks", "audio_restart_pipewire"],
                confidence=0.85, risk_level="low", verified=True,
            ),
            KBEntry(
                id="kb_audio_permission",
                domain="audio",
                tags=["audio", "permission", "group", "access"],
                symptom="Audio permission denied",
                symptom_ru="Нет доступа к аудио устройству",
                diagnosis="User not in audio group",
                diagnosis_ru="Пользователь не в группе audio",
                solution_steps=[
                    "Add user to audio group",
                    "Re-login or reboot",
                ],
                actions=["user_add_group"],
                action_params=[{"group": "audio", "username": "{current_user}"}],
                confidence=0.8, risk_level="low", verified=True,
            ),

            # ═══ DISK ═════════════════════════════════════
            KBEntry(
                id="kb_disk_full",
                domain="disk",
                tags=["disk", "full", "space", "enospc"],
                symptom="Disk is full (No space left on device)",
                symptom_ru="Диск переполнен",
                diagnosis="Filesystem has no free space",
                diagnosis_ru="Файловая система не имеет свободного места",
                solution_steps=[
                    "Check usage: df -h",
                    "Clean package cache: pacman -Scc",
                    "Find large files: du -sh /* 2>/dev/null | sort -rh | head",
                    "Clean journal: journalctl --vacuum-size=100M",
                ],
                actions=["disk_usage"],
                confidence=0.9, risk_level="low", verified=True,
            ),
            KBEntry(
                id="kb_disk_readonly",
                domain="disk",
                tags=["disk", "readonly", "filesystem", "ro"],
                symptom="Read-only filesystem",
                symptom_ru="Файловая система только для чтения",
                diagnosis="Filesystem corruption or hardware issue",
                diagnosis_ru="Повреждение ФС или аппаратная проблема",
                solution_steps=[
                    "Check SMART: smartctl -a /dev/sdX",
                    "Check filesystem: fsck -n /dev/sdX",
                    "Remount: mount -o remount,rw /",
                ],
                actions=["disk_smart", "disk_fsck_check"],
                confidence=0.7, risk_level="high", verified=True,
            ),

            # ═══ BOOT ═════════════════════════════════════
            KBEntry(
                id="kb_boot_grub_missing",
                domain="boot",
                tags=["boot", "grub", "bootloader", "missing"],
                symptom="GRUB bootloader not found after install",
                symptom_ru="Загрузчик GRUB не найден после установки",
                diagnosis="GRUB not installed properly",
                diagnosis_ru="GRUB не установлен корректно",
                solution_steps=[
                    "Chroot into system",
                    "Install GRUB: grub-install --target=x86_64-efi",
                    "Generate config: grub-mkconfig -o /boot/grub/grub.cfg",
                ],
                actions=["boot_grub_install", "boot_grub_config"],
                confidence=0.85, risk_level="high", verified=True,
            ),
            KBEntry(
                id="kb_boot_initramfs",
                domain="boot",
                tags=["boot", "initramfs", "mkinitcpio", "missing"],
                symptom="Initramfs missing or outdated",
                symptom_ru="Initramfs отсутствует или устарел",
                diagnosis="Initramfs needs regeneration",
                diagnosis_ru="Необходимо регенерировать initramfs",
                solution_steps=[
                    "Run mkinitcpio -P",
                    "Recheck boot entries",
                ],
                actions=["boot_initramfs"],
                confidence=0.9, risk_level="medium", verified=True,
            ),

            # ═══ DISPLAY / GPU ═════════════════════════════
            KBEntry(
                id="kb_gpu_nvidia_fail",
                domain="display",
                tags=["gpu", "nvidia", "driver", "module", "fail"],
                symptom="NVIDIA driver not loading",
                symptom_ru="Драйвер NVIDIA не загружается",
                diagnosis="Kernel module mismatch or missing driver",
                diagnosis_ru="Несоответствие модуля ядра или отсутствие драйвера",
                solution_steps=[
                    "Check GPU: lspci | grep -i nvidia",
                    "Check driver: nvidia-smi",
                    "Reinstall: pacman -S nvidia nvidia-utils",
                    "Rebuild initramfs: mkinitcpio -P",
                ],
                actions=["disp_gpu_info", "disp_nvidia_smi", "boot_initramfs"],
                confidence=0.75, risk_level="medium", verified=True,
            ),

            # ═══ SERVICE ═══════════════════════════════════
            KBEntry(
                id="kb_svc_failed",
                domain="service",
                tags=["service", "systemd", "failed", "restart"],
                symptom="Systemd service in failed state",
                symptom_ru="Systemd сервис в состоянии ошибки",
                diagnosis="Service crashed or misconfigured",
                diagnosis_ru="Сервис упал или неправильно настроен",
                solution_steps=[
                    "Check logs: journalctl -u SERVICE -n 50",
                    "Restart service",
                    "Check config if restart fails",
                ],
                actions=["svc_status", "svc_restart"],
                confidence=0.8, risk_level="low", verified=True,
            ),

            # ═══ SECURITY ═════════════════════════════════
            KBEntry(
                id="kb_sec_permission_denied",
                domain="security",
                tags=["permission", "denied", "access", "root"],
                symptom="Permission denied on operation",
                symptom_ru="Отказано в доступе при выполнении операции",
                diagnosis="Insufficient privileges or wrong ownership",
                diagnosis_ru="Недостаточно привилегий или неверный владелец",
                solution_steps=[
                    "Check permissions: stat -c '%a %U %G %n' FILE",
                    "Check if root needed",
                    "Add user to required group if needed",
                ],
                actions=["sec_check_permissions"],
                confidence=0.7, risk_level="none", verified=True,
            ),
        ]

        for entry in entries:
            self._index(entry)

        logger.debug("LocalKB: populated %d entries", len(entries))


# ─── Singleton ─────────────────────────────────────────────────────────────────

_local_kb: Optional[LocalKB] = None

def get_local_kb() -> LocalKB:
    """Получить единственный экземпляр LocalKB."""
    global _local_kb
    if _local_kb is None:
        _local_kb = LocalKB()
    return _local_kb
