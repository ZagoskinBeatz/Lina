"""
Lina — Единый интерфейс к пакетным менеджерам.

Поддержка: pacman (Arch), apt (Debian/Ubuntu), dnf (Fedora/RHEL),
           zypper (openSUSE), flatpak, snap.

БЕЗОПАСНОСТЬ: Модуль НИКОГДА не выполняет install/remove.
Только генерирует команду и показывает пользователю.
Чтение (search, info, is_installed) выполняется напрямую.
"""

import subprocess
import re
from typing import Dict, List, Optional, Tuple
from pathlib import Path


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def _run(cmd: str, timeout: int = 15) -> str:
    """Выполняет команду, возвращает stdout."""
    try:
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True,
            timeout=timeout,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _run_lines(cmd: str, timeout: int = 15) -> List[str]:
    out = _run(cmd, timeout)
    return [l for l in out.split("\n") if l.strip()] if out else []


def _which(cmd: str) -> bool:
    """Проверяет наличие команды в PATH."""
    return bool(_run(f"which {cmd} 2>/dev/null"))


# ─── Определение дистрибутива ──────────────────────────────────────────────────

def detect_distro() -> Dict:
    """
    Определяет дистрибутив и пакетный менеджер.

    Returns:
        {distro, distro_id, version, package_manager, family}
    """
    info = {
        "distro": "unknown",
        "distro_id": "unknown",
        "version": "",
        "package_manager": "unknown",
        "family": "unknown",
    }

    # os-release
    os_release = _run("cat /etc/os-release 2>/dev/null")
    if os_release:
        for line in os_release.split("\n"):
            if line.startswith("NAME="):
                info["distro"] = line.split("=", 1)[1].strip('"')
            elif line.startswith("ID="):
                info["distro_id"] = line.split("=", 1)[1].strip('"').lower()
            elif line.startswith("VERSION_ID="):
                info["version"] = line.split("=", 1)[1].strip('"')

    # Определяем family & package_manager
    distro_id = info["distro_id"]

    if distro_id in ("arch", "cachyos", "endeavouros", "manjaro", "artix", "garuda"):
        info["family"] = "arch"
        info["package_manager"] = "pacman"
    elif distro_id in ("ubuntu", "debian", "linuxmint", "pop", "elementary", "zorin", "kali", "mx"):
        info["family"] = "debian"
        info["package_manager"] = "apt"
    elif distro_id in ("fedora", "rhel", "centos", "rocky", "alma", "nobara"):
        info["family"] = "fedora"
        info["package_manager"] = "dnf"
    elif distro_id in ("opensuse-tumbleweed", "opensuse-leap", "opensuse"):
        info["family"] = "suse"
        info["package_manager"] = "zypper"
    else:
        # Фолбэк: проверяем наличие менеджеров
        for mgr in ("pacman", "apt", "dnf", "zypper"):
            if _which(mgr):
                info["package_manager"] = mgr
                break

    return info


# ─── Базовый интерфейс ────────────────────────────────────────────────────────

