"""
Lina Installer — Packaging & Systemd.

Генерирует файлы для пакетирования:
  - lina.service      — systemd unit-файл
  - PKGBUILD            — Arch Linux / AUR
  - debian/control      — Debian/Ubuntu .deb
  - lina.spec         — Fedora .rpm
  - flatpak manifest    — Flatpak
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("lina.installer.packaging")


# ─── Версия ─────────────────────────────────────────────────────────────────

LINA_VERSION = "1.0.0"
LINA_DESCRIPTION = "Lina — локальный ИИ-помощник для Linux"
LINA_URL = "https://github.com/lina-linux/lina"
LINA_LICENSE = "MIT"


# ─── Конфигурация пакета ─────────────────────────────────────────────────────

@dataclass
class PackageInfo:
    """Метаданные пакета."""
    name: str = "lina"
    version: str = LINA_VERSION
    release: int = 1
    description: str = LINA_DESCRIPTION
    url: str = LINA_URL
    license: str = LINA_LICENSE
    maintainer: str = "Lina Team"
    email: str = "lina@linux.local"

    # Зависимости
    depends: List[str] = field(default_factory=lambda: [
        "python>=3.10",
        "python-pip",
    ])
    optional_depends: List[str] = field(default_factory=lambda: [
        "python-pyqt6: GUI интерфейс",
        "espeak-ng: Text-to-Speech",
        "piper-tts: Высококачественный TTS",
    ])

    # Пути установки
    install_prefix: str = "/usr"
    config_dir: str = "/etc/lina"
    data_dir: str = "/usr/share/lina"
    bin_path: str = "/usr/bin/lina"


# ─── Systemd ─────────────────────────────────────────────────────────────────

SYSTEMD_SERVICE_TEMPLATE = """\
[Unit]
Description={description}
After=network.target graphical.target
Documentation=man:lina(1)

[Service]
Type=simple
User=%u
ExecStart={bin_path} --daemon
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5
MemoryMax=8G
CPUQuota=60%
Environment="LINA_CONFIG={config_dir}/config.yaml"
Environment="LINA_DATA={data_dir}"

# Безопасность
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.config/lina %h/.cache/lina
PrivateTmp=yes

[Install]
WantedBy=default.target
"""

SYSTEMD_USER_SERVICE_TEMPLATE = """\
[Unit]
Description={description} (user service)
After=graphical-session.target

[Service]
Type=simple
ExecStart={bin_path} --gui
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5
MemoryMax=8G

[Install]
WantedBy=default.target
"""


class SystemdGenerator:
    """Генератор systemd unit-файлов."""

    def __init__(self, pkg: Optional[PackageInfo] = None):
        self.pkg = pkg or PackageInfo()

    def generate_system_service(self) -> str:
        return SYSTEMD_SERVICE_TEMPLATE.format(
            description=self.pkg.description,
            bin_path=self.pkg.bin_path,
            config_dir=self.pkg.config_dir,
            data_dir=self.pkg.data_dir,
        )

    def generate_user_service(self) -> str:
        return SYSTEMD_USER_SERVICE_TEMPLATE.format(
            description=self.pkg.description,
            bin_path=self.pkg.bin_path,
        )

    def validate_service(self, content: str) -> Dict[str, bool]:
        checks = {
            "has_unit": "[Unit]" in content,
            "has_service": "[Service]" in content,
            "has_install": "[Install]" in content,
            "has_exec": "ExecStart=" in content,
            "has_type": "Type=" in content,
            "has_restart": "Restart=" in content,
            "has_description": "Description=" in content,
        }
        return checks


# ─── PKGBUILD (Arch Linux) ──────────────────────────────────────────────────

PKGBUILD_TEMPLATE = """\
# Maintainer: {maintainer} <{email}>
pkgname={name}
pkgver={version}
pkgrel={release}
pkgdesc='{description}'
arch=('any')
url='{url}'
license=('{license}')
depends=({depends_str})
optdepends=({optdepends_str})
makedepends=('python-build' 'python-installer' 'python-wheel')
source=("$pkgname-$pkgver.tar.gz::{url}/archive/v$pkgver.tar.gz")
sha256sums=('SKIP')

