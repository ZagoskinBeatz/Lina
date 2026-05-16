# -*- coding: utf-8 -*-
"""
Lina Core — Entity Parser (Phase 28).

Извлекает именованные сущности из пользовательских запросов
и веб-текстов для улучшения поиска, ранжирования и верификации фактов.

Сущности:
  DEVICE   — устройство (смартфон, ноутбук, планшет)
  CPU      — процессор / SoC
  GPU      — видеокарта
  RAM      — объём оперативной памяти
  STORAGE  — объём хранилища
  DISPLAY  — экран (размер, тип)
  BATTERY  — аккумулятор
  OS       — операционная система
  BRAND    — бренд (производитель)
  MODEL    — номер модели
  PRICE    — цена
  PERSON   — персона
  PLACE    — место

Архитектура:
  Regex-based NER — работает мгновенно, без ML-зависимостей.
  Оптимизирован для RU/EN hardware-запросов.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional

logger = logging.getLogger("lina.core.entity_parser")


# ═══════════════════════════════════════════════════════════════════════════════
#  Entity Types
# ═══════════════════════════════════════════════════════════════════════════════

class EntityType(Enum):
    DEVICE = "device"
    CPU = "cpu"
    GPU = "gpu"
    RAM = "ram"
    STORAGE = "storage"
    DISPLAY = "display"
    BATTERY = "battery"
    OS = "os"
    BRAND = "brand"
    MODEL = "model"
    PRICE = "price"
    PERSON = "person"
    PLACE = "place"
    ATTRIBUTE = "attribute"  # generic attribute request


@dataclass
class Entity:
    """Одна извлечённая сущность."""
    type: EntityType
    value: str
    span: tuple = (0, 0)      # (start, end) в исходном тексте
    confidence: float = 0.9

    def __repr__(self) -> str:
        return f"Entity({self.type.value}: {self.value!r}, conf={self.confidence:.2f})"


@dataclass
class ParsedQuery:
    """Результат разбора запроса."""
    raw_query: str
    entities: List[Entity] = field(default_factory=list)
    device: Optional[str] = None      # главное устройство
    brand: Optional[str] = None       # бренд
    attribute: Optional[str] = None   # запрашиваемый атрибут (cpu, ram, display...)

    def has(self, entity_type: EntityType) -> bool:
        return any(e.type == entity_type for e in self.entities)

    def get(self, entity_type: EntityType) -> Optional[Entity]:
        for e in self.entities:
            if e.type == entity_type:
                return e
        return None

    def get_all(self, entity_type: EntityType) -> List[Entity]:
        return [e for e in self.entities if e.type == entity_type]

    def to_dict(self) -> Dict:
        return {
            "raw_query": self.raw_query,
            "device": self.device,
            "brand": self.brand,
            "attribute": self.attribute,
            "entities": [(e.type.value, e.value) for e in self.entities],
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  Brand & Pattern Databases
# ═══════════════════════════════════════════════════════════════════════════════

# Бренды смартфонов/ноутбуков/чипсетов
_BRAND_MAP: Dict[str, str] = {
    # Смартфоны
    "samsung": "Samsung", "galaxy": "Samsung", "oneplus": "OnePlus",
    "realme": "Realme", "realm": "Realme",  # common typo
    "xiaomi": "Xiaomi", "xaomi": "Xiaomi", "сяоми": "Xiaomi",
    "redmi": "Xiaomi",
    "poco": "Xiaomi", "huawei": "Huawei", "honor": "Honor",
    "apple": "Apple", "iphone": "Apple", "ipad": "Apple",
    "oppo": "OPPO", "vivo": "Vivo", "nothing": "Nothing",
    "motorola": "Motorola", "moto": "Motorola", "nokia": "Nokia",
    "sony": "Sony", "xperia": "Sony", "pixel": "Google",
    "google pixel": "Google", "meizu": "Meizu", "zte": "ZTE",
    "tecno": "Tecno", "infinix": "Infinix", "itel": "Itel",
    # Ноутбуки
    "macbook": "Apple", "imac": "Apple", "thinkpad": "Lenovo",
    "ideapad": "Lenovo", "pavilion": "HP", "elitebook": "HP",
    "inspiron": "Dell", "latitude": "Dell", "xps": "Dell",
    "zenbook": "ASUS", "vivobook": "ASUS", "rog": "ASUS",
    "predator": "Acer", "aspire": "Acer", "nitro": "Acer",
    "legion": "Lenovo", "yoga": "Lenovo",
    # Ноутбуки — бренды
    "asus": "ASUS", "lenovo": "Lenovo", "acer": "Acer",
    "dell": "Dell", "hp": "HP", "msi": "MSI",
    # Чипсеты
    "snapdragon": "Qualcomm", "dimensity": "MediaTek",
    "exynos": "Samsung", "helio": "MediaTek", "mediatek": "MediaTek",
    "kirin": "HiSilicon", "tensor": "Google", "unisoc": "Unisoc",
    "apple a": "Apple", "apple m": "Apple",
    # GPU
    "geforce": "NVIDIA", "rtx": "NVIDIA", "gtx": "NVIDIA",
    "radeon": "AMD", "rx": "AMD",
    # CPU
    "ryzen": "AMD", "intel": "Intel", "core i": "Intel",
    "xeon": "Intel", "epyc": "AMD", "threadripper": "AMD",
}

# Алиасы-опечатки, для которых device name следует нормализовать к каноничному бренду.
# НЕ включает суб-бренды (poco, redmi, galaxy) — только именно опечатки.
_BRAND_TYPO_ALIASES: frozenset = frozenset({
    "realm",   # → Realme
    "xaomi",   # → Xiaomi
    "сяоми",   # → Xiaomi
})

# Regex для извлечения брендов (case-insensitive)
_BRAND_RE = re.compile(
    r"\b("
    + "|".join(sorted(_BRAND_MAP.keys(), key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)

# Паттерны для номера модели (iPhone 15 Pro, Galaxy S24 Ultra, RTX 4090, etc.)
_MODEL_PATTERNS = [
    # iPhone 15 Pro Max, Galaxy S24 Ultra, Pixel 9 Pro
    re.compile(
        r"\b((?:iphone|galaxy\s*s|galaxy\s*a|galaxy\s*z|pixel|redmi\s*note|"
        r"redmi|poco\s*[fxmc]|realme?\s*gt|realme?|oneplus|honor|huawei\s*"
        r"p|huawei\s*mate|oppo\s*reno|oppo\s*find|vivo\s*[xvyst]|nothing\s*"
        r"phone|moto\s*g|moto\s*edge|xperia|nokia)\s*"
        r"\d[\w\s]*?(?:pro\s*max|ultra|plus|\+|lite|neo|fe|se)?)\b",
        re.IGNORECASE,
    ),
    # MacBook Pro M3, ThinkPad X1 Carbon, XPS 15
    re.compile(
        r"\b((?:macbook|thinkpad|ideapad|pavilion|elitebook|inspiron|"
        r"latitude|xps|zenbook|vivobook|rog\s*strix|predator|aspire|"
        r"nitro|legion|yoga)\s*\S+(?:\s+\S+)?)\b",
        re.IGNORECASE,
    ),
    # RTX 4090, GTX 1660 Ti, RX 7900 XTX, Ryzen 9 7950X
    re.compile(
        r"\b((?:rtx|gtx|rx|ryzen\s*\d|core\s*i\d|xeon|epyc)\s*"
        r"\d{3,5}\s*(?:ti|xt|xtx|x|super|s|g)?)\b",
        re.IGNORECASE,
    ),
    # Snapdragon 8 Gen 3, Dimensity 9200, Helio G99, Exynos 2400
    re.compile(
        r"\b((?:snapdragon|dimensity|exynos|helio|kirin|tensor|"
        r"apple\s*[am])\s*\d[\w\s]*?(?:gen\s*\d)?)\b",
        re.IGNORECASE,
    ),
]

# Атрибуты (что именно спрашивают)
_ATTRIBUTE_MAP: Dict[str, EntityType] = {
    "процессор": EntityType.CPU, "cpu": EntityType.CPU,
    "чип": EntityType.CPU, "чипсет": EntityType.CPU,
    "soc": EntityType.CPU, "processor": EntityType.CPU,
    "видеокарт": EntityType.GPU, "gpu": EntityType.GPU,
    "график": EntityType.GPU, "graphics": EntityType.GPU,
    "оперативн": EntityType.RAM, "ram": EntityType.RAM,
    "памят": EntityType.RAM, "озу": EntityType.RAM, "memory": EntityType.RAM,
    "экран": EntityType.DISPLAY, "дисплей": EntityType.DISPLAY,
    "display": EntityType.DISPLAY, "screen": EntityType.DISPLAY,
    "аккумулятор": EntityType.BATTERY, "батаре": EntityType.BATTERY,
    "battery": EntityType.BATTERY, "ёмкость": EntityType.BATTERY,
    "хранилищ": EntityType.STORAGE, "память": EntityType.STORAGE,
    "storage": EntityType.STORAGE, "пзу": EntityType.STORAGE,
    "rom": EntityType.STORAGE,
    "ос": EntityType.OS, "операционн": EntityType.OS,
    "android": EntityType.OS, "ios": EntityType.OS,
    "windows": EntityType.OS, "linux": EntityType.OS,
    "цен": EntityType.PRICE, "стоимост": EntityType.PRICE,
    "price": EntityType.PRICE, "cost": EntityType.PRICE,
}

_ATTRIBUTE_RE = re.compile(
    r"\b(" + "|".join(sorted(_ATTRIBUTE_MAP.keys(), key=len, reverse=True)) + r")\w*\b",
    re.IGNORECASE,
)

# Паттерны для числовых спецификаций в тексте
_SPEC_PATTERNS = {
    EntityType.RAM: re.compile(
        r"\b(\d{1,3})\s*(?:гб|gb)\s*(?:озу|ram|оперативн|lpddr|ddr)",
        re.IGNORECASE,
    ),
    EntityType.STORAGE: re.compile(
        r"\b(\d{2,4})\s*(?:гб|gb|тб|tb)\s*(?:пзу|rom|storage|встроенн|памят|ssd|hdd|nvme)",
        re.IGNORECASE,
    ),
    EntityType.BATTERY: re.compile(
        r"\b(\d{3,5})\s*(?:мач|мА·ч|mah)\b",
        re.IGNORECASE,
    ),
    EntityType.DISPLAY: re.compile(
        r"\b(\d[\d.]+)\s*(?:дюйм|inch|\")\s*(?:amoled|ips|oled|lcd|ltpo|super\s*amoled)?",
        re.IGNORECASE,
    ),
    EntityType.PRICE: re.compile(
        r"\b(\d[\d\s,.]+)\s*(?:руб|₽|\$|€|usd|eur|тыс)\b",
        re.IGNORECASE,
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
#  Entity Parser
# ═══════════════════════════════════════════════════════════════════════════════

class EntityParser:
    """
    Извлекает именованные сущности из текста.

    Regex-based — мгновенная работа, без ML-зависимостей.
    Оптимизирован для hardware-запросов (RU/EN).

    Usage:
        parser = EntityParser()
        parsed = parser.parse("Realme 10 процессор")
        # parsed.device = "Realme 10"
        # parsed.brand = "Realme"
        # parsed.attribute = "cpu"
    """

    def parse(self, query: str) -> ParsedQuery:
        """Извлечь сущности из запроса пользователя."""
        result = ParsedQuery(raw_query=query)
        text = query.strip()
        if not text:
            return result

        # 1. Бренды
        for m in _BRAND_RE.finditer(text):
            raw = m.group(1).lower()
            canonical = _BRAND_MAP.get(raw, raw.title())
            result.entities.append(Entity(
                type=EntityType.BRAND,
                value=canonical,
                span=(m.start(), m.end()),
            ))
            if not result.brand:
                result.brand = canonical

        # 2. Модели (полные названия: "iPhone 15 Pro", "RTX 4090")
        for pat in _MODEL_PATTERNS:
            for m in pat.finditer(text):
                model = " ".join(m.group(1).split())  # normalize whitespace
                result.entities.append(Entity(
                    type=EntityType.MODEL,
                    value=model,
                    span=(m.start(), m.end()),
                ))
                if not result.device:
                    result.device = model

        # 3. Атрибуты (что именно спрашивают)
        for m in _ATTRIBUTE_RE.finditer(text):
            raw = m.group(1).lower()
            # Найти ближайшее совпадение в _ATTRIBUTE_MAP
            etype = None
            for key, val in _ATTRIBUTE_MAP.items():
                if raw.startswith(key) or key.startswith(raw):
                    etype = val
                    break
            if etype:
                result.entities.append(Entity(
                    type=EntityType.ATTRIBUTE,
                    value=etype.value,
                    span=(m.start(), m.end()),
                ))
                if not result.attribute:
                    result.attribute = etype.value

        # 4. Если нет device, попробовать собрать из бренда + ближайших слов
        if not result.device and result.brand:
            # Собираем все алиасы этого бренда для поиска в тексте
            aliases = [k for k, v in _BRAND_MAP.items() if v == result.brand]
            aliases.append(result.brand.lower())
            alias_pat = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
            brand_pat = re.compile(
                r"\b(" + alias_pat + r")\s+(\S+(?:\s+\S+){0,3})",
                re.IGNORECASE,
            )
            m = brand_pat.search(text)
            if m:
                candidate = m.group(0).strip()
                # Обрезаем дескрипторы
                candidate = re.sub(
                    r"\s+(характеристик\w*|процессор\w*|обзор|цен\w*|specs|review|price)\b.*",
                    "", candidate, flags=re.IGNORECASE,
                ).strip()
                if candidate and len(candidate) > len(result.brand) + 1:
                    result.device = candidate

        # 5. Нормализация device — замена опечатки бренда на каноничную форму
        #    "Realm 10" → "Realme 10", "Xaomi 14" → "Xiaomi 14"
        #    Не заменяем суб-бренды (poco → Xiaomi, redmi → Xiaomi)
        if result.device and result.brand:
            device_lower = result.device.lower()
            for alias in _BRAND_TYPO_ALIASES:
                if device_lower.startswith(alias):
                    # Проверяем, что после алиаса не идёт буква (word boundary)
                    after = device_lower[len(alias):len(alias) + 1]
                    if after and after.isalpha():
                        continue  # "realme" starts with "realm" — not a typo
                    rest = result.device[len(alias):]
                    result.device = result.brand + rest
                    break

        return result

    def extract_specs_from_text(self, text: str) -> List[Entity]:
        """Извлечь числовые спецификации из текста веб-страницы.

        Пример:
            "6 ГБ ОЗУ, 128 ГБ встроенной памяти, батарея 5000 мАч"
            → [Entity(RAM, "6 ГБ"), Entity(STORAGE, "128 ГБ"), Entity(BATTERY, "5000 мАч")]
        """
        entities = []
        for etype, pat in _SPEC_PATTERNS.items():
            for m in pat.finditer(text):
                entities.append(Entity(
                    type=etype,
                    value=m.group(0).strip(),
                    span=(m.start(), m.end()),
                    confidence=0.85,
                ))
        return entities

    def extract_from_web_text(self, text: str) -> List[Entity]:
        """Извлечь все сущности из текста веб-страницы (бренды + модели + спеки)."""
        entities = []
        # Бренды
        for m in _BRAND_RE.finditer(text):
            raw = m.group(1).lower()
            canonical = _BRAND_MAP.get(raw, raw.title())
            entities.append(Entity(
                type=EntityType.BRAND,
                value=canonical,
                span=(m.start(), m.end()),
                confidence=0.9,
            ))

        # Модели
        for pat in _MODEL_PATTERNS:
            for m in pat.finditer(text):
                model = " ".join(m.group(1).split())
                entities.append(Entity(
                    type=EntityType.MODEL,
                    value=model,
                    span=(m.start(), m.end()),
                    confidence=0.85,
                ))

        # Спецификации
        entities.extend(self.extract_specs_from_text(text))
        return entities


# ═══════════════════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════════════════

_parser: Optional[EntityParser] = None


def get_entity_parser() -> EntityParser:
    """Получить (или создать) экземпляр EntityParser."""
    global _parser
    if _parser is None:
        _parser = EntityParser()
    return _parser
