# Arch Linux — Полное руководство

## Философия Arch Linux

Arch Linux — минималистичный, rolling-release дистрибутив, следующий принципу KISS (Keep It Simple, Stupid). Пользователь сам решает, что устанавливать.

## Пакетный менеджер pacman

### Основные команды

```bash
# Обновление системы
sudo pacman -Syu

# Установка пакета
sudo pacman -S <пакет>

# Удаление пакета (с зависимостями)
sudo pacman -Rns <пакет>

# Поиск пакета
pacman -Ss <запрос>

# Информация о пакете
pacman -Si <пакет>        # из репозитория
pacman -Qi <пакет>        # установленный

# Список установленных пакетов
pacman -Q                  # все
pacman -Qe                 # явно установленные
pacman -Qm                 # из AUR (foreign)

# Список файлов пакета
pacman -Ql <пакет>

# Какому пакету принадлежит файл
pacman -Qo /путь/к/файлу

# Очистка кэша
sudo pacman -Sc            # старые версии
sudo pacman -Scc           # весь кэш

# Неиспользуемые зависимости (orphans)
pacman -Qtdq               # список
sudo pacman -Rns $(pacman -Qtdq)  # удалить
```

### Конфигурация pacman

Файл: `/etc/pacman.conf`

```ini
# Полезные опции
[options]
Color                    # Цветной вывод
ParallelDownloads = 5    # Параллельная загрузка
ILoveCandy               # Прогересс-бар Pac-Man

# Мультибиблиотека (для 32-bit приложений, Steam)
[multilib]
Include = /etc/pacman.d/mirrorlist
```

### Зеркала (mirrorlist)

```bash
# Обновить список зеркал (reflector)
sudo reflector --country Russia,Germany,France \
    --protocol https --sort rate --save /etc/pacman.d/mirrorlist

# Вручную — редактировать /etc/pacman.d/mirrorlist
```

### Hooks (хуки pacman)

Хуки выполняются автоматически после установки/обновления.

```bash
# Системные хуки
ls /usr/share/libalpm/scripts/

# Пользовательские хуки
ls /etc/pacman.d/hooks/
```

## AUR (Arch User Repository)

AUR — репозиторий пользовательских пакетов. **Не проверяется** официально — всегда проверяйте PKGBUILD перед установкой.

### AUR-хелперы

```bash
# yay (рекомендуется)
yay -S <пакет>            # установка из AUR
yay -Syu                  # обновить всё (pacman + AUR)
yay -Ss <запрос>          # поиск (pacman + AUR)

# paru (альтернатива, на Rust)
paru -S <пакет>
paru -Syu
```

### Установка yay

```bash
sudo pacman -S --needed base-devel git
git clone https://aur.archlinux.org/yay-bin.git
cd yay-bin
makepkg -si
```

### Ручная установка из AUR

```bash
git clone https://aur.archlinux.org/<пакет>.git
cd <пакет>
cat PKGBUILD              # ОБЯЗАТЕЛЬНО проверить!
makepkg -si
```

## Установка Arch Linux

### Краткий чеклист

1. Загрузиться с ISO
2. Проверить UEFI: `ls /sys/firmware/efi/efivars`
3. Подключить интернет: `iwctl` для Wi-Fi
4. Время: `timedatectl set-ntp true`
5. Разметка диска: `fdisk` или `cfdisk`
6. Форматирование: `mkfs.ext4`, `mkfs.fat -F32`
7. Монтирование: `mount /dev/sdXn /mnt`
8. Установка: `pacstrap -K /mnt base linux linux-firmware`
9. fstab: `genfstab -U /mnt >> /mnt/etc/fstab`
10. Chroot: `arch-chroot /mnt`
11. Настройка: locale, timezone, hostname, пароль root
12. Загрузчик: `pacman -S grub efibootmgr && grub-install && grub-mkconfig`
13. Выход и перезагрузка

### Минимальный набор после установки

```bash
# Базовые утилиты
sudo pacman -S networkmanager sudo nano vim
sudo systemctl enable NetworkManager

# Создать пользователя
useradd -m -G wheel <user>
passwd <user>
EDITOR=nano visudo  # Раскомментировать %wheel ALL=(ALL) ALL

# Рабочий стол (KDE Plasma)
sudo pacman -S plasma-meta kde-applications-meta sddm
sudo systemctl enable sddm

# Драйверы GPU
# NVIDIA:
sudo pacman -S nvidia nvidia-utils
# AMD:
sudo pacman -S mesa vulkan-radeon
# Intel:
sudo pacman -S mesa vulkan-intel
```

## Частые проблемы Arch

### Ошибки ключей при обновлении

```bash
# Обновить связку ключей
sudo pacman -Sy archlinux-keyring
sudo pacman -Syu

# Если не помогло — полный сброс
sudo rm -rf /etc/pacman.d/gnupg
sudo pacman-key --init
sudo pacman-key --populate archlinux
```

### Конфликт файлов при обновлении

```bash
# Ошибка: "file exists in filesystem"
# 1. Проверить чей файл
pacman -Qo /путь/к/файлу

# 2. Если ничей — удалить и повторить
sudo rm /путь/к/файлу
sudo pacman -Syu

# 3. Или принудительная перезапись
sudo pacman -Syu --overwrite '/путь/*'
```

### Откат (downgrade) пакета

```bash
# Из кэша pacman
sudo pacman -U /var/cache/pacman/pkg/<пакет>-<версия>.pkg.tar.zst

# Через downgrade утилиту
yay -S downgrade
sudo downgrade <пакет>
```

### Сломанная система после обновления

```bash
# 1. Загрузиться с Live USB
# 2. Монтировать систему
mount /dev/sdXn /mnt
mount /dev/sdXn /mnt/boot  # если отдельный раздел
# 3. Chroot
arch-chroot /mnt
# 4. Откатить проблемные пакеты
pacman -U /var/cache/pacman/pkg/<пакет>-<старая_версия>.pkg.tar.zst
```