package() {{
    cd "$srcdir/$pkgname-$pkgver"

    # Установка Python-пакета
    python -m installer --destdir="$pkgdir" dist/*.whl

    # Systemd unit
    install -Dm644 lina.service "$pkgdir/usr/lib/systemd/user/lina.service"

    # Конфиг
    install -Dm644 config.yaml "$pkgdir/etc/lina/config.yaml"

    # Знания
    install -dm755 "$pkgdir/usr/share/lina/knowledge"
    cp -r knowledge/* "$pkgdir/usr/share/lina/knowledge/"

    # Desktop file
    install -Dm644 lina.desktop "$pkgdir/usr/share/applications/lina.desktop"

    # Иконка
    install -Dm644 lina.svg "$pkgdir/usr/share/icons/hicolor/scalable/apps/lina.svg"

    # Man-страница
    install -Dm644 man/lina.1 "$pkgdir/usr/share/man/man1/lina.1"
}}
"""


class PKGBUILDGenerator:
    """Генератор PKGBUILD для Arch Linux."""

    def __init__(self, pkg: Optional[PackageInfo] = None):
        self.pkg = pkg or PackageInfo()

    def generate(self) -> str:
        depends_str = " ".join(f"'{d.split(':')[0].strip()}'"
                                for d in self.pkg.depends)
        optdepends_str = " ".join(f"'{d}'" for d in self.pkg.optional_depends)

        return PKGBUILD_TEMPLATE.format(
            name=self.pkg.name,
            version=self.pkg.version,
            release=self.pkg.release,
            description=self.pkg.description,
            url=self.pkg.url,
            license=self.pkg.license,
            maintainer=self.pkg.maintainer,
            email=self.pkg.email,
            depends_str=depends_str,
            optdepends_str=optdepends_str,
        )

    def validate(self, content: str) -> Dict[str, bool]:
        return {
            "has_pkgname": "pkgname=" in content,
            "has_pkgver": "pkgver=" in content,
            "has_depends": "depends=(" in content,
            "has_package_func": "package()" in content,
            "has_arch": "arch=" in content,
            "has_license": "license=" in content,
        }


# ─── Debian ──────────────────────────────────────────────────────────────────

DEBIAN_CONTROL_TEMPLATE = """\
Source: {name}
Section: utils
Priority: optional
Maintainer: {maintainer} <{email}>
Build-Depends: debhelper-compat (= 13), python3, python3-pip, dh-python
Standards-Version: 4.6.0
Homepage: {url}

Package: {name}
Architecture: all
Depends: python3 (>= 3.10), ${{python3:Depends}}, ${{misc:Depends}}
Recommends: espeak-ng
Suggests: python3-pyqt6
Description: {description}
 Lina — локальный ИИ-помощник для Linux.
 Работает полностью оффлайн, используя LLM-модели.
 Поддерживает диагностику системы, управление пакетами,
 голосовой ввод/вывод и графический интерфейс.
"""


class DebianGenerator:
    """Генератор debian/ файлов для .deb."""

    def __init__(self, pkg: Optional[PackageInfo] = None):
        self.pkg = pkg or PackageInfo()

    def generate_control(self) -> str:
        return DEBIAN_CONTROL_TEMPLATE.format(
            name=self.pkg.name,
            maintainer=self.pkg.maintainer,
            email=self.pkg.email,
            url=self.pkg.url,
            description=self.pkg.description,
        )

    def generate_postinst(self) -> str:
        return """\
#!/bin/sh
set -e
# Создание директорий
mkdir -p /etc/lina
mkdir -p /var/lib/lina

# Установка прав
chmod 755 /usr/bin/lina

#DEBHELPER#
exit 0
"""

    def generate_rules(self) -> str:
        return """\
#!/usr/bin/make -f
%:
\tdh $@ --with python3 --buildsystem=pybuild
"""

    def validate(self, content: str) -> Dict[str, bool]:
        return {
            "has_source": "Source:" in content,
            "has_package": "Package:" in content,
            "has_depends": "Depends:" in content,
            "has_description": "Description:" in content,
        }


# ─── RPM (Fedora) ───────────────────────────────────────────────────────────

RPM_SPEC_TEMPLATE = """\
Name:           {name}
Version:        {version}
Release:        {release}%{{?dist}}
Summary:        {description}

License:        {license}
URL:            {url}
Source0:        %{{name}}-%{{version}}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel python3-pip
Requires:       python3 >= 3.10

%description
Lina — локальный ИИ-помощник для Linux.
Работает полностью оффлайн, используя LLM-модели.

%prep
%autosetup

%build
%py3_build

%install
%py3_install
install -Dm644 lina.service %{{buildroot}}%{{_unitdir}}/lina.service

%post
%systemd_post lina.service

%preun
%systemd_preun lina.service

%postun
%systemd_postun_with_restart lina.service

%files
%license LICENSE
%doc README.md
%{{_bindir}}/lina
%{{python3_sitelib}}/lina/
%{{python3_sitelib}}/lina-*.egg-info/
%{{_unitdir}}/lina.service
"""


class RPMGenerator:
    """Генератор .spec для Fedora RPM."""

    def __init__(self, pkg: Optional[PackageInfo] = None):
        self.pkg = pkg or PackageInfo()

    def generate_spec(self) -> str:
        return RPM_SPEC_TEMPLATE.format(
            name=self.pkg.name,
            version=self.pkg.version,
            release=self.pkg.release,
            description=self.pkg.description,
            url=self.pkg.url,
            license=self.pkg.license,
        )

    def validate(self, content: str) -> Dict[str, bool]:
        return {
            "has_name": "Name:" in content,
            "has_version": "Version:" in content,
            "has_requires": "Requires:" in content,
            "has_files": "%files" in content,
            "has_systemd": "systemd" in content,
        }


# ─── Фасад ──────────────────────────────────────────────────────────────────

class PackagingManager:
    """Единый интерфейс генерации файлов для пакетирования."""

    def __init__(self, pkg: Optional[PackageInfo] = None):
        self.pkg = pkg or PackageInfo()
        self.systemd = SystemdGenerator(self.pkg)
        self.pkgbuild = PKGBUILDGenerator(self.pkg)
        self.debian = DebianGenerator(self.pkg)
        self.rpm = RPMGenerator(self.pkg)

    def generate_all(self) -> Dict[str, str]:
        """Генерирует все файлы пакетирования."""
        return {
            "lina.service": self.systemd.generate_system_service(),
            "lina-user.service": self.systemd.generate_user_service(),
            "PKGBUILD": self.pkgbuild.generate(),
            "debian/control": self.debian.generate_control(),
            "debian/postinst": self.debian.generate_postinst(),
            "debian/rules": self.debian.generate_rules(),
            "lina.spec": self.rpm.generate_spec(),
        }

    def get_info(self) -> Dict:
        return {
            "package": self.pkg.name,
            "version": self.pkg.version,
            "formats": ["arch/PKGBUILD", "debian/deb", "fedora/rpm", "systemd"],
        }
