# -*- coding: utf-8 -*-
"""
Lina Core — Fact Extractor (v2 Pipeline).

Extracts structured (subject, predicate, object) triples from text passages.

Strategy:
  1. Regex patterns for hardware specs (processor, RAM, battery, display…)
  2. Key-value pair detection ("Feature: Value" lines)
  3. Named entity co-occurrence for general facts

Design: deterministic, no LLM required, multi-language (RU/EN).
"""

from __future__ import annotations

import re
import logging
from typing import List, Dict, Optional, Tuple, Set

from lina.models.datatypes import Fact, Passage

logger = logging.getLogger("lina.core.fact_extractor")


# ═══════════════════════════════════════════════════
#  Spec Extraction Patterns
# ═══════════════════════════════════════════════════

# Each entry: (predicate_name, compiled_regex, group_index_for_value)
# Regex must have at least one capture group containing the value.
_SPEC_PATTERNS: List[Tuple[str, re.Pattern, int]] = [
    # Processor / SoC
    # NOTE: trailing word separators use [^\S\n] (horizontal whitespace only)
    # to prevent capturing across newlines (e.g. "Helio G99\nOЗУ" → "Helio G99 O").
    ("processor", re.compile(
        r"(?:процессор\w*|chipset|soc|processor|\u0447\u0438\u043f\u0441\u0435\u0442\w*|powered by)\s*[:\-\u2013\u2014]?\s*"
        r"((?:Qualcomm\s+)?Snapdragon\s\d[\w]*(?:[^\S\n](?!RAM|Storage|Battery|Display|Charging|OS|Screen|Memory)[A-Za-z0-9]+){0,3}|"
        r"(?:MediaTek\s+)?(?:Dimensity|Helio)\s[A-Za-z0-9]+(?:[^\S\n](?!RAM|Storage|Battery|Display|Charging|OS|Screen|Memory)[A-Za-z0-9]+){0,2}|"
        r"Exynos\s\d[\w]*(?:[^\S\n](?!RAM|Storage|Battery|Display|Charging|OS|Screen|Memory)[A-Za-z0-9]+){0,2}|"
        r"Apple\s[AM]\d[\w]*(?:[^\S\n](?!RAM|Storage|Battery|Display|Charging|OS|Screen|Memory)[A-Za-z]+){0,2}|"
        r"Tensor\s*G?\d*|"
        r"Kirin\s\d[\w]*(?:[^\S\n](?!RAM|Storage|Battery|Display|Charging|OS|Screen|Memory)[A-Za-z0-9]+){0,2}|"
        r"Unisoc\s[A-Za-z0-9]+(?:[^\S\n](?!RAM|Storage|Battery|Display|Charging|OS|Screen|Memory)[A-Za-z0-9]+){0,2})",
        re.IGNORECASE,
    ), 1),

    # RAM
    ("RAM", re.compile(
        r"(?:оперативная\s+память|озу|ram|lpddr\d?)\s*[:\-–—]?\s*"
        r"(\d{1,3}\s*(?:гб|gb)(?:\s*(?:lpddr\d\w?|ddr\d\w?))?)",
        re.IGNORECASE,
    ), 1),
    ("RAM", re.compile(
        r"(\d{1,3}\s*(?:гб|gb))\s+(?:озу|ram|оперативн)",
        re.IGNORECASE,
    ), 1),

    # Storage
    ("storage", re.compile(
        r"(?:встроенн\w*\s+памят\w*|пзу|storage|rom|внутренн\w+\s+памят\w*)\s*[:\-\u2013\u2014]?\s*"
        r"(\d{2,4}\s*(?:гб|gb|тб|tb)(?:\s*(?:ufs|emmc|nvme|ssd)[\s\d.]*)?)",
        re.IGNORECASE,
    ), 1),
    # Reverse storage: "128 GB встроенной памяти"
    ("storage", re.compile(
        r"(\d{2,4}\s*(?:гб|gb|тб|tb))\s+(?:встроенн|внутренн|пзу|storage|rom|накопител)",
        re.IGNORECASE,
    ), 1),

    # Battery
    ("battery", re.compile(
        r"(?:аккумулятор\w*|батаре\w*|battery|ёмкост\w*)\s*[:\-\u2013\u2014]?\s*"
        r"(\d{3,5}\s*(?:мач|мА·ч|mah))",
        re.IGNORECASE,
    ), 1),
    ("battery", re.compile(
        r"(\d{3,5})\s*(?:мач|мА·ч|mah)\b",
        re.IGNORECASE,
    ), 0),

    # Display
    ("display", re.compile(
        r"(?:экран\w*|диспле\w*|display|screen)\s*[:\-\u2013\u2014]?\s*"
        r"(\d[\d.]+\s*(?:дюйм\w*|inch|\")"
        r"(?:\s*[,;]?\s*(?:amoled|oled|ips|lcd|ltpo|super\s*amoled|dynamic\s*amoled))?)",
        re.IGNORECASE,
    ), 1),

    # Display type (standalone — from table normalization)
    ("display_type", re.compile(
        r"(?:display\s+type|тип\s+(?:экрана|дисплея))\s*[:\-–—]\s*"
        r"((?:Super\s+)?AMOLED|Dynamic\s+AMOLED|IPS(?:\s+LCD)?|OLED|LCD|LTPO"
        r"(?:[,\s]+\d{2,3}\s*Hz)?(?:[,\s]+\d+\s*nits(?:\s*\([^)]*\))?)?)",
        re.IGNORECASE,
    ), 1),

    # Display size (from table normalization: "Display Size: 6.4 inches")
    ("display_size", re.compile(
        r"(?:display\s+size|размер\s+(?:экрана|дисплея))\s*[:\-–—]\s*"
        r"(\d[\d.]+\s*(?:inches|inch|дюйм\w*|\"))",
        re.IGNORECASE,
    ), 1),

    # Camera (main)
    # NOTE: standalone "камера" requires negative lookbehind to avoid
    # matching "фронтальная камера" (which is front camera, not main).
    ("main camera", re.compile(
        r"(?:основная\s+камера|main\s+camera|rear\s+camera|(?<!\w)(?<!фронтальная\s)камера)\s*[:\-–—]?\s*"
        r"((?:(?:Dual|Triple|Quad|Penta|Single)\s*)?(?:\d{1,3}\s*(?:мп|mp)\s*(?:,\s*[^+\n]{0,60})?"
        r"(?:\s*\+\s*\d{1,3}\s*(?:мп|mp)\s*(?:,\s*[^+\n]{0,60})?)*))",
        re.IGNORECASE,
    ), 1),

    # Main camera — from table normalization: "Main Camera Dual: 50 MP, ..."
    ("main camera", re.compile(
        r"(?:main\s+camera)\s+(?:dual|triple|quad|penta|single)\s*[:\-–—]\s*"
        r"(\d{1,3}\s*(?:мп|mp)[^\n]{0,80})",
        re.IGNORECASE,
    ), 1),

    # Selfie / front camera
    ("front camera", re.compile(
        r"(?:selfie\s+camera|фронтальная\s+камера|front\s+camera)\s*"
        r"(?:single|dual)?\s*[:\-–—]?\s*"
        r"(\d{1,3}\s*(?:мп|mp)[^\n]{0,80})",
        re.IGNORECASE,
    ), 1),

    # OS
    ("OS", re.compile(
        r"(?:операционная\s+система|ос|os|runs|based\s+on)\s*[:\-–—]?\s*"
        r"(Android\s*\d[\d.]*|iOS\s*\d[\d.]*|HarmonyOS\s*[\d.]*"
        r"|Windows\s*\d+|MIUI\s*\d[\d.]*|One\s*UI\s*[\d.]*"
        r"|ColorOS\s*[\d.]*|OxygenOS\s*[\d.]*)",
        re.IGNORECASE,
    ), 1),

    # Charging
    ("charging", re.compile(
        r"(?:зарядка|charging|быстрая\s+зарядка|fast\s+charg\w*)\s*[:\-–—]?\s*"
        r"(\d{1,3}\s*(?:вт|w|ватт))",
        re.IGNORECASE,
    ), 1),

    # Refresh rate
    ("refresh rate", re.compile(
        r"(?:частота\s+обновления|refresh\s+rate)\s*[:\-–—]?\s*"
        r"(\d{2,3}\s*(?:гц|hz))",
        re.IGNORECASE,
    ), 1),

    # Resolution
    ("resolution", re.compile(
        r"(?:разрешение|resolution)\s*[:\-–—]?\s*"
        r"(\d{3,4}\s*[×xXхХ]\s*\d{3,4})",
        re.IGNORECASE,
    ), 1),

    # Weight
    ("weight", re.compile(
        r"(?:вес|масса|weight)\s*[:\-–—]?\s*"
        r"(\d{2,3}\s*(?:г|гр|грамм|g|grams?))",
        re.IGNORECASE,
    ), 1),

    # Dimensions
    ("dimensions", re.compile(
        r"(?:размеры|dimensions|габарит)\s*[:\-–—]?\s*"
        r"(\d[\d.]+\s*[×xXхХ]\s*\d[\d.]+\s*[×xXхХ]\s*\d[\d.]+\s*(?:мм|mm)?)",
        re.IGNORECASE,
    ), 1),

    # Protection
    ("protection", re.compile(
        r"(?:защита|protection|rating)\s*[:\-–—]?\s*"
        r"(IP\d{2}\w*|Gorilla\s*Glass\s*\w*|MIL-STD[\w-]+)",
        re.IGNORECASE,
    ), 1),

    # Price
    ("price", re.compile(
        r"(?:цена|стоимость|price|от|from)\s*[:\-–—]?\s*"
        r"(\d[\d\s,.]+\s*(?:руб|₽|\$|€|usd|eur))",
        re.IGNORECASE,
    ), 1),

    # GPU (desktop/laptop)
    ("GPU", re.compile(
        r"(?:видеокарта|gpu|graphics)\s*[:\-–—]?\s*"
        r"((?:NVIDIA\s+)?(?:GeForce\s+)?(?:RTX|GTX)\s*\d{3,5}\w*|"
        r"(?:AMD\s+)?Radeon\s+RX\s*\d{3,5}\w*|"
        r"Intel\s+(?:UHD|Iris\s+Xe|Arc\s+A?)\s*\d*|"
        r"Adreno\s+\d+|Mali[\s-]*G\d+\w*)",
        re.IGNORECASE,
    ), 1),
    # ── GPU / Desktop Hardware Spec Patterns ──

    # GPU architecture / chip
    ("GPU chip", re.compile(
        r"(?:gpu\s+(?:chip|\u0447\u0438\u043f)|architecture|\u0430\u0440\u0445\u0438\u0442\u0435\u043a\u0442\u0443\u0440\u0430|\u0433\u0440\u0430\u0444\u0438\u0447\u0435\u0441\u043a\u0438\u0439\s+\u043f\u0440\u043e\u0446\u0435\u0441\u0441\u043e\u0440)\s*[:\-\u2013\u2014]?\s*"
        r"((?:NVIDIA\s+)?G[A-Z]\d{2,3}\w*|(?:AMD\s+)?Navi\s*\d+\w*|AD\d{2,3}\w*)",
        re.IGNORECASE,
    ), 1),

    # CUDA cores / Stream processors / Shaders
    ("CUDA cores", re.compile(
        r"(?:cuda\s+cores?|\u044f\u0434\u0435\u0440\s+cuda|cuda\s+\u044f\u0434\u0435\u0440|shaders?|stream\s+processors?)\s*[:\-\u2013\u2014]?\s*"
        r"(\d{3,6})",
        re.IGNORECASE,
    ), 1),
    # Reverse: "5888 CUDA cores"
    ("CUDA cores", re.compile(
        r"(\d{3,6})\s+(?:cuda\s+cores?|\u044f\u0434\u0435\u0440\s+cuda|cuda|stream\s+proc|shaders?)",
        re.IGNORECASE,
    ), 1),

    # VRAM / Video memory
    ("VRAM", re.compile(
        r"(?:vram|\u0432\u0438\u0434\u0435\u043e\u043f\u0430\u043c\u044f\u0442\u044c|video\s+memory|memory\s+size|\u043f\u0430\u043c\u044f\u0442\u044c\s+\u0432\u0438\u0434\u0435\u043e\u043a\u0430\u0440\u0442\u044b)\s*[:\-\u2013\u2014]?\s*"
        r"(\d{1,3}\s*(?:\u0433\u0431|gb)(?:\s*(?:gddr\d\w?|hbm\d?\w?))?)",
        re.IGNORECASE,
    ), 1),
    # Reverse: "8 GB GDDR6"
    ("VRAM", re.compile(
        r"(\d{1,3}\s*(?:\u0433\u0431|gb)\s*(?:gddr\d\w?|hbm\d?\w?))",
        re.IGNORECASE,
    ), 1),

    # Memory bus width
    ("memory bus", re.compile(
        r"(?:memory\s+bus|\u0448\u0438\u043d\u0430\s+\u043f\u0430\u043c\u044f\u0442\u0438|bus\s+width|\u0448\u0438\u0440\u0438\u043d\u0430\s+\u0448\u0438\u043d\u044b)\s*[:\-\u2013\u2014]?\s*"
        r"(\d{2,4}[- ]?(?:\u0431\u0438\u0442|bit))",
        re.IGNORECASE,
    ), 1),
    # Reverse: "256-bit"
    ("memory bus", re.compile(
        r"(\d{2,4})[- ]?(?:\u0431\u0438\u0442|bit)\b",
        re.IGNORECASE,
    ), 0),

    # Memory type (standalone: GDDR6X, HBM2e)
    ("memory type", re.compile(
        r"(?:memory\s+type|\u0442\u0438\u043f\s+\u043f\u0430\u043c\u044f\u0442\u0438)\s*[:\-\u2013\u2014]?\s*"
        r"(GDDR\d\w*|HBM\d?\w*|DDR\d\w*)",
        re.IGNORECASE,
    ), 1),

    # Boost clock
    ("boost clock", re.compile(
        r"(?:boost\s+clock|\u0431\u0443\u0441\u0442\s+\u0447\u0430\u0441\u0442\u043e\u0442\u0430|\u0447\u0430\u0441\u0442\u043e\u0442\u0430\s+boost|turbo\s+clock)\s*[:\-\u2013\u2014]?\s*"
        r"(\d{3,5}\s*(?:\u043c\u0433\u0446|mhz|\u0433\u0433\u0446|ghz))",
        re.IGNORECASE,
    ), 1),

    # Base clock
    ("base clock", re.compile(
        r"(?:base\s+clock|\u0431\u0430\u0437\u043e\u0432\u0430\u044f\s+\u0447\u0430\u0441\u0442\u043e\u0442\u0430|\u0447\u0430\u0441\u0442\u043e\u0442\u0430\s+base)\s*[:\-\u2013\u2014]?\s*"
        r"(\d{3,5}\s*(?:\u043c\u0433\u0446|mhz|\u0433\u0433\u0446|ghz))",
        re.IGNORECASE,
    ), 1),

    # TDP / Power
    ("TDP", re.compile(
        r"(?:tdp|power|\u044d\u043d\u0435\u0440\u0433\u043e\u043f\u043e\u0442\u0440\u0435\u0431\u043b\u0435\u043d\u0438\u0435|power\s+consumption|\u043c\u043e\u0449\u043d\u043e\u0441\u0442\u044c)\s*[:\-\u2013\u2014]?\s*"
        r"(\d{2,4}\s*(?:\u0432\u0442|w|watts?))",
        re.IGNORECASE,
    ), 1),

    # Interface (PCIe)
    ("interface", re.compile(
        r"(?:interface|\u0438\u043d\u0442\u0435\u0440\u0444\u0435\u0439\u0441|bus\s+interface)\s*[:\-\u2013\u2014]?\s*"
        r"(PCIe\s*\d[.\d]*\s*x\d+)",
        re.IGNORECASE,
    ), 1),
    # Standalone "PCIe 4.0 x16"
    ("interface", re.compile(
        r"\b(PCIe\s*\d[.\d]*\s*x\d+)\b",
        re.IGNORECASE,
    ), 1),

    # Display outputs (HDMI, DisplayPort)
    ("outputs", re.compile(
        r"(?:outputs?|\u0440\u0430\u0437\u044a\u0451\u043c\u044b|\u0432\u044b\u0445\u043e\u0434\u044b|ports?|display\s+outputs?)\s*[:\-\u2013\u2014]?\s*"
        r"((?:\d\s*[×xXхХ]\s*)?(?:HDMI|DisplayPort|DP|DVI|VGA|USB[- ]?C)(?:[\s,+]+(?:\d\s*[\u00d7xXхХ]\s*)?(?:HDMI|DisplayPort|DP|DVI|USB[- ]?C))*(?:\s*\d[.\d]*)?)",
        re.IGNORECASE,
    ), 1),

    # RT cores
    ("RT cores", re.compile(
        r"(?:rt\s+cores?|ray\s+tracing\s+cores?)\s*[:\-\u2013\u2014]?\s*"
        r"(\d{1,3})",
        re.IGNORECASE,
    ), 1),

    # Tensor cores
    ("Tensor cores", re.compile(
        r"(?:tensor\s+cores?)\s*[:\-\u2013\u2014]?\s*"
        r"(\d{1,3})",
        re.IGNORECASE,
    ), 1),

    # Memory bandwidth
    ("memory bandwidth", re.compile(
        r"(?:memory\s+bandwidth|\u043f\u0440\u043e\u043f\u0443\u0441\u043a\u043d\u0430\u044f\s+\u0441\u043f\u043e\u0441\u043e\u0431\u043d\u043e\u0441\u0442\u044c|bandwidth)\s*[:\-\u2013\u2014]?\s*"
        r"(\d[\d.]*\s*(?:GB/s|\u0413\u0411/\u0441))",
        re.IGNORECASE,
    ), 1),]

