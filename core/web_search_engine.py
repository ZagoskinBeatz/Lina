# -*- coding: utf-8 -*-
"""
Lina Core — Web Search Engine (Phase 27).

Production-grade веб-поиск с гарантиями:
  - Никогда не падает (все исключения перехвачены)
  - Не возвращает пустые ответы (fallback цепочка)
  - Retry до 3 раз
  - Fallback: Brave → DDGS → DuckDuckGo → SearXNG → Wikipedia
  - Проверка релевантности результатов
  - Специальные flow: погода, курсы валют
  - Структурированные ответы

Архитектура:
  search(query) → retry_logic() → rank_results() → summarize()
  Если основной API не работает → fallback_logic()
  Если web capability disabled → мягкий ответ без crash

Интеграция:
  ExecutionPlan: primary_path=TOOL, fallback_path=LLM
"""

import re
import json
import logging
import time
import threading
import warnings
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
# HTMLParser replaced by lina.parser (readability-lxml + BS4)

# Suppress duckduckgo-search rename warning globally
warnings.filterwarnings("ignore", message=".*renamed to.*ddgs.*", category=RuntimeWarning)
# Suppress SSL InsecureRequestWarning (DDG lite uses plain HTTP, Brave may be HTTPS)
try:
    import urllib3 as _urllib3
    _urllib3.disable_warnings(_urllib3.exceptions.InsecureRequestWarning)
    _urllib3.disable_warnings()
except Exception:
    pass
try:
    from urllib3.exceptions import InsecureRequestWarning as _InsecureReqWarn
    warnings.filterwarnings("ignore", category=_InsecureReqWarn)
except Exception:
    pass
warnings.filterwarnings("ignore", message=".*InsecureRequestWarning.*")
warnings.filterwarnings("ignore", message=".*Unverified HTTPS.*")
warnings.filterwarnings("ignore", message=".*certificate verification.*")
warnings.filterwarnings("ignore", module="urllib3.*")
# Also suppress via requests
try:
    import requests as _req_mod
    _req_mod.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
except Exception:
    pass

logger = logging.getLogger("lina.core.web_search_engine")


# ═══════════════════════════════════════════════════════════════════════════════
#  Data Models
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SearchResult:
    """Один результат поиска."""
    title: str = ""
    url: str = ""
    snippet: str = ""
    relevance: float = 0.0   # 0.0–1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet[:300],
            "relevance": round(self.relevance, 2),
        }


