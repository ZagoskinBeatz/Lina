# CachyOS / Arch Linux — специфика

## CachyOS
CachyOS — оптимизированный дистрибутив на базе Arch Linux с:
- Ядро с оптимизациями (x86-64-v3/v4, BORE scheduler)
- Предварительно настроенный ZSTD-сжатие
- Оптимизированные пакеты с LTO, PGO
- Calamares-установщик
- cachyos-rate-mirrors для быстрых зеркал

## Репозитории CachyOS
```bash
# /etc/pacman.conf
[cachyos]
Include = /etc/pacman.d/cachyos-mirrorlist

# Ядра CachyOS
sudo pacman -S linux-cachyos        # BORE scheduler
sudo pacman -S linux-cachyos-rc     # release candidate
sudo pacman -S linux-cachyos-bore   # BORE + sched_ext
```

## Arch Linux — основные операции

### Pacman
```bash
# Обновление системы
sudo pacman -Syu

# Установка пакета
sudo pacman -S <пакет>

# Поиск пакета
pacman -Ss <запрос>

# Информация о пакете
pacman -Si <пакет>       # из репозитория
pacman -Qi <пакет>       # установленный

# Удаление
sudo pacman -R <пакет>           # только пакет
sudo pacman -Rs <пакет>          # + неиспользуемые зависимости
sudo pacman -Rns <пакет>         # + конфиги

# Список файлов пакета
pacman -Ql <пакет>

# Какому пакету принадлежит файл
pacman -Qo /path/to/file

# Очистка кэша
sudo pacman -Sc     # старые версии
sudo pacman -Scc    # весь кэш

# Список осиротевших пакетов
pacman -Qdt
sudo pacman -Rns $(pacman -Qdtq)   # удалить сироты
```

### AUR (Arch User Repository)
```bash
# Через yay (AUR helper)
yay -S <пакет>          # установить из AUR
yay -Sua                # обновить AUR-пакеты
yay -Ss <запрос>        # поиск (включая AUR)

# Через paru (альтернатива)
paru -S <пакет>
paru -Sua

# Ручная сборка
git clone https://aur.archlinux.org/<пакет>.git
cd <пакет>
makepkg -sri
```

### Зеркала
```bash
# Обновление зеркал
sudo reflector --country Russia,Germany --latest 20 --sort rate \
  --save /etc/pacman.d/mirrorlist

# CachyOS
sudo cachyos-rate-mirrors
```

### Systemd
```bash
# Управление сервисами
sudo systemctl enable <сервис>    # автозапуск
sudo systemctl start <сервис>     # запуск
sudo systemctl status <сервис>    # статус
sudo systemctl restart <сервис>   # перезапуск
sudo systemctl disable <сервис>   # отключить

# Логи
journalctl -u <сервис>           # логи сервиса
journalctl -b                    # текущая загрузка
journalctl -b -1                 # прошлая загрузка
journalctl --since "1 hour ago"  # за последний час
```

### Mkinitcpio
```bash
# После обновления модулей ядра
sudo mkinitcpio -P     # пересборка для всех ядер

# Конфигурация: /etc/mkinitcpio.conf
# HOOKS=(base udev autodetect modconf block filesystems keyboard fsck)
```

## Советы
1. Всегда делайте `pacman -Syu` перед установкой нового пакета
2. Читайте Arch Wiki — лучшая документация Linux
3. Подпишитесь на рассылку arch-announce для критических обновлений
4. Используйте timeshift/snapper для BTRFS-снимков перед обновлением
5. Не игнорируйте `.pacnew`/`.pacsave` файлы

## CachyOS — расширенная информация

### Ядра CachyOS
| Ядро | Описание |
|------|----------|
| linux-cachyos | Основное, BORE scheduler, оптимизации |
| linux-cachyos-bore | BORE scheduler для интерактивности |
| linux-cachyos-hardened | Усиленная безопасность |
| linux-cachyos-rt | Реальное время (низкая задержка) |
| linux-cachyos-lts | Долгосрочная поддержка |

```bash
# Список установленных ядер
pacman -Q | grep linux-cachyos

# Установить другое ядро
sudo pacman -S linux-cachyos-bore linux-cachyos-bore-headers
sudo grub-mkconfig -o /boot/grub/grub.cfg

# Сравнить schedulers
# BORE — для десктопа (интерактивность)
# EEVDF — стандартный Linux 6.6+
# PDS — Process Data Sharing (CachyOS специфичный)
```