# Key-value pair pattern:  "Label: Value" or "Label — Value"
_KV_RE = re.compile(
    r"^[\s•\-*]*([A-ZА-ЯЁa-zа-яё][\w\s]{2,30}?)\s*[:\-–—]\s*(.{3,100})$",
    re.MULTILINE,
)


# ═══════════════════════════════════════════════════
#  Value Validators — sanity checks per predicate
# ═══════════════════════════════════════════════════

def _extract_number(text: str) -> Optional[float]:
    """Extract first numeric value from text."""
    m = re.search(r'(\d[\d.,]*)\s*', text)
    if m:
        try:
            return float(m.group(1).replace(',', '.').replace(' ', ''))
        except ValueError:
            return None
    return None


def _validate_display(value: str) -> bool:
    """Display size: 3.0–15.0 inches.  Reject 'см'/'cm'."""
    low = value.lower()
    # Reject centimeters — phones are measured in inches
    if re.search(r'\bсм\b|\bcm\b', low):
        return False
    n = _extract_number(low)
    if n is None:
        return True  # non-numeric display descriptions are OK (e.g. "Super AMOLED")
    return 3.0 <= n <= 15.0


def _validate_display_size(value: str) -> bool:
    """Display size: 3.0–15.0 inches."""
    low = value.lower()
    if re.search(r'\bсм\b|\bcm\b', low):
        return False
    n = _extract_number(low)
    return n is None or 3.0 <= n <= 15.0


