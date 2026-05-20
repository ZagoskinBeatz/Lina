# -*- coding: utf-8 -*-
"""
Lina Core — Intent Router (Phase 22).

Чёткая маршрутизация намерений пользователя.
Router ТОЛЬКО классифицирует — НИКОГДА не выполняет.

Поток:
  USER_INPUT → IntentRouter.route() → RoutingDecision
    → EXECUTION_LAYER выбирает engine по intent

Whitelist intent:
  chat, math, system_command, file_operation,
  open_application, web_search, weather_query, install_application,
  web, rag, cv, tool_explicit, meta, chain, macro

Иерархия приоритетов (OPEN_APPLICATION > WEATHER_QUERY > WEB_SEARCH > WEB):
  Если в тексте глагол запуска (открой/запусти/run/launch/стартуй)
  → intent = OPEN_APPLICATION (даже если слово похоже на термин).
  Только если приложение не найдено → fallback в WEB_SEARCH.

Если confidence < threshold → LLM fallback (chat).
"""

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any

logger = logging.getLogger("lina.core.intent_router")


# ═══════════════════════════════════════════════════════════
#  Intent Types (Whitelist)
# ═══════════════════════════════════════════════════════════

class Intent(str, Enum):
    """Whitelist намерений. Каждое → конкретный engine."""
    CHAT = "chat"                      # Свободное общение → LLM
    MATH = "math"                      # Математика → LLM
    SYSTEM_COMMAND = "system_command"   # ! команды → subprocess
    FILE_OPERATION = "file_operation"   # Файлы → builtin handler
    WEB = "web"                        # Общий web → legacy compatibility
    WEB_SEARCH = "web_search"          # Поиск в интернете → WebSearchEngine
    WEATHER_QUERY = "weather_query"    # Погода → WebSearchEngine.weather
    INSTALL_APPLICATION = "install_application"  # Установка → пакетные менеджеры
    RAG = "rag"                        # База знаний → RAG engine
    CV = "cv"                          # Computer Vision → CV engine
    TOOL_EXPLICIT = "tool_explicit"    # Явный tool → tool engine
    META = "meta"                      # /команды → meta handler
    CHAIN = "chain"                    # Цепочки → chain handler
    MACRO = "macro"                    # Макросы → macro handler
    OPEN_APPLICATION = "open_application"  # Запуск приложений → ApplicationResolver
    SYSTEM_DIAGNOSTIC = "system_diagnostic"  # Диагностика → PROBLEM TERMINATOR


# ═══════════════════════════════════════════════════════════
#  Routing Decision
# ═══════════════════════════════════════════════════════════

@dataclass
class RoutingDecision:
    """Результат маршрутизации. Router возвращает ТОЛЬКО это."""
    intent: Intent = Intent.CHAT
    confidence: float = 0.5
    reason: str = ""
    alternatives: List[Intent] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "alternatives": [a.value for a in self.alternatives],
        }


# ═══════════════════════════════════════════════════════════
#  Паттерны классификации
# ═══════════════════════════════════════════════════════════

# Meta-команды (начинаются с /)
_META_PATTERN = re.compile(r"^/\w+", re.IGNORECASE)

# Системные команды (начинаются с !)
_SYSTEM_CMD_PATTERN = re.compile(r"^!\s*\S+")

# Цепочки (→, ->, =>)
_CHAIN_PATTERN = re.compile(r"\u2192|->|=>|;\s*(затем|потом)", re.IGNORECASE)

# CV-паттерны
_CV_PATTERNS = [
    re.compile(r"скриншот|screenshot", re.IGNORECASE),
    re.compile(r"распознай\s+текст|ocr", re.IGNORECASE),
    re.compile(r"найди\s+(ошибки|прогресс)\s+на\s+экране", re.IGNORECASE),
    re.compile(r"анализ\s+(gui|скриншот)", re.IGNORECASE),
    re.compile(r"статус\s+cv", re.IGNORECASE),
    re.compile(r"список\s+скриншотов", re.IGNORECASE),
]

# RAG-паттерны
_RAG_PATTERNS = [
    re.compile(r"индексируй|проиндексируй", re.IGNORECASE),
    re.compile(r"поиск\s+в\s+базе\s+знаний", re.IGNORECASE),
    re.compile(r"статус\s+базы\s+знаний", re.IGNORECASE),
    re.compile(r"очисти\s+базу", re.IGNORECASE),
]

# Файловые операции
_FILE_PATTERNS = [
    re.compile(r"покажи\s+файлы|дерево\s+каталога", re.IGNORECASE),
    re.compile(r"прочитай\s+файл|найди\s+файл", re.IGNORECASE),
    re.compile(r"создай\s+файл|удали\s+файл", re.IGNORECASE),
    re.compile(r"(?:открой|открыть)\s+файл\s+\S+", re.IGNORECASE),
    re.compile(r"(?:открой|открыть)\s+\S+\.\w{1,5}$", re.IGNORECASE),
    re.compile(r"(?:редактируй|покажи|отредактируй)\s+\S+\.\w{1,5}", re.IGNORECASE),
]

# LLM/модель — tool_explicit
_LLM_TOOL_PATTERNS = [
    re.compile(r"загрузи\s+(мини|полную)\s+модель", re.IGNORECASE),
    re.compile(r"выгрузи\s+модель", re.IGNORECASE),
    re.compile(r"статус\s+модели", re.IGNORECASE),
    re.compile(r"очисти\s+кэш", re.IGNORECASE),
]

