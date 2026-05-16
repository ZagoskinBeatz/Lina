# CachyOS — Оптимизированный Arch Linux

## Что такое CachyOS

CachyOS — дистрибутив на базе Arch Linux с фокусом на производительность. Использует оптимизированные ядра, собранные с x86-64-v3/v4 инструкциями, кастомный репозиторий с пересобранными пакетами и удобный установщик.

## Ключевые особенности

- **Оптимизированные ядра**: linux-cachyos (sched-ext, BORE scheduler)
- **Скомпилированные пакеты**: x86-64-v3 (AVX2) оптимизации
- **Кастомный репозиторий**: cachyos, cachyos-v3, cachyos-v4
- **Установщик**: графический (Calamares) и CLI
- **KDE Plasma по умолчанию** (также GNOME, Xfce, Hyprland, i3)

## Пакетный менеджер

CachyOS использует стандартный pacman + AUR. Всё из руководства Arch Linux применимо.

```bash
# Обновление системы
sudo pacman -Syu

# CachyOS-специфичные пакеты
pacman -Ss cachyos          # поиск в cachyos repo

# Ядра CachyOS
sudo pacman -S linux-cachyos linux-cachyos-headers
sudo pacman -S linux-cachyos-lts linux-cachyos-lts-headers  # LTS
```

## Ядра CachyOS

### Доступные ядра

| Ядро | Описание |
| ------ | ---------- |
| linux-cachyos | Основное, BORE scheduler, sched-ext |
| linux-cachyos-lts | LTS ядро с оптимизациями |
| linux-cachyos-bore | С BORE scheduler |
| linux-cachyos-hardened | Усиленная безопасность |
| linux-cachyos-rt | Real-time ядро |

### Переключение ядра

```bash
# Установить новое ядро
sudo pacman -S linux-cachyos linux-cachyos-headers

# Обновить GRUB
sudo grub-mkconfig -o /boot/grub/grub.cfg

# Перезагрузиться и выбрать ядро в GRUB
```

### Проверка текущего ядра

```bash
uname -r                    # Версия ядра
cat /proc/cmdline           # Параметры загрузки
```

## Репозитории CachyOS

### Конфигурация в /etc/pacman.conf

```ini
# CachyOS основной репозиторий
[cachyos]
Include = /etc/pacman.d/cachyos-mirrorlist

# CachyOS v3 (для процессоров с AVX2+)
[cachyos-v3]
Include = /etc/pacman.d/cachyos-v3-mirrorlist

# CachyOS v4 (для процессоров с AVX-512)
[cachyos-v4]
Include = /etc/pacman.d/cachyos-v4-mirrorlist
```

### Проверка поддержки v3/v4

```bash
/lib/ld-linux-x86-64.so.2 --help 2>&1 | grep v3
# Если есть "(supported, searched)" — процессор поддерживает v3
```

## CachyOS Settings Manager

```bash
# Утилита настройки CachyOS
cachyos-settings

# Или через GUI
cachyos-hello              # Приветственное окно с утилитами
```

## Оптимизации по умолчанию

- **zram** вместо swap-файла (сжатый RAM как swap)
- **BORE scheduler** — оптимизированный планировщик задач
- **Profile-Guided Optimization (PGO)** — часть пакетов собрана с PGO
- **MGLRU** — Multi-Gen LRU для лучшего управления памятью
- **BBR3** — сетевой congestion control

## Проблемы и решения

### Обновление сломало драйверы NVIDIA

```bash
# CachyOS nvidia-utils может конфликтовать с ядром
# Переустановить NVIDIA пакеты после обновления ядра
sudo pacman -S nvidia-utils nvidia linux-cachyos-nvidia
sudo mkinitcpio -P
sudo grub-mkconfig -o /boot/grub/grub.cfg
```

### Переход с обычного Arch на CachyOS ядро

```bash
# Установить CachyOS keyring и mirrorlist
sudo pacman-key --recv-keys F3B607488DB35A47
sudo pacman-key --lsign-key F3B607488DB35A47
sudo pacman -S cachyos-keyring cachyos-mirrorlist

# Добавить [cachyos] в /etc/pacman.conf
# Затем:
sudo pacman -Syu linux-cachyos linux-cachyos-headers
```
