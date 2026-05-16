# Contributing to Lina

Спасибо за интерес к проекту!

## Архитектурные правила (ОБЯЗАТЕЛЬНО)

Перед написанием кода прочитайте [docs/architecture.md](docs/architecture.md).

**Нарушение этих правил = автоматический reject PR:**

1. **Все входы через IntentBridge** — UI, CLI, GUI, DBus, Hotkey → `IntentBridge.from_text()` или `.from_action()`. Прямые вызовы `subprocess.run()` из UI запрещены.
2. **Все действия через ActionRegistry** — whitelist-only. Незарегистрированное действие = BLOCKED.
3. **Governance не обходится** — Access → Policy → Audit pipeline обязателен для любого действия.
4. **Audit не отключается** — после `lock_enabled()` отключить аудит невозможно.
5. **InputValidator для всех входов** — размер, null bytes, obfuscation, allowlists.
6. **Нет PII в логах** — user_text хешируется, пароли/ключи не логируются.

## Как внести вклад

### 1. Сообщить об ошибке

Создайте Issue с описанием:

- Что произошло
- Что ожидалось
- Шаги воспроизведения
- Дистрибутив и версия Lina

### 2. Предложить фичу

Создайте Issue с тегом `feature-request`:

- Описание функциональности
- Почему это полезно
- Примеры использования
- Влияние на governance (если есть)

### 3. Прислать код

```bash
# Fork + clone
git clone https://github.com/YOUR_USER/lina.git
cd lina

# Создать ветку
git checkout -b feat/my-feature

# Виртуальное окружение
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install -r requirements.txt

# Внести изменения ...

# Запустить основной набор тестов
python -m pytest tests -q

# Или адресно:
python -m pytest tests/test_architecture_v07.py -q
python -m pytest tests/test_phase1_integration.py -q
python -m pytest tests/test_phase2_integration.py -q
python -m pytest tests/test_phase3_integration.py -q
python -m pytest tests/test_phase4_integration.py -q
python -m pytest tests/test_phase5_integration.py -q

# Commit + push
git add .
git commit -m "feat(governance): описание изменения"
git push origin feat/my-feature
```

Создайте Pull Request.

## Формат коммитов

```text
<тип>(<область>): краткое описание

Подробное описание (опционально).

Closes: #123
```

**Типы**: `feat`, `fix`, `docs`, `test`, `refactor`, `security`, `perf`

**Области**: `governance`, `intent`, `access`, `security`, `safety`, `core`, `shell`, `gui`, `voice`, `rag`, `diag`, `ci`, `installer`

Примеры:

- `feat(governance): add bluetooth domain to policy engine`
- `fix(security): tighten base64 obfuscation regex`
- `test(intent): add adversarial injection tests`
- `docs(architecture): update trust boundary diagram`

## Принципы

1. **Zero-trust** — все входные данные считаются враждебными
2. **Governance = truth** — governance принимает решения, UI только отображает
3. **Offline-first** — никаких облачных API
4. **Defense-in-depth** — validation → access → policy → safety → audit
5. **Тесты обязательны** — каждый enforcement point покрыт тестами
6. **Русский + English** — все тексты через i18n
7. **Модульность** — каждый компонент изолирован

## Структура кода

```text
lina/
├── intent/         # Intent Layer (bridge, router, types)
├── governance/     # Governance (policy, audit, actions, confirmation, dbus)
├── access/         # Access Control (resolver, levels)
├── security/       # Input Validation (input_validator)
├── safety/         # Safety Validator (models, validator)
├── core/           # Core Pipeline, UX, REPL, CLI
├── shell/          # Shell Commander (governed)
├── rag/            # RAG (indexer, retriever)
├── diagnostics/    # Diagnostic Engine + 21 trees
├── knowledge/      # База знаний (markdown)
├── system/         # OS integration (systemd, pacman, network, hw)
├── gui/            # GUI PyQt6 (chat, tray, theme, settings)
├── voice/          # Voice I/O (STT/TTS)
├── installer/      # Packaging, first-run, updater
├── agent/          # Agent (evaluator, executor, planner)
├── learning/       # Learning from interactions
├── cv/             # Computer vision (OCR, GUI detection)
├── metrics/        # System metrics collection
├── docs/           # Documentation
└── tests/          # Основной pytest-набор
```

## Code Style

| Правило | Стандарт |
| --- | --- |
| Python | 3.11+ |
| Type hints | Обязательны для public API |
| Docstrings | Русский язык. Описание + инварианты |
| Imports | stdlib → third-party → lina. Без `import *` |
| Singletons | `_instance = None` + `get_<name>()` |
| Enums | `str, Enum` для JSON-сериализации |
| Dataclasses | `@dataclass` для DTO |
| Error handling | Catch конкретные exceptions |
| Logging | `logging.getLogger(__name__)` |

## Тестирование

### Правила

1. Фреймворк: **pytest**
2. Каждый enforcement point — минимум 1 позитивный + 1 негативный тест
3. Adversarial тесты: injection, obfuscation, bypass
4. Singleton reset в `setUp()`
5. Mock все `subprocess.run()` вызовы
6. Русские docstrings для тестовых классов

### Запуск

```bash
# Все тесты
python -m pytest tests -q

# Должны быть зелёные тесты без падений
```

## Лицензия

Отправляя PR, вы соглашаетесь с лицензией MIT.
