# Fedora — Руководство

## Обзор

Fedora — community-дистрибутив, спонсируемый Red Hat. Использует самые свежие технологии (Wayland, PipeWire, Btrfs по умолчанию). Выходит каждые ~6 месяцев.

## Пакетный менеджер dnf

### Основные команды

```bash
# Обновление системы
sudo dnf upgrade              # обновить всё
sudo dnf upgrade --refresh    # с обновлением метаданных

# Установка / удаление
sudo dnf install <пакет>
sudo dnf remove <пакет>
sudo dnf autoremove            # неиспользуемые зависимости

# Поиск и информация
dnf search <запрос>
dnf info <пакет>
dnf list installed
dnf provides /путь/к/файлу

# Группы
dnf group list
sudo dnf group install "Development Tools"

# История
dnf history
sudo dnf history undo <ID>    # откат операции
```

### DNF5 (Fedora 41+)

DNF5 — переписан на C++ для скорости:

```bash
# Синтаксис тот же, но быстрее
sudo dnf5 install <пакет>
sudo dnf5 upgrade
```

### COPR (Community Repositories)

```bash
# Аналог PPA для Fedora
sudo dnf copr enable user/project
sudo dnf install <пакет>
```

## RPM Fusion (дополнительные репо)

```bash
# Установка RPM Fusion (free + nonfree)
sudo dnf install \
  https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
  https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm

# После этого доступны: nvidia-driver, steam, vlc, и т.д.
sudo dnf install nvidia-driver
sudo dnf install steam
```

## Обновление между версиями

```bash
# Обновление Fedora (например, 40 → 41)
sudo dnf upgrade --refresh
sudo dnf install dnf-plugin-system-upgrade
sudo dnf system-upgrade download --releasever=41
sudo dnf system-upgrade reboot
```

## Fedora Silverblue / Kinoite

Immutable-варианты Fedora:

- **Silverblue** — GNOME, неизменяемая корневая ФС
- **Kinoite** — KDE Plasma, неизменяемая корневая ФС

```bash
# Управляется через rpm-ostree
rpm-ostree install <пакет>
rpm-ostree upgrade
rpm-ostree rollback            # откат на предыдущую версию
```

## Частые проблемы Fedora

### Нет мультимедиа кодеков

```bash
# После установки RPM Fusion:
sudo dnf install gstreamer1-plugins-{bad-*,good-*,base} \
  gstreamer1-plugin-openh264 gstreamer1-libav \
  --exclude=gstreamer1-plugins-bad-free-devel
sudo dnf group upgrade --with-optional Multimedia
```

### SELinux блокирует приложение

```bash
# Просмотр блокировок
sudo ausearch -m avc -ts recent
# Создать разрешающий модуль
sudo audit2allow -a -M mymodule
sudo semodule -i mymodule.pp
# Временно отключить (не рекомендуется)
sudo setenforce 0
```
