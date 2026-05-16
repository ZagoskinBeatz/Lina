# Zram и Swap — управление подкачкой

## Swap
Swap — область на диске, используемая как расширение RAM.

### Swap-файл
```bash
# Создание swap-файла (4 GB)
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Добавить в /etc/fstab для автомонтирования
echo '/swapfile none swap defaults 0 0' | sudo tee -a /etc/fstab

# Проверка
swapon --show
free -h
```

### Swap-раздел
```bash
sudo mkswap /dev/sdXn
sudo swapon /dev/sdXn
# В /etc/fstab:
# UUID=xxx none swap defaults 0 0
```

### Управление
```bash
swapon --show              # активные swap
sudo swapoff /swapfile     # отключить
sudo swapon /swapfile      # включить

# swappiness (когда начать использовать swap)
cat /proc/sys/vm/swappiness           # текущее значение (по-умолч. 60)
sudo sysctl vm.swappiness=10          # временно
# Постоянно: /etc/sysctl.d/99-swappiness.conf
# vm.swappiness=10
```

## Zram (сжатый swap в RAM)
Zram создаёт сжатый блочный девайс в RAM. Эффективнее обычного swap
на SSD — снижает wear и повышает отзывчивость.

### Установка
```bash
# Arch / CachyOS (часто предустановлен)
sudo pacman -S zram-generator

# Ubuntu 22.04+
sudo apt install zram-tools

# Fedora (по умолчанию)
# уже включён
```

### Настройка zram-generator
```bash
# /etc/systemd/zram-generator.conf
[zram0]
zram-size = ram / 2     # половина RAM
compression-algorithm = zstd
```

### Ручная настройка
```bash
# Загрузить модуль
sudo modprobe zram

# Создать zram-устройство
echo zstd | sudo tee /sys/block/zram0/comp_algorithm
echo 4G | sudo tee /sys/block/zram0/disksize
sudo mkswap /dev/zram0
sudo swapon -p 100 /dev/zram0   # высокий приоритет

# Проверка
zramctl
swapon --show
```

### Рекомендации
| RAM | Swap (SSD) | Zram |
|---|---|---|
| 4 GB | 4-8 GB | 2 GB |
| 8 GB | 4 GB | 4 GB |
| 16 GB | 2-4 GB или zram only | 8 GB |
| 32+ GB | Не нужен (или zram) | 8-16 GB |

### Для гибернации
Hibernate требует swap >= RAM (для сохранения всей памяти на диск).
Zram НЕ подходит для hibernate — нужен реальный swap.

```bash
# Проверить достаточно ли swap для hibernate
free -h | grep Swap
# Должен быть >= RAM  
```

## Настройка swappiness
```bash
# swappiness определяет агрессивность использования swap
# Диапазон: 0 (минимально сwap) — 200 (максимально swap)
# По умолчанию: 60

# Проверить текущее значение
cat /proc/sys/vm/swappiness

# Установить временно
sudo sysctl vm.swappiness=10

# Установить постоянно
echo "vm.swappiness=10" | sudo tee /etc/sysctl.d/99-swappiness.conf
sudo sysctl --system

# Рекомендации:
# Десктоп с SSD: vm.swappiness=10
# Десктоп с HDD: vm.swappiness=30
# Сервер: vm.swappiness=60 (по умолчанию)
# Zram: vm.swappiness=180 (zram быстрый, можно агрессивнее)
```

## Zram — продвинутая настройка

### Алгоритмы сжатия
| Алгоритм | Сжатие | Скорость | Использование CPU |
|----------|--------|----------|-------------------|
| lzo | Среднее | Быстрый | Низкое |
| lzo-rle | Среднее+ | Быстрый | Низкое |
| lz4 | Низкое | Очень быстрый | Минимальное |
| zstd | Высокое | Средний | Среднее |

```bash
# Проверить текущий алгоритм
cat /sys/block/zram0/comp_algorithm

# Доступные алгоритмы
cat /sys/block/zram0/comp_algorithm
# [lzo] lzo-rle lz4 lz4hc zstd

# Рекомендация: zstd (лучшее сжатие), lz4 (минимальная задержка)
```

### Мониторинг Zram
```bash
# Статистика zram
cat /sys/block/zram0/mm_stat
# orig_data_size  compr_data_size  mem_used  mem_limit  ...

# Или через zramctl
zramctl
# NAME       ALGORITHM DISKSIZE   DATA   COMPR  TOTAL STREAMS MOUNTPOINT
# /dev/zram0 zstd          8G  1.2G 456.3M 513.2M       8 [SWAP]

# Коэффициент сжатия:
# compr_data_size / orig_data_size × 100%
# Типично: 30-50% (сжатие 2-3x)
```

### systemd-zram-setup@.service (Arch / CachyOS)
```bash
# CachyOS уже включает zram по умолчанию
# Конфигурация: /etc/systemd/zram-generator.conf
[zram0]
zram-size = ram / 2
compression-algorithm = zstd
swap-priority = 100

# Применить
sudo systemctl daemon-reload
sudo systemctl restart systemd-zram-setup@zram0.service
```

## Swap на файле — подробная настройка
```bash
# Создание swap-файла (для ext4 и btrfs)
sudo fallocate -l 8G /swapfile
# На btrfs используйте dd вместо fallocate:
sudo dd if=/dev/zero of=/swapfile bs=1M count=8192 status=progress

# Установить права
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Добавить в fstab для автомонтирования
echo "/swapfile none swap defaults 0 0" | sudo tee -a /etc/fstab

# Для btrfs: сначала отключите CoW
sudo chattr +C /swapfile
# И в fstab: subvol= НЕ указывать для swap
```

## Hibernate (спящий режим на диск)
```bash
# Требования:
# - Swap >= RAM (файл или раздел)
# - Ядро с поддержкой hibernate

# 1. Найти UUID и offset swap-файла
sudo filefrag -v /swapfile | head -4
# ext: логический физический
# 0:  0..    33791:  физический_offset..

# 2. Добавить параметры ядра в GRUB
# /etc/default/grub
GRUB_CMDLINE_LINUX="resume=UUID=<swap_partition_uuid> resume_offset=<offset>"
sudo grub-mkconfig -o /boot/grub/grub.cfg

# 3. Initramfs — добавить resume hook
# /etc/mkinitcpio.conf
HOOKS=(... filesystems resume ...)
sudo mkinitcpio -P

# 4. Тестирование
systemctl hibernate            # гибернация
systemctl hybrid-sleep         # гибрид (suspend + hibernate)
systemctl suspend-then-hibernate  # сначала suspend, потом hibernate
```

## Мониторинг памяти
```bash
# Подробная информация о памяти
free -h                          # общая статистика
cat /proc/meminfo               # детальная информация ядра
vmstat 1                        # мониторинг в реальном времени (1 сек)
smem -t                         # реальное потребление процессами

# Анализ OOM (Out of Memory)
dmesg | grep -i "oom\|out of memory\|killed"
journalctl -k | grep -i oom

# vm.vfs_cache_pressure
# Агрессивность освобождения кэша dentry/inode
# По умолчанию: 100
# Для десктопа: 50-75 (меньше = держать кэш дольше)
echo "vm.vfs_cache_pressure=50" | sudo tee /etc/sysctl.d/99-cache.conf

# vm.dirty_ratio / vm.dirty_background_ratio
# Контроль записи на диск
echo "vm.dirty_ratio=10" | sudo tee -a /etc/sysctl.d/99-cache.conf
echo "vm.dirty_background_ratio=5" | sudo tee -a /etc/sysctl.d/99-cache.conf
sudo sysctl --system
```
