# Файловая система Linux

## Иерархия каталогов (FHS)

| Каталог | Назначение |
|---------|-----------|
| `/` | Корень файловой системы |
| `/bin` | Основные команды (ls, cp, mv, cat, grep) |
| `/sbin` | Системные команды (fdisk, mkfs, iptables) |
| `/etc` | Конфигурационные файлы |
| `/home` | Домашние каталоги пользователей |
| `/root` | Домашний каталог root |
| `/var` | Переменные данные (логи, кэш, почта) |
| `/tmp` | Временные файлы (очищается при перезагрузке) |
| `/usr` | Пользовательские программы и библиотеки |
| `/usr/bin` | Пользовательские команды |
| `/usr/lib` | Библиотеки |
| `/usr/share` | Архитектурно-независимые данные |
| `/opt` | Дополнительное ПО (стороннее) |
| `/dev` | Файлы устройств |
| `/proc` | Виртуальная ФС ядра (процессы, параметры) |
| `/sys` | Виртуальная ФС устройств и драйверов |
| `/mnt` | Точки монтирования (вручную) |
| `/media` | Точки монтирования (автоматически) |
| `/boot` | Ядро, initramfs, загрузчик |
| `/srv` | Данные сервисов (веб-сервер и т.п.) |

## Типы файловых систем

### ext4 (по умолчанию для большинства дистрибутивов)
```bash
# Создать ext4 на разделе
sudo mkfs.ext4 /dev/sda1

# Проверить целостность
sudo e2fsck -f /dev/sda1

# Показать информацию
sudo tune2fs -l /dev/sda1
```

### btrfs (CoW, снапшоты, сжатие)
```bash
# Создать btrfs
sudo mkfs.btrfs /dev/sda1

# Создать subvolume
sudo btrfs subvolume create /mnt/@home

# Создать снапшот
sudo btrfs subvolume snapshot /home /snapshots/home-$(date +%Y%m%d)

# Включить сжатие (zstd)
sudo mount -o compress=zstd /dev/sda1 /mnt

# Показать использование
sudo btrfs filesystem usage /
```

### XFS (для больших файлов, серверов)
```bash
sudo mkfs.xfs /dev/sda1
sudo xfs_info /dev/sda1
```

## Права доступа

### Числовые права
| Число | Права | Описание |
|-------|-------|----------|
| 7 | rwx | Чтение + запись + выполнение |
| 6 | rw- | Чтение + запись |
| 5 | r-x | Чтение + выполнение |
| 4 | r-- | Только чтение |
| 0 | --- | Нет прав |

### Команды управления правами
```bash
# Изменить права (владелец rwx, группа rx, остальные r)
chmod 754 file.txt

# Рекурсивно
chmod -R 755 /path/to/dir

# Символический формат
chmod u+x script.sh        # Добавить выполнение владельцу
chmod go-w file.txt         # Убрать запись у группы и остальных
chmod a+r file.txt          # Чтение для всех

# Изменить владельца
chown user:group file.txt
chown -R user:group /path/

# Специальные биты
chmod u+s /usr/bin/prog     # SUID — выполнять от владельца
chmod g+s /shared/dir       # SGID — наследовать группу
chmod +t /tmp               # Sticky bit — удалять только свои файлы
```

## Монтирование

### Ручное монтирование
```bash
# Монтировать раздел
sudo mount /dev/sda1 /mnt

# С параметрами
sudo mount -o rw,noatime,compress=zstd /dev/sda1 /mnt

# Монтировать ISO
sudo mount -o loop image.iso /mnt

# Размонтировать
sudo umount /mnt

# Принудительно (если busy)
sudo umount -l /mnt
```

### /etc/fstab — автомонтирование при загрузке
```bash
# Формат: <устройство> <точка> <тип> <опции> <dump> <fsck>
UUID=abc123  /           ext4   defaults,noatime    0 1
UUID=def456  /home       btrfs  defaults,compress=zstd  0 0
UUID=ghi789  none        swap   sw                  0 0
/dev/sdb1    /data       ext4   defaults,nofail     0 2

# Узнать UUID
sudo blkid
lsblk -f

# Применить fstab без перезагрузки
sudo mount -a
```

### tmpfs (RAM-диск)
```bash
# В fstab
tmpfs  /tmp  tmpfs  defaults,size=4G,noatime  0 0

# Вручную
sudo mount -t tmpfs -o size=2G tmpfs /ramdisk
```

## Ссылки

### Жёсткие ссылки
```bash
ln original.txt link.txt    # Одинаковый inode, только в пределах одной ФС
```

