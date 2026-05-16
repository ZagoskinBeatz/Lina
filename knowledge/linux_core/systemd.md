# Systemd — система инициализации и управления

## Основные концепции
Systemd — система инициализации Linux (PID 1) и менеджер сервисов. Управляет загрузкой,
сервисами, логами, монтированием, таймерами, сетью и многим другим.

### Типы юнитов
| Тип | Суффикс | Назначение |
|-----|---------|-----------|
| Service | `.service` | Демоны и процессы |
| Socket | `.socket` | Активация через сокет (on-demand) |
| Timer | `.timer` | Периодические задачи (замена cron) |
| Mount | `.mount` | Точки монтирования |
| Target | `.target` | Группы юнитов (аналог runlevel) |
| Path | `.path` | Отслеживание файлов/каталогов |
| Device | `.device` | Устройства ядра |
| Slice | `.slice` | Группы cgroup для ограничения ресурсов |
| Scope | `.scope` | Внешне созданные процессы |
| Swap | `.swap` | Файлы/разделы подкачки |

### Расположение юнит-файлов (приоритет сверху вниз)
```
/etc/systemd/system/        ← Администратор (высший приоритет)
/run/systemd/system/        ← Временные (runtime)
/usr/lib/systemd/system/    ← Пакетный менеджер (не редактировать!)
```

### Пользовательские юниты
```
~/.config/systemd/user/           ← Пользовательские юниты
/etc/systemd/user/                ← Системные для всех пользователей
/usr/lib/systemd/user/            ← От пакетов
```

## Управление сервисами
```bash
# Основные операции
systemctl start <service>        # запуск
systemctl stop <service>         # остановка
systemctl restart <service>      # перезапуск
systemctl reload <service>       # перечитать конфигурацию
systemctl enable <service>       # автозапуск
systemctl disable <service>      # отключить автозапуск
systemctl enable --now <service> # включить + запустить
systemctl status <service>       # статус
systemctl is-active <service>    # проверка: active/inactive
systemctl is-enabled <service>   # проверка: enabled/disabled

# Информация
systemctl list-units --type=service             # все активные сервисы
systemctl list-units --type=service --state=failed  # проваленные
systemctl list-unit-files --type=service        # все unit-файлы
systemctl show <service>                        # все свойства
systemctl cat <service>                         # содержимое unit-файла
systemctl list-dependencies <service>           # зависимости
systemctl list-dependencies --reverse <service> # кто зависит от нас

# Маскировка (полная блокировка запуска)
systemctl mask <service>         # заблокировать
systemctl unmask <service>       # разблокировать

# Перезагрузка менеджера юнитов
systemctl daemon-reload          # после изменения unit-файлов
systemctl daemon-reexec          # перезапуск самого systemd
```

### Типы сервисов (Type= в [Service])
| Type | Описание |
|------|----------|
| `simple` | Основной процесс — ExecStart (по умолчанию) |
| `exec` | Как simple, но ready после exec() |
| `forking` | Демон fork() в фон, PIDFile= нужен |
| `oneshot` | Одноразовая задача, RemainAfterExit=yes |
| `notify` | Процесс сигналит sd_notify() о готовности |
| `dbus` | Готов когда появится на D-Bus |
| `idle` | Ждёт завершения всех заданий |

## Journalctl — логирование
```bash
journalctl                           # все логи
journalctl -b                        # текущая загрузка
journalctl -b -1                     # прошлая загрузка
journalctl -b -2                     # позапрошлая загрузка
journalctl --list-boots              # список всех загрузок
journalctl -u <service>              # логи сервиса
journalctl -u <service> -f           # follow (как tail -f)
journalctl -u <service> -n 50        # последние 50 строк
journalctl --since "2 hours ago"     # за период
journalctl --since "2024-01-01" --until "2024-01-02"
journalctl -p err                    # только ошибки
journalctl -p warning -b             # предупреждения текущей загрузки
journalctl _PID=1234                 # логи по PID
journalctl _UID=1000                 # логи по пользователю
journalctl -k                        # только ядро (dmesg)
journalctl -o json-pretty            # JSON формат
journalctl -o verbose                # расширенный формат
journalctl --disk-usage              # использование диска
sudo journalctl --vacuum-time=7d     # очистка старше 7 дней
sudo journalctl --vacuum-size=500M   # ограничить до 500 МБ

# Постоянное хранение логов (по умолчанию volatile в некоторых дистрибутивах)
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
```

### Уровни приоритета (-p)
| Код | Уровень | Описание |
|-----|---------|----------|
| 0 | emerg | Система неработоспособна |
| 1 | alert | Требуется немедленное действие |
| 2 | crit | Критические условия |
| 3 | err | Ошибки |
| 4 | warning | Предупреждения |
| 5 | notice | Нормально, но значимо |
| 6 | info | Информационные сообщения |
| 7 | debug | Отладочные сообщения |

