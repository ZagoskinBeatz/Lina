"""
Lina — Single Heavy Model LLM Engine (Phase 21).

zbeatz15:46
Привет! Подскажи, как установить telegram?

LINA15:48
Привет! Для CachyOS с пакетным менеджером pacman лучше всего использовать официальный пакет из репозиториев.

bash
sudo pacman -S telegram-desktop


bash
pacman -Qs telegram-desktop | command -v telegram-desktop


bash
telegram-desktop --version

❌ Ошибка (код 1): sudo pacman -S telegram-desktop failed retrieving file 'telegram-desktop-6.7.6-1.1-x86_64_v3.pkg.tar.zst' from mirror.nju.edu.cn : User was rejected by the SOCKS5 server (1 1). error: failed retrieving file 'telegram-desktop-6.7.6-1.1-x86_64_v3.pkg.tar.zst' from mirror.zyner.org : User was rejected by the SOCKS5 server (1 1). error: failed retrieving file 'telegram-desktop-6.7.6-1.1-x86_64_v3.pkg.tar.zst' from mirrors.ustc.edu.cn : User was rejected by the SOCKS5 server (1 1). warning: failed to retrieve some files error: failed to commit transaction (failed to retrieve some files) Errors occurred, no packages were upgraded.
Одна модель (full, 7-13B+) с ленивой загрузкой через llama-cpp-python.

Поток:
  1. Проверяем ресурсы (RAM, CPU) перед загрузкой
  2. Загружаем модель (lazy load)
  3. Генерируем ответ с контекстом RAG
  4. Опционально выгружаем для освобождения RAM

Phase 21:
  - LLM CALL MODE: raw prompt only (никогда messages/chat)
  - Hard assert перед вызовом: prompt_tokens + max_tokens <= real_n_ctx
  - Реальный n_ctx берётся из llm.n_ctx(), а не из конфига
  - LLM BUDGET REPORT перед каждой генерацией
  - --llm-debug режим: печать rendered prompt до 1000 символов
"""

import json
import os
import time
import hashlib
import gc
import logging
import threading
from pathlib import Path
from typing import Optional, Generator, Literal, Dict, Any

from lina.config import config, ModelProfile
from lina.llm.token_budget import TokenBudget, BudgetReport
from lina.core.output import get_printer
from lina.core.context_budget import ContextBudgetManager, SAFETY_MARGIN

logger = logging.getLogger("lina.llm.engine")

# Максимальное количество токенов генерации (потолок).
# 512 слишком мало для кода/диагностики; 1024 достаточно при n_ctx=4096.
MAX_GENERATION_TOKENS = 1024

# ── Предкомпилированные regex для _clean_answer ──
import re as _re

_RE_RAG_BLOCK = _re.compile(
    r"---\s*Контекст из базы знаний\s*---.*?(?:---\s*Конец контекста\s*---|$)",
    _re.DOTALL,
)
_RE_RAG_START = _re.compile(r"---\s*Контекст из базы знаний\s*---")
_RE_RAG_END = _re.compile(r"---\s*Конец контекста\s*---")
_RE_RAG_SRC = _re.compile(r"\[Источник:\s*[^\]]*\]")
_RE_SECTION_MARKERS = _re.compile(
    r"^(?:###\s*)?(SYSTEM|ASSISTANT|HISTORY|CONTEXT|USER|"
    r"Система|Lina|Контекст|Диалог|Пользователь|"
    r"РАНТАЙМ|БЕЗОПАСНОСТЬ|ВОЗМОЖНОСТИ|ФОРМАТ)\s*:?\s*$",
    _re.MULTILINE | _re.IGNORECASE,
)
_RE_PROMPT_LEAKS = [
    _re.compile(r"Ты\s*[—\-]\s*Lina.*ИИ.*$", _re.MULTILINE | _re.I),
    _re.compile(r"ЗАПРЕЩЕНО\s+без\s+подтверждения.*$", _re.MULTILINE | _re.I),
    _re.compile(r"НЕ\s+повторяй\s+контекст.*$", _re.MULTILINE | _re.I),
    _re.compile(r"НЕ\s+советуй\s+пользователю.*$", _re.MULTILINE | _re.I),
    _re.compile(r"Отвечай\s+КРАТКО.*$", _re.MULTILINE | _re.I),
    _re.compile(r"Отвечай\s+НА\s+ТЕМУ.*$", _re.MULTILINE | _re.I),
    _re.compile(r"Если\s+не\s+знаешь.*честно.*$", _re.MULTILINE | _re.I),
    _re.compile(r"Если\s+в\s+контексте\s+есть.*$", _re.MULTILINE | _re.I),
]
_RE_SNAPSHOT_LEAKS = [
    _re.compile(
        r"^\s*(Дистрибутив|Ядро|Хост|Хостнейм|CPU|RAM|GPU|DE|Shell|Uptime|Display|"
        r"Доступные\s+утилиты|Диск\s+/)\s*[:=].*$",
        _re.MULTILINE | _re.I,
    ),
    _re.compile(r"Версия\s+CachyOS.*$", _re.MULTILINE | _re.I),
    _re.compile(r"^\s*CachyOS.*$", _re.MULTILINE | _re.I),
    _re.compile(r"^\s*Отвечай\s+КРАТКО.*$", _re.MULTILINE | _re.I),
    _re.compile(r"^\s*НИКОГДА\s+не.*$", _re.MULTILINE | _re.I),
]
_RE_MULTI_NEWLINE = _re.compile(r"\n{3,}")

# ── Чистим markdown-ссылки [Текст](https://...) и голые URL ────────────────
# Модель часто копирует ссылки из веб-источников несмотря на запрет.
# Удалять полностью нельзя (могут быть полезные команды с URL внутри
# `wget`/`curl`), поэтому режем только в обычном тексте, не в код-блоках.
_RE_MD_LINK = _re.compile(r"\[([^\]]+)\]\(\s*https?://[^)\s]+\s*\)")
# Голые URL — но только http/https, и только когда они в тексте (не в `code`).
_RE_BARE_URL = _re.compile(r"(?<!\()\bhttps?://[^\s)>\]]+")

# ── Стоп-токены LLM ──────────────────────────────────────────────────────────
# Базовые маркеры конца чата + утечки разметки промпта.
_BASE_STOP_TOKENS = [
    "</s>",
    "\n### USER", "\n### SYSTEM", "\n### HISTORY", "\n### CONTEXT",
    "\nSYSTEM\n", "\nUSER\n", "\nSYSTEM:", "\nUSER:",
]
# CoT-открывалки. Только специфические многословные фразы и XML-теги, чтобы
# не ловить ложные срабатывания на обычных словах вроде "Analyze" или
# "Request" в нормальном ответе.
#
# ВАЖНО: эти строки должны почти никогда не встречаться в нормальном русском
# ответе. Один шумный стоп-токен делает модель немой — проверено на проде.
_COT_STOP_TOKENS = [
    "Thinking Process:",
    "Thinking Process\n",
    "<think>",
    "<thinking>",
]
# Полный список передаём в llama_cpp — он остановит генерацию на любом из них.
LLM_STOP_TOKENS = _BASE_STOP_TOKENS + _COT_STOP_TOKENS

# ── Chain-of-Thought leakage ──────────────────────────────────────────────────
# Qwen3 / DeepSeek-R1 / Phi-3 могут вставлять в начало ответа блок размышлений
# на английском (Thinking Process / Analyze the Request / My Role / Constraints…).
# Удаляем блок и пытаемся восстановить «чистый» ответ.
_RE_THINK_TAG = _re.compile(
    r"<think(?:ing)?\b[^>]*>.*?(?:</think(?:ing)?>|$)",
    _re.IGNORECASE | _re.DOTALL,
)
_RE_COT_OPENER = _re.compile(
    r"\b(?:Thinking\s+Process|Analyze\s+the\s+(?:Request|Provided\s+Context|"
    r"Question|Input|Context\s*\(Search\s+Results\))|My\s+Role|Constraints|"
    r"Evaluate\s+Information\s+Availability)\s*[:.]",
    _re.IGNORECASE,
)
# CoT-фразы: режем по последнему вхождению (модель обычно «думает» в начале).
_RE_COT_PHRASES = _re.compile(
    r"(?:Thinking\s+Process|Analyze\s+the\s+(?:Request|Provided\s+Context|"
    r"Question|Input|Context)|My\s+Role|Constraints|"
    r"Evaluate\s+Information\s+Availability|"
    r"^\s*\d+\.\s+(?:Analyze|Identify|Plan|Evaluate|Assess|Outline)\b"
    r")[^\n]*",
    _re.IGNORECASE | _re.MULTILINE,
)
# Явные маркеры финального ответа.
_RE_COT_END_MARKERS = [
    _re.compile(
        r"(?:^|\n)\s*(?:Final\s+(?:Answer|Response)|Ответ|Итог|Резюме)\s*[:\-—]\s*",
        _re.IGNORECASE,
    ),
    _re.compile(r"(?:^|\n)\s*---+\s*(?:\n|$)"),
]
# Долю «русскости» строки считаем простым счётчиком символов кириллицы.
_RE_CYR = _re.compile(r"[А-Яа-яЁё]")
_RE_LAT = _re.compile(r"[A-Za-z]")


def _is_russian_paragraph(text: str, *, min_chars: int = 20,
                          min_ratio: float = 0.30) -> bool:
    """True если абзац выглядит как русский ответ (а не английский CoT)."""
    text = text.strip()
    if len(text) < min_chars:
        return False
    cyr = len(_RE_CYR.findall(text))
    lat = len(_RE_LAT.findall(text))
    # Код в ```bash``` блоках содержит латиницу — это OK.
    if "```" in text and cyr >= 5:
        return True
    total = cyr + lat
    if total == 0:
        return False
    return cyr / total >= min_ratio


def _strip_urls_outside_code(text: str) -> str:
    """Strip markdown links and bare URLs outside ``` code blocks.

    Внутри код-блоков ссылки (например, `wget https://...`) сохраняем,
    они могут быть частью команды.
    """
    if "```" not in text:
        # Нет код-блоков — режем по всему тексту.
        text = _RE_MD_LINK.sub(lambda m: m.group(1), text)
        text = _RE_BARE_URL.sub("", text)
        return text

    # Разбиваем по ``` и обрабатываем только нечётные (вне) сегменты.
    parts = text.split("```")
    for i in range(0, len(parts), 2):
        seg = parts[i]
        seg = _RE_MD_LINK.sub(lambda m: m.group(1), seg)
        seg = _RE_BARE_URL.sub("", seg)
        parts[i] = seg
    return "```".join(parts)


