# Продвинутое использование systemd

## Шаблоны юнитов (Unit Templates)

Шаблонный юнит содержит `@` в имени. При инстанцировании `%i` заменяется
идентификатором экземпляра.

```ini
# /etc/systemd/system/container@.service
[Unit]
Description=Container %i
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/podman start -a %i
ExecStop=/usr/bin/podman stop -t 10 %i
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# Активировать экземпляры
sudo systemctl enable --now container@webapp.service
sudo systemctl enable --now container@db.service
sudo systemctl status container@webapp.service
```

### Спецификаторы шаблонов

| Спецификатор | Описание |
|-------------|----------|
| `%i` | Имя экземпляра (после @, неэкранированное) |
| `%I` | Имя экземпляра (экранированное) |
| `%n` | Полное имя юнита |
| `%N` | Полное имя юнита (экранированное) |
| `%p` | Префикс имени (до @) |
| `%u` | Имя пользователя (для user-юнитов) |
| `%h` | Домашний каталог пользователя |
| `%H` | Имя хоста |
| `%t` | Каталог рантайма (/run или /run/user/UID) |

## Socket Activation

systemd может слушать сокет и запускать сервис только при входящем соединении.
Это экономит ресурсы — процесс не работает, пока не нужен.

```ini
# /etc/systemd/system/myapp.socket
[Unit]
Description=MyApp Socket

[Socket]
ListenStream=8080
Accept=no
# Accept=yes создаёт новый экземпляр сервиса на каждое соединение

[Install]
WantedBy=sockets.target
```

```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=MyApp Service
Requires=myapp.socket

[Service]
Type=simple
ExecStart=/opt/myapp/server
# Сервис получает сокет через файловый дескриптор 3
# В коде: sd_listen_fds() или просто fd=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now myapp.socket
# Сервис myapp.service запустится автоматически при первом подключении к порту 8080
sudo systemctl status myapp.socket
systemctl list-sockets
```

### D-Bus активация

```ini
# /etc/systemd/system/myapp-dbus.service
[Unit]
Description=MyApp D-Bus Service

[Service]
Type=dbus
BusName=org.example.MyApp
ExecStart=/opt/myapp/dbus-server

[Install]
WantedBy=multi-user.target
```

## Таймеры systemd (расширенно)

### Монотонные таймеры (относительно события)

```ini
# /etc/systemd/system/cleanup.timer
[Unit]
Description=Периодическая очистка

[Timer]
OnBootSec=5min                    # Через 5 минут после загрузки
OnUnitActiveSec=1h                # Каждый час после последнего запуска
RandomizedDelaySec=5min           # Случайная задержка до 5 минут (снижение нагрузки)
Persistent=true                   # Запустить пропущенные (если система была выключена)

[Install]
WantedBy=timers.target
```

### Календарные таймеры (как cron)

```ini
[Timer]
OnCalendar=daily                  # Ежедневно в 00:00
OnCalendar=weekly                 # Еженедельно
OnCalendar=monthly                # Ежемесячно
OnCalendar=*-*-* 02:30:00         # Каждый день в 02:30
OnCalendar=Mon..Fri 09:00         # Пн-Пт в 09:00
OnCalendar=*-*-01 00:00:00        # 1-го числа каждого месяца
OnCalendar=Sat *-*-* 14:00:00     # Каждую субботу в 14:00
OnCalendar=*:0/15                 # Каждые 15 минут
```

### Проверка расписания

```bash
# Проверить, когда таймер сработает
systemd-analyze calendar "Mon..Fri 09:00"
systemd-analyze calendar "*-*-* 02:30:00" --iterations=5

# Все активные таймеры
systemctl list-timers --all

# Запустить таймер вручную (для тестирования)
sudo systemctl start cleanup.service
```

### Таймер вместо cron (примеры)

```bash
# Cron: */5 * * * * /usr/local/bin/check_health.sh
# Systemd:
# check-health.timer → OnCalendar=*:0/5
# check-health.service → ExecStart=/usr/local/bin/check_health.sh

# Cron: 0 2 * * 0 /usr/local/bin/weekly_backup.sh
# Systemd:
# weekly-backup.timer → OnCalendar=Sun 02:00
# weekly-backup.service → ExecStart=/usr/local/bin/weekly_backup.sh
```

## Journald — расширенная настройка

### Конфигурация

```ini
# /etc/systemd/journald.conf
[Journal]
Storage=persistent                # auto | volatile | persistent | none
Compress=yes
SystemMaxUse=2G                   # Макс. размер журнала
SystemMaxFileSize=128M            # Макс. размер одного файла
MaxRetentionSec=1month            # Хранить не дольше месяца
MaxLevelStore=info                # Не хранить debug в постоянном журнале
MaxLevelSyslog=warning            # Не передавать ниже warning в syslog
ForwardToSyslog=no                # Не передавать в syslog
RateLimitIntervalSec=30s          # Лимит частоты
RateLimitBurst=10000              # Макс. сообщений за интервал
```

