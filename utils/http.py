# -*- coding: utf-8 -*-
"""
Lina Utils — HTTP Client (replaces subprocess+curl).

Dual-backend: prefers ``requests`` (handles SOCKS5 proxies via PySocks),
falls back to ``urllib.request`` when requests is unavailable.

Usage:
    from lina.utils.http import http_get, http_post

    body = http_get("https://example.com", timeout=10)
    body = http_post("https://lite.duckduckgo.com/lite/",
                     data="q=test", timeout=10)
"""

from __future__ import annotations

import gzip
import logging
import os
import ssl
import warnings
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger("lina.utils.http")

_DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"

# ── Try importing requests (used as SOCKS fallback when urllib fails) ──
_requests = None
try:
    import requests as _requests
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="requests")
    warnings.filterwarnings("ignore", message=".*urllib3.*chardet.*charset_normalizer.*")
    warnings.filterwarnings("ignore", message=".*RequestsDependencyWarning.*")
    # Suppress InsecureRequestWarning from ALL urllib3 instances (system + bundled)
    import urllib3
    urllib3.disable_warnings()
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    # Also suppress via requests' bundled urllib3
    try:
        _requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
    except Exception:
        pass
    # Category-level filter — catches ALL flavors of the warning
    try:
        from urllib3.exceptions import InsecureRequestWarning as _IW
        warnings.filterwarnings("ignore", category=_IW)
    except Exception:
        pass
    try:
        from requests.packages.urllib3.exceptions import InsecureRequestWarning as _IW2
        warnings.filterwarnings("ignore", category=_IW2)
    except Exception:
        pass
    # Broadest possible filters
    warnings.filterwarnings("ignore", message=".*Unverified HTTPS.*")
    warnings.filterwarnings("ignore", message=".*InsecureRequestWarning.*")
    warnings.filterwarnings("ignore", message=".*certificate verification.*")
    # module= uses regex on the fully-qualified module name
    warnings.filterwarnings("ignore", module=r".*urllib3.*")
    warnings.filterwarnings("ignore", module=r".*connectionpool.*")
except ImportError:
    _requests = None

_DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"

# Lenient SSL context (matches curl -sL behaviour — follows redirects,
# doesn't reject self-signed certs in dev scenarios).
_ssl_ctx: ssl.SSLContext | None = None


def _get_ssl_ctx() -> ssl.SSLContext:
    global _ssl_ctx
    if _ssl_ctx is None:
        _ssl_ctx = ssl.create_default_context()
        # Some sites (e.g. SearXNG instances) may have imperfect certs;
        # curl -sL silently accepts these.  Match that behaviour.
        _ssl_ctx.check_hostname = False
        _ssl_ctx.verify_mode = ssl.CERT_NONE
    return _ssl_ctx


def _has_socks_proxy() -> bool:
    """True when environment requests a SOCKS proxy backend."""
    return any(
        "socks" in os.environ.get(v, "").lower()
        for v in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy")
    )


def _http_get_requests(
    url: str,
    *,
    timeout: int,
    user_agent: str,
    headers: Optional[Dict[str, str]],
    encoding: str,
    raw: bool,
) -> str | bytes:
    if _requests is None:
        return b"" if raw else ""
    try:
        hdrs = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        if headers:
            hdrs.update(headers)
        resp = _requests.get(url, headers=hdrs, timeout=timeout, verify=False)
        data = resp.content
        if raw:
            return data
        return data.decode(encoding, errors="replace")
    except Exception as e:
        logger.debug("HTTP GET (requests) %s failed: %s", url, e)
        return b"" if raw else ""


def _http_post_requests(
    url: str,
    *,
    body: bytes,
    timeout: int,
    user_agent: str,
    headers: Optional[Dict[str, str]],
    encoding: str,
) -> str:
    if _requests is None:
        return ""
    try:
        hdrs = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if headers:
            hdrs.update(headers)
        resp = _requests.post(url, data=body, headers=hdrs,
                              timeout=timeout, verify=False)
        return resp.content.decode(encoding, errors="replace")
    except Exception as e:
        logger.debug("HTTP POST (requests) %s failed: %s", url, e)
        return ""


def _http_request_requests(
    url: str,
    *,
    method: str,
    body_bytes: bytes | None,
    timeout: int,
    user_agent: str,
    headers: Optional[Dict[str, str]],
    encoding: str,
) -> tuple[int, str]:
    if _requests is None:
        return 0, ""
    try:
        hdrs = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        if body_bytes is not None and not (headers and "Content-Type" in headers):
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        if headers:
            hdrs.update(headers)
        resp = _requests.request(
            method.upper(), url, data=body_bytes,
            headers=hdrs, timeout=timeout, verify=False,
        )
        return resp.status_code, resp.content.decode(encoding, errors="replace")
    except Exception as e:
        logger.debug("HTTP %s (requests) %s failed: %s", method, url, e)
        return 0, ""


