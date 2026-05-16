"""
Lina — Клиент для внешних API.

Возможности:
  - HTTP-запросы к REST API (через urllib)
  - Базовый JSON-клиент
  - Погода, курсы валют и другие открытые API
"""

import json
import ipaddress
import re
import logging
from typing import Optional
from urllib.parse import quote as url_quote, urlparse

from lina.system.logger import logger

_CURRENCY_CODE_RE = re.compile(r'^[A-Z]{3}$')

# Private/internal IP ranges that must be blocked (SSRF protection)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),
    ipaddress.ip_network('fe80::/10'),
]


class APIClient:
    """
    Универсальный HTTP-клиент через curl.

    Не требует requests — использует curl из системы.
    """

    TIMEOUT = 10  # секунды

    def _validate_url(self, url: str) -> Optional[str]:
        """Validate URL for SSRF. Returns error message or None if OK."""
        try:
            parsed = urlparse(url)
        except Exception:
            return "Некорректный URL."
        if parsed.scheme not in ('https', 'http'):
            return f"Недопустимая схема URL: {parsed.scheme}"
        hostname = parsed.hostname
        if not hostname:
            return "URL не содержит хост."
        # Resolve hostname and check against blocked ranges
        import socket
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return "Не удалось разрешить хост."
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            for net in _BLOCKED_NETWORKS:
                if addr in net:
                    return "Запрос к внутренним адресам запрещён."
        return None

    def request(
        self,
        url: str,
        method: str = "GET",
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        """
        Выполняет HTTP-запрос через urllib (pure Python).

        Args:
            url: URL для запроса.
            method: HTTP метод.
            data: Тело запроса (JSON).
            headers: Заголовки.

        Returns:
            dict: body, status_code, success, json (если ответ JSON)
        """
        # SSRF protection: validate URL
        url_err = self._validate_url(url)
        if url_err:
            return {"body": "", "status_code": 0, "success": False, "error": url_err}

        from lina.utils.http import http_request

        req_headers = dict(headers) if headers else {}
        req_data = None
        if data:
            req_headers["Content-Type"] = "application/json"
            req_data = json.dumps(data, ensure_ascii=False)

        logger.debug(f"API request: {method} {url}")

        try:
            status, body = http_request(
                url,
                method=method,
                data=req_data,
                timeout=self.TIMEOUT,
                headers=req_headers,
            )

            if status == 0:
                return {
                    "body": "",
                    "status_code": 0,
                    "success": False,
                    "error": "HTTP request failed (timeout or connection error)",
                }

            # Пытаемся распарсить JSON
            json_body = None
            try:
                json_body = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                pass

            logger.audit("api_request", details={
                "method": method,
                "url": url,
                "status": status,
            })

            return {
                "body": body,
                "status_code": status,
                "success": 200 <= status < 400,
                "json": json_body,
            }

        except TimeoutError:
            return {"body": "", "status_code": 0, "success": False, "error": "Таймаут"}
        except Exception as e:
            logging.getLogger(__name__).error("API request error: %s", e)
            return {"body": "", "status_code": 0, "success": False, "error": "Внутренняя ошибка при выполнении запроса."}

    def get_json(self, url: str) -> Optional[dict]:
        """Упрощённый GET-запрос, возвращает JSON или None."""
        result = self.request(url)
        return result.get("json")

    # ── Встроенные API ──

    def get_weather(self, city: str = "Moscow") -> str:
        """Погода через wttr.in (бесплатно, без ключа)."""
        city_clean = city.strip()
        if not city_clean:
            city_clean = "Moscow"
        city_encoded = url_quote(city_clean, safe="")
        url = f"https://wttr.in/{city_encoded}?format=3&lang=ru"
        result = self.request(url)
        if result["success"]:
            return result["body"]
        return f"Не удалось получить погоду: {result.get('error') or 'неизвестная ошибка'}"

    def get_ip_info(self) -> dict:
        """Информация о текущем IP."""
        result = self.request("https://ipinfo.io/json")
        return result.get("json") or {"error": "Не удалось получить IP-информацию"}

    def get_exchange_rate(self, base: str = "USD", target: str = "RUB") -> str:
        """
        Курс валюты через frankfurter.app (бесплатно).
        """
        if not _CURRENCY_CODE_RE.match(base) or not _CURRENCY_CODE_RE.match(target):
            return "Некорректный код валюты. Используйте ISO 4217 (например, USD, EUR, RUB)."
        url = f"https://api.frankfurter.app/latest?from={base}&to={target}"
        result = self.request(url)
        data = result.get("json")
        if data and "rates" in data:
            rate = data["rates"].get(target, "?")
            return f"1 {base} = {rate} {target}"
        return "Не удалось получить курс валюты"
