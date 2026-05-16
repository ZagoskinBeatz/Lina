"""
Lina — Smart Workflows.

Многошаговые «умные» сценарии для устройств и API.

В отличие от одноходовых _DIRECT_ACTIONS,
здесь Lina сама управляет процессом: включает BT, сканирует,
ждёт устройство, подключает — как в фантастических фильмах.

Сценарии:
  • bluetooth_connect(device_name) — найти + подключить BT-устройство
  • wifi_connect(ssid, password?)   — подключиться к WiFi-сети
  • get_weather(city?)              — погода через Open-Meteo (бесплатно)
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import time
from typing import Optional, List, Dict, Tuple
from urllib.parse import quote as url_quote

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _run(cmd: str | list, timeout: int = 10) -> Tuple[str, int]:
    """Run a command. Accepts string (split via shlex) or list. Returns (output, returncode)."""
    try:
        args = cmd if isinstance(cmd, list) else shlex.split(cmd)
        proc = subprocess.run(
            args, shell=False, capture_output=True, text=True,
            timeout=timeout,
            env={**os.environ, "LANG": "C.UTF-8"},
        )
        out = proc.stdout.strip()
        if proc.returncode != 0 and proc.stderr.strip():
            out = (out + "\n" + proc.stderr.strip()).strip()
        return out, proc.returncode
    except subprocess.TimeoutExpired:
        return "⏰ Таймаут", -1
    except Exception as e:
        return str(e), -1


# ═══════════════════════════════════════════════════════════════════════════════
#  Bluetooth Smart Connect
# ═══════════════════════════════════════════════════════════════════════════════

# Aliases: user might say «Buds Pro» or «наушники» or «колонка»
_BT_DEVICE_ALIASES: Dict[str, List[str]] = {
    "buds": ["buds"],
    "buds pro": ["buds pro"],
    "galaxy buds": ["galaxy buds", "buds"],
    "airpods": ["airpods"],
    "наушники": [],  # special: match any audio device
    "колонка": [],   # special: match any audio device
    "jbl": ["jbl"],
    "sony": ["sony", "wh-", "wf-"],
    "marshall": ["marshall"],
    "xiaomi": ["xiaomi", "redmi buds"],
}


def _bt_is_powered() -> bool:
    """Check if Bluetooth adapter is powered on."""
    out, _ = _run(["bluetoothctl", "show"])
    return "Powered: yes" in out


def _bt_power_on() -> bool:
    """Turn on Bluetooth adapter."""
    _run(["bluetoothctl", "power", "on"])
    time.sleep(0.5)
    return _bt_is_powered()


def _bt_get_paired_devices() -> List[Dict[str, str]]:
    """List paired BT devices. Returns [{mac, name}]."""
    out, _ = _run(["bluetoothctl", "devices", "Paired"])
    if not out.strip():
        out, _ = _run(["bluetoothctl", "paired-devices"])
    devices = []
    for line in out.splitlines():
        # "Device AA:BB:CC:DD:EE:FF Some Name"
        m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line.strip())
        if m:
            devices.append({"mac": m.group(1), "name": m.group(2)})
    return devices


def _bt_get_scanned_devices() -> List[Dict[str, str]]:
    """List all known (scanned) BT devices. Returns [{mac, name}]."""
    out, _ = _run(["bluetoothctl", "devices"])
    devices = []
    for line in out.splitlines():
        m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line.strip())
        if m:
            devices.append({"mac": m.group(1), "name": m.group(2)})
    return devices


def _bt_is_connected(mac: str) -> bool:
    """Check if a BT device is connected."""
    out, _ = _run(["bluetoothctl", "info", mac])
    return "Connected: yes" in out


def _bt_find_device(name_query: str, devices: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Find device by fuzzy name match."""
    query = name_query.lower()
    aliases = _BT_DEVICE_ALIASES.get(query, [query])

    # If empty aliases (generic "наушники"/"колонка") — match any
    generic = not aliases

    for dev in devices:
        dev_name_lower = dev["name"].lower()
        if generic:
            # For generic queries, match any non-trivial device
            # Skip devices with MAC-like names (unnamed)
            if re.match(r"^[0-9a-f]{2}[:-]", dev_name_lower):
                continue
            return dev
        for alias in aliases:
            if alias.lower() in dev_name_lower:
                return dev
    return None


