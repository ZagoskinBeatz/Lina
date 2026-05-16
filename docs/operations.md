# Руководство по эксплуатации Lina

## Оглавление

1. [Развёртывание](#развёртывание)
2. [Конфигурация](#конфигурация)
3. [Мониторинг и Health Checks](#мониторинг-и-health-checks)
4. [Логирование](#логирование)
5. [Обновление и миграция](#обновление-и-миграция)
6. [Устранение неполадок](#устранение-неполадок)

---

## Развёртывание

### Требования

| Компонент | Минимум | Рекомендуется |
|-----------|---------|---------------|
| Python | 3.11+ | 3.14 |
| RAM | 2 GB | 8 GB (с LLM) |
| Disk | 500 MB | 10 GB (с моделями) |
| OS | Linux (systemd) | Arch / Fedora / Ubuntu |
| GPU | — | NVIDIA / AMD (для локальных LLM) |

### Установка

```bash
# 1. Клонировать
git clone <repo-url> ~/AI/lina
cd ~/AI/lina

# 2. Виртуальное окружение
python -m venv ../.venv
source ../.venv/bin/activate

# 3. Зависимости
pip install -r requirements.txt

# 4. Первый запуск (создаёт конфиги)
python lina.py
```

### Структура каталогов в runtime

```
~/.config/lina/
├── policy.toml           # Политики governance
├── config.toml           # Основные настройки
└── actions/              # Пользовательские действия (JSON)

~/.local/share/lina/
├── audit/
│   └── audit.jsonl       # Аудит-лог
├── cache/
│   ├── command_history.json
│   └── response_cache.json
├── models/               # Локальные LLM модели
└── knowledge/            # База знаний

~/.cache/lina/
├── logs/
│   └── lina.log          # Основной лог
└── chroma_db/
    ├── tfidf_index.json
    └── vector_index.json
```

### Shell integration (fish / bash)

```bash
# Fish
cp lina.fish ~/.config/fish/functions/

# Bash
source lina.sh
```

---

## Конфигурация

### policy.toml — Governance Policy

```toml
[general]
max_auto_risk = "medium"          # Максимальный риск без подтверждения
require_confirmation_above = "high"  # Порог подтверждения
always_block_critical = true       # Блокировать critical-действия
dry_run_default = true             # Dry-run по умолчанию

[domains]
allowed = [
    "service", "package", "network", "disk", "config",
    "user", "boot", "display", "audio", "security",
    "installer", "desktop", "system", "safety", "general"
]
blocked = []

[actions]
blocked = []
always_confirm = [
    "pkg_remove", "pkg_update",
    "boot_grub_install", "boot_systemd_install",
    "inst_pacstrap"
]

[rate_limit]
enabled = true
window = 60            # Секунды
max_actions = 20       # Глобальный лимит за окно
per_action = 5         # Лимит на одно действие за окно

[audit]
enabled = true
path = ""              # По умолчанию: ~/.local/share/lina/audit/audit.jsonl

[network]
allow_internet = false  # DENY по умолчанию
allowed_urls = []

[installer]
installer_mode = false
allowed_extra = []
```

### Рекомендации по настройке

| Сценарий | Настройка |
|----------|-----------|
| Максимальная безопасность | `max_auto_risk = "none"`, `dry_run_default = true` |
| Повседневная работа | `max_auto_risk = "medium"`, `dry_run_default = false` |
| Установка ОС | `installer_mode = true`, добавить действия в `allowed_extra` |
| Опытный пользователь | `require_confirmation_above = "critical"` |
| Корпоративная среда | `blocked_domains = ["installer", "boot"]`, `rate_limit.max_actions = 10` |

---

## Мониторинг и Health Checks

### DBus health check

```bash
# Через busctl (systemd)
busctl call org.lina.Governance /org/lina/Governance \
    org.lina.Governance health_check

# Ответ: JSON
# {
#   "status": "healthy",
#   "version": "2",
#   "uptime_seconds": 3600,
#   "components": {
#     "policy_engine": "ok",
#     "action_registry": "ok",
#     "audit_logger": "ok",
#     "input_validator": "ok"
#   }
# }
```

### Программный health check

```python
from lina.governance.dbus_service import LinaGovernanceService

service = LinaGovernanceService()
result = json.loads(service.health_check())
assert result["status"] == "healthy"
```

### Метрики

```python
from lina.governance.policy_engine import get_policy_engine
from lina.governance.action_registry import get_action_registry
from lina.access.resolver import get_access_resolver

# Статистика политик
policy_stats = get_policy_engine().get_stats()
# {"total_checks": 150, "allow": 120, "deny": 15, "confirm": 10, "rate_limited": 5}

# Статистика действий
action_stats = get_action_registry().get_stats()
# {"total_actions": 53, "by_category": {...}, "by_risk": {...}}

# Статистика доступа
access_stats = get_access_resolver().get_stats()
# {"total_checks": 200, "allowed": 180, "denied": 20, "session_level": "USER"}
```

### Что мониторить

| Метрика | Порог | Действие |
|---------|-------|----------|
| `policy.deny` > 50% | Высокий | Проверить конфигурацию или подозрительную активность |
| `rate_limited` > 0 | Нормально | Информационно — система работает |
| `audit.log` size > 100MB | Предупреждение | Проверить rotation, уменьшить window |
| `health_check` ≠ "healthy" | Критический | Перезапустить сервис |
| `access.denied` spike | Подозрительно | Проверить audit log |

---

## Логирование

### Аудит-лог (governance)

**Формат**: JSONL (один JSON-объект на строку)

```json
{"timestamp": "2025-01-15T10:30:00", "event": "action_allowed", "action_id": "svc_restart", "domain": "service", "source": "cli", "risk": "medium", "decision": "ALLOW"}
{"timestamp": "2025-01-15T10:30:05", "event": "action_denied", "action_id": "disk_format", "domain": "disk", "source": "dbus", "risk": "critical", "decision": "DENY", "reason": "always_block_critical"}
```

**Свойства**:
- Нет PII (user_text хешируется или не записывается)
- Lock protection: после `lock_enabled()` аудит нельзя отключить
- Автоматическая rotation при 10 MB
- Путь: `~/.local/share/lina/audit/audit.jsonl`

### Application log

**Путь**: `~/.cache/lina/logs/lina.log`

```
2025-01-15 10:30:00 INFO  [intent.bridge] from_text: classified as SYSTEM_ACTION domain=service
2025-01-15 10:30:00 INFO  [intent.router] process: access=ALLOWED policy=ALLOW
2025-01-15 10:30:01 INFO  [governance.action_registry] execute: svc_restart status=SUCCESS duration=1.2s
```

### Просмотр логов

```bash
# Последние 50 аудит-событий
tail -50 ~/.local/share/lina/audit/audit.jsonl | python -m json.tool --no-ensure-ascii

# Все DENY за сегодня
grep '"DENY"' ~/.local/share/lina/audit/audit.jsonl | tail -20

# Security violations
grep 'security_violation' ~/.local/share/lina/audit/audit.jsonl

# Rate-limited events
grep 'rate_limit' ~/.local/share/lina/audit/audit.jsonl
```

### Ротация логов

Аудит-лог ротируется автоматически при достижении `_max_file_size` (10 MB по умолчанию):
- Текущий файл переименовывается с суффиксом `.1`, `.2` и т.д.
- Создаётся новый пустой файл
- Старые файлы НЕ удаляются автоматически — настройте cron/systemd-timer:

```bash
# Удалять логи старше 30 дней
find ~/.local/share/lina/audit/ -name "audit.jsonl.*" -mtime +30 -delete
```

---

## Обновление и миграция

### Обновление

```bash
cd ~/AI/lina
git pull
pip install -r requirements.txt

# Проверить совместимость конфигурации
python -c "
from lina.governance.policy_engine import get_policy_engine
engine = get_policy_engine()
print(f'Policy loaded: {len(engine._config.allowed_domains)} domains')
print(f'Status: OK')
"
```

### Миграция конфигурации

При обновлении major version проверьте:

1. **policy.toml** — новые домены автоматически не добавляются.
   Сравните с дефолтной конфигурацией:
   ```bash
   diff ~/.config/lina/policy.toml lina/defaults/policy.toml
   ```

2. **DBus API versioning** — метод `get_version()` возвращает текущую версию.
   При изменении API клиенты должны проверять версию.

3. **Действия** — новые builtin-действия добавляются автоматически при init.
   Пользовательские действия сохраняются.

### Резервное копирование

```bash
# Конфигурация + данные
tar czf lina-backup-$(date +%Y%m%d).tar.gz \
    ~/.config/lina/ \
    ~/.local/share/lina/ \
    --exclude="*.log.*"
```

---

## Устранение неполадок

### Общие проблемы

#### «Действие отклонено» (DENIED)

```bash
# 1. Проверить аудит — почему DENY
grep 'DENY' ~/.local/share/lina/audit/audit.jsonl | tail -5

# 2. Частые причины:
# - Домен не в allowed_domains → добавить в policy.toml
# - Действие в blocked_actions → убрать из policy.toml
# - Critical risk → always_block_critical = true (default)
# - Source trust слишком низкий (dbus → trust=1)
# - Rate limit exceeded
```

#### «Требуется подтверждение» (NEEDS_CONFIRM)

```
# Причины:
# - Действие в always_confirm list
# - Risk > require_confirmation_above
# - Source dbus/hotkey → всегда confirm для power+
# - 3+ failures на этом действии → force confirm
```

#### «Действие не найдено» (NOT_FOUND)

```python
# Проверить реестр
from lina.governance.action_registry import get_action_registry
reg = get_action_registry()
print(reg.list_actions(domain="network"))  # все действия домена
```

#### DBus не работает

```bash
# Проверить сервис
busctl list | grep lina

# Проверить версию
busctl call org.lina.Governance /org/lina/Governance \
    org.lina.Governance get_version

# Перезапустить
systemctl --user restart lina-governance.service
```

#### GUI не запускается

```bash
# Проверить PyQt6
python -c "from PyQt6.QtWidgets import QApplication; print('OK')"

# Проверить DISPLAY
echo $DISPLAY
echo $WAYLAND_DISPLAY

# Запустить в debug mode
LINA_DEBUG=1 python lina.py --gui 2>&1 | tee /tmp/lina-debug.log
```

#### LLM не отвечает

```bash
# Проверить наличие модели
ls ~/.local/share/lina/models/

# Проверить ollama (если используется)
ollama list
curl http://localhost:11434/api/tags

# Fallback: Lina работает без LLM (diagnostics, actions, KB)
```

### Диагностические команды

```bash
# Полная диагностика
python -c "
from lina.governance.policy_engine import get_policy_engine
from lina.governance.action_registry import get_action_registry
from lina.access.resolver import get_access_resolver
from lina.security.input_validator import get_input_validator

pe = get_policy_engine()
ar = get_action_registry()
ac = get_access_resolver()
iv = get_input_validator()

print(f'PolicyEngine: {len(pe._config.allowed_domains)} domains, config OK')
print(f'ActionRegistry: {len(ar._actions)} actions registered')
print(f'AccessResolver: session={ac._session_level.value}')
print(f'InputValidator: max_input={iv._max_input_length}')
print(f'Status: ALL OK')
"
```
