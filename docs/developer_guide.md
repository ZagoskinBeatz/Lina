# Руководство разработчика Lina

## Оглавление

1. [Добавление нового домена](#добавление-нового-домена)
2. [Добавление нового действия](#добавление-нового-действия)
3. [Добавление DBus-эндпоинта](#добавление-dbus-эндпоинта)
4. [Написание тестов](#написание-тестов)
5. [Стандарты кодирования](#стандарты-кодирования)
6. [Работа с governance pipeline](#работа-с-governance-pipeline)
7. [Работа с диагностическими деревьями](#работа-с-диагностическими-деревьями)

---

## Добавление нового домена

Пример: добавляем домен `bluetooth`.

### Шаг 1. Объявить домен в политике

Файл: `~/.config/lina/policy.toml`

```toml
[domains]
allowed = [
    "service", "package", "network", "disk", "config",
    "user", "boot", "display", "audio", "security",
    "installer", "desktop", "system", "safety", "general",
    "bluetooth"  # ← новый
]
```

### Шаг 2. Добавить домен в InputValidator

Файл: `security/input_validator.py`

```python
VALID_DOMAINS: frozenset = frozenset({
    "service", "package", "network", ... ,
    "bluetooth",  # ← новый
})
```

### Шаг 3. Определить уровень доступа

Файл: `access/levels.py`

```python
DOMAIN_ACCESS_MAP = {
    ...
    "bluetooth": AccessLevel.USER,  # или POWER / ADMIN
}
```

### Шаг 4. Добавить маппинг IntentType → домен (опционально)

Если нужна автоматическая классификация, в `intent/bridge.py`:

```python
_TYPE_TO_DOMAIN = {
    ...
    IntentType.QUERY: "bluetooth",  # если требуется
}
```

### Шаг 5. Зарегистрировать действия домена

```python
from lina.governance.action_registry import ActionDef, get_action_registry

registry = get_action_registry()
registry.register(ActionDef(
    id="bt_enable",
    domain="bluetooth",
    category="display_ops",
    command_template="bluetoothctl power on",
    description="Enable Bluetooth",
    description_ru="Включить Bluetooth",
    risk_level="low",
    timeout=10,
))
```

### Шаг 6. Написать тесты

```python
class TestBluetoothDomain(unittest.TestCase):
    def test_domain_allowed(self):
        ok, reason = get_input_validator().validate_domain("bluetooth")
        self.assertTrue(ok)

    def test_action_registered(self):
        reg = get_action_registry()
        self.assertTrue(reg.has("bt_enable"))
```

### Чеклист

- [ ] Домен добавлен в `VALID_DOMAINS`
- [ ] Домен добавлен в `policy.toml` → `[domains].allowed`
- [ ] Уровень доступа определён в `DOMAIN_ACCESS_MAP`
- [ ] Действия зарегистрированы в `ActionRegistry`
- [ ] Тесты написаны и проходят (323+ tests green)

---

## Добавление нового действия

Действия (actions) — whitelist-only. Незарегистрированное действие будет отклонено.

### Определение ActionDef

```python
from lina.governance.action_registry import (
    ActionDef, ActionRisk, ActionCategory, get_action_registry
)

action = ActionDef(
    id="net_wifi_scan",                        # уникальный ID
    domain="network",                          # домен
    category=ActionCategory.NETWORK_OPS,       # категория
    command_template="nmcli device wifi list",  # шаблон команды
    description="Scan WiFi networks",
    description_ru="Сканирование WiFi сетей",
    requires_root=False,
    risk_level=ActionRisk.LOW,
    destructive=False,
    reversible=True,
    dry_run_cmd="echo '[dry-run] would scan wifi'",
    verify_cmd="nmcli -t device wifi list | head -1",
    verify_pattern=".+",
    timeout=15,
    params=[],
    allowed_param_values={},
)

get_action_registry().register(action)
```

### Действие с параметрами

```python
ActionDef(
    id="svc_restart",
    domain="service",
    category=ActionCategory.SERVICE_CONTROL,
    command_template="systemctl restart {service_name}",
    params=["service_name"],
    allowed_param_values={
        "service_name": ["NetworkManager", "bluetooth", "cups"],
    },
    risk_level=ActionRisk.MEDIUM,
    requires_root=True,
)
```

**Безопасность параметров**:
- Допустимые символы: `[a-zA-Z0-9_\-./@\s]`
- Если задан `allowed_param_values` — значение ДОЛЖНО быть в списке
- Shell-метасимволы (`;&|$()`) → `BLOCKED`

### Массовая загрузка из JSON

```json
{
  "actions": [
    {
      "id": "bt_enable",
      "domain": "bluetooth",
      "category": "display_ops",
      "command_template": "bluetoothctl power on",
      "risk_level": "low",
      "timeout": 10
    }
  ]
}
```

```python
count = get_action_registry().load_from_file("path/to/actions.json")
print(f"Загружено {count} действий")
```

### Валидация при выполнении (автоматическая)

1. Действие существует в реестре? → иначе `BLOCKED`
2. Параметры переданы? → иначе `FAILED`  
3. Blacklist (14 паттернов: `rm -rf /`, `dd if=/dev/zero`, fork bomb...) → `BLOCKED`
4. Injection check (`;&|$()`, `../`) → `BLOCKED`
5. Allowed values check → `BLOCKED`
6. Param sanitization → strip опасных символов

---

## Добавление DBus-эндпоинта

Файл: `governance/dbus_service.py`

### Структура DBus-интерфейса

```
org.lina.Governance
├── diagnose(domain: str) → str          # Диагностика
├── execute_action(json: str) → str      # Выполнение действия
├── health_check() → str                 # Проверка здоровья
├── get_version() → str                  # Версия API  (v2)
├── get_capabilities() → str             # Список возможностей
└── get_escalation(esc_id: str) → str    # Получить эскалацию
```

### Добавление нового метода

```python
# В governance/dbus_service.py, класс LinaGovernanceService

@dbus_interface.method()
def my_new_method(self, arg1: str) -> str:
    """Описание метода."""
    # 1. Валидация входных данных (ОБЯЗАТЕЛЬНО)
    validator = get_input_validator()
    ok, reason = validator.validate_domain(arg1)  # или другая валидация
    if not ok:
        return json.dumps({"status": "error", "reason": reason})

    # 2. Маршрутизация через governance (ОБЯЗАТЕЛЬНО)
    bridge = get_intent_bridge()
    result = bridge.from_action(
        action_id="my_action",
        domain="my_domain",
        params={"key": arg1},
        source="dbus",   # ← ВАЖНО: source всегда "dbus"
    )

    # 3. Возврат JSON
    return json.dumps({
        "status": result.status.value,
        "response": result.response_text or "",
    })
```

### Правила для DBus-эндпоинтов

1. **Валидация ДО governance** — используйте `InputValidator`
2. **source="dbus"** — для аудита и rate-limiting (trust level 1)
3. **Формат ответа** — всегда JSON-строка
4. **IPC API versioning** — метод `get_version()` возвращает текущую версию
5. **Ограничение размера** — JSON payload ≤ 8192 bytes

---

## Написание тестов

### Фреймворк

Lina использует `unittest` (stdlib) + `unittest.mock`. **Не pytest**.

### Структура тестового файла

```python
"""Тесты для модуля XYZ — Phase N."""
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

class TestXYZ(unittest.TestCase):
    """Тесты для класса XYZ."""

    def setUp(self):
        """Сброс singleton-состояния перед каждым тестом."""
        import lina.module.submodule as mod
        mod._singleton_instance = None  # сброс singleton

    def tearDown(self):
        """Очистка после теста."""
        pass

    def test_basic_feature(self):
        """Проверка базовой функциональности."""
        result = my_function()
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "success")

    def test_edge_case(self):
        """Проверка граничного случая."""
        with self.assertRaises(ValueError):
            my_function(None)

if __name__ == "__main__":
    unittest.main()
```

### Паттерны тестирования

#### Сброс синглтонов

```python
def setUp(self):
    import lina.intent.bridge as bridge_mod
    bridge_mod._bridge = None
    import lina.governance.policy_engine as pe_mod
    pe_mod._engine = None
```

#### Мокирование external I/O

```python
@patch("lina.governance.action_registry.subprocess.run")
def test_execute_action(self, mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="OK",
        stderr="",
    )
    result = registry.execute("net_wifi_scan")
    self.assertEqual(result.status, ExecStatus.SUCCESS)
```

#### Тестирование governance pipeline

```python
def test_denied_by_policy(self):
    intent = Intent(
        type=IntentType.SYSTEM_ACTION,
        domain="disk",
        action="disk_partition",
        source="dbus",  # low trust
    )
    result = router.process(intent)
    self.assertEqual(result.status, IntentStatus.DENIED)
```

### Запуск тестов

```bash
# Все тесты
cd lina && python -m pytest tests/ -v

# Или через custom runner
python run_all_tests.py

# Один файл
python -m unittest tests/test_phase5_integration.py -v

# Один тест
python -m unittest tests.test_phase5_integration.TestInputValidation.test_null_bytes -v
```

### Правила тестирования

1. **Каждый enforcement point** должен иметь тест: валидация, deny, confirm, success
2. **Adversarial тесты** — проверяйте обход: injection, obfuscation, bypass
3. **Singleton reset** — всегда сбрасывайте singleton в `setUp()`
4. **Нет реальных subprocess** — мокируйте все вызовы `subprocess.run`
5. **Русские docstrings** — описывайте ЧТО проверяется
6. **Имена тестов** — `test_<feature>_<scenario>` (англ.)
7. **Целевое покрытие** — каждый модуль ≥ 1 позитивный + 1 негативный тест

### Текущий тестовый набор

| Файл | Тестов | Описание |
|------|--------|----------|
| `test_architecture_v07.py` | 90 | Архитектурные контракты |
| `test_phase1_integration.py` | 42 | Intent, GUI, DBus, Access |
| `test_phase2_integration.py` | 47 | Audit, Policy, IPC |
| `test_phase3_integration.py` | 37 | Shell, Hotkeys, State |
| `test_phase4_integration.py` | 40 | UX, ResponseFormatter |
| `test_phase5_integration.py` | 67 | Security, InputValidator, Adversarial |
| **Итого** | **323** | **Все зелёные** |

---

## Стандарты кодирования

### Общие правила

| Правило | Стандарт |
|---------|----------|
| Python | 3.11+ (3.14 в production) |
| Типизация | type hints обязательны для public API |
| Docstrings | Русский язык. Описание + инварианты |
| Imports | stdlib → third-party → lina. Без `import *` |
| Singletons | `_instance = None` + `get_<name>()` функция |
| Enums | `str, Enum` pattern для JSON-сериализации |
| Dataclasses | `@dataclass` для DTO. `__slots__` где возможно |
| Error handling | Catch конкретные исключения, log + return safe default |
| Logging | `logging.getLogger(__name__)`. Debug → Info → Warning → Error |

### Архитектурные контракты

**ОБЯЗАТЕЛЬНО**:
- Все точки входа → через `IntentBridge` (from_text / from_action / from_diagnose)
- Все действия → через `ActionRegistry` (whitelist-only)
- Все решения → через governance (Access → Policy → Audit)
- Нет прямых вызовов `subprocess.run()` из UI/CLI/GUI
- Response → через `ResponseFormatter.format_result()`

**ЗАПРЕЩЕНО**:
- Обход governance pipeline
- Отключение AuditLogger после `lock_enabled()`
- Прямой import и вызов safety/security модулей из UI
- Hardcoded пароли, ключи, PII в коде или логах
- `shell=True` без governance-контролируемого sandbox

### Формат коммитов

```
<тип>(<область>): краткое описание

Подробное описание, если нужно.

Closes: #123
```

Типы: `feat`, `fix`, `docs`, `test`, `refactor`, `security`, `perf`

Области: `governance`, `intent`, `access`, `security`, `safety`, `core`, `shell`, `gui`, `voice`, `rag`, `diag`, `ci`

---

## Работа с governance pipeline

### Полный цикл выполнения действия

```python
from lina.intent.bridge import get_intent_bridge
from lina.intent.types import IntentStatus

bridge = get_intent_bridge()

# 1. Через текст (основной путь)
result = bridge.from_text(
    "перезапусти NetworkManager",
    source="cli",
    pipeline_handler=my_llm_handler,  # для CHAT/QUERY fallback
)

# 2. Через прямое действие
result = bridge.from_action(
    action_id="svc_restart",
    domain="service",
    params={"service_name": "NetworkManager"},
    source="ui",
)

# 3. Обработка результата
if result.status == IntentStatus.SUCCESS:
    print(result.response_text)
elif result.status == IntentStatus.DENIED:
    print(f"Отказано: {result.policy_decision}")
elif result.status == IntentStatus.NEEDS_CONFIRM:
    print(f"Требуется подтверждение: {result.escalation_id}")
elif result.status == IntentStatus.FAILED:
    print(f"Ошибка выполнения: {result.response_text}")
```

### Диагностика

```python
result = bridge.from_diagnose(
    domain="network",
    user_text="WiFi не работает",
    source="cli",
)
# result.response_text содержит диагностический отчёт
```

---

## Работа с диагностическими деревьями

### Создание нового дерева

Файл: `diagnostics/trees/bluetooth_not_working.json`

```json
{
  "id": "bluetooth_not_working",
  "name": "Bluetooth не работает",
  "category": "bluetooth",
  "triggers": [
    "bluetooth не работает",
    "блютуз не подключается",
    "bt не видит устройства"
  ],
  "steps": [
    {
      "id": "check_service",
      "description": "Проверка сервиса bluetooth",
      "check": "systemctl is-active bluetooth",
      "parse": "^active$",
      "if_match": {
        "next": "check_rfkill"
      },
      "if_no_match": {
        "diagnosis": "Сервис bluetooth не запущен",
        "solution": "systemctl start bluetooth",
        "explanation": "Bluetooth-сервис отключён. Запуск решит проблему.",
        "severity": "medium",
        "requires_root": true,
        "next": null
      }
    },
    {
      "id": "check_rfkill",
      "description": "Проверка RF-блокировки",
      "check": "rfkill list bluetooth",
      "parse": "Soft blocked: yes",
      "if_match": {
        "diagnosis": "Bluetooth заблокирован программно",
        "solution": "rfkill unblock bluetooth",
        "explanation": "RF-блокировка активна. Разблокировка восстановит работу.",
        "severity": "low",
        "requires_root": false,
        "next": null
      },
      "if_no_match": {
        "diagnosis": "Bluetooth-адаптер активен, но устройства не видны",
        "solution": "bluetoothctl scan on",
        "explanation": "Адаптер работает. Запустите сканирование.",
        "severity": "info",
        "requires_root": false,
        "next": null
      }
    }
  ]
}
```

### Правила деревьев

1. **id** — уникальный snake_case идентификатор
2. **triggers** — ≥ 3 варианта пользовательского ввода (для fuzzy match)
3. **check** — БЕЗОПАСНАЯ read-only команда (без rm, mkfs, dd и т.д.)
4. **parse** — regex для сопоставления с stdout
5. **severity** — `info`, `low`, `medium`, `high`, `critical`
6. **next** — `null` для завершения, ID следующего шага для продолжения
7. **Циклы запрещены** — движок отслеживает visited steps (max 30)

### Тестирование дерева

```python
from lina.diagnostics.engine import DiagnosticEngine

engine = DiagnosticEngine()
engine.load_tree_from_dict({
    "id": "test_tree",
    "name": "Test",
    "category": "test",
    "triggers": ["test problem"],
    "steps": [...]
})

report = engine.run_diagnostic("test_tree")
self.assertIsNotNone(report.final_diagnosis)
self.assertGreater(report.confidence, 0.5)
```