def _validate_battery(value: str) -> bool:
    """Battery: 500–10000 mAh."""
    n = _extract_number(value)
    return n is None or 500 <= n <= 10000


def _validate_ram(value: str) -> bool:
    """RAM: 1–64 GB (common values)."""
    n = _extract_number(value)
    if n is None:
        return True
    # Must be a reasonable phone/laptop RAM size
    return 1 <= n <= 128


def _validate_storage(value: str) -> bool:
    """Storage: 8 GB – 4 TB."""
    n = _extract_number(value)
    if n is None:
        return True
    low = value.lower()
    if 'тб' in low or 'tb' in low:
        return 0.5 <= n <= 16
    return 8 <= n <= 4096


def _validate_resolution(value: str) -> bool:
    """Resolution: both dimensions must be 100–10000."""
    m = re.search(r'(\d{3,5})\s*[×xXхХ]\s*(\d{3,5})', value)
    if not m:
        return True
    w, h = int(m.group(1)), int(m.group(2))
    return 100 <= w <= 10000 and 100 <= h <= 10000


def _validate_weight(value: str) -> bool:
    """Weight: 50–5000 g."""
    n = _extract_number(value)
    return n is None or 50 <= n <= 5000


def _validate_charging(value: str) -> bool:
    """Charging: 5–300 W."""
    n = _extract_number(value)
    return n is None or 5 <= n <= 300