def bluetooth_connect(device_query: str, progress_cb=None) -> str:
    """
    Smart Bluetooth connect workflow.

    Steps:
        1. Check if BT is powered on → power on if not
        2. Look in paired devices first
        3. If not found → scan for 15 seconds
        4. If found → trust + pair + connect
        5. Report result

    Args:
        device_query: Device name fragment (e.g. "Buds Pro", "JBL", "наушники")
        progress_cb: Optional callback(str) for live status updates

    Returns:
        Human-readable result string
    """
    def _report(msg: str):
        logger.info("BT workflow: %s", msg)
        if progress_cb:
            progress_cb(msg)

    _report(f"🔍 Ищу устройство «{device_query}»...")

    # 1. Power on
    if not _bt_is_powered():
        _report("📡 Включаю Bluetooth...")
        if not _bt_power_on():
            return "❌ Не удалось включить Bluetooth. Проверьте аппаратный переключатель."

    # 2. Check paired devices first
    _report("📋 Проверяю спаренные устройства...")
    paired = _bt_get_paired_devices()
    found = _bt_find_device(device_query, paired)

    if found:
        if _bt_is_connected(found["mac"]):
            return f"✅ «{found['name']}» уже подключено."
        _report(f"🔗 Подключаю «{found['name']}»...")
        out, rc = _run(["bluetoothctl", "connect", found['mac']], timeout=15)
        if rc == 0 or "successful" in out.lower():
            return f"✅ «{found['name']}» подключено!"
        # retry once
        time.sleep(1)
        out, rc = _run(["bluetoothctl", "connect", found['mac']], timeout=15)
        if rc == 0 or "successful" in out.lower():
            return f"✅ «{found['name']}» подключено!"
        return f"⚠ Устройство «{found['name']}» найдено, но подключение не удалось:\n{out}"

    # 3. Scan for new devices
    _report("📡 Сканирую Bluetooth-устройства (15 сек)...")

    # Start scan in background
    _run(["bluetoothctl", "--timeout", "15", "scan", "on"], timeout=1)

    # Poll for device every 2 sec, up to ~45 sec total
    max_wait = 45
    poll_interval = 3
    elapsed = 0

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        all_devices = _bt_get_scanned_devices()
        found = _bt_find_device(device_query, all_devices)

        if found:
            _report(f"✅ Найдено: «{found['name']}» ({found['mac']})")

            # Trust
            _run(["bluetoothctl", "trust", found['mac']])
            time.sleep(0.3)

            # Pair (if not already)
            out_pair, _ = _run(["bluetoothctl", "pair", found['mac']], timeout=15)
            time.sleep(0.5)

            # Connect
            _report(f"🔗 Подключаю «{found['name']}»...")
            out_conn, rc = _run(["bluetoothctl", "connect", found['mac']], timeout=15)

            if rc == 0 or "successful" in out_conn.lower():
                return f"✅ «{found['name']}» подключено!"

            # One retry
            time.sleep(2)
            out_conn, rc = _run(["bluetoothctl", "connect", found['mac']], timeout=15)
            if rc == 0 or "successful" in out_conn.lower():
                return f"✅ «{found['name']}» подключено!"

            return (f"⚠ Устройство «{found['name']}» найдено и спарено, "
                    f"но подключение не удалось:\n{out_conn}")

        if elapsed < max_wait:
            remaining = max_wait - elapsed
            _report(f"📡 Сканирую... (ещё {remaining} сек)")

    # Stop scan
    _run(["bluetoothctl", "scan", "off"])

    return (f"❌ Устройство «{device_query}» не найдено за {max_wait} сек.\n"
            f"Убедитесь, что устройство включено и находится в режиме сопряжения.")