# ── Continuation: проверка «закончен ли ответ» ────────────────────────────────
_SENTENCE_END_CHARS = set('.!?…»")»\u2014')


def _looks_complete(text: str) -> bool:
    """Heuristic: True if the answer looks finished, False if it looks
    cut off mid-sentence and might benefit from a continuation pass.

    Учитываем кавычки/скобки/код-блоки в конце и обычное окончание
    предложения. Не идеально, но достаточно чтобы отличать обрыв
    «...устано» от законченного абзаца.
    """
    if not text:
        return True
    text = text.rstrip()
    # Открытый код-блок (нечётное число ```) — точно не закончен.
    if text.count("```") % 2 == 1:
        return False
    # Закрытый код-блок в конце — закончен.
    if text.endswith("```"):
        return True
    last = text[-1]
    if last in _SENTENCE_END_CHARS:
        return True
    # Закрывающие скобки/кавычки — закончен.
    if last in (')', ']', '}'):
        return True
    # Цифра/процент/единица в конце (например «версия 0.5», «50%», «5 GB») —
    # допускаем как завершение.
    if last.isdigit() or last in '%°':
        return True
    # Эмодзи / non-BMP символы (в Unicode выше U+1F000) — обычно конец.
    if ord(last) >= 0x2600:
        return True
    return False


# ── Постобработка howto-ответов: не больше 2 bash-блоков ──────────────────────
_RE_BASH_BLOCK = _re.compile(
    r"```(?:bash|sh|shell|console|zsh|fish)?\s*\n.*?```",
    _re.DOTALL | _re.IGNORECASE,
)


def _trim_howto_blocks(text: str, max_blocks: int = 2) -> str:
    """Cap number of code blocks in a how-to answer to keep it focused.

    Пользователь ожидает «1 проверка → 1 действие». Если модель
    выдала больше блоков (3-5 «или так, или этак») — оставляем
    первые `max_blocks` и сохраняем текст между ними нетронутым.
    """
    if not text:
        return text
    blocks = list(_RE_BASH_BLOCK.finditer(text))
    if len(blocks) <= max_blocks:
        return text
    # Конец последнего разрешённого блока
    cut_at = blocks[max_blocks - 1].end()
    head = text[:cut_at].rstrip()
    # Любой текст между last allowed block и (max_blocks+1)-м — выкидываем,
    # вместе со всем хвостом. Добавляем короткую сноску для пользователя.
    return head + "\n\n_(Дополнительные варианты обрезаны — "  \
                  "если этот не подойдёт, попроси «другой способ».)_"


def _strip_cot(text: str) -> str:
    """Remove CoT block, keep the actual answer.

    Стратегия:
      1) Если есть закрытый `<think>…</think>` — выкидываем блок.
         Если есть открытый `<think>` БЕЗ закрытия — мы НЕ режем всё:
         внутри может быть финальный ответ модели (Qwen3-style).
      2) Если есть явный маркер финала (`Final Answer:`, `Ответ:`, `---`) —
         берём после ПОСЛЕДНЕГО.
      3) Берём последний крупный русский абзац (30+ символов) — это
         типичный паттерн «думала на английском, ответила на русском».
      4) Иначе режем после последней CoT-фразы.
      5) Если ничего — возвращаем пусто (caller перегенерит).
    """
    if not text:
        return text

    # 1. Закрытые think-блоки — удаляем целиком.
    cleaned = _re.sub(
        r"<think(?:ing)?\b[^>]*>.*?</think(?:ing)?>",
        "", text, flags=_re.IGNORECASE | _re.DOTALL,
    )
    # Открывающий <think> без закрытия — НЕ удаляем, только убираем сам тег
    # как маркер. Содержимое остаётся в строке, дальше обрабатываем как
    # обычный текст с CoT-преамбулой.
    cleaned = _re.sub(r"<think(?:ing)?\b[^>]*>", "", cleaned,
                      flags=_re.IGNORECASE)
    cleaned = _re.sub(r"</think(?:ing)?>", "", cleaned, flags=_re.IGNORECASE)
    cleaned = cleaned.strip()

    head = cleaned[:600]
    if not _RE_COT_OPENER.search(head) and not _RE_COT_PHRASES.search(head):
        return cleaned

    # 2. Явные маркеры финала — берём последнее вхождение.
    for end_re in _RE_COT_END_MARKERS:
        matches = list(end_re.finditer(cleaned))
        if matches:
            tail = cleaned[matches[-1].end():].strip()
            if _is_russian_paragraph(tail):
                return tail

    # 3. Самый длинный русский абзац (модель часто думает по-английски,
    # ответ выдаёт по-русски одним большим блоком).
    paragraphs = [p.strip() for p in _re.split(r"\n\s*\n", cleaned) if p.strip()]
    russian_paragraphs = [
        p for p in paragraphs
        if _is_russian_paragraph(p, min_chars=30, min_ratio=0.40)
    ]
    if russian_paragraphs:
        # Возвращаем самый длинный — это обычно сам ответ.
        return max(russian_paragraphs, key=len)

    # 4. Резать по последней CoT-фразе.
    matches = list(_RE_COT_PHRASES.finditer(cleaned))
    if matches:
        last_match = matches[-1]
        rest = cleaned[last_match.end():]
        m = _re.search(r"(?:\n\s*\n|(?<=[.!?\n])\s+)(?=[А-ЯЁ])", rest)
        if m:
            tail = rest[m.end():].strip()
            if _is_russian_paragraph(tail):
                return tail
        # Любое русское предложение от 15 символов.
        m2 = _re.search(r"[А-ЯЁ][^.!?\n]{15,}[.!?]", cleaned)
        if m2:
            return cleaned[m2.start():].strip()

    return ""

# Phase 21: LLM debug mode — включается через --llm-debug или LLM_DEBUG=1
_LLM_DEBUG = bool(os.environ.get("LLM_DEBUG", ""))


# ─── Тип модели ────────────────────────────────────────────────────────────────

# Phase 20.1: only "full" is used now
ModelTier = str


# ─── Кэш ответов ──────────────────────────────────────────────────────────────

class ResponseCache:
    """
    Кэш ответов LLM для повторяющихся запросов.

    Сохраняет пары (запрос → ответ) в JSON-файл.
    """

    def __init__(self):
        self.cache_config = config.cache
        self.cache_file = Path(self.cache_config.cache_file)
        self._lock = threading.Lock()
        self._cache = self._load()

    def _load(self) -> dict:
        """Загружает кэш из файла."""
        if not self.cache_config.enabled:
            return {}
        try:
            if self.cache_file.exists():
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
        return {}

    def _save(self) -> None:
        """Сохраняет кэш в файл (thread-safe)."""
        if not self.cache_config.enabled:
            return
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with open(self.cache_file, "w", encoding="utf-8") as f:
                    json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def _make_key(self, query: str, context: str = "",
                  session_id: str = "", tier: str = "",
                  intent: str = "") -> str:
        """Создаёт ключ кэша из запроса, контекста, session_id, tier и intent."""
        combined = (
            f"{session_id}|{tier}|{intent}|"
            f"{query.strip().lower()}|{context.strip()[:200]}"
        )
        return hashlib.sha256(combined.encode()).hexdigest()

    def get(self, query: str, context: str = "",
            session_id: str = "", tier: str = "",
            intent: str = "") -> Optional[str]:
        """Ищет ответ в кэше."""
        if not self.cache_config.enabled:
            return None

        key = self._make_key(query, context, session_id, tier, intent=intent)
        with self._lock:
            entry = self._cache.get(key)

            if entry:
                if time.time() - entry.get("timestamp", 0) < self.cache_config.ttl_seconds:
                    return entry["response"]
                else:
                    del self._cache[key]
        self._save()
        return None

    def put(self, query: str, response: str, context: str = "",
            session_id: str = "", tier: str = "",
            intent: str = "") -> None:
        """Сохраняет ответ в кэш."""
        if not self.cache_config.enabled:
            return

        key = self._make_key(query, context, session_id, tier, intent=intent)
        with self._lock:
            self._cache[key] = {
                "query": query,
                "response": response,
                "timestamp": time.time(),
            }

            # Удаляем старые записи если превышен лимит
            if len(self._cache) > self.cache_config.max_entries:
                sorted_keys = sorted(
                    self._cache.keys(),
                    key=lambda k: self._cache[k].get("timestamp", 0)
                )
                for old_key in sorted_keys[:len(self._cache) - self.cache_config.max_entries]:
                    del self._cache[old_key]

        self._save()

    def clear(self) -> None:
        """Очищает кэш."""
        self._cache = {}
        self._save()


# ─── Классификатор запросов ────────────────────────────────────────────────────