def _validate_refresh_rate(value: str) -> bool:
    """Refresh rate: 30–240 Hz."""
    n = _extract_number(value)
    return n is None or 30 <= n <= 240


def _validate_price(value: str) -> bool:
    """Price must be > 0."""
    n = _extract_number(value)
    return n is None or n > 0


# Map predicate → validator function
_SPEC_VALIDATORS: Dict[str, callable] = {
    "display": _validate_display,
    "display_size": _validate_display_size,
    "display_type": lambda v: bool(v.strip()),
    "battery": _validate_battery,
    "RAM": _validate_ram,
    "storage": _validate_storage,
    "resolution": _validate_resolution,
    "weight": _validate_weight,
    "charging": _validate_charging,
    "refresh rate": _validate_refresh_rate,
    "price": _validate_price,
}

# Predicates that are inherently single-valued for one device.
# The extractor keeps max _MAX_PER_PREDICATE values for these.
_SINGLE_VALUE_PREDS: frozenset = frozenset({
    "processor", "battery", "RAM", "storage", "display",
    "display_size", "display_type", "OS", "charging",
    "refresh rate", "resolution", "weight", "dimensions",
    "protection", "price", "GPU",
    # GPU hardware specs
    "GPU chip", "CUDA cores", "VRAM", "memory bus", "memory type",
    "boost clock", "base clock", "TDP", "interface", "outputs",
    "RT cores", "Tensor cores", "memory bandwidth",
})
_MAX_PER_SINGLE_PRED = 2  # keep at most 2 per single-value predicate per text block

