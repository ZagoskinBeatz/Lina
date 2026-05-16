"""
Lina — Prompt Engineering (X1).

Промпт-шаблоны для LLM:
  - Системный промпт для Linux-эксперта
  - RAG-промпт template
  - Диагностический промпт
  - Инструментарий для генерации промптов
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("lina.core.prompts")


# ─── Системный промпт ────────────────────────────────────────────────────────

SYSTEM_PROMPT_RU = """\
Ты — Lina, локальный ИИ-помощник для Linux.
Ты работаешь полностью оффлайн, прямо на компьютере пользователя.

Правила:
1. Отвечай на русском языке, если пользователь пишет на русском.
2. Отвечай на английском, если пользователь пишет на английском.
3. Давай конкретные команды — не абстрактные советы.
4. Формат: сначала краткий ответ (1-2 предложения), потом объяснение.
5. Если нужна команда — оформи в блоке кода: ```bash ... ```
6. Если действие опасно — предупреди пользователя.

Честность:
- Если ты НЕ ЗНАЕШЬ ответ — скажи об этом прямо: «Я не знаю» или «У меня нет информации».
- НИКОГДА не выдумывай названия пакетов, программ или команд, которых не существует.
- Если результат поиска пакетов не совпадает с запросом пользователя — скажи, что пакет не найден.
- Если пакет в репозитории не найден — предложи поискать в интернете (инструмент web_search).
- НЕ утверждай, что случайный пакет из репозитория — это то, что искал пользователь.
- НИКОГДА не выдумывай технические характеристики (железо, видеокарты, процессоры, память, числа, размеры, частоты, ядра, кэш и т.д.).
- НИКОГДА не выдумывай цены, даты выхода, версии ПО, результаты бенчмарков.
- Если вопрос касается конкретных характеристик, цифр, спецификаций — ВСЕГДА используй web_search для проверки.
- Лучше честно сказать «Я поищу» и дать точные данные из интернета, чем ответить неверно из памяти.

Инструменты:
- install_app: поиск в системных репозиториях (pacman/apt/flatpak/snap/AUR).
- web_search: поиск информации в интернете — используй для неизвестных программ.
- Если install_app не нашёл нужное приложение → используй web_search.
- Если пользователь просит «найди в интернете» → всегда используй web_search.
- Если вопрос про характеристики оборудования, цены, версии → ОБЯЗАТЕЛЬНО используй web_search.
- Если пользователь спрашивает про конкретную модель (GPU, CPU, телефон, ноутбук и т.д.) → web_search.

Безопасность:
- Никогда не предлагай `rm -rf /` или другие деструктивные команды без объяснения.
- Команды с `sudo` — только с обоснованием, зачем нужны права root.
- Не удаляй системные файлы без подтверждения.
- При работе с пакетным менеджером — сначала покажи что будет сделано.

Контекст системы:
- Дистрибутив: {distro}
- Пакетный менеджер: {package_manager}
- Desktop Environment: {desktop}
- Ядро: {kernel}
"""

SYSTEM_PROMPT_EN = """\
You are Lina, a local AI assistant for Linux.
You run completely offline, directly on the user's computer.

Rules:
1. Respond in Russian if the user writes in Russian.
2. Respond in English if the user writes in English.
3. Provide specific commands — not abstract advice.
4. Format: brief answer first (1-2 sentences), then explanation.
5. If a command is needed — format in a code block: ```bash ... ```
6. If an action is dangerous — warn the user.

Honesty:
- If you DON'T KNOW the answer — say so directly: "I don't know" or "I don't have that information".
- NEVER make up package names, programs, or commands that don't exist.
- If a package search result doesn't match the user's query — say it's not found.
- If a package isn't in the repos — suggest searching the web (web_search tool).
- Do NOT claim a random repo package is what the user asked for.
- NEVER fabricate technical specifications (hardware, GPUs, CPUs, memory, clock speeds, cores, cache, etc.).
- NEVER fabricate prices, release dates, software versions, or benchmark results.
- If a question is about specific specs, numbers, or specifications — ALWAYS use web_search to verify.
- It's better to say "Let me search" and give accurate data than to answer incorrectly from memory.

Tools:
- install_app: search system repos (pacman/apt/flatpak/snap/AUR).
- web_search: search the internet — use for unknown programs.
- If install_app didn't find it → use web_search.
- If user asks to "search the internet" → always use web_search.
- If asking about hardware specs, prices, versions → MUST use web_search.
- If asking about a specific model (GPU, CPU, phone, laptop, etc.) → web_search.

