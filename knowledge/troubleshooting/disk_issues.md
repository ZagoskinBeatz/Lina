# Проблемы с дисками и файловыми системами

## Диск заполнен — нет места

### Диагностика
```bash
df -h                               # использование по разделам
df -i                               # использование inode (мелкие файлы)
du -sh /* 2>/dev/null | sort -rh | head -10  # крупнейшие каталоги
du -sh /home/* 2>/dev/null | sort -rh
ncdu /                              # интерактивный

# Нижний уровень
sudo btrfs fi usage /               # для BTRFS
lsblk -f                            # разделы и FS
findmnt                             # точки монтирования
```

### Быстрая очистка
```bash
# Журналы systemd
journalctl --disk-usage
sudo journalctl --vacuum-size=200M
sudo journalctl --vacuum-time=7d

# Кэш пакетов
sudo pacman -Scc                    # Arch — полная очистка кэша
sudo apt clean                      # Debian/Ubuntu
sudo dnf clean all                  # Fedora

# Временные файлы
sudo rm -rf /tmp/*
sudo systemd-tmpfiles --clean

# Старые ядра (Ubuntu)
sudo apt autoremove --purge

# Логи
sudo find /var/log -name "*.gz" -delete
sudo find /var/log -name "*.old" -delete
sudo truncate -s 0 /var/log/syslog

# Docker
docker system prune -af
docker volume prune

# Flatpak
flatpak uninstall --unused
flatpak repair

# Snap
sudo snap list --all | awk '/disabled/{print $1, $3}' | \
  while read name rev; do sudo snap remove "$name" --revision="$rev"; done

# Пользовательский кэш
rm -rf ~/.cache/thumbnails/*
rm -rf ~/.cache/pip
rm -rf ~/.cache/yay
du -sh ~/.cache/* | sort -rh | head

# Крупные файлы
find / -xdev -type f -size +500M 2>/dev/null
find /home -name "*.iso" -o -name "*.tar.gz" -o -name "*.zip" | xargs du -sh | sort -rh

# Удалённые но открытые файлы (занимают место)
sudo lsof +L1 | grep deleted
# → перезапустить процесс чтобы освободить место
```

### Подводные камни BTRFS
```bash
# Снапшоты занимают место
sudo btrfs subvolume list /
sudo btrfs subvolume delete /.snapshots/*/snapshot

# Снапшоты Snapper
sudo snapper list
sudo snapper delete 1-50            # удалить снапшоты 1-50
sudo snapper set-config "NUMBER_LIMIT=5"

# Баланс (перераспределение данных)
sudo btrfs balance start -dusage=50 /
sudo btrfs balance status /

# Обслуживание
sudo btrfs scrub start /
sudo btrfs scrub status /
```

## SMART — мониторинг здоровья диска

### Проверка
```bash
sudo pacman -S smartmontools

# Статус
sudo smartctl -H /dev/sda           # PASSED/FAILED
sudo smartctl -a /dev/sda           # полный отчёт
sudo smartctl -a /dev/nvme0n1       # для NVMe

# Критические атрибуты
# ID  5 — Reallocated_Sector_Ct   → > 0 = проблемы
# ID 187 — Reported_Uncorrect     → > 0 = ошибки чтения
# ID 188 — Command_Timeout        → растёт = диск умирает
# ID 197 — Current_Pending_Sector → ожидающие перемещения
# ID 198 — Offline_Uncorrectable  → неисправимые секторы
# NVMe: Critical Warning, Media Errors, Available Spare

# Тесты
sudo smartctl -t short /dev/sda    # ~2 минуты
sudo smartctl -t long /dev/sda     # ~2-8 часов
sudo smartctl -l selftest /dev/sda # результаты

# Автоматический мониторинг
sudo systemctl enable --now smartd
# /etc/smartd.conf:
# /dev/sda -a -o on -S on -n standby,q -s (S/../.././02|L/../../6/03) -W 4,45,55 -m root
```

## Восстановление файловой системы

### fsck — проверка и восстановление
```bash
# ВАЖНО: только на размонтированной FS!
sudo umount /dev/sda1

# ext4
sudo fsck.ext4 -f /dev/sda1        # принудительная проверка
sudo fsck.ext4 -y /dev/sda1        # автоисправление

# XFS
sudo xfs_repair /dev/sda1
sudo xfs_repair -L /dev/sda1       # с потерей журнала (крайний случай)

# BTRFS
sudo btrfs check /dev/sda1
sudo btrfs check --repair /dev/sda1  # ОПАСНО — только при необходимости
sudo btrfs rescue super-recover /dev/sda1

# Из initramfs (если корневой раздел)
# На экране GRUB: добавить init=/bin/bash
# mount -o remount,ro /
# fsck.ext4 -f /dev/sda2
# reboot -f
```

### Восстановление удалённых файлов
```bash
# ext4 — extundelete
sudo pacman -S extundelete
sudo extundelete /dev/sda1 --restore-all

# ext4 — TestDisk / PhotoRec
sudo pacman -S testdisk
sudo testdisk /dev/sda              # восстановление разделов
sudo photorec /dev/sda              # восстановление файлов

# BTRFS
# Если удалённый файл в снапшоте:
sudo mount -o subvol=@/.snapshots/1/snapshot /mnt
cp /mnt/path/to/file /home/user/

# ddrescue — копирование с повреждённого диска
sudo pacman -S ddrescue
sudo ddrescue /dev/sda /dev/sdb rescue.log
sudo ddrescue -d -r3 /dev/sda /dev/sdb rescue.log  # повторные проходы
```

