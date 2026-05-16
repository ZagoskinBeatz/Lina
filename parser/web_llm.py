# -*- coding: utf-8 -*-
"""
web_llm.py — Local mini-GGUF model for web content summarisation.

Adapted from Parcer/search_cli/llm.py for lina integration.

Uses lina's existing mini model (mini.gguf) to summarize extracted web text.
All inference is thread-safe via a global lock.
"""

import logging
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Optional, List, Dict

from lina.config import MODELS_DIR

logger = logging.getLogger("lina.parser.web_llm")

# ---------------------------------------------------------------------------
# Model configuration (uses lina's existing model paths)
# ---------------------------------------------------------------------------

LLM_MODEL_PATH_MINI = MODELS_DIR / "mini" / "mini.gguf"
LLM_MODEL_PATH_FULL = MODELS_DIR / "full" / "Qwen2.5-7B-Instruct-Q4_K_M.gguf"

# Default model for web summarisation — mini is fast and sufficient
LLM_MODEL_PATH = LLM_MODEL_PATH_MINI

LLM_N_CTX = 4096
LLM_MAX_TOKENS = 256             # ← 768→384→256: reduced for speed on CPU
_CHUNK_MAX_TOKENS = 128          # ← 200→128: tighter per-chunk
_COMBINE_MAX_TOKENS = 256        # ← 400→256: tighter combine
LLM_TEMPERATURE = 0.3
LLM_REPEAT_PENALTY = 1.15
LLM_TOP_P = 0.9
LLM_TOP_K = 40
_LLM_RETRIES = 0                 # ← 1→0: no retries (saves 30-60s)
_MAX_TEXT_CHARS_PER_CALL = 2000   # ← 5000→3000→2000: less text to LLM → faster

LLM_STOP = [
    "\n\n\n", "User question:", "Instructions:", "Web content:",
    "---\n", "---\n\n", "--- Source", "##", "\n\nNote:", "Your task:",
    "Question:", "Content:", "Вопрос:", "Текст:", "Питання:",
    "Frage:", "Pregunta:", "Texte :",
    "Cite this", "Share this", "Related articles", "Read more",
    "Advertisement", "Subscribe", "Follow us", "Click here",
    "\nBy ", "Written by", "Published on", "All rights reserved",
    "Читайте также", "Поделиться", "Подписаться", "Реклама",
]

# Thread lock — llama-cpp-python is NOT safe for concurrent calls.
_llm_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_LANG_NAMES: Dict[str, str] = {
    "en": "English", "ru": "Russian", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "pt": "Portuguese", "zh": "Chinese",
    "ja": "Japanese", "ko": "Korean", "ar": "Arabic", "nl": "Dutch",
    "pl": "Polish", "uk": "Ukrainian", "tr": "Turkish",
}

_CYRILLIC_ALIASES = {"mk", "bg", "sr"}


def detect_language(text: str) -> str:
    """Detect BCP-47 language code of *text*. Falls back to 'en'."""
    try:
        from langdetect import detect_langs, DetectorFactory
        DetectorFactory.seed = 42
        candidates = detect_langs(text)
        if candidates:
            code = str(candidates[0].lang).split("-")[0].lower()
            if code in _CYRILLIC_ALIASES:
                code = "ru"
            return code
    except Exception:
        pass
    return "en"


def _language_name(code: str) -> str:
    return _LANG_NAMES.get(code, code.upper())


# ---------------------------------------------------------------------------
# Model loader (singleton)
# ---------------------------------------------------------------------------

_loaded_model = None
_model_path_loaded: Optional[Path] = None


