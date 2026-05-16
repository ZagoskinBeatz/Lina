# Архитектура Lina

## Обзор

Lina — governance-based контролируемый ИИ-ассистент для Linux.
Вся архитектура построена на принципе **zero-trust**: ни один компонент UI
не выполняет действия напрямую. Все операции проходят через governance pipeline.

## Контрольная плоскость vs плоскость исполнения

```
┌──────────────────────────────────────────────────────────────────┐
│                     CONTROL PLANE                                │
│                                                                  │
│  UI / CLI / DBus / Hotkeys                                       │
│       │                                                          │
│       ▼                                                          │
│  ┌─────────────┐    Phase 5: InputValidator                      │
│  │ IntentBridge │◄── validate_text / validate_source / sanitize  │
│  └──────┬──────┘                                                 │
│         ▼                                                        │
│  ┌─────────────┐                                                 │
│  │ IntentRouter │    governance pipeline orchestrator             │
│  └──────┬──────┘                                                 │
│         ├──► AccessResolver   (access levels, rate limits)       │
│         ├──► PolicyEngine     (rules, domains, risk, rate)       │
│         ├──► Confirmation     (escalation, user consent)         │
│         └──► AuditLogger      (JSONL, no PII, locked)           │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                     EXECUTION PLANE                              │
│                                                                  │
│  ActionRegistry    (whitelist-only, sandbox, timeout)            │
│  KnowledgeBase     (KB local + user, diagnostics)               │
│  LLM Pipeline      (model router, RAG, prompt engine)           │
│  DiagnosticsEngine (21 JSON decision trees)                      │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                     UX LAYER (Phase 4)                           │
│                                                                  │
│  ResponseFormatter  (format_result → human-friendly text)        │
│  PostProcessor      (strip debug, leaks, raw tool output)        │
│  DegradationStrategy (fallback messages)                         │
└──────────────────────────────────────────────────────────────────┘
```

## Жизненный цикл Intent

Каждый пользовательский запрос проходит полный цикл:

```
User Input
    │
    ▼
1. InputValidator.validate_text()     ← Phase 5: size, null, obfuscation
    │
    ▼
2. IntentBridge.from_text()           ← classify → IntentType + domain
    │
    ▼
3. Intent.__post_init__()             ← Phase 5: truncate, clamp, validate
    │
    ▼
4. IntentRouter.process(intent)
    │
    ├─► Chat/Query? ──► skip governance ──► LLM pipeline
    │
    ├─► AccessResolver.check()        ← access level, rate limit, source trust
    │     │
    │     └─► DENIED? ──► return DENIED + audit
    │
    ├─► PolicyEngine.check()          ← domain rules, risk, content safety
    │     │
    │     ├─► DENY? ──► return DENIED + audit
    │     └─► CONFIRM? ──► EscalationManager → return NEEDS_CONFIRM
    │
    ├─► DiagnosticsEngine             ← if DIAGNOSE type
    │
    └─► ActionRegistry.execute()      ← if action type
          │
          └─► SafetyValidator → sandbox → result
    │
    ▼
5. AuditLogger.log()                  ← every decision recorded
    │
    ▼
6. ResponseFormatter.format_result()  ← Phase 4: human-friendly UX
    │
    ▼
User sees result
```

## Границы доверия (Trust Boundaries)

