"""
Lina — Управление systemd-сервисами.

Чтение (list, status, logs) — выполняется напрямую.
Запись (start, stop, restart, enable) — ГЕНЕРИРУЕТ команду для пользователя.

БЕЗОПАСНОСТЬ: Модуль НЕ выполняет управляющие команды.
Cервисы из whitelist не требуют sudo для чтения статуса.
"""

import subprocess
import re
import shlex
from typing import Dict, List, Optional

# Strict regex for valid systemd service/unit names
_SAFE_SERVICE_RE = re.compile(r'^[a-zA-Z0-9@._:-]{1,256}$')


def _validate_service_name(name: str) -> str:
    """Validate service name to prevent shell injection."""
    if not _SAFE_SERVICE_RE.match(name):
        raise ValueError(f"Недопустимое имя сервиса: {name!r}")
    return name


def _run(cmd: str, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True,
            timeout=timeout,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _run_lines(cmd: str, timeout: int = 10) -> List[str]:
    out = _run(cmd, timeout)
    return [l for l in out.split("\n") if l.strip()] if out else []


class ServiceManager:
    """
    Управление systemd-сервисами.

    Чтение: выполняется напрямую.
    Запись: генерирует команду + требует подтверждение.
    """

    def list_services(
        self,
        state: str = "all",
        service_type: str = "service",
    ) -> List[Dict]:
        """
        Список systemd-сервисов.

        Args:
            state: "running", "failed", "enabled", "disabled", "all"
            service_type: "service", "timer", "socket", "all"

        Returns:
            [{name, load_state, active_state, sub_state, description}, ...]
        """
        type_flag = f"--type={service_type}" if service_type != "all" else ""

        if state == "all":
            cmd = f"systemctl --no-pager --no-legend list-units {type_flag} 2>/dev/null"
        elif state in ("running", "failed", "inactive"):
            cmd = f"systemctl --no-pager --no-legend list-units --state={state} {type_flag} 2>/dev/null"
        elif state in ("enabled", "disabled"):
            cmd = f"systemctl --no-pager --no-legend list-unit-files --state={state} {type_flag} 2>/dev/null"
            lines = _run_lines(cmd)
            return [
                {"name": parts[0], "state": parts[1]} 
                for line in lines 
                if (parts := line.split()) and len(parts) >= 2
            ]
        else:
            cmd = f"systemctl --no-pager --no-legend list-units {type_flag} 2>/dev/null"

        lines = _run_lines(cmd)
        services = []
        for line in lines:
            parts = line.split(None, 4)
            if len(parts) >= 4:
                services.append({
                    "name": parts[0],
                    "load_state": parts[1],
                    "active_state": parts[2],
                    "sub_state": parts[3],
                    "description": parts[4] if len(parts) > 4 else "",
                })
        return services

    def status(self, name: str) -> Dict:
        """
        Подробный статус сервиса.

        Returns:
            {name, active, enabled, running, pid, memory, cpu,
             loaded, description, since, logs_last}
        """
        name = _validate_service_name(name)
        info = {
            "name": name,
            "active": False,
            "enabled": False,
            "running": False,
            "pid": "",
            "memory": "",
            "cpu": "",
            "loaded": False,
            "description": "",
            "since": "",
        }

        # systemctl show — machine-readable
        show = _run(f"systemctl show {name} --no-pager 2>/dev/null")
        if not show:
            info["error"] = "Сервис не найден"
            return info

        props = {}
        for line in show.split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                props[k] = v

        info["active"] = props.get("ActiveState", "") == "active"
        info["running"] = props.get("SubState", "") == "running"
        info["loaded"] = props.get("LoadState", "") == "loaded"
        info["enabled"] = props.get("UnitFileState", "") in ("enabled", "enabled-runtime")
        info["pid"] = props.get("MainPID", "0")
        info["memory"] = props.get("MemoryCurrent", "")
        if info["memory"] and info["memory"].isdigit():
            mb = int(info["memory"]) / (1024 * 1024)
            info["memory"] = f"{mb:.1f} MB"
        info["description"] = props.get("Description", "")
        info["since"] = props.get("ActiveEnterTimestamp", "")

        return info

    def is_active(self, name: str) -> bool:
        """Проверяет, активен ли сервис."""
        name = _validate_service_name(name)
        out = _run(f"systemctl is-active {name} 2>/dev/null")
        return out == "active"

    def is_enabled(self, name: str) -> bool:
        """Проверяет, включён ли автозапуск."""
        name = _validate_service_name(name)
        out = _run(f"systemctl is-enabled {name} 2>/dev/null")
        return out == "enabled"

    def logs(self, name: str, lines: int = 50, since: str = "") -> str:
        """
        Логи сервиса из journalctl.

        Args:
            name: Имя сервиса.
            lines: Количество строк.
            since: Период ("1h", "30m", "1d").

        Returns:
            Текст логов.
        """
        name = _validate_service_name(name)
        lines = max(1, min(lines, 1000))  # cap lines
        since_flag = ""
        if since and re.match(r'^\d+[smhd]$', since):
            since_flag = f"--since='-{since}'"
        return _run(
            f"journalctl -u {name} --no-pager -n {lines} {since_flag} 2>/dev/null",
            timeout=15,
        )

    # ── Генерация команд (НЕ выполняет!) ──

    def start(self, name: str) -> Dict:
        """ГЕНЕРИРУЕТ команду запуска."""
        name = _validate_service_name(name)
        return {
            "command": f"sudo systemctl start {name}",
            "description": f"Запустить сервис {name}",
            "requires_root": True,
            "action": "start",
            "service": name,
        }

    def stop(self, name: str) -> Dict:
        """ГЕНЕРИРУЕТ команду остановки."""
        name = _validate_service_name(name)
        return {
            "command": f"sudo systemctl stop {name}",
            "description": f"Остановить сервис {name}",
            "requires_root": True,
            "action": "stop",
            "service": name,
        }

    def restart(self, name: str) -> Dict:
        """ГЕНЕРИРУЕТ команду перезапуска."""
        name = _validate_service_name(name)
        return {
            "command": f"sudo systemctl restart {name}",
            "description": f"Перезапустить сервис {name}",
            "requires_root": True,
            "action": "restart",
            "service": name,
        }

    def enable(self, name: str) -> Dict:
        """ГЕНЕРИРУЕТ команду включения автозапуска."""
        name = _validate_service_name(name)
        return {
            "command": f"sudo systemctl enable {name}",
            "description": f"Включить автозапуск {name}",
            "requires_root": True,
            "action": "enable",
            "service": name,
        }

    def disable(self, name: str) -> Dict:
        """ГЕНЕРИРУЕТ команду отключения автозапуска."""
        name = _validate_service_name(name)
        return {
            "command": f"sudo systemctl disable {name}",
            "description": f"Отключить автозапуск {name}",
            "requires_root": True,
            "action": "disable",
            "service": name,
        }

    def diagnose(self, name: str) -> Dict:
        """
        Диагностика проблемного сервиса.

        Returns:
            {name, status, diagnosis, logs, suggestions}
        """
        st = self.status(name)
        log_text = self.logs(name, lines=30)

        diagnosis = {
            "name": name,
            "status": st,
            "logs": log_text,
            "diagnosis": "",
            "suggestions": [],
        }

        if not st.get("loaded"):
            diagnosis["diagnosis"] = f"Сервис {name} не найден в systemd"
            diagnosis["suggestions"].append(f"Проверьте имя: systemctl list-units | grep {name}")
            return diagnosis

        if st.get("running"):
            diagnosis["diagnosis"] = f"Сервис {name} работает нормально"
            return diagnosis

        if st.get("active") and not st.get("running"):
            diagnosis["diagnosis"] = f"Сервис {name} активен, но не в состоянии running (возможно, exited)"
            return diagnosis

        # Сервис не активен — анализируем логи
        diagnosis["diagnosis"] = f"Сервис {name} не запущен"

        if "Permission denied" in log_text:
            diagnosis["suggestions"].append("Проблема с правами доступа")
            diagnosis["suggestions"].append(f"Проверьте: journalctl -u {name} | grep -i permission")

        if "No such file" in log_text or "not found" in log_text.lower():
            diagnosis["suggestions"].append("Отсутствует исполняемый файл или конфиг")
            diagnosis["suggestions"].append(f"Проверьте ExecStart: systemctl cat {name}")

        if "port" in log_text.lower() and ("bind" in log_text.lower() or "address already in use" in log_text.lower()):
            diagnosis["suggestions"].append("Порт уже занят другим процессом")
            diagnosis["suggestions"].append("Проверьте: ss -tlnp | grep <port>")

        if not diagnosis["suggestions"]:
            diagnosis["suggestions"].append(f"Посмотрите полные логи: journalctl -u {name} -n 100")
            diagnosis["suggestions"].append(f"Попробуйте перезапустить: sudo systemctl restart {name}")

        if not st.get("enabled"):
            diagnosis["suggestions"].append(f"Автозапуск отключён. Включить: sudo systemctl enable {name}")

        return diagnosis

    def format_status(self, name: str) -> str:
        """Форматирует статус сервиса в текст."""
        st = self.status(name)
        icon = "✅" if st.get("running") else ("⚠" if st.get("active") else "❌")
        enabled = "✅ auto" if st.get("enabled") else "⬜ manual"
        lines = [
            f"{icon} {name}: {st.get('description', '')}",
            f"   Active: {'yes' if st['active'] else 'no'} | Running: {'yes' if st['running'] else 'no'} | {enabled}",
        ]
        if st.get("pid") and st["pid"] != "0":
            lines.append(f"   PID: {st['pid']} | Memory: {st.get('memory', '?')}")
        if st.get("since"):
            lines.append(f"   Since: {st['since']}")
        return "\n".join(lines)