def load_llm(model_path: Optional[Path] = None):
    """
    Load a local GGUF model and return a llama_cpp.Llama instance.

    Returns None on any failure (caller should degrade gracefully).
    """
    global _loaded_model, _model_path_loaded

    path = model_path or LLM_MODEL_PATH

    # Return cached model if same path
    if _loaded_model is not None and _model_path_loaded == path:
        return _loaded_model

    if not path.exists():
        logger.warning("Web LLM model not found at: %s", path)
        return None

    try:
        from llama_cpp import Llama
    except ImportError:
        logger.warning("llama-cpp-python not installed, web summarisation disabled")
        return None

    logger.info("Loading web LLM model: %s", path.name)
    try:
        llm = Llama(
            model_path=str(path),
            n_ctx=LLM_N_CTX,
            verbose=False,
        )
        _loaded_model = llm
        _model_path_loaded = path
        logger.info("Web LLM model %s loaded", path.name)
        return llm
    except Exception as exc:
        logger.error("Failed to load web LLM model: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Localised prompt templates
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "ru": {
        "summary": (
            "Дай практичный ответ на вопрос, используя текст ниже.\n"
            "{distro_hint}\n"
            "Формат ответа:\n"
            "1. Проблема — 1-2 предложения.\n"
            "2. Решение — точные команды или шаги.\n"
            "3. Пояснение — кратко, только если нужно.\n"
            "Напиши 80-150 слов. Завершай каждое предложение.\n"
            "Ссылайся на источники как [1],[2].\n"
            "Указывай ТОЛЬКО те команды, что есть в тексте или стандартные менеджеры пакетов.\n"
            "НЕ придумывай команды, пакеты, авторов или URL.\n"
            "НЕ пиши 'В заключение', 'Подводя итоги', 'Надеюсь, это помогло'.\n"
            "Отвечай ТОЛЬКО на русском.\n\n"
            "Вопрос: {query}\n\n"
            "{text}\n\n"
            "Ответ:"
        ),
        "specs": (
            "Извлеки ВСЕ технические характеристики из текста ниже.\n"
            "Формат: одна характеристика на строку, «Параметр: значение».\n"
            "Пример:\n"
            "  Дисплей: 6.4\" Super AMOLED, 1080×2400, 90 Гц\n"
            "  Процессор: MediaTek Helio G99\n"
            "  ОЗУ: 8 ГБ\n"
            "  Накопитель: 128 ГБ\n"
            "  Камера: 50 Мп + 2 Мп\n"
            "  Аккумулятор: 5000 мАч, 33 Вт\n\n"
            "Правила:\n"
            "- Указывай ТОЛЬКО данные из текста. НЕ придумывай.\n"
            "- Каждый параметр — ровно ОДИН раз. Не повторяйся.\n"
            "- Если данных нет в тексте — НЕ пиши этот параметр.\n"
            "- Числа и единицы — точно как в тексте.\n"
            "- НЕ добавляй комментарии, выводы или рекламу.\n\n"
            "Вопрос: {query}\n\n"
            "{text}\n\n"
            "Характеристики:"
        ),
        "combine": (
            "Объедини ответы в один краткий ответ (80-150 слов).\n"
            "{distro_hint}\n"
            "Сначала — решение (команды/шаги), потом пояснение.\n"
            "Без повторов. Сохрани источники [1],[2].\n"
            "НЕ пиши 'В заключение' или 'Подводя итоги'.\n"
            "Отвечай ТОЛЬКО на русском.\n\n"
            "Вопрос: {query}\n\n"
            "{partial_summaries}\n\n"
            "Итог:"
        ),
        "partial": (
            "Кратко ответь по тексту (40-60 слов, команды и факты).\n"
            "Завершай предложения. Отвечай на русском.\n\n"
            "Вопрос: {query}\n\n"
            "Часть {i}/{total}:\n{chunk}\n\n"
            "Ответ:"
        ),
    },
    "uk": {
        "summary": (
            "Дай практичну відповідь, використовуючи текст нижче.\n"
            "{distro_hint}\n"
            "Формат:\n"
            "1. Проблема — 1-2 речення.\n"
            "2. Рішення — точні команди або кроки.\n"
            "3. Пояснення — коротко, якщо потрібно.\n"
            "80-150 слів. Заверши кожне речення.\n"
            "Вказуй джерела [1],[2]. Тільки команди з тексту.\n"
            "НЕ вигадуй команди, пакети чи URL.\n"
            "Відповідай ТІЛЬКИ українською.\n\n"
            "Питання: {query}\n\n"
            "{text}\n\n"
            "Відповідь:"
        ),
        "specs": (
            "Витягни ВСІ технічні характеристики з тексту нижче.\n"
            "Формат: один параметр на рядок, «Параметр: значення».\n"
            "Правила:\n"
            "- ТІЛЬКИ дані з тексту. НЕ вигадуй.\n"
            "- Кожен параметр — рівно ОДИН раз.\n"
            "- Числа і одиниці — точно як у тексті.\n\n"
            "Питання: {query}\n\n"
            "{text}\n\n"
            "Характеристики:"
        ),
        "combine": (
            "Об'єднай у одну відповідь (80-150 слів).\n"
            "{distro_hint}\n"
            "Рішення спочатку, потім пояснення.\n"
            "Без повторів. Збережи джерела.\n"
            "Відповідай ТІЛЬКИ українською.\n\n"
            "Питання: {query}\n\n"
            "{partial_summaries}\n\n"
            "Підсумок:"
        ),
        "partial": (
            "Коротко відповідж (40-60 слів, команди і факти).\n\n"
            "Питання: {query}\n\n"
            "Частина {i}/{total}:\n{chunk}\n\n"
            "Відповідь:"
        ),
    },
}