# Predicates that share a single per-predicate counter.
# E.g. display / display_size / display_type are all "screen specs" —
# a device has ONE screen, so total extractions should be limited.
_PRED_GROUP: Dict[str, str] = {
    "display": "_display_group",
    "display_size": "_display_group",
    "display_type": "_display_group",
}


# Map raw KV labels (lowercase) → canonical predicate name.
# Used to apply validation + per-predicate limits to Strategy 2 (KV pairs).
_KV_LABEL_MAP: Dict[str, str] = {
    # Display
    "экран": "display", "дисплей": "display", "display": "display",
    "screen": "display", "диагональ экрана": "display_size",
    "display size": "display_size", "размер экрана": "display_size",
    "тип экрана": "display_type", "тип дисплея": "display_type",
    "display type": "display_type",
    # Processor
    "процессор": "processor", "чипсет": "processor", "chipset": "processor",
    "soc": "processor", "processor": "processor", "cpu": "processor",
    # RAM
    "озу": "RAM", "оперативная память": "RAM", "ram": "RAM",
    # Storage
    "пзу": "storage", "встроенная память": "storage", "storage": "storage",
    "rom": "storage", "внутренняя память": "storage",
    "накопитель": "storage", "память": "storage", "internal": "storage",
    # Battery
    "аккумулятор": "battery", "батарея": "battery", "battery": "battery",
    "ёмкость аккумулятора": "battery",
    # Camera
    "основная камера": "main camera", "камера": "main camera",
    "main camera": "main camera", "rear camera": "main camera",
    "фронтальная камера": "front camera", "front camera": "front camera",
    "selfie camera": "front camera",
    # Camera KV variants (e.g. "Main Camera Dual: 50 MP")
    "main camera dual": "main camera", "main camera triple": "main camera",
    "main camera quad": "main camera", "selfie camera single": "front camera",
    "selfie camera dual": "front camera",
    # OS
    "операционная система": "OS", "ос": "OS", "os": "OS",
    # Other
    "зарядка": "charging", "charging": "charging",
    "быстрая зарядка": "charging", "fast charging": "charging",
    "частота обновления": "refresh rate", "refresh rate": "refresh rate",
    "разрешение": "resolution", "resolution": "resolution",
    "разрешение экрана": "resolution",
    "вес": "weight", "масса": "weight", "weight": "weight",
    "размеры": "dimensions", "dimensions": "dimensions",
    "габариты": "dimensions",
    "защита": "protection", "protection": "protection",
    "цена": "price", "стоимость": "price", "price": "price",
    "видеокарта": "GPU", "gpu": "GPU", "graphics": "GPU",
    # GPU hardware
    "cuda cores": "CUDA cores", "cuda ядер": "CUDA cores",
    "ядер cuda": "CUDA cores", "shaders": "CUDA cores",
    "stream processors": "CUDA cores",
    "vram": "VRAM", "видеопамять": "VRAM",
    "video memory": "VRAM", "memory size": "VRAM",
    "память видеокарты": "VRAM",
    "memory bus": "memory bus", "шина памяти": "memory bus",
    "bus width": "memory bus", "ширина шины": "memory bus",
    "memory type": "memory type", "тип памяти": "memory type",
    "boost clock": "boost clock", "буст частота": "boost clock",
    "base clock": "base clock", "базовая частота": "base clock",
    "tdp": "TDP", "энергопотребление": "TDP",
    "power consumption": "TDP", "power": "TDP",
    "interface": "interface", "интерфейс": "interface",
    "bus interface": "interface",
    "outputs": "outputs", "разъёмы": "outputs",
    "выходы": "outputs", "display outputs": "outputs",
    "rt cores": "RT cores", "tensor cores": "Tensor cores",
    "memory bandwidth": "memory bandwidth",
    "пропускная способность": "memory bandwidth",
}