```
┌─────────────────────────────────────────────────────┐
│ UNTRUSTED                                           │
│                                                     │
│  External User Input (CLI, GUI, DBus, Hotkey)       │
│  IPC messages (DBus payloads)                       │
│  shell ! commands                                   │
│                                                     │
├─────────────── VALIDATION GATE ─────────────────────┤
│                                                     │
│  InputValidator (Phase 5)                           │
│    ✓ max length (4096 chars)                        │
│    ✓ null byte detection                            │
│    ✓ control character stripping                    │
│    ✓ Unicode NFC normalization                      │
│    ✓ obfuscation detection (base64, eval, backtick) │
│    ✓ source allowlist (ui, cli, dbus, hotkey, ...)  │
│    ✓ domain allowlist (15 known domains)            │
│    ✓ action ID format validation                    │
│    ✓ params depth/size/content limits               │
│                                                     │
├─────────────── GOVERNANCE GATE ─────────────────────┤
│                                                     │
│  TRUSTED (governance decides)                       │
│                                                     │
│  AccessResolver → PolicyEngine → Confirmation       │
│  AuditLogger records every decision                 │
│                                                     │
├─────────────── EXECUTION GATE ──────────────────────┤
│                                                     │
│  CONTROLLED EXECUTION                               │
│                                                     │
│  ActionRegistry (whitelist-only, sandbox, timeout)   │
│  SafetyValidator (60+ patterns, 14 blacklist)       │
│  SubprocessSandbox (shell=True with sanitization)   │
│                                                     │
└─────────────────────────────────────────────────────┘
```

## Модули — подробное описание

### intent/ — Intent Layer

| Модуль | Ответственность |
|--------|-----------------|
| `types.py` | `Intent`, `IntentResult`, `IntentType`, `IntentStatus` — строгие dataclasses |
| `bridge.py` | `IntentBridge` — мост UI→governance. Единственная точка входа для внешних вызовов |
| `router.py` | `IntentRouter` — оркестратор governance pipeline. Access→Policy→Execute→Audit |

**Инвариант**: Все точки входа (CLI, GUI, DBus, Hotkey) ОБЯЗАНЫ использовать IntentBridge.
Прямые вызовы системных команд из UI запрещены.

### governance/ — Governance Layer

| Модуль | Ответственность |
|--------|-----------------|
| `policy_engine.py` | TOML-based политики. check() → ALLOW/DENY/CONFIRM. Rate limiting |
| `audit_logger.py` | JSONL аудит. No PII. Lock protection (Phase 5). Rotation |
| `confirmation.py` | Эскалации. CLI/GUI/DBus modes. Timeout management |
| `action_registry.py` | Whitelist-only реестр действий. Sandbox. Injection protection |
| `dbus_service.py` | IPC через DBus. Validation (Phase 5). API versioning |
| `escalation.py` | Менеджер эскалаций. Создание/разрешение |
| `state_machine.py` | Runtime state machine |

**Инвариант**: Governance НИКОГДА не обходится. Audit НЕЛЬЗЯ отключить после `lock_enabled()`.

### access/ — Access Control

| Модуль | Ответственность |
|--------|-----------------|
| `resolver.py` | 3-уровневая модель: USER/POWER/ADMIN. Source trust scoring. Rate limits |

**Инвариант**: Admin-действия блокируются от low-trust источников (dbus, hotkey).

### security/ — Security Layer (Phase 5)

| Модуль | Ответственность |
|--------|-----------------|
| `input_validator.py` | Zero-trust валидация: size, null, obfuscation, allowlists |

**Инвариант**: Каждый вход проходит через InputValidator ДО governance pipeline.

### safety/ — Safety Validator

| Модуль | Ответственность |
|--------|-----------------|
| `models.py` | 60+ security patterns, `RiskLevel`, `ThreatType`, `SafetyVerdict` |
| `validator.py` | 3-tier check: whitelist → pattern → LLM. Combines risk scores |

### core/ — Core Pipeline & UX

| Модуль | Ответственность |
|--------|-----------------|
| `main_pipeline.py` | 14-шаговый конвейер обработки |
| `repl.py` | Production REPL сессия (с governance routing) |
| `cli.py` | CLI entry point (с governance routing) |
| `response_ux.py` | ResponseFormatter — human-friendly output (Phase 4) |
| `post_processor.py` | Фильтрация debug markers, system prompt leaks |
| `degradation.py` | DegradationStrategy — failure tracking, safe mode |
| `integrity_checker.py` | Execution path integrity, plan hash verification |
| `production_guard.py` | Response safety (20+ forbidden patterns) |
| `budget_governor.py` | Token budget management |
| `model_router.py` | LLM model selection |
| `prompts.py` | Шаблоны промптов для LLM |
| `i18n.py` | Интернационализация (ru/en) |
| `metrics.py` | Сбор метрик и статистики |