_PROMPT_TEMPLATES_EN = {
    "summary": (
        "Give a practical answer using the text below.\n"
        "{distro_hint}\n"
        "Format:\n"
        "1. Problem — 1-2 sentences.\n"
        "2. Solution — exact commands or steps.\n"
        "3. Explanation — brief, only if needed.\n"
        "Write 80-150 words. Finish every sentence.\n"
        "Cite sources as [1],[2]. Only use commands found in the text.\n"
        "Do NOT invent commands, packages, or URLs.\n"
        "Do NOT write 'In conclusion' or 'I hope this helps'.\n\n"
        "Question: {query}\n\n"
        "{text}\n\n"
        "Answer in {language_name}:"
    ),
    "specs": (
        "Extract ALL technical specifications from the text below.\n"
        "Format: one spec per line, 'Parameter: value'.\n"
        "Rules:\n"
        "- ONLY data from the text. Do NOT invent.\n"
        "- Each parameter exactly ONCE. No repetition.\n"
        "- Numbers and units exactly as in the text.\n"
        "- NO comments, conclusions, or ads.\n\n"
        "Question: {query}\n\n"
        "{text}\n\n"
        "Specifications:"
    ),
    "combine": (
        "Combine into one answer (80-150 words).\n"
        "{distro_hint}\n"
        "Solution first, then explanation.\n"
        "No duplicates. Keep source numbers [1],[2].\n\n"
        "Question: {query}\n\n"
        "{partial_summaries}\n\n"
        "Result in {language_name}:"
    ),
    "partial": (
        "Answer briefly (40-60 words, commands and facts).\n"
        "Finish sentences. In {language_name}.\n\n"
        "Question: {query}\n\n"
        "Part {i}/{total}:\n{chunk}\n\n"
        "Answer:"
    ),
}