Safety:
- Never suggest `rm -rf /` or other destructive commands without explanation.
- `sudo` commands — only with justification for why root is needed.
- Do not delete system files without confirmation.
- With package managers — first show what will be done.

System context:
- Distro: {distro}
- Package manager: {package_manager}
- Desktop Environment: {desktop}
- Kernel: {kernel}
"""


# ─── RAG промпт ──────────────────────────────────────────────────────────────

RAG_PROMPT_TEMPLATE = """\
На основании следующей информации из базы знаний:

{context}

Ответь на вопрос пользователя: {query}

Если информации недостаточно — скажи об этом честно и предложи альтернативу.
Не придумывай несуществующие команды или опции.
"""

RAG_PROMPT_TEMPLATE_EN = """\
Based on the following information from the knowledge base:

{context}

Answer the user's question: {query}

If the information is insufficient — say so honestly and suggest an alternative.
Do not invent non-existent commands or options.
"""


# ─── Диагностический промпт ──────────────────────────────────────────────────

DIAGNOSTIC_PROMPT_TEMPLATE = """\
Ты — системный инженер Linux. Проанализируй следующие логи и данные:

{logs}

Системная информация:
{system_info}

Задача: Найди проблему и предложи решение.
Формат ответа:
1. Проблема: (кратко, 1-2 предложения)
2. Причина: (что вызвало проблему)
3. Решение: (конкретные команды)
4. Профилактика: (как избежать в будущем)
"""

COMMAND_GENERATION_PROMPT = """\
Сгенерируй Linux-команду для следующей задачи:
{task}

Дистрибутив: {distro}
Пакетный менеджер: {package_manager}

Верни только команду или набор команд в формате bash.
Если нужен sudo — добавь.
Если опасно — добавь комментарий.
"""

EXPLANATION_PROMPT = """\
Объясни следующую Linux-команду простым языком:

```bash
{command}
```