# ═══════════════════════════════════════════════════
#  GSMarena / Structured Table Normalizer
# ═══════════════════════════════════════════════════

# GSMarena spec tables are rendered as:
#   Category \n Label \n Value
# e.g. "Display \n Type \n Super AMOLED, 90Hz"
# We need to collapse these into "Display Type: Super AMOLED, 90Hz"
# so our KV and spec patterns can match them.

# Known section headers (GSMarena-style)
_TABLE_HEADERS = frozenset({
    "network", "body", "display", "platform", "memory", "main camera",
    "selfie camera", "sound", "comms", "features", "battery", "misc",
    "tests", "sar", "sar eu",
})

# Known sub-labels that follow section headers
_TABLE_SUBLABELS = frozenset({
    "type", "size", "resolution", "protection",
    "os", "chipset", "cpu", "gpu",
    "card slot", "internal",
    "single", "dual", "triple", "quad", "penta",
    "features", "video",
    "loudspeaker", "3.5mm jack",
    "wlan", "bluetooth", "positioning", "nfc", "infrared port",
    "radio", "usb",
    "sensors",
    "charging", "colors", "models", "price",
    "technology", "2g bands", "3g bands", "4g bands", "5g bands",
    "speed",
    "dimensions", "weight", "build", "sim",
})


def _normalize_table_text(text: str) -> str:
    """Normalize GSMarena-style structured table text into KV pairs.

    Converts multi-line table cells like:
        Display \\n Type \\n Super AMOLED, 90Hz
    into:
        display_type: Super AMOLED, 90Hz

    This runs BEFORE regex spec patterns, so the normalized text
    can be matched by existing pattern rules.
    """
    lines = text.split("\n")
    normalized_lines: list[str] = []
    current_section = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            normalized_lines.append(line)
            continue

        low = stripped.lower()

        # Check if this is a section header
        if low in _TABLE_HEADERS:
            current_section = stripped
            continue

        # Check if this is a sub-label under a section
        if low in _TABLE_SUBLABELS and current_section:
            # The next content on this same line (after any whitespace)
            # or the rest of the line is the value
            continue  # value will be picked up in the next iteration

        # If we have a section header, build a KV pair
        # Try to detect "Label \n Value" pattern within a single line
        # (from cells joined with \n)
        # This line might be just a value — attach to section or emit as-is
        if current_section:
            normalized_lines.append(f"{current_section}: {stripped}")
            current_section = ""  # Reset after attaching
        else:
            normalized_lines.append(line)

    return "\n".join(normalized_lines)