## Разметка и создание FS

### Создание разделов
```bash
# fdisk (MBR/GPT)
sudo fdisk /dev/sda

# gdisk (только GPT)
sudo gdisk /dev/sda

# parted (скрипты)
sudo parted /dev/sda mklabel gpt
sudo parted /dev/sda mkpart primary ext4 1MiB 100GiB
sudo parted /dev/sda mkpart primary linux-swap 100GiB 108GiB

# cgdisk — TUI для GPT
sudo cgdisk /dev/sda
```

### Создание файловых систем
```bash
# ext4
sudo mkfs.ext4 -L "Data" /dev/sda1

# BTRFS
sudo mkfs.btrfs -L "Data" /dev/sda1
sudo mkfs.btrfs -d raid1 -m raid1 /dev/sda1 /dev/sdb1  # RAID1

# XFS
sudo mkfs.xfs -L "Data" /dev/sda1

# FAT32 (для EFI)
sudo mkfs.fat -F32 /dev/sda1

# swap
sudo mkswap /dev/sda2
sudo swapon /dev/sda2

# exFAT (для совместимости Windows)
sudo mkfs.exfat /dev/sda1
```

### /etc/fstab
```bash
# Формат: <устройство>  <точка>  <тип>  <опции>  <dump>  <fsck>

# По UUID (рекомендуется)
UUID=xxxx-xxxx  /           btrfs  defaults,noatime,compress=zstd:3,ssd  0  0
UUID=xxxx-xxxx  /boot/efi   vfat   defaults,umask=0077  0  2
UUID=xxxx-xxxx  /home       ext4   defaults,noatime  0  2
UUID=xxxx-xxxx  none        swap   defaults  0  0

# tmpfs — каталоги в RAM
tmpfs  /tmp  tmpfs  defaults,noatime,mode=1777,size=4G  0  0

# NFS
server:/share  /mnt/nfs  nfs  defaults,_netdev  0  0

# Найти UUID
sudo blkid
lsblk -f
```

## Шифрование диска — LUKS
```bash
# Создание
sudo cryptsetup luksFormat /dev/sda1
sudo cryptsetup open /dev/sda1 cryptdata
sudo mkfs.ext4 /dev/mapper/cryptdata
sudo mount /dev/mapper/cryptdata /mnt

# Управление ключами
sudo cryptsetup luksDump /dev/sda1      # информация
sudo cryptsetup luksAddKey /dev/sda1    # добавить ключ
sudo cryptsetup luksRemoveKey /dev/sda1 # удалить ключ

# Автоматическая разблокировка
# /etc/crypttab:
# cryptdata  UUID=xxxx  none  luks

# С ключ-файлом
sudo dd if=/dev/urandom of=/root/.keyfile bs=1024 count=4
sudo chmod 400 /root/.keyfile
sudo cryptsetup luksAddKey /dev/sda1 /root/.keyfile
# /etc/crypttab:
# cryptdata  UUID=xxxx  /root/.keyfile  luks

# Закрытие
sudo umount /mnt
sudo cryptsetup close cryptdata
```

## RAID — программный массив

### mdadm
```bash
# Создание RAID1 (зеркало)
sudo mdadm --create /dev/md0 --level=1 --raid-devices=2 /dev/sda1 /dev/sdb1
sudo mkfs.ext4 /dev/md0

# Статус
cat /proc/mdstat
sudo mdadm --detail /dev/md0

# Замена диска
sudo mdadm /dev/md0 --fail /dev/sdb1
sudo mdadm /dev/md0 --remove /dev/sdb1
sudo mdadm /dev/md0 --add /dev/sdc1

# Сохранение конфигурации
sudo mdadm --detail --scan >> /etc/mdadm.conf
sudo mkinitcpio -P
```

## Troubleshooting

### Диск не монтируется
```bash
# Проверить
sudo blkid /dev/sda1
sudo file -s /dev/sda1
dmesg | tail -20

# Причины:
# 1. FS повреждена → fsck
# 2. Windows fast startup → ntfsfix /dev/sda1
# 3. Нет драйвера → sudo pacman -S ntfs-3g exfatprogs
# 4. fstab ошибка → sudo mount -a (покажет ошибки)
```

### I/O errors
```bash
# Проверить dmesg
dmesg | grep -i "i/o error\|sector\|reset\|offline"

# Причины:
# 1. Плохие секторы → SMART тест
# 2. Кабель SATA → попробовать другой порт/кабель
# 3. Блок питания → проверить напряжение
# 4. Диск умирает → backup + замена
```

### Высокий iowait
```bash
# Найти виновника
iotop -oPa
pidstat -d 1

# Частые причины:
# 1. Swap thrashing → добавить RAM или earlyoom
# 2. Фоновый scrub/balance → btrfs scrub cancel /
# 3. Антивирус/индексатор → отключить
# 4. Журналирование → tune2fs -o journal_data_writeback
```
