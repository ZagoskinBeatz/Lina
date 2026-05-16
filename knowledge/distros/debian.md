# Debian — Руководство

## Обзор

Debian — один из старейших дистрибутивов, основа для Ubuntu, Linux Mint и других. Известен стабильностью и огромным количеством пакетов (~59000).

## Ветки Debian

| Ветка | Описание |
| ------- | ---------- |
| **stable** | Основной релиз (Bookworm 12) |
| **testing** | Будущий stable (Trixie 13) |
| **unstable (sid)** | Rolling release, для разработчиков |
| **backports** | Новые пакеты для stable |

## Пакетный менеджер apt/dpkg

```bash
# Обновление
sudo apt update && sudo apt upgrade
sudo apt full-upgrade          # с учётом зависимостей

# Установка / удаление
sudo apt install <пакет>
sudo apt remove <пакет>
sudo apt purge <пакет>

# Поиск
apt search <запрос>
apt show <пакет>

# Низкоуровневые dpkg операции
sudo dpkg -i package.deb       # установить .deb
dpkg -l                        # список установленных
dpkg -L <пакет>               # файлы пакета
```

## Источники пакетов

Файл: `/etc/apt/sources.list`

```text
deb http://deb.debian.org/debian/ bookworm main contrib non-free non-free-firmware
deb http://security.debian.org/debian-security bookworm-security main
deb http://deb.debian.org/debian/ bookworm-updates main
deb http://deb.debian.org/debian/ bookworm-backports main
```

### Backports

```bash
# Установить пакет из backports
sudo apt -t bookworm-backports install <пакет>
```

## Обновление между версиями

```bash
# 1. Обновить текущую версию
sudo apt update && sudo apt full-upgrade

# 2. Изменить sources.list (bullseye → bookworm)
sudo sed -i 's/bullseye/bookworm/g' /etc/apt/sources.list

# 3. Обновить
sudo apt update
sudo apt full-upgrade
sudo reboot
```

## Частые проблемы

### "E: Unable to locate package"

```bash
# Проверить sources.list
cat /etc/apt/sources.list
# Обновить список
sudo apt update
# Проверить архитектуру
dpkg --print-architecture
```

### Зависимости не удовлетворены

```bash
sudo apt --fix-broken install
sudo dpkg --configure -a
sudo apt clean
sudo apt update
```
