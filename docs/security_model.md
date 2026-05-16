# Модель безопасности Lina

## Обзор

Lina — governance-based ИИ-помощник, построенный по принципу **zero-trust**.
Архитектура предполагает, что ЛЮБОЙ входной сигнал потенциально враждебный:
пользовательские тексты, IPC-сообщения, параметры действий.

Документ описывает:
- Модель угроз
- Поверхность атаки
- Механизмы защиты
- Известные ограничения

---

## Модель угроз

### Актёры

| Актёр | Контекст | Trust Level |
|-------|----------|-------------|
| Локальный пользователь | CLI / GUI | 3 (trusted) |
| DBus-клиент | Сторонний процесс через IPC | 1 (untrusted) |
| Hotkey handler | Глобальные сочетания клавиш | 1 (untrusted) |
| LLM output | Сгенерированный текст/команды | 0 (hostile) |
| Diagnostic trees | JSON-файлы с командами | 2 (semi-trusted) |

### Угрозы

| ID | Угроза | Severity | Mitigated? |
|----|--------|----------|------------|
| T1 | Command injection через пользовательский ввод | Critical | ✅ InputValidator + ActionRegistry |
| T2 | Обход governance через прямой вызов action | Critical | ✅ IntentBridge — единственная точка входа |
| T3 | Privilege escalation через DBus | High | ✅ Source trust scoring + rate limits |
| T4 | Отключение аудита злоумышленником | High | ✅ AuditLogger.lock_enabled() |
| T5 | Prompt injection через LLM | High | ⚠️ Частично (PostProcessor + ProductionGuard) |
| T6 | Shell metachar injection (`; && \|`) | Critical | ✅ ActionRegistry + InputValidator |
| T7 | Path traversal (`../../etc/passwd`) | High | ✅ InputValidator + param sanitization |
| T8 | Base64/hex obfuscation bypass | High | ✅ Obfuscation detection (8+ patterns) |
| T9 | Rate-limit exhaustion | Medium | ✅ Sliding window per-action + global |
| T10 | Denial-of-service через oversized input | Medium | ✅ Max lengths (4096 text, 8192 JSON) |
| T11 | Unicode normalization attacks | Medium | ✅ NFC normalization before processing |
| T12 | Fork bomb через action execution | Critical | ✅ Blacklist (14 patterns including fork bomb) |
| T13 | Data exfiltration через `curl \| sh` | Critical | ✅ Blacklist + internet policy default=deny |

---

## Поверхность атаки

### Точки входа (Attack Surface)