## Создание своего сервиса
```ini
# /etc/systemd/system/myservice.service
[Unit]
Description=My Custom Service
Documentation=man:myservice(8) https://example.com/docs
After=network.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=myuser
Group=myuser
WorkingDirectory=/home/myuser/app
ExecStartPre=/usr/bin/test -f /home/myuser/app/config.yaml
ExecStart=/usr/bin/python3 app.py
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5
TimeoutStartSec=30
TimeoutStopSec=30

# Безопасность (hardening)
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictRealtime=yes
MemoryMax=512M
CPUQuota=50%

# Окружение
Environment=LANG=ru_RU.UTF-8
EnvironmentFile=-/etc/default/myservice

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now myservice
journalctl -u myservice -f           # следить за логами
```

### Пользовательский сервис (без sudo)
```ini
# ~/.config/systemd/user/myapp.service
[Unit]
Description=My User App

[Service]
ExecStart=%h/bin/myapp
Restart=always

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now myapp
loginctl enable-linger $USER         # работать без активной сессии
```

### Переопределение существующего сервиса
```bash
# Не редактируйте файлы в /usr/lib/systemd/system/!
# Используйте drop-in:
sudo systemctl edit <service>
# Создаёт /etc/systemd/system/<service>.d/override.conf
```

## Таймеры (замена cron)
```ini
# /etc/systemd/system/backup.timer
[Unit]
Description=Daily Backup Timer

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=1h

[Install]
WantedBy=timers.target
```

### Форматы OnCalendar
```
OnCalendar=minutely           # каждую минуту
OnCalendar=hourly             # каждый час
OnCalendar=daily              # ежедневно (00:00)
OnCalendar=weekly             # еженедельно (понедельник 00:00)
OnCalendar=monthly            # ежемесячно
OnCalendar=*-*-* 03:00:00     # каждый день в 3:00
OnCalendar=Mon *-*-* 09:00    # понедельники в 9:00
OnCalendar=*-*-01 00:00:00    # первое число каждого месяца
OnBootSec=5min                # через 5 минут после загрузки
OnUnitActiveSec=1h            # каждый час после последнего запуска
```

```bash
systemd-analyze calendar "Mon *-*-* 09:00"   # проверить формат
systemctl list-timers --all                   # список таймеров
```

## Systemd-analyze (диагностика загрузки)
```bash
systemd-analyze                      # время загрузки (firmware+loader+kernel+userspace)
systemd-analyze blame                # сервисы по времени загрузки
systemd-analyze critical-chain       # критический путь
systemd-analyze critical-chain <svc> # критический путь для конкретного сервиса
systemd-analyze plot > boot.svg      # графическая диаграмма (открыть в браузере)
systemd-analyze dot | dot -Tsvg > deps.svg  # граф зависимостей
systemd-analyze verify <unit>        # проверить синтаксис юнит-файла
systemd-analyze security <service>   # аудит безопасности сервиса (0-10 баллов)
systemd-analyze cat-config systemd/journald.conf  # итоговая конфигурация
```

## Targets (уровни запуска)
```bash
systemctl get-default                    # текущий target
systemctl set-default graphical.target   # GUI при загрузке
systemctl set-default multi-user.target  # только консоль
systemctl isolate rescue.target          # recovery mode
systemctl isolate emergency.target       # аварийный режим (минимальная shell)
```

| Target | Аналог SysVinit | Описание |
|--------|-----------------|----------|
| poweroff.target | 0 | Выключение |
| rescue.target | 1 | Однопользовательский |
| multi-user.target | 3 | Многопользовательский (без GUI) |
| graphical.target | 5 | GUI |
| reboot.target | 6 | Перезагрузка |

## Управление ресурсами (cgroups)
```bash
# Ограничить память и CPU для сервиса
systemctl set-property <service> MemoryMax=1G
systemctl set-property <service> CPUQuota=200%    # 2 ядра

# Посмотреть использование ресурсов
systemd-cgtop                        # топ по cgroup
systemctl show <service> -p MemoryCurrent
systemctl show <service> -p CPUUsageNSec
```

## systemd-networkd (управление сетью)
```bash
systemctl enable --now systemd-networkd
networkctl list                      # список интерфейсов
networkctl status eth0               # статус
```

## systemd-resolved (DNS)
```bash
systemctl status systemd-resolved
resolvectl status                    # текущие DNS
resolvectl query example.com         # DNS-запрос
```

## Частые проблемы
1. **Сервис не стартует** — `systemctl status <svc>`, `journalctl -u <svc> -b`
2. **Job timeout** — увеличить `TimeoutStartSec=` или `TimeoutStopSec=`
3. **Failed to enable: masked** — `systemctl unmask <svc>`
4. **Логи занимают много места** — `journalctl --vacuum-time=7d`
5. **Долгая загрузка** — `systemd-analyze blame`, отключить ненужные сервисы
6. **Unit not found** — `systemctl daemon-reload` после создания файла
