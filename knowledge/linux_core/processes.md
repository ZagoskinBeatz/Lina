# Процессы и управление

## Просмотр процессов

```bash
# ps — снимок процессов
ps aux                      # Все процессы (BSD-формат)
ps -ef                      # Все процессы (System V)
ps aux --sort=-%mem         # Отсортировать по RAM
ps aux --sort=-%cpu         # Отсортировать по CPU
ps -u username              # Процессы пользователя
ps aux | grep nginx         # Найти процесс

# top — интерактивный мониторинг
top                         # Запустить
# Горячие клавиши: q — выход, M — сортировка по RAM, P — по CPU,
# k — убить процесс, 1 — показать все ядра

# htop — улучшенный мониторинг (нужно установить)
htop

# btop — современный мониторинг
btop
```

## Управление процессами

```bash
# Запуск в фоне
command &
nohup command &             # Продолжить после выхода из терминала

# Управление заданиями
jobs                        # Список фоновых заданий
fg %1                       # Вернуть в передний план
bg %1                       # Продолжить в фоне
Ctrl+Z                      # Приостановить текущий процесс

# Завершение процессов
kill PID                    # SIGTERM (вежливое завершение)
kill -9 PID                 # SIGKILL (принудительное)
kill -STOP PID              # Приостановить
kill -CONT PID              # Возобновить
killall firefox             # Убить все процессы по имени
pkill -f "python script"    # Убить по шаблону командной строки

# Приоритет (nice)
nice -n 10 command          # Запустить с пониженным приоритетом
renice -n 5 -p PID          # Изменить приоритет (-20..19, чем меньше — выше)
```

## Сигналы

| Сигнал | Номер | Действие |
|--------|-------|----------|
| SIGHUP | 1 | Перезагрузка конфигурации |
| SIGINT | 2 | Прерывание (Ctrl+C) |
| SIGQUIT | 3 | Завершение с core dump |
| SIGKILL | 9 | Принудительное завершение (нельзя перехватить) |
| SIGTERM | 15 | Вежливое завершение (по умолчанию) |
| SIGSTOP | 19 | Приостановка (нельзя перехватить) |
| SIGCONT | 18 | Возобновление |
| SIGUSR1 | 10 | Пользовательский сигнал 1 |
| SIGUSR2 | 12 | Пользовательский сигнал 2 |

## systemd

### Управление сервисами
```bash
# Статус
systemctl status nginx
systemctl is-active nginx
systemctl is-enabled nginx

# Запуск/остановка
sudo systemctl start nginx
sudo systemctl stop nginx
sudo systemctl restart nginx
sudo systemctl reload nginx         # Перечитать конфиг без перезапуска

# Автозапуск
sudo systemctl enable nginx         # Включить при загрузке
sudo systemctl disable nginx        # Отключить
sudo systemctl enable --now nginx   # Включить + запустить

# Маскировка (полная блокировка)
sudo systemctl mask bluetooth
sudo systemctl unmask bluetooth

# Список сервисов
systemctl list-units --type=service
systemctl list-units --type=service --state=failed
systemctl list-unit-files --type=service
```

### Журнал systemd (journalctl)
```bash
# Все логи
journalctl

# Логи сервиса
journalctl -u nginx
journalctl -u nginx --since "1 hour ago"
journalctl -u nginx --since today

# Логи текущей загрузки
journalctl -b
journalctl -b -1            # Предыдущая загрузка

# По приоритету
journalctl -p err            # Только ошибки
journalctl -p warning        # Предупреждения и выше

# В реальном времени
journalctl -f
journalctl -fu nginx

# Размер журнала
journalctl --disk-usage
sudo journalctl --vacuum-size=500M   # Ограничить до 500 МБ
sudo journalctl --vacuum-time=7d     # Удалить старше 7 дней
```

### Создание своего сервиса
```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=My Application
After=network.target

[Service]
Type=simple
User=myuser
WorkingDirectory=/opt/myapp
ExecStart=/opt/myapp/run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now myapp
```

### Таймеры systemd (замена cron)
```ini
# /etc/systemd/system/cleanup.timer
[Unit]
Description=Ежедневная очистка

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl list-timers
```

## cgroups (Control Groups)

```bash
# Посмотреть cgroup процесса
cat /proc/<PID>/cgroup

# Ограничить RAM для процесса через systemd
systemd-run --scope -p MemoryMax=500M command

# Для сервиса
# В секции [Service]:
MemoryMax=1G
CPUQuota=50%
```

## /proc — виртуальная ФС процессов

```bash
cat /proc/cpuinfo           # Информация о CPU
cat /proc/meminfo           # Информация о RAM
cat /proc/loadavg           # Средняя загрузка
cat /proc/<PID>/status      # Статус процесса
cat /proc/<PID>/cmdline     # Командная строка процесса
cat /proc/<PID>/fd/         # Открытые файловые дескрипторы
ls -la /proc/<PID>/fd       # Список открытых файлов
```

## Полезные утилиты