# Системная информация — tool_explicit
_SYSTEM_INFO_PATTERNS = [
    re.compile(r"статус\s+системы", re.IGNORECASE),
    re.compile(r"процессы", re.IGNORECASE),
    re.compile(r"обзор\s+системы", re.IGNORECASE),
    re.compile(r"сколько\s+(памят|опера|озу|ram|места|свобод|занят|ядер|проц)", re.IGNORECASE),
    re.compile(r"(память|ram|cpu|gpu|диск|процессор|ядро|ядра|загруз\w*|видеокарт\w*)\b", re.IGNORECASE),
    re.compile(r"(диагональ|экран|дисплей|монитор|разрешени\w+\s+экран)\b", re.IGNORECASE),
    re.compile(r"(информац|инфо|данные)\s+(о\s+)?(систем|компьютер|пк|ноутбук)", re.IGNORECASE),
]

# Бренды / модели устройств — если в запросе есть бренд, «процессор»/«память» — это про устройство, не про локальный PC.
_PRODUCT_BRAND_RE = re.compile(
    r"(?i)\b("
    r"oneplus|realme|samsung|galaxy|xiaomi|redmi|poco|huawei|honor"
    r"|apple|iphone|ipad|macbook|imac|pixel|google\s+pixel"
    r"|oppo|vivo|nothing|motorola|moto\b|nokia|sony|xperia"
    r"|asus|lenovo|acer|dell|hp\b|thinkpad|ideapad|pavilion"
    r"|snapdragon|dimensity|exynos|helio|mediatek|kirin|tensor"
    r"|geforce|radeon|ryzen|intel\s+core|core\s+i[3579]"
    r"|rtx\s*\d+|gtx\s*\d+|rx\s*\d+"
    r")\b",
)

# ─── WEATHER_QUERY — отдельный intent для погоды ───
_WEATHER_PATTERNS = [
    re.compile(r"погод[аеуы]\s+", re.IGNORECASE),
    re.compile(r"погод[аеуы]$", re.IGNORECASE),
    re.compile(r"погд[аеуы]", re.IGNORECASE),              # опечатка «погда»
    re.compile(r"температур[аеу]\s+", re.IGNORECASE),
    re.compile(r"weather\s+", re.IGNORECASE),
    re.compile(r"прогноз\s+(погод|на\s+сегодня|на\s+завтра)", re.IGNORECASE),
    re.compile(r"какая\s+погода", re.IGNORECASE),
    re.compile(r"какая\s+погда", re.IGNORECASE),            # опечатка
    re.compile(r"сколько\s+градусов", re.IGNORECASE),
    re.compile(r"будет\s+ли\s+дождь", re.IGNORECASE),
    re.compile(r"будет\s+ли\s+снег", re.IGNORECASE),
]

# ─── INSTALL_APPLICATION — установка приложений ───
_INSTALL_PATTERNS = [
    re.compile(r"(?:установи|инсталлируй|поставь)\s+(.+)", re.IGNORECASE),
    re.compile(r"(?:install|setup)\s+(.+)", re.IGNORECASE),
    # NB: «как установить» removed — it's an informational query (→ web_search),
    # not a command to install. Only imperative forms trigger actual install.
    re.compile(r"(?:скачать|скачай)\s+(?:и\s+установ\w+\s+)?(.+)", re.IGNORECASE),
    re.compile(r"(?:вариант|способ)\s+установить\s+(.+)", re.IGNORECASE),
    re.compile(r"можно\s+(?:ли\s+)?установить\s+(.+)", re.IGNORECASE),
    re.compile(r"хочу\s+установить\s+(.+)", re.IGNORECASE),
    re.compile(r"нужно\s+установить\s+(.+)", re.IGNORECASE),
]
_INSTALL_EXCEPTIONS = {
    "обновление", "обновления", "драйвер", "драйвера", "пакеты",
    "фильм", "видео", "музыку", "песню", "книгу", "файл",
    "игру", "картинку", "фото", "документ",
}

# Шумовые слова-категории в install-контексте
# «установи мессенджер Max» → strip «мессенджер» → «max»
_INSTALL_CATEGORY_WORDS = {
    "мессенджер", "мессенджера", "клиент", "клиента",
    "браузер", "браузера", "редактор", "редактора",
    "плеер", "плеера", "менеджер", "менеджера",
    "утилита", "утилиту", "сервис", "сервиса",
    "приложение", "программу", "программа", "программ",
    "пакет", "пакета", "среда", "среду",
}

# Стоп-слова: не могут быть именем приложения
_INSTALL_STOP_WORDS = {
    "и", "а", "но", "или", "для", "в", "на", "из", "с", "по",
    "мне", "мой", "это", "его", "её", "их", "мы", "вы", "они",
    "через", "потом", "тоже", "ещё", "еще", "все", "весь", "всё",
    "показывай", "покажи", "логи", "лог", "консоль", "консоли",
    "используя", "только",
}


def _clean_install_target(raw: str) -> str:
    """Clean captured install target: strip noise/category words.

    Examples:
        'мессенджер max'      → 'max'
        'браузер яндекс'      → 'яндекс'
        'и показывай мне логи' → ''  (garbage, all stop words)
    """
    words = raw.split()
    # Strip leading category words
    while words and words[0] in _INSTALL_CATEGORY_WORDS:
        words.pop(0)
    # Strip leading stop-words
    while words and words[0] in _INSTALL_STOP_WORDS:
        words.pop(0)
    if not words:
        return ""
    # If remaining words are all stop-words, return empty
    real_words = [w for w in words if w not in _INSTALL_STOP_WORDS]
    if not real_words:
        return ""
    return " ".join(words)