Формат:
1. Что делает: (одно предложение)
2. Параметры: (разбор каждого флага)
3. Безопасность: (насколько безопасна, побочные эффекты)
"""


# ─── Конфиг промптов ─────────────────────────────────────────────────────────

@dataclass
class PromptConfig:
    """Конфигурация промптов."""
    language: str = "ru"          # ru / en
    max_context_tokens: int = 2048  # Макс. токенов для контекста RAG
    max_system_tokens: int = 500   # Макс. токенов системного промпта
    include_system_info: bool = True  # Включать инфо о системе
    safety_level: str = "strict"   # strict / moderate / permissive

    def to_dict(self) -> Dict:
        return {
            "language": self.language,
            "max_context_tokens": self.max_context_tokens,
            "max_system_tokens": self.max_system_tokens,
            "include_system_info": self.include_system_info,
            "safety_level": self.safety_level,
        }


# ─── System Info ──────────────────────────────────────────────────────────────

@dataclass
class SystemContext:
    """Контекст системы для подстановки в промпты."""
    distro: str = "Linux"
    package_manager: str = "apt"
    desktop: str = ""
    kernel: str = ""

    def to_dict(self) -> Dict:
        return {
            "distro": self.distro,
            "package_manager": self.package_manager,
            "desktop": self.desktop,
            "kernel": self.kernel,
        }


# ─── Prompt Builder ──────────────────────────────────────────────────────────

class PromptBuilder:
    """Генератор промптов для Lina.

    Собирает промпты из шаблонов с подстановкой контекста.
    """

    def __init__(self, config: Optional[PromptConfig] = None,
                 system_ctx: Optional[SystemContext] = None):
        self.config = config or PromptConfig()
        self.system_ctx = system_ctx or SystemContext()
        self._custom_templates: Dict[str, str] = {}
        logger.info("PromptBuilder создан")

    # ── Системный промпт ──

    def get_system_prompt(self) -> str:
        """Возвращает системный промпт с подстановкой контекста."""
        tpl = SYSTEM_PROMPT_RU if self.config.language == "ru" else SYSTEM_PROMPT_EN
        return tpl.format(**self.system_ctx.to_dict())

    # ── RAG промпт ──

    def build_rag_prompt(self, query: str, context: str) -> str:
        """Строит RAG-промпт с контекстом из базы знаний."""
        tpl = (RAG_PROMPT_TEMPLATE if self.config.language == "ru"
               else RAG_PROMPT_TEMPLATE_EN)
        # Обрезаем контекст если слишком длинный
        max_chars = self.config.max_context_tokens * 4  # ~4 символа на токен
        if len(context) > max_chars:
            context = context[:max_chars] + "\n... (обрезано)"
        return tpl.format(context=context, query=query)

    # ── Диагностический промпт ──

    def build_diagnostic_prompt(self, logs: str,
                                 system_info: str = "") -> str:
        """Строит промпт для диагностики."""
        return DIAGNOSTIC_PROMPT_TEMPLATE.format(
            logs=logs,
            system_info=system_info or "Не указана",
        )

    # ── Генерация команд ──

    def build_command_prompt(self, task: str) -> str:
        """Строит промпт для генерации команды."""
        return COMMAND_GENERATION_PROMPT.format(
            task=task,
            distro=self.system_ctx.distro,
            package_manager=self.system_ctx.package_manager,
        )

    # ── Объяснение команд ──

    def build_explanation_prompt(self, command: str) -> str:
        """Строит промпт для объяснения команды."""
        return EXPLANATION_PROMPT.format(command=command)

    # ── Пользовательские шаблоны ──

    def add_template(self, name: str, template: str) -> None:
        """Добавляет пользовательский шаблон."""
        self._custom_templates[name] = template

    def build_custom(self, template_name: str, **kwargs) -> Optional[str]:
        """Строит промпт из пользовательского шаблона."""
        tpl = self._custom_templates.get(template_name)
        if not tpl:
            return None
        try:
            return tpl.format(**kwargs)
        except KeyError as e:
            logger.error(f"Ошибка шаблона '{template_name}': missing key {e}")
            return None

    def list_templates(self) -> List[str]:
        """Список пользовательских шаблонов."""
        return list(self._custom_templates.keys())

    # ── Безопасность ──

    def check_safety(self, text: str) -> Dict[str, bool]:
        """Проверяет текст на опасные паттерны."""
        import re as _re_safety
        dangerous_patterns = [
            _re_safety.compile(r'rm\s+-rf\s+/(?:\s|$|\*)'),
            _re_safety.compile(r'dd\s+if=/dev/zero\s+of=/dev/(?:sd[a-z]|nvme\w+|mmcblk\w+)'),
            _re_safety.compile(r'mkfs\.'),
            _re_safety.compile(r':\(\)\{\s*:\|\s*:&\s*\}\s*;'),
            _re_safety.compile(r'chmod\s+-R\s+777\s+/'),
            _re_safety.compile(r'chown\s+-R\b'),
            _re_safety.compile(r'>\s*/dev/sd[a-z]'),
            _re_safety.compile(r'mv\s+/\s+/dev/null'),
        ]

        warnings = []
        for pattern in dangerous_patterns:
            if pattern.search(text):
                warnings.append(pattern.pattern)

        sudo_count = text.count("sudo ")
        result = {
            "is_safe": len(warnings) == 0,
            "warnings": warnings,
            "sudo_commands": sudo_count,
            "needs_review": sudo_count > 0 or len(warnings) > 0,
        }

        if self.config.safety_level == "strict" and not result["is_safe"]:
            logger.warning("Опасный контент: %s", warnings)

        return result

    # ── Утилиты ──

    @staticmethod
    def _chars_per_token(text: str) -> float:
        """Weighted chars-per-token ratio for mixed Latin/Cyrillic text."""
        cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
        latin = len(text) - cyrillic
        total = cyrillic + latin
        if total == 0:
            return 3.0
        # Cyrillic ~2 chars/token, Latin ~4 chars/token
        return (cyrillic * 2 + latin * 4) / total

    def estimate_tokens(self, text: str) -> int:
        """Примерная оценка количества токенов."""
        if not text:
            return 0
        cpt = self._chars_per_token(text)
        if cpt == 0:
            return 0
        return max(1, int(len(text) / cpt))

    def truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Обрезает текст до примерного кол-ва токенов."""
        cpt = self._chars_per_token(text)
        max_chars = int(max_tokens * cpt)
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "..."

    def to_dict(self) -> Dict:
        return {
            "config": self.config.to_dict(),
            "system_context": self.system_ctx.to_dict(),
            "custom_templates": self.list_templates(),
        }

    def get_info(self) -> str:
        return (f"PromptBuilder: lang={self.config.language}, "
                f"safety={self.config.safety_level}, "
                f"templates={len(self._custom_templates)}")


# TYPICAL_LINUX_QUESTIONS — moved to tests (was dead code in production)