```
┌────────────────────────────────────────────────────────────┐
│                    EXTERNAL INPUTS                          │
│                                                            │
│  1. CLI text input          → IntentBridge.from_text()     │
│  2. GUI chat input          → IntentBridge.from_text()     │
│  3. GUI tray actions        → IntentBridge.from_action()   │
│  4. DBus diagnose()         → InputValidator → governance  │
│  5. DBus execute_action()   → InputValidator → governance  │
│  6. Hotkey triggers         → IntentBridge.from_action()   │
│  7. Shell ! commands        → _handle_system_command_governed() │
│  8. Voice input (STT)       → IntentBridge.from_text()     │
│                                                            │
├────────────────────────────────────────────────────────────┤
│                    DATA INPUTS                              │
│                                                            │
│  9. Diagnostic tree JSONs   → engine validates commands    │
│  10. policy.toml            → parsed by PolicyEngine       │
│  11. actions.json           → loaded by ActionRegistry     │
│  12. LLM responses          → PostProcessor filters        │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### Критические данные

| Ресурс | Защита |
|--------|--------|
| Audit log (JSONL) | lock_enabled(), rotation, no PII |
| Policy config (TOML) | File permissions, reload-only |
| Action registry | In-memory, whitelist-only |
| User input history | Не сохраняется (stateless session) |

---

## Механизмы защиты — Defense in Depth

### Слой 1: Валидация входных данных (Phase 5)

**Модуль**: `security/input_validator.py`

| Проверка | Лимит | Действие при нарушении |
|----------|-------|------------------------|
| Длина текста | 4096 chars | DENY |
| Null bytes (`\x00`) | 0 allowed | DENY |
| Control characters | stripped | Sanitize (сохраняя `\n\t`) |
| Unicode | NFC normalization | Normalize |
| Obfuscation (base64, hex, eval) | 8+ patterns | DENY |
| Source | allowlist: 8 sources | DENY |
| Domain | allowlist: 16 domains | DENY |
| Action ID | `^[a-zA-Z0-9_\-]+$` | DENY |
| Params depth | max 4 levels | DENY |
| Params keys | max 32 | DENY |
| Param value length | max 1024 chars | DENY |
| JSON payload | max 8192 bytes | DENY |

### Слой 2: Access Control

**Модуль**: `access/resolver.py`

| Механизм | Описание |
|----------|----------|
| 3 уровня доступа | USER < POWER < ADMIN |
| Source trust scoring | ui/cli=3, dbus/hotkey=1 |
| Domain→Level mapping | 28+ domains mapped |
| Action elevation | 28 actions require specific level |
| Rate limiting | admin=3/min, power=10/min, user=100/min |
| Failure escalation | 3+ failures → force confirmation |
| Session level | Default=USER, can elevate |

### Слой 3: Policy Engine

**Модуль**: `governance/policy_engine.py`

8-шаговый policy check pipeline:

1. Blocked actions → DENY
2. Blocked domains → DENY
3. Domain allowlist → DENY if unknown
4. Critical risk → DENY (по умолчанию)
5. Rate limit (sliding window) → RATE_LIMITED
6. Always-confirm list → CONFIRM
7. Risk threshold → CONFIRM if risk > configured max
8. Auto-approve → ALLOW (only low risk)

### Слой 4: Action Execution Safety

**Модуль**: `governance/action_registry.py`

| Защита | Детали |
|--------|--------|
| Whitelist-only | Незарегистрированное действие = BLOCKED |
| Blacklist | 14 regex: `rm -rf /`, `dd if=/dev/zero`, `:(){:\|:&};:`, `curl\|sh`, `shutdown`, `reboot`, `mkfs`, `chmod 777 /`, etc. |
| Injection detection | Shell metacharacters: `;&\|$()` |
| Path traversal | `../` detection |
| Param sanitization | Only `[a-zA-Z0-9_\-./@\s]` |
| Param allowlists | Per-action whitelisted values |
| Timeout | Per-action (default 30s) |
| Dry-run mode | Available for every action |
| Verification | Post-execution verify commands |

### Слой 5: Content Safety (Safety Validator)

**Модуль**: `safety/validator.py` + `safety/models.py`

| Компонент | Описание |
|-----------|----------|
| 60+ security patterns | Regex-based command analysis |
| 14 blacklist patterns | Unconditionally blocked |
| 3-tier check | whitelist → pattern → LLM fallback |
| Risk scoring | NONE < LOW < MEDIUM < HIGH < CRITICAL |
| Threat classification | 7 threat types |

### Слой 6: Output Safety

**Модули**: `core/post_processor.py`, `core/production_guard.py`

| Защита | Описание |
|--------|----------|
| System prompt leak detection | 20+ forbidden patterns |
| Debug marker stripping | Remove internal markers before response |
| Traceback filtering | Strip Python tracebacks from user output |
| PII filtering | No passwords/keys in responses |

### Слой 7: Аудит

**Модуль**: `governance/audit_logger.py`

| Свойство | Значение |
|----------|----------|
| Формат | JSONL (один JSON-объект на строку) |
| PII | Нет (user_text не логируется; хешируется) |
| Locking | `lock_enabled()` → нельзя отключить |
| Rotation | Автоматическая при 10MB |
| Записываемые события | allow, deny, confirm, escalate, execute, rate_limit, error, security_violation |

---

## Trust Boundary Diagram

```
                    ┌──────────────────┐
                    │  UNTRUSTED       │
                    │  User Input      │
                    │  DBus IPC        │
                    │  LLM Output      │
                    └────────┬─────────┘
                             │
                    ═════════╪═════════  Validation Gate
                             │
                    ┌────────▼─────────┐
                    │  InputValidator   │
                    │  • size check     │
                    │  • null bytes     │
                    │  • obfuscation    │
                    │  • allowlists     │
                    └────────┬─────────┘
                             │
                    ═════════╪═════════  Governance Gate
                             │
                    ┌────────▼─────────┐
                    │  IntentRouter     │
                    │  ├ AccessResolver │
                    │  ├ PolicyEngine   │
                    │  ├ Confirmation   │
                    │  └ AuditLogger    │
                    └────────┬─────────┘
                             │
                    ═════════╪═════════  Execution Gate
                             │
                    ┌────────▼─────────┐
                    │  ActionRegistry   │
                    │  • whitelist      │
                    │  • blacklist      │
                    │  • injection chk  │
                    │  • sandbox        │
                    │  • timeout        │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │  CONTROLLED       │
                    │  Subprocess exec  │
                    └──────────────────┘
