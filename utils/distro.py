"""
Lina — Определение дистрибутива Linux.

Определяет текущий дистрибутив Linux и пакетный менеджер
для динамической адаптации системного промпта.
"""

import os
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class DistroInfo:
    """Информация о текущем дистрибутиве Linux."""
    name: str = "unknown"           # Имя дистрибутива (ubuntu, arch, fedora...)
    family: str = "unknown"         # Семейство (debian, arch, rhel, suse)
    pretty_name: str = "Linux"      # Красивое имя для отображения
    version: str = ""               # Версия
    package_manager: str = ""       # Пакетный менеджер (apt, pacman, dnf, zypper)
    install_cmd: str = ""           # Команда установки пакета
    update_cmd: str = ""            # Команда обновления
    search_cmd: str = ""            # Команда поиска пакета
    remove_cmd: str = ""            # Команда удаления

    @property
    def is_known(self) -> bool:
        """Известен ли дистрибутив."""
        return self.family != "unknown"


# ── Таблица семейств дистрибутивов ──

_DISTRO_FAMILIES = {
    # Debian-семейство
    "ubuntu": "debian",
    "debian": "debian",
    "linuxmint": "debian",
    "pop": "debian",
    "elementary": "debian",
    "zorin": "debian",
    "kali": "debian",
    "mx": "debian",
    "lmde": "debian",
    "neon": "debian",

    # Arch-семейство
    "arch": "arch",
    "manjaro": "arch",
    "endeavouros": "arch",
    "cachyos": "arch",
    "garuda": "arch",
    "artix": "arch",
    "arcolinux": "arch",

    # RHEL/Fedora-семейство
    "fedora": "rhel",
    "centos": "rhel",
    "rhel": "rhel",
    "rocky": "rhel",
    "alma": "rhel",
    "nobara": "rhel",

    # SUSE-семейство
    "opensuse": "suse",
    "opensuse-leap": "suse",
    "opensuse-tumbleweed": "suse",
    "sles": "suse",
    "suse": "suse",

    # Другие
    "gentoo": "gentoo",
    "void": "void",
    "nixos": "nix",
    "alpine": "alpine",
    "solus": "solus",
}

# ── Команды пакетных менеджеров по семейству ──

_PKG_COMMANDS = {
    "debian": {
        "package_manager": "apt",
        "install_cmd": "sudo apt install -y",
        "update_cmd": "sudo apt update && sudo apt upgrade -y",
        "search_cmd": "apt search",
        "remove_cmd": "sudo apt remove",
    },
    "arch": {
        "package_manager": "pacman",
        "install_cmd": "sudo pacman -S --noconfirm",
        "update_cmd": "sudo pacman -Syu --noconfirm",
        "search_cmd": "pacman -Ss",
        "remove_cmd": "sudo pacman -R",
    },
    "rhel": {
        "package_manager": "dnf",
        "install_cmd": "sudo dnf install -y",
        "update_cmd": "sudo dnf upgrade -y",
        "search_cmd": "dnf search",
        "remove_cmd": "sudo dnf remove",
    },
    "suse": {
        "package_manager": "zypper",
        "install_cmd": "sudo zypper install -y",
        "update_cmd": "sudo zypper refresh && sudo zypper update -y",
        "search_cmd": "zypper search",
        "remove_cmd": "sudo zypper remove",
    },
    "gentoo": {
        "package_manager": "emerge",
        "install_cmd": "sudo emerge",
        "update_cmd": "sudo emerge --sync && sudo emerge -uDN @world",
        "search_cmd": "emerge --search",
        "remove_cmd": "sudo emerge --unmerge",
    },
    "alpine": {
        "package_manager": "apk",
        "install_cmd": "sudo apk add",
        "update_cmd": "sudo apk update && sudo apk upgrade",
        "search_cmd": "apk search",
        "remove_cmd": "sudo apk del",
    },
    "void": {
        "package_manager": "xbps",
        "install_cmd": "sudo xbps-install -S",
        "update_cmd": "sudo xbps-install -Su",
        "search_cmd": "xbps-query -Rs",
        "remove_cmd": "sudo xbps-remove",
    },
}