# ═══════════════════════════════════════════════════════════════════════════════
#  WiFi Smart Connect
# ═══════════════════════════════════════════════════════════════════════════════

def _wifi_is_enabled() -> bool:
    """Check if WiFi radio is on."""
    out, _ = _run(["nmcli", "radio", "wifi"])
    return "enabled" in out.lower()


def _wifi_enable() -> bool:
    """Turn on WiFi radio."""
    _run(["nmcli", "radio", "wifi", "on"])
    time.sleep(1)
    return _wifi_is_enabled()


def _wifi_get_active() -> Optional[str]:
    """Return active WiFi SSID or None."""
    out, _ = _run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi", "list"])
    for line in out.splitlines():
        if line.startswith("yes:"):
            return line.split(":", 1)[1]
    return None


def _wifi_scan() -> List[Dict[str, str]]:
    """Scan WiFi networks. Returns [{ssid, signal, security}]."""
    # Force rescan
    _run(["nmcli", "dev", "wifi", "rescan"], timeout=10)
    time.sleep(2)
    out, _ = _run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"])
    networks = []
    seen = set()
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] and parts[0] not in seen:
            seen.add(parts[0])
            networks.append({
                "ssid": parts[0],
                "signal": parts[1],
                "security": parts[2],
            })
    return sorted(networks, key=lambda n: int(n["signal"] or "0"), reverse=True)


def _wifi_has_saved(ssid: str) -> bool:
    """Check if there's a saved connection profile for this SSID."""
    out, _ = _run(["nmcli", "-t", "-f", "NAME", "connection", "show"])
    return ssid in out


def wifi_connect(ssid: str, password: Optional[str] = None, progress_cb=None) -> str:
    """
    Smart WiFi connect workflow.

    Steps:
        1. Enable WiFi if disabled
        2. Check if already connected
        3. Check saved connections
        4. Scan and find network
        5. Connect (with password if open/new)
        6. Verify connection

    Args:
        ssid: Network name (or fragment)
        password: Optional password (None = try saved / open)
        progress_cb: Optional callback(str) for live status

    Returns:
        Human-readable result string
    """
    def _report(msg: str):
        logger.info("WiFi workflow: %s", msg)
        if progress_cb:
            progress_cb(msg)

    _report(f"📶 Подключаюсь к «{ssid}»...")

    # 1. Enable WiFi
    if not _wifi_is_enabled():
        _report("📡 Включаю WiFi...")
        if not _wifi_enable():
            return "❌ Не удалось включить WiFi."

    # 2. Already connected?
    active = _wifi_get_active()
    if active and ssid.lower() in active.lower():
        return f"✅ Уже подключено к «{active}»."

    # 3. Try saved connection (no rescan needed)
    if _wifi_has_saved(ssid):
        _report(f"🔗 Использую сохранённое подключение «{ssid}»...")
        out, rc = _run(["nmcli", "connection", "up", ssid], timeout=30)
        if rc == 0:
            return f"✅ Подключено к «{ssid}»!"

    # 4. Scan for network
    _report("📡 Сканирую сети...")
    networks = _wifi_scan()

    # Find matching SSID (fuzzy)
    exact = None
    partial = None
    for net in networks:
        if net["ssid"].lower() == ssid.lower():
            exact = net
            break
        if ssid.lower() in net["ssid"].lower():
            partial = net

    target = exact or partial
    if not target:
        net_list = "\n".join(f"  • {n['ssid']} ({n['signal']}%, {n['security']})"
                              for n in networks[:10])
        return (f"❌ Сеть «{ssid}» не найдена.\n"
                f"Доступные сети:\n{net_list}")

    real_ssid = target["ssid"]
    _report(f"🔗 Подключаюсь к «{real_ssid}» (сигнал: {target['signal']}%)...")

    # 5. Connect
    has_security = target["security"] and target["security"] != "--"
    if has_security and password:
        out, rc = _run(
            ["nmcli", "dev", "wifi", "connect", real_ssid, "password", password],
            timeout=30,
        )
    elif has_security and not password:
        # Try without password (might have saved credentials)
        out, rc = _run(["nmcli", "dev", "wifi", "connect", real_ssid], timeout=30)
        if rc != 0 and "secrets" in out.lower():
            return (f"🔒 Сеть «{real_ssid}» защищена паролем.\n"
                    f"Скажите: «подключись к {real_ssid} пароль XXXXXX»")
    else:
        # Open network
        out, rc = _run(["nmcli", "dev", "wifi", "connect", real_ssid], timeout=30)

    if rc == 0:
        return f"✅ Подключено к «{real_ssid}»!"

    return f"⚠ Не удалось подключиться к «{real_ssid}»:\n{out}"