_FOLLOWUP_TEMPLATES: Dict[str, str] = {
    "ru": (
        "Ответь на уточняющий вопрос, используя контекст и текст ниже.\n"
        "{distro_hint}\n"
        "Напиши 80-150 слов. Сначала решение, потом пояснение.\n"
        "Ссылайся на [1],[2]. Без повторов. Без URL.\n"
        "Указывай ТОЛЬКО команды из текста. НЕ придумывай команды.\n"
        "Отвечай ТОЛЬКО на русском.\n\n"
        "Контекст:\n{context}\n\n"
        "Вопрос: {query}\n\n"
        "{text}\n\n"
        "Ответ:"
    ),
    "uk": (
        "Відповідж на уточнювальне питання, використовуючи контекст та текст.\n"
        "{distro_hint}\n"
        "Напиши 80-150 слів. Рішення спочатку, потім пояснення.\n"
        "Вказуй [1],[2]. Без повторів. Без URL.\n"
        "Тільки команди з тексту. НЕ вигадуй команди.\n"
        "Відповідай ТІЛЬКИ українською.\n\n"
        "Контекст:\n{context}\n\n"
        "Питання: {query}\n\n"
        "{text}\n\n"
        "Відповідь:"
    ),
}

_FOLLOWUP_TEMPLATE_EN = (
    "Answer the follow-up question using the context and text below.\n"
    "{distro_hint}\n"
    "Write 80-150 words. Solution first, then explanation.\n"
    "Cite sources as [1],[2]. Only use commands from the text.\n\n"
    "Context:\n{context}\n\n"
    "Question: {query}\n\n"
    "{text}\n\n"
    "Detailed answer in {language_name}:"
)

_MERGE_TEMPLATES: Dict[str, str] = {
    "ru": (
        "Объедини предыдущий ответ с новой информацией.\n"
        "Напиши 80-150 слов. Сначала решение, потом пояснение.\n"
        "Не повторяй одинаковые факты. Добавь только новое.\n"
        "Ссылайся на [1],[2]. Отвечай ТОЛЬКО на русском.\n\n"
        "Вопрос: {query}\n\n"
        "Предыдущий ответ:\n{old_summary}\n\n"
        "Новая информация:\n{new_text}\n\n"
        "Полный обновлённый ответ на русском:"
    ),
}

_MERGE_TEMPLATE_EN = (
    "Merge the previous answer with new information.\n"
    "Write 80-150 words. Solution first, then explanation.\n"
    "Do NOT repeat identical facts. Only add new details.\n"
    "Cite sources as [1],[2].\n\n"
    "Question: {query}\n\n"
    "Previous answer:\n{old_summary}\n\n"
    "New information:\n{new_text}\n\n"
    "Complete updated answer in {language_name}:"
)


# ---------------------------------------------------------------------------
# Distro hint for prompts
# ---------------------------------------------------------------------------

_DISTRO_PROMPT_HINTS: Dict[str, Dict[str, str]] = {
    "ARCH_BASED": {
        "en": "The user is on an Arch-based system (pacman). Use pacman/yay, not apt.",
        "ru": "Пользователь на Arch-системе (pacman). Используй pacman/yay, не apt.",
    },
    "DEBIAN_BASED": {
        "en": "The user is on Debian/Ubuntu (apt). Use apt/dpkg, not pacman.",
        "ru": "Пользователь на Debian/Ubuntu (apt). Используй apt/dpkg, не pacman.",
    },
    "REDHAT_BASED": {
        "en": "The user is on Fedora/RHEL (dnf). Use dnf/rpm, not apt.",
        "ru": "Пользователь на Fedora/RHEL (dnf). Используй dnf/rpm, не apt.",
    },
}


def _distro_hint(family: Optional[str], lang_code: str = "en") -> str:
    if not family or family not in _DISTRO_PROMPT_HINTS:
        return ""
    bucket = _DISTRO_PROMPT_HINTS[family]
    return bucket.get(lang_code, bucket["en"])


# ---------------------------------------------------------------------------
# Dangerous commands filter
# ---------------------------------------------------------------------------

_DANGEROUS_COMMAND_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"rm\s+-rf\s+/(?:\s|$)",
        r"rm\s+-rf\s+/\*",
        r"rm\s+-rf\s+~\s",
        r"dd\s+if=/dev/(?:zero|random|urandom)\s+of=/dev/[sh]d",
        r"mkfs\.\S+\s+/dev/[sh]d",
        r"chmod\s+-R\s+777\s+/\s",
        r":\(\)\{\s*:\|:&\s*\};:",
        r">\s*/dev/sd[a-z]",
        r"mv\s+/\s+/dev/null",
    ]
]


