# Lina — Встроенные макросы
# ===========================
# Каждый .yaml файл описывает один макрос (последовательность шагов).
# Макросы загружаются автоматически при старте macro_engine.

# Формат макроса

Макрос — это YAML-файл с определённой структурой, описывающий последовательность
автоматизированных шагов. Каждый шаг выполняет одно действие (action) с набором
параметров (params).

## Обязательные поля

- `name` — уникальное имя макроса (латиницей, snake_case)
- `description` — описание на русском языке
- `steps` — список шагов для выполнения

## Необязательные поля

- `trigger` — автоматический триггер: `cron`, `dbus`, `file`, `null`
- `variables` — переменные со значениями по умолчанию
- `tags` — теги для классификации (`system`, `backup`, `network`, `development`)
- `requires` — зависимости (пакеты, которые должны быть установлены)
- `confirm` — запросить подтверждение перед запуском (по умолчанию false)
- `timeout_global` — общий таймаут всего макроса в секундах
- `rollback` — шаги отката при ошибке

## Пример минимального макроса

```yaml
name: example_macro
description: "Пример макроса"
steps:
  - action: shell
    params:
      command: "echo Hello from Lina"
      description: "Приветствие"
```

## Типы action

| Action   | Описание                                | Параметры |
|----------|-----------------------------------------|-----------|
| shell    | Выполнение shell-команды                | command, workdir, sudo, timeout |
| notify   | Отправка уведомления на рабочий стол     | title, body, urgency (low/normal/critical) |
| check    | Проверка условия (abort при провале)     | condition, message |
| file     | Операции с файлами                       | operation (copy/move/delete/chmod), src, dst |
| service  | Управление systemd-сервисами             | name, operation (start/stop/restart/enable/disable) |
| package  | Установка/удаление пакетов               | names, operation (install/remove) |
| prompt   | Запросить ввод от пользователя           | message, variable, default |
| wait     | Пауза                                   | seconds |
| template | Создать файл из шаблона                 | src, dst, vars |

## Детали каждого action

### shell — выполнение команд
```yaml
steps:
  - action: shell
    params:
      command: "pacman -Syu --noconfirm"
      description: "Обновление системы"
      sudo: true
      timeout: 600         # 10 минут
      workdir: "/tmp"
      env:
        LANG: "C"
      on_error: abort      # abort | continue | retry
      retries: 3           # количество повторных попыток при retry
```

### check — проверка условия
```yaml
steps:
  - action: check
    params:
      condition: "command -v docker"
      message: "Docker не установлен. Установите docker и повторите."
      # Если condition возвращает exit code != 0 → abort макроса
```

### file — операции с файлами
```yaml
steps:
  - action: file
    params:
      operation: copy       # copy | move | delete | chmod | mkdir | symlink
      src: "/etc/pacman.conf"
      dst: "/etc/pacman.conf.bak"
      # Для chmod:
      # operation: chmod
      # path: "/path/to/file"
      # mode: "755"
```

### service — управление сервисами
```yaml
steps:
  - action: service
    params:
      name: "nginx"
      operation: restart     # start | stop | restart | enable | disable | status
      # enable_now: true     # enable + start одновременно
```

### notify — уведомления
```yaml
steps:
  - action: notify
    params:
      title: "Бэкап завершён"
      body: "Borg backup создан на {{ backup_target }}"
      urgency: normal        # low | normal | critical
      icon: "dialog-information"
```

### prompt — пользовательский ввод
```yaml
steps:
  - action: prompt
    params:
      message: "Введите имя сервера:"
      variable: server_name
      default: "localhost"
      # Значение доступно как {{ server_name }} в последующих шагах
```

### template — создание файлов из шаблонов
```yaml
steps:
  - action: template
    params:
      src: "templates/nginx.conf.j2"
      dst: "/etc/nginx/sites-available/{{ site_name }}"
      vars:
        server_name: "example.com"
        root: "/var/www/example"
```

## Переменные

В полях `command`, `body`, `condition`, `dst` можно использовать `{{ variable_name }}`.
Значения подставляются из:
1. Блока `variables` (значения по умолчанию)
2. Аргументов при вызове макроса
3. Результатов `prompt` шагов
4. Встроенных переменных

### Встроенные переменные

| Переменная | Описание |
|-----------|----------|
| `{{ hostname }}` | Имя хоста |
| `{{ user }}` | Текущий пользователь |
| `{{ home }}` | Домашний каталог |
| `{{ date }}` | Текущая дата (YYYY-MM-DD) |
| `{{ time }}` | Текущее время (HH:MM:SS) |
| `{{ distro }}` | Дистрибутив Linux |
| `{{ arch }}` | Архитектура (x86_64) |

