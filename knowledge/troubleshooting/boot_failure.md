# Система не загружается — диагностика и восстановление

## Этапы загрузки Linux

```
BIOS/UEFI → GRUB (bootloader) → Ядро (vmlinuz) → initramfs → systemd (PID 1) → Цели (targets) → Login
```

Проблема может быть на любом этапе. Определяем где остановилась загрузка.

## Симптомы и диагностика

### Ничего не появляется / нет BIOS
- Проблема с железом (БП, RAM, видеокарта)
- Проверить POST-индикаторы материнской платы
- Попробовать вытащить RAM и вставить заново

### Появляется BIOS, но нет GRUB
```
"No bootable device" / "Boot device not found"
```
**Причины:**
- Неверный порядок загрузки в BIOS
- Повреждён загрузчик GRUB
- Диск не найден / отключён

**Решение:**
```bash
# 1. Зайти в BIOS (DEL / F2 / F12 / Esc)
# 2. Проверить порядок загрузки → поставить Linux-диск первым
# 3. Для UEFI: убедиться что режим Secure Boot = Off (или подписанный загрузчик)
```

### GRUB появляется, но ядро не грузится
```
error: file '/vmlinuz-linux' not found
```
**Решение из GRUB CLI:**
```bash
# В GRUB нажать 'c' для командной строки
grub> ls                          # Список разделов
grub> ls (hd0,gpt2)/             # Найти корневой раздел
grub> ls (hd0,gpt2)/boot/        # Найти ядро

grub> set root=(hd0,gpt2)
grub> linux /boot/vmlinuz-linux root=/dev/sda2
grub> initrd /boot/initramfs-linux.img
grub> boot
```

### Kernel panic / initramfs emergency
```
"Kernel panic - not syncing"
"Failed to mount root filesystem"
"You are now being dropped into an emergency shell"
```
**Причины:**
- initramfs не содержит нужных модулей
- Раздел root указан неверно
- Файловая система повреждена

**Решение:**
```bash
# Загрузиться с Live USB
# Определить разделы:
lsblk -f

# Смонтировать корень:
sudo mount /dev/sda2 /mnt
# Если UEFI:
sudo mount /dev/sda1 /mnt/boot/efi

# Arch: chroot
sudo arch-chroot /mnt
mkinitcpio -P                     # Пересобрать initramfs

# Ubuntu/Debian:
sudo mount --bind /dev /mnt/dev
sudo mount --bind /proc /mnt/proc
sudo mount --bind /sys /mnt/sys
sudo chroot /mnt
update-initramfs -u -k all
```

### systemd не запускается / зависает на сервисе
```
"A start job is running for..."
"Failed to start..."
```
**Решение:**
```bash
# Загрузиться в emergency mode:
# В GRUB: нажать 'e', добавить к строке linux:
systemd.unit=emergency.target
# Затем Ctrl+X для загрузки

# Внутри emergency:
journalctl -xb                    # Все логи текущей загрузки
systemctl list-units --failed     # Неудачные сервисы
systemctl disable <проблемный_сервис>

# Или single-user mode:
# В GRUB добавить: single или 1
```

## Восстановление GRUB

### Arch Linux
```bash
# С Live USB:
sudo mount /dev/sda2 /mnt        # Корневой раздел
sudo mount /dev/sda1 /mnt/boot/efi  # EFI раздел (UEFI)
sudo arch-chroot /mnt

# Переустановить GRUB (UEFI):
grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=GRUB
grub-mkconfig -o /boot/grub/grub.cfg

# Переустановить GRUB (BIOS/MBR):
grub-install --target=i386-pc /dev/sda
grub-mkconfig -o /boot/grub/grub.cfg

exit
sudo umount -R /mnt
sudo reboot
```

### Ubuntu / Debian
```bash
# С Live USB:
sudo mount /dev/sda2 /mnt
sudo mount /dev/sda1 /mnt/boot/efi
sudo mount --bind /dev /mnt/dev
sudo mount --bind /proc /mnt/proc
sudo mount --bind /sys /mnt/sys
sudo mount --bind /run /mnt/run
sudo chroot /mnt

grub-install /dev/sda             # BIOS
# или
grub-install --target=x86_64-efi --efi-directory=/boot/efi  # UEFI
update-grub

exit
sudo umount -R /mnt
sudo reboot
```

### boot-repair (Ubuntu — автоматический)
```bash
# С Live USB:
sudo add-apt-repository ppa:yannubuntu/boot-repair
sudo apt update
sudo apt install boot-repair
boot-repair
# → "Рекомендуемое восстановление"
```

## Файловая система повреждена

```bash
# С Live USB:
sudo fsck /dev/sda2              # Проверка и исправление
sudo fsck -y /dev/sda2           # Автоматическое исправление
sudo fsck.ext4 -f /dev/sda2     # Принудительная проверка ext4
sudo btrfs check /dev/sda2      # btrfs

# НИКОГДА не запускать fsck на смонтированном разделе!
```

## Проблемы с fstab

```bash
# Неправильный fstab → система не загрузится
# Симптом: "dependency failed for /home" или зависание при монтировании

# С Live USB или emergency mode:
sudo mount /dev/sda2 /mnt
nano /mnt/etc/fstab

# Проверить UUID:
blkid /dev/sda*
# Сравнить UUID в fstab с реальными

# Совет: добавить nofail к некритичным разделам
# /dev/sdb1  /data  ext4  defaults,nofail  0 2
```

## Чёрный экран после обновления ядра

```bash
# В GRUB → Advanced options → выбрать предыдущее ядро
# Если работает — удалить проблемное ядро или подождать патч

# Arch:
sudo pacman -U /var/cache/pacman/pkg/linux-<старая_версия>.pkg.tar.zst

# Ubuntu:
sudo apt install linux-image-<старая_версия>-generic
```

## Dual-boot: Windows перезаписал загрузчик

```bash
# Симптом: после обновления Windows Linux не грузится

# 1. Загрузиться с Live USB
# 2. Восстановить GRUB (см. выше)
# 3. Или через UEFI в BIOS выбрать загрузочную запись Linux
```

## Полезные параметры ядра (в GRUB → 'e')

```
nomodeset                    # Отключить графический драйвер (чёрный экран)
acpi=off                     # Отключить ACPI (зависание при загрузке)
nouveau.modeset=0            # Отключить nouveau (для установки NVIDIA)
nvidia-drm.modeset=1         # Включить modesetting NVIDIA
systemd.unit=multi-user.target  # Загрузка без GUI
systemd.unit=rescue.target   # Режим восстановления
init=/bin/bash               # Загрузить только bash (экстренный доступ)
```