def filter_dangerous_commands(text: str) -> str:
    """Remove or redact dangerous / destructive commands from LLM output."""
    for pat in _DANGEROUS_COMMAND_PATTERNS:
        text = pat.sub("[DANGEROUS COMMAND REMOVED — do not execute]", text)
    return text


# ---------------------------------------------------------------------------
# LLM output quality checks
# ---------------------------------------------------------------------------

def _is_garbage(text: str) -> bool:
    """Return True if *text* looks like degenerate model output."""
    stripped = text.strip()
    if len(stripped) < 20:
        return True
    words = stripped.split()
    if not words:
        return True
    single_char_ratio = sum(1 for w in words if len(w) <= 1) / len(words)
    if single_char_ratio > 0.6:
        return True
    counts = Counter(words)
    most_common_count = counts.most_common(1)[0][1]
    if most_common_count / len(words) > 0.4 and len(words) > 8:
        return True
    return False


_OUTPUT_NOISE_RE = [
    re.compile(p, re.IGNORECASE) for p in [
        r"cite this", r"share this", r"related articles?", r"read more",
        r"advertisement", r"subscribe", r"follow us", r"click here",
        r"all rights reserved", r"^by [A-Z][a-z]+ [A-Z]", r"^written by",
        r"^published on", r"^source:\s", r"^author:\s",
        r"\bhttps?://", r"\bwww\.",
        r"^in conclusion", r"^to summarize", r"^i hope this helps",
        r"^в заключение", r"^подводя итоги", r"^надеюсь.+помогло",
        r"читайте также", r"поделиться", r"подписаться", r"реклама",
    ]
]

_GARBAGE_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"cite this article", r"share this article",
        r"subscribe to", r"follow us on", r"all rights reserved",
        r"copyright \d{4}", r"читайте также", r"подписаться на",
    ]
]


def _clean_summary(text: str) -> str:
    """Post-process LLM output: strip noise, dedup, clean up."""
    if not text:
        return ""

    lines = text.rstrip().split("\n")

    # Remove trailing source labels
    while lines:
        stripped = lines[-1].strip()
        if re.match(r'^\[(?:src-)?\d+\]', stripped):
            lines.pop()
        elif stripped == "":
            lines.pop()
        else:
            break

    # Remove noise lines
    cleaned: List[str] = []
    for line in lines:
        s = line.strip()
        if s == "":
            cleaned.append(line)
            continue
        if any(pat.search(s) for pat in _OUTPUT_NOISE_RE):
            continue
        cleaned.append(line)

    # Deduplicate
    seen: set = set()
    deduped: List[str] = []
    for line in cleaned:
        key = line.strip().lower()
        if key == "":
            deduped.append(line)
            continue
        if key not in seen:
            seen.add(key)
            deduped.append(line)

    result = "\n".join(deduped).rstrip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def _is_truncated(text: str, min_words: int = 50) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    words = stripped.split()
    if len(words) < min_words:
        return True
    if stripped[-1] not in '.!?»"\')\u2026':
        return True
    return False


def _has_output_garbage(text: str) -> bool:
    if not text:
        return False
    for pat in _GARBAGE_PATTERNS:
        if pat.search(text):
            return True
    sentences = [s.strip() for s in re.split(r'[.!?]\s+', text) if len(s.strip()) > 20]
    if sentences:
        counts = Counter(s.lower() for s in sentences)
        if counts and counts.most_common(1)[0][1] >= 3:
            return True
    return False


# ---------------------------------------------------------------------------
# Core LLM call
# ---------------------------------------------------------------------------