# ─── WEB_SEARCH — общий веб-поиск ───
_WEB_SEARCH_PATTERNS = [
    re.compile(r"поиск\s+в\s+(интернете|инете|сети|нете|инет)", re.IGNORECASE),
    re.compile(r"найди\s+(в\s+интернете|в\s+инете|в\s+сети|в\s+нете|информацию)", re.IGNORECASE),
    re.compile(r"найди\s+в\s+инет", re.IGNORECASE),         # «найди в инете»
    re.compile(r"(search|google|загугл)", re.IGNORECASE),
    # NB: «что такое», «расскажи про» now moved to _FACTUAL_SEARCH_PATTERNS below.
    # «кто такой» still handled by chat (people → LLM).
    re.compile(r"(загугли|нагугли|поищи)\s+", re.IGNORECASE),
    re.compile(r"найди\s+в\s+гугл", re.IGNORECASE),
    # ── «найди <anything>» catch-all — web_search unless it's a file/system term ──
    # "Найди арактреистики Tecno", "Найди обзор iPhone 15", etc.
    re.compile(
        r"найди\s+"
        r"(?!файл|ошибки|прогресс|\S+\.\w{1,5}\s*$)"
        r"\S{2,}\s+\S{2,}",
        re.IGNORECASE,
    ),
    # Явные запросы поиска: «узнай», «выясни» — НЕ для системных запросов
    re.compile(
        r"(узнай|выясни)\s+"
        r"(?!.*\b(?:верси\w*|ядр\w*|видеокарт\w*|памят\w*|процессор\w*|"
        r"cpu|ram|gpu|дис[кц]\w*|батаре\w*|загруз\w*|uptime|сколько|систем\w*)\b)",
        re.IGNORECASE,
    ),
    re.compile(r"найди\s+в\s+интернет", re.IGNORECASE),
    # ── Phase 27.1: relaxed patterns — «найди ... в интернет» with words between ──
    re.compile(r"найди\s+.{1,60}\s+в\s+(интернете|интернет|инете|инет|сети|нете)", re.IGNORECASE),
    # ── «какие параметры/характеристики/спецификации у <product>» — product spec query ──
    re.compile(
        r"как(?:ие|ой|ая|ое)\s+(?:параметр|характеристик|спецификаци|specs?)\w*\s+(?:у\s+)?\S+",
        re.IGNORECASE,
    ),
    # ── «характеристики <product>» / «спеки <product>» — direct spec lookup ──
    # Excludes system/computer words to avoid false positives like "характеристики системы"
    re.compile(
        r"(?:характеристик|спецификаци|параметр|specs?)\w*\s+(?:у\s+)?"
        r"(?!(?:систем|компьютер|ноутбук|машин|пк|pc|устройств|железа|оборудован)\w*\b)"
        r"\S{2,}",
        re.IGNORECASE,
    ),
    # ── Typo-tolerant: common misspellings of «характеристики» ──
    # "характреистики", "харатеристики", "характиристики", "харектеристики"
    re.compile(
        r"(?:хара(?:к?т[ер]{1,3}[иеэ]ст|ктери́ст))\w*\s+\S{2,}",
        re.IGNORECASE,
    ),
    # ── «<product> характеристики» — spec keyword at END (reversed order) ──
    # "Gainward RTX 3070 характеристики", "iPhone 15 Pro обзор"
    re.compile(
        r"\S{2,}\s+(?:характеристик|спецификаци|параметр|specs?|спеки|обзор|review)\w*\s*$",
        re.IGNORECASE,
    ),
    # ── Hardware brand + spec/price/review keyword (both required, any order) ──
    re.compile(
        r"(?=.*\b(?:RTX|GTX|RX\s?\d|Ryzen|Core\s*i[3-9]|Xeon|Threadripper|"
        r"GeForce|Radeon|Snapdragon|Dimensity|Exynos|"
        r"MacBook|iMac|iPhone|iPad|Galaxy|Surface|PlayStation|Xbox|Switch|"
        r"Nvidia|NVIDIA|AMD|Intel|Qualcomm|Samsung|Huawei|Xiaomi)\b)"
        r"(?=.*\b(?:характеристик|спецификаци|параметр|specs?|спеки|обзор|"
        r"benchmark|бенчмарк|тест|цена|стоимость|сравнени|vs\.?|review|отзыв)\w*\b)",
        re.IGNORECASE,
    ),
    # ── Model number (3+ digits) + spec keyword (both required, any order) ──
    re.compile(
        r"(?=.*\b\w*\d{3,}\w*\b)"
        r"(?=.*\b(?:характеристик|спецификаци|параметр|specs?|спеки|обзор|"
        r"benchmark|бенчмарк|тест|цена|стоимость|сравнени|vs\.?|review|отзыв)\w*\b)"
        r".{8,}",
        re.IGNORECASE,
    ),
    # ── «расскажи про/о <hardware_brand>» — product info via web_search ──
    re.compile(
        r"(?:расскажи|расскажите|опиши|опишите|дай\s+инф\w*)\s+(?:(?:про|о|об)\s+)?"
        r"(?=.*\b(?:RTX|GTX|RX\s?\d|Ryzen|Core\s*i[3-9]|Xeon|Threadripper|"
        r"GeForce|Radeon|Snapdragon|Dimensity|Exynos|"
        r"MacBook|iMac|iPhone|iPad|Galaxy|Surface|PlayStation|Xbox|Switch|"
        r"Nvidia|NVIDIA|AMD|Intel|Qualcomm|Samsung|Huawei|Xiaomi|Gainward|"
        r"MSI|ASUS|Gigabyte|EVGA|Zotac|Palit|Sapphire|ASRock|Corsair)\b)",
        re.IGNORECASE,
    ),
    # ── «расскажи про <model with 3+ digit number>» — product by model number ──
    re.compile(
        r"(?:расскажи|расскажите|опиши|опишите|дай\s+инф\w*)\s+(?:(?:про|о|об)\s+)?"
        r"(?=.*\b\w*\d{3,}\w*\b).{4,}",
        re.IGNORECASE,
    ),
    # ── «что за/что такое <hardware_brand/model>» — identification query ──
    re.compile(
        r"что\s+(?:за|такое)\s+"
        r"(?=.*\b(?:RTX|GTX|RX\s?\d|Ryzen|Core\s*i[3-9]|Xeon|GeForce|Radeon|"
        r"Nvidia|AMD|Intel|Snapdragon|MacBook|iPhone|Galaxy|PlayStation|Xbox)\b)",
        re.IGNORECASE,
    ),
    # ── Price queries: «сколько стоит <product>», «цена <product>» ──
    re.compile(
        r"(?:сколько\s+стоит|какая\s+цена|цена\s+на|цена\s+\S{2,}|price\s+of)\s+\S{2,}",
        re.IGNORECASE,
    ),
    # ── Version/release queries: «какая последняя версия X», «когда вышел X» ──
    re.compile(
        r"(?:последняя|актуальная|текущая|новая|свежая)\s+верси[яию]\s+\S{2,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:какая|какой)\s+(?:последняя|актуальная|текущая|новая)\s+верси[яию]",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:когда\s+вы(?:шел|шла|йдет|ходит)|дата\s+выхода|release\s+date)\s+\S{2,}",
        re.IGNORECASE,
    ),
    # ── Comparison: «X vs Y», «X или Y что лучше», «сравни X и Y» ──
    re.compile(
        r"\S{2,}\s+(?:vs\.?|versus|против)\s+\S{2,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:сравни|сравнение|сравнить)\s+\S{2,}\s+(?:и|с|or|and|vs)\s+\S{2,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:сравни|сравнение|сравнить)\s+.{3,40}\s+(?:и|с|or|and|vs)\s+.{3,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\S{2,}\s+(?:или|or)\s+\S{2,}\s+(?:что\s+лучше|лучше|which\s+is\s+better)",
        re.IGNORECASE,
    ),
    # ── «стоит ли покупать/брать <product>» — purchase/recommendation query ──
    re.compile(
        r"(?:стоит\s+ли|стоит)\s+(?:покупать|брать|купить|взять)\s+\S{2,}",
        re.IGNORECASE,
    ),
    # ── «какой/какая/какое <property> у <brand/device>» — device property query ──
    # "Какая диагональ у Samsung Galaxy S24 Ultra", "Какой экран у iPhone 15 Pro"
    re.compile(
        r"как(?:ой|ая|ое|ие)\s+\S{2,}.{0,30}\s+(?:у|в|на)\s+"
        r"(?=.*\b(?:Samsung|Galaxy|iPhone|iPad|MacBook|Xiaomi|Redmi|Poco|"
        r"Huawei|Honor|Realme|OnePlus|Oppo|Vivo|Nothing|Motorola|Nokia|Sony|"
        r"Pixel|ASUS|Lenovo|Acer|Dell|HP|ThinkPad|Surface|"
        r"RTX|GTX|Ryzen|GeForce|Radeon|Intel|AMD|Nvidia)\b)",
        re.IGNORECASE,
    ),
    # ── «<brand/device> какой <property>» — reversed word order ──
    # "Samsung Galaxy S24 Ultra какая диагональ", "iPhone 15 экран"
    re.compile(
        r"(?:Samsung|Galaxy|iPhone|iPad|MacBook|Xiaomi|Huawei|Honor|Pixel|"
        r"Realme|OnePlus|Oppo|Nothing)\s+\S{2,}.{0,30}\b"
        r"(?:диагональ|экран|дисплей|камер\w*|батаре\w*|аккумулятор\w*|"
        r"процессор\w*|чипсет\w*|памят\w*|вес\w*|размер\w*|разрешени\w*|"
        r"частот\w*|яркост\w*|заряд\w*|объ[её]м\w*)\b",
        re.IGNORECASE,
    ),
    # ── «альтернативы <software>», «аналог <software>» ──
    re.compile(
        r"(?:альтернатив|аналог|замен)\w*\s+(?:для\s+|к\s+)?\S{2,}",
        re.IGNORECASE,
    ),
    # ── «как скачать/установить/настроить/обновить <X>» — informational queries ──
    # Route through web_search so user gets official instructions, not LLM guesses
    re.compile(
        r"(?:как|где)\s+(?:скачать|загрузить|download|установить|настроить|обновить|удалить)\s+\S{2,}",
        re.IGNORECASE,
    ),
    # ── Factual/comparison/rating queries → web_search ──
    # «рейтинг X», «топ X», «лучшие X»
    re.compile(
        r"(?:рейтинг|топ\s*\d*|лучши[еёйх]|worst|best|ranking)\s+\S{2,}",
        re.IGNORECASE,
    ),
    # «чем отличается X от Y», «в чём разница между X и Y», «разница между»
    re.compile(
        r"(?:чем\s+отлича|в\s+ч[её]м\s+разниц|разница\s+между|difference\s+between)",
        re.IGNORECASE,
    ),
    # ── «чем X лучше/хуже Y», «что лучше X или Y» — comparison queries ──
    re.compile(
        r"чем\s+.{2,50}?\s+(?:лучше|хуже|быстрее|медленнее|мощнее|дешевле|дороже)\s+\S{2,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:что|какой|какая|какое)\s+лучше\b.{2,80}?\bили\b",
        re.IGNORECASE,
    ),
    # «сравни X и Y», «X vs Y» — comparison queries
    re.compile(
        r"(?:сравни|сравнить|сравнение)\s+.{2,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\S{2,}\s+vs\s+\S{2,}",
        re.IGNORECASE,
    ),
    # «сколько X в/у/на» — factual count questions
    re.compile(
        r"сколько\s+\S+\s+(?:в\b|у\b|на\b|из\b)",
        re.IGNORECASE,
    ),
    # «сколько живут/весит/весят/длится/населения» — factual questions
    re.compile(
        r"сколько\s+(?:живут|живёт|длится|длилась|весит|весят|видов|населения|жителей|людей\s+жив[ёеу])",
        re.IGNORECASE,
    ),
    # «кто написал/изобрёл/создал» — factual authorship questions
    re.compile(
        r"кто\s+(?:написал|изобр[её]л|создал|придумал|открыл|первый|first)",
        re.IGNORECASE,
    ),
    # «в каком году» — historical date questions
    re.compile(
        r"в\s+каком\s+году",
        re.IGNORECASE,
    ),
    # «что такое X» — any «что такое» question → web_search
    # Local LLM can't provide quality explanations, web produces better results.
    re.compile(
        r"что\s+такое\s+\S{2,}",
        re.IGNORECASE,
    ),
    # «расскажи про X» / «расскажи о X» → web_search
    re.compile(
        r"расскажи\s+(?:про|о|об)\s+\S{2,}",
        re.IGNORECASE,
    ),
    # «что нового в X» / «что изменилось в X» → web_search (needs latest data)
    re.compile(
        r"что\s+(?:нового|изменилось|появилось|добавили|улучшили)\s+(?:в\s+)?\S{2,}",
        re.IGNORECASE,
    ),
    # «какое самое быстрое/большое/высокое X» — superlative knowledge questions
    re.compile(
        r"как(?:ое|ой|ая|ие)\s+(?:самое?|самая|самые|самый)\s+\S{2,}",
        re.IGNORECASE,
    ),
    # «какая столица/площадь/валюта/население X» — factual knowledge
    re.compile(
        r"как(?:ая|ой|ое|ие)\s+(?:столиц|площадь|валют|населен|температур|высот"
        r"|глубин|длин|скорост|мощност|частот|стоимост|цен)\w*",
        re.IGNORECASE,
    ),
    # «сколько человек/людей живёт/живут на Земле» — population queries
    re.compile(
        r"сколько\s+(?:человек|людей)\s+(?:живёт|живут|проживает)",
        re.IGNORECASE,
    ),
    # «обзор <product>» — product review (requires brand/model after "обзор")
    re.compile(
        r"обзор\s+(?=.*\b(?:RTX|GTX|RX|Ryzen|Core|Xeon|GeForce|Radeon|"
        r"Snapdragon|Dimensity|MacBook|iMac|iPhone|iPad|Galaxy|Surface|"
        r"PlayStation|Xbox|Switch|Nvidia|AMD|Intel|Qualcomm|Samsung|Huawei|"
        r"Xiaomi|Realme|OnePlus|Poco|ASUS|ROG|MSI|Gigabyte|Lenovo|HP|Dell|"
        r"Acer|Apple|Google|Pixel|Nothing|Sony|LG|Motorola|Honor|Vivo|Oppo|"
        r"Tecno|Infinix|ZTE|Meizu|Nokia|Redmi|Black\s*Shark)\b)",
        re.IGNORECASE,
    ),
    # «<brand> <model> specs/review/обзор» — English spec queries
    re.compile(
        r"\b(?:specs?|specifications?|review|benchmark|обзор)\b.*\b\w*\d{1,}\w*\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\w*\d{1,}\w*\b.*\b(?:specs?|specifications?|review|benchmark)\b",
        re.IGNORECASE,
    ),
]