def wifi_disconnect(progress_cb=None) -> str:
    """Disconnect from current WiFi."""
    active = _wifi_get_active()
    if not active:
        return "ℹ WiFi не подключён."
    out, rc = _run(["nmcli", "connection", "down", active])
    if rc == 0:
        return f"✅ Отключено от «{active}»."
    return f"⚠ Не удалось отключиться: {out}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Weather — Open-Meteo (free, no API key, no rate limits)
# ═══════════════════════════════════════════════════════════════════════════════

# WMO weather codes → human descriptions
_WMO_CODES: Dict[int, str] = {
    0: "☀️ Ясно",
    1: "🌤 Преимущественно ясно",
    2: "⛅ Переменная облачность",
    3: "☁️ Пасмурно",
    45: "🌫 Туман",
    48: "🌫 Изморозь",
    51: "🌦 Лёгкая морось",
    53: "🌦 Морось",
    55: "🌧 Сильная морось",
    61: "🌧 Небольшой дождь",
    63: "🌧 Дождь",
    65: "🌧 Сильный дождь",
    66: "🌧❄ Ледяной дождь",
    67: "🌧❄ Сильный ледяной дождь",
    71: "🌨 Небольшой снег",
    73: "🌨 Снег",
    75: "❄️ Сильный снег",
    77: "❄️ Снежная крупа",
    80: "🌦 Ливень",
    81: "🌧 Сильный ливень",
    82: "⛈ Очень сильный ливень",
    85: "🌨 Снегопад",
    86: "❄️ Сильный снегопад",
    95: "⛈ Гроза",
    96: "⛈ Гроза с градом",
    99: "⛈ Гроза с сильным градом",
}