### CachyOS Settings Manager
```bash
# Графическая утилита для настройки
cachyos-settings                 # или из меню

# Что умеет:
# - Выбор ядра
# - Настройка зеркал
# - Настройка GPU-драйверов
# - Оптимизация производительности
# - Настройка boot-loader
```

### Репозитории CachyOS
```bash
# /etc/pacman.conf
[cachyos-v3]                    # x86-64-v3 оптимизация (AVX2+)
[cachyos-core-v3]
[cachyos-extra-v3]
[cachyos]                       # базовый репо

# Проверить поддержку v3
/lib/ld-linux-x86-64.so.2 --help | grep v3
# Если есть "x86-64-v3 (supported)" — можно использовать v3
```

## Arch Linux — продвинутые темы

### Pacman — расширенные операции
```bash
# Список сирот (неиспользуемых зависимостей)
pacman -Qdtq

# Удалить сироты
sudo pacman -Rns $(pacman -Qdtq)

# Список файлов пакета
pacman -Ql <package>

# Найти владельца файла
pacman -Qo /usr/bin/python

# Список явно установленных пакетов
pacman -Qe

# Размер пакетов (сортировка по размеру)
pacman -Qi | awk '/^Name/{name=$3} /^Installed Size/{print $4, $5, name}' | sort -rh | head -20

# Кэш пакетов
sudo paccache -r          # оставить 3 последних версии
sudo paccache -rk1         # оставить только 1 версию
sudo paccache -ruk0        # удалить все неустановленные

# Параллельная загрузка
# /etc/pacman.conf
ParallelDownloads = 5
Color
VerbosePkgLists
```

### AUR — продвинутое использование
```bash
# Ручная установка из AUR
git clone https://aur.archlinux.org/<package>.git
cd <package>
makepkg -si

# Проверка PKGBUILD перед сборкой (безопасность!)
less PKGBUILD

# AUR-хелперы
# paru (рекомендуется CachyOS)
paru -S <package>          # поиск + установка
paru -Sua                  # обновить все AUR-пакеты
paru -Gc <package>         # показать комментарии AUR
paru --gendb               # создать базу для обнаружения -git пакетов

# yay
yay -S <package>
yay -Sua

# Devtools — чистая сборка в chroot
extra-x86_64-build         # сборка в чистом окружении
```

### Зеркала и reflector
```bash
# Автоматическое обновление зеркал
sudo reflector --country Russia,Germany --protocol https --sort rate --save /etc/pacman.d/mirrorlist

# CachyOS имеет свой cachyos-rate-mirrors
sudo cachyos-rate-mirrors

# Таймер для автообновления зеркал
sudo systemctl enable --now reflector.timer
```

### Ключи и подписи
```bash
# Инициализация ключей
sudo pacman-key --init
sudo pacman-key --populate archlinux cachyos

# Обновление ключей (при ошибках подписи)
sudo pacman-key --refresh-keys

# Импорт ключа вручную
sudo pacman-key --recv-keys <KEYID> --keyserver keyserver.ubuntu.com
sudo pacman-key --lsign-key <KEYID>
```

### Pacnew и Pacsave
```bash
# Найти все .pacnew файлы
sudo find /etc -name "*.pacnew" 2>/dev/null

# Сравнить и объединить (pacdiff)
sudo pacdiff                   # интерактивный diff

# Или вручную
diff /etc/ssh/sshd_config /etc/ssh/sshd_config.pacnew
sudo mv /etc/ssh/sshd_config.pacnew /etc/ssh/sshd_config
```

### Восстановление после неудачного обновления
```bash
# Если система не загружается после pacman -Syu:
# 1. Загрузиться с Live USB
# 2. arch-chroot:
mount /dev/sda2 /mnt
mount /dev/sda1 /mnt/boot
arch-chroot /mnt

# 3. Даунгрейд пакета
pacman -U /var/cache/pacman/pkg/<package>-<old_version>.pkg.tar.zst

# Или через downgrade
sudo downgrade <package>       # AUR: downgrade

# 4. Пересобрать initramfs
mkinitcpio -P

# 5. Обновить загрузчик
grub-mkconfig -o /boot/grub/grub.cfg
```

### Makepkg оптимизация
```bash
# /etc/makepkg.conf
CFLAGS="-march=native -O2 -pipe"
CXXFLAGS="$CFLAGS"
MAKEFLAGS="-j$(nproc)"        # параллельная компиляция
COMPRESSZST=(zstd -c -T0 -)   # многопоточное сжатие

# Компиляция в tmpfs (RAM)
BUILDDIR=/tmp/makepkg
```