### shell/ — Shell Commander

| Модуль | Ответственность |
|--------|-----------------|
| `commander.py` | Командный процессор. `!` commands governed (Phase 5) |

**Инвариант**: `_handle_system_command_governed()` маршрутизирует через IntentBridge.
Legacy `_handle_system_command()` помечен DEPRECATED — будет удалён в v1.0.

### rag/ — Retrieval-Augmented Generation

| Модуль | Ответственность |
|--------|-----------------|
| `indexer_v2.py` | Индексация документов (TF-IDF + BM25) |
| `retriever.py` | Гибридный поиск по базе знаний |
| `auto_learner.py` | Автоматическое обучение на новых данных |

### diagnostics/ — Diagnostic Engine

| Модуль | Ответственность |
|--------|-----------------|
| `engine.py` | Движок диагностических деревьев |
| `integration.py` | API: `diagnose()` |
| `trees/` | 21 JSON-деревьев решений |

### gui/ — GUI (PyQt6)

| Модуль | Ответственность |
|--------|-----------------|
| `chat.py` | Окно чата (governance-wired, Phase 3+4) |
| `tray.py` | System tray (governance-wired, Phase 3) |
| `theme.py` | Catppuccin темы (dark/light) |
| `settings.py` | Настройки |

### voice/ — Voice I/O

| Модуль | Ответственность |
|--------|-----------------|
| `stt.py` | Speech-to-Text (Whisper/Vosk) |
| `tts.py` | Text-to-Speech (Piper/eSpeak/Edge) |
| `pipeline.py` | Голосовой пайплайн |

### Вспомогательные модули

| Модуль | Ответственность |
|--------|-----------------|
| `agent/` | Evaluator, executor, planner, memory |
| `learning/` | Обучение на взаимодействиях пользователя |
| `cv/` | Computer vision (OCR, GUI detection) |
| `metrics/` | Сбор системных метрик |
| `installer/` | Packaging, first-run wizard, updater |
| `system/` | OS integration (systemd, pacman, network, hardware) |

## Enforcement Points

| Точка | Что проверяется | Фаза |
|-------|-----------------|-------|
| IntentBridge.from_text() | InputValidator: size, null, source, obfuscation | Phase 5 |
| IntentBridge.from_action() | InputValidator: action ID, domain, params | Phase 5 |
| Intent.__post_init__() | Truncation, confidence clamping, params limits | Phase 5 |
| IntentRouter.process() | Access→Policy→Audit chain | Phase 1-2 |
| AccessResolver.check() | Access levels, rate limits, source trust | Phase 1 |
| PolicyEngine.check() | Domain rules, risk, content safety | Phase 1 |
| ActionRegistry.execute() | Whitelist, injection-check, sandbox, timeout | Phase 1 |
| AuditLogger.log() | Every decision recorded (lockable) | Phase 2+5 |
| ResponseFormatter | Traceback stripping, domain advice | Phase 4 |
| DBus service | IPC validation before pipeline | Phase 5 |
| Commander | `!` commands governed, not direct | Phase 5 |

## Принципы дизайна

1. **Zero-trust** — все входные данные считаются враждебными
2. **Defense-in-depth** — несколько слоёв защиты (validation → access → policy → safety)
3. **No bypass** — нет пути обхода governance
4. **Audit everything** — каждое решение логируется (без PII)
5. **Governance = truth** — governance принимает решения, UI и UX — только отображение
6. **Offline-first** — никаких облачных сервисов, всё локально
7. **Graceful degradation** — при отказе компонента система сообщает и продолжает
8. **Расширяемость** — новый домен/действие добавляется через governance, не в обход
