# Диагностика производительности Linux

## Быстрая оценка — «60-секундный чеклист»
```bash
uptime                              # load average
dmesg -T | tail                     # ошибки ядра
vmstat 1 5                          # CPU, память, I/O
mpstat -P ALL 1 3                   # нагрузка по ядрам
pidstat 1 3                         # по процессам
iostat -xz 1 3                      # дисковый I/O
free -h                             # память
sar -n DEV 1 3                      # сетевой трафик
sar -n TCP,ETCP 1 3                 # TCP-соединения
top                                 # общая картина
```

## CPU — высокая нагрузка

### Диагностика
```bash
# Общая нагрузка
uptime                              # load average: 1m 5m 15m
# load > количество ядер = перегрузка
nproc                               # количество ядер

# По процессам
top -o %CPU                         # сортировка по CPU
htop                                # интерактивный (с деревом)
ps aux --sort=-%cpu | head -20      # топ потребителей

# По ядрам
mpstat -P ALL 1                     # нагрузка каждого ядра
# %usr — пользовательский код
# %sys — ядро
# %iowait — ожидание I/O
# %idle — простой

# Трассировка
strace -p PID -c                    # системные вызовы (сводка)
perf top                            # профилирование в реальном времени
perf record -g -p PID sleep 10      # запись профиля
perf report                         # анализ
```

### Решения
```bash
# Ограничить процесс
cpulimit -l 50 -p PID              # лимит 50% CPU
renice 19 -p PID                    # снизить приоритет
nice -n 19 command                  # запуск с низким приоритетом

# Привязка к ядрам
taskset -c 0,1 command              # только ядра 0 и 1
taskset -cp 0-3 PID                 # изменить для запущенного

# Частоты CPU
cpupower frequency-info
cpupower frequency-set -g performance
cpupower frequency-set -g powersave

# systemd cgroups
systemd-run --scope -p CPUQuota=50% command
# Или через unit:
# [Service]
# CPUQuota=200%              # max 2 ядра
```

## Память — нехватка RAM

### Диагностика
```bash
# Общая картина
free -h
cat /proc/meminfo

# Подробно
# total = used + free + buffers/cache
# buffers/cache — автоматически освобождается

# По процессам
ps aux --sort=-%mem | head -20
smem -t -k                          # реальное потребление (USS/PSS/RSS)
# USS = уникальная для процесса
# PSS = пропорциональная (разделённая)
# RSS = residence set (включая shared)

# OOM-killer
dmesg | grep -i "out of memory"
dmesg | grep -i "killed process"
journalctl -k | grep -i oom

# Подробная диагностика
vmstat 1                            # si/so — swap in/out
slabtop                             # память ядра
cat /proc/buddyinfo                 # фрагментация
```

### Решения
```bash
# Очистить кэш (безопасно)
sudo sync
echo 3 | sudo tee /proc/sys/vm/drop_caches

# Swappiness — когда начинать swap
cat /proc/sys/vm/swappiness
sudo sysctl vm.swappiness=10        # меньше = позже swap (SSD)
# Постоянно: vm.swappiness=10 в /etc/sysctl.d/99-custom.conf

# OOM-killer приоритеты
cat /proc/PID/oom_score              # текущий score
echo -17 | sudo tee /proc/PID/oom_adj  # защитить процесс
echo 1000 | sudo tee /proc/PID/oom_score_adj  # убить первым

# Earlyoom — превентивный OOM-killer
sudo pacman -S earlyoom
sudo systemctl enable --now earlyoom
# Убивает до зависания системы

# ZRAM для сжатой подкачки
sudo modprobe zram
echo lz4 | sudo tee /sys/block/zram0/comp_algorithm
echo 4G | sudo tee /sys/block/zram0/disksize
sudo mkswap /dev/zram0
sudo swapon -p 100 /dev/zram0
```

## Диск — I/O проблемы

### Диагностика
```bash
# Нагрузка на диск
iostat -xz 1
# %util > 80% — диск перегружен
# await > 10ms (SSD) или > 20ms (HDD) — высокая задержка
# r/s, w/s — операции в секунду

# Какие процессы нагружают диск
iotop -oP                           # только активные
pidstat -d 1                        # по процессам
fatrace                             # мониторинг доступа к файлам

# SMART — здоровье диска
sudo smartctl -a /dev/sda
sudo smartctl -H /dev/sda           # quick health
sudo smartctl -t short /dev/sda     # быстрый selftest
sudo smartctl -t long /dev/sda      # полный selftest

# Файловая система
df -h                               # использование FS
df -i                               # использование inode
du -sh /* 2>/dev/null | sort -rh | head # крупнейшие каталоги
ncdu /                              # интерактивный

# BTRFS
sudo btrfs fi usage /
sudo btrfs device stats /
```