_LLM_TIMEOUT = 45  # seconds — hard cap to prevent 18-min hangs (was 90)


def _call_llm(llm, prompt: str, max_tokens: int = LLM_MAX_TOKENS) -> str:
    """Send *prompt* to the loaded model and return generated text."""
    import signal

    class _LLMTimeout(Exception):
        pass

    def _alarm_handler(signum, frame):
        raise _LLMTimeout("LLM inference exceeded timeout")

    for attempt in range(_LLM_RETRIES + 1):
        try:
            temp = LLM_TEMPERATURE if attempt == 0 else LLM_TEMPERATURE + 0.15
            # Set hard timeout to prevent CPU-bound hangs
            old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(_LLM_TIMEOUT)
            try:
                with _llm_lock:
                    response = llm(
                        prompt,
                        max_tokens=max_tokens,
                        temperature=temp,
                        repeat_penalty=LLM_REPEAT_PENALTY,
                        top_p=LLM_TOP_P,
                        top_k=LLM_TOP_K,
                        stop=LLM_STOP,
                    )
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
            result = response["choices"][0]["text"].strip().strip("-").strip()

            if not _is_garbage(result):
                return result

            if attempt == _LLM_RETRIES:
                return "[Summary unavailable — model produced incoherent output.]"
        except _LLMTimeout:
            logger.warning("LLM inference timeout (%ds) — returning partial", _LLM_TIMEOUT)
            return "[Время генерации истекло — используйте сырые данные.]"
        except Exception as exc:
            if attempt == _LLM_RETRIES:
                return f"[LLM inference error: {exc}]"
    return ""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _get_templates(lang_code: str) -> Dict[str, str]:
    tpl = _PROMPT_TEMPLATES.get(lang_code, _PROMPT_TEMPLATES_EN)
    # Ensure 'specs' key exists — fall back to EN specs if missing
    if "specs" not in tpl:
        tpl = dict(tpl)
        tpl["specs"] = _PROMPT_TEMPLATES_EN["specs"]
    return tpl


def _build_summary_prompt(
    query: str, text: str, source_urls: List[str], language_name: str,
    *, lang_code: str = "en", distro_hint: str = "",
) -> str:
    tpl = _get_templates(lang_code)["summary"]
    return tpl.format(
        query=query, text=text, language_name=language_name,
        distro_hint=distro_hint,
    )


def _build_combine_prompt(
    query: str, partial_summaries: str, language_name: str,
    *, lang_code: str = "en", distro_hint: str = "",
) -> str:
    tpl = _get_templates(lang_code)["combine"]
    return tpl.format(
        query=query, partial_summaries=partial_summaries,
        language_name=language_name, distro_hint=distro_hint,
    )


# ---------------------------------------------------------------------------
# Public API: summarize web content
# ---------------------------------------------------------------------------

