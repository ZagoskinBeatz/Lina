# Lina — Governance-Based ИИ-ассистент для Linux

**v1.0.0** · MIT License · pytest-based test suite · Offline-first

Контролируемый ИИ-ассистент с zero-trust архитектурой, governance pipeline,
локальной LLM (3B–13B), RAG, диагностикой, голосом (STT/TTS) и GUI.

## Архитектура

```text
User Input → InputValidator → IntentBridge → IntentRouter
                                                │
                              ┌─────────────────┤
                              ▼                 ▼
                        AccessResolver    PolicyEngine
                              │                 │
                              └────────┬────────┘
                                       ▼
                              ActionRegistry (whitelist-only)
                                       │
                                       ▼
                              AuditLogger (JSONL, locked)
                                       │
                                       ▼
                              ResponseFormatter → User
```

Подробнее: [docs/architecture.md](docs/architecture.md)

## Структура проекта

```text
lina/
├── intent/           # Intent Layer — bridge, router, types
├── governance/       # Governance — policy, audit, actions, confirmation, dbus
├── access/           # Access Control — 3-level resolver
├── security/         # Input Validation — zero-trust validator
├── safety/           # Safety — 60+ patterns, 3-tier check
├── core/             # Core — pipeline, UX, REPL, CLI, degradation
├── shell/            # Shell Commander — governed ! commands
├── rag/              # RAG — TF-IDF + BM25, auto-learner
├── diagnostics/      # Diagnostics — engine + 21 JSON decision trees
├── knowledge/        # Knowledge base (markdown docs)
├── system/           # OS integration (systemd, pacman, network, hw)
├── gui/              # GUI PyQt6 (chat, tray, theme, settings)
├── voice/            # Voice I/O (Whisper/Vosk STT, Piper/eSpeak TTS)
├── agent/            # Agent (evaluator, executor, planner, memory)
├── learning/         # Learning from user interactions
├── cv/               # Computer Vision (OCR, GUI detection)
├── installer/        # Packaging, first-run wizard, updater
├── metrics/          # System metrics collection
├── docs/             # Documentation
│   ├── architecture.md     # Architecture & trust boundaries
│   ├── developer_guide.md  # Developer guide (how to extend)
│   ├── security_model.md   # Threat model & mitigations
│   ├── operations.md       # Operations & troubleshooting
│   └── api.md              # API reference
└── tests/            # Основной pytest-набор
```

## Быстрый старт

```bash
cd lina
python -m venv ../.venv && source ../.venv/bin/activate
pip install -r requirements.txt
python -m lina                # CLI REPL
python -m lina --gui          # GUI
python -m lina --first-run    # Мастер первого запуска
```

Подробнее: [INSTALL.md](INSTALL.md)

## Governance Pipeline

Каждый запрос проходит 6-шаговый pipeline:

1. **InputValidator** — size, null bytes, obfuscation, allowlists
2. **IntentBridge** — classify → Intent type + domain
3. **AccessResolver** — access level, source trust, rate limits
4. **PolicyEngine** — domain rules, risk, confirmation thresholds
5. **ActionRegistry** — whitelist, blacklist, injection check, sandbox
6. **AuditLogger** — every decision recorded (no PII, locked)

## Безопасность

| Слой | Защита |
| --- | --- |
| Validation | 4096 char limit, null bytes, Unicode NFC, obfuscation (8+ patterns) |
| Access | 3 levels (USER/POWER/ADMIN), source trust scoring, rate limits |
| Policy | Domain allowlists, risk thresholds, blocked actions |
| Execution | 53 whitelisted actions, 14 blacklist regex, injection detection |
| Safety | 60+ security patterns, 3-tier check (whitelist → pattern → LLM) |
| Audit | JSONL, no PII, lock protection, 10MB auto-rotation |
| Output | Traceback stripping, system prompt leak detection, domain advice |

Подробнее: [docs/security_model.md](docs/security_model.md)

## Команды

### Системные

| Команда | Описание |
| --- | --- |
| `!<команда>` | Shell-команда (через governance) |
| `статус системы` | CPU, RAM, swap |
| `процессы` | Топ процессов по RAM |

### Диагностика

| Команда | Описание |
| --- | --- |
| `wifi не работает` | Авто-диагностика (21 дерево) |
| `нет звука` | Diagnostic tree для audio |

### База знаний (RAG)

| Команда | Описание |
| --- | --- |
| `индексируй` | Индексировать документы |
| `поиск в базе: <запрос>` | Поиск по базе знаний |

### Мета-команды

| Команда | Описание |
| --- | --- |
| `/help` | Справка |
| `/статус` | Полный статус Lina |
| `/выход` | Выход |

### LLM-запросы

Любой текст → LLM с контекстом из RAG.

## Тестирование

```bash
python -m pytest tests -q
```

| Тесты | Кол-во | Описание |
| --- | --- | --- |
| Architecture | 90 | Архитектурные контракты |
| Phase 1–5 | 233 | Intent, GUI, Audit, Shell, Security |
| GUI | 200+ | PyQt6 виджеты, чат, терминал |
| Engine & Tools | 450+ | Диагностика, RAG, безопасность |
| Voice | 82 | STT, TTS, VoicePipeline |
| Packaging | 75 | CLI, installer, генераторы пакетов |

## Документация

- [Architecture](docs/architecture.md) — архитектура, trust boundaries, модули
- [Developer Guide](docs/developer_guide.md) — как расширять систему
- [Security Model](docs/security_model.md) — модель угроз, защита
- [Operations](docs/operations.md) — эксплуатация, мониторинг, логи
- [Contributing](CONTRIBUTING.md) — правила контрибуции
- [Install](INSTALL.md) — установка

## Принципы

1. **Zero-trust** — все входные данные враждебные
2. **Governance = truth** — governance решает, UI отображает
3. **Offline-first** — никаких облачных сервисов
4. **Defense-in-depth** — validation → access → policy → safety → audit
5. **No bypass** — нет пути обхода governance
6. **Audit everything** — каждое решение логируется
7. **Graceful degradation** — при отказе компонента система сообщает и продолжает

## Ресурсы

- **Runtime**: < 100 MB RAM (без LLM)
- **LLM**: 1-8 GB RAM (в зависимости от модели)
- **CPU**: контролируется через BudgetGovernor

## Лицензия

См. [LICENSE](../LICENSE)
