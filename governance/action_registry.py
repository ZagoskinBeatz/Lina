"""
ActionRegistry — реестр разрешённых действий (whitelist).

Каждое действие в системе проходит через ActionRegistry.
Только зарегистрированные действия могут быть выполнены.

Категории:
  service_control, package_ops, network_ops, disk_ops, config_ops,
  user_ops, boot_ops, display_ops, audio_ops, security_ops

Принципы:
  1. Действие НЕ в реестре → БЛОКИРОВКА
  2. Каждое действие имеет risk_level и dry_run
  3. Каждое выполнение логируется в аудит
  4. Действия НЕ выполняются напрямую — только генерируют команду

Phase: GOVERNANCE LAYER / Module 1
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Enum ──────────────────────────────────────────────────────────────────────

class ActionRisk(str, Enum):
    """Уровень риска действия."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionCategory(str, Enum):
    """Категория действия."""
    SERVICE_CONTROL = "service_control"
    PACKAGE_OPS = "package_ops"
    NETWORK_OPS = "network_ops"
    DISK_OPS = "disk_ops"
    CONFIG_OPS = "config_ops"
    USER_OPS = "user_ops"
    BOOT_OPS = "boot_ops"
    DISPLAY_OPS = "display_ops"
    AUDIO_OPS = "audio_ops"
    SECURITY_OPS = "security_ops"
    INSTALLER_OPS = "installer_ops"


class ExecStatus(str, Enum):
    """Статус выполнения действия."""
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    DRY_RUN_OK = "dry_run_ok"
    DRY_RUN_FAIL = "dry_run_fail"
    TIMEOUT = "timeout"
    NEEDS_CONFIRM = "needs_confirm"


# ─── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class ActionDef:
    """Определение одного зарегистрированного действия."""
    id: str
    domain: str
    category: str
    command_template: str
    description: str = ""
    description_ru: str = ""
    requires_root: bool = False
    risk_level: str = "low"
    destructive: bool = False
    reversible: bool = True
    reverse_action: str = ""
    dry_run_cmd: str = ""
    verify_cmd: str = ""
    verify_pattern: str = ""
    timeout: int = 30
    params: List[str] = field(default_factory=list)
    allowed_param_values: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ActionResult:
    """Результат выполнения действия."""
    action_id: str
    status: str = ExecStatus.SUCCESS
    command: str = ""
    output: str = ""
    error: str = ""
    duration: float = 0.0
    dry_run: bool = False
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id, "status": self.status,
            "command": self.command, "output": self.output[:500],
            "error": self.error[:300], "duration": round(self.duration, 2),
            "dry_run": self.dry_run, "verified": self.verified,
        }


# ─── Blacklist ─────────────────────────────────────────────────────────────────

_ABSOLUTE_BLACKLIST = [
    re.compile(r"rm\s+-rf\s+/\s*$"),
    re.compile(r"rm\s+-rf\s+/\*"),
    re.compile(r"dd\s+if=/dev/zero\s+of=/dev/sd"),
    re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;"),
    re.compile(r"mkfs\.\w+\s+/dev/sd[a-z]$"),
    re.compile(r"kill\s+-9\s+1\b"),
    re.compile(r"chmod\s+-R\s+777\s+/\s*$"),
    re.compile(r"curl\s+.*\|\s*(ba)?sh"),
    re.compile(r"wget\s+.*\|\s*(ba)?sh"),
    re.compile(r"eval\s+.*\$\("),
    re.compile(r"shutdown\s+-h\s+now"),
    re.compile(r"reboot\s*$"),
    re.compile(r"init\s+0"),
    re.compile(r"cat\s+/dev/urandom\s*>"),
]

_INJECTION_PATTERNS = [
    re.compile(r"[;&|`$]"),       # Shell metacharacters
    re.compile(r"\$\("),          # Command substitution
    re.compile(r"\.\.\/"),        # Path traversal
]


# ─── ActionRegistry ────────────────────────────────────────────────────────────