```bash
# lsof — открытые файлы
lsof -i :80                 # Что слушает порт 80
lsof -u username             # Файлы пользователя
lsof +D /var/log             # Файлы в каталоге

# strace — системные вызовы
strace -p PID
strace -e trace=open,read command
strace -c command            # Подсчёт системных вызовов (профилирование)
strace -o trace.log command  # Запись в файл
strace -tt -T -p PID        # С метками времени и длительностью

# ltrace — библиотечные вызовы
ltrace command               # Показать вызовы libc
ltrace -c command            # Подсчёт вызовов

# Нагрузка
uptime                      # Аптайм и load average
vmstat 1                    # CPU, память, I/O (каждую секунду)
iostat 1                    # Дисковый I/O
mpstat -P ALL 1             # CPU по ядрам
free -h                     # Память
```

## Namespaces и изоляция

Linux поддерживает namespaces для изоляции процессов — основа контейнеров.

| Namespace | Изолирует |
|-----------|----------|
| PID | Дерево процессов |
| NET | Сетевой стек |
| MNT | Точки монтирования |
| UTS | Hostname и domain |
| IPC | Inter-process communication |
| USER | UID/GID mapping |
| CGROUP | cgroup корень |

```bash
# Посмотреть namespaces процесса
ls -la /proc/<PID>/ns/

# Запустить процесс в изолированном namespace
sudo unshare --pid --mount-proc --fork bash
# Внутри: PID 1 — ваш bash, полная изоляция процессов

# Войти в namespace контейнера
sudo nsenter -t <PID> -m -u -i -n -p bash
```

## OOM Killer (Out of Memory)

```bash
# Проверить события OOM
dmesg | grep -i "oom\|killed process"
journalctl -k | grep -i oom

# Установить приоритет OOM для процесса
# -1000 = никогда не убивать, +1000 = убить первым
echo -1000 > /proc/<PID>/oom_score_adj   # Защитить процесс
echo 500 > /proc/<PID>/oom_score_adj     # Повысить шанс убийства

# Текущий OOM-score
cat /proc/<PID>/oom_score                # Текущий рейтинг
cat /proc/<PID>/oom_score_adj            # Пользовательская настройка

# Отключить OOM killer для systemd-сервиса
# В секции [Service]:
OOMScoreAdjust=-1000
```

## Планировщики CPU

```bash
# Текущий планировщик
cat /sys/block/sda/queue/scheduler

# Посмотреть политику планирования процесса
chrt -p <PID>

# Запустить с realtime приоритетом (SCHED_FIFO)
sudo chrt -f 50 command

# Запустить с round-robin приоритетом
sudo chrt -r 50 command

# Привязать процесс к ядрам (CPU affinity)
taskset -c 0,1 command           # Только ядра 0 и 1
taskset -cp 0-3 <PID>           # Изменить для существующего
```

## Мониторинг процессов в реальном времени

```bash
# htop — расширенный top
htop
# Горячие клавиши htop:
# F5 — дерево процессов
# F6 — сортировка
# F9 — отправить сигнал
# F4 — фильтр по имени
# t — переключить дерево/список
# H — скрыть/показать потоки (threads)
# u — отфильтровать по пользователю

# btop — ещё более красивый мониторинг
btop
# Показывает CPU, RAM, Disk, Network в одном интерфейсе

# glances — мониторинг с web-интерфейсом
glances
glances -w                       # Web-сервер на порту 61208

# atop — с записью истории
atop                             # Интерактивный
atop -r /var/log/atop/atop_YYYYMMDD  # Чтение записи
# Запись автоматическая если включён atopd.service
```

## Файловые дескрипторы

```bash
# Лимиты файловых дескрипторов
ulimit -n                        # Текущий лимит (per-process)
cat /proc/sys/fs/file-max       # Системный лимит
cat /proc/sys/fs/file-nr        # Используется / лимит

# Увеличить лимит (временно)
ulimit -n 65535

# Увеличить лимит (постоянно)
# /etc/security/limits.conf
*  soft  nofile  65535
*  hard  nofile  65535

# Для systemd-сервиса
# В секции [Service]:
LimitNOFILE=65535

# Открытые дескрипторы процесса
ls -la /proc/<PID>/fd | wc -l
lsof -p <PID> | wc -l
```

## Автозапуск процессов

### systemd (рекомендуется)
```bash
# Системный сервис: /etc/systemd/system/
# Пользовательский: ~/.config/systemd/user/
# Подробнее → systemd_advanced.md
```

### cron
```bash
# Редактировать расписание
crontab -e
# Формат: мин час день месяц день_недели команда
0 2 * * * /usr/local/bin/backup.sh          # Каждый день в 02:00
*/15 * * * * /usr/local/bin/check.sh        # Каждые 15 минут
0 0 * * 0 /usr/local/bin/weekly.sh          # Каждое воскресенье

# Список задач
crontab -l

# Системный cron
cat /etc/crontab
ls /etc/cron.d/ /etc/cron.daily/ /etc/cron.hourly/ /etc/cron.weekly/
```

### autostart (для DE)
```bash
# XDG autostart: ~/.config/autostart/*.desktop
# Пример:
# ~/.config/autostart/myapp.desktop
```

```ini
[Desktop Entry]
Type=Application
Name=MyApp
Exec=/usr/bin/myapp
Hidden=false
X-GNOME-Autostart-enabled=true
```
