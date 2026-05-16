# Восстановление системы Linux

## Live USB — главный инструмент восстановления

Загрузочный USB-накопитель с Linux — основное средство для восстановления
незагружающейся системы. Рекомендуется всегда иметь под рукой.

### Создание загрузочного USB

```bash
# Из Linux
# ОСТОРОЖНО: /dev/sdX — это USB-флешка, НЕ системный диск!

# dd (универсальный)
sudo dd if=archlinux.iso of=/dev/sdX bs=4M status=progress oflag=sync

# Ventoy — мультизагрузочный USB
# Устанавливается один раз, затем просто копируете ISO на флешку
wget https://github.com/ventoy/Ventoy/releases/download/v1.0.99/ventoy-1.0.99-linux.tar.gz
tar xzf ventoy-*.tar.gz && cd ventoy-*
sudo sh Ventoy2Disk.sh -i /dev/sdX
# Копировать любые ISO на раздел Ventoy:
cp archlinux.iso ubuntu.iso /mnt/ventoy/

# balenaEtcher (GUI)
# https://etcher.balena.io/
```

### Загрузка с USB

```
1. Вставить USB в компьютер
2. Перезагрузить, нажать F12 / F8 / Esc (зависит от BIOS)
3. Выбрать USB-устройство
4. Для UEFI: выбрать запись с "UEFI:" в начале
5. Если Secure Boot мешает → отключить в BIOS (F2/Del)
```

## Монтирование корневого раздела

### Определение разделов

```bash
# Показать все диски и разделы
lsblk -f
# Пример:
# sda
# ├─sda1  vfat    EFI      /boot/efi
# ├─sda2  ext4    ROOT     /
# └─sda3  ext4    HOME     /home

# Или
fdisk -l
blkid
```

### Монтирование (стандартная структура)

```bash
# Корневая ФС
sudo mount /dev/sda2 /mnt

# Отдельный /home (если есть)
sudo mount /dev/sda3 /mnt/home

# EFI раздел (UEFI-системы)
sudo mount /dev/sda1 /mnt/boot/efi
# Или (Arch по умолчанию):
sudo mount /dev/sda1 /mnt/boot

# Если btrfs с subvolumes:
sudo mount -o subvol=@ /dev/sda2 /mnt
sudo mount -o subvol=@home /dev/sda2 /mnt/home
sudo mount /dev/sda1 /mnt/boot/efi
```

### Монтирование зашифрованного раздела (LUKS)

```bash
# Открыть зашифрованный раздел
sudo cryptsetup open /dev/sda2 cryptroot
# Ввести пароль

# Монтировать
sudo mount /dev/mapper/cryptroot /mnt
```

## chroot — работа внутри установленной системы

### Arch Linux (arch-chroot)

```bash
# arch-chroot автоматически монтирует /proc, /sys, /dev
sudo arch-chroot /mnt

# Теперь вы "внутри" установленной системы
# Можно выполнять pacman, systemctl, mkinitcpio и т.д.

# Выход
exit
```

### Универсальный chroot (любой дистрибутив)

```bash
# Монтировать виртуальные ФС
sudo mount --bind /dev /mnt/dev
sudo mount --bind /dev/pts /mnt/dev/pts
sudo mount --bind /proc /mnt/proc
sudo mount --bind /sys /mnt/sys
sudo mount --bind /run /mnt/run

# Для DNS-резолвинга внутри chroot:
sudo cp /etc/resolv.conf /mnt/etc/resolv.conf

# Войти
sudo chroot /mnt /bin/bash

# Выход
exit

# Размонтировать всё
sudo umount -R /mnt
```

## Типичные задачи восстановления

### Восстановление загрузчика GRUB

```bash
# UEFI (Arch):
sudo arch-chroot /mnt
grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=GRUB
grub-mkconfig -o /boot/grub/grub.cfg
exit

# BIOS/MBR (Arch):
sudo arch-chroot /mnt
grub-install --target=i386-pc /dev/sda
grub-mkconfig -o /boot/grub/grub.cfg
exit

# Ubuntu/Debian (UEFI):
sudo chroot /mnt
grub-install --target=x86_64-efi --efi-directory=/boot/efi
update-grub
exit

# Альтернатива: boot-repair (Ubuntu)
sudo add-apt-repository ppa:yannubuntu/boot-repair
sudo apt update && sudo apt install boot-repair
boot-repair
```

### Пересборка initramfs

```bash
# Arch
sudo arch-chroot /mnt
mkinitcpio -P                    # Все пресеты
mkinitcpio -p linux              # Только linux пресет
exit

# Ubuntu/Debian
sudo chroot /mnt
update-initramfs -u -k all
exit

# Fedora
sudo chroot /mnt
dracut --force
exit
```

### Сброс пароля root

```bash
# Из Live USB:
sudo mount /dev/sda2 /mnt
sudo arch-chroot /mnt            # или chroot
passwd                           # Установить новый пароль root
passwd username                  # Или пароль пользователя
exit
sudo umount -R /mnt
sudo reboot
```

### Исправление fstab