### Символические ссылки (симлинки)
```bash
ln -s /path/to/original symlink   # Указатель на путь
ln -sf /new/target symlink        # Перезаписать существующий
```

## Поиск файлов

```bash
# find — поиск по критериям
find / -name "*.log" -size +100M          # Файлы > 100 МБ
find /home -mtime -7 -type f              # Изменённые за 7 дней
find / -perm -4000 -type f                # Файлы с SUID
find /tmp -type f -empty -delete          # Удалить пустые файлы

# locate — быстрый поиск по базе данных
sudo updatedb                              # Обновить базу
locate nginx.conf                          # Найти файл

# which / whereis — поиск команд
which python3
whereis gcc
```

## Работа с дисками

```bash
# Показать разделы
lsblk
fdisk -l
parted -l

# Создать/изменить разделы
sudo fdisk /dev/sda        # MBR
sudo gdisk /dev/sda        # GPT
sudo parted /dev/sda       # Интерактивный

# Проверить использование
df -h                       # По разделам
du -sh /var/*               # Размер каталогов
ncdu /                      # Интерактивный (нужно установить)
```

## LVM (Logical Volume Manager)

```bash
# Создать
sudo pvcreate /dev/sda1              # Physical Volume
sudo vgcreate vg0 /dev/sda1          # Volume Group
sudo lvcreate -L 50G -n lv_home vg0  # Logical Volume
sudo mkfs.ext4 /dev/vg0/lv_home

# Расширить
sudo lvextend -L +20G /dev/vg0/lv_home
sudo resize2fs /dev/vg0/lv_home      # ext4
sudo xfs_growfs /dev/vg0/lv_home     # xfs

# Показать
sudo pvs && sudo vgs && sudo lvs
```

## LUKS (шифрование дисков)

```bash
# Зашифровать раздел
sudo cryptsetup luksFormat /dev/sda2

# Открыть (расшифровать)
sudo cryptsetup open /dev/sda2 crypthome

# Создать ФС и монтировать
sudo mkfs.ext4 /dev/mapper/crypthome
sudo mount /dev/mapper/crypthome /home

# Закрыть
sudo cryptsetup close crypthome
```

## RAID (mdadm)

```bash
# Создать RAID 1 (зеркало)
sudo mdadm --create /dev/md0 --level=1 --raid-devices=2 /dev/sdb1 /dev/sdc1

# Создать RAID 5 (с паритетом)
sudo mdadm --create /dev/md0 --level=5 --raid-devices=3 /dev/sdb1 /dev/sdc1 /dev/sdd1

# Статус
cat /proc/mdstat
sudo mdadm --detail /dev/md0

# Заменить диск
sudo mdadm --manage /dev/md0 --fail /dev/sdc1
sudo mdadm --manage /dev/md0 --remove /dev/sdc1
sudo mdadm --manage /dev/md0 --add /dev/sde1

# Сохранить конфигурацию
sudo mdadm --detail --scan >> /etc/mdadm.conf
```

## Квоты дискового пространства

```bash
# Включить квоты (ext4)
sudo mount -o remount,usrquota,grpquota /home
sudo quotacheck -cugm /home
sudo quotaon /home

# Установить квоту для пользователя (мягкий лимит 5G, жёсткий 6G)
sudo edquota -u username
# → blocks soft=5242880 hard=6291456

# Показать квоты
sudo repquota -a                     # Все пользователи
quota -u username                    # Конкретный пользователь

# btrfs квоты
sudo btrfs quota enable /
sudo btrfs qgroup limit 10G /home
sudo btrfs qgroup show /
```

## ACL (Access Control Lists)

```bash
# Расширенные права доступа (помимо стандартных owner/group/other)

# Установить ACL
setfacl -m u:username:rwx /shared/dir     # Пользователю
setfacl -m g:developers:rw /shared/dir    # Группе
setfacl -m d:g:developers:rw /shared/dir  # Default ACL (для новых файлов)

# Посмотреть ACL
getfacl /shared/dir

# Удалить ACL
setfacl -b /shared/dir                    # Удалить все ACL
setfacl -x u:username /shared/dir         # Удалить для пользователя

# Рекурсивно
setfacl -R -m g:team:rwx /project/
```

## inotify — мониторинг изменений файлов

```bash
# inotifywait — реакция на изменения
sudo pacman -S inotify-tools

# Следить за каталогом
inotifywait -m -r /etc/ -e modify,create,delete

# Автоматически перезапустить при изменении конфиг-файла
while inotifywait -e modify /etc/nginx/nginx.conf; do
    sudo systemctl reload nginx
done

# fatrace — показать какие файлы открываются
sudo fatrace
sudo fatrace -f W                # Только записи
```