def summarize_web_text(
    query: str,
    text: str,
    source_urls: Optional[List[str]] = None,
    language: str = "ru",
    mode: str = "auto",
) -> Optional[str]:
    """
    Summarize web-extracted text using the local mini GGUF model.

    Args:
        query: User's search query.
        text: Combined web-page text (from collect_pages_text).
        source_urls: URLs that contributed text (for citations).
        language: BCP-47 language code.
        mode: "auto" (detect from query), "specs", or "summary".

    Returns:
        Summary string, or None if model is unavailable.
    """
    from lina.parser.page_parser import chunk_text
    from lina.parser.text_cleaner import detect_linux_family

    llm = load_llm()
    if llm is None:
        return None

    if source_urls is None:
        source_urls = []

    lang_name = _language_name(language)
    chunks = chunk_text(text, chunk_chars=_MAX_TEXT_CHARS_PER_CALL)
    chunks = [c for c in chunks if c.strip()]
    if not chunks:
        return None
    # Cap to 2 chunks max — more would take >3 min on CPU
    if len(chunks) > 2:
        logger.info("Capping %d chunks to 2 (speed guard)", len(chunks))
        chunks = chunks[:2]

    family = detect_linux_family(query + "\n" + text)
    hint = _distro_hint(family, language)
    if family:
        logger.debug("Detected distro family: %s", family)

    # --- Auto-detect mode ---
    _RE_SPECS = re.compile(
        r'характеристик|спецификац|параметр|specs?\b|specifications?',
        re.IGNORECASE,
    )
    if mode == "auto":
        mode = "specs" if _RE_SPECS.search(query) else "summary"
    use_specs = (mode == "specs")
    if use_specs:
        logger.info("Web LLM mini: режим SPECS (извлечение характеристик)")

    t0 = time.perf_counter()

    # --- Single-chunk path (most common) ---
    if len(chunks) == 1:
        if use_specs:
            tpl_key = "specs"
            templates = _get_templates(language)
            prompt = templates[tpl_key].format(
                query=query, text=chunks[0],
            )
        else:
            prompt = _build_summary_prompt(
                query, chunks[0], source_urls, lang_name,
                lang_code=language, distro_hint=hint,
            )
        result = _call_llm(llm, prompt, max_tokens=LLM_MAX_TOKENS)
        cleaned = _clean_summary(result)

        # Only retry if output has garbage AND retries are enabled.
        # For specs mode, truncation is acceptable (we got the key facts).
        if _LLM_RETRIES > 0 and _has_output_garbage(cleaned) and not use_specs:
            result2 = _call_llm(llm, prompt, max_tokens=LLM_MAX_TOKENS)
            cleaned2 = _clean_summary(result2)
            if not _has_output_garbage(cleaned2) and (
                _has_output_garbage(cleaned)
                or len(cleaned2.split()) > len(cleaned.split())
            ):
                cleaned = cleaned2

        dt = time.perf_counter() - t0
        logger.info("Web LLM mini: готово за %.1fс (1 чанк, %d слов)",
                      dt, len(cleaned.split()))
        return filter_dangerous_commands(cleaned)

    # --- Multi-chunk hierarchical path ---
    n = len(chunks)
    logger.info("Текст разделён на %d чанков", n)

    tpl_partial = _get_templates(language)["partial"]

    def _summarise_chunk(idx: int) -> tuple:
        prompt = tpl_partial.format(
            query=query, chunk=chunks[idx],
            i=idx + 1, total=n, language_name=lang_name,
        )
        return idx, _call_llm(llm, prompt, max_tokens=_CHUNK_MAX_TOKENS)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    partial_results: List[str] = [""] * n
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = {pool.submit(_summarise_chunk, i): i for i in range(n)}
        for future in as_completed(futures):
            idx, summary = future.result()
            partial_results[idx] = summary

    valid_partials = [p for p in partial_results if p and not p.startswith("[")]
    if not valid_partials:
        return None

    # Combine
    combined_partials = "\n\n".join(valid_partials)
    if len(combined_partials) > 4000:
        combined_partials = combined_partials[:4000] + " ..."

    combine_prompt = _build_combine_prompt(
        query, combined_partials, lang_name,
        lang_code=language, distro_hint=hint,
    )
    result = _call_llm(llm, combine_prompt, max_tokens=_COMBINE_MAX_TOKENS)
    cleaned = _clean_summary(result)

    # Skip costly retry for specs mode — truncated facts are still useful.
    if _LLM_RETRIES > 0 and _has_output_garbage(cleaned) and not use_specs:
        result2 = _call_llm(llm, combine_prompt, max_tokens=_COMBINE_MAX_TOKENS)
        cleaned2 = _clean_summary(result2)
        if not _has_output_garbage(cleaned2) and (
            _has_output_garbage(cleaned)
            or len(cleaned2.split()) > len(cleaned.split())
        ):
            cleaned = cleaned2

    dt = time.perf_counter() - t0
    logger.info("Web LLM mini: готово за %.1fс (%d чанков + объединение, %d слов)",
                  dt, n, len(cleaned.split()))
    return filter_dangerous_commands(cleaned)