### Решения
```bash
# I/O scheduler
cat /sys/block/sda/queue/scheduler  # текущий
echo mq-deadline | sudo tee /sys/block/sda/queue/scheduler  # для HDD
echo none | sudo tee /sys/block/nvme0n1/queue/scheduler     # для NVMe

# TRIM (SSD)
sudo fstrim -av                     # разовый
sudo systemctl enable fstrim.timer  # еженедельный

# Ограничить I/O процесса
ionice -c3 -p PID                    # idle class
ionice -c2 -n7 command               # low priority
systemd-run --scope -p IOWeight=10 command

# Найти и удалить крупные файлы
find / -xdev -type f -size +100M 2>/dev/null | head
journalctl --vacuum-size=200M       # очистить журналы
sudo pacman -Scc                    # очистить кэш пакетов
```

## Сеть — медленное соединение

### Диагностика
```bash
# Скорость
speedtest-cli
curl -o /dev/null -s -w '%{speed_download}\n' http://speedtest.tele2.net/10MB.zip

# Задержка
ping -c 20 8.8.8.8 | tail -1       # min/avg/max/mdev
mtr -rw google.com                  # пошаговая задержка

# DNS
time dig google.com                 # время DNS-ответа
resolvectl statistics               # кэш DNS

# Соединения
ss -s                               # сводка
ss -tn state time-wait | wc -l     # TIME_WAIT
ss -tn state established | wc -l   # активные

# Мониторинг трафика
iftop -i wlan0                      # по соединениям
nethogs                             # по процессам
vnstat -l                           # live

# TCP-тюнинг
sysctl net.core.rmem_max
sysctl net.core.wmem_max
sysctl net.ipv4.tcp_congestion_control
```

### Решения
```bash
# Оптимизация TCP
sudo tee /etc/sysctl.d/30-network.conf << 'EOF'
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = cake
net.ipv4.tcp_fastopen = 3
EOF
sudo sysctl --system

# MTU
ping -c 1 -M do -s 1472 8.8.8.8   # тест MTU (1472 + 28 = 1500)
sudo ip link set eth0 mtu 1400      # уменьшить при проблемах

# DNS — ускорение
# Сменить на быстрый DNS: 1.1.1.1, 8.8.8.8, 9.9.9.9
# Включить DNS кэш через systemd-resolved
```

## Загрузка — медленный старт

### Диагностика
```bash
# Время загрузки
systemd-analyze
systemd-analyze blame               # медленные сервисы
systemd-analyze critical-chain      # критический путь
systemd-analyze plot > boot.svg     # визуализация

# GRUB
# Убрать timeout: GRUB_TIMEOUT=0
# Добавить: GRUB_CMDLINE_LINUX_DEFAULT="quiet loglevel=3"
```

### Решения
```bash
# Отключить ненужные сервисы
sudo systemctl disable bluetooth
sudo systemctl disable cups
sudo systemctl mask NetworkManager-wait-online.service

# Параллельный fsck
# /etc/fstab: последняя колонка 2 для не-root разделов

# initramfs — убрать ненужные хуки
# /etc/mkinitcpio.conf → HOOKS
sudo mkinitcpio -P
```

## Мониторинг — инструменты
| Инструмент | Уровень | Описание |
|-----------|---------|----------|
| htop / btm | Система | TUI мониторинг |
| glances | Система | Всё-в-одном |
| iotop | Диск | I/O по процессам |
| iftop | Сеть | Трафик по соединениям |
| nethogs | Сеть | Трафик по процессам |
| perf | CPU | Профилирование |
| strace | Процесс | Системные вызовы |
| sysbench | Бенчмарк | CPU/RAM/Disk/threads |
| stress-ng | Стресс-тест | Нагрузочный тест |
| s-tui | CPU | Частоты, температура |

## Автоматический тюнинг
```bash
# TLP — оптимизация питания ноутбука
sudo pacman -S tlp
sudo systemctl enable --now tlp

# auto-cpufreq — адаптивные частоты
sudo auto-cpufreq --install
sudo auto-cpufreq --stats

# tuned — профили производительности
sudo tuned-adm list
sudo tuned-adm profile throughput-performance
sudo tuned-adm profile powersave
```