# City → (lat, lon) for common Russian cities
# Keys include nominative AND prepositional case (предложный падеж)
_CITY_COORDS: Dict[str, Tuple[float, float]] = {
    "москва": (55.7558, 37.6173),
    "москве": (55.7558, 37.6173),
    "moscow": (55.7558, 37.6173),
    "питер": (59.9343, 30.3351),
    "питере": (59.9343, 30.3351),
    "петербург": (59.9343, 30.3351),
    "петербурге": (59.9343, 30.3351),
    "санкт-петербург": (59.9343, 30.3351),
    "санкт-петербурге": (59.9343, 30.3351),
    "saint petersburg": (59.9343, 30.3351),
    "spb": (59.9343, 30.3351),
    "новосибирск": (55.0084, 82.9357),
    "новосибирске": (55.0084, 82.9357),
    "novosibirsk": (55.0084, 82.9357),
    "екатеринбург": (56.8389, 60.6057),
    "екатеринбурге": (56.8389, 60.6057),
    "казань": (55.7887, 49.1221),
    "казани": (55.7887, 49.1221),
    "нижний новгород": (56.2965, 43.9361),
    "нижнем новгороде": (56.2965, 43.9361),
    "челябинск": (55.1644, 61.4368),
    "челябинске": (55.1644, 61.4368),
    "самара": (53.1959, 50.1002),
    "самаре": (53.1959, 50.1002),
    "омск": (54.9885, 73.3242),
    "омске": (54.9885, 73.3242),
    "ростов": (47.2357, 39.7015),
    "ростове": (47.2357, 39.7015),
    "ростов-на-дону": (47.2357, 39.7015),
    "уфа": (54.7388, 55.9721),
    "уфе": (54.7388, 55.9721),
    "красноярск": (56.0153, 92.8932),
    "красноярске": (56.0153, 92.8932),
    "пермь": (58.0105, 56.2502),
    "перми": (58.0105, 56.2502),
    "воронеж": (51.6720, 39.1843),
    "воронеже": (51.6720, 39.1843),
    "волгоград": (48.7080, 44.5133),
    "волгограде": (48.7080, 44.5133),
    "краснодар": (45.0355, 38.9753),
    "краснодаре": (45.0355, 38.9753),
    "сочи": (43.5855, 39.7231),
    "калининград": (54.7104, 20.4522),
    "калининграде": (54.7104, 20.4522),
    "минск": (53.9045, 27.5615),
    "минске": (53.9045, 27.5615),
    "киев": (50.4501, 30.5234),
    "киеве": (50.4501, 30.5234),
    "алматы": (43.2220, 76.8512),
    "ташкент": (41.2995, 69.2401),
    "ташкенте": (41.2995, 69.2401),
    "тбилиси": (41.7151, 44.8271),
    "лондон": (51.5074, -0.1278),
    "лондоне": (51.5074, -0.1278),
    "london": (51.5074, -0.1278),
    "париж": (48.8566, 2.3522),
    "париже": (48.8566, 2.3522),
    "paris": (48.8566, 2.3522),
    "берлин": (52.5200, 13.4050),
    "берлине": (52.5200, 13.4050),
    "berlin": (52.5200, 13.4050),
    "нью-йорк": (40.7128, -74.0060),
    "нью-йорке": (40.7128, -74.0060),
    "new york": (40.7128, -74.0060),
    "токио": (35.6762, 139.6503),
    "tokyo": (35.6762, 139.6503),
}


def _geocode(city: str) -> Optional[Tuple[float, float]]:
    """Resolve city name to (lat, lon). First local dict, then Open-Meteo geocoding."""
    city_lower = city.lower().strip()

    # 1. Local lookup
    if city_lower in _CITY_COORDS:
        return _CITY_COORDS[city_lower]

    # 2. Open-Meteo geocoding API (free)
    try:
        from lina.utils.http import http_get
        encoded = url_quote(city, safe="")
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={encoded}&count=1&language=ru"
        out = http_get(url, timeout=5)
        if out:
            data = json.loads(out)
            results = data.get("results", [])
            if results:
                r = results[0]
                return (r["latitude"], r["longitude"])
    except Exception as e:
        logger.debug("Geocoding failed: %s", e)

    return None


def get_weather(city: str = "Москва") -> str:
    """
    Get current weather via Open-Meteo (free, no API key).

    Args:
        city: City name (Russian or English)

    Returns:
        Human-readable weather string
    """
    coords = _geocode(city)
    if not coords:
        return f"❌ Город «{city}» не найден. Попробуйте указать точнее."

    lat, lon = coords
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,apparent_temperature,weathercode,"
        f"windspeed_10m,relative_humidity_2m,precipitation"
        f"&timezone=auto"
    )

    from lina.utils.http import http_get
    out = http_get(url, timeout=10)
    if not out:
        return "❌ Не удалось получить данные о погоде."

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return "❌ Ошибка разбора данных погоды."

    current = data.get("current", {})
    if not current:
        return "❌ Нет данных о текущей погоде."

    temp = current.get("temperature_2m", "?")
    feels = current.get("apparent_temperature", "?")
    code = current.get("weathercode", -1)
    wind = current.get("windspeed_10m", "?")
    humidity = current.get("relative_humidity_2m", "?")
    precip = current.get("precipitation", 0)

    desc = _WMO_CODES.get(code, f"Код: {code}")

    lines = [
        f"🌍 Погода в городе {city.capitalize()}:",
        f"  {desc}",
        f"  🌡 Температура: {temp}°C (ощущается как {feels}°C)",
        f"  💧 Влажность: {humidity}%",
        f"  💨 Ветер: {wind} км/ч",
    ]
    if precip and float(precip) > 0:
        lines.append(f"  🌧 Осадки: {precip} мм")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  Bluetooth Disconnect