```bash
# Неправильный fstab → система не загрузится
sudo mount /dev/sda2 /mnt
nano /mnt/etc/fstab

# Проверить UUID:
blkid
# Убедиться что UUID в fstab соответствуют реальным

# Совет: добавить nofail для некритичных разделов
# UUID=xxx  /data  ext4  defaults,nofail  0  2
```

### Откат обновления ядра

```bash
# Arch
sudo arch-chroot /mnt
# Список доступных ядер в кэше:
ls /var/cache/pacman/pkg/linux-*.pkg.tar.zst
# Установить предыдущую версию:
pacman -U /var/cache/pacman/pkg/linux-6.8.1.arch1-1-x86_64.pkg.tar.zst
mkinitcpio -P
exit

# Fedora
sudo chroot /mnt
# Список установленных ядер:
rpm -qa kernel
dnf install kernel-6.7.9-200.fc39
exit
```

### Удаление проблемного сервиса

```bash
sudo arch-chroot /mnt
systemctl disable problematic.service
# Или полная маскировка:
systemctl mask problematic.service
exit
```

## Восстановление файловой системы

### ext4

```bash
# Проверка (ТОЛЬКО на размонтированном разделе!)
sudo e2fsck -f /dev/sda2
sudo e2fsck -fy /dev/sda2       # Автоматическое исправление

# Если ошибка "superblock invalid":
sudo e2fsck -b 32768 /dev/sda2  # Использовать резервный суперблок

# Посмотреть расположение резервных суперблоков:
sudo mke2fs -n /dev/sda2
```

### btrfs

```bash
# Проверка (ТОЛЬКО на размонтированном!)
sudo btrfs check /dev/sda2

# Исправление (ОСТОРОЖНО):
sudo btrfs check --repair /dev/sda2

# Восстановление из снапшота:
sudo mount /dev/sda2 /mnt
ls /mnt/@snapshots/               # Список снапшотов
# Заменить корневой subvolume:
sudo mv /mnt/@ /mnt/@.broken
sudo btrfs subvolume snapshot /mnt/@snapshots/42/snapshot /mnt/@
sudo umount /mnt
sudo reboot
```

### XFS

```bash
sudo xfs_repair /dev/sda2
# Если не работает:
sudo xfs_repair -L /dev/sda2    # С потерей журнала (последнее средство)
```

## Восстановление данных

### TestDisk — восстановление разделов

```bash
sudo pacman -S testdisk          # Arch
sudo apt install testdisk        # Ubuntu

sudo testdisk /dev/sda
# → Analyse → Quick Search → Found partitions → Write
```

### PhotoRec — восстановление файлов

```bash
sudo photorec /dev/sda
# Выбрать раздел → тип ФС → путь для сохранения
# Восстанавливает удалённые файлы по сигнатурам
```

### extundelete (ext3/ext4)

```bash
sudo apt install extundelete
# Восстановить все удалённые файлы:
sudo extundelete /dev/sda2 --restore-all
# Конкретный файл:
sudo extundelete /dev/sda2 --restore-file home/user/document.txt
```

### ddrescue — клонирование повреждённого диска

```bash
# Сначала клонировать, затем восстанавливать данные с клона!
sudo ddrescue /dev/sda /dev/sdb rescue.log
# Повторить для проблемных секторов:
sudo ddrescue -d -r3 /dev/sda /dev/sdb rescue.log
```

## Btrfs-снапшоты для восстановления

### Snapper (openSUSE, Arch)

```bash
# Список снапшотов
sudo snapper list

# Сравнить снапшот с текущим состоянием
sudo snapper status 42..0

# Отменить изменения (откатить к снапшоту)
sudo snapper undochange 42..0

# Полный откат (при загрузке)
# В GRUB → выбрать снапшот из списка
# После загрузки:
sudo snapper rollback
sudo reboot
```

### Timeshift

```bash
# GUI
sudo timeshift-gtk

# CLI — создать снапшот
sudo timeshift --create --comments "Before update"

# Восстановить
sudo timeshift --restore --snapshot '2024-01-15_10-30-00'

# Список снапшотов
sudo timeshift --list
```

## Чеклист восстановления системы

```
□ 1. Загрузиться с Live USB
□ 2. lsblk -f → определить разделы
□ 3. mount корневой раздел на /mnt
□ 4. mount дополнительные разделы (/home, /boot/efi)
□ 5. arch-chroot /mnt (или chroot с bind-mounts)
□ 6. Определить проблему:
     - journalctl -b → логи
     - systemctl --failed → сломанные сервисы
     - pacman -Qk → целостность пакетов
□ 7. Исправить:
     - Загрузчик → grub-install + grub-mkconfig
     - Ядро → mkinitcpio -P / update-initramfs
     - Пароль → passwd
     - fstab → nano /etc/fstab + blkid
     - Пакет → pacman -U (из кэша) / pacman -S
□ 8. exit → umount -R /mnt → reboot
```

## Экстренный режим (без Live USB)

```bash
# В GRUB нажать 'e', добавить к строке linux:
init=/bin/bash
# Ctrl+X → загрузится минимальная оболочка

# Перемонтировать / для записи:
mount -o remount,rw /

# Выполнить нужные действия (пароль, fstab и т.д.)
passwd root
nano /etc/fstab

# Синхронизировать и перезагрузить
sync
reboot -f
```
