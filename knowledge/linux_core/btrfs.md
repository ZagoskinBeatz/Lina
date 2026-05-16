# BTRFS — файловая система

## Обзор
BTRFS — современная CoW (copy-on-write) файловая система с поддержкой
снимков, подтомов, сжатия, RAID и онлайн-дефрагментации.

## Создание и монтирование
```bash
# Форматирование
sudo mkfs.btrfs /dev/sdX

# Монтирование с оптимальными параметрами
sudo mount -o compress=zstd:3,noatime,ssd,discard=async /dev/sdX /mnt

# /etc/fstab
UUID=xxx  /  btrfs  compress=zstd:3,noatime,ssd,discard=async,subvol=@  0  0
UUID=xxx  /home  btrfs  compress=zstd:3,noatime,ssd,discard=async,subvol=@home  0  0
```

## Подтома (subvolumes)
```bash
# Создание подтомов
sudo btrfs subvolume create /mnt/@
sudo btrfs subvolume create /mnt/@home
sudo btrfs subvolume create /mnt/@var
sudo btrfs subvolume create /mnt/@snapshots

# Список подтомов
sudo btrfs subvolume list /

# Удаление
sudo btrfs subvolume delete /mnt/@old
```

## Снимки (snapshots)
```bash
# Создание read-only снимка
sudo btrfs subvolume snapshot -r / /.snapshots/2024-01-01

# Создание read-write снимка
sudo btrfs subvolume snapshot / /.snapshots/2024-01-01-rw

# Откат к снимку
sudo mv / /old_root
sudo btrfs subvolume snapshot /.snapshots/2024-01-01 /
```

## Snapper (автоматические снимки)
```bash
# Установка
sudo pacman -S snapper snapper-gui   # Arch
sudo apt install snapper             # Ubuntu

# Создание конфигурации
sudo snapper -c root create-config /

# Создать снимок
sudo snapper -c root create --description "before update"

# Список снимков
sudo snapper -c root list

# Сравнение
sudo snapper -c root diff 1..2

# Откат
sudo snapper -c root undochange 1..2
```

## Сжатие
```bash
# Проверка уровня сжатия
sudo compsize /          # нужен пакет compsize

# Дефрагментация со сжатием
sudo btrfs filesystem defragment -r -czstd /
```

## Мониторинг
```bash
sudo btrfs filesystem usage /        # использование
sudo btrfs filesystem df /           # по типам данных
sudo btrfs device stats /            # ошибки устройств
sudo btrfs scrub start /             # проверка целостности
sudo btrfs scrub status /            # статус проверки
```

## Частые проблемы
1. **"No space left"** — `sudo btrfs balance start -dusage=50 /`
2. **Медленные метаданные** — `sudo btrfs balance start -musage=70 /`
3. **Ошибки после сбоя** — `sudo btrfs check --readonly /dev/sdX`

## RAID (встроенный)
```bash
# Создать btrfs RAID1 (зеркалирование)
sudo mkfs.btrfs -d raid1 -m raid1 /dev/sda /dev/sdb

# Добавить устройство
sudo btrfs device add /dev/sdc /mnt
sudo btrfs balance start -dconvert=raid1 -mconvert=raid1 /mnt

# Удалить устройство
sudo btrfs device remove /dev/sda /mnt

# Заменить неисправное устройство
sudo btrfs replace start <devid> /dev/sdd /mnt
sudo btrfs replace status /mnt
```

### Поддерживаемые профили RAID
| Профиль | Данные | Метаданные | Мин. дисков |
|---------|--------|-----------|-------------|
| single | 1 копия | 1 копия | 1 |
| dup | - | 2 копии на 1 диске | 1 |
| raid0 | Striping | Striping | 2 |
| raid1 | Зеркало | Зеркало | 2 |
| raid1c3 | 3 копии | 3 копии | 3 |
| raid5 | Parity | raid1 | 3 |
| raid6 | Double parity | raid1c3 | 4 |
| raid10 | Зеркало + stripe | raid1 | 4 |

## Отправка и получение (send/receive)
```bash
# Инкрементальный бэкап на другой диск
sudo btrfs subvolume snapshot -r / /.snapshots/snap-new
sudo btrfs send /.snapshots/snap-new | sudo btrfs receive /backup/

# Инкрементальная отправка (только разница)
sudo btrfs send -p /.snapshots/snap-old /.snapshots/snap-new | sudo btrfs receive /backup/

# Через SSH на удалённый сервер
sudo btrfs send /.snapshots/snap-new | ssh user@server sudo btrfs receive /backup/
```

## Квоты (ограничение размера подтомов)
```bash
# Включить квоты
sudo btrfs quota enable /

# Задать лимит подтому
sudo btrfs qgroup limit 50G /home

# Посмотреть использование
sudo btrfs qgroup show -reF /
```

## Дедупликация
```bash
# Установить duperemove
sudo pacman -S duperemove   # Arch
sudo apt install duperemove # Debian

# Запуск дедупликации
sudo duperemove -rdh /data/

# Альтернатива: bees (фоновая дедупликация)
# https://github.com/Zygo/bees
```

## Конвертация из ext4
```bash
# Конвертировать ext4 → btrfs (in-place)
sudo btrfs-convert /dev/sdX

# Откатить обратно (если есть подтом ext2_saved)
sudo btrfs-convert -r /dev/sdX

# Удалить образ отката (освободить место)
sudo btrfs subvolume delete /ext2_saved
```

## Оптимальные параметры монтирования
```bash
# SSD
compress=zstd:3,noatime,ssd,discard=async,space_cache=v2

# HDD
compress=zstd:3,noatime,autodefrag,space_cache=v2

# Для /var/log (без CoW для баз данных)
# Отключить CoW для каталога:
chattr +C /var/lib/mysql/
chattr +C /var/lib/postgresql/
```

## Проверка и восстановление
```bash
# Проверка целостности (read-only, безопасно)
sudo btrfs scrub start /
sudo btrfs scrub status /

# Просмотр ошибок устройств
sudo btrfs device stats /

# Сброс счётчиков ошибок
sudo btrfs device stats -z /

# Проверка ФС (ТОЛЬКО при размонтированном разделе)
sudo btrfs check --readonly /dev/sdX

# Восстановление (осторожно!)
sudo btrfs rescue super-recover /dev/sdX
sudo btrfs rescue zero-log /dev/sdX
sudo btrfs check --repair /dev/sdX   # последний шанс, риск потери данных
```

## Сравнение BTRFS vs EXT4 vs XFS
| Параметр | BTRFS | EXT4 | XFS |
|----------|-------|------|-----|
| CoW | Да | Нет | Нет |
| Снимки | Да (подтома) | Нет (только LVM) | Нет |
| Сжатие | zstd/lzo/zlib | Нет | Нет |
| RAID | Встроенный | Нет (md) | Нет (md) |
| Макс. размер ФС | 16 EiB | 1 EiB | 8 EiB |
| Онлайн-ресайз | Да | Да | Только увеличение |
| Дефрагментация | Онлайн | Онлайн | Онлайн |
| Стабильность | Хорошая (RAID5/6 эксп.) | Отличная | Отличная |