# ─── WEB (legacy) — валюта, курсы, общий web ───
_WEB_PATTERNS = [
    re.compile(r"курс\s+(валют|доллар|евро|рубл|юан|биткоин|usd|eur|gbp)", re.IGNORECASE),
    re.compile(r"(usd|eur|gbp|cny)\s*(to|в|к)\s*(rub|руб)", re.IGNORECASE),
    re.compile(r"конвертир\w*\s+(доллар|евро|рубл|фунт|валют)", re.IGNORECASE),
    re.compile(r"сколько\s+стоит\s+(доллар|евро|биткоин)", re.IGNORECASE),
    re.compile(r"сколько\s+(сейчас\s+)?(доллар|евро|биткоин|рубль)", re.IGNORECASE),
    re.compile(r"(доллар|евро|биткоин)\s+к\s+рубл", re.IGNORECASE),
    re.compile(r"цена\s+(доллар|евро|биткоин)", re.IGNORECASE),
    re.compile(r"новост[иьей]", re.IGNORECASE),
]

# Макросы
_MACRO_PATTERNS = [
    re.compile(r"макрос\s+(запусти|список|сохрани|удали)", re.IGNORECASE),
    re.compile(r"/макрос\s+\S+", re.IGNORECASE),
]

# Запуск приложений — «открой», «запусти», «run», «launch», «стартуй»
_APP_LAUNCH_PATTERNS = [
    re.compile(
        r"(?:открой|запусти|запуск|включи|вруби|стартуй|стартани|отрой|run|launch|open|start)\s+(.+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(открыть|запустить|включить|врубить|стартовать)\s+(.+)",
        re.IGNORECASE,
    ),
]

