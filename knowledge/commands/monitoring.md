# Мониторинг системы — команды и инструменты

## Процессы и CPU
```bash
# Обзор системы
htop                              # интерактивный мониторинг
top                               # базовый мониторинг
btop                              # красивый TUI-мониторинг
glances                           # всё-в-одном (Python)

# Load average
uptime                            # время работы + load average
cat /proc/loadavg
# load average: 1.50, 2.00, 1.75 (1m, 5m, 15m)
# load > nproc = перегрузка

# Топ по CPU
ps aux --sort=-%cpu | head -20
pidstat 1 5                       # CPU по процессам (каждую секунду, 5 раз)
pidstat -t 1                      # с потоками

# Информация о CPU
lscpu                              # архитектура, ядра, потоки, кэши
cat /proc/cpuinfo | grep "model name" | head -1
nproc                              # количество ядер

# Частоты и производительность
cpupower frequency-info
cpupower monitor                   # мониторинг C-states
turbostat --Summary --show Busy% --interval 1  # частоты, C-states (Intel)

# Трассировка CPU
perf top                           # профилирование в реальном времени
perf stat command                  # статистика выполнения
mpstat -P ALL 1                    # нагрузка по ядрам
```

## Память (RAM)
```bash
free -h                           # общая информация
vmstat 1 5                        # статистика VM-подсистемы (si/so = swap)
cat /proc/meminfo                 # детальная информация
smem -t -k                        # реальное потребление (USS/PSS/RSS)

# Топ по памяти
ps aux --sort=-%mem | head -20
ps -eo pid,user,%mem,rss,comm --sort=-%mem | head -20

# Swap
swapon --show                     # активные swap-устройства
cat /proc/swaps

# Утечки памяти / OOM
dmesg | grep -i "oom\|out of memory\|killed process"
cat /proc/<PID>/status | grep -i "vmrss\|vmsize\|vmpeak"
cat /proc/<PID>/oom_score          # вероятность убийства OOM-killer

# Кэш и буферы
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches  # очистить (безопасно)
slabtop                           # память ядра (slab allocator)
```

## Диски
```bash
df -h                             # использование файловых систем
df -i                             # использование inode
du -sh /path/*                    # размер директорий
du -sh * | sort -rh | head -20   # топ-20 самых больших
ncdu /                            # интерактивный анализ (рекомендуется)

# I/O
iostat -xz 1                     # статистика ввода-вывода
# %util > 80% = диск перегружен
# await > 10ms (SSD) = высокая задержка
iotop -oPa                       # I/O по процессам (нужен root)
pidstat -d 1                     # дисковый I/O по процессам
fatrace                          # мониторинг доступа к файлам

# SMART
sudo smartctl -a /dev/sda        # здоровье диска
sudo smartctl -H /dev/sda        # quick health check
sudo smartctl -t short /dev/sda  # запуск теста

# NVMe
sudo nvme list
sudo nvme smart-log /dev/nvme0n1

# Информация о дисках
lsblk                             # блочные устройства
lsblk -f                          # с файловыми системами
blkid                             # UUID и типы FS
fdisk -l                          # таблица разделов
```

## Сеть
```bash
ip a                              # интерфейсы и IP
ip route                          # маршруты
ss -tulnp                         # открытые порты
ss -tn                            # активные TCP-соединения
ss -s                             # статистика сокетов

# Трафик
iftop -i eth0                     # трафик по соединениям
nethogs                           # трафик по процессам
nload                             # график трафика
vnstat -d                         # статистика по дням
vnstat -m                         # по месяцам
bmon                              # TUI монитор

# Диагностика
ping -c 4 8.8.8.8               # связь
traceroute google.com            # маршрут
mtr -rw google.com               # продвинутый traceroute
dig google.com                    # DNS запрос
curl -I https://example.com      # HTTP заголовки

# Подробная диагностика
tcpdump -i eth0 -n port 80       # захват пакетов
nmap -sT localhost                # сканирование портов
iperf3 -s / iperf3 -c server     # тест пропускной способности
```

## Логи
```bash
journalctl -b -p err             # ошибки текущей загрузки
journalctl -f                    # live-логи
journalctl -fu <unit>            # live-логи конкретного сервиса
journalctl --since "1 hour ago"  # за последний час
journalctl --since today -p warning  # предупреждения за сегодня
journalctl -k                    # сообщения ядра (kernel ring buffer)
dmesg -T | tail -50              # сообщения ядра (с timestamps)
dmesg -T --level=err             # только ошибки
journalctl --list-boots          # список загрузок
journalctl -b -1                 # логи предыдущей загрузки

# Размер журнала
journalctl --disk-usage
sudo journalctl --vacuum-size=500M   # Ограничить до 500 МБ
sudo journalctl --vacuum-time=7d     # Удалить старше 7 дней
```

## Температура и сенсоры
```bash
sensors                           # температура CPU/GPU (lm-sensors)
sudo sensors-detect               # настройка модулей сенсоров
cat /sys/class/thermal/thermal_zone*/temp  # напрямую
nvidia-smi                        # GPU NVIDIA
watch -n 1 sensors                # live мониторинг
s-tui                             # TUI монитор CPU (частоты, температура, мощность)
```

## Батарея (ноутбуки)
```bash
upower -i /org/freedesktop/UPower/devices/battery_BAT0
cat /sys/class/power_supply/BAT0/capacity  # процент заряда
cat /sys/class/power_supply/BAT0/status    # Charging/Discharging
acpi -b                           # состояние батареи
acpi -t                           # температура
tlp-stat -b                       # статус через TLP (подробно)
```

## Время загрузки
```bash
systemd-analyze                    # общее время
systemd-analyze blame              # медленные сервисы
systemd-analyze critical-chain     # критический путь
systemd-analyze plot > boot.svg    # визуализация
```

## Инструменты сводная таблица
| Утилита | Уровень | Install |
|---------|---------|---------|
| htop | CPU/RAM | pacman -S htop |
| btop | Всё | pacman -S btop |
| glances | Всё | pacman -S glances |
| iotop | Диск | pacman -S iotop |
| iftop | Сеть | pacman -S iftop |
| nethogs | Сеть/процессы | pacman -S nethogs |
| nload | Сеть/график | pacman -S nload |
| ncdu | Диск/место | pacman -S ncdu |
| s-tui | CPU/temp | pip install s-tui |
| perf | CPU/профилирование | pacman -S perf |
| strace | Процессы/syscalls | pacman -S strace |
| sysbench | Бенчмарки | pacman -S sysbench |