class PackageManager:
    """
    Единый интерфейс к пакетным менеджерам.

    БЕЗОПАСНОСТЬ:
      - search(), info(), is_installed(), list_installed() — выполняются напрямую
      - install(), remove(), update() — ТОЛЬКО генерируют команду
    """

    def __init__(self):
        self._distro_info = detect_distro()
        self._mgr = self._distro_info["package_manager"]

    @property
    def distro_info(self) -> Dict:
        return self._distro_info

    @property
    def manager_name(self) -> str:
        return self._mgr

    def search(self, query: str, limit: int = 20) -> List[Dict]:
        """
        Поиск пакета в репозиториях.

        Returns:
            [{name, version, description, installed}, ...]
        """
        if self._mgr == "pacman":
            return self._pacman_search(query, limit)
        elif self._mgr == "apt":
            return self._apt_search(query, limit)
        elif self._mgr == "dnf":
            return self._dnf_search(query, limit)
        elif self._mgr == "zypper":
            return self._zypper_search(query, limit)
        return []

    def info(self, name: str) -> Dict:
        """
        Информация о пакете.

        Returns:
            {name, version, description, installed, size, depends, ...}
        """
        if self._mgr == "pacman":
            return self._pacman_info(name)
        elif self._mgr == "apt":
            return self._apt_info(name)
        elif self._mgr == "dnf":
            return self._dnf_info(name)
        elif self._mgr == "zypper":
            return self._zypper_info(name)
        return {"name": name, "error": f"Неизвестный менеджер: {self._mgr}"}

    def is_installed(self, name: str) -> bool:
        """Проверяет, установлен ли пакет."""
        if self._mgr == "pacman":
            return bool(_run(f"pacman -Qq {name} 2>/dev/null"))
        elif self._mgr == "apt":
            out = _run(f"dpkg-query -W -f='${{Status}}' {name} 2>/dev/null")
            return "install ok installed" in out
        elif self._mgr == "dnf":
            return bool(_run(f"rpm -q {name} 2>/dev/null"))
        elif self._mgr == "zypper":
            return bool(_run(f"rpm -q {name} 2>/dev/null"))
        return False

    def list_installed(self, limit: int = 0) -> List[Dict]:
        """
        Список установленных пакетов.

        Returns:
            [{name, version}, ...]
        """
        if self._mgr == "pacman":
            lines = _run_lines("pacman -Q 2>/dev/null")
            pkgs = []
            for line in lines:
                parts = line.split(None, 1)
                if parts:
                    pkgs.append({
                        "name": parts[0],
                        "version": parts[1] if len(parts) > 1 else "",
                    })
            return pkgs[:limit] if limit else pkgs

        elif self._mgr == "apt":
            lines = _run_lines("dpkg-query -W -f='${Package} ${Version}\\n' 2>/dev/null")
            pkgs = []
            for line in lines:
                parts = line.split(None, 1)
                if parts:
                    pkgs.append({
                        "name": parts[0],
                        "version": parts[1] if len(parts) > 1 else "",
                    })
            return pkgs[:limit] if limit else pkgs

        elif self._mgr == "dnf":
            lines = _run_lines("rpm -qa --qf '%{NAME} %{VERSION}-%{RELEASE}\\n' 2>/dev/null")
            pkgs = []
            for line in lines:
                parts = line.split(None, 1)
                if parts:
                    pkgs.append({
                        "name": parts[0],
                        "version": parts[1] if len(parts) > 1 else "",
                    })
            return pkgs[:limit] if limit else pkgs

        return []

    # ── Генерация команд (НЕ выполняет!) ──

    def install(self, name: str) -> Dict:
        """
        ГЕНЕРИРУЕТ команду установки. НЕ выполняет!

        Returns:
            {command, description, requires_root}
        """
        cmds = {
            "pacman": f"sudo pacman -S {name}",
            "apt": f"sudo apt install {name}",
            "dnf": f"sudo dnf install {name}",
            "zypper": f"sudo zypper install {name}",
        }
        return {
            "command": cmds.get(self._mgr, f"# Установка {name} — неизвестный менеджер"),
            "description": f"Установить пакет {name}",
            "requires_root": True,
            "action": "install",
            "package": name,
        }

    def remove(self, name: str) -> Dict:
        """ГЕНЕРИРУЕТ команду удаления. НЕ выполняет!"""
        cmds = {
            "pacman": f"sudo pacman -Rs {name}",
            "apt": f"sudo apt remove {name}",
            "dnf": f"sudo dnf remove {name}",
            "zypper": f"sudo zypper remove {name}",
        }
        return {
            "command": cmds.get(self._mgr, f"# Удаление {name} — неизвестный менеджер"),
            "description": f"Удалить пакет {name} и неиспользуемые зависимости",
            "requires_root": True,
            "action": "remove",
            "package": name,
        }

    def update(self) -> Dict:
        """ГЕНЕРИРУЕТ команду обновления системы. НЕ выполняет!"""
        cmds = {
            "pacman": "sudo pacman -Syu",
            "apt": "sudo apt update && sudo apt upgrade",
            "dnf": "sudo dnf upgrade --refresh",
            "zypper": "sudo zypper ref && sudo zypper up",
        }
        return {
            "command": cmds.get(self._mgr, "# Обновление — неизвестный менеджер"),
            "description": "Обновить систему",
            "requires_root": True,
            "action": "update",
        }

    def check_updates(self) -> List[Dict]:
        """
        Проверяет доступные обновления (read-only).

        Returns:
            [{name, current_version, new_version}, ...]
        """
        if self._mgr == "pacman":
            lines = _run_lines("checkupdates 2>/dev/null", timeout=30)
            updates = []
            for line in lines:
                parts = line.split()
                if len(parts) >= 4:
                    updates.append({
                        "name": parts[0],
                        "current_version": parts[1],
                        "new_version": parts[3],
                    })
            return updates

        elif self._mgr == "apt":
            _run("apt list --upgradable 2>/dev/null")
            lines = _run_lines("apt list --upgradable 2>/dev/null")
            updates = []
            for line in lines:
                if "/" in line:
                    name = line.split("/")[0]
                    match = re.search(r'(\S+)\s+\[upgradable from:\s+(\S+)\]', line)
                    if match:
                        updates.append({
                            "name": name,
                            "new_version": match.group(1),
                            "current_version": match.group(2),
                        })
            return updates

        elif self._mgr == "dnf":
            lines = _run_lines("dnf check-update -q 2>/dev/null", timeout=30)
            updates = []
            for line in lines:
                parts = line.split()
                if len(parts) >= 2 and not line.startswith(" "):
                    updates.append({
                        "name": parts[0].split(".")[0],
                        "new_version": parts[1],
                        "current_version": "",
                    })
            return updates

        return []

    def list_orphans(self) -> List[str]:
        """Список пакетов-сирот (неиспользуемые зависимости)."""
        if self._mgr == "pacman":
            return _run_lines("pacman -Qdtq 2>/dev/null")
        elif self._mgr == "apt":
            lines = _run_lines("apt-get --dry-run autoremove 2>/dev/null | grep '^Remv'")
            return [l.split()[1] for l in lines if len(l.split()) > 1]
        elif self._mgr == "dnf":
            return _run_lines("dnf autoremove --assumeno 2>/dev/null | grep -E '^ ' | awk '{print $1}'")
        return []

    # ── Flatpak ──

    def flatpak_search(self, query: str) -> List[Dict]:
        """Поиск Flatpak-пакетов."""
        if not _which("flatpak"):
            return []
        lines = _run_lines(f"flatpak search {query} 2>/dev/null")
        results = []
        for line in lines[1:] if lines else []:  # skip header
            parts = line.split("\t")
            if len(parts) >= 3:
                results.append({
                    "name": parts[0].strip(),
                    "description": parts[1].strip() if len(parts) > 1 else "",
                    "app_id": parts[2].strip() if len(parts) > 2 else "",
                    "source": "flatpak",
                })
        return results

    def flatpak_list(self) -> List[Dict]:
        """Список установленных Flatpak."""
        if not _which("flatpak"):
            return []
        lines = _run_lines("flatpak list --columns=application,name,version 2>/dev/null")
        result = []
        for line in lines:
            parts = line.split("\t")
            if parts:
                result.append({
                    "app_id": parts[0].strip(),
                    "name": parts[1].strip() if len(parts) > 1 else "",
                    "version": parts[2].strip() if len(parts) > 2 else "",
                })
        return result

    # ── Реализации для конкретных менеджеров ──

    def _pacman_search(self, query: str, limit: int) -> List[Dict]:
        lines = _run_lines(f"pacman -Ss {query} 2>/dev/null")
        results = []
        i = 0
        while i < len(lines) and len(results) < limit:
            line = lines[i]
            if "/" in line and not line.startswith(" "):
                parts = line.split()
                repo_name = parts[0] if parts else ""
                version = parts[1] if len(parts) > 1 else ""
                installed = "[installed" in line
                name = repo_name.split("/")[-1] if "/" in repo_name else repo_name
                desc = lines[i + 1].strip() if i + 1 < len(lines) else ""
                results.append({
                    "name": name,
                    "version": version,
                    "description": desc,
                    "installed": installed,
                    "repo": repo_name.split("/")[0] if "/" in repo_name else "",
                })
                i += 2
            else:
                i += 1
        return results

    def _apt_search(self, query: str, limit: int) -> List[Dict]:
        lines = _run_lines(f"apt-cache search {query} 2>/dev/null")
        results = []
        for line in lines[:limit]:
            parts = line.split(" - ", 1)
            if parts:
                name = parts[0].strip()
                desc = parts[1].strip() if len(parts) > 1 else ""
                results.append({
                    "name": name,
                    "version": "",
                    "description": desc,
                    "installed": self.is_installed(name),
                })
        return results

    def _dnf_search(self, query: str, limit: int) -> List[Dict]:
        lines = _run_lines(f"dnf search {query} -q 2>/dev/null")
        results = []
        for line in lines[:limit]:
            parts = line.split(" : ", 1)
            if len(parts) >= 2:
                name = parts[0].strip().split(".")[0]
                desc = parts[1].strip()
                results.append({
                    "name": name,
                    "version": "",
                    "description": desc,
                    "installed": False,
                })
        return results

    def _zypper_search(self, query: str, limit: int) -> List[Dict]:
        lines = _run_lines(f"zypper se {query} 2>/dev/null")
        results = []
        for line in lines:
            if "|" in line:
                cols = [c.strip() for c in line.split("|")]
                if len(cols) >= 3 and cols[0] not in ("S", "-", ""):
                    results.append({
                        "name": cols[1],
                        "version": cols[3] if len(cols) > 3 else "",
                        "description": cols[2] if len(cols) > 2 else "",
                        "installed": cols[0] == "i",
                    })
        return results[:limit]

    def _pacman_info(self, name: str) -> Dict:
        out = _run(f"pacman -Si {name} 2>/dev/null") or _run(f"pacman -Qi {name} 2>/dev/null")
        if not out:
            return {"name": name, "error": "Пакет не найден"}
        info = {"name": name, "installed": self.is_installed(name)}
        for line in out.split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip().lower()
                val = val.strip()
                if key == "version":
                    info["version"] = val
                elif key == "description" or key == "описание":
                    info["description"] = val
                elif key in ("installed size", "размер установки"):
                    info["size"] = val
                elif key in ("depends on", "зависит от"):
                    info["depends"] = [d.strip() for d in val.split() if d.strip() != "None"]
                elif key in ("url", "ссылка"):
                    info["url"] = val
        return info

    def _apt_info(self, name: str) -> Dict:
        out = _run(f"apt-cache show {name} 2>/dev/null")
        if not out:
            return {"name": name, "error": "Пакет не найден"}
        info = {"name": name, "installed": self.is_installed(name)}
        for line in out.split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip().lower()
                val = val.strip()
                if key == "version":
                    info["version"] = val
                elif key == "description":
                    info["description"] = val
                elif key == "installed-size":
                    info["size"] = val
                elif key == "depends":
                    info["depends"] = [d.strip().split()[0] for d in val.split(",")]
                elif key == "homepage":
                    info["url"] = val
        return info

    def _dnf_info(self, name: str) -> Dict:
        out = _run(f"dnf info {name} -q 2>/dev/null")
        if not out:
            return {"name": name, "error": "Пакет не найден"}
        info = {"name": name, "installed": self.is_installed(name)}
        for line in out.split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip().lower()
                val = val.strip()
                if key == "version":
                    info["version"] = val
                elif key == "description":
                    info["description"] = val
                elif key == "size":
                    info["size"] = val
                elif key == "url":
                    info["url"] = val
        return info

    def _zypper_info(self, name: str) -> Dict:
        out = _run(f"zypper info {name} 2>/dev/null")
        if not out:
            return {"name": name, "error": "Пакет не найден"}
        info = {"name": name, "installed": self.is_installed(name)}
        for line in out.split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip().lower()
                val = val.strip()
                if key == "version":
                    info["version"] = val
                elif key in ("summary", "description"):
                    info["description"] = val
                elif key == "installed size":
                    info["size"] = val
        return info