class ActionRegistry:
    """
    Реестр разрешённых действий.

    Только зарегистрированные действия могут быть запрошены к выполнению.
    Каждое действие проходит проверку перед выполнением:
      1. Проверка наличия в реестре
      2. Проверка blacklist
      3. Проверка injection
      4. Проверка параметров
      5. Формирование команды
      6. Логирование в аудит
    """

    def __init__(self) -> None:
        self._actions: Dict[str, ActionDef] = {}
        self._audit: List[Dict[str, Any]] = []
        self._max_audit = 2000
        self._register_builtins()

    # ── Регистрация ──────────────────────────────────────

    def register(self, action: ActionDef) -> None:
        """Зарегистрировать действие в реестре."""
        self._actions[action.id] = action
        logger.debug("ActionRegistry: registered %s", action.id)

    def unregister(self, action_id: str) -> bool:
        """Удалить действие из реестра."""
        if action_id in self._actions:
            del self._actions[action_id]
            return True
        return False

    def get(self, action_id: str) -> Optional[ActionDef]:
        """Получить определение действия."""
        return self._actions.get(action_id)

    def list_actions(self, category: str = "", domain: str = "") -> List[ActionDef]:
        """Список действий (с фильтрацией)."""
        result = list(self._actions.values())
        if category:
            result = [a for a in result if a.category == category]
        if domain:
            result = [a for a in result if a.domain == domain]
        return sorted(result, key=lambda a: (a.category, a.id))

    def has(self, action_id: str) -> bool:
        """Проверить наличие действия в реестре."""
        return action_id in self._actions

    # ── Валидация ────────────────────────────────────────

    def validate_action(self, action_id: str,
                        params: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
        """
        Валидировать действие перед выполнением.
        Returns: (ok, reason)
        """
        adef = self._actions.get(action_id)
        if not adef:
            return False, f"Action '{action_id}' not in registry"

        cmd = self._build_command(adef, params or {})
        if not cmd:
            return False, "Failed to build command"

        # Blacklist check
        for pattern in _ABSOLUTE_BLACKLIST:
            if pattern.search(cmd):
                self._log_audit(action_id, "BLOCKED", "blacklist", cmd)
                return False, f"Command matches blacklist pattern"

        # Injection check for user-provided params
        if params:
            for key, value in params.items():
                for pattern in _INJECTION_PATTERNS:
                    if pattern.search(value):
                        self._log_audit(action_id, "BLOCKED", "injection", cmd)
                        return False, f"Param '{key}' contains injection pattern"

        # Allowed values check
        if params and adef.allowed_param_values:
            for key, allowed in adef.allowed_param_values.items():
                if key in params and params[key] not in allowed:
                    return False, f"Param '{key}' value not in allowed list"

        return True, "OK"

    def prepare(self, action_id: str,
                params: Optional[Dict[str, str]] = None) -> ActionResult:
        """
        Подготовить команду (НЕ выполнять).
        Возвращает ActionResult с командой для исполнения.
        """
        ok, reason = self.validate_action(action_id, params)
        if not ok:
            return ActionResult(
                action_id=action_id, status=ExecStatus.BLOCKED, error=reason
            )

        adef = self._actions[action_id]
        cmd = self._build_command(adef, params or {})

        if adef.requires_root:
            cmd = f"sudo {cmd}"

        risk = ActionRisk(adef.risk_level)
        if risk in (ActionRisk.HIGH, ActionRisk.CRITICAL):
            self._log_audit(action_id, "NEEDS_CONFIRM", "high_risk", cmd)
            return ActionResult(
                action_id=action_id, status=ExecStatus.NEEDS_CONFIRM,
                command=cmd,
            )

        self._log_audit(action_id, "PREPARED", "ok", cmd)
        return ActionResult(
            action_id=action_id, status=ExecStatus.SUCCESS, command=cmd
        )

    def execute(self, action_id: str,
                params: Optional[Dict[str, str]] = None,
                dry_run: bool = False) -> ActionResult:
        """
        Выполнить действие из реестра.
        При dry_run=True — только проверяет (не исполняет).
        """
        prepared = self.prepare(action_id, params)
        if prepared.status == ExecStatus.BLOCKED:
            return prepared

        adef = self._actions[action_id]
        cmd = prepared.command
        t0 = time.monotonic()

        # Dry-run
        if dry_run and adef.dry_run_cmd:
            dry_cmd = self._build_command_raw(adef.dry_run_cmd, params or {})
            rc, out = self._run(dry_cmd, adef.timeout)
            dur = time.monotonic() - t0
            status = ExecStatus.DRY_RUN_OK if rc == 0 else ExecStatus.DRY_RUN_FAIL
            self._log_audit(action_id, status, "dry_run", dry_cmd)
            return ActionResult(
                action_id=action_id, status=status, command=dry_cmd,
                output=out, duration=dur, dry_run=True,
            )
        elif dry_run:
            # No dry_run command — just validate
            return ActionResult(
                action_id=action_id, status=ExecStatus.DRY_RUN_OK,
                command=cmd, dry_run=True,
            )

        # Actual execution
        if prepared.status == ExecStatus.NEEDS_CONFIRM:
            return prepared  # Caller must confirm first

        rc, out = self._run(cmd, adef.timeout)
        dur = time.monotonic() - t0

        if rc != 0:
            self._log_audit(action_id, "FAILED", f"rc={rc}", cmd)
            return ActionResult(
                action_id=action_id, status=ExecStatus.FAILED,
                command=cmd, output=out, error=f"Exit code: {rc}",
                duration=dur,
            )

        # Verify
        verified = False
        if adef.verify_cmd:
            v_cmd = self._build_command_raw(adef.verify_cmd, params or {})
            v_rc, v_out = self._run(v_cmd, 10)
            if adef.verify_pattern:
                verified = bool(re.search(adef.verify_pattern, v_out))
            else:
                verified = v_rc == 0

        self._log_audit(action_id, "SUCCESS", "executed", cmd)
        return ActionResult(
            action_id=action_id, status=ExecStatus.SUCCESS,
            command=cmd, output=out, duration=dur, verified=verified,
        )

    # ── Util ─────────────────────────────────────────────

    def _build_command(self, adef: ActionDef,
                       params: Dict[str, str]) -> str:
        """Построить команду из шаблона и параметров."""
        cmd = adef.command_template
        for key, value in params.items():
            # Sanitize value — only alphanumeric, dash, dot, underscore, slash, space
            safe = re.sub(r"[^a-zA-Z0-9_\-./@ ]", "", value)
            cmd = cmd.replace(f"{{{key}}}", safe)
        return cmd

    def _build_command_raw(self, template: str,
                           params: Dict[str, str]) -> str:
        """Построить команду из сырого шаблона."""
        cmd = template
        for key, value in params.items():
            safe = re.sub(r"[^a-zA-Z0-9_\-./@ ]", "", value)
            cmd = cmd.replace(f"{{{key}}}", safe)
        return cmd

    @staticmethod
    def _run(cmd: str, timeout: int = 30) -> Tuple[int, str]:
        """Выполнить команду через subprocess."""
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, env={**os.environ, "LANG": "C.UTF-8"},
            )
            return r.returncode, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            return -1, f"TIMEOUT ({timeout}s)"
        except Exception as e:
            return -2, str(e)

    def _log_audit(self, action_id: str, status: str,
                   reason: str, command: str) -> None:
        """Записать в аудит-лог."""
        entry = {
            "timestamp": time.time(),
            "action_id": action_id,
            "status": status,
            "reason": reason,
            "command": command[:300],
        }
        self._audit.append(entry)
        if len(self._audit) > self._max_audit:
            self._audit = self._audit[-self._max_audit:]
        logger.debug("ActionRegistry audit: %s %s: %s", action_id, status, reason)

    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Получить аудит-лог."""
        return self._audit[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Статистика реестра."""
        cats: Dict[str, int] = {}
        risks: Dict[str, int] = {}
        for a in self._actions.values():
            cats[a.category] = cats.get(a.category, 0) + 1
            risks[a.risk_level] = risks.get(a.risk_level, 0) + 1
        return {
            "total_actions": len(self._actions),
            "categories": cats,
            "risk_distribution": risks,
            "audit_entries": len(self._audit),
        }

    # ── Загрузка / Сохранение из JSON ────────────────────

    def load_from_file(self, path: str) -> int:
        """Загрузить действия из JSON файла. Returns count loaded."""
        try:
            with open(path) as f:
                data = json.load(f)
            count = 0
            for entry in data.get("actions", []):
                self.register(ActionDef(**entry))
                count += 1
            logger.info("ActionRegistry: loaded %d actions from %s", count, path)
            return count
        except Exception as e:
            logger.error("ActionRegistry: load error: %s", e)
            return 0

    def save_to_file(self, path: str) -> bool:
        """Сохранить реестр в JSON файл."""
        try:
            data = {"actions": [a.to_dict() for a in self._actions.values()]}
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error("ActionRegistry: save error: %s", e)
            return False

    # ── Builtin Actions ──────────────────────────────────

    def _register_builtins(self) -> None:
        """Зарегистрировать встроенные действия."""
        builtins = [
            # ─── service_control ───────────────────────
            ActionDef(
                id="svc_restart", domain="service", category="service_control",
                command_template="systemctl restart {service}",
                description_ru="Перезапуск сервиса",
                requires_root=True, risk_level="low",
                params=["service"],
                dry_run_cmd="systemctl status {service}",
                verify_cmd="systemctl is-active {service}",
                verify_pattern="^active",
            ),
            ActionDef(
                id="svc_stop", domain="service", category="service_control",
                command_template="systemctl stop {service}",
                description_ru="Остановка сервиса",
                requires_root=True, risk_level="medium",
                params=["service"],
                verify_cmd="systemctl is-active {service}",
                verify_pattern="^inactive",
            ),
            ActionDef(
                id="svc_start", domain="service", category="service_control",
                command_template="systemctl start {service}",
                description_ru="Запуск сервиса",
                requires_root=True, risk_level="low",
                params=["service"],
                verify_cmd="systemctl is-active {service}",
                verify_pattern="^active",
            ),
            ActionDef(
                id="svc_enable", domain="service", category="service_control",
                command_template="systemctl enable {service}",
                description_ru="Включить автозапуск сервиса",
                requires_root=True, risk_level="low",
                params=["service"],
            ),
            ActionDef(
                id="svc_disable", domain="service", category="service_control",
                command_template="systemctl disable {service}",
                description_ru="Отключить автозапуск сервиса",
                requires_root=True, risk_level="medium",
                params=["service"],
            ),
            ActionDef(
                id="svc_status", domain="service", category="service_control",
                command_template="systemctl status {service}",
                description_ru="Статус сервиса",
                requires_root=False, risk_level="none",
                params=["service"],
            ),
            ActionDef(
                id="svc_user_restart", domain="service", category="service_control",
                command_template="systemctl --user restart {service}",
                description_ru="Перезапуск пользовательского сервиса",
                requires_root=False, risk_level="low",
                params=["service"],
            ),
            # ─── package_ops ───────────────────────────
            ActionDef(
                id="pkg_install", domain="package", category="package_ops",
                command_template="pacman -S --noconfirm {package}",
                description_ru="Установка пакета",
                requires_root=True, risk_level="medium",
                params=["package"],
                dry_run_cmd="pacman -Si {package}",
            ),
            ActionDef(
                id="pkg_remove", domain="package", category="package_ops",
                command_template="pacman -R --noconfirm {package}",
                description_ru="Удаление пакета",
                requires_root=True, risk_level="high", destructive=True,
                params=["package"],
                dry_run_cmd="pacman -Qi {package}",
            ),
            ActionDef(
                id="pkg_update", domain="package", category="package_ops",
                command_template="pacman -Syu --noconfirm",
                description_ru="Обновление всех пакетов",
                requires_root=True, risk_level="high",
            ),
            ActionDef(
                id="pkg_search", domain="package", category="package_ops",
                command_template="pacman -Ss {query}",
                description_ru="Поиск пакета",
                requires_root=False, risk_level="none",
                params=["query"],
            ),
            ActionDef(
                id="pkg_info", domain="package", category="package_ops",
                command_template="pacman -Qi {package}",
                description_ru="Информация о пакете",
                requires_root=False, risk_level="none",
                params=["package"],
            ),
            ActionDef(
                id="pkg_check_updates", domain="package", category="package_ops",
                command_template="checkupdates",
                description_ru="Проверка обновлений",
                requires_root=False, risk_level="none",
            ),
            ActionDef(
                id="flatpak_install", domain="package", category="package_ops",
                command_template="flatpak install -y flathub {app}",
                description_ru="Установка Flatpak приложения",
                requires_root=False, risk_level="medium",
                params=["app"],
            ),
            ActionDef(
                id="flatpak_remove", domain="package", category="package_ops",
                command_template="flatpak uninstall -y {app}",
                description_ru="Удаление Flatpak приложения",
                requires_root=False, risk_level="medium",
                params=["app"],
            ),
            # ─── network_ops ──────────────────────────
            ActionDef(
                id="net_restart_nm", domain="network", category="network_ops",
                command_template="systemctl restart NetworkManager",
                description_ru="Перезапуск NetworkManager",
                requires_root=True, risk_level="medium",
                verify_cmd="nmcli general status",
                verify_pattern="connected",
            ),
            ActionDef(
                id="net_restart_resolved", domain="network", category="network_ops",
                command_template="systemctl restart systemd-resolved",
                description_ru="Перезапуск DNS-резолвера",
                requires_root=True, risk_level="low",
                verify_cmd="resolvectl query archlinux.org",
            ),
            ActionDef(
                id="net_flush_dns", domain="network", category="network_ops",
                command_template="resolvectl flush-caches",
                description_ru="Очистка DNS кэша",
                requires_root=True, risk_level="none",
            ),
            ActionDef(
                id="net_check_ping", domain="network", category="network_ops",
                command_template="ping -c 3 -W 5 {target}",
                description_ru="Проверка соединения (ping)",
                requires_root=False, risk_level="none",
                params=["target"],
            ),
            ActionDef(
                id="net_set_dns", domain="network", category="network_ops",
                command_template="resolvectl dns {interface} {dns}",
                description_ru="Установка DNS сервера",
                requires_root=True, risk_level="medium",
                params=["interface", "dns"],
            ),
            ActionDef(
                id="net_wifi_scan", domain="network", category="network_ops",
                command_template="nmcli device wifi rescan && nmcli device wifi list",
                description_ru="Сканирование WiFi сетей",
                requires_root=False, risk_level="none",
            ),
            ActionDef(
                id="net_wifi_connect", domain="network", category="network_ops",
                command_template="nmcli device wifi connect {ssid} password {password}",
                description_ru="Подключение к WiFi",
                requires_root=False, risk_level="low",
                params=["ssid", "password"],
            ),
            ActionDef(
                id="net_check_gw", domain="network", category="network_ops",
                command_template="ip route show default",
                description_ru="Проверка шлюза по умолчанию",
                requires_root=False, risk_level="none",
            ),
            ActionDef(
                id="net_check_interfaces", domain="network", category="network_ops",
                command_template="ip -br addr show",
                description_ru="Список сетевых интерфейсов",
                requires_root=False, risk_level="none",
            ),
            ActionDef(
                id="net_firewall_status", domain="network", category="network_ops",
                command_template="firewall-cmd --state 2>/dev/null || ufw status 2>/dev/null || iptables -L -n --line-numbers 2>/dev/null | head -30",
                description_ru="Статус фаерволла",
                requires_root=True, risk_level="none",
            ),
            # ─── disk_ops ─────────────────────────────
            ActionDef(
                id="disk_usage", domain="disk", category="disk_ops",
                command_template="df -h",
                description_ru="Использование дисков",
                requires_root=False, risk_level="none",
            ),
            ActionDef(
                id="disk_smart", domain="disk", category="disk_ops",
                command_template="smartctl -a {device}",
                description_ru="SMART статус диска",
                requires_root=True, risk_level="none",
                params=["device"],
            ),
            ActionDef(
                id="disk_fsck_check", domain="disk", category="disk_ops",
                command_template="fsck -n {device}",
                description_ru="Проверка файловой системы (read-only)",
                requires_root=True, risk_level="low",
                params=["device"],
            ),
            ActionDef(
                id="disk_mount", domain="disk", category="disk_ops",
                command_template="mount {device} {mountpoint}",
                description_ru="Монтирование раздела",
                requires_root=True, risk_level="medium",
                params=["device", "mountpoint"],
            ),
            ActionDef(
                id="disk_umount", domain="disk", category="disk_ops",
                command_template="umount {mountpoint}",
                description_ru="Отмонтирование раздела",
                requires_root=True, risk_level="medium",
                params=["mountpoint"],
            ),
            ActionDef(
                id="disk_btrfs_scrub", domain="disk", category="disk_ops",
                command_template="btrfs scrub start {mountpoint}",
                description_ru="Запуск btrfs scrub",
                requires_root=True, risk_level="low",
                params=["mountpoint"],
            ),
            # ─── config_ops ───────────────────────────
            ActionDef(
                id="cfg_backup", domain="config", category="config_ops",
                command_template="cp -a {path} {path}.bak.$(date +%Y%m%d%H%M%S)",
                description_ru="Бэкап конфигурации",
                requires_root=True, risk_level="none",
                params=["path"],
            ),
            ActionDef(
                id="cfg_restore", domain="config", category="config_ops",
                command_template="cp -a {backup_path} {path}",
                description_ru="Восстановление конфигурации",
                requires_root=True, risk_level="medium",
                params=["backup_path", "path"],
            ),
            ActionDef(
                id="cfg_set_timezone", domain="config", category="config_ops",
                command_template="timedatectl set-timezone {timezone}",
                description_ru="Установка часового пояса",
                requires_root=True, risk_level="low",
                params=["timezone"],
            ),
            ActionDef(
                id="cfg_set_locale", domain="config", category="config_ops",
                command_template="localectl set-locale LANG={locale}",
                description_ru="Установка локали",
                requires_root=True, risk_level="low",
                params=["locale"],
            ),
            ActionDef(
                id="cfg_set_hostname", domain="config", category="config_ops",
                command_template="hostnamectl set-hostname {hostname}",
                description_ru="Установка hostname",
                requires_root=True, risk_level="low",
                params=["hostname"],
            ),
            ActionDef(
                id="cfg_generate_fstab", domain="config", category="config_ops",
                command_template="genfstab -U {root} >> {root}/etc/fstab",
                description_ru="Генерация fstab",
                requires_root=True, risk_level="medium",
                params=["root"],
            ),
            ActionDef(
                id="cfg_generate_locale", domain="config", category="config_ops",
                command_template="locale-gen",
                description_ru="Генерация локали",
                requires_root=True, risk_level="none",
            ),
            ActionDef(
                id="cfg_generate_initramfs", domain="config", category="config_ops",
                command_template="mkinitcpio -P",
                description_ru="Генерация initramfs",
                requires_root=True, risk_level="medium",
            ),
            # ─── user_ops ─────────────────────────────
            ActionDef(
                id="user_create", domain="user", category="user_ops",
                command_template="useradd -m -G wheel {username}",
                description_ru="Создание пользователя",
                requires_root=True, risk_level="medium",
                params=["username"],
            ),
            ActionDef(
                id="user_add_group", domain="user", category="user_ops",
                command_template="usermod -aG {group} {username}",
                description_ru="Добавление в группу",
                requires_root=True, risk_level="low",
                params=["group", "username"],
            ),
            ActionDef(
                id="user_set_shell", domain="user", category="user_ops",
                command_template="chsh -s {shell} {username}",
                description_ru="Смена оболочки",
                requires_root=True, risk_level="low",
                params=["shell", "username"],
            ),
            ActionDef(
                id="user_lock", domain="user", category="user_ops",
                command_template="passwd -l {username}",
                description_ru="Блокировка пользователя",
                requires_root=True, risk_level="medium",
                params=["username"],
            ),
            # ─── boot_ops ─────────────────────────────
            ActionDef(
                id="boot_grub_install", domain="boot", category="boot_ops",
                command_template="grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=GRUB",
                description_ru="Установка GRUB (EFI)",
                requires_root=True, risk_level="high",
            ),
            ActionDef(
                id="boot_grub_config", domain="boot", category="boot_ops",
                command_template="grub-mkconfig -o /boot/grub/grub.cfg",
                description_ru="Генерация конфига GRUB",
                requires_root=True, risk_level="medium",
            ),
            ActionDef(
                id="boot_systemd_install", domain="boot", category="boot_ops",
                command_template="bootctl install",
                description_ru="Установка systemd-boot",
                requires_root=True, risk_level="high",
            ),
            ActionDef(
                id="boot_systemd_update", domain="boot", category="boot_ops",
                command_template="bootctl update",
                description_ru="Обновление systemd-boot",
                requires_root=True, risk_level="medium",
            ),
            ActionDef(
                id="boot_initramfs", domain="boot", category="boot_ops",
                command_template="mkinitcpio -P",
                description_ru="Регенерация initramfs",
                requires_root=True, risk_level="medium",
            ),
            ActionDef(
                id="boot_check_efi", domain="boot", category="boot_ops",
                command_template="efibootmgr -v",
                description_ru="Проверка EFI записей",
                requires_root=True, risk_level="none",
            ),
            # ─── display_ops ──────────────────────────
            ActionDef(
                id="disp_list_monitors", domain="display", category="display_ops",
                command_template="xrandr --listmonitors 2>/dev/null || wlr-randr 2>/dev/null",
                description_ru="Список мониторов",
                requires_root=False, risk_level="none",
            ),
            ActionDef(
                id="disp_set_resolution", domain="display", category="display_ops",
                command_template="xrandr --output {output} --mode {mode}",
                description_ru="Установка разрешения",
                requires_root=False, risk_level="low",
                params=["output", "mode"],
            ),
            ActionDef(
                id="disp_set_brightness", domain="display", category="display_ops",
                command_template="brightnessctl set {level}%",
                description_ru="Установка яркости",
                requires_root=False, risk_level="none",
                params=["level"],
            ),
            ActionDef(
                id="disp_gpu_info", domain="display", category="display_ops",
                command_template="lspci -k | grep -A3 -i vga",
                description_ru="Информация о GPU",
                requires_root=False, risk_level="none",
            ),
            ActionDef(
                id="disp_nvidia_smi", domain="display", category="display_ops",
                command_template="nvidia-smi",
                description_ru="Статус NVIDIA GPU",
                requires_root=False, risk_level="none",
            ),
            # ─── audio_ops ────────────────────────────
            ActionDef(
                id="audio_restart_pipewire", domain="audio", category="audio_ops",
                command_template="systemctl --user restart pipewire pipewire-pulse wireplumber",
                description_ru="Перезапуск PipeWire",
                requires_root=False, risk_level="low",
                verify_cmd="pactl info",
                verify_pattern="Server Name:",
            ),
            ActionDef(
                id="audio_restart_pulse", domain="audio", category="audio_ops",
                command_template="pulseaudio -k && pulseaudio --start",
                description_ru="Перезапуск PulseAudio",
                requires_root=False, risk_level="low",
            ),
            ActionDef(
                id="audio_list_sinks", domain="audio", category="audio_ops",
                command_template="pactl list sinks short",
                description_ru="Список аудио выходов",
                requires_root=False, risk_level="none",
            ),
            ActionDef(
                id="audio_set_volume", domain="audio", category="audio_ops",
                command_template="pactl set-sink-volume @DEFAULT_SINK@ {level}%",
                description_ru="Установка громкости",
                requires_root=False, risk_level="none",
                params=["level"],
            ),
            ActionDef(
                id="audio_set_default", domain="audio", category="audio_ops",
                command_template="pactl set-default-sink {sink}",
                description_ru="Установка аудио выхода по умолчанию",
                requires_root=False, risk_level="low",
                params=["sink"],
            ),
            ActionDef(
                id="audio_check_status", domain="audio", category="audio_ops",
                command_template="pactl info && pactl list sinks short",
                description_ru="Проверка аудио статуса",
                requires_root=False, risk_level="none",
            ),
            # ─── security_ops ─────────────────────────
            ActionDef(
                id="sec_firewall_enable", domain="security", category="security_ops",
                command_template="ufw enable",
                description_ru="Включение фаерволла",
                requires_root=True, risk_level="medium",
            ),
            ActionDef(
                id="sec_firewall_status", domain="security", category="security_ops",
                command_template="ufw status verbose 2>/dev/null || firewall-cmd --state 2>/dev/null",
                description_ru="Статус фаерволла",
                requires_root=True, risk_level="none",
            ),
            ActionDef(
                id="sec_check_suid", domain="security", category="security_ops",
                command_template="find /tmp /var/tmp -perm -4000 -ls 2>/dev/null",
                description_ru="Проверка SUID файлов в temp",
                requires_root=False, risk_level="none",
            ),
            ActionDef(
                id="sec_check_permissions", domain="security", category="security_ops",
                command_template="stat -c '%a %U %G %n' {path}",
                description_ru="Проверка прав файла",
                requires_root=False, risk_level="none",
                params=["path"],
            ),
            # ─── installer_ops ────────────────────────
            ActionDef(
                id="inst_mount_partition", domain="installer", category="installer_ops",
                command_template="mount {device} {mountpoint}",
                description_ru="Монтирование раздела (installer)",
                requires_root=True, risk_level="medium",
                params=["device", "mountpoint"],
            ),
            ActionDef(
                id="inst_create_subvol", domain="installer", category="installer_ops",
                command_template="btrfs subvolume create {path}",
                description_ru="Создание btrfs subvolume",
                requires_root=True, risk_level="medium",
                params=["path"],
            ),
            ActionDef(
                id="inst_pacstrap", domain="installer", category="installer_ops",
                command_template="pacstrap {root} {packages}",
                description_ru="Установка базовых пакетов",
                requires_root=True, risk_level="high",
                params=["root", "packages"],
            ),
            ActionDef(
                id="inst_chroot_cmd", domain="installer", category="installer_ops",
                command_template="arch-chroot {root} {command}",
                description_ru="Команда в chroot",
                requires_root=True, risk_level="high",
                params=["root", "command"],
            ),
            ActionDef(
                id="inst_enable_service", domain="installer", category="installer_ops",
                command_template="arch-chroot {root} systemctl enable {service}",
                description_ru="Включение сервиса в chroot",
                requires_root=True, risk_level="medium",
                params=["root", "service"],
            ),
        ]

        for action in builtins:
            self.register(action)


# ─── Singleton ─────────────────────────────────────────────────────────────────

_registry: Optional[ActionRegistry] = None

def get_action_registry() -> ActionRegistry:
    """Получить единственный экземпляр ActionRegistry."""
    global _registry
    if _registry is None:
        _registry = ActionRegistry()
    return _registry