### Расширенные запросы journalctl

```bash
# По PID
journalctl _PID=1234

# По UID
journalctl _UID=1000

# По executable
journalctl _COMM=nginx

# Комбинация полей (AND)
journalctl _SYSTEMD_UNIT=nginx.service _PID=5678

# OR (через +)
journalctl _SYSTEMD_UNIT=nginx.service + _SYSTEMD_UNIT=php-fpm.service

# JSON-вывод (для парсинга)
journalctl -u nginx -o json-pretty --since "1 hour ago"

# Показать поля сообщения
journalctl -u nginx -o verbose --no-pager | head -50

# Экспорт журнала
journalctl --since "2024-01-01" --until "2024-01-31" -o export > january.journal
```

### Ротация журнала

```bash
# Принудительная ротация
sudo journalctl --rotate

# Очистка по размеру
sudo journalctl --vacuum-size=500M

# Очистка по времени
sudo journalctl --vacuum-time=2weeks

# Очистка по количеству файлов
sudo journalctl --vacuum-files=5

# Текущее использование
journalctl --disk-usage
```

## Зависимости и порядок юнитов

### Типы зависимостей

```ini
[Unit]
# Порядок запуска
After=network.target             # Запустить ПОСЛЕ network.target
Before=httpd.service             # Запустить ДО httpd

# Зависимости (совместный запуск)
Requires=postgresql.service      # Обязательная зависимость (упадёт если зависимость упала)
Wants=redis.service              # Мягкая зависимость (продолжит если redis упал)
BindsTo=docker.service           # Жёсткая привязка (останавливается вместе)

# Условия запуска
ConditionPathExists=/etc/myapp.conf
ConditionFileNotEmpty=/etc/myapp.conf
ConditionACPower=true            # Только при питании от сети
ConditionVirtualization=no       # Только на реальной машине

# Конфликты
Conflicts=iptables.service       # Не может работать одновременно
```

### Анализ зависимостей

```bash
# Дерево зависимостей
systemctl list-dependencies nginx.service
systemctl list-dependencies nginx.service --reverse  # Обратные зависимости

# Порядок запуска
systemctl list-dependencies --after nginx.service
systemctl list-dependencies --before nginx.service

# Граф зависимостей (SVG)
systemd-analyze dot | dot -Tsvg > deps.svg
```

## Ресурсные ограничения (cgroups v2)

```ini
# /etc/systemd/system/myapp.service.d/limits.conf
[Service]
# CPU
CPUQuota=200%                    # Макс. 2 ядра (100% = 1 ядро)
CPUWeight=100                    # Относительный вес (1-10000, default=100)
AllowedCPUs=0-3                  # Разрешённые ядра

# Память
MemoryMax=2G                     # Жёсткий лимит (OOM kill при превышении)
MemoryHigh=1G                    # Мягкий лимит (замедление)
MemorySwapMax=0                  # Запретить swap

# I/O
IOWeight=100                     # Относительный приоритет I/O
IOReadBandwidthMax=/dev/sda 50M  # Макс. скорость чтения
IOWriteBandwidthMax=/dev/sda 30M # Макс. скорость записи

# Процессы
TasksMax=100                     # Макс. количество процессов/потоков

# Сеть (через IP accounting)
IPAccounting=yes
IPAddressAllow=192.168.0.0/16
IPAddressDeny=any
```

```bash
# Проверить ограничения
systemctl show myapp.service -p MemoryMax,CPUQuota,TasksMax

# Текущее потребление
systemctl status myapp.service  # Shows Memory/CPU in status

# cgroup напрямую
systemd-cgtop                   # Топ по ресурсам (аналог top для cgroups)
```

## Пользовательские юниты (user services)

```bash
# Каталог: ~/.config/systemd/user/
mkdir -p ~/.config/systemd/user

# Пример: автозапуск Syncthing
# ~/.config/systemd/user/syncthing.service
```

```ini
[Unit]
Description=Syncthing

[Service]
ExecStart=/usr/bin/syncthing serve --no-browser
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
# Управление
systemctl --user daemon-reload
systemctl --user enable --now syncthing.service
systemctl --user status syncthing.service

# Логи
journalctl --user -u syncthing.service

# Чтобы пользовательские сервисы работали без логина:
sudo loginctl enable-linger username
```

## Генераторы systemd

Генераторы — скрипты, которые динамически создают юниты при загрузке.

