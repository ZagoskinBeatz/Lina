# GRUB — загрузчик Linux

## Обзор
GRUB (GRand Unified Bootloader) — стандартный загрузчик для большинства дистрибутивов Linux.
GRUB2 (текущая версия) поддерживает UEFI, GPT, множество файловых систем и ядер.

## Конфигурация

### Основной файл: /etc/default/grub
```bash
# Время ожидания в меню (секунды)
GRUB_TIMEOUT=5
GRUB_TIMEOUT_STYLE=menu           # menu / countdown / hidden

# ОС по умолчанию (0 = первая)
GRUB_DEFAULT=0
# Или по имени:
GRUB_DEFAULT="Advanced options for Arch Linux>Arch Linux, with Linux linux"
# Или запомнить последний выбор:
GRUB_DEFAULT=saved
GRUB_SAVEDEFAULT=true

# Параметры ядра
GRUB_CMDLINE_LINUX=""              # для всех режимов
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"  # только для normal mode

# Разрешение
GRUB_GFXMODE=1920x1080x32
GRUB_GFXPAYLOAD_LINUX=keep        # передать разрешение ядру

# Отключить submenu
GRUB_DISABLE_SUBMENU=y

# Обнаружение других ОС
GRUB_DISABLE_OS_PROBER=false       # для dual-boot
```

### Применение изменений
```bash
# После редактирования /etc/default/grub:
sudo grub-mkconfig -o /boot/grub/grub.cfg

# На Fedora/RHEL:
sudo grub2-mkconfig -o /boot/grub2/grub.cfg
# UEFI:
sudo grub2-mkconfig -o /boot/efi/EFI/fedora/grub.cfg
```

## Установка GRUB

### UEFI
```bash
# Установить на EFI-раздел
sudo grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=GRUB

# Или (Arch):
sudo grub-install --target=x86_64-efi --efi-directory=/boot --bootloader-id=GRUB

# Проверить установку
efibootmgr -v                     # список EFI-записей
ls /boot/efi/EFI/                  # должен быть каталог GRUB
```

### Legacy BIOS (MBR)
```bash
sudo grub-install --target=i386-pc /dev/sda
# НЕ указывайте раздел (sda1), только диск (sda)
```

## Dual-boot (Windows + Linux)

### Обнаружение Windows
```bash
# Установить os-prober
sudo pacman -S os-prober           # Arch
sudo apt install os-prober         # Debian

# Включить os-prober в GRUB
# /etc/default/grub
GRUB_DISABLE_OS_PROBER=false

# Обновить конфигурацию
sudo os-prober                     # должен найти Windows
sudo grub-mkconfig -o /boot/grub/grub.cfg
```

### Ручная запись для Windows
```bash
# Если os-prober не находит, добавьте в /etc/grub.d/40_custom:
menuentry "Windows 11" {
    insmod part_gpt
    insmod fat
    insmod chain
    search --no-floppy --fs-uuid --set=root <EFI_PARTITION_UUID>
    chainloader /EFI/Microsoft/Boot/bootmgfw.efi
}
# UUID можно найти: sudo blkid | grep EFI
sudo grub-mkconfig -o /boot/grub/grub.cfg
```

## Параметры ядра (GRUB_CMDLINE_LINUX)

### Часто используемые
```bash
# Тихая загрузка
quiet splash                       # без лишнего вывода

# Гибернация
resume=UUID=<swap_uuid> resume_offset=<offset>

# IOMMU (для виртуализации с GPU passthrough)
intel_iommu=on iommu=pt           # Intel
amd_iommu=on iommu=pt            # AMD

# NVIDIA DRM
nvidia_drm.modeset=1

# Ограничить RAM (для тестирования)
mem=4G

# Безопасность
init_on_alloc=1 init_on_free=1 slab_nomerge
page_alloc.shuffle=1 randomize_kstack_offset=on

# Отладка
systemd.log_level=debug           # подробные логи systemd
rd.break                          # остановиться в initramfs
single                            # однопользовательский режим
nomodeset                         # без KMS (для проблем с видео)
```

## Восстановление GRUB

### Из Live USB
```bash
# 1. Загрузиться с Live USB

# 2. Монтировать разделы
sudo mount /dev/sda2 /mnt          # корневой раздел
sudo mount /dev/sda1 /mnt/boot/efi # EFI-раздел

# 3. Chroot
sudo arch-chroot /mnt              # Arch
# Или:
for dir in dev proc sys run; do sudo mount --bind /$dir /mnt/$dir; done
sudo chroot /mnt

# 4. Переустановить GRUB
grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=GRUB
grub-mkconfig -o /boot/grub/grub.cfg

# 5. Выйти и перезагрузиться
exit
sudo umount -R /mnt
reboot
```

### Из GRUB Rescue (если GRUB повреждён)
```bash
# Если видите grub rescue>:
set prefix=(hd0,gpt2)/boot/grub
set root=(hd0,gpt2)
insmod normal
normal

# Если grub>:
ls                                 # список разделов
ls (hd0,gpt2)/                    # проверить содержимое
set root=(hd0,gpt2)
linux /boot/vmlinuz-linux root=/dev/sda2
initrd /boot/initramfs-linux.img
boot
```

## Темы GRUB
```bash
# Установка темы
# Распакуйте тему в /boot/grub/themes/<theme_name>/

# /etc/default/grub
GRUB_THEME=/boot/grub/themes/mytheme/theme.txt

sudo grub-mkconfig -o /boot/grub/grub.cfg

# Популярные темы: Vimix, Stylish, CyberRe, Catppuccin
# Установка из AUR:
paru -S grub-theme-vimix
```

## Скрипты /etc/grub.d/
```
/etc/grub.d/
├── 00_header          # заголовок конфигурации
├── 10_linux           # записи Linux
├── 20_linux_xen       # Xen
├── 30_os-prober       # другие ОС (Windows)
├── 40_custom          # пользовательские записи
└── 41_custom          # ещё пользовательские
```

```bash
# Сделать скрипт исполняемым/неисполняемым
sudo chmod +x /etc/grub.d/30_os-prober    # включить
sudo chmod -x /etc/grub.d/30_os-prober    # отключить

# Всегда после изменений:
sudo grub-mkconfig -o /boot/grub/grub.cfg
```

## Альтернативы GRUB
| Загрузчик | Описание |
|-----------|----------|
| systemd-boot | Простой, только UEFI, быстрый |
| rEFInd | Красивый, авто-обнаружение ядер |
| EFISTUB | Загрузка ядра напрямую из UEFI |
| Syslinux | Лёгкий, legacy BIOS |
| LILO | Устаревший |

### systemd-boot (альтернатива)
```bash
# Установка
sudo bootctl install

# Конфигурация /boot/loader/loader.conf
default arch
timeout 5
editor no

# Запись ядра /boot/loader/entries/arch.conf
title Arch Linux
linux /vmlinuz-linux
initrd /initramfs-linux.img
options root=UUID=<root_uuid> rw quiet

# Обновление
sudo bootctl update
```

## Частые проблемы
1. **GRUB не видит Windows** — `GRUB_DISABLE_OS_PROBER=false`, установить os-prober
2. **Error: unknown filesystem** — повреждён GRUB, переустановить из Live USB
3. **Minimal BASH-like line editing** — неверный prefix, исправить вручную
4. **Чёрный экран после GRUB** — добавить `nomodeset` в параметры ядра
5. **GRUB замедляет загрузку** — уменьшить GRUB_TIMEOUT или перейти на systemd-boot
6. **Появился GRUB после обновления Windows** — Windows перезаписал EFI, переустановить GRUB
