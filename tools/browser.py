"""
Lina — Инструмент веб-поиска и загрузки документов.

Возможности:
  - Поиск в Google/DuckDuckGo
  - Загрузка и извлечение текста из веб-страниц
  - Парсинг RSS-фидов

HTTP: pure-Python urllib (без внешних бинарников).
"""

import re
import json
from typing import Optional
from html.parser import HTMLParser
from urllib.parse import quote_plus

from lina.system.logger import logger
from lina.utils.http import http_get, http_post


class _HTMLTextExtractor(HTMLParser):
    """Извлекает текст из HTML, убирая теги."""

    SKIP_TAGS = {"script", "style", "noscript", "head", "meta", "link"}

    def __init__(self):
        super().__init__()
        self._pieces = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._pieces.append(text)

    def get_text(self) -> str:
        return " ".join(self._pieces)


class WebTool:
    """
    Инструменты для работы с вебом.

    Не требует requests/beautifulsoup — использует urllib + встроенный HTMLParser.
    """

    HTTP_TIMEOUT = 15  # секунды

    def fetch_url(self, url: str, max_length: int = 50000) -> dict:
        """
        Загружает страницу и извлекает текст.

        Args:
            url: URL для загрузки.
            max_length: Макс. длина текста.

        Returns:
            dict: text, title, url, success
        """
        logger.info(f"WebTool: fetch {url}")

        # Validate URL scheme — block file://, gopher://, dict:// etc.
        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(url)
        if _parsed.scheme not in ("http", "https"):
            return {"text": "", "url": url, "success": False,
                    "error": f"Запрещённая схема URL: {_parsed.scheme or 'empty'}"}
        if not _parsed.hostname:
            return {"text": "", "url": url, "success": False,
                    "error": "URL без хоста"}

        try:
            html = http_get(
                url,
                timeout=self.HTTP_TIMEOUT,
                user_agent="Mozilla/5.0 (compatible; LinaBot/1.0)",
            )

            if not html:
                return {"text": "", "url": url, "success": False,
                        "error": "HTTP request failed"}

            # Извлекаем заголовок
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else ""

            # Извлекаем текст
            extractor = _HTMLTextExtractor()
            extractor.feed(html)
            text = extractor.get_text()

            if len(text) > max_length:
                text = text[:max_length] + "..."

            logger.audit("web_fetch", details={"url": url, "text_length": len(text)})

            return {
                "text": text,
                "title": title,
                "url": url,
                "success": True,
            }

        except TimeoutError:
            return {"text": "", "url": url, "success": False, "error": "Таймаут загрузки"}
        except Exception as e:
            return {"text": "", "url": url, "success": False, "error": str(e)}

    def search_duckduckgo(self, query: str, max_results: int = 5) -> list:
        """
        Поиск через DuckDuckGo Lite (без API-ключа).

        Args:
            query: Поисковый запрос.
            max_results: Макс. результатов.

        Returns:
            Список dict: title, url, snippet.
        """
        logger.info(f"WebTool: search '{query}'")

        encoded_query = quote_plus(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded_query}"

        try:
            html = http_get(
                url,
                timeout=self.HTTP_TIMEOUT,
                user_agent="Mozilla/5.0",
            )

            if not html:
                return []

            # Парсим результаты из DuckDuckGo Lite
            results = []
            # Ссылки в формате <a rel="nofollow" href="URL" class="result-link">Title</a>
            link_pattern = re.compile(
                r'<a[^>]+href="([^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>',
                re.IGNORECASE | re.DOTALL,
            )
            # Сниппеты
            snippet_pattern = re.compile(
                r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
                re.IGNORECASE | re.DOTALL,
            )

            links = link_pattern.findall(html)
            snippets = snippet_pattern.findall(html)

            for i, (href, title) in enumerate(links[:max_results]):
                snip = ""
                if i < len(snippets):
                    snip = re.sub(r"<[^>]+>", "", snippets[i]).strip()

                results.append({
                    "title": re.sub(r"<[^>]+>", "", title).strip(),
                    "url": href,
                    "snippet": snip[:300],
                })

            logger.audit("web_search", details={
                "query": query,
                "results_count": len(results),
            })

            return results

        except Exception as e:
            logger.error(f"Web search failed: {e}")
            return []

    def summarize_url(self, url: str, max_chars: int = 2000) -> str:
        """
        Загружает страницу и возвращает краткое содержание (первые N символов текста).
        """
        result = self.fetch_url(url, max_length=max_chars)
        if not result["success"]:
            return f"Не удалось загрузить: {result.get('error', 'неизвестная ошибка')}"

        title = result.get("title", "")
        text = result.get("text", "")

        prefix = f"📄 {title}\n\n" if title else ""
        return prefix + text