## Специальные поля шагов

- `condition` — shell-выражение; шаг выполняется только если оно успешно
- `on_error` — поведение при ошибке: `abort` (стоп), `continue` (продолжить), `retry`
- `sudo` — если `true`, команда выполняется с повышенными привилегиями
- `timeout` — таймаут в секундах (по умолчанию нет ограничения)
- `workdir` — рабочая директория для выполнения команды
- `description` — описание шага для логирования
- `skip_if` — пропустить шаг если условие истинно

## Триггеры (автоматический запуск)

### cron — по расписанию
```yaml
trigger:
  type: cron
  schedule: "0 2 * * *"       # Каждый день в 2:00
  # Формат: мин час день месяц день_недели
```

### file — при изменении файла
```yaml
trigger:
  type: file
  path: "/etc/pacman.conf"
  events: [modify, create]     # modify | create | delete | move
```

### dbus — по системному событию
```yaml
trigger:
  type: dbus
  signal: "org.freedesktop.login1.PrepareForSleep"
  # Запуск перед сном системы
```

## Примеры полных макросов

### Обновление системы с бэкапом
```yaml
name: safe_update
description: "Безопасное обновление системы с предварительным снапшотом"
tags: [system, backup]
requires: [btrfs-progs, snapper]
confirm: true
variables:
  snapshot_desc: "Pre-update snapshot"
steps:
  - action: check
    params:
      condition: "mountpoint -q / && btrfs filesystem usage / >/dev/null 2>&1"
      message: "Корневая FS не BTRFS — снапшот невозможен"

  - action: shell
    params:
      command: "snapper -c root create -d '{{ snapshot_desc }}'"
      description: "Создание снапшота"
      sudo: true

  - action: shell
    params:
      command: "pacman -Syu --noconfirm"
      description: "Обновление пакетов"
      sudo: true
      timeout: 600
      on_error: abort

  - action: notify
    params:
      title: "Обновление завершено"
      body: "Система обновлена. Снапшот: {{ snapshot_desc }}"
```

### Настройка среды разработки
```yaml
name: setup_dev_env
description: "Настройка среды разработки Python"
tags: [development]
variables:
  python_version: "3.12"
  project_dir: "{{ home }}/projects/new-project"
steps:
  - action: shell
    params:
      command: "mkdir -p {{ project_dir }}"
      description: "Создание каталога проекта"

  - action: shell
    params:
      command: "python{{ python_version }} -m venv {{ project_dir }}/.venv"
      description: "Создание виртуального окружения"

  - action: shell
    params:
      command: "{{ project_dir }}/.venv/bin/pip install ruff pytest mypy"
      description: "Установка инструментов"

  - action: template
    params:
      src: "templates/pyproject.toml.j2"
      dst: "{{ project_dir }}/pyproject.toml"

  - action: notify
    params:
      title: "Проект создан"
      body: "Python {{ python_version }} проект готов в {{ project_dir }}"
```

### Очистка системы
```yaml
name: system_cleanup
description: "Очистка системы от мусора"
tags: [system, cleanup]
steps:
  - action: shell
    params:
      command: "paccache -rk2"
      description: "Очистка кэша pacman (оставить 2 версии)"
      sudo: true

  - action: shell
    params:
      command: "pacman -Rns $(pacman -Qdtq) 2>/dev/null || true"
      description: "Удаление осиротевших пакетов"
      sudo: true
      on_error: continue

  - action: shell
    params:
      command: "journalctl --vacuum-size=200M"
      description: "Ограничение размера журнала"
      sudo: true

  - action: shell
    params:
      command: "rm -rf ~/.cache/thumbnails/*"
      description: "Очистка кэша миниатюр"

  - action: shell
    params:
      command: "rm -rf ~/.local/share/Trash/*"
      description: "Очистка корзины"

  - action: notify
    params:
      title: "Очистка завершена"
      body: "Система очищена от временных файлов и мусора"
```

## API макросов (для разработчиков)

### Запуск макроса из Python
```python
from lina.tools.macro_engine import MacroEngine

engine = MacroEngine()
engine.load_macros("macros/")

# Запуск
result = engine.run("safe_update", variables={"snapshot_desc": "Before fix"})

# Список макросов
macros = engine.list_macros()

# Информация о макросе
info = engine.get_macro_info("safe_update")
```

### Создание пользовательских action
```python
from lina.tools.macro_engine import register_action

@register_action("custom_action")
def my_action(params, context):
    """Пользовательский action."""
    result = do_something(params["input"])
    context.set_variable("output", result)
    return {"success": True, "output": result}
```