def http_get(
    url: str,
    *,
    timeout: int = 10,
    user_agent: str = _DEFAULT_UA,
    headers: Optional[Dict[str, str]] = None,
    encoding: str = "utf-8",
    raw: bool = False,
) -> str | bytes:
    """HTTP GET → decoded text (or raw bytes if raw=True).

    Returns empty string/bytes on any error.
    """
    # ── urllib fallback ──
    req = Request(url, method="GET")
    req.add_header("User-Agent", user_agent)
    req.add_header("Accept-Encoding", "gzip, deflate")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    try:
        with urlopen(req, timeout=timeout, context=_get_ssl_ctx()) as resp:
            data = resp.read()

            # Decompress gzip if served compressed
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                data = gzip.decompress(data)

            if raw:
                return data
            return data.decode(encoding, errors="replace")

    except HTTPError as e:
        e.close()
        logger.debug("HTTP GET %s → %d %s", url, e.code, e.reason)
        return b"" if raw else ""
    except (URLError, TimeoutError, OSError) as e:
        logger.debug("HTTP GET %s failed: %s", url, e)
        if _has_socks_proxy() and _requests is not None:
            return _http_get_requests(
                url,
                timeout=timeout,
                user_agent=user_agent,
                headers=headers,
                encoding=encoding,
                raw=raw,
            )
        return b"" if raw else ""
    except Exception as e:
        logger.debug("HTTP GET %s unexpected error: %s", url, e)
        return b"" if raw else ""


def http_post(
    url: str,
    *,
    data: str | bytes | Dict[str, str] = "",
    timeout: int = 10,
    user_agent: str = _DEFAULT_UA,
    headers: Optional[Dict[str, str]] = None,
    encoding: str = "utf-8",
) -> str:
    """HTTP POST → decoded text.

    ``data`` can be a string, bytes, or dict (auto-urlencoded).
    Returns empty string on any error.
    """
    if isinstance(data, dict):
        body = urlencode(data).encode()
    elif isinstance(data, str):
        body = data.encode()
    else:
        body = data

    # ── urllib fallback ──
    req = Request(url, data=body, method="POST")
    req.add_header("User-Agent", user_agent)
    req.add_header("Accept-Encoding", "gzip, deflate")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    try:
        with urlopen(req, timeout=timeout, context=_get_ssl_ctx()) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode(encoding, errors="replace")

    except HTTPError as e:
        e.close()
        logger.debug("HTTP POST %s → %d %s", url, e.code, e.reason)
        return ""
    except (URLError, TimeoutError, OSError) as e:
        logger.debug("HTTP POST %s failed: %s", url, e)
        if _has_socks_proxy() and _requests is not None:
            return _http_post_requests(
                url,
                body=body,
                timeout=timeout,
                user_agent=user_agent,
                headers=headers,
                encoding=encoding,
            )
        return ""
    except Exception as e:
        logger.debug("HTTP POST %s unexpected error: %s", url, e)
        return ""


def http_request(
    url: str,
    *,
    method: str = "GET",
    data: str | bytes | Dict[str, str] | None = None,
    timeout: int = 10,
    user_agent: str = _DEFAULT_UA,
    headers: Optional[Dict[str, str]] = None,
    encoding: str = "utf-8",
) -> tuple[int, str]:
    """Generic HTTP request → (status_code, body_text).

    Returns (0, "") on any network/timeout error.
    Used by tools/api.py as a drop-in replacement for curl.
    """
    body_bytes = None
    if data is not None:
        if isinstance(data, dict):
            body_bytes = urlencode(data).encode()
        elif isinstance(data, str):
            body_bytes = data.encode()
        else:
            body_bytes = data

    # ── urllib fallback ──
    req = Request(url, data=body_bytes, method=method.upper())
    req.add_header("User-Agent", user_agent)
    req.add_header("Accept-Encoding", "gzip, deflate")
    if body_bytes is not None and not (headers and "Content-Type" in headers):
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    try:
        with urlopen(req, timeout=timeout, context=_get_ssl_ctx()) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return resp.status, raw.decode(encoding, errors="replace")
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode(encoding, errors="replace")
        except Exception:
            pass
        e.close()
        return e.code, body
    except (URLError, TimeoutError, OSError) as e:
        logger.debug("HTTP %s %s failed: %s", method, url, e)
        if _has_socks_proxy() and _requests is not None:
            return _http_request_requests(
                url,
                method=method,
                body_bytes=body_bytes,
                timeout=timeout,
                user_agent=user_agent,
                headers=headers,
                encoding=encoding,
            )
        return 0, ""
    except Exception as e:
        logger.debug("HTTP %s %s unexpected: %s", method, url, e)
        return 0, ""


def http_check(url: str, *, timeout: int = 5) -> bool:
    """Light connectivity check — returns True if HTTP request succeeds (any 2xx/3xx).

    Used by network_manager.py to replace curl -s -o /dev/null.
    """
    try:
        req = Request(url, method="HEAD")
        req.add_header("User-Agent", _DEFAULT_UA)
        with urlopen(req, timeout=timeout, context=_get_ssl_ctx()) as resp:
            return 200 <= resp.status < 400
    except HTTPError as e:
        e.close()
        # Any HTTP response means connectivity exists
        return True
    except Exception:
        return False