# ─── SYSTEM_DIAGNOSTIC — диагностика и починка системы ───
_DIAGNOSTIC_PATTERNS = [
    re.compile(r"(?:почему|чому).*(?:не работает|не \bработ|сломал|зависа|не запуска|не подключ|ошибк|error|crash|fail|broken)", re.IGNORECASE),
    re.compile(r"(?:не работает|перестал|сломал|пропал|нет)\s+(?:интернет|звук|wifi|блютуз|экран|видео|сеть|vpn|принтер|usb|микрофон)", re.IGNORECASE),
    re.compile(r"(?:интернет|звук|wifi|блютуз|экран|видео|сеть|vpn|принтер|usb|микрофон)\s+(?:не работает|не подключ|перестал|сломал|пропал|отвалил|глючит|лагает)", re.IGNORECASE),
    re.compile(r"(?:диагностик|диагностир|проверь\s+систем|health.*check|починить|почини|исправить|исправь|fix\b|repair|troubleshoot)", re.IGNORECASE),
    re.compile(r"(?:ошибк|error|warning|критическ|critical|fail).{0,30}(?:систем|kernel|driver|service|пакет|package)", re.IGNORECASE),
    re.compile(r"(?:BSOD|kernel\s*panic|segfault|OOM|out\s+of\s+memory|freeze|чёрный\s+экран|синий\s+экран)", re.IGNORECASE),
    re.compile(r"(?:что\s+с|проблем\s+с|глючит|лагает|тормозит|медленн)\s+(?:систем|компьютер|ноутбук|звук|сет|интернет|диск)", re.IGNORECASE),
]