def detect_distro() -> DistroInfo:
    """
    Определяет текущий дистрибутив Linux.

    Использует /etc/os-release (стандарт freedesktop) для определения.
    В случае недоступности пробует fallback-методы.

    Returns:
        DistroInfo с заполненными полями.
    """
    info = DistroInfo()

    # ── Метод 1: /etc/os-release (стандарт) ──
    os_release = _parse_os_release()
    if os_release:
        raw_id = os_release.get("ID", "").lower().strip()
        info.name = raw_id
        info.pretty_name = os_release.get("PRETTY_NAME", raw_id or "Linux")
        info.version = os_release.get("VERSION_ID", "")

        # Определяем семейство
        if raw_id in _DISTRO_FAMILIES:
            info.family = _DISTRO_FAMILIES[raw_id]
        else:
            # Проверяем ID_LIKE для производных
            id_like = os_release.get("ID_LIKE", "").lower()
            for part in id_like.split():
                if part in _DISTRO_FAMILIES:
                    info.family = _DISTRO_FAMILIES[part]
                    break

    # ── Метод 2: fallback по наличию пакетного менеджера ──
    if info.family == "unknown":
        info.family = _detect_by_package_manager()
        if info.family != "unknown" and not info.name:
            info.name = info.family

    # ── Заполняем команды пакетного менеджера ──
    if info.family in _PKG_COMMANDS:
        cmds = _PKG_COMMANDS[info.family]
        info.package_manager = cmds["package_manager"]
        info.install_cmd = cmds["install_cmd"]
        info.update_cmd = cmds["update_cmd"]
        info.search_cmd = cmds["search_cmd"]
        info.remove_cmd = cmds["remove_cmd"]

    return info


def _parse_os_release() -> dict:
    """Парсит /etc/os-release в dict."""
    result = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        key, _, value = line.partition("=")
                        # Убираем кавычки
                        value = value.strip('"').strip("'")
                        result[key.strip()] = value
            return result
        except (IOError, PermissionError):
            continue
    return result


def _detect_by_package_manager() -> str:
    """Fallback: определяем семейство по наличию пакетного менеджера."""
    checks = [
        ("apt", "debian"),
        ("pacman", "arch"),
        ("dnf", "rhel"),
        ("zypper", "suse"),
        ("emerge", "gentoo"),
        ("apk", "alpine"),
        ("xbps-install", "void"),
    ]
    for cmd, family in checks:
        try:
            result = subprocess.run(
                ["which", cmd],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return family
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return "unknown"


def get_distro_prompt_section(distro: Optional[DistroInfo] = None) -> str:
    """
    Генерирует секцию промпта с инструкциями для текущего дистрибутива.

    Args:
        distro: DistroInfo (если None — определяет автоматически).

    Returns:
        Строка-секция для системного промпта.
    """
    if distro is None:
        distro = detect_distro()

    if not distro.is_known:
        return (
            "Дистрибутив не определён. Используй универсальные команды. "
            "Спрашивай у пользователя дистрибутив перед установкой пакетов."
        )

    lines = [
        f"Текущий дистрибутив: {distro.pretty_name}",
        f"Семейство: {distro.family}",
        f"Пакетный менеджер: {distro.package_manager}",
        f"Установка пакета: {distro.install_cmd} <пакет>",
        f"Обновление системы: {distro.update_cmd}",
        f"Поиск пакета: {distro.search_cmd} <запрос>",
        f"Удаление пакета: {distro.remove_cmd} <пакет>",
    ]

    # Доп. инструкции по семейству
    if distro.family == "debian":
        lines.extend([
            "Для PPA: sudo add-apt-repository ppa:<имя> && sudo apt update",
            "Snap: snap install <пакет> (если установлен snapd)",
            "Flatpak: flatpak install <пакет> (если установлен flatpak)",
        ])
    elif distro.family == "arch":
        lines.extend([
            "AUR-хелпер: yay -S <пакет> или paru -S <пакет>",
            "Очистка кэша: sudo pacman -Sc",
            "Поиск файлов в пакетах: pacman -Ql <пакет>",
            "Список установленных: pacman -Q",
        ])
    elif distro.family == "rhel":
        lines.extend([
            "COPR-репозитории: sudo dnf copr enable <owner>/<project>",
            "Группы пакетов: sudo dnf groupinstall '<группа>'",
            "Модули: dnf module list",
        ])
    elif distro.family == "suse":
        lines.extend([
            "OBS-репозитории: sudo zypper addrepo <url> <имя>",
            "Паттерны: sudo zypper install -t pattern <паттерн>",
            "Информация: zypper info <пакет>",
        ])

    return "\n".join(lines)


# ── Кэшированный экземпляр ──
_cached_distro: Optional[DistroInfo] = None


def get_cached_distro() -> DistroInfo:
    """Возвращает закэшированную информацию о дистрибутиве."""
    global _cached_distro
    if _cached_distro is None:
        _cached_distro = detect_distro()
    return _cached_distro