# ═══════════════════════════════════════════════════════════════════════════════

def bluetooth_disconnect(device_query: str = "", progress_cb=None) -> str:
    """Disconnect a Bluetooth device (or all)."""
    if not _bt_is_powered():
        return "ℹ️ Bluetooth выключен."

    # If no specific device — disconnect all connected
    if not device_query or device_query.lower() in ("все", "всё", "all"):
        devices = _bt_get_paired_devices()
        disconnected = []
        for dev in devices:
            if _bt_is_connected(dev["mac"]):
                _run(["bluetoothctl", "disconnect", dev["mac"]])
                disconnected.append(dev["name"])
        if disconnected:
            return "✅ Отключено: " + ", ".join(disconnected)
        return "ℹ️ Нет подключённых Bluetooth-устройств."

    # Disconnect specific device
    paired = _bt_get_paired_devices()
    found = _bt_find_device(device_query, paired)
    if found:
        if not _bt_is_connected(found["mac"]):
            return f"ℹ️ «{found['name']}» уже отключено."
        _run(["bluetoothctl", "disconnect", found['mac']])
        return f"✅ «{found['name']}» отключено."

    return f"⚠ Устройство «{device_query}» не найдено среди спаренных."


# ═══════════════════════════════════════════════════════════════════════════════
#  Pattern Matchers (for QueryPreprocessor integration)
# ═══════════════════════════════════════════════════════════════════════════════

# «подключи Buds Pro», «подключись к buds», «соедини с наушниками»
BT_CONNECT_PATTERN = re.compile(
    r"(?:подключи|подсоедини|соедини|connect|pair|спарь|"
    r"сопряги|запарь|законнекть?)"
    r"(?:сь)?"                          # подключись
    r"\s*(?:к\s+|с\s+)?"              # к / с
    r"(?:(?:по\s+)?(?:блютуз|блютус|bluetooth|bt)\s+)?"  # по блютуз
    r"(.+?)"                          # device name
    r"(?:\s+(?:по|by|via|through)\s+(?:блютуз|блютус|bluetooth|bt))?$",  # trailing «по блютуз»
    re.IGNORECASE,
)

# «отключи наушники», «отключи Buds Pro от блютуза»
BT_DISCONNECT_PATTERN = re.compile(
    r"(?:отключи|отсоедини|disconnect|разъедини)\s*"
    r"(?:от\s+(?:блютуз|bluetooth)\s+)?(.+?)(?:\s+от\s+(?:блютуз|bluetooth))?$",
    re.IGNORECASE,
)

# «подключись к WiFi MyNetwork», «подключи вайфай HomeNet пароль 12345»
WIFI_CONNECT_PATTERN = re.compile(
    r"(?:подключи|подключись|подсоедини|connect)\s*"
    r"(?:к\s+)?(?:(?:wi-?fi|wifi|вай-?фай|вайфай)\s+)?"
    r"(?:к\s+)?(?:сети\s+)?"
    r"([^\s]+(?:\s+[^\s]+)?)"
    r"(?:\s+(?:пароль|password|pass|ключ)\s+(.+))?$",
    re.IGNORECASE,
)

# «погода», «погода в Москве», «какая погода в Питере»
WEATHER_PATTERN = re.compile(
    r"(?:погода|weather)\s*(?:в\s+|in\s+)?(.+)?$",
    re.IGNORECASE,
)