# ─── SYSTEM_CONTROL — управление настройками ОС ───
_SYSTEM_CONTROL_PATTERNS = [
    # Громкость / Volume
    re.compile(r"громкость\s*[\d%]|(?:поставь|установи|сделай|задай|выставь|верни)\s+громкость", re.IGNORECASE),
    re.compile(r"(?:убав|прибав|увелич|уменьш|повыс|пониз)\w*\s+(?:громкость|звук|volume)", re.IGNORECASE),
    re.compile(r"(?:volume|звук)\s*[\d%]", re.IGNORECASE),
    re.compile(r"(?:mute|unmute|замьют|размьют|выключи\s+звук|включи\s+звук|без\s+звука)", re.IGNORECASE),
    # Яркость / Brightness
    re.compile(r"яркость\s*[\d%]|(?:поставь|установи|сделай|задай|выставь|верни)\s+яркость", re.IGNORECASE),
    re.compile(r"(?:убав|прибав|увелич|уменьш|повыс|пониз)\w*\s+(?:яркость|brightness)", re.IGNORECASE),
    re.compile(r"(?:brightness)\s*[\d%]", re.IGNORECASE),
    # Питание / power
    re.compile(r"(?:перезагруз|перезапуст|reboot|restart|выключи\s+компьютер|shutdown|poweroff|suspend|hibernate|усып|гибернац)", re.IGNORECASE),
    re.compile(r"(?:заблокируй|lock)\s+(?:экран|screen|компьютер|сессию)", re.IGNORECASE),
    # Wi-Fi / Bluetooth toggle
    re.compile(r"(?:включи|выключи|вкл|выкл|toggle)\s+(?:wifi|wi-fi|вай-?фай|блютуз|bluetooth)", re.IGNORECASE),
    re.compile(r"(?:wifi|wi-fi|вай-?фай|блютуз|bluetooth)\s+(?:вкл|выкл|включи|выключи|on|off)", re.IGNORECASE),
    # Разрешение экрана / display
    re.compile(r"(?:разрешение\s+экрана|resolution|display\s+setting)", re.IGNORECASE),
    re.compile(r"(?:ночной\s+режим|night\s+light|тёмная\s+тема|dark\s+mode)", re.IGNORECASE),
]

# Математика
_MATH_PATTERN = re.compile(
    r"^[\d\s\+\-\*/\(\)\.\^%=]+$|"
    r"(сколько(?!\s+(времени|сейчас|часов|минут|стоит|лет|людей|раз|жителей|весит|зарабатыва|народу|живут|живёт|длится|длилась|существует|населения|видов|стран|планет|континентов|океанов|морей|городов|звёзд|языков|букв|хромосом|калорий|весят|длин|человек|костей|костей|слов|слов|глаз|ног|рук|пальцев|зубов))|вычисли|посчитай|корень|процент|площадь|объём|формула)\b",
    re.IGNORECASE,
)

# Дата/время — быстрый ответ без LLM
_DATETIME_PATTERNS = [
    re.compile(r"который\s+час", re.IGNORECASE),
    re.compile(r"сколько\s+(времени|сейчас|(сейчас\s+)?часов)", re.IGNORECASE),
    re.compile(r"как(ое|ой|ая)\s+(сейчас\s+)?(дата|число|день|time|date)", re.IGNORECASE),
    re.compile(r"какой\s+сегодня\s+день", re.IGNORECASE),
    re.compile(r"какое\s+сегодня\s+число", re.IGNORECASE),
    re.compile(r"^\s*time\s*$", re.IGNORECASE),
    re.compile(r"^\s*date\s*$", re.IGNORECASE),
    re.compile(r"(текущее|сейчас)\s+(время|дата)", re.IGNORECASE),
]

# ─── INSTALLED APP QUERIES — «какие VPN установлены», «есть ли у меня X» ───
# These must go to system_command so LLM generates bash and it gets executed.
_INSTALLED_APP_PATTERNS = [
    re.compile(r"как(?:ие|ой|ая|ое)\s+.{0,20}установлен", re.IGNORECASE),
    re.compile(r"(?:есть|стоит|имеется)\s+(?:ли\s+)?(?:у\s+меня\s+)?.{0,20}(?:установлен|программ|приложен)", re.IGNORECASE),
    re.compile(r"список\s+(?:установленных|приложений|программ|пакетов)", re.IGNORECASE),
    re.compile(r"(?:покажи|выведи|глянь|чекни)\s+(?:все\s+)?(?:установленн|приложени|программ|пакет)", re.IGNORECASE),
    re.compile(r"(?:что|какие)\s+(?:у\s+меня\s+)?(?:за\s+)?(?:приложения|программы|пакеты)\s+(?:установлен|стоят|есть)", re.IGNORECASE),
    re.compile(r"(?:установлен|стоит)\s+(?:ли\s+)?\S{2,}", re.IGNORECASE),
]