class QueryClassifier:
    """Классифицирует запросы: mini (быстрые/простые) vs full (сложные).

    Mini-задачи (Phi-3 3B): действия, короткие вопросы, статус, управление.
    Full-задачи (7B+): анализ, диагностика, длинные объяснения, код.

    Sticky: после full-запроса следующие N секунд тоже идут через full
    (чтобы не переключать модель на каждое сообщение в диалоге).
    """
    STICKY_SECONDS = 120

    # Паттерны для FULL модели (сложные задачи)
    _FULL_PATTERNS = [
        # Мультишаг / анализ
        r"\bпочему\b.*\bне\b",          # "почему не работает..."
        r"\bобъясни\b",                  # объяснения
        r"\bрасскажи\b",                 # рассказы
        r"\bпроанализируй\b",            # анализ
        r"\bдиагност",                   # диагностика/диагностируй
        r"\bсравни\b",                   # сравнение
        r"\bнапиши\s+(скрипт|код|программу)",  # кодогенерация
        r"\bсоздай\b.*\b(скрипт|файл|конфиг)\b",
        r"\bнастро(й|ить|ивать|йка)\b",  # настрой/настроить/настройка
        r"\bотлад",                      # отладка
        r"\bdebug\b",
        r"\bтроублшут",                  # troubleshoot
        r"\bлог[иа]\b",                 # анализ логов
        r"\bjournalctl\b",
        r"\bdmesg\b",
        r"\bпошагово\b",                 # пошаговые инструкции
        r"\bинструкци",                  # инструкции
        r"\bустановк[аи]\b.*\bс нуля\b",  # установка с нуля
        r"\bкак\b.*\bработает\b",        # теоретические вопросы
        r"\bразница\b.*\bмежду\b",       # сравнение концептов
        r"\boptimiz|оптимиз",            # оптимизация
        r"\bс\s+нуля\b",                 # "с нуля" → сложная задача
        # Запросы про характеристики/спецификации/обзоры — нужна full модель
        r"\bхарактеристик",         # характеристики / тех.характеристики
        r"\bспецификац",            # спецификация
        r"\bspecs?\b",                  # specs
        r"\bspecification",              # specifications
        r"\bобзор\b",                    # обзор
        r"\breview\b",                   # review
    ]

    # Паттерны для MINI модели (быстрые задачи)
    _MINI_PATTERNS = [
        r"^(привет|здравствуй|хай|хелло|йо|прив)",  # приветствия
        r"^(спасибо|пасиб|благодар|спс|ок|окей|ладно|понял)",  # реакции
        # Громкость / звук (с числом и без)
        r"\b(громкость|звук|volume|волюм|громче|тише|погромче|потише|приглуш|прибав)\b",
        # Яркость (с числом и без)
        r"\b(яркость|brightness|брайт|ярче|темнее)\b",
        r"\b(wifi|вайфай|вай-фай)\b.*(вкл|выкл|on|off|включ|выключ|врубить|вырубить)",
        r"\b(блютуз|bluetooth|блутус)\b.*(вкл|выкл|on|off|включ|выключ)",
        r"\b(время|час|дата|число|день)\b",            # время/дата
        r"\b(открой|запусти|закрой|убей|кильни)\b.*\w+",  # open/close app
        r"\b(покажи|список)\b.*(процесс|файл|папк)",   # ls/ps
        r"\b(скриншот|скрин|screenshot)\b",
        r"\b(обнови|апдейт|update)\b",                 # обновление
        r"\b(перезагру|ребут|reboot|рестарт)\b",       # перезагрузка
        r"^(да|нет)$",                                 # короткие ответы
        r"^\d+$",                                      # просто число
        r"\b(статус|status|инфо)\b",                   # статус системы
        r"\b(сколько|какой|какая)\b.*(места|памят|ram|cpu|проц|озу|диск)",
        # Простые действия: сделай/поставь/включи/выключи + объект
        r"\b(сделай|поставь|включи|выключи|врубай?|вырубай?|убавь|накрути|подними|скрути)\b",
    ]

    def __init__(self):
        self._full_re = [_re.compile(p, _re.I) for p in self._FULL_PATTERNS]
        self._mini_re = [_re.compile(p, _re.I) for p in self._MINI_PATTERNS]
        self._last_full_time = 0.0

    def record(self, tier: str) -> None:
        """Запоминает, что использовалась full модель → sticky."""
        if tier == "full":
            self._last_full_time = time.time()

    def classify(self, query: str, context: str = "", intent: str = "") -> str:
        """Определяет tier: 'mini' или 'full'."""
        # web_search intent ALWAYS needs full model for quality summarisation
        if intent == "web_search":
            return "full"

        # Sticky: если недавно был full → оставляем full (контекст диалога)
        if time.time() - self._last_full_time < self.STICKY_SECONDS:
            return "full"

        q = query.strip()

        # Короткие follow-up запросы с анафорой → full (нужен контекст)
        # «а в чей», «а какой», «а что ещё», «а он/она»
        if len(q) < 60 and _re.search(
            r'^а\s+|\bв\s+чей\b|\bа\s+(как|что|какой|какая|какие|кто|где|когда|чей)\b',
            q, _re.I,
        ):
            return "full"

        # Очень короткие запросы → mini (greetings, "ок", etc.)
        if len(q) < 12:
            return "mini"

        # Простые knowledge-запросы → mini (быстрый ответ из памяти)
        if _re.search(r'\bкто\s+тако[йе]\b|\bчто\s+такое\b', q, _re.I):
            return "mini"

        # Проверяем full-паттерны ПЕРВЫМИ (сложные задачи = приоритет)
        for pat in self._full_re:
            if pat.search(q):
                return "full"

        # Проверяем mini-паттерны
        for pat in self._mini_re:
            if pat.search(q):
                return "mini"

        # Длинные запросы (>120 символов) — вероятно сложные → full
        if len(q) > 120:
            return "full"

        # По умолчанию — mini (быстрее, достаточно для большинства задач)
        return "mini"


# ─── Одна загруженная модель ───────────────────────────────────────────────────

class _LoadedModel:
    """Обёртка над загруженной llama-cpp моделью с метаданными."""

    def __init__(self, model, profile: ModelProfile, tier: str):
        self.model = model
        self.profile = profile
        self.tier = tier
        self.loaded_at = time.time()
        self.last_used = time.time()

    def touch(self):
        """Обновляет время последнего использования."""
        self.last_used = time.time()

    @property
    def idle_seconds(self) -> float:
        """Секунды с момента последнего использования."""
        return time.time() - self.last_used


# ─── Single Model LLM Engine ───────────────────────────────────────────────────