```

---

## Enforcement Points — полная карта

| # | Точка | Модуль | Fase | Что проверяется |
|---|-------|--------|------|-----------------|
| 1 | `IntentBridge.from_text()` | `intent/bridge.py` | 5 | InputValidator: size, null, source, obfuscation |
| 2 | `IntentBridge.from_action()` | `intent/bridge.py` | 5 | InputValidator: action ID, domain, params |
| 3 | `IntentBridge.from_diagnose()` | `intent/bridge.py` | 5 | InputValidator: domain, user_text |
| 4 | `Intent.__post_init__()` | `intent/types.py` | 5 | Truncation, clamping, params limits |
| 5 | `AccessResolver.check()` | `access/resolver.py` | 1 | Level, trust, rate, session, failures |
| 6 | `PolicyEngine.check()` | `governance/policy_engine.py` | 1 | Domain, risk, rate, block, confirm |
| 7 | `ActionRegistry.execute()` | `governance/action_registry.py` | 1 | Whitelist, blacklist, injection, params |
| 8 | `AuditLogger.log()` | `governance/audit_logger.py` | 2+5 | Every decision recorded, locked |
| 9 | `DBus service validate` | `governance/dbus_service.py` | 5 | IPC input validation before pipeline |
| 10 | `Commander governed` | `shell/commander.py` | 5 | ! commands via IntentBridge |
| 11 | `ResponseFormatter` | `core/response_ux.py` | 4 | Traceback stripping, domain advice |

---

## Известные ограничения

### Частичные митигации (⚠️)

| Область | Описание | Риск | Статус |
|---------|----------|------|--------|
| Prompt injection | LLM может сгенерировать опасную команду. PostProcessor + ProductionGuard фильтруют, но 100% гарантий нет | Medium | Частично. Зависит от модели |
| Semantic bypass | Пользователь может перефразировать команду чтобы обойти keyword classifier | Low | Частично. Governance ловит на уровне ActionRegistry |
| Timing attacks | Нет защиты от timing-based side channels | Low | Не в scope |
| Physical access | Нет защиты от физического доступа к машине | Out of scope | — |

### Архитектурные ограничения

1. **Singleton pattern** — модули используют module-level singletons. Race conditions возможны при многопоточном доступе (не является production concern, т.к. Lina — single-user)
2. **TOML config trust** — `policy.toml` доверяется как файл пользователя. Если атакующий может писать в `~/.config/lina/`, он может ослабить политики
3. **No SELinux/AppArmor** — sandbox работает на уровне приложения, не ядра
4. **No cryptographic signing** — действия и деревья не подписаны. Целостность обеспечивается file permissions
5. **Single-machine model** — нет сетевой безопасности, т.к. Lina работает offline-only

### Рекомендации по усилению

- Рассмотреть AppArmor profile для ограничения файловых операций
- Добавить cryptographic hashing для action registry (tamper detection)
- Добавить timeout escalation для LLM responses (если модель зависает)
- Рассмотреть seccomp-bpf для sandbox execution

---

## Тестирование безопасности

### Текущее покрытие

67 тестов в `test_phase5_integration.py`:
- Adversarial fuzzing (null bytes, control chars, oversized input)
- Obfuscation detection (base64, hex, eval, backtick)
- Shell injection (`;`, `&&`, `|`, `$()`)
- Path traversal (`../../etc/shadow`)
- Governance bypass attempts (direct action, ! commands)
- Rate limiting verification
- Audit lock verification
- DBus validation

### Как запустить red-team тесты

```bash
cd lina
python -m unittest tests/test_phase5_integration.py -v
python -m unittest tests/security_redteam/ -v
python -m unittest tests/security_v3/ -v
```

### Чеклист безопасности для новых PR

- [ ] Все входные данные проходят через `InputValidator`
- [ ] Все действия маршрутизируются через governance
- [ ] Нет прямых вызовов `subprocess.run()` из UI
- [ ] Нет hardcoded credentials
- [ ] Тесты включают adversarial cases
- [ ] AuditLogger записывает все решения
- [ ] source корректно передаётся (ui/cli/dbus/hotkey)