def _collapse_multiline_cells(text: str) -> str:
    """Collapse GSMarena multi-line cells separated by ' \\n '.

    GSMarena tables produce: 'Label \\n Sub-label \\n Value'
    all on one visual line.  We split on real newlines, then for each
    line look for the cell-separator pattern: ' \\n ' within the text
    between newlines.

    Actually, the HTML cleaner produces literal newlines between cells,
    so we need to match line sequences like:
        "Display"
        " Type"
        " Super AMOLED, 90Hz, 1000 nits (peak)"
    """
    lines = text.split("\n")
    result_lines: list[str] = []
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()
        low = stripped.lower()

        # Check if current line is a table section header
        if low in _TABLE_HEADERS:
            section = stripped
            # Consume following indented lines (sub-label + value groups)
            i += 1
            while i < len(lines):
                next_stripped = lines[i].strip()
                next_low = next_stripped.lower()
                if not next_stripped:
                    i += 1
                    continue
                # If next line is a known sub-label, read the value after it
                if next_low in _TABLE_SUBLABELS:
                    sublabel = next_stripped
                    i += 1
                    # Collect value lines until next sublabel or section
                    value_parts = []
                    while i < len(lines):
                        val_stripped = lines[i].strip()
                        val_low = val_stripped.lower()
                        if (val_low in _TABLE_HEADERS
                                or val_low in _TABLE_SUBLABELS
                                or not val_stripped):
                            break
                        value_parts.append(val_stripped)
                        i += 1
                    if value_parts:
                        value = " ".join(value_parts)
                        result_lines.append(
                            f"{section} {sublabel}: {value}"
                        )
                # If next line is another section header, stop
                elif next_low in _TABLE_HEADERS:
                    break
                else:
                    # value directly under section without sublabel
                    result_lines.append(f"{section}: {next_stripped}")
                    i += 1
        else:
            result_lines.append(lines[i])
            i += 1

    return "\n".join(result_lines)


class FactExtractor:
    """
    Extracts structured facts from passages.

    Usage:
        ext = FactExtractor()
        facts = ext.extract_from_passages(passages, subject="Realme 10")
    """

    def extract_from_passages(
        self,
        passages: List[Passage],
        subject: str = "",
    ) -> List[Fact]:
        """
        Extract all facts from a list of passages.

        Args:
            passages:  Ranked passages.
            subject:   Primary topic entity (e.g. "Realme 10").

        Returns:
            List of Fact objects (may contain duplicates — aggregator merges them).
        """
        all_facts: List[Fact] = []
        for passage in passages:
            facts = self._extract_from_text(
                passage.text,
                source_url=passage.source_url,
                subject=subject,
            )
            all_facts.extend(facts)
        return all_facts

    def _extract_from_text(
        self,
        text: str,
        source_url: str = "",
        subject: str = "",
    ) -> List[Fact]:
        """Extract facts from a single text block."""
        facts: List[Fact] = []
        if not text:
            return facts

        subj = subject or "unknown"

        # Pre-process: collapse GSMarena-style table cells into KV pairs
        normalized = _collapse_multiline_cells(text)

        # Strategy 1: Regex spec patterns — run on BOTH original and normalized
        seen_values: set[str] = set()
        pred_count: Dict[str, int] = {}   # per-predicate counter
        for source_text in (normalized, text):
            for predicate, pattern, group_idx in _SPEC_PATTERNS:
                for m in pattern.finditer(source_text):
                    try:
                        value = m.group(group_idx).strip() if group_idx > 0 else m.group(0).strip()
                    except IndexError:
                        value = m.group(0).strip()
                    nv = _normalize_value(value)
                    dedup_key = f"{predicate}|{nv.lower()}"
                    if not nv or len(nv) <= 1 or dedup_key in seen_values:
                        continue

                    # ── Value validation: reject out-of-range garbage ──
                    validator = _SPEC_VALIDATORS.get(predicate)
                    if validator and not validator(nv):
                        logger.debug(
                            "FactExtractor: rejected %s=%r (failed validation)",
                            predicate, nv,
                        )
                        continue

                    # ── Per-predicate limit for single-value specs ──
                    if predicate in _SINGLE_VALUE_PREDS:
                        group_key = _PRED_GROUP.get(predicate, predicate)
                        cnt = pred_count.get(group_key, 0)
                        if cnt >= _MAX_PER_SINGLE_PRED:
                            logger.debug(
                                "FactExtractor: skipped %s=%r (limit %d reached)",
                                predicate, nv, _MAX_PER_SINGLE_PRED,
                            )
                            continue
                        pred_count[group_key] = cnt + 1

                    seen_values.add(dedup_key)
                    facts.append(Fact(
                        subject=subj,
                        predicate=predicate,
                        object_value=nv,
                        sources=[source_url] if source_url else [],
                        confidence=0.70,
                    ))

        # Strategy 2: Key-value pairs (on normalized text only)
        # Apply the same validation + per-predicate limits as Strategy 1.
        for m in _KV_RE.finditer(normalized):
            label = m.group(1).strip()
            value = m.group(2).strip()
            if not _is_meaningful_kv(label, value):
                continue
            nv = _normalize_value(value)
            # Map KV label to canonical predicate for validation/limits
            canonical = _KV_LABEL_MAP.get(label.lower().strip())
            dedup_key = f"{(canonical or label).lower()}|{nv.lower()}"
            if dedup_key in seen_values:
                continue

            # Value validation using canonical predicate
            if canonical:
                validator = _SPEC_VALIDATORS.get(canonical)
                if validator and not validator(nv):
                    logger.debug(
                        "FactExtractor KV: rejected %s(%s)=%r (failed validation)",
                        label, canonical, nv,
                    )
                    continue
                # Per-predicate limit (shared counter with Strategy 1)
                if canonical in _SINGLE_VALUE_PREDS:
                    group_key = _PRED_GROUP.get(canonical, canonical)
                    cnt = pred_count.get(group_key, 0)
                    if cnt >= _MAX_PER_SINGLE_PRED:
                        logger.debug(
                            "FactExtractor KV: skipped %s(%s)=%r (limit reached)",
                            label, canonical, nv,
                        )
                        continue
                    pred_count[group_key] = cnt + 1

            seen_values.add(dedup_key)
            facts.append(Fact(
                subject=subj,
                predicate=canonical or label,
                object_value=nv,
                sources=[source_url] if source_url else [],
                confidence=0.50,
            ))

        return facts