# ─── GAMING COMPATIBILITY — «можно ли поиграть», «потянет ли» ───
# These need BOTH system context (user's hardware) AND web search (game requirements).
# Route as web_search — app.py will detect gaming pattern and inject system context too.
_GAMING_COMPAT_PATTERNS = [
    re.compile(r"(?:можно|могу)\s+(?:ли\s+)?(?:(?:на\s+\S+\s+)?(?:систем|комп|пк|ноутбук)\w*\s+)?(?:поиграть|запустить|играть)\s+(?:в\s+)?\S+", re.IGNORECASE),
    re.compile(r"(?:потянет|пойд[её]т|запустится|хватит)\s+(?:ли\s+)?.{0,20}(?:игр|game)", re.IGNORECASE),
    re.compile(r"(?:игр|game)\w*\s+.{0,15}(?:потянет|пойд[её]т|запустится|хватит)", re.IGNORECASE),
    re.compile(r"(?:систем(?:ные|ых)?|минимальн(?:ые|ых)?|рекомендуем(?:ые|ых)?)\s+требовани\w*\s+\S+", re.IGNORECASE),
    re.compile(r"(?:можно|могу)\s+(?:ли\s+)?поиграть", re.IGNORECASE),
    re.compile(r"(?:потянет|пойд[её]т)\s+(?:ли\s+)?\S{2,}", re.IGNORECASE),
]

# ═══════════════════════════════════════════════════════════
#  IntentRouter
# ═══════════════════════════════════════════════════════════