```bash
# Каталог генераторов
/usr/lib/systemd/system-generators/

# Примеры встроенных:
# systemd-fstab-generator — создаёт .mount юниты из /etc/fstab
# systemd-cryptsetup-generator — из /etc/crypttab
# systemd-gpt-auto-generator — автоопределение разделов GPT

# Отладка генераторов
sudo /usr/lib/systemd/system-generators/systemd-fstab-generator /tmp/gen /tmp/gen2 /tmp/gen3
ls /tmp/gen/
```

## Анализ загрузки

```bash
# Время загрузки
systemd-analyze

# Детализация по сервисам
systemd-analyze blame

# Критический путь (что задерживало загрузку больше всего)
systemd-analyze critical-chain

# SVG-диаграмма загрузки
systemd-analyze plot > boot.svg

# Проверка юнита на ошибки
systemd-analyze verify /etc/systemd/system/myapp.service

# Карта безопасности юнита
systemd-analyze security myapp.service
```

## Безопасность сервисов (Hardening)

```ini
[Service]
# Изоляция файловой системы
ProtectHome=true                 # /home, /root, /run/user — недоступны
ProtectSystem=strict             # / — только чтение (strict: включая /etc)
ReadWritePaths=/var/lib/myapp    # Разрешить запись только сюда
PrivateTmp=true                  # Собственный /tmp

# Изоляция сети
PrivateNetwork=true              # Изолированная сеть (только loopback)
RestrictAddressFamilies=AF_INET AF_INET6  # Только IPv4/IPv6

# Ограничение возможностей
NoNewPrivileges=true             # Запрет эскалации привилегий
CapabilityBoundingSet=CAP_NET_BIND_SERVICE  # Только привязка к портам < 1024
AmbientCapabilities=CAP_NET_BIND_SERVICE

# Изоляция устройств
PrivateDevices=true              # Только pseudo-устройства
DevicePolicy=closed

# Системные вызовы
SystemCallFilter=@system-service  # Белый список syscalls
SystemCallArchitectures=native    # Только нативная архитектура

# Пользователь
DynamicUser=true                 # Создать эфемерного пользователя
User=myapp                       # Запуск от имени пользователя
Group=myapp
```

## Целевые юниты (Targets)

Targets заменяют SysV runlevels.

| Target | SysV | Описание |
|--------|------|----------|
| poweroff.target | 0 | Выключение |
| rescue.target | 1 | Однопользовательский режим |
| multi-user.target | 3 | Многопользовательский (без GUI) |
| graphical.target | 5 | Графический (с GUI) |
| reboot.target | 6 | Перезагрузка |
| emergency.target | - | Экстренный режим (минимум) |

```bash
# Текущая цель
systemctl get-default

# Изменить цель по умолчанию
sudo systemctl set-default multi-user.target   # Без GUI
sudo systemctl set-default graphical.target    # С GUI

# Переключиться на цель (без перезагрузки)
sudo systemctl isolate multi-user.target       # Отключить GUI
sudo systemctl isolate rescue.target           # Восстановление

# Создать свою цель
# /etc/systemd/system/myapp.target
```

```ini
[Unit]
Description=My Application Stack
Requires=multi-user.target
After=multi-user.target
Wants=myapp.service myapp-worker.service redis.service
```

## Разрешение проблем с systemd

```bash
# Не запускается сервис
systemctl status myapp.service
journalctl -xeu myapp.service

# Зависла загрузка
# Добавить к строке ядра в GRUB: systemd.debug-shell=1
# Это даст root shell на tty9 (Ctrl+Alt+F9)

# Показать все проблемные юниты
systemctl --failed
systemctl reset-failed                # Сбросить состояние

# Перечитать конфигурацию
sudo systemctl daemon-reload

# Полная перезагрузка systemd (без перезагрузки системы)
sudo systemctl daemon-reexec
```

## Переменные окружения

```ini
[Service]
# Прямо в юните
Environment="DB_HOST=localhost" "DB_PORT=5432"

# Из файла
EnvironmentFile=/etc/myapp/env
EnvironmentFile=-/etc/myapp/env.local  # - означает "не ошибка если нет"

# Передать через systemctl
# sudo systemctl set-environment MY_VAR=value
# sudo systemctl show-environment
```

## Drop-in конфигурации

Переопределение настроек юнита без редактирования оригинала.

```bash
# Создать drop-in
sudo systemctl edit myapp.service
# Создаёт /etc/systemd/system/myapp.service.d/override.conf

# Или вручную
sudo mkdir -p /etc/systemd/system/myapp.service.d/
sudo nano /etc/systemd/system/myapp.service.d/memory-limit.conf
```

```ini
# override.conf
[Service]
MemoryMax=4G
Environment="LOG_LEVEL=debug"
```

```bash
# Показать итоговую конфигурацию (после merge)
systemctl cat myapp.service

# Сбросить все drop-in
sudo systemctl revert myapp.service
```