class LLMEngine:
    """
    Single Model LLM Engine — Phase 20.1.

    Одна модель (full) с ленивой загрузкой.

    Особенности:
      - Проверка ресурсов перед загрузкой
      - Автоматическая выгрузка по таймауту неактивности
      - Кэш ответов
    """

    def __init__(self, llm_debug: bool = False):
        self.llm_config = config.llm
        self._active: Optional[_LoadedModel] = None
        self._lock = threading.Lock()  # guards _active, load/unload
        self._cache = ResponseCache()
        self._classifier = QueryClassifier()
        self._budget = TokenBudget()
        self._context_budget: Optional[ContextBudgetManager] = None
        self._last_budget_report: Optional[BudgetReport] = None
        self._llama_available: Optional[bool] = None  # кэш проверки импорта
        self._llm_debug = llm_debug or _LLM_DEBUG  # Phase 21: диагностика
        self._real_n_ctx: Optional[int] = None  # Phase 21: реальный n_ctx от модели
        self._session_id: str = ""  # v0.8.0: fix — was referenced but never defined

    def _print(self, *args, **kwargs) -> None:
        """Безопасный вывод через SafePrinter (fish-совместимый)."""
        get_printer().print(*args, **kwargs)

    # ── Свойства ──

    @property
    def is_loaded(self) -> bool:
        """Есть ли загруженная модель в памяти."""
        with self._lock:
            return self._active is not None

    @property
    def active_tier(self) -> Optional[str]:
        """Какая модель сейчас загружена."""
        with self._lock:
            return self._active.tier if self._active else None

    @property
    def active_profile(self) -> Optional[ModelProfile]:
        """Профиль загруженной модели."""
        with self._lock:
            return self._active.profile if self._active else None

    def _get_profile(self, tier: str = "full") -> ModelProfile:
        """Возвращает профиль модели по tier (mini или full)."""
        return self.llm_config.get_profile(tier)

    # ── Загрузка / выгрузка ──

    def _check_llama_available(self) -> bool:
        """Проверяет, установлен ли llama-cpp-python."""
        if self._llama_available is None:
            try:
                import llama_cpp  # noqa: F401
                self._llama_available = True
            except ImportError:
                self._llama_available = False
        return self._llama_available

    def _detect_gpu_layers(self) -> int:
        """Auto-detect GPU offload support via llama-cpp API.

        Only uses llama-cpp's own API to check if the library was compiled
        with GPU support (CUDA, ROCm, Metal, Vulkan). Does NOT spawn
        subprocesses — avoids latency on systems without discrete GPU.

        Returns -1 (all layers to GPU) if supported, 0 (CPU only) otherwise.
        """
        try:
            import llama_cpp

            # Primary: llama-cpp built-in check (available since 0.2.x)
            supports = getattr(llama_cpp, 'llama_supports_gpu_offload', None)
            if callable(supports) and supports():
                logger.info("GPU offload supported (llama_supports_gpu_offload)")
                return -1

            # Fallback: check library name for GPU backend hints
            lib = getattr(llama_cpp, '_lib', None) or getattr(llama_cpp, 'lib', None)
            if lib is not None:
                lib_name = str(getattr(lib, '_name', ''))
                if any(s in lib_name.lower() for s in ('cuda', 'hip', 'vulkan', 'metal', 'rocm')):
                    logger.info("GPU backend in lib path: %s", lib_name)
                    return -1

        except Exception as e:
            logger.debug("GPU detection error: %s", e)

        return 0  # CPU only

    def load(self, tier: str = "full") -> bool:
        """
        Загружает модель (lazy load). Thread-safe.

        Если уже загружена — ничего не делает.

        Args:
            tier: Тип модели (всегда "full").

        Returns:
            True если модель загружена и готова.
        """
        with self._lock:
            return self._load_locked(tier)

    def _load_locked(self, tier: str) -> bool:
        """Internal load — must be called under self._lock."""
        # Уже загружена нужная модель
        if self._active and self._active.tier == tier:
            self._active.touch()
            return True

        profile = self._get_profile(tier)
        model_path = profile.model_path
        tier_label = "быстрая (mini)" if tier == "mini" else "полная (full)"

        # Проверяем файл модели
        if not Path(model_path).exists():
            self._print(
                f"⚠ Модель ({tier_label}) не найдена: {model_path}\n"
                f"  Скачайте GGUF модель и поместите в: {model_path}\n"
                f"  Запустите: python download_model.py\n"
                f"  Lina будет работать без LLM (встроенные команды + RAG)."
            )
            return False

        # Проверяем llama-cpp-python
        if not self._check_llama_available():
            self._print(
                "⚠ llama-cpp-python не установлен.\n"
                "  Установите: pip install llama-cpp-python\n"
                "  Lina будет работать без LLM (встроенные команды + RAG)."
            )
            return False

        # Проверяем ресурсы
        resources_ok = self._check_resources(profile)
        if not resources_ok:
            return False

        # Выгружаем текущую модель если есть
        if self._active:
            self._print(f"  🔄 Переключение → {tier_label}")
            self._unload_internal()

        # Загружаем новую
        try:
            from llama_cpp import Llama

            # GPU auto-detection: if n_gpu_layers=0, try to offload all layers
            gpu_layers = profile.n_gpu_layers
            if gpu_layers == 0:
                gpu_layers = self._detect_gpu_layers()

            self._print(f"⏳ Загрузка {tier_label} LLM ({Path(model_path).name})...")
            if gpu_layers > 0:
                self._print(f"  🎮 GPU offload: {gpu_layers} слоёв")
            start = time.time()

            model = Llama(
                model_path=model_path,
                n_ctx=profile.n_ctx,
                n_threads=profile.n_threads,
                n_gpu_layers=gpu_layers,
                verbose=config.verbose,
            )

            elapsed = time.time() - start
            self._active = _LoadedModel(model, profile, tier)

            # Phase 21: определяем РЕАЛЬНЫЙ n_ctx от модели
            if hasattr(model, 'n_ctx'):
                real_n_ctx = model.n_ctx()
                logger.debug("REAL n_ctx: %d (config: %d)", real_n_ctx, profile.n_ctx)
                if real_n_ctx != profile.n_ctx:
                    logger.warning(
                        "n_ctx MISMATCH: model=%d, config=%d. Using model value.",
                        real_n_ctx, profile.n_ctx,
                    )
                self._real_n_ctx = real_n_ctx
            else:
                self._real_n_ctx = profile.n_ctx

            # Phase 20.3: инициализируем ContextBudgetManager с llm.tokenize()
            self._context_budget = ContextBudgetManager(
                llm=model,
                n_ctx=self._real_n_ctx,
            )
            self._print(f"✅ {tier_label.capitalize()} модель загружена за {elapsed:.1f} сек.")
            return True

        except Exception as e:
            self._print(f"❌ Ошибка загрузки {tier_label} модели: {e}")
            return False

    def unload(self) -> None:
        """Выгружает текущую модель из памяти. Thread-safe."""
        with self._lock:
            if self._active:
                tier_label = "быстрая (mini)" if self._active.tier == "mini" else "полная (full)"
                self._unload_internal()
                self._print(f"♻ {tier_label.capitalize()} модель выгружена из памяти.")

    def _unload_internal(self) -> None:
        """Внутренняя выгрузка без вывода."""
        if self._active:
            model = self._active.model
            self._active = None
            self._context_budget = None
            # llama-cpp .close() immediately frees GGML buffers
            if hasattr(model, 'close'):
                try:
                    model.close()
                except Exception:
                    pass
            del model
            gc.collect()

    def _check_resources(self, profile: ModelProfile) -> bool:
        """Проверяет, хватает ли ресурсов для загрузки модели."""
        if profile.estimated_ram_mb <= 0:
            return True

        try:
            from lina.system.monitor import SystemMonitor
            monitor = SystemMonitor()
            check = monitor.check_resources_ok(
                max_ram_mb=profile.estimated_ram_mb,
                # Skip CPU check during model load — loading itself causes
                # a brief 100% spike, which is expected and not actionable.
                max_cpu=0,
            )

            if not check["ok"]:
                for w in check["warnings"]:
                    self._print(f"  ⚠ {w}")
                self._print("  ℹ Модель не загружена: недостаточно ресурсов.")
                return False

            if check.get("warnings"):
                for w in check["warnings"]:
                    self._print(f"  ⚠ {w}")

            return True
        except Exception:
            # Если мониторинг недоступен — не загружаем (fail-closed)
            logger.warning("Resource check unavailable — refusing model load")
            return False

    def check_idle_unload(self) -> None:
        """
        Проверяет, нужно ли выгрузить модель по таймауту неактивности.
        Вызывается из главного цикла.
        """
        with self._lock:
            if not self._active:
                return
            timeout = self.llm_config.idle_unload_seconds
            if timeout > 0 and self._active.idle_seconds > timeout:
                tier_label = "полная"
                self._print(f"\n♻ Автовыгрузка {tier_label} модели (неактивна {timeout} сек)")
                self._unload_internal()

    # ── Генерация ──

    # ── Компактный системный промпт для chat/knowledge запросов ──
    _CHAT_SYSTEM_PROMPT = (
        "Ты — Lina, русскоязычный ИИ-ассистент и помощник для Linux.\n"
        "Отвечай КРАТКО (1-3 предложения), точно, на русском.\n"
        "Если не знаешь — скажи честно 'Я не знаю точно'.\n"
        "Отвечай НА ТЕМУ вопроса. Если спрашивают про авто, компанию, спорт — "
        "отвечай про это, НЕ переводи на тему Linux.\n"
        "Если в контексте есть [Результаты веб-поиска], используй ЭТИ данные для ответа.\n"
        "Если в контексте есть системная информация — используй её для ответа.\n"
        "НЕ советуй пользователю 'выполнить команду', а ВЫПОЛНИ сам — "
        "оберни команду в ```bash блок.\n"
        "НИКОГДА не выдумывай модель/название компьютера (Laptop-Z и т.п.) — "
        "если модель неизвестна, пиши 'ваш компьютер'.\n"
        "Если спрашивают 'какие X установлены'/'есть ли у меня X' — "
        "ОБЯЗАТЕЛЬНО генерируй ```bash``` команду для проверки.\n"
    )

    # ── FACT MODE: строгий промпт для web_search с верифицированными фактами ──
    _FACT_MODE_PROMPT = (
        "Ты — Lina, русскоязычный ИИ-ассистент.\n"
        "Тебе предоставлены ПРОВЕРЕННЫЕ ФАКТЫ из нескольких источников.\n\n"
        "СТРОГИЕ ПРАВИЛА:\n"
        "1. Отвечай ТОЛЬКО на основе предоставленных фактов.\n"
        "2. ЗАПРЕЩЕНО добавлять информацию, которой нет в фактах.\n"
        "3. ЗАПРЕЩЕНО делать предположения или догадки.\n"
        "4. ЗАПРЕЩЕНО использовать знания вне предоставленного контекста.\n"
        "5. ЗАПРЕЩЕНО выдумывать характеристики, числа, модели, цены, даты.\n"
        "6. Если фактов нет, недостаточно или контекст содержит "
        "'не найдены' / 'не удалось' — ответь: "
        "'К сожалению, мне не удалось найти достоверную информацию.'\n"
        "7. Перечисли факты кратко, своими словами, на русском.\n"
        "8. НЕ копируй ссылки. НЕ показывай URL.\n"
        "9. Если в контексте есть только 1-2 факта — назови их и честно "
        "скажи, что полной информации нет.\n"
    )

    # ── HOWTO MODE: гибрид для запросов «как установить / настроить» ─────────
    # Здесь модель ИМЕЕТ право использовать собственные знания о Linux
    # (пакетные менеджеры, типовые шаги), а веб-источники служат подсказкой
    # про конкретный продукт. Это покрывает кейсы вроде «установи Kiro IDE»,
    # где про сам Kiro в вебе мало, но процесс установки .tar.gz / AppImage /
    # AUR — стандартный.
    _HOWTO_MODE_PROMPT = (
        "Ты — Lina, русскоязычный ИИ-ассистент для Linux. Помогаешь "
        "ставить и настраивать ПО.\n\n"
        "АЛГОРИТМ (выполни строго в этом порядке):\n"
        "Шаг A. Проверь дистрибутив пользователя в блоке «СИСТЕМА "
        "ПОЛЬЗОВАТЕЛЯ» в начале промпта. Используй только тот "
        "пакетный менеджер, что указан там.\n"
        "Шаг B. Проверь веб-подсказки и подбери ПЕРВЫЙ подходящий "
        "вариант установки в этом порядке:\n"
        "  1) родной пакетный менеджер (pacman/apt/dnf/...) с конкретным "
        "именем пакета,\n"
        "  2) AUR (если у пользователя Arch/CachyOS) — через yay/paru,\n"
        "  3) Flatpak (`flatpak install flathub <id>`),\n"
        "  4) AppImage / .deb / .rpm — скачать руками с GitHub Releases.\n"
        "Шаг C. Сформируй ответ строго по формату:\n"
        "  • 1-2 предложения про выбранный способ.\n"
        "  • Один ```bash блок с проверкой (опционально).\n"
        "  • Один ```bash блок с командой установки.\n"
        "  • 1 предложение про результат. Стоп.\n\n"
        "БЕЗОПАСНОСТЬ — НИКОГДА:\n"
        "• `curl ... | sh`, `wget ... | bash`, `bash <(curl ...)`. Это "
        "выдача root-доступа произвольному скрипту из интернета. "
        "Вместо такого скрипта — пакетный менеджер / AUR / Flatpak / "
        "ручное скачивание AppImage и проверка перед запуском.\n"
        "• Команды установки/удаления без sudo — упадут с ошибкой.\n"
        "• inline-комментарии в ```bash блоках "
        "(`pacman -S firefox  # browser`) — ломают исполнитель. "
        "Поясняй ВНЕ блока.\n"
        "• markdown-ссылки `[текст](url)` или голые https://… в тексте. "
        "URL разрешён ТОЛЬКО внутри `wget`/`curl` команды и только "
        "если он явно есть в веб-подсказках.\n"
        "• Команды для чужих дистрибутивов. У пользователя ОДИН "
        "пакетный менеджер из блока «СИСТЕМА ПОЛЬЗОВАТЕЛЯ».\n"
        "• Развёрнутые рассуждения, нумерованные планы, "
        "английские «Thinking Process / Analyze the Request».\n\n"
        "ВЕРИФИКАЦИЯ (обязательно после каждого действия):\n"
        "После ```bash блока с установкой/настройкой — ВСЕГДА дай "
        "ОТДЕЛЬНЫЙ ```bash блок с проверкой:\n"
        "  • `pacman -Q <пакет>` или `which <бинарник>` — установлено?\n"
        "  • `<бинарник> --version` — запускается?\n"
        "Формат: действие → проверка → краткий вывод. Стоп.\n"
        "'Команда выполнилась' — НЕ финал. Финал — пользователь может "
        "ИСПОЛЬЗОВАТЬ результат.\n\n"
        "ЕСЛИ НЕ ЗНАЕШЬ:\n"
        "Если в веб-подсказках нет конкретного имени пакета или "
        "способа — честно скажи «точного имени пакета не нашлось», "
        "предложи `<пакетный-менеджер> -Ss <имя>` для самостоятельного "
        "поиска или Flatpak/AppImage. НЕ выдумывай URL и имена.\n"
    )

    @classmethod
    def _build_howto_prompt(cls) -> str:
        """Build howto-mode prompt with dynamic distro info PREPENDED.

        Дистро-блок ставим в самое начало промпта — модели лучше уважают
        первые токены системного промпта, чем хвост. Иначе на длинном
        контексте модель «забывает» про pacman и предлагает apt.
        """
        try:
            from lina.utils.distro import get_cached_distro
            distro = get_cached_distro()
        except Exception:
            distro = None

        if not distro or not getattr(distro, "is_known", False):
            header = (
                "═══ СИСТЕМА ПОЛЬЗОВАТЕЛЯ ═══\n"
                "Дистрибутив: НЕ ОПРЕДЕЛЁН.\n"
                "Спроси пользователя или предложи универсальный способ "
                "(Flatpak / AppImage / GitHub releases).\n"
                "═══════════════════════════\n\n"
            )
            return header + cls._HOWTO_MODE_PROMPT

        pkg = distro.package_manager
        pretty = getattr(distro, "pretty_name", "Linux")

        if pkg == "pacman":
            cmds = (
                "Установка: sudo pacman -S <пакет>\n"
                "AUR (если нет в основных репо): yay -S <пакет> | paru -S <пакет>\n"
                "Поиск: pacman -Ss <запрос>\n"
                "Проверка: pacman -Qs <пакет> | command -v <бинарник>\n"
                "ЗАПРЕЩЕНО ДЛЯ ЭТОЙ СИСТЕМЫ: apt, apt-get, dnf, yum, zypper — "
                "их здесь нет, команды упадут с ошибкой.\n"
            )
        elif pkg == "apt":
            cmds = (
                "Установка: sudo apt install <пакет>\n"
                "PPA: sudo add-apt-repository ppa:<owner>/<repo>\n"
                "Поиск: apt search <запрос>\n"
                "Проверка: dpkg -l | grep <пакет> | command -v <бинарник>\n"
                "ЗАПРЕЩЕНО ДЛЯ ЭТОЙ СИСТЕМЫ: pacman, yay, dnf, zypper.\n"
            )
        elif pkg == "dnf":
            cmds = (
                "Установка: sudo dnf install <пакет>\n"
                "Репо: sudo dnf copr enable <owner>/<repo>\n"
                "Поиск: dnf search <запрос>\n"
                "Проверка: rpm -q <пакет> | command -v <бинарник>\n"
                "ЗАПРЕЩЕНО ДЛЯ ЭТОЙ СИСТЕМЫ: apt, pacman, zypper.\n"
            )
        elif pkg == "zypper":
            cmds = (
                "Установка: sudo zypper install <пакет>\n"
                "Поиск: zypper search <запрос>\n"
                "Проверка: rpm -q <пакет> | command -v <бинарник>\n"
                "ЗАПРЕЩЕНО ДЛЯ ЭТОЙ СИСТЕМЫ: apt, pacman, dnf.\n"
            )
        else:
            cmds = (
                f"Пакетный менеджер: {pkg} (используй его команды).\n"
            )

        header = (
            "═══ СИСТЕМА ПОЛЬЗОВАТЕЛЯ ═══\n"
            f"Дистрибутив: {pretty}\n"
            f"Пакетный менеджер: {pkg}\n"
            f"{cmds}"
            "Универсальные альтернативы (если пакета нет в репо): "
            "Flatpak, AppImage, GitHub releases, ручная сборка из tar.gz.\n"
            "═══════════════════════════\n\n"
        )
        return header + cls._HOWTO_MODE_PROMPT

    # Интенты, для которых НЕ нужен полный системный промпт.
    # web_search is NOT here — it uses _FACT_MODE_PROMPT or _HOWTO_MODE_PROMPT.
    _CHAT_INTENTS = frozenset({"chat", "math", "rag", "weather_query", "web"})

    # Детектор how-to запросов внутри intent=web_search. Если совпало —
    # переключаемся со строгого FACT_MODE на гибридный HOWTO_MODE.
    _RE_HOWTO_QUERY = _re.compile(
        r"\b(?:как|как\s+(?:установить|поставить|настроить|собрать|"
        r"скачать|удалить|обновить|запустить|включить|выключить|"
        r"подключить|починить|исправить|сделать|использовать)|"
        r"подскажи\s+как|объясни\s+как|"
        r"how\s+to|install|setup|configure|"
        r"установка|настройка|инструкция|руководство|гайд|туториал)\b",
        _re.I,
    )

    # ── Shared prompt assembly ──

    def _prepare_prompt(
        self,
        query: str,
        context: str,
        profile: "ModelProfile",
        history: Optional[list],
        intent: str,
        real_n_ctx: int,
    ) -> tuple:
        """Build prompt and compute effective_max_tokens.

        Returns (prompt, effective_max_tokens, prompt_tokens, budget_report).
        Shared by generate() and generate_stream() to avoid duplication.
        """
        budget_report = None

        if self._context_budget is not None:
            # FACT MODE vs HOWTO MODE: web_search использует один из двух
            # строгих промптов. Для запросов «как установить / настроить»
            # включаем гибридный HOWTO MODE — там модель ИМЕЕТ право
            # использовать свои знания о Linux в дополнение к веб-фактам.
            # Для всех остальных web_search (specs, цены, факты) — строгий
            # FACT MODE без права галлюцинировать.
            if intent == "web_search":
                if self._RE_HOWTO_QUERY.search(query or ""):
                    full_system = self._build_howto_prompt()
                else:
                    full_system = self._FACT_MODE_PROMPT
            # Компактный промпт для chat/knowledge запросов
            elif intent in self._CHAT_INTENTS:
                full_system = self._CHAT_SYSTEM_PROMPT
            else:
                sys_prompt = self.llm_config.system_prompt
                runtime_section = self._build_runtime_section()
                full_system = sys_prompt
                if runtime_section:
                    full_system = sys_prompt + "\n\n" + runtime_section

            # CBM: history в формате list[str]
            # Лимиты подняты с 200/300 до 500/800 — на коротких историях
            # большие лимиты сэкономят контекст для system prompt'а через
            # CBM-trimmer. Если бюджет переполнится — он сам ужмёт.
            history_strs = []
            if history:
                for user_msg, assistant_msg in history[-3:]:
                    history_strs.append(f"Пользователь: {user_msg[:500]}")
                    if assistant_msg:
                        short = assistant_msg[:800]
                        if len(assistant_msg) > 800:
                            short += "..."
                        history_strs.append(f"Lina: {short}")

            prompt, effective_max_tokens = self._context_budget.build_prompt(
                system_prompt=full_system,
                history=history_strs,
                rag_context=context,
                user_input=query,
                max_tokens=min(profile.max_tokens, MAX_GENERATION_TOKENS),
            )
            prompt_tokens = self._context_budget.count(prompt)

            # Legacy budget report для логирования
            try:
                budget_report = self._budget.calculate(
                    model_tier="full",
                    context_window=real_n_ctx,
                    max_tokens=effective_max_tokens,
                    system_prompt=full_system,
                    query=query,
                    rag_context=context,
                    runtime_section="",
                )
            except Exception as e:
                logger.warning("Budget calculation failed: %s", e)
        else:
            # Fallback: старая сборка промпта (heuristic)
            prompt, budget_report = self._budget_prompt(
                query, context, profile, history=history
            )
            prompt_tokens = int(len(prompt) / 2.2) + 1
            available = real_n_ctx - prompt_tokens - SAFETY_MARGIN
            effective_max_tokens = min(
                profile.max_tokens, max(available, 1), MAX_GENERATION_TOKENS,
            )

            # Safe mode override
            if budget_report and "safe_mode" in budget_report.strategies_applied:
                effective_max_tokens = min(effective_max_tokens, 128)

        self._last_budget_report = budget_report
        if budget_report:
            self._log_budget(budget_report)

        # Абсолютный потолок
        effective_max_tokens = min(effective_max_tokens, MAX_GENERATION_TOKENS)

        # Safety net — финальный рубеж
        if prompt_tokens + effective_max_tokens > real_n_ctx:
            effective_max_tokens = max(real_n_ctx - prompt_tokens - SAFETY_MARGIN, 1)
            logger.warning(
                "OVERFLOW CORRECTED: prompt=%d + max=%d → %d <= n_ctx=%d",
                prompt_tokens, effective_max_tokens,
                prompt_tokens + effective_max_tokens, real_n_ctx,
            )

        return prompt, effective_max_tokens, prompt_tokens, budget_report

    def generate(
        self,
        query: str,
        context: str = "",
        use_cache: bool = True,
        tier: Optional[str] = None,
        history: Optional[list] = None,
        session_id: str = "",
        intent: str = "",
    ) -> str:
        """
        Генерирует ответ LLM.

        Args:
            query: Запрос пользователя.
            context: Контекст из RAG.
            use_cache: Использовать кэш.
            tier: Тип модели ("mini" или "full"). None → авто-классификация.
            history: История диалога [(user, assistant), ...].
            intent: Интент запроса (chat, system_command и т.д.).

        Returns:
            Текст ответа.
        """
        # Кэш
        cache_tier = tier if isinstance(tier, str) else (tier.value if tier else "")
        if use_cache:
            cached = self._cache.get(query, context, session_id=session_id, tier=cache_tier)
            if cached:
                logger.debug("Cache hit for query: %s", query[:50])
                return cached

        # Классификация
        selected_tier = tier or self._classifier.classify(query, context, intent=intent)

        # Тихий фолбэк: если файл модели отсутствует — переключаемся без предупреждения
        _profile = self._get_profile(selected_tier)
        if not Path(_profile.model_path).exists():
            fallback = "full" if selected_tier == "mini" else "mini"
            logger.info("Tier '%s' model missing, silent switch → '%s'", selected_tier, fallback)
            selected_tier = fallback

        # Пробуем загрузить выбранную модель
        if not self.load(selected_tier):
            # Если llama-cpp-python недоступен — нет смысла пробовать другую модель
            if not self._check_llama_available():
                return (
                    "⚠ LLM модель недоступна (llama-cpp-python не установлен).\n"
                    "  Установите: pip install llama-cpp-python"
                )
            # Фолбэк: если mini не загрузилась → full, и наоборот
            fallback_tier = "full" if selected_tier == "mini" else "mini"
            if not self.load(fallback_tier):
                return (
                    "⚠ LLM модель недоступна. Добавьте GGUF-файл модели.\n"
                    f"  full: {self.llm_config.full.model_path}\n"
                    f"  mini: {self.llm_config.mini.model_path}"
                )
            selected_tier = fallback_tier

        profile = self._active.profile
        self._active.touch()

        logger.debug("LLM CALL MODE: raw_prompt")
        real_n_ctx = self._real_n_ctx or profile.n_ctx

        # Единая сборка промпта
        prompt, effective_max_tokens, prompt_tokens, budget_report = (
            self._prepare_prompt(query, context, profile, history, intent, real_n_ctx)
        )

        # ── LLM BUDGET REPORT ──
        total = prompt_tokens + effective_max_tokens
        logger.debug(
            "\n===== LLM BUDGET REPORT =====\n"
            "Mode: raw_prompt\n"
            "n_ctx (real): %d\n"
            "n_ctx (config): %d\n"
            "Prompt tokens: %d\n"
            "Max tokens: %d\n"
            "Total: %d\n"
            "Safety margin: %d\n"
            "Overflow: %s\n"
            "=============================",
            real_n_ctx, profile.n_ctx,
            prompt_tokens, effective_max_tokens, total,
            SAFETY_MARGIN,
            "YES" if total > real_n_ctx else "NO",
        )

        # --llm-debug — печать для диагностики
        if self._llm_debug:
            snippet = prompt[:1000]
            if len(prompt) > 1000:
                snippet += "... [TRUNCATED]"
            print(
                f"\n[LLM-DEBUG] mode=raw_prompt "
                f"prompt_tokens={prompt_tokens} "
                f"max_tokens={effective_max_tokens} "
                f"total={total} "
                f"n_ctx={real_n_ctx}\n"
                f"[LLM-DEBUG] prompt=\n{snippet}\n"
            )

        tier_label = " full"
        if config.verbose:
            self._print(f"  [{tier_label}] Генерация ответа...")

        # ── Timeout guard ───────────────────────────────────────────
        # Wraps the blocking model() call so a hung LLM cannot freeze
        # the entire application forever.  Uses config.resources.llm_timeout
        # (default 120 s).
        llm_timeout = getattr(config.resources, "llm_timeout", 120)

        try:
            # Explicitly reset KV cache to prevent cross-query contamination.
            # Without this, the mini model can leak tokens from a previous
            # generation into the next one (e.g. "Realme 10" → "GRealme10Hub"
            # when the next query asks about GitHub).
            model = self._active.model
            if hasattr(model, 'reset'):
                model.reset()
            elif hasattr(model, '_ctx') and model._ctx is not None:
                try:
                    from llama_cpp import llama_kv_cache_clear
                    llama_kv_cache_clear(model._ctx.ctx)
                except Exception:
                    pass

            # Run model inference in a thread with hard timeout to prevent
            # infinite hangs (e.g. degenerate KV-cache loops).
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    self._active.model,
                    prompt,
                    max_tokens=effective_max_tokens,
                    temperature=profile.temperature,
                    top_p=profile.top_p,
                    repeat_penalty=profile.repeat_penalty,
                    stop=LLM_STOP_TOKENS,
                )
                try:
                    response = future.result(timeout=llm_timeout)
                except FuturesTimeout:
                    logger.error("LLM generation timed out after %ds", llm_timeout)
                    return f"⏱ Генерация ответа превысила лимит ({llm_timeout}с). Попробуйте упростить запрос."

            answer = response["choices"][0]["text"].strip()
            raw_len = len(answer)
            finish_reason = (response.get("choices") or [{}])[0].get("finish_reason", "")

            # Убираем утёкшие RAG-маркеры из ответа
            answer = self._clean_answer(answer)

            # ── Auto-recovery: пустой ответ ─────────────────────────
            # Срабатывает в двух случаях:
            #  • модель ничего не сгенерила (стоп-токен на первом куске),
            #  • модель потратила бюджет на CoT, и санайзер всё вырезал.
            # В обоих случаях прозрачно перегенерим с явным запретом
            # рассуждений, без RAG/контекста.
            if not answer.strip():
                logger.warning(
                    "Empty answer (raw=%d chars). "
                    "Regenerating with anti-reasoning prompt.", raw_len,
                )
                try:
                    answer = self._regenerate_direct(
                        query=query, profile=profile,
                        timeout=llm_timeout,
                    )
                except Exception as e:
                    logger.error("Auto-recovery failed: %s", e, exc_info=True)
                    answer = ""

            # ── Continuation: ответ обрезался по max_tokens ────────
            # Если llama-cpp сообщил finish_reason="length" и ответ
            # выглядит как незавершённое предложение — догенерируем
            # продолжение и склеиваем. Так пользователь не получит
            # обрыв на полуслове.
            if (finish_reason == "length" and answer
                    and not _looks_complete(answer)):
                try:
                    extra = self._continue_answer(
                        query=query, partial=answer,
                        profile=profile, timeout=llm_timeout,
                    )
                except Exception as e:
                    logger.error("Continuation failed: %s", e, exc_info=True)
                    extra = ""
                if extra:
                    answer = (answer + extra).strip()

            # Логируем токены ответа
            usage = response.get("usage", {})
            self._log_token_usage(usage, budget_report)

            # Запоминаем tier для sticky-логики
            self._classifier.record(selected_tier)

            # ── Trim множественных bash-блоков ──────────────────────
            # Модель часто пишет полотно с 4-5 «или так, или этак» вариантами
            # или зацикливается на одном блоке. Оставляем первые 2 (действие +
            # проверка) — пользователь выполняет один шаг, и если он не подошёл,
            # попросит другой. Применяем ВСЕГДА, не только для web_search.
            answer = _trim_howto_blocks(answer, max_blocks=2)

            # Кэшируем
            if use_cache and answer:
                self._cache.put(query, answer, context, session_id=session_id, tier=cache_tier)

            # Авто-выгрузка если включена
            if self.llm_config.auto_unload:
                self._unload_internal()

            # Добавляем метку модели в verbose-режиме
            if config.verbose:
                return f"[{tier_label}] {answer}"
            return answer

        except Exception as e:
            logger.error("Ошибка генерации: %s", e, exc_info=True)
            return "❌ Произошла внутренняя ошибка при генерации ответа"

    @staticmethod
    def _clean_answer(text: str) -> str:
        """Убирает утёкшие маркеры и мусор из ответа LLM."""
        # Сразу режем CoT-утечки (Thinking Process / <think>) если они есть
        text = _strip_cot(text)
        # Обрезаем при утечке системного промпта (голый SYSTEM без ###)
        for bare in ("\nSYSTEM\n", "\nSYSTEM:", "\nUSER\n", "\nUSER:"):
            pos = text.find(bare)
            if pos >= 0:
                text = text[:pos].strip()
        # Извлекаем текст после ### ASSISTANT если маркер присутствует
        for marker in ("### ASSISTANT", "### Lina:"):
            pos = text.rfind(marker)
            if pos >= 0:
                text = text[pos + len(marker):].strip()

        # Удаляем блоки RAG-контекста
        text = _RE_RAG_BLOCK.sub("", text)
        text = _RE_RAG_START.sub("", text)
        text = _RE_RAG_END.sub("", text)
        # Удаляем [Источник: ...] заголовки RAG
        text = _RE_RAG_SRC.sub("", text)
        # Удаляем секционные маркеры промпта
        text = _RE_SECTION_MARKERS.sub("", text)
        # Удаляем утечки системного промпта
        for pat in _RE_PROMPT_LEAKS:
            text = pat.sub("", text)
        # Удаляем утечки системного снимка (дистрибутив, ядро, CPU, RAM, GPU)
        for pat in _RE_SNAPSHOT_LEAKS:
            text = pat.sub("", text)
        # Чистим markdown-ссылки и голые URL (вне код-блоков). Веб-источники
        # часто протекают сюда даже когда мы прямо запретили их в промпте.
        text = _strip_urls_outside_code(text)
        # Убираем лишние пустые строки
        text = _RE_MULTI_NEWLINE.sub("\n\n", text)
        return text.strip()

    def _regenerate_direct(
        self,
        query: str,
        profile,
        timeout: int = 60,
    ) -> str:
        """Re-run the model with a strict anti-reasoning prompt.

        Используется как auto-recovery когда основной ответ оказался
        пустым (модель не выдала ничего из-за стоп-токена или ушла в CoT).
        Здесь:
          • короткий явный системный промпт,
          • дистро-fact-card если how-to запрос,
          • МИНИМУМ стоп-токенов,
          • без агрессивной CoT-очистки на выходе.
        """
        if not self._active or not self._active.model:
            logger.warning("Auto-recovery: no active model")
            return ""

        # Если это how-to запрос — добавим дистро-fact-card в начало
        # и попросим модель помочь с установкой/настройкой.
        is_howto = bool(self._RE_HOWTO_QUERY.search(query or ""))
        fact_card = ""
        if is_howto:
            try:
                from lina.utils.distro import get_cached_distro
                d = get_cached_distro()
                if d and getattr(d, "is_known", False):
                    pkg = d.package_manager or "?"
                    pretty = getattr(d, "pretty_name", None) or d.name or "Linux"
                    fact_card = (
                        f"СИСТЕМА ПОЛЬЗОВАТЕЛЯ: {pretty} (пакетный менеджер {pkg}).\n"
                        f"Команды только для {pkg}, чужие — НЕ ПИСАТЬ.\n"
                    )
            except Exception:
                pass

        if is_howto:
            sys_prompt = (
                "Ты — Lina, русскоязычный ассистент для Linux. "
                "Ты помогаешь устанавливать и настраивать ПО на ПК пользователя. "
                "Отвечай на русском, кратко и по делу. "
                "Команды оборачивай в ```bash блок. "
                "Без рассуждений вслух, без английских вступлений."
            )
        else:
            sys_prompt = (
                "Ты — Lina, русскоязычный ИИ-ассистент для Linux. "
                "Отвечай НАПРЯМУЮ на русском, кратко, по делу. "
                "Без английских вводных, без блоков размышлений. "
                "Если нужны команды — оборачивай их в ```bash блок."
            )

        prompt = (
            f"### SYSTEM\n{sys_prompt}\n"
            f"{fact_card}\n"
            f"### USER\n{query}\n\n"
            f"### ASSISTANT\n"
        )

        recovery_stops = ["</s>", "\n### USER", "\n### SYSTEM"]

        logger.info(
            "Auto-recovery: prompt_len=%d max_tokens=%d temp=%.2f stops=%d howto=%s",
            len(prompt),
            min(profile.max_tokens, 384),
            max(0.2, min(profile.temperature, 0.6)),
            len(recovery_stops),
            is_howto,
        )

        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                self._active.model,
                prompt,
                max_tokens=min(profile.max_tokens, 384),
                temperature=max(0.2, min(profile.temperature, 0.6)),
                top_p=profile.top_p,
                repeat_penalty=profile.repeat_penalty,
                stop=recovery_stops,
            )
            try:
                resp = future.result(timeout=timeout)
            except FuturesTimeout:
                logger.error("Auto-recovery generation timed out after %ds", timeout)
                return ""

        text = (resp.get("choices") or [{}])[0].get("text", "").strip()
        logger.info(
            "Auto-recovery raw output (%d chars): %.200r",
            len(text), text,
        )

        # Лёгкая обработка маркеров.
        text = _re.sub(r"^\s*(### )?ASSISTANT\s*:?\s*", "", text)
        text = _re.sub(r"^\s*Ответ\s*[:\-—]\s*", "", text)
        text = _strip_urls_outside_code(text)
        text = _RE_MULTI_NEWLINE.sub("\n\n", text)

        # Если recovery выдал CoT — пробуем его срезать. Если после
        # стрипа полезного текста не осталось — отдаём короткий
        # человеческий fallback вместо англоязычного «Thinking Process».
        cleaned = _strip_cot(text).strip()
        if cleaned:
            return cleaned
        if _RE_COT_OPENER.search(text[:400]):
            logger.warning(
                "Auto-recovery still leaked CoT, suppressing. Raw: %.200r", text,
            )
            return (
                "Не получилось коротко сформулировать ответ. "
                "Можешь переспросить — отвечу подробнее."
            )
        return text.strip()

    def _continue_answer(
        self,
        query: str,
        partial: str,
        profile,
        timeout: int = 60,
        max_extra_tokens: int = 384,
    ) -> str:
        """Догенерировать продолжение если ответ оборвался по max_tokens.

        Идея: даём модели её же незаконченный ответ + просьбу
        «продолжи с того же места». Модель видит свой контекст и
        дописывает естественно.

        Возвращает только дельту (новый текст), без partial.
        """
        if not self._active or not self._active.model or not partial:
            return ""

        sys_prompt = (
            "Ты — Lina, русскоязычный ассистент. Продолжи свой "
            "предыдущий ответ с того места, где он оборвался. "
            "НЕ повторяй уже сказанное. НЕ начинай заново. "
            "Просто допиши концовку — кратко и до точки. "
            "Без рассуждений и английских вводных."
        )
        prompt = (
            f"### SYSTEM\n{sys_prompt}\n\n"
            f"### USER\n{query}\n\n"
            f"### ASSISTANT\n{partial}"
        )

        recovery_stops = ["</s>", "\n### USER", "\n### SYSTEM"]
        logger.info(
            "Continuation: partial_len=%d max_extra=%d",
            len(partial), max_extra_tokens,
        )

        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                self._active.model,
                prompt,
                max_tokens=max_extra_tokens,
                temperature=max(0.2, min(profile.temperature, 0.5)),
                top_p=profile.top_p,
                repeat_penalty=profile.repeat_penalty,
                stop=recovery_stops,
            )
            try:
                resp = future.result(timeout=timeout)
            except FuturesTimeout:
                logger.error("Continuation timed out after %ds", timeout)
                return ""

        extra = (resp.get("choices") or [{}])[0].get("text", "")
        logger.info("Continuation got %d chars", len(extra))
        # На continuation не делаем агрессивный CoT-стрип: модель уже
        # пишет внутри своего предыдущего ответа, риск минимальный.
        # Только маркеры разметки + URL.
        extra = _re.sub(r"^\s*(### )?ASSISTANT\s*:?\s*", "", extra)
        extra = _strip_urls_outside_code(extra)
        return extra.rstrip()

    # ── Streaming generation ──

    def generate_stream(
        self,
        query: str,
        context: str = "",
        tier: Optional[str] = None,
        cancel_flag: Optional[list] = None,
        history: Optional[list] = None,
        intent: str = "",
    ) -> Generator[str, None, None]:
        """
        Потоковая генерация ответа (посимвольно).

        Args:
            query: Запрос пользователя.
            context: RAG-контекст.
            tier: Тип модели или None для автоклассификации.
            cancel_flag: A list [bool]. Set [True] to abort.
            history: История диалога [(user, assistant), ...].
            intent: Интент запроса (chat, system_command и т.д.).

        Yields:
            Токены ответа по мере генерации.
        """
        if cancel_flag is None:
            cancel_flag = [False]

        selected_tier = tier or self._classifier.classify(query, context, intent=intent)

        # Silent fallback if model file missing
        _profile = self._get_profile(selected_tier)
        if not Path(_profile.model_path).exists():
            fallback = "full" if selected_tier == "mini" else "mini"
            selected_tier = fallback

        if not self.load(selected_tier):
            fallback_tier = "full" if selected_tier == "mini" else "mini"
            if not self.load(fallback_tier):
                yield "⚠ LLM модель недоступна."
                return
            selected_tier = fallback_tier

        profile = self._active.profile
        self._active.touch()

        logger.debug("LLM CALL MODE: raw_prompt (stream)")
        real_n_ctx = self._real_n_ctx or profile.n_ctx

        # Единая сборка промпта
        prompt, effective_max_tokens, prompt_tokens, _budget = (
            self._prepare_prompt(query, context, profile, history, intent, real_n_ctx)
        )

        logger.debug(
            "STREAM BUDGET: prompt=%d max=%d total=%d n_ctx=%d",
            prompt_tokens, effective_max_tokens,
            prompt_tokens + effective_max_tokens, real_n_ctx,
        )

        try:
            # Explicitly reset KV cache (same as in generate())
            model = self._active.model
            if hasattr(model, 'reset'):
                model.reset()
            elif hasattr(model, '_ctx') and model._ctx is not None:
                try:
                    from llama_cpp import llama_kv_cache_clear
                    llama_kv_cache_clear(model._ctx.ctx)
                except Exception:
                    pass

            tokens_list = []
            stream_finish_reason = ""
            for chunk in self._active.model(
                prompt,
                max_tokens=effective_max_tokens,
                temperature=profile.temperature,
                top_p=profile.top_p,
                repeat_penalty=profile.repeat_penalty,
                stop=LLM_STOP_TOKENS,
                stream=True,
            ):
                if cancel_flag[0]:
                    break
                # Запоминаем последний finish_reason — пригодится для continuation.
                _fr = chunk["choices"][0].get("finish_reason")
                if _fr:
                    stream_finish_reason = _fr
                token = chunk["choices"][0]["text"]
                tokens_list.append(token)
                # Буферизуем первые 30 токенов для раннего обнаружения мусора.
                # 30 — компромисс: достаточно чтобы CoT успел показаться,
                # но мало чтобы заметно задержать первый токен пользователю.
                # Стратегия:
                #   1. Явные утечки разметки (RAG/системный снимок/промпт) → сброс.
                #   2. Явные CoT-маркеры (Thinking Process, Analyze the Request,
                #      <think>, и т.п.) → сброс, чтобы caller перегенерил.
                #   3. Просто английское вступление без CoT-маркеров — пропускаем.
                if len(tokens_list) <= 30:
                    partial = "".join(tokens_list).strip()
                    if len(tokens_list) == 30:
                        is_garbage = (
                            not partial
                            or _RE_RAG_BLOCK.search(partial) is not None
                            or _RE_RAG_SRC.search(partial) is not None
                            or _RE_SECTION_MARKERS.search(partial) is not None
                            or any(p.search(partial) for p in _RE_PROMPT_LEAKS)
                            or any(p.search(partial) for p in _RE_SNAPSHOT_LEAKS)
                        )
                        is_cot_leak = _RE_COT_OPENER.search(partial) is not None
                        if is_garbage or is_cot_leak:
                            tokens_list.clear()
                            logger.warning(
                                "Early-stream filter: %s detected, suppressing. "
                                "Partial: %.150r",
                                "CoT" if is_cot_leak else "garbage",
                                partial,
                            )
                            yield ""  # сигнал caller'у что ответ пуст
                            break
                        # Сброс буфера — отдаём накопленные токены
                        for buffered in tokens_list:
                            yield buffered
                else:
                    yield token

            # Flush buffered tokens if stream ended before 30-token threshold
            if 0 < len(tokens_list) <= 30:
                # Здесь стрим закончился раньше 30 токенов — в коротком
                # ответе CoT обычно не успевает развернуться, отдаём как есть.
                for buffered in tokens_list:
                    yield buffered

            # Post-process
            raw_text = "".join(tokens_list).strip()
            raw_len = len(raw_text)
            full_answer = self._clean_answer(raw_text)
            self._classifier.record(selected_tier)

            # Diagnostic: log what the raw stream looked like when we end up empty.
            if not full_answer:
                logger.info(
                    "Stream raw output (%d chars, %d tokens): %.300r",
                    raw_len, len(tokens_list), raw_text[:300],
                )

            # ── Auto-recovery: пустой ответ в стриме ───────────────
            # Срабатывает если модель ничего не сгенерила или санайзер
            # всё вырезал. Перегенерим без стрима, дописываем как продолжение.
            if not full_answer and not cancel_flag[0]:
                logger.warning(
                    "Stream: empty answer (raw=%d). "
                    "Regenerating with anti-reasoning prompt.", raw_len,
                )
                try:
                    recovered = self._regenerate_direct(
                        query=query, profile=profile,
                        timeout=getattr(config.resources, "llm_timeout", 60),
                    )
                except Exception as e:
                    logger.error("Stream auto-recovery failed: %s", e, exc_info=True)
                    recovered = ""
                if recovered:
                    yield recovered
                    full_answer = recovered

            # ── Continuation: ответ обрезался по max_tokens ────────
            # Если стрим закончился по причине length и текст оборван
            # на полуслове — догенерируем продолжение и стримим его дальше.
            elif (full_answer and not cancel_flag[0]
                    and stream_finish_reason == "length"
                    and not _looks_complete(full_answer)):
                logger.info(
                    "Stream: hit max_tokens with incomplete tail, continuing. "
                    "Last 80: %r", full_answer[-80:],
                )
                try:
                    extra = self._continue_answer(
                        query=query, partial=full_answer,
                        profile=profile,
                        timeout=getattr(config.resources, "llm_timeout", 60),
                    )
                except Exception as e:
                    logger.error("Stream continuation failed: %s", e, exc_info=True)
                    extra = ""
                if extra:
                    yield extra
                    full_answer = (full_answer + extra).strip()

            # Cache only valid, non-cancelled, non-garbage responses
            if full_answer and len(full_answer) > 10 and not cancel_flag[0]:
                self._cache.put(query, full_answer, context,
                                session_id=self._session_id,
                                tier=selected_tier, intent=intent)

        except Exception as e:
            logger.error("Ошибка генерации (stream): %s", e, exc_info=True)
            yield "\n❌ Произошла внутренняя ошибка при генерации ответа"

    # ── Token Budget ──

    @property
    def last_budget_report(self) -> Optional[BudgetReport]:
        """Последний отчёт о токенном бюджете."""
        return self._last_budget_report

    def _budget_prompt(
        self,
        query: str,
        context: str,
        profile: ModelProfile,
        history: Optional[list] = None,
    ) -> tuple:
        """
        Формирует промпт с учётом токенного бюджета.

        Сначала пробует полный промпт. Если не помещается — применяет
        стратегии авто-урезания (trim runtime → trim RAG → mini prompt → safe mode).

        Args:
            query: Запрос пользователя.
            context: RAG-контекст.
            profile: Профиль активной модели.

        Returns:
            Tuple[str, Optional[BudgetReport]]:
              - Промпт (возможно, урезанный)
              - Отчёт о бюджете (или None)
        """
        tier = self._active.tier if self._active else "full"

        # Системный промпт (всегда полный)
        sys_prompt = self.llm_config.system_prompt

        # Рантайм-секция
        runtime_section = self._build_runtime_section()

        # Рассчитываем бюджет
        report = self._budget.calculate(
            model_tier=tier,
            context_window=profile.n_ctx,
            max_tokens=profile.max_tokens,
            system_prompt=sys_prompt,
            query=query,
            rag_context=context,
            runtime_section=runtime_section,
        )

        # Если помещается — строим промпт как обычно
        if report.fits:
            prompt = self._assemble_prompt(
                sys_prompt, runtime_section, context, query, history=history
            )
            return prompt, report

        # Не помещается → авто-урезание
        trimmed = self._budget.auto_trim(
            model_tier=tier,
            context_window=profile.n_ctx,
            max_tokens=profile.max_tokens,
            system_prompt=sys_prompt,
            query=query,
            rag_context=context,
            runtime_section=runtime_section,
            compact_prompt="",
        )

        prompt = self._assemble_prompt(
            trimmed["system_prompt"],
            trimmed["runtime_section"],
            trimmed["rag_context"],
            query,
            history=history,
        )

        report = trimmed["report"]
        if report and report.strategies_applied:
            strategies = ", ".join(report.strategies_applied)
            logger.info("Token budget: применены стратегии [%s]", strategies)
            if config.verbose:
                self._print(f"  ⚠ Token budget: {strategies}")

        return prompt, report

    def _assemble_prompt(
        self,
        system_prompt: str,
        runtime_section: str,
        context: str,
        query: str,
        history: Optional[list] = None,
    ) -> str:
        """
        Собирает финальный промпт из компонентов.

        Использует безопасную структуру:
          ### SYSTEM → ### HISTORY → ### CONTEXT → ### USER → ### ASSISTANT

        Args:
            system_prompt: Системный промпт (полный или мини).
            runtime_section: Рантайм-блок (может быть пустым).
            context: RAG-контекст (может быть пустым).
            query: Запрос пользователя.
            history: История диалога [(user, assistant), ...].

        Returns:
            Готовый промпт для LLM.
        """
        parts = [f"### SYSTEM\n{system_prompt}"]

        if runtime_section:
            parts.append(f"\n{runtime_section}")

        # История диалога
        if history:
            dialog_parts = []
            for user_msg, assistant_msg in history:
                dialog_parts.append(f"Пользователь: {user_msg[:500]}")
                if assistant_msg:
                    short = assistant_msg[:800]
                    if len(assistant_msg) > 800:
                        short += "..."
                    dialog_parts.append(f"Lina: {short}")
            parts.append(f"\n### HISTORY\n" + "\n".join(dialog_parts))

        if context:
            parts.append(f"\n### CONTEXT\n{context}")

        parts.append(f"\n### USER\n{query}")
        parts.append("\n### ASSISTANT")

        return "\n".join(parts)

    def _log_budget(self, report: BudgetReport) -> None:
        """Логирует бюджет в structured-формате."""
        logger.debug(
            "Token budget: tier=%s input=%d/%d (%.0f%%) gen=%d avail=%d",
            report.model_tier,
            report.total_input_tokens,
            report.context_window,
            report.utilization * 100,
            report.max_tokens,
            report.available_for_generation,
        )
        for w in report.warnings:
            logger.warning("Token budget: %s", w)

    def _log_token_usage(
        self,
        usage: Dict[str, Any],
        budget_report: Optional[BudgetReport],
    ) -> None:
        """
        Логирует фактическое использование токенов после генерации.

        Args:
            usage: Данные usage из ответа llama-cpp.
            budget_report: Отчёт бюджета (для контекста).
        """
        if not usage:
            return

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total = usage.get("total_tokens", prompt_tokens + completion_tokens)

        ctx_window = budget_report.context_window if budget_report else 0
        utilization = total / ctx_window if ctx_window > 0 else 0

        logger.info(
            "Token usage: prompt=%d completion=%d total=%d ctx=%d (%.0f%%)",
            prompt_tokens, completion_tokens, total, ctx_window,
            utilization * 100,
        )

        # Warning если >90% окна
        if utilization > 0.90:
            logger.warning(
                "Высокая утилизация контекста: %d/%d (%.0f%%)",
                total, ctx_window, utilization * 100,
            )

    # ── Построение промпта (legacy, для обратной совместимости) ──

    def _build_prompt(self, query: str, context: str = "") -> str:
        """
        Формирует полный промпт для модели.

        Включает:
          - Системный промпт
          - Динамическую секцию рантайма (CPU, RAM, Swap, модель, CV)
          - Контекст из RAG (если есть)
          - Запрос пользователя
        """
        sys_prompt = self.llm_config.system_prompt

        parts = [f"### Система:\n{sys_prompt}\n"]

        # Динамическая рантайм-секция
        runtime_section = self._build_runtime_section()
        if runtime_section:
            parts.append(f"{runtime_section}\n")

        if context:
            parts.append(f"### Контекст:\n{context}\n")

        parts.append(f"### Пользователь:\n{query}\n")
        parts.append("### Lina:\n")

        return "\n".join(parts)

    def _build_runtime_section(self) -> str:
        """
        Строит динамическую секцию рантайма через utils/prompt.

        Собирает состояние: активная модель, кэш, CV-модули,
        и передаёт в build_runtime_section() для форматирования
        с актуальными CPU/RAM/Swap метриками.
        """
        try:
            from lina.utils.prompt import build_runtime_section, get_cv_module_status

            # Состояние моделей
            active_tier = self.active_tier
            mini_loaded = False
            full_loaded = (self._active is not None
                           and self._active.tier == "full")

            # Размер кэша
            cache_size = len(self._cache._cache) if hasattr(
                self._cache, '_cache') else 0

            # CV-модули
            cv_enabled = config.cv.enabled
            cv_modules = get_cv_module_status()

            return build_runtime_section(
                active_tier=active_tier,
                mini_loaded=mini_loaded,
                full_loaded=full_loaded,
                cache_size=cache_size,
                cv_enabled=cv_enabled,
                cv_modules=cv_modules,
            )
        except Exception:
            # Fallback: базовая информация без psutil/CV
            return self._get_runtime_info_fallback()

    def _get_runtime_info_fallback(self) -> str:
        """Fallback рантайм-информации без внешних зависимостей."""
        info = []
        if self._active:
            tier_label = "полная"
            info.append(f"Активная модель: {tier_label}")
        else:
            info.append("Модель: не загружена")

        cache_size = len(self._cache._cache) if hasattr(
            self._cache, '_cache') else 0
        if cache_size > 0:
            info.append(f"Кэш: {cache_size} записей")

        return "### РАНТАЙМ\n" + " | ".join(info) if info else ""

    def clear_cache(self) -> None:
        """Очищает кэш ответов."""
        self._cache.clear()
        self._print("🗑 Кэш ответов очищен.")

    # ── Информация ──

    def get_status(self) -> dict:
        """Возвращает полный статус движка."""
        full_exists = Path(self.llm_config.full.model_path).exists()

        info = {
            "active_tier": self.active_tier,
            "active_model": None,
            "full": {
                "path": self.llm_config.full.model_path,
                "exists": full_exists,
                "estimated_ram_mb": self.llm_config.full.estimated_ram_mb,
                "n_ctx": self.llm_config.full.n_ctx,
            },
            "auto_unload": self.llm_config.auto_unload,
            "idle_unload_seconds": self.llm_config.idle_unload_seconds,
            "llama_cpp_available": self._check_llama_available(),
        }

        if self._active:
            info["active_model"] = {
                "tier": self._active.tier,
                "path": self._active.profile.model_path,
                "loaded_at": time.strftime(
                    "%H:%M:%S", time.localtime(self._active.loaded_at)
                ),
                "idle_seconds": round(self._active.idle_seconds),
            }

        return info

    def format_status(self) -> str:
        """Форматирует статус в читаемую строку."""
        s = self.get_status()

        lines = ["╔══════════════════════════════════════════════╗"]
        lines.append("║          LLM Engine — Статус                ║")
        lines.append("╠══════════════════════════════════════════════╣")

        # Активная модель
        if s["active_tier"]:
            tier_label = " полная"
            idle = s["active_model"]["idle_seconds"]
            lines.append(f"║  Активная: {tier_label} (idle: {idle} сек)")
        else:
            lines.append("║  Активная: нет (спит) ⬜")

        # Полная
        full_status = "✅ есть" if s["full"]["exists"] else "❌ нет"
        lines.append(f"║  Полная: {full_status}  ~{s['full']['estimated_ram_mb']} MB RAM")

        # llama-cpp
        llama_ok = "✅" if s["llama_cpp_available"] else "❌"
        lines.append(f"║  llama-cpp-python: {llama_ok}")

        lines.append("╚══════════════════════════════════════════════╝")
        return "\n".join(lines)

    def get_token_metrics(self) -> Optional[Dict[str, Any]]:
        """
        Возвращает метрики токенов из последнего запроса.

        Для интеграции в JSON-отчёты тестов и мониторинг.

        Returns:
            Dict с метриками или None.
        """
        if not self._last_budget_report:
            return None
        return self._last_budget_report.to_dict()