class IntentRouter:
    """Маршрутизатор намерений (Phase 22).

    ТОЛЬКО классифицирует → НИКОГДА не исполняет.
    Изолирован от всех engine-ов.

    Attributes:
        confidence_threshold: Минимальная уверенность. Ниже → chat fallback.
        _stats: Счётчики маршрутизации.
    """

    def __init__(self, confidence_threshold: float = 0.5):
        self.confidence_threshold = confidence_threshold
        self._stats: Dict[str, int] = {i.value: 0 for i in Intent}
        self._stats["total"] = 0

        # Слова-исключения: не считать «запуском приложения» если совпадает
        self._app_launch_exceptions = {
            "музыку", "видео", "песню", "фильм", "трек", "воспроизведение",
            "файл", "ссылку", "сайт", "страницу", "url",
            "wifi", "wi-fi", "вай-фай", "вайфай", "блютуз", "bluetooth",
            "звук", "громкость", "яркость", "volume", "brightness",
        }

    def route(self, user_input: str) -> RoutingDecision:
        """Классифицирует ввод пользователя.

        Правила (по приоритету):
          1. Meta-команды (/...) → META
          2. Системные команды (!) → SYSTEM_COMMAND
          3. Цепочки (→, ->) → CHAIN
          4. Макросы → MACRO
          5. CV-паттерны → CV
          6. RAG-паттерны → RAG
          7. Файловые паттерны → FILE_OPERATION
          8. Web-паттерны → WEB
          9. LLM-tool → TOOL_EXPLICIT
          10. System-info → TOOL_EXPLICIT
          11. Математика → MATH
          12. Всё остальное → CHAT (LLM fallback)

        Args:
            user_input: Сырой ввод пользователя.

        Returns:
            RoutingDecision с intent, confidence, reason.
        """
        self._stats["total"] += 1
        text = user_input.strip()

        if not text:
            return RoutingDecision(
                intent=Intent.CHAT, confidence=0.0,
                reason="empty input",
            )

        decision = self._classify(text)

        # Если уверенность ниже порога → fallback к LLM
        if decision.confidence < self.confidence_threshold:
            decision.alternatives.insert(0, decision.intent)
            decision.intent = Intent.CHAT
            decision.reason = f"low confidence ({decision.confidence:.2f}), fallback to chat"

        self._stats[decision.intent.value] += 1

        logger.debug(
            "ROUTER_DECISION: intent=%s confidence=%.2f reason=%s alternatives=%s",
            decision.intent.value, decision.confidence,
            decision.reason,
            [a.value for a in decision.alternatives],
        )

        return decision

    def _classify(self, text: str) -> RoutingDecision:
        """Внутренняя классификация по паттернам."""

        # 1. Meta
        if _META_PATTERN.match(text):
            return RoutingDecision(
                intent=Intent.META, confidence=1.0,
                reason="starts with /",
            )

        # 2. System command
        if _SYSTEM_CMD_PATTERN.match(text):
            return RoutingDecision(
                intent=Intent.SYSTEM_COMMAND, confidence=1.0,
                reason="starts with !",
            )

        # 3. Chain
        if _CHAIN_PATTERN.search(text):
            return RoutingDecision(
                intent=Intent.CHAIN, confidence=0.95,
                reason="chain separator detected",
            )

        # 4. Macro
        for p in _MACRO_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.MACRO, confidence=0.95,
                    reason="macro pattern",
                )

        # 5. CV
        for p in _CV_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.CV, confidence=0.9,
                    reason="CV pattern",
                    alternatives=[Intent.CHAT],
                )

        # 6. RAG
        for p in _RAG_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.RAG, confidence=0.9,
                    reason="RAG pattern",
                )

        # 7. File operations
        for p in _FILE_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.FILE_OPERATION, confidence=0.9,
                    reason="file operation pattern",
                )

        # 7.5. OPEN_APPLICATION — «открой X», «запусти Y», «run Z»
        #       Приоритет: OPEN_APPLICATION > информационные intent
        for p in _APP_LAUNCH_PATTERNS:
            m = p.search(text)
            if m:
                app_name = m.group(m.lastindex).strip().lower()
                # Снимаем хвост «в браузере / через браузер / в хроме».
                # Без этого «открой яндекс музыку в браузере» парсится
                # как app_name="яндекс музыку в браузере", и SITE_MAP-lookup
                # промахивается (там просто «яндекс музыка»).
                app_name = re.sub(
                    r"\s+(?:в|через)\s+(?:браузере?|хроме?|firefox|chrome|"
                    r"opera|edge|safari)\.?$",
                    "",
                    app_name,
                    flags=re.IGNORECASE,
                ).strip()
                first_word = app_name.split()[0] if app_name else ""
                if first_word not in self._app_launch_exceptions:
                    return RoutingDecision(
                        intent=Intent.OPEN_APPLICATION,
                        confidence=0.95,
                        reason=f"app launch pattern: '{app_name}'",
                        metadata={"app_name": app_name},
                        alternatives=[Intent.WEB_SEARCH],
                    )

        # 7.6. SYSTEM_DIAGNOSTIC — диагностика / починка
        for p in _DIAGNOSTIC_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.SYSTEM_DIAGNOSTIC,
                    confidence=0.92,
                    reason="system diagnostic pattern",
                    alternatives=[Intent.CHAT, Intent.SYSTEM_COMMAND],
                )

        # 8. WEATHER_QUERY — погода (отдельный intent)
        for p in _WEATHER_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.WEATHER_QUERY, confidence=0.95,
                    reason="weather pattern",
                    alternatives=[Intent.WEB_SEARCH],
                )

        # 8.5 INSTALL_APPLICATION — установка приложений
        for p in _INSTALL_PATTERNS:
            m = p.search(text)
            if m:
                target = m.group(1).strip().lower() if m.lastindex else ""
                target = _clean_install_target(target)
                if not target:
                    continue
                first_w = target.split()[0] if target else ""
                if first_w not in _INSTALL_EXCEPTIONS:
                    return RoutingDecision(
                        intent=Intent.INSTALL_APPLICATION,
                        confidence=0.90,
                        reason=f"install pattern: '{target}'",
                        metadata={"app_name": target},
                        alternatives=[Intent.WEB_SEARCH],
                    )

        # 8.7. GAMING COMPATIBILITY — «можно ли поиграть в FC 25»
        # Route as web_search with gaming_query flag so app.py
        # also injects system context (hardware specs).
        for p in _GAMING_COMPAT_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.WEB_SEARCH, confidence=0.92,
                    reason="gaming compatibility query",
                    metadata={"gaming_query": True},
                    alternatives=[Intent.SYSTEM_COMMAND],
                )

        # 9. WEB_SEARCH — явный веб-поиск
        for p in _WEB_SEARCH_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.WEB_SEARCH, confidence=0.90,
                    reason="web search pattern",
                    alternatives=[Intent.CHAT],
                )

        # 10. WEB (legacy — курсы, новости)
        for p in _WEB_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.WEB, confidence=0.85,
                    reason="web pattern",
                    alternatives=[Intent.CHAT],
                )

        # 11. LLM tool explicit
        for p in _LLM_TOOL_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.TOOL_EXPLICIT, confidence=0.9,
                    reason="LLM tool pattern",
                )

        # 12. SYSTEM_CONTROL — громкость, яркость, перезагрузка, wifi и т.д.
        # Checked BEFORE system info to avoid misclassifying active commands
        # like "перезагрузи wifi" as passive info queries.
        for p in _SYSTEM_CONTROL_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.SYSTEM_COMMAND, confidence=0.92,
                    reason="system control pattern",
                    alternatives=[Intent.CHAT],
                )

        # 12.5. System info (skip if query mentions a device brand → web_search)
        for p in _SYSTEM_INFO_PATTERNS:
            if p.search(text):
                # «Realme 10 процессор» → web, «какой процессор» → system
                if _PRODUCT_BRAND_RE.search(text):
                    return RoutingDecision(
                        intent=Intent.WEB_SEARCH, confidence=0.85,
                        reason="hardware keyword + product brand → web search",
                        alternatives=[Intent.SYSTEM_COMMAND],
                    )
                return RoutingDecision(
                    intent=Intent.SYSTEM_COMMAND, confidence=0.85,
                    reason="system info pattern",
                )

        # 12.7. DATETIME — дата/время (быстрый ответ без LLM)
        for p in _DATETIME_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.SYSTEM_COMMAND, confidence=0.95,
                    reason="datetime pattern",
                    metadata={"datetime_query": True},
                    alternatives=[Intent.CHAT],
                )

        # 13. INSTALLED APP QUERIES — «какие VPN установлены»
        # Must be system_command so LLM bash blocks get executed.
        for p in _INSTALLED_APP_PATTERNS:
            if p.search(text):
                return RoutingDecision(
                    intent=Intent.SYSTEM_COMMAND, confidence=0.88,
                    reason="installed app query",
                    alternatives=[Intent.CHAT],
                )

        # 14. Math
        if _MATH_PATTERN.search(text):
            return RoutingDecision(
                intent=Intent.MATH, confidence=0.7,
                reason="math pattern",
                alternatives=[Intent.CHAT],
            )

        # 15. Default → Chat (LLM)
        return RoutingDecision(
            intent=Intent.CHAT, confidence=0.6,
            reason="no specific pattern matched, LLM fallback",
        )

    def get_stats(self) -> Dict[str, int]:
        """Статистика маршрутизации."""
        return dict(self._stats)

    def reset_stats(self) -> None:
        """Сброс счётчиков."""
        for k in self._stats:
            self._stats[k] = 0