# ── Helpers ──

def _normalize_value(value: str) -> str:
    """Clean up extracted value."""
    v = re.sub(r"\s+", " ", value).strip()
    v = v.rstrip(".,;:")
    # Strip trailing partial Russian words that leak from reverse patterns
    # e.g. "6 GB оперативн" → "6 GB", "128 GB встроенн" → "128 GB"
    v = re.sub(
        r"\s+(?:оперативн\w*|встроенн\w*|внутренн\w*|накопител\w*"
        r"|памят\w*|дисплей\w*|экран\w*)\s*$",
        "", v, flags=re.IGNORECASE,
    )
    return v


def _is_meaningful_kv(label: str, value: str) -> bool:
    """Filter out garbage KV pairs."""
    if len(label) < 2 or len(value) < 2:
        return False
    if len(value) > 100:
        return False
    # ── URL / link noise ──
    # Labels or values that are URLs (from search-result summaries)
    _url_re = re.compile(r'https?://', re.IGNORECASE)
    if _url_re.search(label) or _url_re.search(value):
        return False
    if label.lower().strip().startswith("www.") or value.lower().strip().startswith("www."):
        return False
    # ── Site title noise (e.g. "Обзор смартфона Realme 10: iXBT.com") ──
    # Value that looks like a domain name
    if re.match(r'^[a-zA-Z0-9-]+\.\w{2,6}$', value.strip()):
        return False
    # Reject labels that captured across newlines (regex \s includes \n)
    if '\n' in label or '\r' in label:
        return False
    # Skip navigation-like labels
    noise = {"menu", "home", "skip", "search", "login", "sign", "cookie",
             "navigation", "privacy", "terms", "copyright", "share",
             "результаты поиска", "результат поиска", "подробности",
             "источник", "источники", "ссылка", "ссылки"}
    if label.lower() in noise:
        return False
    # Skip entity-name-as-label (e.g. "Realme 10: Specifications")
    # Heuristic: if label has digits and looks like a product name, skip
    if re.match(r'^[A-Za-zА-Яа-я]+\s+\d', label):
        return False
    # Skip bot-protection / error page artifacts
    bot_noise = {"ray id", "your ip", "cloudflare", "captcha", "hcaptcha",
                 "recaptcha", "error", "error code", "performance",
                 "security by", "security", "challenge", "bot",
                 "page not found", "404", "403", "forbidden",
                 "access denied", "blocked", "ddos protection"}
    if label.lower().strip() in bot_noise:
        return False
    # Skip if value looks like a Cloudflare Ray ID (hex hash)
    if label.lower().strip() == "ray" and re.match(r'^[0-9a-f]{10,}$', value.strip()):
        return False
    return True


# ═══════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════

_extractor: FactExtractor | None = None


def get_fact_extractor() -> FactExtractor:
    global _extractor
    if _extractor is None:
        _extractor = FactExtractor()
    return _extractor
