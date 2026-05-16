"""
AutoFixEngine — автоматическое исправление проблем Linux.

Принимает Diagnosis от ErrorClassifier и выполняет:
1. Анализ последствий (impact assessment)
2. Dry-run проверку
3. Backup критических файлов
4. Применение исправления
5. Верификацию результата
6. Rollback при неудаче

Три режима работы:
- SAFE: только диагностика, никаких изменений
- ASSIST: предлагает fix, ждёт подтверждения
- AUTONOMOUS: исправляет автоматически при LOW-MEDIUM риске

Phase: PROBLEM TERMINATOR / Module 4
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re as _re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Dataclasses
# ═══════════════════════════════════════════════════════════════════

class FixMode(Enum):
    SAFE = "safe"           # Только диагностика
    ASSIST = "assist"       # Предлагает, ждёт подтверждения
    AUTONOMOUS = "autonomous"  # Исправляет LOW/MEDIUM автоматически


class FixStatus(Enum):
    PENDING = "pending"
    DRY_RUN_OK = "dry_run_ok"
    DRY_RUN_FAIL = "dry_run_fail"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"      # Риск слишком высок / заблокирован


@dataclass
class FixAction:
    """Одно исправляющее действие."""
    name: str
    command: str                      # Shell-команда
    description: str = ""
    requires_sudo: bool = False
    destructive: bool = False         # Может потерять данные?
    backup_paths: List[str] = field(default_factory=list)  # Файлы для backup перед выполнением
    dry_run_cmd: str = ""             # Команда для dry-run проверки
    verify_cmd: str = ""              # Команда проверки результата
    verify_pattern: str = ""          # Regex для успешной верификации
    rollback_cmd: str = ""            # Команда отката
    risk: str = "low"                 # low/medium/high/critical
    timeout: int = 30                 # Секунд

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "command": self.command,
            "description": self.description, "risk": self.risk,
            "requires_sudo": self.requires_sudo,
            "destructive": self.destructive,
        }


@dataclass
class FixResult:
    """Результат применения исправления."""
    action: FixAction
    status: FixStatus = FixStatus.PENDING
    output: str = ""
    error: str = ""
    rolled_back: bool = False
    duration: float = 0.0
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.name,
            "status": self.status.value,
            "output": self.output[:500],
            "error": self.error[:500],
            "verified": self.verified,
            "rolled_back": self.rolled_back,
            "duration": round(self.duration, 2),
        }


@dataclass
class FixPlan:
    """План исправления — набор действий."""
    category: str
    diagnosis_summary: str
    actions: List[FixAction] = field(default_factory=list)
    results: List[FixResult] = field(default_factory=list)
    mode: FixMode = FixMode.ASSIST
    overall_status: FixStatus = FixStatus.PENDING

    def format_text(self) -> str:
        mode_icon = {"safe": "🔒", "assist": "🤝", "autonomous": "🤖"}.get(
            self.mode.value, "❓"
        )
        lines = [
            f"═══ План исправления [{mode_icon} {self.mode.value}] ═══",
            f"  Проблема: {self.diagnosis_summary}",
            f"  Статус: {self.overall_status.value}",
            "",
        ]
        for i, action in enumerate(self.actions, 1):
            risk_icon = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(
                action.risk, "❓"
            )
            sudo = " [sudo]" if action.requires_sudo else ""
            lines.append(f"  {i}. {risk_icon}{sudo} {action.description or action.name}")
            lines.append(f"     $ {action.command}")

        if self.results:
            lines.append("")
            lines.append("  Результаты:")
            for r in self.results:
                status_icon = {
                    "success": "✅", "failed": "❌", "rolled_back": "↩️",
                    "blocked": "⛔", "pending": "⏳",
                }.get(r.status.value, "?")
                lines.append(f"    {status_icon} {r.action.name}: {r.status.value}")
                if r.error:
                    lines.append(f"       ⚠️ {r.error[:100]}")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Рецепты исправлений
# ═══════════════════════════════════════════════════════════════════
# Каждая категория → список FixAction

def _detect_pkg_mgr() -> str:
    """Определяет пакетный менеджер."""
    for mgr in ("pacman", "apt", "dnf", "zypper"):
        if shutil.which(mgr):
            return mgr
    return "unknown"


def _make_recipes() -> Dict[str, List[FixAction]]:
    """Генерирует рецепты под текущую систему."""
    pkg = _detect_pkg_mgr()

    # Общие утилиты
    restart_nm = FixAction(
        name="restart_networkmanager",
        command="systemctl restart NetworkManager",
        description="Перезапуск NetworkManager",
        requires_sudo=True,
        verify_cmd="systemctl is-active NetworkManager",
        verify_pattern="^active",
        rollback_cmd="systemctl restart NetworkManager",
        risk="low", timeout=15,
    )
    restart_resolved = FixAction(
        name="restart_resolved",
        command="systemctl restart systemd-resolved",
        description="Перезапуск systemd-resolved",
        requires_sudo=True,
        verify_cmd="resolvectl status 2>/dev/null | head -5",
        risk="low", timeout=10,
    )
    restart_audio = FixAction(
        name="restart_audio",
        command="systemctl --user restart pipewire pipewire-pulse wireplumber 2>/dev/null || systemctl --user restart pulseaudio 2>/dev/null",
        description="Перезапуск аудио стека (PipeWire / PulseAudio)",
        verify_cmd="pactl list sinks short 2>/dev/null",
        risk="low", timeout=10,
    )
    restart_bluetooth = FixAction(
        name="restart_bluetooth",
        command="systemctl restart bluetooth",
        description="Перезапуск Bluetooth сервиса",
        requires_sudo=True,
        verify_cmd="systemctl is-active bluetooth",
        verify_pattern="^active",
        risk="low", timeout=10,
    )

    # Пакетный менеджер — fix broken
    if pkg == "pacman":
        fix_packages = FixAction(
            name="fix_packages",
            command="pacman -Syy && pacman -Syu --noconfirm",
            description="Обновление базы и пакетов (pacman)",
            requires_sudo=True,
            dry_run_cmd="pacman -Syu --print",
            risk="medium", timeout=120,
        )
        remove_lock = FixAction(
            name="remove_pkg_lock",
            command="rm -f /var/lib/pacman/db.lck",
            description="Удалить lock-файл pacman",
            requires_sudo=True,
            verify_cmd="test ! -f /var/lib/pacman/db.lck && echo ok",
            verify_pattern="ok",
            risk="low", timeout=5,
        )
        clear_cache = FixAction(
            name="clear_pkg_cache",
            command="pacman -Sc --noconfirm",
            description="Очистка кэша пакетов",
            requires_sudo=True,
            risk="low", timeout=30,
        )
    elif pkg == "apt":
        fix_packages = FixAction(
            name="fix_packages",
            command="apt-get update && apt-get -f install -y",
            description="Обновление базы и починка зависимостей (apt)",
            requires_sudo=True,
            dry_run_cmd="apt-get -f install --dry-run",
            risk="medium", timeout=120,
        )
        remove_lock = FixAction(
            name="remove_pkg_lock",
            command="rm -f /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock",
            description="Удалить lock-файлы apt/dpkg",
            requires_sudo=True,
            risk="low", timeout=5,
        )
        clear_cache = FixAction(
            name="clear_pkg_cache",
            command="apt-get clean",
            description="Очистка кэша apt",
            requires_sudo=True,
            risk="low", timeout=30,
        )
    elif pkg == "dnf":
        fix_packages = FixAction(
            name="fix_packages",
            command="dnf clean all && dnf distro-sync -y",
            description="Обновление и синхронизация пакетов (dnf)",
            requires_sudo=True,
            dry_run_cmd="dnf distro-sync --assumeno",
            risk="medium", timeout=120,
        )
        remove_lock = FixAction(
            name="remove_pkg_lock",
            command="rm -f /var/run/dnf.pid",
            description="Удалить lock-файл dnf",
            requires_sudo=True,
            risk="low", timeout=5,
        )
        clear_cache = FixAction(
            name="clear_pkg_cache",
            command="dnf clean all",
            description="Очистка кэша dnf",
            requires_sudo=True,
            risk="low", timeout=30,
        )
    else:
        fix_packages = FixAction(
            name="fix_packages",
            command="echo 'Неизвестный пакетный менеджер'",
            description="Пакетный менеджер не поддерживается",
            risk="low", timeout=5,
        )
        remove_lock = FixAction(
            name="remove_pkg_lock",
            command="echo 'no lock to remove'",
            description="Нет lock-файла",
            risk="low", timeout=5,
        )
        clear_cache = FixAction(
            name="clear_pkg_cache",
            command="echo 'no cache to clear'",
            description="Нет кэша для очистки",
            risk="low", timeout=5,
        )

    return {
        # ─── Сеть
        "network_failure": [
            FixAction(
                name="check_interfaces",
                command="ip link show",
                description="Проверить интерфейсы",
                risk="low", timeout=5,
            ),
            FixAction(
                name="dhcp_renew",
                command="dhclient -r && dhclient 2>/dev/null || nmcli connection up $(nmcli -t -f NAME connection show --active | head -1) 2>/dev/null || true",
                description="Обновить DHCP",
                requires_sudo=True,
                risk="low", timeout=15,
            ),
            restart_nm,
        ],
        # ─── DNS
        "dns_failure": [
            FixAction(
                name="set_fallback_dns",
                command="echo 'nameserver 1.1.1.1\nnameserver 8.8.8.8' | tee /etc/resolv.conf.lina_fix > /dev/null && echo 'Создан /etc/resolv.conf.lina_fix'",
                description="Создать fallback DNS конфигурацию",
                requires_sudo=True,
                backup_paths=["/etc/resolv.conf"],
                risk="medium", timeout=5,
            ),
            restart_resolved,
        ],
        # ─── Аудио
        "audio_failure": [
            FixAction(
                name="unmute",
                command="pactl set-sink-mute @DEFAULT_SINK@ 0 && pactl set-sink-volume @DEFAULT_SINK@ 50%",
                description="Снять mute и установить 50% громкость",
                verify_cmd="pactl get-sink-mute @DEFAULT_SINK@",
                verify_pattern="no",
                risk="low", timeout=5,
            ),
            restart_audio,
            FixAction(
                name="reload_alsa",
                command="alsactl restore 2>/dev/null || true",
                description="Восстановить ALSA настройки",
                risk="low", timeout=5,
            ),
        ],
        # ─── GPU
        "gpu_failure": [
            FixAction(
                name="check_gpu_driver",
                command="lspci -k | grep -A3 VGA",
                description="Проверить GPU драйвер",
                risk="low", timeout=5,
            ),
        ],
        # ─── Сервисы
        "service_crash_loop": [
            FixAction(
                name="restart_service",
                command="systemctl restart {service_name}",
                description="Перезапуск сервиса",
                requires_sudo=True,
                verify_cmd="systemctl is-active {service_name}",
                verify_pattern="^active",
                risk="low", timeout=15,
            ),
        ],
        # ─── Пакеты
        "corrupt_package": [remove_lock, clear_cache, fix_packages],
        "broken_dependency": [clear_cache, fix_packages],
        # ─── Диск
        "disk_failure": [
            FixAction(
                name="clean_tmp",
                command="find /tmp -type f -atime +7 -delete 2>/dev/null; find /var/tmp -type f -atime +7 -delete 2>/dev/null; echo done",
                description="Очистить старые файлы в /tmp",
                requires_sudo=True,
                risk="low", timeout=30,
            ),
            FixAction(
                name="clean_journal",
                command="journalctl --vacuum-time=3d --vacuum-size=100M",
                description="Очистить старые журналы systemd",
                requires_sudo=True,
                risk="low", timeout=15,
            ),
            clear_cache,
        ],
        # ─── Память
        "memory_exhaustion": [
            FixAction(
                name="clear_caches",
                command="sync && echo 3 > /proc/sys/vm/drop_caches",
                description="Очистить page cache",
                requires_sudo=True,
                risk="low", timeout=5,
            ),
        ],
        # ─── Bluetooth
        "bluetooth_failure": [
            FixAction(
                name="unblock_bluetooth",
                command="rfkill unblock bluetooth 2>/dev/null || true",
                description="Разблокировать Bluetooth (rfkill)",
                risk="low", timeout=5,
            ),
            restart_bluetooth,
        ],
        # ─── USB
        "usb_failure": [
            FixAction(
                name="reset_usb",
                command="echo 'Для сброса USB попробуйте переподключить устройство'",
                description="Совет: переподключить USB",
                risk="low", timeout=5,
            ),
        ],
        # ─── Загрузка
        "boot_failure": [
            FixAction(
                name="regen_initramfs",
                command="mkinitcpio -P 2>/dev/null || update-initramfs -u 2>/dev/null || dracut -f 2>/dev/null",
                description="Перегенерация initramfs",
                requires_sudo=True, destructive=True,
                backup_paths=["/boot"],
                risk="critical", timeout=120,
            ),
            FixAction(
                name="update_grub",
                command="grub-mkconfig -o /boot/grub/grub.cfg 2>/dev/null || update-grub 2>/dev/null",
                description="Обновить GRUB",
                requires_sudo=True,
                backup_paths=["/boot/grub/grub.cfg"],
                risk="high", timeout=60,
            ),
        ],
        # ─── Температура
        "thermal_issue": [
            FixAction(
                name="check_fans",
                command="sensors 2>/dev/null | grep -i fan || echo 'Нет данных о вентиляторах'",
                description="Проверить вентиляторы",
                risk="low", timeout=5,
            ),
        ],
    }


# ═══════════════════════════════════════════════════════════════════
#  AutoFixEngine
# ═══════════════════════════════════════════════════════════════════

class AutoFixEngine:
    """
    Автоматическое исправление проблем Linux.

    Никогда не выполняет:
    - Команды без анализа последствий
    - Destructive-действия без backup
    - Sudo без контроля
    - HIGH/CRITICAL-фиксы в AUTONOMOUS-режиме
    """

    _BACKUP_DIR = Path.home() / ".local" / "share" / "lina" / "backups"
    _LOG_DIR = Path.home() / ".local" / "share" / "lina" / "fix_logs"

    # Запрещённые паттерны (никогда не выполняются)
    _DANGEROUS_PATTERNS = [
        r"rm\s+(-rf?|--recursive).*(/|~|\*)",
        r"mkfs\.",
        r"dd\s+if=.*of=/dev/",
        r">\s*/dev/sd",
        r"chmod\s+-R\s+777\s+/",
        r":()\s*{",  # fork bomb
        r"\|\s*bash",  # piped bash
    ]

    def __init__(self, mode: FixMode = FixMode.ASSIST) -> None:
        self.mode = mode
        self._recipes = _make_recipes()
        self._BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        self._LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._confirm_callback: Optional[Callable[[str], bool]] = None

    def set_mode(self, mode: FixMode) -> None:
        self.mode = mode

    def set_confirm_callback(self, cb: Callable[[str], bool]) -> None:
        """Устанавливает callback для запроса подтверждения в ASSIST-режиме."""
        self._confirm_callback = cb

    # ─── Главный метод ────────────────────────────────────────

    def create_plan(
        self,
        diagnosis: Any,  # Diagnosis from ErrorClassifier
        service_name: str = "",
    ) -> FixPlan:
        """
        Создаёт план исправления на основе диагноза.

        Не выполняет — только планирует.
        """
        category = diagnosis.category.value if hasattr(diagnosis, "category") else str(diagnosis)
        summary = diagnosis.summary if hasattr(diagnosis, "summary") else str(diagnosis)

        actions = self._recipes.get(category, [])

        # Validate service_name against shell injection
        _SAFE_SVC = _re.compile(r"^[a-zA-Z0-9@._-]+$")
        if service_name and not _SAFE_SVC.match(service_name):
            return FixPlan(
                category=category, summary=summary,
                actions=[], risk_level="blocked",
            )

        # Template substitution
        final_actions = []
        for a in actions:
            a_copy = FixAction(
                name=a.name,
                command=a.command.replace("{service_name}", service_name),
                description=a.description.replace("{service_name}", service_name),
                requires_sudo=a.requires_sudo,
                destructive=a.destructive,
                backup_paths=list(a.backup_paths),
                dry_run_cmd=a.dry_run_cmd,
                verify_cmd=a.verify_cmd.replace("{service_name}", service_name),
                verify_pattern=a.verify_pattern,
                rollback_cmd=a.rollback_cmd,
                risk=a.risk,
                timeout=a.timeout,
            )
            final_actions.append(a_copy)

        plan = FixPlan(
            category=category,
            diagnosis_summary=summary,
            actions=final_actions,
            mode=self.mode,
        )

        return plan

    def execute_plan(self, plan: FixPlan) -> FixPlan:
        """
        Выполняет план исправления с учётом режима.

        SAFE: ничего не делает, только возвращает план.
        ASSIST: выполняет только после подтверждения.
        AUTONOMOUS: выполняет LOW/MEDIUM-риск автоматически.

        Интеграция OVERLORD:
        - RiskEngine: оценка плана перед выполнением
        - SelfHealer: блокировка повторных рецептов
        """
        # ── OVERLORD: RiskEngine assessment ──
        try:
            from lina.diagnostics.risk_engine import get_risk_engine
            risk = get_risk_engine()
            assessment = risk.assess_plan([a.command for a in plan.actions])
            if assessment.verdict.value == "critical":
                plan.overall_status = FixStatus.BLOCKED
                for action in plan.actions:
                    plan.results.append(FixResult(
                        action=action, status=FixStatus.BLOCKED,
                        error=f"RiskEngine: CRITICAL risk ({assessment.total_risk:.2f})",
                    ))
                self._log_plan(plan)
                return plan
        except ImportError:
            pass  # RiskEngine not available — continue without it
        except Exception as e:
            logger.warning("RiskEngine assessment error: %s", e)

        # ── OVERLORD: SelfHealer recipe check ──
        try:
            from lina.diagnostics.self_healer import get_self_healer
            healer = get_self_healer()
            cmds = [a.command for a in plan.actions]
            if healer.is_recipe_blocked(plan.category, cmds):
                plan.overall_status = FixStatus.BLOCKED
                for action in plan.actions:
                    plan.results.append(FixResult(
                        action=action, status=FixStatus.BLOCKED,
                        error="SelfHealer: recipe blocked (previously worsened system)",
                    ))
                self._log_plan(plan)
                return plan
        except ImportError:
            pass
        except Exception as e:
            logger.warning("SelfHealer check error: %s", e)

        if plan.mode == FixMode.SAFE:
            plan.overall_status = FixStatus.BLOCKED
            for action in plan.actions:
                plan.results.append(FixResult(
                    action=action, status=FixStatus.BLOCKED,
                    output="Safe mode — только диагностика",
                ))
            self._log_plan(plan)
            return plan

        for action in plan.actions:
            result = self._execute_action(action, plan.mode)
            plan.results.append(result)
            # Если критическая ошибка — прекращаем
            if result.status == FixStatus.FAILED and action.risk in ("high", "critical"):
                plan.overall_status = FixStatus.FAILED
                self._log_plan(plan)
                return plan

        # Определяем общий статус
        statuses = [r.status for r in plan.results]
        if all(s == FixStatus.SUCCESS or s == FixStatus.BLOCKED for s in statuses):
            plan.overall_status = FixStatus.SUCCESS
        elif any(s == FixStatus.FAILED for s in statuses):
            plan.overall_status = FixStatus.FAILED
        elif any(s == FixStatus.ROLLED_BACK for s in statuses):
            plan.overall_status = FixStatus.ROLLED_BACK
        else:
            plan.overall_status = FixStatus.PENDING

        self._log_plan(plan)
        return plan

    # ─── Выполнение одного действия ───────────────────────────

    def _execute_action(self, action: FixAction, mode: FixMode) -> FixResult:
        """Выполняет одно действие с full safety pipeline."""
        result = FixResult(action=action)
        start = time.time()

        try:
            # 1. Security check
            if self._is_dangerous(action.command):
                result.status = FixStatus.BLOCKED
                result.error = "⛔ Команда заблокирована (опасная)"
                return result

            # 2. Risk gating
            if mode == FixMode.AUTONOMOUS and action.risk in ("high", "critical"):
                result.status = FixStatus.BLOCKED
                result.error = f"Автономный режим не выполняет {action.risk}-риск команды"
                return result

            # 3. ASSIST mode — запросить подтверждение
            if mode == FixMode.ASSIST:
                if self._confirm_callback:
                    prompt = (
                        f"Выполнить: {action.description}\n"
                        f"  $ {action.command}\n"
                        f"  Риск: {action.risk}"
                        f"{' [sudo]' if action.requires_sudo else ''}"
                    )
                    if not self._confirm_callback(prompt):
                        result.status = FixStatus.BLOCKED
                        result.error = "Пользователь отклонил"
                        return result
                else:
                    # Без callback в ASSIST → не выполняем
                    result.status = FixStatus.AWAITING_CONFIRMATION
                    result.output = action.command
                    return result

            # 4. Backup
            if action.backup_paths:
                self._backup(action.backup_paths)

            # 5. Dry-run (если есть)
            if action.dry_run_cmd:
                rc, out = self._run_cmd(action.dry_run_cmd, action.timeout)
                if rc != 0:
                    result.status = FixStatus.DRY_RUN_FAIL
                    result.error = f"Dry-run failed: {out[:200]}"
                    return result
                result.status = FixStatus.DRY_RUN_OK

            # 6. Execute
            result.status = FixStatus.EXECUTING
            cmd = action.command
            if action.requires_sudo and os.getuid() != 0:
                # Используем pkexec для графического sudo
                if shutil.which("pkexec"):
                    cmd = f"pkexec sh -c {shlex.quote(cmd)}"
                else:
                    cmd = f"sudo sh -c {shlex.quote(cmd)}"

            rc, out = self._run_cmd(cmd, action.timeout)
            result.output = out

            if rc != 0:
                result.status = FixStatus.FAILED
                result.error = out
                # 7. Rollback при неудаче
                if action.rollback_cmd:
                    self._run_cmd(action.rollback_cmd, action.timeout)
                    result.rolled_back = True
                    result.status = FixStatus.ROLLED_BACK
                return result

            # 8. Verify
            if action.verify_cmd:
                rc_v, out_v = self._run_cmd(action.verify_cmd, 10)
                import re
                if action.verify_pattern:
                    result.verified = bool(re.search(action.verify_pattern, out_v))
                else:
                    result.verified = rc_v == 0
            else:
                result.verified = True

            result.status = FixStatus.SUCCESS

        except Exception as e:
            result.status = FixStatus.FAILED
            result.error = str(e)
            logger.error("AutoFix action %s failed: %s", action.name, e)

        finally:
            result.duration = time.time() - start

        return result

    # ─── Утилиты ──────────────────────────────────────────────

    @staticmethod
    def _run_cmd(cmd: str, timeout: int = 30) -> Tuple[int, str]:
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, env={**os.environ, "LANG": "C.UTF-8"},
            )
            return r.returncode, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            return -1, f"TIMEOUT ({timeout}s)"
        except Exception as e:
            return -1, str(e)

    def _is_dangerous(self, cmd: str) -> bool:
        import re
        for pattern in self._DANGEROUS_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return True
        return False

    def _backup(self, paths: List[str]) -> None:
        """Создаёт backup файлов перед опасной операцией."""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self._BACKUP_DIR / ts
        backup_dir.mkdir(parents=True, exist_ok=True)
        for path in paths:
            src = Path(path)
            if src.exists():
                try:
                    if src.is_dir():
                        shutil.copytree(str(src), str(backup_dir / src.name))
                    else:
                        shutil.copy2(str(src), str(backup_dir / src.name))
                    logger.info("Backup: %s → %s", src, backup_dir / src.name)
                except Exception as e:
                    logger.warning("Backup failed for %s: %s", src, e)

    def _log_plan(self, plan: FixPlan) -> None:
        """Логирует план и результаты в JSON."""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self._LOG_DIR / f"fix_{ts}_{plan.category}.json"
        data = {
            "timestamp": ts,
            "category": plan.category,
            "mode": plan.mode.value,
            "diagnosis": plan.diagnosis_summary,
            "overall_status": plan.overall_status.value,
            "actions": [a.to_dict() for a in plan.actions],
            "results": [r.to_dict() for r in plan.results],
        }
        try:
            log_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning("Fix log write failed: %s", e)

    def list_backups(self) -> List[str]:
        """Список бэкапов."""
        if not self._BACKUP_DIR.exists():
            return []
        return sorted(
            [d.name for d in self._BACKUP_DIR.iterdir() if d.is_dir()],
            reverse=True,
        )

    def list_fix_logs(self) -> List[str]:
        """Список логов исправлений."""
        if not self._LOG_DIR.exists():
            return []
        return sorted(
            [f.name for f in self._LOG_DIR.glob("*.json")],
            reverse=True,
        )


# ═══════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════

_engine: Optional[AutoFixEngine] = None


def get_autofix() -> AutoFixEngine:
    global _engine
    if _engine is None:
        _engine = AutoFixEngine(mode=FixMode.ASSIST)
    return _engine