@dataclass
class WebSearchResponse:
    """Полный ответ веб-поиска."""
    success: bool = False
    query: str = ""
    results: List[SearchResult] = field(default_factory=list)
    summary: str = ""
    source: str = ""          # duckduckgo / bing / scrape
    error: str = ""
    attempts: int = 0
    elapsed_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "query": self.query,
            "results_count": len(self.results),
            "summary": self.summary,
            "source": self.source,
            "error": self.error,
            "attempts": self.attempts,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class WeatherData:
    """Структурированные данные о погоде."""
    city: str = ""
    temperature: str = ""
    description: str = ""
    humidity: str = ""
    wind: str = ""
    raw_text: str = ""
    source: str = ""

    def format(self) -> str:
        parts = [f"🌤️ Погода: {self.city}"]
        if self.temperature:
            parts.append(f"  🌡️ Температура: {self.temperature}")
        if self.description:
            parts.append(f"  ☁️ {self.description}")
        if self.humidity:
            parts.append(f"  💧 Влажность: {self.humidity}")
        if self.wind:
            parts.append(f"  💨 Ветер: {self.wind}")
        if self.source:
            parts.append(f"  📍 Источник: {self.source}")
        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  HTML parsing — delegated to lina.parser (readability-lxml + BS4)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_text(html: str) -> str:
    """Извлекает текст из HTML через readability-lxml + BS4 из lina.parser."""
    try:
        from lina.parser.page_parser import extract_text as _parser_extract
        text = _parser_extract(html)
        if text and len(text) > 20:
            return text
    except Exception:
        logger.debug("lina.parser extraction failed, falling back to regex")
    # Fallback: regex-метод
    cleaned = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL)
    cleaned = re.sub(r"<style[^>]*>.*?</style>", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"&nbsp;", " ", cleaned)
    cleaned = re.sub(r"&[a-z]+;", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _strip_tags(html: str) -> str:
    """Убирает HTML-теги regex-ом."""
    return re.sub(r"<[^>]+>", "", html).strip()


# ═══════════════════════════════════════════════════════════════════════════════
#  Паттерны для определения типа запроса
# ═══════════════════════════════════════════════════════════════════════════════

_WEATHER_PATTERNS = [
    re.compile(r"погод[аеуы]", re.IGNORECASE),
    re.compile(r"weather\b", re.IGNORECASE),
    re.compile(r"температур[аеу]", re.IGNORECASE),
    re.compile(r"прогноз\s+(погоды|на\s+\w+)", re.IGNORECASE),
]

_CURRENCY_PATTERNS = [
    re.compile(r"курс\s+(валют|доллар|евро|рубл|юан)", re.IGNORECASE),
    re.compile(r"(доллар|евро|юань|фунт)\s+к\s+рубл", re.IGNORECASE),
    re.compile(r"(usd|eur|gbp|cny)\s*(to|в|к)\s*(rub|руб)", re.IGNORECASE),
    re.compile(r"сколько\s+(стоит\s+|сейчас\s+)?(доллар|евро|биткоин|юань|фунт)", re.IGNORECASE),
    re.compile(r"конвертир\w*\s+(доллар|евро|рубл|фунт|юан|биткоин|usd|eur|rub|gbp|cny)", re.IGNORECASE),
    re.compile(r"перевести\s+\w+\s+в\s+(доллар|евро|рубл)", re.IGNORECASE),
    re.compile(r"(доллар|евро|биткоин)\s+(сегодня|сейчас)", re.IGNORECASE),
    re.compile(r"цена\s+(доллар|евро|биткоин)", re.IGNORECASE),
]

_NEWS_PATTERNS = [
    re.compile(r"новост[иьей]", re.IGNORECASE),
    re.compile(r"news\b", re.IGNORECASE),
    re.compile(r"что\s+нового", re.IGNORECASE),
    re.compile(r"что\s+произошло", re.IGNORECASE),
]

# Город из запроса о погоде
_CITY_EXTRACT = re.compile(
    r"(?:погод[аеуы]|weather|прогноз)\s+(?:в|in|для|for|на)?\s*(.+)",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Typo correction for common Russian tech/search words
# ═══════════════════════════════════════════════════════════════════════════════

# Map: compiled regex → correct replacement
_TYPO_FIXES = [
    # "характреистики", "харктеристики", "хореактеристики", "харектеристики",
    # "арактреистики" (after "Найди" prefix strip), etc.
    (re.compile(r'\b(?:х?[ао]?р[аеи]?к?т[еёр]{1,3}[иеэ]стик\w*)\b',
                re.IGNORECASE), 'характеристики'),
    # "спецыфикации", "специфекации"
    (re.compile(r'\bспец[иыь]ф[иеа]кац\w*\b', re.IGNORECASE), 'спецификации'),
]


def _fix_common_typos(query: str) -> str:
    """Fix common typos in Russian search queries."""
    result = query
    for pattern, replacement in _TYPO_FIXES:
        result = pattern.sub(replacement, result)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  Direct spec site URL generation
# ═══════════════════════════════════════════════════════════════════════════════

# Stop words to strip when building device name slug
_SPEC_QUERY_STOP = {
    "характеристики", "характеристик", "спецификации", "спецификация",
    "обзор", "review", "specs", "specifications", "параметры",
    "подробные", "полные", "технические", "основные", "смартфон",
    "телефон", "phone", "smartphone", "mobile",
}


def _generate_spec_site_urls(query: str) -> list[str]:
    """
    Generate direct URLs for known spec sites based on device name in query.

    NOTE: devicespecifications.com and nanoreview.net are NOT used — they
    always serve CAPTCHA/Cloudflare challenge pages.
    Instead we rely on Wikipedia + search-discovered URLs which are more reliable.

    Returns list of URLs that may contain structured spec data.
    """
    # No reliable direct spec sites currently work without CAPTCHA.
    # Wikipedia (found via search) is the best source.
    # Keeping this function for future additions of working sites.
    return []


# ═══════════════════════════════════════════════════════════════════════════════
#  WebSearchEngine
# ═══════════════════════════════════════════════════════════════════════════════

class WebSearchEngine:
    """
    Production-grade веб-поиск.

    Гарантии:
      - Никогда не бросает исключения наружу
      - Retry до MAX_RETRIES раз
      - Fallback цепочка: Brave → DDGS → DuckDuckGo → SearXNG → Wikipedia
      - Специальные flow для погоды/курсов
      - Проверка релевантности

    Usage:
        engine = WebSearchEngine()
        resp = engine.search("погода в Перми")
        print(resp.summary)
    """

    HTTP_TIMEOUT = 8        # ← Proxy adds latency, need 8s for reliable results
    MAX_RETRIES = 1          # ← 2→1: don't waste time retrying
    RELEVANCE_THRESHOLD = 0.3
    MAX_FETCH_PAGES = 2      # ← 3→2: fewer pages = less LLM work
    MAX_SUMMARY_CHARS = 2500 # ← 3500→2500: tighter output

    # ── Кэш и rate-limiter ──
    CACHE_TTL = 300          # 5 минут TTL кэша
    CACHE_MAX_SIZE = 128     # макс. записей в кэше
    RATE_LIMIT_DELAY = 1.0   # ← DDG rate-limits aggressively through SOCKS proxy

    def __init__(self, web_capable: bool = True):
        self._web_capable = web_capable
        self._lock = threading.Lock()  # guards _stats and _cache
        self._stats = {
            "searches": 0,
            "successes": 0,
            "failures": 0,
            "retries": 0,
            "fallbacks": 0,
            "cache_hits": 0,
        }
        # LRU-кэш: {query_norm: (timestamp, WebSearchResponse)}
        self._cache: Dict[str, Tuple[float, "WebSearchResponse"]] = {}
        # Rate limiter: {engine_name: last_request_timestamp}
        self._last_request: Dict[str, float] = {}
        # Persistent session for DDG BS4 — reuses SOCKS connections
        self._ddg_session: Optional["requests.Session"] = None
        # Track DDG rate-limit state: time when 202 was last received
        self._ddg_rate_limited_until: float = 0.0

    # ── Шаблоны командных префиксов для очистки запроса ──
    _QUERY_STRIP_PATTERNS = [
        re.compile(r"^(?:найди|поищи|загугли|нагугли|выясни|узнай)\s+"
                   r"(?:в\s+(?:интернете|интернет|инете|инет|сети|нете|гугл\w*)\s*)?\s*",
                   re.IGNORECASE),
        re.compile(r"^(?:search|google)\s+(?:for\s+)?\s*", re.IGNORECASE),
        re.compile(r"^(?:найди|поищи)\s+(?:мне\s+)?(?:информацию\s+)?(?:о|об|про)\s+",
                   re.IGNORECASE),
        re.compile(r"^(?:найди|поищи)\s+(?:мне\s+)?", re.IGNORECASE),
    ]

    @classmethod
    def _clean_search_query(cls, query: str) -> str:
        """Strip command prefixes from user input to get a clean search query.

        Examples:
            'Найди в интернете характеристики Macbook M1' → 'характеристики Macbook M1'
            'загугли RTX 3070 specs' → 'RTX 3070 specs'
            'найди информацию о Linux' → 'Linux'
            'MacBook M1 характеристики' → 'MacBook M1 характеристики'
        """
        cleaned = query.strip()
        for pat in cls._QUERY_STRIP_PATTERNS:
            cleaned = pat.sub("", cleaned).strip()
            if cleaned != query.strip():
                break  # apply only the first matching pattern
        return cleaned if cleaned else query.strip()

    # ── Общие/стоп-слова, не несущие сущностной нагрузки ──
    _STOP_WORDS = {
        "характеристики", "спецификации", "обзор", "тест", "benchmark",
        "specs", "specifications", "review", "test", "compare", "comparison",
        "параметры", "параметр",
        "цена", "стоимость", "price", "купить", "buy", "найди", "поищи",
        "расскажи", "покажи", "что", "какой", "какая", "какие", "какое",
        "сколько", "где", "когда", "как", "про", "для", "это", "the",
        "vs", "или", "and", "for", "best", "top", "лучший", "лучшие",
        "новый", "новые", "new", "версия", "version",
        # Русские короткие союзы/предлоги — не несут сущностной нагрузки
        "а", "и", "в", "у", "к", "о", "с", "на", "не", "но", "ну",
        "да", "ли", "бы", "же", "от", "до", "по", "из", "ещё", "еще",
        "мне", "мой", "моя", "моё", "мои", "его", "её", "их",
        # English short stop words
        "a", "an", "is", "it", "of", "to", "in", "on", "at", "my", "me",
        "do", "so", "if", "or", "be", "we", "he",
    }

    @classmethod
    def _extract_key_terms(cls, query: str) -> set:
        """Извлекает ключевые (сущностные) слова из запроса.

        Убирает стоп-слова, оставляет бренды/модели/числа.
        'характеристики Realme 10' → {'realme', '10'}
        'RTX 3070 vs RTX 4060' → {'rtx', '3070', '4060'}
        """
        # Strip punctuation from words before matching
        words = {
            re.sub(r'[^\w]', '', w)
            for w in query.lower().split()
        }
        words.discard('')
        key = words - cls._STOP_WORDS
        # Если всё отфильтровалось — вернуть всё кроме совсем коротких
        if not key:
            key = {w for w in words if len(w) >= 3}
        return key

    @classmethod
    def _results_match_query(cls, query: str, results: list) -> bool:
        """Проверяет, что хотя бы один результат содержит ключевые слова запроса.

        Если ни один title/snippet не содержит бренд/модель из запроса —
        результаты нерелевантны (DDG вернул мусор).
        """
        key_terms = cls._extract_key_terms(query)
        if not key_terms:
            return True  # нечего проверять

        # For short queries (brand+model like "Realme 10"), require ALL
        # key terms.  For longer queries require ≥ 2/3.
        n = len(key_terms)
        if n <= 2:
            threshold = n           # require all terms
        else:
            threshold = max(2, (n * 2 + 2) // 3)

        _LIST_TITLES = ("list of ", "lists of ", "список ", "comparison of ",
                        "timeline of ", "index of ")
        _LIST_URLS   = ("/wiki/list_of_", "/wiki/lists_of_",
                        "/wiki/comparison_of_", "/wiki/index_of_")

        for r in results[:5]:
            title_l = r.title.lower()
            url_l   = r.url.lower()
            # Reject Wikipedia list/disambiguation pages — they mention the
            # device as a line item, not as the subject of the page.
            if any(t in title_l for t in _LIST_TITLES):
                continue
            if any(u in url_l for u in _LIST_URLS):
                continue
            text = (r.title + " " + r.snippet).lower()
            # Also normalize hyphens/dashes in result text for matching
            # ("wh-1000xm5" → "wh1000xm5" matches key term "wh1000xm5")
            text_norm = re.sub(r'[\-–—]', '', text)
            matched = sum(
                1 for t in key_terms
                if t in text or t in text_norm
            )
            if matched >= threshold:
                return True
        return False

    # ──────────────────────────────────────────────────
    #  Главный API
    # ──────────────────────────────────────────────────

    def search(self, query: str) -> WebSearchResponse:
        """
        Главный метод поиска.

        Pipeline:
          0. Проверить кэш (TTL 5 мин)
          1. Проверить capability
          2. Определить тип запроса (погода/курс/общий)
          3. Выполнить поиск с retry + rate limiting
          4. Ранжировать результаты
          5. Скачать top-N страниц
          6. Суммаризировать
          7. Валидировать ответ
          8. Закэшировать

        Returns:
            WebSearchResponse — всегда валидный, никогда не crash.
        """
        with self._lock:
            self._stats["searches"] += 1
        start = time.time()

        # ── Кэш ──
        cached = self._cache_get(query)
        if cached is not None:
            with self._lock:
                self._stats["cache_hits"] += 1
            return cached

        # Анти-падение: если web недоступен
        if not self._web_capable:
            return WebSearchResponse(
                success=False,
                query=query,
                error="Веб-поиск временно недоступен.",
                source="disabled",
            )

        try:
            # Определяем тип запроса
            query_type = self._classify_query(query)

            if query_type == "weather":
                resp = self._handle_weather(query, start)
            elif query_type == "currency":
                resp = self._handle_currency(query, start)
            else:
                resp = self._handle_general_search(query, start)

            # Кэшировать успешные результаты
            if resp.success:
                self._cache_put(query, resp)
            return resp

        except Exception as e:
            # Анти-падение: перехватываем ВСЁ
            logger.error("WebSearchEngine critical error: %s", e, exc_info=True)
            with self._lock:
                self._stats["failures"] += 1
            return WebSearchResponse(
                success=False,
                query=query,
                error=f"Ошибка веб-поиска: {e}",
                attempts=1,
                elapsed_ms=self._elapsed_ms(start),
            )

    def fetch(self, url: str, max_length: int = 50000) -> Dict[str, Any]:
        """
        Скачать страницу и извлечь текст.

        Поддерживает автодетект кодировки (UTF-8, KOI8-R, Windows-1251).

        Returns:
            {"text": str, "title": str, "url": str, "success": bool, "error": str}
        """
        try:
            from lina.utils.http import http_get
            raw_bytes = http_get(
                url, timeout=self.HTTP_TIMEOUT, raw=True,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            if not raw_bytes:
                return {"text": "", "url": url, "success": False,
                        "error": "HTTP fetch failed"}

            html = self._decode_html(raw_bytes)

            title_match = re.search(r"<title[^>]*>(.*?)</title>",
                                    html, re.IGNORECASE | re.DOTALL)
            title = _strip_tags(title_match.group(1)) if title_match else ""

            # Use new readability-based parser + text cleaner
            text = _extract_text(html)
            try:
                from lina.parser.text_cleaner import clean_extracted_text
                text = clean_extracted_text(text)
            except ImportError:
                pass
            if len(text) > max_length:
                text = text[:max_length] + "..."

            return {"text": text, "title": title, "url": url, "success": True}

        except TimeoutError:
            return {"text": "", "url": url, "success": False, "error": "timeout"}
        except Exception as e:
            return {"text": "", "url": url, "success": False, "error": str(e)}

    @staticmethod
    def _decode_html(raw: bytes) -> str:
        """Автодетект кодировки HTML-страницы."""
        # 1. Ищем charset в meta или content-type в первых 2KB
        head = raw[:2048]
        head_ascii = head.decode("ascii", errors="replace")
        charset_match = re.search(
            r'charset=[\"\']?([a-zA-Z0-9_-]+)', head_ascii, re.IGNORECASE
        )
        if charset_match:
            enc = charset_match.group(1).lower().replace("_", "-")
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass

        # 2. Пробуем UTF-8 (самый частый)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass

        # 3. Пробуем KOI8-R и Windows-1251 (частые для .ru)
        for enc in ("koi8-r", "windows-1251", "cp866", "iso-8859-5"):
            try:
                decoded = raw.decode(enc)
                # Проверка: если есть кириллица — скорее всего правильная кодировка
                cyr_count = sum(1 for c in decoded[:500] if '\u0400' <= c <= '\u04ff')
                if cyr_count > 5:
                    return decoded
            except (UnicodeDecodeError, LookupError):
                continue

        # 4. Fallback: UTF-8 с replace
        return raw.decode("utf-8", errors="replace")

    # ──────────────────────────────────────────────────
    #  Классификация запроса
    # ──────────────────────────────────────────────────

    @staticmethod
    def _classify_query(query: str) -> str:
        """Определяет тип запроса: weather / currency / news / general."""
        for p in _WEATHER_PATTERNS:
            if p.search(query):
                return "weather"
        for p in _CURRENCY_PATTERNS:
            if p.search(query):
                return "currency"
        for p in _NEWS_PATTERNS:
            if p.search(query):
                return "news"
        return "general"

    # ──────────────────────────────────────────────────
    #  Погода (специальный flow)
    # ──────────────────────────────────────────────────

    def _handle_weather(self, query: str, start: float) -> WebSearchResponse:
        """Специальный flow для запросов о погоде."""
        city = self._extract_city(query)

        # Приоритет: wttr.in (JSON) → Open-Meteo → wttr.in text → web search
        weather = self._try_wttr_json(city)
        if weather:
            return WebSearchResponse(
                success=True,
                query=query,
                summary=weather.format(),
                source="wttr.in",
                elapsed_ms=self._elapsed_ms(start),
            )

        weather = self._try_open_meteo(city)
        if weather:
            return WebSearchResponse(
                success=True,
                query=query,
                summary=weather.format(),
                source="open-meteo",
                elapsed_ms=self._elapsed_ms(start),
            )

        weather = self._try_wttr_text(city)
        if weather:
            return WebSearchResponse(
                success=True,
                query=query,
                summary=weather.format(),
                source="wttr.in (text)",
                elapsed_ms=self._elapsed_ms(start),
            )

        # Fallback: общий поиск
        return self._handle_general_search(
            f"погода {city} сегодня", start
        )

    def _try_wttr_json(self, city: str) -> Optional[WeatherData]:
        """wttr.in JSON API."""
        try:
            from lina.utils.http import http_get
            url = f"https://wttr.in/{quote_plus(city)}?format=j1"
            body = http_get(url, timeout=10)
            if not body:
                return None
            data = json.loads(body)
            current = data.get("current_condition", [{}])[0]
            if not current:
                return None

            # Описание на русском если есть
            desc_list = current.get("lang_ru", [{}])
            desc = desc_list[0].get("value", "") if desc_list else ""
            if not desc:
                desc = current.get("weatherDesc", [{}])[0].get("value", "")

            return WeatherData(
                city=city,
                temperature=f"{current.get('temp_C', '?')}°C (ощущается {current.get('FeelsLikeC', '?')}°C)",
                description=desc,
                humidity=f"{current.get('humidity', '?')}%",
                wind=f"{current.get('windspeedKmph', '?')} км/ч, {current.get('winddir16Point', '')}",
                source="wttr.in",
            )
        except Exception as e:
            logger.debug("wttr.in JSON failed: %s", e)
            return None

    def _try_wttr_text(self, city: str) -> Optional[WeatherData]:
        """wttr.in текстовый формат."""
        try:
            from lina.utils.http import http_get
            url = f"https://wttr.in/{quote_plus(city)}?format=%C+%t+%h+%w&lang=ru"
            body = http_get(url, timeout=10)
            if not body or "Unknown" in body:
                return None
            text = body.strip()
            return WeatherData(
                city=city,
                raw_text=text,
                description=text,
                source="wttr.in",
            )
        except Exception:
            return None

    # WMO weather codes → описание
    _WMO_CODES = {
        0: "Ясно ☀️", 1: "Малооблачно 🌤", 2: "Облачно ⛅", 3: "Пасмурно ☁️",
        45: "Туман 🌫", 48: "Изморозь 🌫",
        51: "Морось 🌦", 53: "Морось 🌧", 55: "Сильная морось 🌧",
        61: "Дождь 🌧", 63: "Дождь 🌧", 65: "Ливень 🌧",
        71: "Снег 🌨", 73: "Снег 🌨", 75: "Сильный снег ❄️",
        77: "Снежная крупа ❄️",
        80: "Ливневый дождь 🌧", 81: "Ливень 🌧", 82: "Сильный ливень ⛈",
        85: "Снегопад 🌨", 86: "Сильный снегопад ❄️",
        95: "Гроза ⛈", 96: "Гроза с градом ⛈", 99: "Гроза с градом ⛈",
    }

    def _try_open_meteo(self, city: str) -> Optional[WeatherData]:
        """Open-Meteo API (бесплатный, без ключа)."""
        # Пробуем оригинальное имя, затем транслитерацию
        city_variants = [city]
        latin = self._transliterate(city)
        if latin != city:
            city_variants.append(latin)

        for city_try in city_variants:
            try:
                from lina.utils.http import http_get
                geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={quote_plus(city_try)}&count=1&language=ru"
                geo_body = http_get(geo_url, timeout=8)
                if not geo_body:
                    continue
                geo = json.loads(geo_body)
                results = geo.get("results")
                if not results:
                    continue
                loc = results[0]
                lat = loc["latitude"]
                lon = loc["longitude"]
                # Используем запрошенное имя (может быть точнее чем в API)
                city_name = city

                # Текущая погода
                wx_url = (
                    f"https://api.open-meteo.com/v1/forecast?"
                    f"latitude={lat}&longitude={lon}"
                    f"&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
                    f"wind_speed_10m,weather_code&timezone=auto"
                )
                wx_body = http_get(wx_url, timeout=8)
                if not wx_body:
                    continue
                data = json.loads(wx_body)
                current = data.get("current", {})
                if not current:
                    continue

                temp = current.get("temperature_2m", "?")
                feels = current.get("apparent_temperature", "?")
                humidity = current.get("relative_humidity_2m", "?")
                wind = current.get("wind_speed_10m", "?")
                wmo_code = current.get("weather_code", -1)
                desc = self._WMO_CODES.get(wmo_code, f"Код {wmo_code}")

                return WeatherData(
                    city=city_name,
                    temperature=f"{temp}°C (ощущается {feels}°C)",
                    description=desc,
                    humidity=f"{humidity}%",
                    wind=f"{wind} км/ч",
                    source="open-meteo.com",
                )
            except Exception as e:
                logger.debug("Open-Meteo failed for %s: %s", city_try, e)
                continue
        return None

    @staticmethod
    def _transliterate(text: str) -> str:
        """Транслитерация русского текста в латиницу."""
        _TR = {
            "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
            "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
            "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
            "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
            "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
            "э": "e", "ю": "yu", "я": "ya",
        }
        result = []
        for ch in text:
            lower = ch.lower()
            if lower in _TR:
                tr = _TR[lower]
                result.append(tr.upper() if ch.isupper() and tr else tr)
            else:
                result.append(ch)
        return "".join(result)

    # Русские падежные окончания городов → именительный падеж
    _CITY_CASE_MAP = {
        "москве": "Москва", "москвы": "Москва",
        "петербурге": "Санкт-Петербург", "питере": "Санкт-Петербург",
        "перми": "Пермь",
        "казани": "Казань",
        "новосибирске": "Новосибирск", "новосибирска": "Новосибирск",
        "екатеринбурге": "Екатеринбург", "екатеринбурга": "Екатеринбург",
        "самаре": "Самара", "самары": "Самара",
        "челябинске": "Челябинск",
        "омске": "Омск",
        "ростове": "Ростов-на-Дону",
        "уфе": "Уфа", "уфы": "Уфа",
        "красноярске": "Красноярск",
        "воронеже": "Воронеж",
        "нижнем новгороде": "Нижний Новгород",
        "краснодаре": "Краснодар",
        "сочи": "Сочи",
        "владивостоке": "Владивосток",
        "хабаровске": "Хабаровск",
        "иркутске": "Иркутск",
        "тюмени": "Тюмень",
        "саратове": "Саратов",
        "барнауле": "Барнаул",
        "ижевске": "Ижевск",
        "ульяновске": "Ульяновск",
        "томске": "Томск",
        "кирове": "Киров",
    }

    @staticmethod
    def _extract_city(query: str) -> str:
        """Извлекает город из запроса о погоде."""
        m = _CITY_EXTRACT.search(query)
        if m:
            city = m.group(1).strip()
            # Убираем лишние слова
            for remove in ("сегодня", "завтра", "сейчас", "today", "now"):
                city = city.replace(remove, "").strip()
            if not city:
                return "Moscow"
            # Нормализация русских падежей
            city_lower = city.lower()
            for case_form, nominative in WebSearchEngine._CITY_CASE_MAP.items():
                if city_lower == case_form:
                    return nominative
            # Общая эвристика: убираем типичные окончания падежей
            if city_lower.endswith(("е", "и", "ы", "у", "ой")):
                # Оставляем как есть — геокодер разберётся
                pass
            return city
        return "Moscow"

    # ──────────────────────────────────────────────────
    #  Курс валют (специальный flow)
    # ──────────────────────────────────────────────────

    def _handle_currency(self, query: str, start: float) -> WebSearchResponse:
        """Специальный flow для курса валют."""
        # Пробуем exchangerate API
        result = self._try_exchange_rate(query)
        if result:
            return WebSearchResponse(
                success=True,
                query=query,
                summary=result,
                source="exchangerate",
                elapsed_ms=self._elapsed_ms(start),
            )
        # Fallback к общему поиску
        return self._handle_general_search(query, start)

    def _try_exchange_rate(self, query: str) -> Optional[str]:
        """Бесплатный API курса валют."""
        try:
            # Определяем хотя бы пару
            pairs = {
                "доллар": ("USD", "RUB"),
                "евро": ("EUR", "RUB"),
                "юань": ("CNY", "RUB"),
                "фунт": ("GBP", "RUB"),
                "биткоин": ("BTC", "USD"),
                "usd": ("USD", "RUB"),
                "eur": ("EUR", "RUB"),
            }
            from_cur, to_cur = "USD", "RUB"
            q_lower = query.lower()
            for key, (fc, tc) in pairs.items():
                if key in q_lower:
                    from_cur, to_cur = fc, tc
                    break

            from lina.utils.http import http_get
            url = f"https://open.er-api.com/v6/latest/{from_cur}"
            body = http_get(url, timeout=10)
            if not body:
                return None
            data = json.loads(body)
            if data.get("result") != "success":
                return None
            rates = data.get("rates", {})
            rate = rates.get(to_cur)
            if rate is None:
                return None
            return f"💱 Курс {from_cur}/{to_cur}: {rate:.2f}\n📅 Дата: {data.get('time_last_update_utc', 'N/A')}"
        except Exception as e:
            logger.debug("Exchange rate API failed: %s", e)
            return None

    # ──────────────────────────────────────────────────
    #  Общий поиск с retry + fallback + parallel
    # ──────────────────────────────────────────────────

    def _handle_general_search(self, query: str, start: float) -> WebSearchResponse:
        """Общий поиск с параллельным запуском быстрых бэкендов + sequential fallback."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Очистить запрос от командных префиксов
        search_query = self._clean_search_query(query)
        # Fix common typos before sending to search engines
        search_query = _fix_common_typos(search_query)
        logger.debug("Search query cleaned: '%s' → '%s'", query[:60], search_query[:60])

        # ── Оптимизация запроса ──
        search_queries = [search_query]
        try:
            from lina.core.query_optimizer import get_query_optimizer
            from lina.core.entity_parser import get_entity_parser
            parser = get_entity_parser()
            parsed = parser.parse(query)
            optimizer = get_query_optimizer()
            alt_queries = optimizer.optimize(
                search_query,
                device=parsed.device,
                attribute=parsed.attribute,
            )
            # Основной + альтернативный (если отличается)
            for aq in alt_queries:
                if aq and aq.lower() != search_query.lower() and aq not in search_queries:
                    search_queries.append(aq)

            # ── Для спек-запросов: добавить вариант с gsmarena ──
            # GSMArena — лучший источник спецификаций телефонов.
            # Если запрос содержит "характеристики" или "specs" — добавляем site: запрос.
            _is_spec_q = any(w in search_query.lower()
                             for w in ("характеристик", "спецификац", "specs"))
            if _is_spec_q and parsed.device:
                gsmarena_q = f"{parsed.device} site:gsmarena.com"
                if gsmarena_q not in search_queries:
                    search_queries.insert(1, gsmarena_q)  # высокий приоритет
        except Exception as e:
            logger.debug("Query optimizer skipped: %s", e)

        # ── Detect factual queries: skip Phase 2 if Phase 1 returns anything ──
        _factual_fast = bool(re.search(
            r'сколько\b|чем\s+отлича|в\s+ч[её]м\s+разниц|разница\s+между|'
            r'что\s+такое|кто\s+такой|что\s+лучше|'
            r'кто\s+(?:написал|изобрёл|создал|открыл|первый|основал)|'
            r'в\s+каком\s+году|'
            r'как(?:ая|ой|ое)\s+(?:температур|столиц|площадь|валют|населен'
            r'|химическ|самый|самая|самое)\b|'
            r'что\s+нового|нововведен|'
            r'(?:топ|лучшие?|рейтинг)[\s\-]+\d|лучшие?\s+\w|'
            r'как\s+(?:установить|настроить|обновить|удалить|включить|выключить)|'
            r'сравни\b|\bvs\b',
            query, re.IGNORECASE,
        ))

        # ── Phase 1: Параллельный запуск быстрых бэкендов ──
        # DDG BS4 is the most reliable engine (uses requests Session with
        # connection pooling through SOCKS proxy).  Other DDG engines use
        # lina.utils.http which has separate timeout handling.
        # Limit parallel engines to reduce SOCKS proxy load and avoid DDG
        # rate-limiting (HTTP 202).
        parallel_engines = [
            ("ddg_bs4", self._search_duckduckgo_bs4),
            ("ecosia", self._search_ecosia),
            ("brave", self._search_brave),
        ]

        attempts = 0
        best_result = None
        best_score = 0.0

        def _try_engine(name, fn, sq):
            """Попытка поиска в одном движке (для ThreadPoolExecutor)."""
            try:
                results = fn(sq)
                return name, results
            except Exception as e:
                logger.debug("Parallel engine %s failed: %s", name, e)
                return name, []

        # Запускаем параллельно для основного запроса + альтернативных
        with ThreadPoolExecutor(max_workers=min(4, len(parallel_engines))) as pool:
            futures = {}
            for name, fn in parallel_engines:
                # Only use primary query to avoid DDG rate-limiting
                f = pool.submit(_try_engine, name, fn, search_queries[0])
                futures[f] = name
            try:
                for future in as_completed(futures, timeout=self.HTTP_TIMEOUT + 8):
                    attempts += 1
                    try:
                        engine_name, results = future.result(timeout=2)
                        if results:
                            ranked = self._rank_results(query, results)
                            score = max((r.relevance for r in ranked), default=0)
                            has_keyword_match = self._results_match_query(
                                search_queries[0], ranked,
                            )
                            if not has_keyword_match:
                                logger.debug(
                                    "Parallel %s: no keyword match, skipping",
                                    engine_name)
                                continue
                            if score < 0.10 and len(ranked) > 2:
                                logger.debug(
                                    "Parallel %s: best_score=%.2f — irrelevant",
                                    engine_name, score)
                                continue
                            # Выбрать лучший результат
                            if score > best_score:
                                best_score = score
                                best_result = (engine_name, ranked)
                                # ── Early exit: good enough result → stop waiting ──
                                if score >= 0.25 and len(ranked) >= 3:
                                    logger.debug(
                                        "Parallel %s: score=%.2f, %d results — "
                                        "early exit from parallel phase",
                                        engine_name, score, len(ranked))
                                    # Cancel remaining futures
                                    for f in futures:
                                        f.cancel()
                                    break
                    except Exception as e:
                        logger.debug("Parallel future error: %s", e)
            except TimeoutError:
                logger.debug("Parallel search phase timed out, %d futures unfinished",
                             sum(1 for f in futures if not f.done()))

        # ── Phase 1.5: Retry DDG with alt query if Phase 1 failed ──
        if not best_result and len(search_queries) > 1:
            logger.debug("Phase 1 failed, retrying ddg_bs4 with alt query: %s",
                         search_queries[1][:50])
            try:
                alt_results = self._search_duckduckgo_bs4(search_queries[1])
                if alt_results:
                    ranked = self._rank_results(query, alt_results)
                    score = max((r.relevance for r in ranked), default=0)
                    has_kw = self._results_match_query(search_queries[0], ranked)
                    if has_kw and score >= 0.10:
                        best_result = ("ddg_bs4", ranked)
                        best_score = score
            except Exception as e:
                logger.debug("Phase 1.5 ddg_bs4 alt failed: %s", e)

        # ── Phase 1.6: Try Ecosia with alt query if DDG is rate-limited ──
        if not best_result:
            logger.debug("Phase 1.5 failed, trying Ecosia as emergency fallback")
            _ecosia_q = search_queries[1] if len(search_queries) > 1 else search_queries[0]
            try:
                ecosia_results = self._search_ecosia(_ecosia_q)
                if ecosia_results:
                    ranked = self._rank_results(query, ecosia_results)
                    score = max((r.relevance for r in ranked), default=0)
                    has_kw = self._results_match_query(search_queries[0], ranked)
                    if has_kw and score >= 0.10:
                        best_result = ("ecosia", ranked)
                        best_score = score
                    elif ecosia_results:
                        # Even if keyword match fails, Ecosia results are
                        # better than nothing for niche products
                        ranked = self._rank_results(query, ecosia_results)
                        best_result = ("ecosia", ranked)
                        best_score = max((r.relevance for r in ranked), default=0)
                        logger.debug(
                            "Phase 1.6: Ecosia weak match (score=%.2f) "
                            "— using anyway (better than 0 results)",
                            best_score,
                        )
            except Exception as e:
                logger.debug("Phase 1.6 Ecosia failed: %s", e)

        # Если параллельный поиск дал результат — используем
        if best_result:
            engine_name, ranked = best_result
            summary = self._fetch_and_summarize(query, ranked)
            if self._validate_response(summary):
                with self._lock:
                    self._stats["successes"] += 1
                return WebSearchResponse(
                    success=True,
                    query=query,
                    results=ranked,
                    summary=summary,
                    source=engine_name,
                    attempts=attempts,
                    elapsed_ms=self._elapsed_ms(start),
                )

        # ── Phase 2: Sequential fallback (DDG alt queries + Ecosia + SearXNG + Wikipedia) ──
        # For factual queries, snippets are enough — skip expensive Phase 2
        if _factual_fast:
            logger.info(
                "Factual query: skipping Phase 2 sequential fallback"
            )
            return WebSearchResponse(
                success=False,
                query=query,
                results=[],
                summary="",
                source="none",
                attempts=attempts,
                elapsed_ms=self._elapsed_ms(start),
            )
        fallback_engines = [
            ("ddg_bs4", self._search_duckduckgo_bs4),
            ("ecosia", self._search_ecosia),
            ("searxng", self._search_searxng),
            ("wikipedia", self._search_wikipedia),
        ]

        for sq in search_queries:
            for engine_name, engine_fn in fallback_engines:
                for retry in range(self.MAX_RETRIES):
                    attempts += 1
                    try:
                        self._rate_wait(engine_name)
                        results = engine_fn(sq)
                        if results:
                            ranked = self._rank_results(query, results)
                            best = max((r.relevance for r in ranked), default=0)
                            # Keyword match takes priority over score threshold
                            has_kw = self._results_match_query(sq, ranked)
                            if not has_kw:
                                logger.debug(
                                    "Engine %s: results don't contain query keywords, "
                                    "skipping to next engine",
                                    engine_name)
                                break
                            if best < 0.10 and len(ranked) > 2:
                                logger.debug(
                                    "Engine %s returned %d results but best_score=%.2f — "
                                    "results look irrelevant, trying next engine",
                                    engine_name, len(ranked), best)
                                break
                            summary = self._fetch_and_summarize(query, ranked)
                            if self._validate_response(summary):
                                with self._lock:
                                    self._stats["successes"] += 1
                                return WebSearchResponse(
                                    success=True,
                                    query=query,
                                    results=ranked,
                                    summary=summary,
                                    source=engine_name,
                                    attempts=attempts,
                                    elapsed_ms=self._elapsed_ms(start),
                                )
                    except Exception as e:
                        logger.debug("Search engine %s retry %d failed: %s",
                                     engine_name, retry + 1, e)
                        with self._lock:
                            self._stats["retries"] += 1

                with self._lock:
                    self._stats["fallbacks"] += 1

        # Всё не работает
        with self._lock:
            self._stats["failures"] += 1
        return WebSearchResponse(
            success=False,
            query=query,
            error="Не удалось получить результаты поиска после всех попыток.",
            attempts=attempts,
            elapsed_ms=self._elapsed_ms(start),
        )

    # ──────────────────────────────────────────────────
    #  Поисковые движки
    # ──────────────────────────────────────────────────

    def _search_brave(self, query: str) -> List[SearchResult]:
        """Brave Search (основной). Парсит HTML страницу результатов."""
        from lina.utils.http import http_get
        try:
            url = f"https://search.brave.com/search?q={quote_plus(query)}&source=web"
            html = http_get(
                url, timeout=self.HTTP_TIMEOUT,
                headers={
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            if not html:
                return []
        except Exception as e:
            logger.debug("Brave Search fetch failed: %s", e)
            return []

        return self._parse_brave_html(html)

    @staticmethod
    def _parse_brave_html(html: str) -> List[SearchResult]:
        """Парсит HTML страницу Brave Search."""
        results: List[SearchResult] = []
        seen_urls: set = set()

        # Brave results: <a href="URL" ...>TITLE</a> followed by snippet
        for m in re.finditer(
            r'<a[^>]*href="(https?://(?!brave\.com|bing\.com|search\.brave)[^"]+)"'
            r'[^>]*>(.*?)</a>',
            html, re.DOTALL,
        ):
            href = m.group(1).split("&amp;")[0]
            raw_title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not raw_title or len(raw_title) < 8:
                continue
            if href in seen_urls:
                continue

            # Skip non-content URLs
            if any(skip in href for skip in [
                "youtube.com/channel", "google.com/", "bing.com/",
                "brave.com/", "/ads/", "/login",
            ]):
                continue

            # Attempt to grab snippet from nearby HTML
            start_pos = m.end()
            chunk = html[start_pos:start_pos + 800]
            snippet = ""
            snip_match = re.search(
                r'<p[^>]*class="[^"]*snippet-description[^"]*"[^>]*>(.*?)</p>',
                chunk, re.DOTALL,
            )
            if snip_match:
                snippet = re.sub(r"<[^>]+>", "", snip_match.group(1)).strip()[:300]

            seen_urls.add(href)
            results.append(SearchResult(
                title=raw_title[:120],
                url=href,
                snippet=snippet,
            ))
            if len(results) >= 10:
                break

        return results

    def _search_ddgs_library(self, query: str) -> List[SearchResult]:
        """DuckDuckGo через Python-библиотеку duckduckgo-search (надёжный fallback)."""
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.debug("duckduckgo-search not installed, skipping")
            return []

        try:
            # DDGS.__init__ делает warnings.simplefilter("always") перед warn(),
            # поэтому catch_warnings / filterwarnings не помогают.
            # Подавляем назойливое предупреждение монкипатчем.
            _orig_warn = warnings.warn
            def _quiet_warn(msg, *a, **kw):
                if "renamed" in str(msg) and "ddgs" in str(msg):
                    return
                _orig_warn(msg, *a, **kw)
            warnings.warn = _quiet_warn
            try:
                ddgs = DDGS()
            finally:
                warnings.warn = _orig_warn
            raw = ddgs.text(query, region="ru-ru", max_results=8)
            # If ru-ru gave no results, retry with wt-wt (worldwide)
            if not raw:
                raw = ddgs.text(query, region="wt-wt", max_results=8)
            results = []
            _JUNK_DOMAINS = {"zhihu.com", "baidu.com", "bilibili.com", "weibo.com",
                             "csdn.net", "jianshu.com", "163.com", "qq.com",
                             "otvet.mail.ru", "answers.yahoo.com", "quora.com",
                             "otvet.expert", "bolshoyvopros.ru", "sprashivalka.com",
                             "touch.otvet.mail.ru",
                             # CN-community & forums that rarely have useful specs
                             "realmebbs.com", "realmecommunity.com",
                             "bbs.xiaomi.cn", "club.huawei.com",
                             # Social media — not useful for factual queries
                             "facebook.com", "upload.facebook.com",
                             "instagram.com", "twitter.com", "x.com",
                             "zhidao.baidu.com"}
            for item in raw:
                url = item.get("href", "")
                # Skip Chinese spam domains
                if any(junk in url for junk in _JUNK_DOMAINS):
                    continue
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=url,
                    snippet=item.get("body", "")[:300],
                ))
            if not results:
                logger.debug("DDGS library: all results filtered as irrelevant")
            return results
        except Exception as e:
            logger.debug("DDGS library search failed: %s", e)
            return []

    def _search_duckduckgo(self, query: str) -> List[SearchResult]:
        """DuckDuckGo Lite (основной). POST-метод обходит CAPTCHA."""
        from lina.utils.http import http_post
        body = http_post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query},
            timeout=self.HTTP_TIMEOUT,
            headers={"Referer": "https://lite.duckduckgo.com/"},
        )
        if not body:
            return []
        return self._parse_ddg_lite(body)

    def _search_duckduckgo_html(self, query: str) -> List[SearchResult]:
        """DuckDuckGo HTML версия (fallback). POST-метод."""
        from lina.utils.http import http_post
        body = http_post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            timeout=self.HTTP_TIMEOUT,
            headers={
                "Referer": "https://html.duckduckgo.com/",
                "Cookie": "kl=ru-ru",
            },
        )
        if not body:
            return []
        return self._parse_ddg_html(body)

    def _search_duckduckgo_bs4(self, query: str) -> List[SearchResult]:
        """DuckDuckGo HTML через BeautifulSoup (из Parcer — самый надёжный парсер).

        Использует BeautifulSoup для парсинга результатов DuckDuckGo,
        что значительно надёжнее regex-подхода при изменениях HTML-разметки.
        Также правильно декодирует redirect-URL через extract_real_url().

        Пробует три эндпоинта:
          1. POST html.duckduckgo.com/html/ (основной, стабильный)
          2. GET duckduckgo.com/html/ (fallback)
          3. POST lite.duckduckgo.com/lite/ (последний шанс)
        """
        import time as _time
        # Skip if DDG is rate-limited (202 cooldown — 30s)
        if _time.time() < self._ddg_rate_limited_until:
            logger.debug("DDG BS4: skipping — rate-limited for %.0fs more",
                         self._ddg_rate_limited_until - _time.time())
            return []

        try:
            import requests as _requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.debug("bs4/requests not available for DDG BS4 search")
            return []

        # Reuse persistent session for connection pooling through SOCKS proxy
        if self._ddg_session is None:
            self._ddg_session = _requests.Session()
            self._ddg_session.verify = False

        _headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        # Попытка: DDG endpoints — POST html, GET html, POST lite
        # DDG может возвращать 202 при rate-limit — 202 без результатов = skip
        response = None
        _endpoints = [
            (
                "POST html.duckduckgo.com",
                "post",
                "https://html.duckduckgo.com/html/",
                {"data": {"q": query}, "headers": {**_headers, "Referer": "https://html.duckduckgo.com/"}},
            ),
            (
                "GET duckduckgo.com",
                "get",
                "https://duckduckgo.com/html/",
                {"params": {"q": query}, "headers": _headers},
            ),
            (
                "POST lite.duckduckgo.com",
                "post",
                "https://lite.duckduckgo.com/lite/",
                {"data": {"q": query}, "headers": {**_headers, "Referer": "https://lite.duckduckgo.com/"}},
            ),
        ]
        for attempt_name, method, url, kwargs in _endpoints:
            try:
                fn = getattr(self._ddg_session, method)
                resp = fn(url, timeout=self.HTTP_TIMEOUT, **kwargs)
                logger.debug("DDG BS4: %s — status %d, len %d",
                             attempt_name, resp.status_code, len(resp.text))
                if resp.status_code == 200 and len(resp.text) > 1000:
                    response = resp
                    break  # 200 — use immediately
                if resp.status_code == 202 and len(resp.text) > 1000:
                    # 202 may or may not have results — try to parse
                    from bs4 import BeautifulSoup as _BS
                    _test = _BS(resp.text, "html.parser")
                    if _test.select(".result") or _test.select("a.result-link"):
                        response = resp
                        break  # 202 but has parseable results
                    # 202 without results — try next endpoint
                    logger.debug("DDG BS4: %s returned 202 without results, trying next",
                                 attempt_name)
                    continue
            except Exception as e:
                logger.debug("DDG BS4: %s failed: %s", attempt_name, e)
                continue

        if response is None:
            # All endpoints returned 202 or failed — set cooldown
            self._ddg_rate_limited_until = _time.time() + 30
            logger.debug("DDG BS4: all endpoints rate-limited, cooldown 30s")
            return []

        _is_lite = "lite.duckduckgo.com" in (response.url or "")
        soup = BeautifulSoup(response.text, "html.parser")
        results: List[SearchResult] = []

        if _is_lite:
            # DDG Lite format: <a class="result-link"> inside table rows
            for link_tag in soup.select("a.result-link"):
                title = " ".join(link_tag.get_text().split())
                raw_href = link_tag.get("href", "")
                url = self._extract_real_ddg_url(raw_href)
                if not url or not title:
                    continue
                # Try to get snippet from next <td class="result-snippet">
                snippet = ""
                snippet_td = link_tag.find_parent("tr")
                if snippet_td:
                    next_tr = snippet_td.find_next_sibling("tr")
                    if next_tr:
                        snippet_tag = next_tr.select_one("td.result-snippet")
                        if snippet_tag:
                            snippet = " ".join(snippet_tag.get_text().split())
                results.append(SearchResult(
                    title=title[:200], url=url, snippet=snippet[:300],
                ))
                if len(results) >= 15:
                    break
        else:
            # Standard DDG HTML format
            for result_block in soup.select(".result"):
                title_tag = result_block.select_one(".result__a")
                if not title_tag:
                    continue

                title = " ".join(title_tag.get_text().split())
                raw_href = title_tag.get("href", "")
                url = self._extract_real_ddg_url(raw_href)
                if not url:
                    continue

                snippet_tag = result_block.select_one(".result__snippet")
                snippet = (
                    " ".join(snippet_tag.get_text().split())
                    if snippet_tag
                    else ""
                )

                results.append(SearchResult(
                    title=title[:200],
                    url=url,
                    snippet=snippet[:300],
                ))
                if len(results) >= 15:
                    break

        logger.debug("DDG BS4: %d results for '%s'", len(results), query[:40])
        return results

    @staticmethod
    def _extract_real_ddg_url(href: str) -> str:
        """Декодирует DuckDuckGo redirect-URL в реальный URL назначения.

        DDG оборачивает ссылки в redirect вроде:
          //duckduckgo.com/l/?uddg=https%3A%2F%2Freal-site.com&rut=...
        Эта функция извлекает реальный URL.
        """
        from urllib.parse import urlparse, parse_qs, unquote
        if not href:
            return ""
        if href.startswith("http") and "duckduckgo.com" not in href:
            return href
        if href.startswith("//"):
            href = "https:" + href
        try:
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            if "uddg" in params:
                return unquote(params["uddg"][0])
        except Exception:
            pass
        # Если это и так прямая ссылка
        if href.startswith("http") and "duckduckgo.com" not in href:
            return href
        return ""

    @staticmethod
    def _parse_ddg_lite(html: str) -> List[SearchResult]:
        """Парсит DuckDuckGo Lite результаты."""
        results = []
        # DDG use single/double quotes interchangeably
        link_pat = re.compile(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*class=["\']result-link["\'][^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        snip_pat = re.compile(
            r'<td[^>]*class=["\']result-snippet["\'][^>]*>(.*?)</td>',
            re.IGNORECASE | re.DOTALL,
        )
        links = link_pat.findall(html)
        snippets = snip_pat.findall(html)
        for i, (href, title) in enumerate(links[:10]):
            snip = ""
            if i < len(snippets):
                snip = _strip_tags(snippets[i])[:300]
            results.append(SearchResult(
                title=_strip_tags(title),
                url=href,
                snippet=snip,
            ))
        return results

    @staticmethod
    def _parse_ddg_html(html: str) -> List[SearchResult]:
        """Парсит DuckDuckGo HTML результаты."""
        results = []
        # Ссылки — DDG uses both class='...' and class="..."
        link_pat = re.compile(
            r'<a[^>]+class=["\']result__a["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        snip_pat = re.compile(
            r'<a[^>]*class=["\']result__snippet["\'][^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        links = link_pat.findall(html)
        snippets = snip_pat.findall(html)
        for i, (href, title) in enumerate(links[:10]):
            snip = ""
            if i < len(snippets):
                snip = _strip_tags(snippets[i])[:300]
            results.append(SearchResult(
                title=_strip_tags(title),
                url=href,
                snippet=snip,
            ))
        return results

    # ──────────────────────────────────────────────────
    #  SearXNG (метапоиск — публичные инстансы)
    # ──────────────────────────────────────────────────

    _SEARXNG_INSTANCES = [
        "https://search.sapti.me",
        "https://searx.be",
        "https://priv.au",
    ]

    def _search_searxng(self, query: str) -> List[SearchResult]:
        """Поиск через SearXNG публичные инстансы (fallback)."""
        from lina.utils.http import http_get
        for instance_url in self._SEARXNG_INSTANCES:
            try:
                url = f"{instance_url}/search?q={quote_plus(query)}&format=json&language=ru"
                body = http_get(
                    url, timeout=10,
                    headers={"Accept": "application/json"},
                )
                if not body:
                    continue
                data = json.loads(body)
                items = data.get("results", [])
                if not items:
                    continue
                results = []
                for item in items[:10]:
                    results.append(SearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("content", "")[:300],
                    ))
                if results:
                    return results
            except Exception as e:
                logger.debug("SearXNG %s failed: %s", instance_url, e)
                continue
        return []

    # ──────────────────────────────────────────────────
    #  Google HTML Search (fallback when DDG is blocked)
    # ──────────────────────────────────────────────────

    def _search_google_html(self, query: str) -> List[SearchResult]:
        """Google HTML search — parses organic results from google.com/search.

        Used as a fallback when DuckDuckGo is rate-limited (202).
        Sends a simple GET request without JS execution.
        """
        try:
            import requests as _requests
            from bs4 import BeautifulSoup
        except ImportError:
            return []

        _headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml",
        }

        try:
            url = "https://www.google.com/search"
            params = {"q": query, "hl": "ru", "num": "10"}
            resp = _requests.get(url, params=params, headers=_headers,
                                 timeout=self.HTTP_TIMEOUT, verify=False)
            logger.debug("Google HTML: status %d, len %d", resp.status_code,
                         len(resp.text))
            if resp.status_code != 200 or len(resp.text) < 1000:
                return []
        except Exception as e:
            logger.debug("Google HTML failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        results: List[SearchResult] = []

        # Google wraps organic results in <div class="g"> or <div data-sokoban-container>
        for g_div in soup.select("div.g"):
            # Title + URL in <a> tag
            a_tag = g_div.select_one("a[href]")
            if not a_tag:
                continue
            href = a_tag.get("href", "")
            if not href.startswith("http"):
                continue
            # Extract title from <h3>
            h3 = a_tag.select_one("h3")
            if not h3:
                continue
            title = " ".join(h3.get_text().split())

            # Snippet from <div class="VwiC3b"> or similar
            snippet = ""
            for snippet_sel in ("div.VwiC3b", "span.aCOpRe", "div.IsZvec"):
                snippet_tag = g_div.select_one(snippet_sel)
                if snippet_tag:
                    snippet = " ".join(snippet_tag.get_text().split())
                    break

            if title and href:
                results.append(SearchResult(
                    title=title[:200],
                    url=href,
                    snippet=snippet[:300],
                ))
            if len(results) >= 10:
                break

        logger.debug("Google HTML: %d results for '%s'", len(results),
                     query[:40])
        return results

    # ──────────────────────────────────────────────────
    #  Ecosia (uses Bing — reliable through SOCKS proxy)
    # ──────────────────────────────────────────────────

    def _search_ecosia(self, query: str) -> List[SearchResult]:
        """Ecosia search (Bing-powered). Reliable fallback when DDG/Brave fail.

        Ecosia returns server-rendered HTML that doesn't require JS.
        Each result lives in an <article> block with .result__body snippet.
        """
        try:
            import requests as _requests
            from bs4 import BeautifulSoup
        except ImportError:
            logger.debug("requests/bs4 not available for Ecosia")
            return []

        _headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml",
        }

        try:
            # Reuse DDG session for SOCKS connection pooling
            if self._ddg_session is None:
                self._ddg_session = _requests.Session()
                self._ddg_session.verify = False

            resp = self._ddg_session.get(
                "https://www.ecosia.org/search",
                params={"q": query},
                headers=_headers,
                timeout=self.HTTP_TIMEOUT,
            )
            logger.debug("Ecosia: status %d, len %d", resp.status_code,
                         len(resp.text))
            if resp.status_code != 200 or len(resp.text) < 2000:
                return []
        except Exception as e:
            logger.debug("Ecosia search failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        results: List[SearchResult] = []

        for article in soup.select("article"):
            # Find the main result link
            title_link = (
                article.select_one("a[data-test-id='result-title-a']")
                or article.select_one("a.result-title")
            )
            if not title_link:
                # Fallback: first <a> with external href
                for a in article.select("a[href^='http']"):
                    href = a.get("href", "")
                    if "ecosia" not in href:
                        title_link = a
                        break
            if not title_link:
                continue

            href = title_link.get("href", "")
            if not href or "ecosia" in href:
                continue

            title = " ".join(title_link.get_text().split())
            # Clean breadcrumb-style titles (Ecosia sometimes shows URL path as title)
            if title.startswith("http") or "›" in title:
                # Use text after last › as title
                parts = title.split("›")
                title_clean = parts[-1].strip()
                if len(title_clean) > 10:
                    title = title_clean

            # Snippet
            snippet_el = (
                article.select_one(".result__body")
                or article.select_one("p")
            )
            snippet = ""
            if snippet_el:
                snippet = " ".join(snippet_el.get_text().split())
                # Remove leading URL breadcrumb from snippet
                if snippet.startswith("http"):
                    idx = snippet.find("  ")
                    if idx > 0:
                        snippet = snippet[idx:].strip()

            results.append(SearchResult(
                title=title[:200],
                url=href,
                snippet=snippet[:300],
            ))
            if len(results) >= 10:
                break

        logger.debug("Ecosia: %d results for '%s'", len(results), query[:40])
        return results

    # ──────────────────────────────────────────────────
    #  SearXNG JSON API (meta-search — multiple public instances)
    # ──────────────────────────────────────────────────

    _SEARXNG_INSTANCES = [
        "https://search.sapti.me",
        "https://searx.tiekoetter.com",
        "https://search.ononoki.org",
        "https://paulgo.io",
        "https://opnxng.com",
    ]

    def _search_searxng(self, query: str) -> List[SearchResult]:
        """SearXNG meta-search via JSON API. Tries multiple public instances."""
        try:
            import requests as _requests
        except ImportError:
            return []

        if self._ddg_session is None:
            self._ddg_session = _requests.Session()
            self._ddg_session.verify = False

        _headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/html",
        }

        for instance in self._SEARXNG_INSTANCES:
            try:
                resp = self._ddg_session.get(
                    f"{instance}/search",
                    params={"q": query, "format": "json", "language": "ru"},
                    headers=_headers,
                    timeout=self.HTTP_TIMEOUT,
                )
                if resp.status_code != 200:
                    logger.debug("SearXNG %s: status %d", instance, resp.status_code)
                    continue

                data = resp.json()
                items = data.get("results", [])
                if not items:
                    continue

                results: List[SearchResult] = []
                for item in items:
                    url = item.get("url", "")
                    title = item.get("title", "")
                    snippet = item.get("content", "")
                    if url and title:
                        results.append(SearchResult(
                            title=title[:200],
                            url=url,
                            snippet=snippet[:300],
                        ))
                    if len(results) >= 10:
                        break

                if results:
                    logger.debug("SearXNG %s: %d results", instance, len(results))
                    return results
            except Exception as e:
                logger.debug("SearXNG %s failed: %s", instance, str(e)[:60])
                continue

        return []

    # ──────────────────────────────────────────────────
    #  Wikipedia API (для фактических запросов)
    # ──────────────────────────────────────────────────

    def _search_wikipedia(self, query: str) -> List[SearchResult]:
        """Поиск через Wikipedia API (последний fallback).

        Uses cleaned query (strip question words) and higher result limit.
        """
        # Strip common question prefixes to get cleaner search terms
        import re as _re
        clean = _re.sub(
            r"^(?:что\s+такое|кто\s+такой|кто\s+такая|какое?\s+самое?|"
            r"чем\s+отличается|сколько\s+(?:стоит|живут|лет|человек)|"
            r"как\s+(?:установить|настроить))\s+",
            "", query.strip(), flags=_re.IGNORECASE,
        ).strip()
        search_terms = [clean] if clean != query.strip() else []
        search_terms.insert(0, query)  # original first

        for sq in search_terms[:2]:
            for lang in ("ru", "en"):
                try:
                    encoded = quote_plus(sq)
                    from lina.utils.http import http_get
                    url = (
                        f"https://{lang}.wikipedia.org/w/api.php?"
                        f"action=query&list=search&srsearch={encoded}"
                        f"&format=json&utf8=1&srlimit=8"
                    )
                    body = http_get(url, timeout=10)
                    if not body:
                        continue
                    data = json.loads(body)
                    items = data.get("query", {}).get("search", [])
                    if not items:
                        continue
                    results = []
                    for item in items:
                        title = item.get("title", "")
                        snippet_raw = item.get("snippet", "")
                        snippet_clean = _strip_tags(snippet_raw)[:300]
                        page_url = f"https://{lang}.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"
                        results.append(SearchResult(
                            title=title,
                            url=page_url,
                            snippet=snippet_clean,
                        ))
                    if results:
                        return results
                except Exception as e:
                    logger.debug("Wikipedia %s/%s failed: %s", lang, sq[:30], e)
                    continue
        return []

    # ──────────────────────────────────────────────────
    #  Ранжирование результатов
    # ──────────────────────────────────────────────────

    def _rank_results(self, query: str, results: List[SearchResult]) -> List[SearchResult]:
        """Ранжирует результаты по релевантности."""
        q_lower = query.lower()
        # Strip punctuation from query words for accurate matching
        q_words = {
            re.sub(r'[^\w]', '', w)
            for w in q_lower.split()
        }
        q_words.discard('')

        # Exclude descriptor/filler words from overlap scoring.
        # These are Russian search qualifiers that won't appear in English
        # results but don't indicate irrelevance.
        _RANK_IGNORE = self._STOP_WORDS | {
            "полные", "полный", "подробные", "подробный", "детальные",
            "детальный", "технические", "техническ", "основные", "основной",
            "все", "всё", "общие", "общий", "полная", "подробная",
            "полностью", "подробнее", "детально",
        }
        q_content_words = q_words - _RANK_IGNORE
        # If nothing left after filtering, use original words
        if not q_content_words:
            q_content_words = q_words

        for r in results:
            score = 0.0
            text = (r.title + " " + r.snippet).lower()
            # Strip punctuation from both sides for matching
            text_words = {
                re.sub(r'[^\w]', '', w)
                for w in text.split()
            }
            text_words.discard('')
            overlap = q_content_words & text_words
            if q_content_words:
                score += len(overlap) / len(q_content_words) * 0.5
            # Запрос в заголовке
            if q_lower in r.title.lower():
                score += 0.3
            # Нет мусорных URL
            if any(bad in r.url for bad in
                   ["ads.", "tracking.", "click.", "doubleclick", "facebook.com/login"]):
                score -= 0.5
            # Штраф за нерелевантные домены (китайские, рекламные, Q&A помойки)
            if any(bad in r.url for bad in
                   ["zhihu.com", "baidu.com", "messenger.com", "facebook.com",
                    "otvet.mail.ru", "answers.yahoo.com", "bolshoyvopros.ru",
                    "otvet.expert", "sprashivalka.com", "quora.com"]):
                score -= 0.3
            # Бонус за надёжные домены
            for good in [".org", ".gov", "wikipedia", "github.com"]:
                if good in r.url:
                    score += 0.1
                    break
            # Бонус за русские технические сайты
            for ru_good in ["opennet.ru", "comss.ru", "habr.com", "losst.pro",
                            "linux.org.ru", "slo.ru", "securitylab.ru",
                            "4pda.to", "gsmarena.com", "nanoreview.net",
                            "devicespecifications.com"]:
                if ru_good in r.url:
                    score += 0.15
                    break
            # Бонус за сайты с характеристиками / обзорами железа
            for hw_good in ["e-katalog", "market.yandex", "dns-shop", "citilink",
                            "ixbt.com", "overclockers", "4pda.to", "notebookcheck",
                            "techpowerup", "tomshardware", "anandtech", "benchmark",
                            "nix.ru", "regard.ru", "mvideo.ru", "eldorado.ru"]:
                if hw_good in r.url:
                    score += 0.2
                    break
            # Бонус за страницу производителя (домен = слово из запроса)
            try:
                from urllib.parse import urlparse
                domain = urlparse(r.url).netloc.lower()
                for qw in q_content_words:
                    if len(qw) >= 3 and qw in domain:
                        score += 0.25
                        break
            except Exception:
                pass
            r.relevance = max(0.0, min(1.0, score))

        # ── Пост-фильтр: удалить Wikipedia-листы из выдачи ──
        # "Список Android-смартфонов" и прочие листовые страницы не содержат спеков конкретного устройства.
        _WIKI_LIST_TITLES = (
            "list of ", "lists of ", "список ", "comparison of ",
            "timeline of ", "index of ", "outline of ",
        )
        _WIKI_LIST_URLS = (
            "/wiki/list_of_", "/wiki/lists_of_", "/wiki/comparison_of_",
            "/wiki/timeline_of_", "/wiki/index_of_",
        )
        filtered = []
        for r in results:
            title_lower = r.title.lower()
            url_lower = r.url.lower()
            is_list = (
                any(t in title_lower for t in _WIKI_LIST_TITLES)
                or any(u in url_lower for u in _WIKI_LIST_URLS)
            )
            if is_list:
                logger.debug("Filtered list page: %s", r.url[:80])
                # Don't remove entirely — push to bottom with low score
                r.relevance = min(r.relevance, 0.05)
            filtered.append(r)

        filtered.sort(key=lambda r: r.relevance, reverse=True)
        return filtered

    # ──────────────────────────────────────────────────
    #  Скачивание и суммаризация
    # ──────────────────────────────────────────────────

    # ── Snippet richness check: skip LLM when snippets have enough data ──
    @staticmethod
    def _snippets_are_rich(query: str, results: "List[SearchResult]") -> bool:
        """Return True ONLY when snippets contain real structured data
        (spec tables, price lists, etc.) — NOT just random text with digits.

        Very conservative: false positives cause garbage answers.
        """
        snippets = [r.snippet for r in results[:5] if r.snippet]
        if len(snippets) < 3:
            return False
        combined = " ".join(snippets).lower()
        total_chars = len(combined)
        if total_chars < 600:
            return False

        # Query keywords must appear in snippets
        q_words = {w.lower() for w in query.split() if len(w) >= 3}
        found = sum(1 for w in q_words if w in combined)
        if found < max(1, len(q_words) // 2):
            return False  # snippets don't match query

        # Require spec-like patterns: "value + unit" or structured data
        import re
        _SPEC_PAT = re.compile(
            r'\d+\s*(?:мм|mm|г[рб]?|gb?|мб|mb|мач|mah|гц|hz|мп|mp|дюйм|inch|"'
            r'|пикс|px|fps|ram|rom|нм|nm|вт|w\b)',
            re.IGNORECASE,
        )
        spec_hits = len(_SPEC_PAT.findall(combined))
        # Need at least 5 spec-like measurements to trust snippets
        if spec_hits >= 5:
            return True
        return False

    def _fetch_and_summarize(self, query: str, results: List[SearchResult]) -> str:
        """Скачивает top-N страниц через lina.parser (readability-lxml + BS4)
        и собирает краткую сводку с mini-LLM суммаризацией.

        Использует WebSearchSession для:
          - Фильтрации уже посещённых URL (не скачиваем повторно)
          - Контекста разговора при follow-up запросах
          - Объединения новых данных с предыдущим ответом
        """
        if not results:
            return ""

        # Detect specs query early because this flag is used by both the
        # snippet-only fast path and the later page-download logic.
        _is_specs = bool(re.search(
            r'характ[ери]{1,5}стик|спецификац|specs?\b|обзор|review|параметр',
            query, re.IGNORECASE,
        ))

        # ── Получить сессию для отслеживания URL ──
        try:
            from lina.core.web_search_session import get_web_search_session
            session = get_web_search_session()
        except ImportError:
            session = None

        parts = [f"🔍 Результаты поиска: «{query}»\n"]

        # Добавляем сниппеты из всех результатов
        for i, r in enumerate(results[:5], 1):
            parts.append(f"  🔗 {r.title}")
            # Show domain only, not full URL with tracking params
            try:
                from urllib.parse import urlparse
                _domain = urlparse(r.url).netloc
            except Exception:
                _domain = r.url[:60]
            parts.append(f"     {_domain}")
            if r.snippet:
                parts.append(f"     {r.snippet[:300]}")
            parts.append("")

        # ── Fast path: rich snippets → skip page download + LLM ──
        # (very strict: requires spec patterns like "128 GB", "6.4 дюйм", etc.)
        # BUT: NEVER use this for "характеристики/спецификации" queries —
        # raw snippets don't have structured data for SpecExtractor,
        # and FactPipeline can't parse them → 0 facts → hard refusal.
        if not _is_specs and self._snippets_are_rich(query, results):
            logger.info("Snippets rich enough (specs detected) — skipping LLM")
            summary = "\n".join(parts)
            if len(summary) > self.MAX_SUMMARY_CHARS:
                summary = summary[:self.MAX_SUMMARY_CHARS] + "\n..."
            return summary

        # ── Fast path 2: factual/knowledge questions with decent snippets ──
        # For "сколько живут черепахи", "чем отличается X от Y", etc.
        # snippets usually contain the answer — no need for page DL + LLM.
        _FACTUAL_RE = re.compile(
            r'сколько\b|'
            r'чем\s+отлича|в\s+ч[её]м\s+разниц|разница\s+между|'
            r'кто\s+такой|что\s+лучше|'
            r'кто\s+(?:написал|изобрёл|создал|открыл|первый|основал)|'
            r'в\s+каком\s+году|'
            r'как(?:ая|ой|ое)\s+(?:температур|столиц|площадь|валют|населен'
            r'|химическ|самый|самая|самое)\b|'
            r'что\s+нового|что\s+изменилось|нововведен|'
            r'(?:топ|лучшие?|рейтинг)[\s\-]+\d|лучшие?\s+\w|'
            r'как\s+(?:установить|настроить|обновить|удалить|включить|выключить)|'
            r'какой\s+язык|каки[ех]\s+(?:языки?|стран|городов?)|'
            r'когда\s+(?:вышел|выйдет|выходит|появил|появит)|'
            r'сравни\b|\bvs\b|'
            r'как(?:ое|ой|ая)\s+сам(?:ое|ый|ая)\s+(?:больш|маленьк|быстр|медленн|'
            r'длинн|коротк|высок|низк|тяжёл|лёгк|стар|молод)',
            re.IGNORECASE,
        )
        if not _is_specs and _FACTUAL_RE.search(query):
            snippets = [r.snippet for r in results[:5] if r.snippet]
            total_snippet_chars = sum(len(s) for s in snippets)
            q_words = {w.lower() for w in query.split() if len(w) >= 3}
            combined_snip = " ".join(snippets).lower()
            hits = sum(1 for w in q_words if w in combined_snip)
            # Relaxed threshold: 80 chars + 1/3 keyword hits
            if total_snippet_chars >= 80 and hits >= max(1, len(q_words) // 3):
                logger.info(
                    "Factual query fast-path: %d snippet chars, %d/%d keyword hits "
                    "— skipping page download + LLM",
                    total_snippet_chars, hits, len(q_words),
                )
                summary = "\n".join(parts)
                if len(summary) > self.MAX_SUMMARY_CHARS:
                    summary = summary[:self.MAX_SUMMARY_CHARS] + "\n..."
                return summary
            # Even with insufficient snippets, factual queries should NOT
            # fall through to slow page download + LLM (90s+ on CPU).
            # Return whatever snippets we have.
            logger.info(
                "Factual fast-path forced: snippets=%d chars, hits=%d/%d "
                "— insufficient but returning snippets to avoid timeout",
                total_snippet_chars, hits, len(q_words),
            )
            summary = "\n".join(parts)
            if len(summary) > self.MAX_SUMMARY_CHARS:
                summary = summary[:self.MAX_SUMMARY_CHARS] + "\n..."
            return summary

        # Скачиваем top страницы для более глубокого контента
        _SKIP_DOMAINS = {
            "youtube.com", "youtu.be", "vimeo.com", "tiktok.com",
            "instagram.com", "twitter.com", "x.com", "facebook.com",
            "reddit.com", "4pda.to",
        }
        # Дополнительная фильтрация для спек-запросов:
        # магазины/маркетплейсы обычно не содержат полных тех характеристик,
        # НО страницы /specification, /characteristics, /properties — содержат.
        _SPECS_PAGE_SIGNALS = (
            "/specification", "/characteristics", "/properties",
            "/harakteristiki", "/specs", "/tech-specs",
        )
        _MARKETPLACE_DOMAINS = {
            "avito.ru", "avito.com", "rozetka.com.ua", "wildberries.ru",
            "ozon.ru", "aliexpress.ru", "aliexpress.com",
            "amazon.com", "ebay.com", "flipkart.com", "jd.com",
            "kupivip.ru", "tmall.ru", "lamoda.ru", "goods.ru",
        }
        # DNS/MVideo/Citilink/etc have dedicated specs pages — allow them
        _RETAILER_WITH_SPECS = {
            "dns-shop.ru", "mvideo.ru", "eldorado.ru", "citilink.ru",
            "market.yandex.ru", "e-katalog.ru",
        }
        if _is_specs:
            _SKIP_DOMAINS = _SKIP_DOMAINS | _MARKETPLACE_DOMAINS
        # ── For specs queries: block Wikipedia list/disambiguation pages ──
        # They contain specs for MANY devices, confusing SpecExtractor.
        _WIKI_LIST_SIGNALS = [
            "список_устройств", "list_of_", "lists_of_", "список устройств",
            "сравнение", "comparison_of_", "протокол", "protocol",
            "timeline_of_", "index_of_", "outline_of_",
            # Catch title-level signals too
            "list of ", "lists of ", "comparison of ",
        ]
        # ── URL relevance filter: only download pages related to the query ──
        # Extract content words (skip stop words like "характеристики", "обзор")
        _DL_STOP = {
            "характеристики", "характеристик", "спецификации", "спецификация",
            "обзор", "review", "specs", "specifications", "параметры",
            "подробные", "полные", "технические", "основные",
        }
        q_words_lower = {w.lower() for w in query.split() if len(w) >= 2}
        q_content_words = {w for w in q_words_lower if w not in _DL_STOP}
        # Minimum match: ≥2 content words if available, else ≥1
        min_match = min(2, len(q_content_words)) if q_content_words else 1
        check_words = q_content_words or q_words_lower

        fetchable_urls = []
        _url_limit = 5 if _is_specs else 3
        for r in results:
            if r.relevance < 0.1:
                continue
            try:
                from urllib.parse import urlparse
                parsed = urlparse(r.url)
                domain = parsed.netloc.lower().lstrip("www.")
                url_path_lower = (parsed.path + "?" + parsed.query).lower()
                # Check skip domains — but allow retailer spec pages through
                if any(skip in domain for skip in _SKIP_DOMAINS):
                    continue
                if _is_specs and any(ret in domain for ret in _RETAILER_WITH_SPECS):
                    # Only allow if URL is actually a specs/characteristics page
                    if not any(sig in url_path_lower for sig in _SPECS_PAGE_SIGNALS):
                        logger.debug(
                            "Skipping retailer non-specs page: %s", r.url)
                        continue
                # Block Wikipedia list/disambiguation pages (for ALL queries)
                if "wikipedia.org" in domain:
                    title_l = (r.title or "").lower()
                    if any(sig in url_path_lower or sig in title_l
                           for sig in _WIKI_LIST_SIGNALS):
                        logger.debug(
                            "Skipping Wikipedia list page: %s", r.url)
                        continue
            except Exception:
                pass
            # Check title/snippet relevance — require enough content words
            # Use word-boundary matching to avoid "pro" matching "протоколы"
            combined_text = (r.title + " " + (r.snippet or "")).lower()
            import re as _re_url
            def _fuzzy_model(w):
                """'bh470' → 'bh[\\s\\-_]?470' for matching 'BH 470'."""
                _m = _re_url.match(r'^([a-z]+)(\d+)$', w)
                if _m:
                    return _re_url.escape(_m.group(1)) + r'[\s\-_]?' + _re_url.escape(_m.group(2))
                return None
            matched = sum(
                1 for w in check_words
                if (_re_url.search(r'(?<!\w)' + _re_url.escape(w) + r'(?!\w)', combined_text)
                    or (_fuzzy_model(w)
                        and _re_url.search(_fuzzy_model(w), combined_text)))
            )
            if matched < min_match:
                logger.debug(
                    "Skipping URL (only %d/%d content words matched): %s",
                    matched, min_match, r.url,
                )
                continue
            fetchable_urls.append(r.url)
            if len(fetchable_urls) >= _url_limit:
                break

        # For specs queries, prioritize URLs with spec signals in path
        # so that structured /specification pages are downloaded first
        if _is_specs and len(fetchable_urls) > 1:
            def _spec_priority(url):
                _lp = url.lower()
                return 0 if any(s in _lp for s in _SPECS_PAGE_SIGNALS) else 1
            fetchable_urls.sort(key=_spec_priority)

        # ── Retry with relaxed filter for specs queries ──
        # If the strict filter yields < 2 URLs for a specs query, retry
        # with min_match=1 so we don't miss niche devices.
        if _is_specs and len(fetchable_urls) < 2:
            for r in results:
                if r.url in fetchable_urls or r.relevance < 0.1:
                    continue
                try:
                    from urllib.parse import urlparse
                    _p = urlparse(r.url)
                    domain = _p.netloc.lower().lstrip("www.")
                    _path = _p.path.lower()
                    if any(skip in domain for skip in _SKIP_DOMAINS):
                        continue
                    if _is_specs and any(ret in domain for ret in _RETAILER_WITH_SPECS):
                        if not any(sig in _path for sig in _SPECS_PAGE_SIGNALS):
                            continue
                except Exception:
                    pass
                combined_text = (r.title + " " + (r.snippet or "")).lower()
                import re as _re_url2
                matched2 = sum(
                    1 for w in check_words
                    if _re_url2.search(r'(?<!\w)' + _re_url2.escape(w) + r'(?!\w)', combined_text)
                )
                if matched2 >= 1:  # relaxed: just 1 word match
                    fetchable_urls.append(r.url)
                    logger.debug(
                        "Relaxed filter: adding URL (%d/%d words): %s",
                        matched2, min_match, r.url,
                    )
                if len(fetchable_urls) >= _url_limit:
                    break

        # Фильтруем уже посещённые URL через сессию
        if session:
            fresh_urls = session.filter_new_urls(fetchable_urls)
            if fresh_urls:
                logger.debug(
                    "Session URL filter: %d→%d URL (пропущено %d уже посещённых)",
                    len(fetchable_urls), len(fresh_urls),
                    len(fetchable_urls) - len(fresh_urls),
                )
                fetchable_urls = fresh_urls

        # ── Parallel download + readability extraction via lina.parser ──
        try:
            from lina.parser.page_parser import collect_pages_text
            # _is_specs already detected above (before URL filtering)

            # ── Direct spec site URLs: bypass search engines for specs ──
            # For device specs queries, construct direct URLs for known
            # spec sites (nanoreview.net, devicespecifications.com, etc.)
            # These have structured, reliable data that the regex extractor
            # handles perfectly. Prepend to fetchable_urls for priority.
            if _is_specs:
                _direct_urls = _generate_spec_site_urls(query)
                if _direct_urls:
                    # Deduplicate against existing URLs
                    existing = set(fetchable_urls)
                    new_direct = [u for u in _direct_urls if u not in existing]
                    if new_direct:
                        logger.info(
                            "Adding %d direct spec site URLs: %s",
                            len(new_direct),
                            ", ".join(new_direct[:3]),
                        )
                        # Prepend direct URLs (highest priority)
                        fetchable_urls = new_direct + fetchable_urls
                        # Allow more downloads for specs
                        fetchable_urls = fetchable_urls[:5]
            _ppc = 2500 if _is_specs else 600
            _tlim = 6000 if _is_specs else 2000
            combined_text, source_urls = collect_pages_text(
                fetchable_urls,
                per_page_chars=_ppc,
                total_limit=_tlim,
            )

            # ── Retry with remaining URLs if first batch all failed ──
            if not combined_text and _is_specs:
                _tried = set(fetchable_urls)
                _backup_urls = []
                for r in results:
                    if r.url in _tried or r.relevance < 0.1:
                        continue
                    try:
                        from urllib.parse import urlparse
                        _bp = urlparse(r.url)
                        _bd = _bp.netloc.lower().lstrip("www.")
                        if any(s in _bd for s in _SKIP_DOMAINS):
                            continue
                        if any(s in _bd for s in _MARKETPLACE_DOMAINS):
                            continue
                    except Exception:
                        pass
                    _backup_urls.append(r.url)
                    if len(_backup_urls) >= 3:
                        break
                if _backup_urls:
                    logger.info(
                        "First batch 0 pages — retrying with %d backup URLs",
                        len(_backup_urls),
                    )
                    combined_text, source_urls = collect_pages_text(
                        _backup_urls,
                        per_page_chars=_ppc,
                        total_limit=_tlim,
                    )

            # Пометить скачанные URL в сессии
            if session and source_urls:
                session.mark_urls(source_urls)

            # ── Snippet fallback: when all pages fail to download ──
            # Use DDG snippet/title text instead. Russian retailers often
            # block SOCKS proxies (401/429/JS), but their DDG snippets
            # contain useful spec data like "AMD Ryzen 7, 16GB, 512GB SSD".
            _using_snippets = False
            if not combined_text and _is_specs and results:
                _snippet_parts = []
                for r in results[:8]:
                    _st = (r.title or "") + "\n" + (r.snippet or "")
                    _st = _st.strip()
                    if _st:
                        _snippet_parts.append(_st)
                if _snippet_parts:
                    combined_text = "\n\n".join(_snippet_parts)
                    source_urls = [r.url for r in results[:8] if r.url]
                    _using_snippets = True
                    logger.info(
                        "All pages failed — using %d DDG snippets as text "
                        "(%d chars)", len(_snippet_parts), len(combined_text),
                    )

            if combined_text:
                # ── SNIPPET SPECS FAST PATH ──
                # When all page downloads failed and we're using DDG snippets
                # for a specs query, skip mini-LLM (0.5B can't summarize
                # short snippets well). Format snippet data directly from
                # DDG result objects.
                if _using_snippets and _is_specs:
                    logger.info(
                        "Snippet specs fast path: formatting DDG snippet data "
                        "directly (skipping mini-LLM)",
                    )
                    _subject = re.sub(
                        r'\b(?:х?[ао]?р[аеи]?к?т[еёр]{1,3}[иеэ]стик\w*'
                        r'|характеристик\w*|спецификац\w*|specs?'
                        r'|обзор|review|параметр\w*'
                        r'|найди|поищи|загугли|нагугли|выясни|узнай'
                        r'|в\s+интернете|в\s+сети|в\s+инете)\b',
                        '', query, flags=re.IGNORECASE,
                    ).strip()
                    _subject = re.sub(r'\s+', ' ', _subject)

                    # Build formatted snippet entries from result objects
                    # Filter: skip junk lines from snippets (forum noise,
                    # "мобильная версия", CTA text, etc.)
                    _JUNK_RE = re.compile(
                        r'мобильная версия|текстовая версия|сейчас:|'
                        r'купить|недорого|доставк|акции|скидки|'
                        r'⭐|инструкц|руководство|эксплуатац|'
                        r'можете скачать|ознакомит',
                        re.IGNORECASE,
                    )
                    _seen = set()
                    _entries = []
                    for r in results[:10]:
                        sn = (r.snippet or "").strip()
                        if not sn or len(sn) < 30:
                            continue
                        # Skip junk snippets
                        if _JUNK_RE.search(sn):
                            continue
                        # Deduplicate by first 50 chars
                        _k = sn[:50].lower()
                        if _k in _seen:
                            continue
                        _seen.add(_k)
                        _entries.append(sn)

                    if _entries:
                        parts.append(
                            f"📱 Характеристики {_subject} "
                            f"(по данным поисковой выдачи):\n"
                        )
                        for entry in _entries[:8]:
                            parts.append(f"  • {entry[:500]}")
                        parts.append("")
                    else:
                        # All snippets were junk — just show titles
                        parts.append(
                            f"📱 {_subject} — найдены результаты, но "
                            f"страницы недоступны через прокси.\n"
                        )
                # ── SPECS FAST PATH: regex-based extraction, NO LLM ──
                # For specs/characteristics queries, try direct extraction
                # from structured page text. This is 100x faster and
                # guaranteed accurate (no hallucinations).
                elif _is_specs:
                    try:
                        from lina.parser.spec_extractor import extract_specs
                        # Extract subject name from query
                        _subject = re.sub(
                            r'\b(?:х?[ао]?р[аеи]?к?т[еёр]{1,3}[иеэ]стик\w*'
                            r'|характеристик\w*|спецификац\w*|specs?'
                            r'|обзор|review|подробн\w*|полн\w*|техническ\w*'
                            r'|параметр\w*'
                            r'|найди|поищи|загугли|нагугли|выясни|узнай'
                            r'|в\s+интернете|в\s+сети|в\s+инете)\b',
                            '', query, flags=re.IGNORECASE,
                        ).strip()
                        _subject = re.sub(r'\s+', ' ', _subject)
                        device_specs = extract_specs(
                            combined_text,
                            device_name=_subject,
                            source_urls=source_urls,
                        )
                        if device_specs and device_specs.filled_count >= 4:
                            logger.info(
                                "SpecExtractor: %d fields, confidence=%.2f "
                                "— using direct specs (NO LLM)",
                                device_specs.filled_count,
                                device_specs.confidence,
                            )
                            # Return specs directly as [DIRECT_FACTS]
                            # so app.py bypasses main LLM too
                            spec_text = device_specs.format_for_user()
                            return (
                                f"🔍 Результаты поиска: «{query}»\n\n"
                                + spec_text
                            )
                    except Exception as e:
                        logger.debug("SpecExtractor failed: %s", e)

                    # If SpecExtractor didn't produce enough fields for a specs query,
                    # serve snippets directly — MUCH faster than LLM fallback.
                    # Replace the top-level snippet block to avoid duplication.
                    if _is_specs:
                        logger.info(
                            "SpecExtractor insufficient — using snippets for specs query (skip LLM)"
                        )
                        _snippet_handled = True
                        # Reset parts — remove redundant top snippet block
                        parts = [f"🔍 Результаты поиска: «{query}»\n"]
                        # Build a clean snippet summary
                        _snip_parts = []
                        for r in results[:8]:
                            title = getattr(r, "title", "") or ""
                            snippet = getattr(r, "snippet", "") or getattr(r, "body", "") or ""
                            url_str = getattr(r, "url", "") or getattr(r, "href", "") or ""
                            if snippet:
                                _snip_parts.append(f"  🔗 {title}")
                                _snip_parts.append(f"     {snippet[:400]}")
                                _snip_parts.append("")
                        if _snip_parts:
                            parts.append(f"📱 {_subject}:\n")
                            parts.extend(_snip_parts)
                            # Add source links at the end (domain only)
                            _src_urls = [r.url for r in results[:5] if r.url]
                            if _src_urls:
                                parts.append("📎 Источники:")
                                for _u in _src_urls[:3]:
                                    try:
                                        from urllib.parse import urlparse as _up
                                        _dom = _up(_u).netloc or _u[:60]
                                    except Exception:
                                        _dom = _u[:60]
                                    parts.append(f"  • {_dom}")
                        else:
                            parts.append(
                                f"📱 {_subject} — страницы найдены, но "
                                f"структурированные характеристики недоступны.\n"
                            )
                if not _snippet_handled:
                    _text_lower = combined_text.lower()
                    import re as _re_text
                    def _fuzzy_model_t(w):
                        _m = _re_text.match(r'^([a-z]+)(\d+)$', w)
                        if _m:
                            return _re_text.escape(_m.group(1)) + r'[\s\-_]?' + _re_text.escape(_m.group(2))
                        return None
                    _topic_hits = sum(
                        1 for w in check_words
                        if (_re_text.search(r'(?<!\w)' + _re_text.escape(w) + r'(?!\w)', _text_lower)
                            or (_fuzzy_model_t(w)
                                and _re_text.search(_fuzzy_model_t(w), _text_lower)))
                    )
                if not _snippet_handled and _topic_hits < min_match:
                    logger.info(
                        "Page text doesn't match query (%d/%d content words) "
                        "— using raw text, skip mini-LLM",
                        _topic_hits, min_match,
                    )
                    parts.append("📄 Подробности:")
                    for line in combined_text.split("\n"):
                        line = line.strip()
                        if line:
                            parts.append(f"  {line[:500]}")
                    parts.append("")
                # ── Skip mini-LLM for very short text — main LLM will handle it ──
                # But NEVER skip for specs queries — we need structured extraction
                elif not _snippet_handled and len(combined_text) < 800 and not _is_specs:
                    logger.info(
                        "Page text short (%d chars < 800) — using raw text, skip mini-LLM",
                        len(combined_text),
                    )
                    parts.append("📄 Подробности:")
                    for line in combined_text.split("\n"):
                        line = line.strip()
                        if line:
                            parts.append(f"  {line[:500]}")
                    parts.append("")
                elif not _snippet_handled:
                    # ── Определить: follow-up или новый запрос ──
                    is_followup = session and session.is_followup(query) and session.last_summary

                    # ── Mini-LLM summarisation (из Parcer/search_cli) ──
                    try:
                        if is_followup:
                            from lina.parser.web_llm import summarize_followup_web
                            context = session.get_history_text() if session else ""
                            llm_summary = summarize_followup_web(
                                query=query,
                                text=combined_text,
                                source_urls=source_urls,
                                context=context,
                                language=session.language if session else "ru",
                            )
                            # Объединить с предыдущим ответом
                            if llm_summary and session:
                                llm_summary = session.merge_with_previous(
                                    llm_summary, query,
                                )
                        else:
                            from lina.parser.web_llm import summarize_web_text
                            lang = session.language if session else "ru"
                            llm_summary = summarize_web_text(
                                query=query,
                                text=combined_text,
                                source_urls=source_urls,
                                language=lang,
                            )

                        if llm_summary and len(llm_summary.strip()) > 80:
                            logger.info(
                                "web_llm mini: суммаризация %d→%d симв.%s",
                                len(combined_text), len(llm_summary),
                                " (follow-up)" if is_followup else "",
                            )
                            parts.append("📄 Суммаризация (mini-LLM):")
                            for line in llm_summary.split("\n"):
                                line = line.strip()
                                if line:
                                    parts.append(f"  {line[:500]}")
                            parts.append("")
                        else:
                            # Mini-LLM returned nothing usable — use raw text
                            logger.info("web_llm mini: нет результата, используем сырой текст")
                            parts.append("📄 Подробности:")
                            for line in combined_text.split("\n"):
                                line = line.strip()
                                if line:
                                    parts.append(f"  {line[:500]}")
                            parts.append("")
                    except Exception as llm_err:
                        logger.info("web_llm mini недоступен (%s), используем сырой текст", llm_err)
                        parts.append("📄 Подробности:")
                        for line in combined_text.split("\n"):
                            line = line.strip()
                            if line:
                                parts.append(f"  {line[:500]}")
                        parts.append("")
        except Exception as e:
            logger.warning("lina.parser parallel fetch failed: %s — falling back", e)
            # Fallback: sequential fetch via self.fetch()
            for url in fetchable_urls[:2]:
                page = self.fetch(url, max_length=12000)
                if page["success"] and page["text"]:
                    for p in page["text"].split("\n")[:10]:
                        p = p.strip()
                        if p and len(p) > 40:
                            parts.append(f"  {p[:400]}")
                    parts.append("")

        summary = "\n".join(parts)
        if len(summary) > self.MAX_SUMMARY_CHARS:
            summary = summary[:self.MAX_SUMMARY_CHARS] + "\n..."

        return summary

    # ──────────────────────────────────────────────────
    #  Валидация ответа
    # ──────────────────────────────────────────────────

    @staticmethod
    def _validate_response(summary: str) -> bool:
        """Проверяет что ответ валидный."""
        if not summary or not summary.strip():
            return False
        if len(summary.strip()) < 20:
            return False
        # Не содержит stack traces
        if "Traceback" in summary or "Exception" in summary:
            return False
        # Не содержит только ошибки
        s_lower = summary.lower()
        if s_lower.startswith("error") or s_lower.startswith("ошибка"):
            return False
        return True

    # ──────────────────────────────────────────────────
    #  Кэш и Rate Limiter
    # ──────────────────────────────────────────────────

    def _cache_key(self, query: str) -> str:
        """Нормализованный ключ кэша."""
        return query.strip().lower()

    def _cache_get(self, query: str) -> Optional["WebSearchResponse"]:
        """Получить из кэша (если TTL не истёк)."""
        key = self._cache_key(query)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            ts, resp = entry
            if time.time() - ts > self.CACHE_TTL:
                del self._cache[key]
                return None
            return resp

    def _cache_put(self, query: str, resp: "WebSearchResponse") -> None:
        """Положить в кэш с TTL."""
        key = self._cache_key(query)
        with self._lock:
            # LRU-eviction: удалить самую старую запись если превышен лимит
            if len(self._cache) >= self.CACHE_MAX_SIZE:
                oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest_key]
            self._cache[key] = (time.time(), resp)

    def cache_clear(self) -> int:
        """Очистить кэш. Возвращает кол-во удалённых записей."""
        n = len(self._cache)
        self._cache.clear()
        return n

    def _rate_wait(self, engine_name: str) -> None:
        """Подождать если слишком быстро обращаемся к движку."""
        last = self._last_request.get(engine_name, 0)
        elapsed = time.time() - last
        if elapsed < self.RATE_LIMIT_DELAY:
            wait = self.RATE_LIMIT_DELAY - elapsed
            time.sleep(wait)
        self._last_request[engine_name] = time.time()

    # ──────────────────────────────────────────────────
    #  Utilities
    # ──────────────────────────────────────────────────

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.time() - start) * 1000)

    def get_stats(self) -> Dict[str, Any]:
        """Статистика для диагностики."""
        with self._lock:
            stats = dict(self._stats)
            stats["cache_size"] = len(self._cache)
        return stats

    def set_web_capable(self, capable: bool):
        """Включить/выключить web capability."""
        self._web_capable = capable


# ═══════════════════════════════════════════════════════════════════════════════
#  Синглтон
# ═══════════════════════════════════════════════════════════════════════════════

_engine: Optional[WebSearchEngine] = None
_engine_lock = threading.Lock()


def get_web_search_engine() -> WebSearchEngine:
    """Получить (или создать) экземпляр WebSearchEngine (thread-safe)."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = WebSearchEngine()
    return _engine