# ---------------------------------------------------------------------------
# Follow-up summarisation (с контекстом разговора)
# ---------------------------------------------------------------------------

def summarize_followup_web(
    query: str,
    text: str,
    source_urls: Optional[List[str]] = None,
    context: str = "",
    language: str = "ru",
) -> Optional[str]:
    """
    Суммаризация follow-up запроса с учётом контекста разговора.

    Args:
        query:       Уточняющий вопрос пользователя.
        text:        Текст веб-страниц (из collect_pages_text).
        source_urls: URL-ы, из которых извлечён текст.
        context:     Контекст разговора (из WebSearchSession.get_history_text).
        language:    Код языка (BCP-47).

    Returns:
        Суммаризация или None если модель недоступна.
    """
    from lina.parser.text_cleaner import detect_linux_family

    llm = load_llm()
    if llm is None:
        return None

    lang_name = _language_name(language)
    tpl = _FOLLOWUP_TEMPLATES.get(language, _FOLLOWUP_TEMPLATE_EN)

    family = detect_linux_family(query + "\n" + text)
    hint = _distro_hint(family, language)

    prompt = tpl.format(
        query=query,
        text=text[:4000],
        context=context,
        language_name=lang_name,
        distro_hint=hint,
    )

    t0 = time.perf_counter()
    result = _call_llm(llm, prompt, max_tokens=LLM_MAX_TOKENS)
    cleaned = _clean_summary(result)

    if _is_truncated(cleaned):
        result2 = _call_llm(llm, prompt, max_tokens=LLM_MAX_TOKENS)
        cleaned2 = _clean_summary(result2)
        if len(cleaned2.split()) > len(cleaned.split()):
            cleaned = cleaned2

    dt = time.perf_counter() - t0
    logger.info("Web LLM mini: follow-up за %.1fс (%d слов)", dt, len(cleaned.split()))
    return filter_dangerous_commands(cleaned)


# ---------------------------------------------------------------------------
# Merge summaries (объединение старого ответа с новой информацией)
# ---------------------------------------------------------------------------

def merge_web_summaries(
    query: str,
    old_summary: str,
    new_text: str,
    language: str = "ru",
) -> Optional[str]:
    """
    Объединить предыдущий ответ с новой информацией без дублирования.

    Args:
        query:       Вопрос пользователя.
        old_summary: Предыдущая суммаризация.
        new_text:    Новый текст (из веб-страниц или новая суммаризация).
        language:    Код языка (BCP-47).

    Returns:
        Объединённая суммаризация или None если модель недоступна.
    """
    llm = load_llm()
    if llm is None:
        return None

    lang_name = _language_name(language)
    tpl = _MERGE_TEMPLATES.get(language, _MERGE_TEMPLATE_EN)

    # Обрезать входы для контекстного окна
    if len(old_summary) > 800:
        old_summary = old_summary[:800] + " …"
    if len(new_text) > 1500:
        new_text = new_text[:1500] + " …"

    prompt = tpl.format(
        query=query,
        old_summary=old_summary,
        new_text=new_text,
        language_name=lang_name,
    )

    t0 = time.perf_counter()
    result = _call_llm(llm, prompt, max_tokens=LLM_MAX_TOKENS)
    cleaned = _clean_summary(result)

    if _is_truncated(cleaned):
        result2 = _call_llm(llm, prompt, max_tokens=LLM_MAX_TOKENS)
        cleaned2 = _clean_summary(result2)
        if len(cleaned2.split()) > len(cleaned.split()):
            cleaned = cleaned2

    dt = time.perf_counter() - t0
    logger.info("Web LLM mini: merge за %.1fс (%d слов)", dt, len(cleaned.split()))
    return filter_dangerous_commands(cleaned)
