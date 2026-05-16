# -*- coding: utf-8 -*-
"""
Regex-based spec extractor for phones, laptops and GPUs.

Extracts structured specs directly from page text WITHOUT any LLM.
Supports:
  - Phones/tablets: display, processor, RAM, storage, battery, camera, OS
  - Laptops: + Wi-Fi, Bluetooth, ports, keyboard, webcam
  - GPUs (video cards): CUDA cores, VRAM, boost/base clock, TDP, memory bus
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("lina.parser.spec_extractor")

# ─── Helper: strip model prefix like "S25:" or "Galaxy S25:" ──────────────
_MODEL_PREFIX_RE = re.compile(
    r'^(?:S\d+\w*|Galaxy\s+\w+|iPhone\s*\d+\w*|Realme\s+\w+|Redmi\s+\w+)\s*:\s*',
    re.IGNORECASE,
)

def _strip_model_prefix(value: str) -> str:
    """Strip 'S25: ' style prefixes from values."""
    return _MODEL_PREFIX_RE.sub('', value).strip()


@dataclass
class DeviceSpecs:
    """Extracted device specifications."""
    device_name: str = ""
    is_laptop: bool = False       # True for laptops/notebooks
    display_size: str = ""       # e.g. "6.2 дюймов" or "16 дюймов"
    display_type: str = ""       # e.g. "Dynamic AMOLED" or "IPS"
    display_resolution: str = "" # e.g. "1080 x 2340" or "1920 x 1200"
    display_refresh: str = ""    # e.g. "120 Гц"
    display_brightness: str = "" # e.g. "2600 нит"
    processor: str = ""          # e.g. "Qualcomm Snapdragon 8 Elite" or "Intel Core i5-1335U"
    cpu_cores: str = ""          # e.g. "8"
    cpu_freq: str = ""           # e.g. "4470 МГц"
    gpu: str = ""                # e.g. "Adreno 830" or "Intel Iris Xe"
    ram: str = ""                # e.g. "12 ГБ" or "16 ГБ"
    ram_type: str = ""           # e.g. "LPDDR5X" or "DDR4"
    storage: str = ""            # e.g. "128 ГБ" or "512 ГБ SSD"
    storage_type: str = ""       # e.g. "UFS 4.0" or "NVMe SSD"
    memory_card: str = ""        # e.g. "Нет"
    battery: str = ""            # e.g. "4000 мАч" or "70 Вт·ч"
    charging: str = ""           # e.g. "25 Вт" or "65 Вт"
    charging_time: str = ""      # e.g. "1:17 ч."
    wireless_charging: str = ""  # e.g. "Да (15 Вт)"
    os: str = ""                 # e.g. "Android 15" or "Windows 11"
    ui: str = ""                 # e.g. "One UI 8.0"
    main_camera: str = ""        # e.g. "50 МП + 10 МП + 12 МП"
    selfie_camera: str = ""      # webcam for laptops
    video: str = ""              # e.g. "8K"
    dimensions: str = ""         # e.g. "146.9 x 70.5 x 7.2 мм"
    weight: str = ""             # e.g. "162 грамма" or "1.8 кг"
    water_resistance: str = ""   # e.g. "IP68"
    sim: str = ""                # e.g. "Nano-SIM + eSIM"
    nfc: str = ""                # e.g. "Да"
    colors: str = ""             # e.g. "Черный, Серебристый..."
    fingerprint: str = ""        # e.g. "Да, в дисплее"
    process_nm: str = ""         # e.g. "3 нм"
    # Laptop-specific fields
    keyboard: str = ""           # e.g. "с подсветкой"
    ports: str = ""              # e.g. "USB-C x2, USB-A, HDMI"
    wifi: str = ""               # e.g. "Wi-Fi 6 (802.11ax)"
    bluetooth: str = ""          # e.g. "Bluetooth 5.3"
    # GPU (video card) specific fields
    is_gpu: bool = False          # True for standalone video cards
    gpu_chip: str = ""            # e.g. "GA104" / "AD102"
    gpu_architecture: str = ""    # e.g. "Ampere" / "Ada Lovelace"
    cuda_cores: str = ""          # e.g. "5888"
    vram: str = ""                # e.g. "8 ГБ"
    vram_type: str = ""           # e.g. "GDDR6X"
    memory_bus: str = ""          # e.g. "256 бит"
    boost_clock: str = ""         # e.g. "1725 МГц"
    base_clock: str = ""          # e.g. "1500 МГц"
    tdp: str = ""                 # e.g. "220 Вт"
    gpu_outputs: str = ""         # e.g. "HDMI 2.1, 3x DP 1.4a"
    gpu_interface: str = ""       # e.g. "PCIe 4.0 x16"
    ray_tracing: str = ""         # e.g. "Да, RT 2nd gen"
    dlss: str = ""                # e.g. "DLSS 2.0"
    # Headphone / audio device specific fields
    is_headphone: bool = False     # True for headphones/earbuds/IEMs
    headphone_type: str = ""       # e.g. "закрытые" / "открытые" / "внутриканальные"
    driver_size: str = ""          # e.g. "40 мм"
    frequency_range: str = ""     # e.g. "20 – 20000 Гц"
    impedance: str = ""           # e.g. "32 Ом"
    sensitivity: str = ""         # e.g. "108 дБ"
    cable_length: str = ""        # e.g. "2.5 м"
    connector: str = ""           # e.g. "3.5 мм / 6.3 мм"
    microphone: str = ""          # e.g. "Да" / "Нет"
    noise_cancelling: str = ""    # e.g. "ANC" / "Нет"
    wireless: str = ""            # e.g. "Bluetooth 5.0" / "Нет"
    # Source tracking
    source_urls: List[str] = field(default_factory=list)
    confidence: float = 0.0      # 0.0–1.0 based on how many fields extracted

    @property
    def filled_count(self) -> int:
        """Count of non-empty spec fields."""
        _skip = {"device_name", "is_laptop", "is_gpu", "is_headphone", "source_urls", "confidence"}
        return sum(
            1 for f in self.__dataclass_fields__
            if f not in _skip and getattr(self, f)
        )

    def format_for_user(self) -> str:
        """Format specs as human-readable text for direct output."""
        parts = []
        if self.is_headphone:
            emoji = "🎧"
        elif self.is_gpu:
            emoji = "🎮"
        elif self.is_laptop:
            emoji = "💻"
        else:
            emoji = "📱"
        if self.device_name:
            parts.append(f"{emoji} {self.device_name}\n")

        if self.is_headphone:
            sections = [
                ("Звук", [
                    ("Тип", self.headphone_type),
                    ("Диаметр драйвера", self.driver_size),
                    ("Частотный диапазон", self.frequency_range),
                    ("Импеданс", self.impedance),
                    ("Чувствительность", self.sensitivity),
                ]),
                ("Подключение", [
                    ("Разъём", self.connector),
                    ("Длина кабеля", self.cable_length),
                    ("Беспроводное", self.wireless),
                    ("Bluetooth", self.bluetooth),
                ]),
                ("Дополнительно", [
                    ("Микрофон", self.microphone),
                    ("Шумоподавление", self.noise_cancelling),
                    ("Автономность", self.battery),
                    ("Защита", self.water_resistance),
                    ("Вес", self.weight),
                ]),
            ]
        elif self.is_gpu:
            sections = [
                ("Графический процессор", [
                    ("Чип", self.gpu_chip),
                    ("Архитектура", self.gpu_architecture),
                    ("Техпроцесс", self.process_nm),
                    ("CUDA / Потоковые проц.", self.cuda_cores),
                    ("Базовая частота", self.base_clock),
                    ("Boost частота", self.boost_clock),
                ]),
                ("Видеопамять", [
                    ("Объём", self.vram),
                    ("Тип", self.vram_type),
                    ("Шина", self.memory_bus),
                ]),
                ("Питание и охлаждение", [
                    ("TDP", self.tdp),
                ]),
                ("Интерфейсы", [
                    ("Шина", self.gpu_interface),
                    ("Видеовыходы", self.gpu_outputs),
                ]),
                ("Технологии", [
                    ("Ray Tracing", self.ray_tracing),
                    ("DLSS / FSR", self.dlss),
                ]),
                ("Корпус", [
                    ("Размеры", self.dimensions),
                    ("Вес", self.weight),
                ]),
            ]
        elif self.is_laptop:
            sections = [
                ("Дисплей", [
                    ("Тип", self.display_type),
                    ("Размер", self.display_size),
                    ("Разрешение", self.display_resolution),
                    ("Частота обновления", self.display_refresh),
                    ("Яркость", self.display_brightness),
                ]),
                ("Процессор", [
                    ("Чипсет", self.processor),
                    ("Ядра", self.cpu_cores),
                    ("Макс. частота", self.cpu_freq),
                    ("Техпроцесс", self.process_nm),
                    ("TDP", self.tdp),
                    ("GPU", self.gpu),
                ]),
                ("Память", [
                    ("ОЗУ", f"{self.ram} {self.ram_type}".strip()),
                    ("Накопитель", f"{self.storage} {self.storage_type}".strip()),
                    ("Карта памяти", self.memory_card),
                ]),
                ("Батарея", [
                    ("Ёмкость", self.battery),
                    ("Зарядка", self.charging),
                ]),
                ("Система", [
                    ("ОС", self.os),
                ]),
                ("Корпус", [
                    ("Размеры", self.dimensions),
                    ("Вес", self.weight),
                    ("Цвета", self.colors),
                ]),
                ("Подключения", [
                    ("Wi-Fi", self.wifi),
                    ("Bluetooth", self.bluetooth),
                    ("Порты", self.ports),
                ]),
                ("Другое", [
                    ("Клавиатура", self.keyboard),
                    ("Веб-камера", self.selfie_camera),
                    ("Сканер отпечатков", self.fingerprint),
                ]),
            ]
        else:
            sections = [
                ("Дисплей", [
                    ("Тип", self.display_type),
                    ("Размер", self.display_size),
                    ("Разрешение", self.display_resolution),
                    ("Частота обновления", self.display_refresh),
                    ("Яркость", self.display_brightness),
                ]),
                ("Процессор", [
                    ("Чипсет", self.processor),
                    ("Ядра", self.cpu_cores),
                    ("Макс. частота", self.cpu_freq),
                    ("Техпроцесс", self.process_nm),
                    ("TDP", self.tdp),
                    ("GPU", self.gpu),
                ]),
                ("Память", [
                    ("ОЗУ", f"{self.ram} {self.ram_type}".strip()),
                    ("Накопитель", f"{self.storage} {self.storage_type}".strip()),
                    ("Карта памяти", self.memory_card),
                ]),
                ("Батарея", [
                    ("Ёмкость", self.battery),
                    ("Зарядка", self.charging),
                    ("Время зарядки", self.charging_time),
                    ("Беспроводная", self.wireless_charging),
                ]),
                ("Камера", [
                    ("Основная", self.main_camera),
                    ("Фронтальная", self.selfie_camera),
                    ("Видео", self.video),
                ]),
                ("Система", [
                    ("ОС", self.os),
                    ("Оболочка", self.ui),
                ]),
                ("Корпус", [
                    ("Размеры", self.dimensions),
                    ("Вес", self.weight),
                    ("Защита", self.water_resistance),
                    ("Цвета", self.colors),
                ]),
                ("Связь", [
                    ("SIM", self.sim),
                    ("NFC", self.nfc),
                    ("Сканер отпечатков", self.fingerprint),
                ]),
            ]

        for section_name, fields in sections:
            field_lines = []
            for label, value in fields:
                if value:
                    field_lines.append(f"  • {label}: {value}")
            if field_lines:
                parts.append(f"\n**{section_name}**")
                parts.extend(field_lines)

        if self.source_urls:
            from urllib.parse import urlparse as _up
            _domains = []
            for _su in self.source_urls[:3]:
                try:
                    _domains.append(_up(_su).netloc or _su[:60])
                except Exception:
                    _domains.append(_su[:60])
            parts.append(f"\n📎 Источники: {', '.join(_domains)}")

        return "\n".join(parts)


# ─── Extraction patterns ──────────────────────────────────────────────────────

def _first_match(text: str, *patterns: str, flags: int = re.IGNORECASE) -> str:
    """Return first group(1) match from multiple patterns, or ''."""
    for pat in patterns:
        m = re.search(pat, text, flags)
        if m:
            return m.group(1).strip()
    return ""


def _extract_display(text: str, specs: DeviceSpecs) -> None:
    """Extract display specifications."""
    # ── Type: Dynamic AMOLED, IPS LCD, OLED, Super AMOLED, etc. ──
    specs.display_type = _first_match(
        text,
        r'(?:Тип|Type)[:\s]+((?:Dynamic\s+)?(?:Super\s+)?(?:AMOLED|OLED|LCD|IPS\s+LCD|TFT|LTPO))',
        r'\b(Dynamic\s+AMOLED(?:\s+2X)?)\b',
        r'\b(Super\s+AMOLED(?:\s+Plus)?)\b',
        r'\b(IPS\s+LCD)\b',
        r'\b(AMOLED|OLED)\b',
        r'\b(IPS)\b',
        r'\b(TN|VA|Mini[- ]?LED)\b',
    )

    # ── Size in inches or дюйм ──
    specs.display_size = _first_match(
        text,
        r'(?:Размер|Size)[:\s]*([\d.,]+\s*(?:дюйм\w*|inch\w*|"))',
        r'(?:Экран|Дисплей|Display)\s*[:\s]*\S*\s*([\d.,]+)["\u2033]',
        r'([\d.,]+)["\u2033]\s*(?:Dynamic|Super|AMOLED|OLED|IPS|LCD)',
        # "6,2-дюймовым" or "16-дюймовым" or "6.2 дюймов"
        r'([\d.,]+)[\s-]*(?:дюйм|inch)',
    )

    # ── Resolution ──
    specs.display_resolution = _first_match(
        text,
        r'(?:Разрешение|Resolution)[:\s]*(\d+\s*[xXхХ×]\s*\d+)',
        r'(\d{3,4}\s*[xXхХ×]\s*\d{3,4})\s*(?:пикс|pixel|px)',
        # Wikipedia: "(2340×1080)" or "FHD+ (2340x1080)" — inside parens
        r'\((\d{3,4}\s*[xXхХ×]\s*\d{3,4})\)',
        # Standalone resolution pattern (last resort, only near display context)
        r'(?:FHD\+?|QHD\+?|HD\+?|WUXGA|WQXGA)\s*\(?(\d{3,4}\s*[xXхХ×]\s*\d{3,4})\)?',
        # Bare resolution (common on spec pages)
        r'\b(\d{3,4}\s*[xXхХ×]\s*\d{3,4})\b',
    )

    # ── Refresh rate ──
    specs.display_refresh = _first_match(
        text,
        r'(?:Частота\s*обновления|Refresh\s*rate)[:\s]*([\d]+\s*(?:Гц|Hz))',
        r'([\d]+)\s*(?:Гц|Hz)\s*(?:частот|refresh|обновлен)',
        # Wikipedia / general: "120 Гц LTPO" or ", 120 Гц"
        r'[,\s]([\d]+\s*(?:Гц|Hz))\s*(?:LTPO|LTPS|Adaptive|,|$)',
        r'([\d]+)\s*(?:Гц|Hz)\b',
    )

    # ── Brightness ──
    specs.display_brightness = _first_match(
        text,
        r'(?:яркость|brightness)[^:]*?[:\s]*([\d]+\s*(?:нит|nits?))',
        r'([\d]+)\s*(?:нит|nits?)\b',
    )


def _extract_processor(text: str, specs: DeviceSpecs, device_name: str = "") -> None:
    """Extract processor/chipset specifications."""
    # Pre-filter: strip comparison/benchmark lines that mention other chips
    # e.g. "Энергоэффективность 8.10 из 100.00 ( Apple M4 (8 cores) )"
    _proc_text = re.sub(
        r'[\d.,]+\s*(?:из|of|from)\s*[\d.,]+\s*\([^)]*(?:Apple|Intel|AMD|Qualcomm|MediaTek|Samsung)[^)]*\)',
        '', text, flags=re.IGNORECASE,
    )
    # Also strip "vs" comparison lines
    _proc_text = re.sub(
        r'\bvs\.?\s+(?:Apple|Intel|AMD|Qualcomm|MediaTek|Samsung)\b[^\n]*',
        '', _proc_text, flags=re.IGNORECASE,
    )
    # ── Chipset name ──
    # Stoppers: parenthesis, newline, "Макс", "CPU", "Частот", comma, pipe
    _CHIP_STOP = r'(?:\n|\(|Макс|CPU|Частот|График|,|\||Ядер|Техпроц|$)'
    specs.processor = _first_match(
        _proc_text,
        # Labeled: "Чипсет: Qualcomm Snapdragon 8 Elite"
        r'(?:Чипсет|Chipset|Процессор|Chip|SoC)[:\s]*((?:Qualcomm\s+)?Snapdragon\s+[\w\s]+?)' + _CHIP_STOP,
        r'(?:Чипсет|Chipset|Процессор|Chip|SoC)[:\s]*((?:MediaTek\s+)?(?:Dimensity|Helio)\s+[\w\s]+?)' + _CHIP_STOP,
        r'(?:Чипсет|Chipset|Процессор|Chip|SoC)[:\s]*((?:Apple\s+)?A\d+\s*\w*)',
        r'(?:Чипсет|Chipset|Процессор|Chip|SoC)[:\s]*((?:Samsung\s+)?Exynos\s+[\w\s]+?)' + _CHIP_STOP,
        r'(?:Чипсет|Chipset|Процессор|Chip|SoC)[:\s]*(Google\s+Tensor\s+\w+)',
        # Laptop CPUs: Intel Core, AMD Ryzen
        r'(?:Чипсет|Chipset|Процессор|CPU|SoC)[:\s]*(Intel\s+Core\s+[\w\-]+(?:\s+\d+\w*)?)',
        r'(?:Чипсет|Chipset|Процессор|CPU|SoC)[:\s]*(AMD\s+Ryzen\s+[\w\-]+(?:\s+\d+\w*)?)',
        r'(?:Чипсет|Chipset|Процессор|CPU|SoC)[:\s]*(Intel\s+(?:Celeron|Pentium|Core\s+Ultra)\s+[\w\-]+)',
        r'(?:Чипсет|Chipset|Процессор|CPU|SoC)[:\s]*(Apple\s+M\d+\s*\w*)',
        # Standalone (no label)
        r'\bЧип[:\s]*(Qualcomm\s+Snapdragon\s+\d+\s*\w*)',
        r'\bЧип[:\s]*(MediaTek\s+Dimensity\s+\d+\s*\w*)',
        # Bare chip names anywhere in text
        r'\b(Qualcomm\s+Snapdragon\s+\d+\s*(?:Elite|Gen\s*\d+|Plus|\+)?)\b',
        r'\b(MediaTek\s+Dimensity\s+\d+\s*(?:Plus|\+)?)\b',
        r'\b(Samsung\s+Exynos\s+\d+)\b',
        r'\b(Google\s+Tensor\s+G\d+)\b',
        # Bare laptop CPU names — but NOT from comparison tables
        # Negative lookbehind: skip if preceded by "vs", "(", score patterns
        r'(?<!vs\s)(?<!\()\b(Intel\s+Core\s+(?:i[3579]|Ultra\s+\d+)[\s\-]*\d+\w*)\b',
        r'(?<!vs\s)(?<!\()\b(AMD\s+Ryzen\s+[3579]\s+\d+\w*)\b',
        r'(?<!vs\s)(?<!\()\b(Apple\s+M\d+\s*(?:Pro|Max|Ultra)?)\b',
    )
    # ── Cross-brand sanity check ──
    # If device_name is "AMD ..." but we extracted "Intel ...", discard
    if specs.processor and device_name:
        _dn = device_name.lower()
        _pr = specs.processor.lower()
        if ('amd' in _dn and 'intel' in _pr) or \
           ('intel' in _dn and 'amd' in _pr) or \
           ('apple' in _dn and ('intel' in _pr or 'amd' in _pr)) or \
           ('qualcomm' in _dn and ('intel' in _pr or 'amd' in _pr)):
            specs.processor = device_name  # Use original device name

    # ── CPU cores ──
    specs.cpu_cores = _first_match(
        text,
        r'(?:CPU[- ]?ядер|Cores?)[:\s]*(\d+)',
        r'(\d+)[- ]?(?:ядер|core)',
    )

    # ── CPU frequency (prefer max/turbo over base) ──
    specs.cpu_freq = _first_match(
        text,
        r'(?:Макс\.?\s*частот\w*|Max\.?\s*freq)[:\s]*([\d]+\s*(?:МГц|MHz|ГГц|GHz))',
        # "до 4500 МГц" / "разгоняться до 4500 МГц" / "Turbo Core позволяет ... до 4500 МГц"
        r'(?:Turbo|Boost|разгон\w*(?:\s+\w+){0,3}\s+до)\s*[:\s]*(\d+\s*(?:МГц|MHz|ГГц|GHz))',
        r'(?:до|up\s+to)\s+(\d{3,5}\s*(?:МГц|MHz|ГГц|GHz))',
        # Base / nominal frequency (fallback)
        r'(?:базов\w*|тактов\w*|Рабоч\w*|Base)\s*частот\w*[:\s–-]*(\d+\s*(?:МГц|MHz|ГГц|GHz))',
        # Bare pattern: "частота: 2000 МГц"
        r'(?:частот\w*)[:\s–-]*(\d+\s*(?:МГц|MHz|ГГц|GHz))',
    )

    # ── Process node ──
    specs.process_nm = _first_match(
        text,
        r'(?:Размер\s*транзистор\w*|Process|Техпроцесс)[:\s]*([\d]+\s*(?:нанометр\w*|нм|nm))',
        r'(?:\(|,\s*)([\d]+\s*(?:nm|нм))\s*(?:\)|,)',
        r'(\d+)\s*(?:nm|нм)\s*(?:техпроцесс|процесс)',
    )

    # ── GPU (only if not already set by _extract_gpu_specs) ──
    if not specs.gpu:
        specs.gpu = _first_match(
            text,
            r'(?:Графика|GPU|Видеочип|Графический\s*процессор)[:\s]*((?:Adreno|Mali|PowerVR|Apple|Immortalis)[\s\-]*[\w\-]+)',
            r'(?:Графика|GPU|Видеочип|Графический\s*процессор)[:\s]*(Intel\s+(?:Iris|UHD|HD)\s*(?:Xe|Plus|Graphics)?\s*\w*)',
            r'(?:Графика|GPU|Видеочип|Графический\s*процессор)[:\s]*(NVIDIA\s+GeForce\s+[\w\s]+?)(?:\n|,|$)',
            r'(?:Графика|GPU|Видеочип|Графический\s*процессор)[:\s]*(AMD\s+Radeon\s+[\w\s]+?)(?:\n|,|$)',
            # "графику AMD Radeon RX Vega 8" (natural language)
            r'(?:график\w+)\s*(AMD\s+Radeon\s+(?:RX\s+)?[\w]+\s*\d+\w*)',
            r'\b(Adreno\s*\d+)\b',
            r'\b(Mali[\s\-]*\w+\s*\w*)\b',
            r'\b(Immortalis[\s\-]*\w+)\b',
            r'\b(Intel\s+Iris\s+Xe\s*\w*)\b',
            r'\b(Intel\s+UHD\s+Graphics\s*\d*)\b',
            r'\b(NVIDIA\s+GeForce\s+(?:RTX|GTX|MX)\s*\d+\w*)\b',
            r'\b(AMD\s+Radeon\s+(?:RX\s+)?(?:Vega|R[579X]|HD)\s*\d+\w*)\b',
            r'\b(AMD\s+Radeon\s+\w+\s*\d+\w*)\b',
        )

    # ── TDP (for CPU / laptop devices, not GPU which has its own) ──
    if not specs.tdp:
        specs.tdp = _first_match(
            text,
            r'(?:TDP|cTDP|PBP|MTP|PL1)[:\s]*(\d+[\-–]\d+\s*(?:Вт|W)\b)',
            r'(?:TDP|cTDP|PBP|MTP|PL1)[:\s]*(\d+\s*(?:Вт|W)\b)',
            r'(?:Потреблени\w*|Power\s*(?:Consumption)?|Мощност\w*|Тепловой\s*пакет)[:\s]*(\d+\s*(?:Вт|W)\b)',
            r'(\d+)\s*(?:Вт|W)\s*(?:TDP|тепловой\s*пакет)',
        )


def _extract_memory(text: str, specs: DeviceSpecs) -> None:
    """Extract RAM and storage specifications."""
    # ── RAM ──
    specs.ram = _first_match(
        text,
        r'(?:Объем\s*ОЗУ|RAM|ОЗУ)[:\s]*([\d]+\s*(?:ГБ|GB|Гб))',
        r'(?:оператив\w+\s*памят\w*)[:\s]*([\d]+\s*(?:ГБ|GB|Гб))',
        # Bare "12 ГБ RAM" or "12GB RAM"
        r'([\d]+)\s*(?:ГБ|GB)\s*(?:RAM|ОЗУ|оператив)',
    )
    specs.ram_type = _first_match(
        text,
        r'(?:Тип\s*памяти|RAM\s*type)[:\s]*(LPDDR\d[\w-]*)',
        r'(?:Тип\s*памяти|RAM\s*type)[:\s]*(DDR\d[\w-]*)',
        r'\b(LPDDR\d[\w-]*)\b',
        r'\b(DDR\d[\w-]*)\b',
    )

    # ── Storage ──
    specs.storage = _first_match(
        text,
        r'(?:Объем\s*накопител\w*|Storage|Встроенн\w+\s*памят\w*|Накопител\w*)[:\s]*([\d]+(?:/[\d]+)*\s*(?:ГБ|GB|ТБ|TB)(?:\s*(?:SSD|HDD|NVMe|eMMC))?)',
        r'(?:внутренн\w+\s*памят\w*)[:\s]*([\d]+\s*(?:ГБ|GB))',
        # Wikipedia: "Флеш-память ... UFS 4.0: 128/256/512 ГБ"
        r'(?:Флеш[\s-]*памят\w*)[:\s]*(?:[\w\s.]+:\s*)?([\d]+(?:/[\d]+)*\s*(?:ГБ|GB|ТБ|TB))',
        # Standalone: "512 ГБ SSD" / "256 GB NVMe"
        r'([\d]+\s*(?:ГБ|GB)\s*(?:SSD|NVMe|HDD)(?:\s*NVMe)?)',
    )
    specs.storage_type = _first_match(
        text,
        r'(?:Тип\s*накопител\w*|Storage\s*type)[:\s]*(UFS\s*[\d.]+|eMMC\s*[\d.]+|NVMe)',
        r'\b(UFS\s*[\d.]+)\b',
    )

    # Memory card
    specs.memory_card = _first_match(
        text,
        r'(?:Карта\s*памяти|Memory\s*card|microSD)[:\s]*(Нет|Да|No|Yes|до\s*[\d]+\s*(?:ГБ|ТБ|GB|TB))',
    )


def _extract_battery(text: str, specs: DeviceSpecs) -> None:
    """Extract battery specifications."""
    # Note: Wikipedia uses "мА·ч" (with middle dot ·), not "мАч"
    specs.battery = _first_match(
        text,
        r'(?:Объем|Ёмкость|Батарея|Battery|Аккумулятор)[:\s]*([\d]+\s*(?:мА[·.]?ч|mAh))',
        # Wikipedia: "S25: 4000 мА·ч" — model prefix before value
        r'(?:Аккумулятор|Батарея)\s+\S+:\s*([\d]+\s*(?:мА[·.]?ч|mAh))',
        # Standalone pattern — any "4000 мАч" / "4000 мА·ч" / "4000 mAh"
        r'(\d{3,5})\s*(?:мА[·.]?ч|mAh)',
    )

    specs.charging = _first_match(
        text,
        r'(?:Макс\.?\s*мощность\s*зарядк\w*|Charging|Зарядка)[:\s]*([\d]+\s*(?:Вт|W)\b)',
        # Wikipedia: "Зарядка S25: 25 Вт проводная" — model prefix
        r'(?:Зарядка)\s+\S+:\s*([\d]+\s*(?:Вт|W))',
        r'(?:быстр\w+\s*зарядк\w*)[^,\n]*?([\d]+\s*(?:Вт|W)\b)',
        r'([\d]+)\s*(?:Вт|W)\s*(?:проводн|провод|wire|charge|зарядк)',
    )

    specs.charging_time = _first_match(
        text,
        r'(?:Время\s*(?:полной\s*)?зарядк\w*|Charging\s*time)[:\s]*([\d:]+\s*(?:ч(?:ас)?\.?|мин|min|h))',
    )

    specs.wireless_charging = _first_match(
        text,
        r'(?:Беспроводн\w+\s*зарядк\w*|Wireless\s*charg\w*)[:\s]*((?:Да|Нет|Yes|No)(?:\s*\([\d]+\s*(?:Вт|W)\))?)',
        # Wikipedia: "15 Вт беспроводная"
        r'([\d]+\s*(?:Вт|W))\s*беспроводн',
    )


def _extract_camera(text: str, specs: DeviceSpecs) -> None:
    """Extract camera specifications."""
    # ── Main camera: "50 МП + 10 МП + 12 МП" or "50 MP" ──
    # Be strict: require label or clear context, avoid matching random numbers
    specs.main_camera = _first_match(
        text,
        # Labeled: "Основная камера: 50 MP + 10 MP + 12 MP"
        r'(?:Основн\w+\s*камер\w*|Задн\w+\s*камер\w*|Main\s*Camera|Rear\s*Camera)\s*[:\s]*(\d+\s*(?:МП|Мп|MP)(?:\s*[+(]\s*\d+\s*(?:МП|Мп|MP)\)?)*)',
        # Wikipedia/general: "50 Мп + 12 Мп + 10 Мп" or "50 Мп (wide) + 12 Мп"
        r'(?:Основн\w+\s*камер\w*)\s*[:\s]*(?:[^\d]*)(\d+\s*(?:МП|Мп|MP)(?:\s*(?:\([^)]*\))?\s*[+]\s*\d+\s*(?:МП|Мп|MP))*)',
        # Nanoreview: "Камера: 3 (50 MP + 10 MP + 12 MP)" — numbers in parens
        r'\bКамера\s*[:\s]+\d+\s*\(([\d]+\s*(?:МП|Мп|MP)(?:\s*\+\s*[\d]+\s*(?:МП|Мп|MP))+)\)',
        # "Матрица: 50 мегапикселей"
        r'(?:Матрица|Sensor)[:\s]*([\d]+\s*(?:мегапикс\w*|МП|Мп|MP))',
    )

    # ── Selfie camera ──
    specs.selfie_camera = _first_match(
        text,
        r'(?:Фронтальн\w+\s*камер\w*|Передн\w+\s*камер\w*)[:\s]*(\d+\s*(?:МП|MP))',
        r'(?:Селфи|Selfie)[^:]*?[:\s]*(\d+\s*(?:МП|MP|мегапикс\w*))',
    )

    # ── Video ──
    specs.video = _first_match(
        text,
        r'(?:Запись)\s*(\d+K)\s*видео',
        r'(?:Video|Видео)[:\s]*((?:8K|4K|1080p)(?:@\d+fps)?)',
        r'((?:8K|4K)@\d+fps)',
    )


def _extract_body(text: str, specs: DeviceSpecs) -> None:
    """Extract physical dimensions and body specs."""
    # ── Dimensions: "146.9 x 70.5 x 7.2 мм" ──
    specs.dimensions = _first_match(
        text,
        r'(?:Размеры|Dimensions)[:\s]*([\d.,]+\s*[xXхХ×]\s*[\d.,]+\s*[xXхХ×]\s*[\d.,]+\s*(?:мм|mm))',
    )
    if not specs.dimensions:
        # Try "Высота/Ширина/Толщина" labels
        h = _first_match(text, r'(?:Высота)[:\s]*([\d.,]+)\s*(?:мм|mm)')
        w = _first_match(text, r'(?:Ширина)[:\s]*([\d.,]+)\s*(?:мм|mm)')
        t = _first_match(text, r'(?:Толщина)[:\s]*([\d.,]+)\s*(?:мм|mm)')
        if h and w and t:
            specs.dimensions = f"{h} × {w} × {t} мм"
    if not specs.dimensions:
        # Wikipedia: "146,9 мм (5,8 ″) В 70,5 мм (2,8 ″) Ш 7,2 мм (0,28 ″) Т"
        m = re.search(
            r'(\d+[.,]\d+)\s*мм\s*\([^)]+\)\s*В\s*'
            r'(\d+[.,]\d+)\s*мм\s*\([^)]+\)\s*Ш\s*'
            r'(\d+[.,]\d+)\s*мм\s*\([^)]+\)\s*Т',
            text,
        )
        if m:
            specs.dimensions = f"{m.group(1)} × {m.group(2)} × {m.group(3)} мм"
    if not specs.dimensions:
        # Wikipedia compact: "146,9 мм В 70,5 мм Ш 7,2 мм Т"
        m = re.search(
            r'(\d+[.,]\d+)\s*(?:мм|mm)\s*В\s*'
            r'(\d+[.,]\d+)\s*(?:мм|mm)\s*Ш\s*'
            r'(\d+[.,]\d+)\s*(?:мм|mm)\s*Т',
            text,
        )
        if m:
            specs.dimensions = f"{m.group(1)} × {m.group(2)} × {m.group(3)} мм"

    # ── Weight ──
    specs.weight = _first_match(
        text,
        r'(?:Вес|Weight|Масса)[:\s]*(?:\S+\s*:\s*)?([\d.,]+\s*(?:грамм\w*|г\.?|g\b))',
        r'(?:Вес|Weight|Масса)\s+\S+:\s*([\d.,]+\s*(?:грамм\w*|г\.?|g\b))',
        r'([\d.,]+)\s*(?:грамм|г\.)\b',
    )

    # ── Water resistance ──
    specs.water_resistance = _first_match(
        text,
        r'(?:Водонепроницаемость|Water|IP)\s*[:\s]*(IP\d+)',
        r'\b(IP\d{2})\b',
    )

    # ── Colors ──
    specs.colors = _first_match(
        text,
        r'(?:Доступн\w+\s*цвет\w*|Colors?|Цвет\w*)[:\s]*([А-Яа-яёA-Za-z,\s]+?)(?:\n|Сканер|Сен|$)',
    )

    # ── Fingerprint ──
    specs.fingerprint = _first_match(
        text,
        r'(?:Сканер\s*отпечатк\w*|Fingerprint)[:\s]*((?:Да|Нет|Yes|No)[^.\n]*)',
    )


def _extract_system(text: str, specs: DeviceSpecs) -> None:
    """Extract OS and connectivity."""
    # ── OS ──
    # Pre-filter: strip "compatibility" lines that mention OS names
    # to avoid "Совместимость с Windows 11" being extracted as OS
    _os_text = re.sub(
        r'(?:совместим\w*|compatible|compatibility|поддерж\w*)\s+(?:с\s+)?'
        r'(?:Windows|Android|macOS|Linux)\s*\d*[^\n]*',
        '', text, flags=re.IGNORECASE,
    )
    specs.os = _first_match(
        _os_text,
        r'(?:Операционная\s*система|Oперационная\s*система)[:\s]*((?:Android|Windows|macOS|ChromeOS|Linux)\s*[\d.]+(?:\s*\w+)?)',
        # Wikipedia: "Первоначальная: Android 15 с One UI 7.0"
        r'(?:Первоначальная|Текущая|Original|Current)[:\s]*(Android\s*\d+)',
        r'(?:^|\n)\s*OS[:\s]*((?:Android|Windows|macOS)\s*[\d.]+)',
        r'\bOS[:\s]*((?:Android|Windows|macOS|Chrome\s*OS)\s*[\d.]+)',
        r'\b(iOS\s*\d+)\b',
        r'\b(HarmonyOS\s*\d+)\b',
        # Bare "Windows 11 Home" / "Android 15"
        r'\b(Windows\s*1[01]\s*(?:Home|Pro)?)\b',
        r'(?:с|with|on)\s+(Android\s*\d+)',
        # "на базе Android 15"
        r'(?:на\s*базе|based\s*on)\s+(Android\s*\d+)',
        # Bare Android/iOS as last resort
        r'\b(Android\s*\d+)\b',
    )

    # ── UI ──
    specs.ui = _first_match(
        text,
        r'(?:Оболочка|UI\b)[:\s]*((?:One\s*UI|MIUI|HyperOS|ColorOS|OxygenOS|Funtouch)\s*[\d.]+)',
        # Bare "One UI 7.0" or "One UI 8.0" anywhere
        r'\b(One\s*UI\s*[\d.]+)\b',
        r'\b(MIUI\s*[\d.]+)\b',
        r'\b(HyperOS\s*[\d.]+)\b',
        r'\b(ColorOS\s*[\d.]+)\b',
    )

    # ── SIM ──
    specs.sim = _first_match(
        text,
        r'(?:SIM|Сим[\s-]?карт\w*)[:\s]*(?:[\d×]+\s*(?:или|or)\s*)*(?:[\d×]+\s*)*((?:Nano|Micro)[\s-]*SIM(?:\s*(?:и|и\s*\d+×?\s*|\+\s*)(?:Nano[\s-]*SIM|eSIM))*)',
        r'((?:Nano|Micro)[\s-]*SIM\s*(?:\+|и)\s*(?:Nano[\s-]*SIM|eSIM))',
        r'((?:Nano|Micro)[\s-]*SIM)',
        r'\b(eSIM)\b',
    )

    # ── NFC ──
    specs.nfc = _first_match(
        text,
        r'(?:NFC)[:\s]*(Да|Нет|Yes|No)',
    )


def _extract_laptop_extras(text: str, specs: DeviceSpecs) -> None:
    """Extract laptop-specific specs (ports, wifi, keyboard, etc.)."""
    # ── Wi-Fi ──
    specs.wifi = _first_match(
        text,
        r'(?:Wi-?Fi|WiFi|WLAN)[:\s]*(Wi-?Fi\s*\d+\w?(?:\s*\([\w.]+\))?)',
        r'(?:Wi-?Fi|WiFi|WLAN)[:\s]*([\w.\s]+802\.11\w+)',
        r'\b(Wi-?Fi\s*\d+\w?(?:\s*\([\w.\s]+\))?)\b',
        r'\b(802\.11\s*\w+)\b',
    )
    if specs.wifi:
        specs.wifi = specs.wifi.strip().rstrip(',')

    # ── Bluetooth ──
    specs.bluetooth = _first_match(
        text,
        r'(?:Bluetooth|BT)[:\s]*([\d.]+)',
        r'\b(Bluetooth\s*[\d.]+)\b',
    )
    if specs.bluetooth and not specs.bluetooth.lower().startswith('bluetooth'):
        specs.bluetooth = f"Bluetooth {specs.bluetooth}"

    # ── Ports ──
    specs.ports = _first_match(
        text,
        r'(?:Порты|Ports|Разъ[её]мы)[:\s]*(.+?)(?:\.\s+[\u0410-\u042fA-Z]|\n|$)',
        r'(?:Интерфейсы)[:\s]*(?!памят)(.+?)(?:\.\s+[\u0410-\u042fA-Z]|\n|$)',
    )
    # Strip memory specs that leaked into ports (e.g. "DDR4-3200")
    if specs.ports and re.search(r'\bDDR\d', specs.ports, re.IGNORECASE):
        cleaned = re.sub(r'\bL?P?DDR\d[\w\-]*', '', specs.ports).strip(' ,;')
        specs.ports = cleaned if cleaned else ""
    # Discard ports if no recognisable port/connector keywords remain
    if specs.ports and not re.search(
        r'USB|HDMI|DisplayPort|Thunderbolt|Type[\s-]?C|RJ[\s-]?45|VGA|Jack|Audio|Ethernet|SD|мм|mm',
        specs.ports, re.IGNORECASE,
    ):
        specs.ports = ""

    # ── Keyboard ──
    specs.keyboard = _first_match(
        text,
        r'(?:Клавиатура|Keyboard)[:\s]*(.+?)(?:\.\s+[\u0410-\u042fA-Z]|\n|$)',
    )

    # ── Webcam (use selfie_camera field) ──
    if not specs.selfie_camera:
        specs.selfie_camera = _first_match(
            text,
            r'(?:Вебкамера|Webcam|Камера)[:\s]*([\d.]+\s*(?:МП|MP))',
            r'(?:Вебкамера|Webcam|Камера)[:\s]*([^\n]{3,40})',
        )

    # ── Weight for laptops: also check for kg ──
    if not specs.weight:
        specs.weight = _first_match(
            text,
            r'(?:Вес|Weight|Масса)[:\s]*([\d.,]+\s*(?:кг|kg))',
            r'([\d.,]+)\s*(?:кг|kg)\b',
        )

    # ── Battery for laptops: Wh ──
    if not specs.battery:
        specs.battery = _first_match(
            text,
            r'(?:Батарея|Battery|Аккумулятор)[:\s]*([\d.,]+\s*(?:Вт[·.]?ч|Wh))',
            r'([\d.,]+)\s*(?:Вт[·.]?ч|Wh)\b',
        )


# ─── GPU (video card) extraction ──────────────────────────────────────────────

def _extract_gpu_specs(text: str, specs: DeviceSpecs) -> None:
    """Extract standalone GPU (video card) specific specs."""
    # ── GPU chip name (GA104, AD102, Navi 31, etc.) ──
    specs.gpu_chip = _first_match(
        text,
        r'(?:GPU|Чип|Chip)[:\s]*((?:GA|GP|GV|TU|AD|GH)\d+[\w-]*)',
        r'(?:GPU|Чип|Chip)[:\s]*(Navi\s*\d+\w*)',
        r'\b((?:GA|GP|GV|TU|AD|GH)\d{3}[\w-]*)\b',
        r'\b(Navi\s*\d+\w*)\b',
    )

    # ── Architecture (Ampere, Ada Lovelace, RDNA 3, etc.) ──
    specs.gpu_architecture = _first_match(
        text,
        r'(?:Архитектура|Architecture)[:\s]*([\w\s]+?)(?:\n|,|$)',
        r'\b(Ampere|Ada\s*Lovelace|Turing|Pascal|Maxwell|Volta|Hopper)\b',
        r'\b(RDNA\s*\d+)\b',
        r'\b(Kepler|Fermi|Polaris|Vega)\b',
    )

    # ── CUDA cores / Stream processors ──
    specs.cuda_cores = _first_match(
        text,
        r'(?:CUDA[- ]?(?:ядер|cores?|Cores?))[:\s]*(\d[\d\s]*\d)',
        r'(?:Потоков\w+\s*процессор\w*|Stream\s*Processors?|Shader\s*(?:Units?|Cores?|Processors?))[:\s]*(\d[\d\s]*\d)',
        r'(?:Шейдерн\w+\s*(?:процессор\w*|блок\w*|ядер\w*))[:\s]*(\d[\d\s]*\d)',
        r'(\d{3,5})\s*(?:CUDA|потоков\w+\s*процессор|shader)',
    )
    if specs.cuda_cores:
        specs.cuda_cores = specs.cuda_cores.replace(' ', '')

    # ── VRAM (Video Memory) ──
    specs.vram = _first_match(
        text,
        r'(?:Видеопамят\w*|Video\s*Memory|VRAM|Память\s*видеокарт\w*|Объ[ёе]м\s*памят\w*)[:\s]*(\d+\s*(?:ГБ|GB|МБ|MB))',
        r'(\d+)\s*(?:ГБ|GB)\s*(?:GDDR|HBM|видеопамят)',
        r'(?:GDDR\d\w?)\s*(\d+\s*(?:ГБ|GB))',
    )

    # ── VRAM type ──
    specs.vram_type = _first_match(
        text,
        r'(?:Тип\s*памят\w*|Memory\s*Type)[:\s]*(GDDR\d\w?|HBM\d?\w*)',
        r'\b(GDDR\d\w?)\b',
        r'\b(HBM\d?\w*)\b',
    )

    # ── Memory bus width ──
    specs.memory_bus = _first_match(
        text,
        r'(?:Шина\s*памят\w*|(?:Memory\s*)?Bus(?:\s*Width)?|Разрядность(?:\s*шины)?)[:\s]*(\d+[\s-]*(?:бит|bit))',
        r'(\d{3})\s*(?:бит|bit)\s*(?:шин|bus|интерфейс)',
    )

    # ── Boost clock ──
    specs.boost_clock = _first_match(
        text,
        r'(?:Boost|Турбо|Макс\.?\s*частот\w*)[^:]*?[:\s]*(\d+\s*(?:МГц|MHz))',
        r'(?:Turbo|Boost)\s*(?:Clock)?[:\s]*(\d+\s*(?:МГц|MHz))',
    )

    # ── Base clock ──
    specs.base_clock = _first_match(
        text,
        r'(?:Base|Базов\w+\s*частот\w*|Номинальн\w+\s*частот\w*)[^:]*?[:\s]*(\d+\s*(?:МГц|MHz))',
        r'(?:Base\s*Clock|Базов\w+\s*частот\w*)[:\s]*(\d+\s*(?:МГц|MHz))',
    )

    # ── TDP (power consumption) ──
    specs.tdp = _first_match(
        text,
        r'(?:TDP|TGP|Потреблени\w*|Power\s*(?:Consumption|Draw)?|Мощност\w*)[:\s]*(\d+\s*(?:Вт|W)\b)',
        r'(\d+)\s*(?:Вт|W)\s*(?:TDP|TGP|потреблени|мощност)',
    )

    # ── Interface (PCIe) ──
    specs.gpu_interface = _first_match(
        text,
        r'(?:Интерфейс|Interface|Шина|Bus)[:\s]*(PCIe?\s*[\d.]+\s*x\d+)',
        r'\b(PCIe?\s*[\d.]+\s*x\d+)\b',
    )

    # ── Video outputs ──
    specs.gpu_outputs = _first_match(
        text,
        r'(?:Видеовыход\w*|Выход\w*|Outputs?|Display\s*Outputs?)[:\s]*(.+?)(?:\.\s+[\u0410-\u042fA-Z]|\n|$)',
    )

    # ── Ray tracing ──
    specs.ray_tracing = _first_match(
        text,
        r'(?:Ray\s*Tracing|Трассировк\w+\s*луч\w*)[:\s]*(.+?)(?:\.\s|\n|$)',
        r'\b(RT\s*(?:Cores?|ядер)\s*\d+\w*\s*\w*)\b',
    )
    if not specs.ray_tracing:
        # Just check presence
        if re.search(r'\bray\s*tracing\b|\bтрассировк\w+\s*луч\w*\b', text, re.I):
            specs.ray_tracing = "Да"

    # ── DLSS / FSR ──
    specs.dlss = _first_match(
        text,
        r'\b(DLSS\s*[\d.]+)\b',
        r'\b(FSR\s*[\d.]+)\b',
        r'\b(DLSS)\b',
    )

    # ── GPU name as processor field ──
    if not specs.gpu:
        specs.gpu = _first_match(
            text,
            r'\b((?:GeForce\s+)?(?:RTX|GTX)\s*\d{3,4}\s*(?:Ti|SUPER|Super)?)\b',
            r'\b(Radeon\s+RX\s*\d{3,4}\s*(?:XT|XTX)?)\b',
            r'\b(Arc\s+A\d{3,4}\w*)\b',
        )

    # ── Dimensions for GPU card ──
    if not specs.dimensions:
        specs.dimensions = _first_match(
            text,
            r'(?:Размер\w*|Dimensions?|Длина)[:\s]*([\d.,]+\s*[xXхХ×]\s*[\d.,]+(?:\s*[xXхХ×]\s*[\d.,]+)?\s*(?:мм|mm))',
        )


# ─── Headphone / audio device extraction ──────────────────────────────────────

def _extract_headphone_specs(text: str, specs: DeviceSpecs) -> None:
    """Extract headphone/earphone/headset specifications."""
    # ── Type (open/closed/in-ear) ──
    specs.headphone_type = _first_match(
        text,
        r'(?:Тип|Конструкци\w*|Type|Design|Акустическ\w+\s*(?:тип|оформлен\w*))[:\s]*(закрыт\w+|открыт\w+|полуоткрыт\w+|внутриканальн\w+|накладн\w+|полноразмерн\w+|вставн\w+|мониторн\w+)',
        r'\b(closed[\s-]*back|open[\s-]*back|semi[\s-]*open|circumaural|supraaural|in[\s-]*ear|over[\s-]*ear|on[\s-]*ear)\b',
        r'(?:Тип\s*(?:наушник\w*|амбушюр\w*)?)[:\s]*([\w\s-]{3,30}?)(?:\n|,|$)',
    )

    # ── Driver size ──
    specs.driver_size = _first_match(
        text,
        r'(?:Диаметр\s*(?:драйвер\w*|динамик\w*|мембран\w*|излучател\w*)|Driver\s*(?:Size|Diameter)|Размер\s*(?:драйвер\w*|динамик\w*)|Динамик\w*)[:\s]*(\d+(?:[.,]\d+)?\s*(?:мм|mm))',
        r'(?:драйвер\w*|динамик\w*|driver)\s*(?:[\w\s]*?)\s*(\d+(?:[.,]\d+)?\s*(?:мм|mm))',
        r'(\d+(?:[.,]\d+)?)\s*(?:мм|mm)\s*(?:драйвер|динамик|driver|мембран|излучател)',
    )

    # ── Frequency range ──
    specs.frequency_range = _first_match(
        text,
        r'(?:Частотн\w+\s*(?:диапазон|характеристик\w*|response)?|Frequency\s*(?:Range|Response)?|Диапазон\s*частот\w*|АЧХ)[:\s]*(\d+[\s]*[-–—][\s]*\d+[\s]*(?:к?Гц|k?Hz))',
        r'(\d{1,3}[\s]*[-–—][\s]*\d{2,5}[\s]*(?:Гц|Hz))',
        r'(\d{1,3}[\s]*(?:Гц|Hz)[\s]*[-–—][\s]*\d{2,5}[\s]*(?:Гц|Hz))',
    )
    # Fallback: combine separate min/max frequency lines
    if not specs.frequency_range:
        m_min = re.search(r'(?:Минимальн\w+\s+(?:\w+\s+)*частот\w*)[:\s]*(\d+)\s*(?:Гц|Hz)', text, re.I)
        m_max = re.search(r'(?:Максимальн\w+\s+(?:\w+\s+)*частот\w*)[:\s]*(\d+)\s*(?:кГц|kHz|Гц|Hz)', text, re.I)
        if m_min and m_max:
            lo = m_min.group(1)
            hi = m_max.group(1)
            hi_unit = "кГц" if re.search(r'кГц|kHz', text[m_max.start():m_max.end()], re.I) else "Гц"
            specs.frequency_range = f"{lo} Гц – {hi} {hi_unit}"

    # ── Impedance ──
    specs.impedance = _first_match(
        text,
        r'(?:Импеданс|Сопротивлен\w*|Impedance|Номинальн\w+\s*(?:сопротивлен\w*|импеданс))[:\s]*(\d+(?:[.,]\d+)?\s*(?:Ом|[oO]hm|Ω))',
        r'(\d+(?:[.,]\d+)?)\s*(?:Ом|[oO]hm|Ω)\s*(?:импеданс|сопротивлен)',
    )

    # ── Sensitivity ──
    specs.sensitivity = _first_match(
        text,
        r'(?:Чувствительн\w*|Sensitivity|Звуковое\s*давлен\w*|SPL)[:\s]*(\d+(?:[.,]\d+)?\s*(?:дБ|dB)(?:\s*/\s*(?:мВт|mW|В|V))?)',
        r'(?:Чувствительн\w*)[\w\s]*?[:\s]+(\d+(?:[.,]\d+)?\s*(?:дБ|dB)(?:\s*/\s*(?:мВт|mW|В|V))?)',
        r'(\d+(?:[.,]\d+)?)\s*(?:дБ|dB)\s*(?:/\s*(?:мВт|mW|В|V))?\s*(?:чувствительн|sensitivity)',
    )

    # ── Cable length ──
    specs.cable_length = _first_match(
        text,
        r'(?:Длина\s*(?:кабел\w*|провод\w*|шнур\w*)|Cable\s*(?:Length)?)[:\s]*([\d.,]+\s*(?:м\b|м[^а-яА-Я]|m\b))',
        r'(?:кабел\w*|провод\w*|шнур\w*|cable)\s*(?:[\w\s]*?)\s*([\d.,]+\s*(?:м\b|m\b))',
    )

    # ── Connector type ──
    specs.connector = _first_match(
        text,
        r'(?:Разъ[её]м\w*|Штекер|Коннектор|Connector|Plug|Jack)[:\s]*([^\n]{3,50}?)(?:\n|$)',
        r'\b((?:3[.,]5|6[.,]3)\s*(?:мм|mm)\s*(?:(?:mini[\s-]?)?jack|TRS|разъ[её]м|штекер)?(?:\s*[+/]\s*(?:3[.,]5|6[.,]3)\s*(?:мм|mm)(?:\s*(?:jack|адаптер|переходник))?)?)\b',
    )

    # ── Microphone ──
    specs.microphone = _first_match(
        text,
        r'(?:Микрофон\w*|Microphone)[:\s]*([\w\s]{2,30}?)(?:\n|,|$)',
    )
    if not specs.microphone:
        if re.search(r'\bмикрофон\w*\s*(?:есть|да|встроен|включ)', text, re.I):
            specs.microphone = "Да"
        elif re.search(r'(?:без|нет)\s*микрофон', text, re.I):
            specs.microphone = "Нет"

    # ── Noise cancelling ──
    specs.noise_cancelling = _first_match(
        text,
        r'(?:Шумоподавлен\w*|Noise\s*Cancell?\w*|ANC)[:\s]*([\w\s]{2,30}?)(?:\n|,|$)',
    )
    if not specs.noise_cancelling:
        if re.search(r'\b(?:ANC|активн\w+\s*шумоподавлен)\b', text, re.I):
            specs.noise_cancelling = "Да (ANC)"

    # ── Wireless / Bluetooth ──
    specs.wireless = _first_match(
        text,
        r'(?:Подключени\w*|Тип\s*подключен\w*|Connection\s*Type)[:\s]*((?:проводн|беспроводн|wireless|wired)[\w\s]*?)(?:\n|,|$)',
    )
    if not specs.wireless:
        if re.search(r'беспроводн|wireless', text, re.I):
            specs.wireless = "Беспроводные"
        elif re.search(r'проводн|wired', text, re.I):
            specs.wireless = "Проводные"
    specs.bluetooth = _first_match(
        text,
        r'(?:Bluetooth)[:\s]*([\d.]+)',
        r'\b(Bluetooth\s*[\d.]+)\b',
    )

    # ── Weight ──
    if not specs.weight:
        specs.weight = _first_match(
            text,
            r'(?:Вес|Масса|Weight)[:\s]*([\d.,]+\s*(?:г\b|грамм|g\b|кг|kg))',
            r'(\d+(?:[.,]\d+)?)\s*(?:г\b|грамм)\s*(?:без\s*кабел)?',
        )

    # ═══════════════════════════════════════════════════════════════════════
    #  Prose-aware fallbacks for TWS earbuds / marketing pages
    # ═══════════════════════════════════════════════════════════════════════

    # ── TWS type detection from prose ──
    if not specs.headphone_type:
        if re.search(r'\bTWS\b', text, re.I):
            specs.headphone_type = "TWS (внутриканальные)"
        elif re.search(r'внутриканальн|in[\s-]?ear|earbud|вкладыш|вставн', text, re.I):
            specs.headphone_type = "Внутриканальные"

    # ── Battery life from prose: "до 39 часов работы/звучания" ──
    if not specs.battery:
        m = re.search(
            r'(?:до\s+)?(\d+(?:[.,]\d+)?)\s*(?:час\w*|ч\.?)\s*'
            r'(?:автономн\w*|работ\w*|звучан\w*|воспроизведен\w*|'
            r'прослушиван\w*|использован\w*)',
            text, re.I,
        )
        if m:
            specs.battery = f"до {m.group(1)} ч"
        else:
            m2 = re.search(
                r'(?:автономност\w*|время\s*работ\w*|battery\s*life)[:\s]*'
                r'(?:до\s+)?(\d+(?:[.,]\d+)?)\s*(?:час\w*|ч\.?|h\b)',
                text, re.I,
            )
            if m2:
                specs.battery = f"до {m2.group(1)} ч"

    # ── IP rating from prose: "IP54", "IP55", "IPX4" ──
    if not specs.water_resistance:
        m = re.search(r'\b(IP[X]?\d{1,2})\b', text, re.I)
        if m:
            specs.water_resistance = m.group(1).upper()

    # ── Bluetooth version from prose: "Bluetooth 5.2" ──
    if not specs.bluetooth:
        m = re.search(r'[Bb]luetooth\s*(\d+(?:\.\d+)?)', text)
        if m:
            specs.bluetooth = f"Bluetooth {m.group(1)}"

    # ── Driver size from prose: "динамик(о)в 10мм", "10мм драйвер" ──
    if not specs.driver_size:
        m = re.search(
            r'(?:драйвер\w*|динамик\w*|излучател\w*|driver)\w*\s+'
            r'(?:[\w\s,]*?\s)?(\d+(?:[.,]\d+)?)\s*(?:мм|mm)',
            text, re.I,
        )
        if not m:
            m = re.search(
                r'(\d+(?:[.,]\d+)?)\s*(?:мм|mm)\s*(?:драйвер|динамик|driver|мембран)',
                text, re.I,
            )
        if m:
            specs.driver_size = f"{m.group(1)} мм"

    # ── ANC depth from prose: "шумоподавления до 45дБ" ──
    if specs.noise_cancelling and not re.search(r'\d', specs.noise_cancelling):
        m = re.search(
            r'шумоподавлен\w*\s*(?:до\s+)?(\d+)\s*(?:дБ|dB)',
            text, re.I,
        )
        if m:
            specs.noise_cancelling = f"ANC (до {m.group(1)} дБ)"

_GPU_KEYWORDS = re.compile(
    r'(?:видеокарт|gpu\b|rtx\s*\d|gtx\s*\d|geforce|radeon\s*rx|'
    r'gainward|palit|msi\s+gaming|asus\s+(?:rog|tuf)|gigabyte\s+(?:aorus|eagle|gaming)|'
    r'evga|zotac|sapphire|powercolor|xfx\b|pny\b|inno3d|galax\b|kfa2|'
    r'nvidia\b|amd\s+radeon|intel\s+arc)',
    re.IGNORECASE,
)


def _is_gpu_query(device_name: str, text: str = "") -> bool:
    """Detect if the device is a standalone GPU/video card."""
    if _GPU_KEYWORDS.search(device_name):
        return True
    # Check text but require stronger signal (at least 2 GPU indicators)
    if text:
        t500 = text[:800].lower()
        gpu_signals = sum(1 for p in [
            r'(?:geforce|radeon)\s+(?:rtx|gtx|rx)\s*\d',
            r'cuda\s*(?:cores?|ядер)',
            r'gddr\d',
            r'видеокарт',
            r'видеопамят',
            r'\bvram\b',
            r'stream\s*processor',
            r'шейдерн',
        ] if re.search(p, t500, re.I))
        if gpu_signals >= 2:
            return True
    return False


# ─── Laptop detection ─────────────────────────────────────────────────────────

_LAPTOP_KEYWORDS = re.compile(
    r'(?:book|laptop|ноутбук|notebook|ultrabook|chromebook|macbook|'
    r'thinkpad|ideapad|pavilion|vivobook|zenbook|swift|aspire)',
    re.IGNORECASE,
)


def _is_laptop_query(device_name: str, text: str = "") -> bool:
    """Detect if the device is a laptop/notebook."""
    if _LAPTOP_KEYWORDS.search(device_name):
        return True
    if text and _LAPTOP_KEYWORDS.search(text[:500]):
        return True
    return False


# ─── Headphone / audio device detection ───────────────────────────────────────

_HEADPHONE_KEYWORDS = re.compile(
    r'(?:наушник|headphone|earphone|earbud|earplug|headset|гарнитур|'
    r'iem\b|in-ear|over-ear|on-ear|monitor\s*headphone|studio\s*headphone|'
    r'buds?\b|airpods?|nothing\s*ear|cmf\s*buds|'
    r'behringer\s*bh|audio-technica\s*ath|sennheiser\s*hd|beyerdynamic\s*dt|'
    r'akg\s*k\d|sony\s*(?:wh-|wf-|mdr-)|jbl\s*(?:tune|live|quantum)|'
    r'samsung\s*galaxy\s*buds|pixel\s*buds|huawei\s*freebuds)',
    re.IGNORECASE,
)


def _is_headphone_query(device_name: str, text: str = "") -> bool:
    """Detect if the device is a headphone/headset/IEM."""
    if _HEADPHONE_KEYWORDS.search(device_name):
        return True
    if text:
        t = text[:800].lower()
        signals = sum(1 for p in [
            r'наушник',
            r'headphone|earphone|earbud|headset',
            r'импеданс|impedance|сопротивлен',
            r'(?:частотн|frequency)\s*(?:диапазон|range|response)',
            r'чувствительн|sensitivity',
            r'драйвер|driver\s*(?:size|diameter)',
            r'(?:3[.,]5|6[.,]3)\s*мм\s*(?:jack|разъ[её]м|штекер)',
        ] if re.search(p, t, re.I))
        if signals >= 2:
            return True
    return False


# ─── Device name validation ───────────────────────────────────────────────────

def _device_name_words(device_name: str) -> List[str]:
    """Extract significant words from device name for validation.

    Returns words that MUST appear in a page for it to be relevant.
    E.g. "Samsung Galaxy S25" → ["samsung", "galaxy", "s25"]
    E.g. "Realme 10" → ["realme", "10"]
    E.g. "gainward RTX 3070" → ["rtx", "3070"] (gainward is AIB brand)
    """
    # Remove generic words that don't identify the specific device
    _GENERIC = {"phone", "smartphone", "смартфон", "телефон", "мобильный",
                "характеристики", "specs", "обзор", "review"}
    # GPU AIB partners: brand names that don't affect specs
    _GPU_BRANDS = {"gainward", "palit", "msi", "asus", "gigabyte", "evga",
                   "zotac", "sapphire", "powercolor", "xfx", "pny", "inno3d",
                   "galax", "kfa2", "colorful", "gaming", "phoenix", "eagle",
                   "aorus", "rog", "tuf", "strix", "ventus", "twin", "dual",
                   "founders", "edition", "видеокарта", "видеокарту"}
    words = []
    for w in device_name.lower().split():
        w = re.sub(r'[^\w\d+]', '', w)
        if w and w not in _GENERIC and w not in _GPU_BRANDS and len(w) >= 1:
            words.append(w)
    return words


def _text_mentions_device(text: str, device_name: str) -> bool:
    """Check if text specifically mentions the target device.

    Uses strict word-boundary matching to avoid false positives like
    "Pro Plus" matching any device in a list that contains generic words.
    Requires the BRAND word AND at least one specific distinguishing word
    (model number, unique sub-brand) to appear near each other.
    """
    if not device_name:
        return True

    name_words = _device_name_words(device_name)
    if not name_words:
        return True

    text_lower = text.lower()

    # Word-boundary matching (avoids "pro" matching "microprocessor", etc.)
    def _has_word(word: str, t: str) -> bool:
        if re.search(r'(?<!\w)' + re.escape(word) + r'(?!\w)', t):
            return True
        # Fuzzy model number: "bh470" matches "bh 470", "bh-470"
        m = re.match(r'^([a-z]+)(\d+)$', word)
        if m:
            pat = re.escape(m.group(1)) + r'[\s\-_]?' + re.escape(m.group(2))
            if re.search(pat, t):
                return True
        return False

    matched_words = [w for w in name_words if _has_word(w, text_lower)]
    matched_count = len(matched_words)

    # Identify the brand word (usually first meaningful word): "realme", "samsung", etc.
    brand_word = name_words[0] if name_words else None
    brand_present = brand_word and _has_word(brand_word, text_lower)

    # Also find numeric/unique model identifier (e.g. "16", "S25", "7950X")
    # These are strong identifiers — if ALL are present we have a strong match
    numeric_ids = [w for w in name_words
                   if re.search(r'\d', w) or len(w) <= 3]  # short codes like "s25", "pro"

    n = len(name_words)
    if n <= 2:
        # Short name: ALL words must match with word boundaries
        return matched_count == n
    else:
        # Long name (3+ words): brand + ALL numeric/unique identifiers must match
        # E.g. "Realme 16 Pro Plus" → need "realme" AND "16" (or "16 pro" phrase)
        if not brand_present:
            return False
        # Check for exact phrase match of brand + first numeric id
        if numeric_ids:
            phrase = brand_word + r'\s+\S*' + numeric_ids[0]
            if re.search(phrase, text_lower):
                return True
        # Fallback: need ≥ n-1 words all matching (almost full name)
        min_required = max(n - 1, 2)
        return matched_count >= min_required


# ─── Main API ─────────────────────────────────────────────────────────────────

def extract_specs(
    text: str,
    device_name: str = "",
    source_urls: Optional[List[str]] = None,
) -> Optional[DeviceSpecs]:
    """
    Extract device specifications from page text using regex patterns.

    When text contains multiple [src-N] sections, extracts from each
    independently and merges, preferring the source with more fields
    (structured sources like nanoreview over gsmarena's sparse text).

    Validates that each source section actually mentions the device name
    before extracting. Rejects pages about other devices.

    Args:
        text: Combined page text (from collect_pages_text or similar).
        device_name: Optional device name for the header.
        source_urls: URLs that contributed text.

    Returns:
        DeviceSpecs if enough specs were found, None otherwise.
    """
    if not text or len(text) < 100:
        return None

    # Split by [src-N] markers if present
    src_sections = re.split(r'\n?\[src-\d+\]\n?', text)
    src_sections = [s.strip() for s in src_sections if s.strip() and len(s.strip()) > 50]

    if len(src_sections) > 1:
        # Extract from each source independently, pick best
        # Only use sections that actually mention the device
        best_specs = None
        best_count = 0
        for i, section in enumerate(src_sections):
            if not _text_mentions_device(section, device_name):
                logger.debug(
                    "SpecExtractor: skipping source %d — doesn't mention '%s'",
                    i + 1, device_name[:40],
                )
                continue
            candidate = _extract_from_text(section, device_name, source_urls)
            if candidate and candidate.filled_count > best_count:
                best_specs = candidate
                best_count = candidate.filled_count

        # Also try full text as fallback (some fields span sources)
        # but only if full text mentions device
        full_specs = None
        if _text_mentions_device(text, device_name):
            full_specs = _extract_from_text(text, device_name, source_urls)

        # Merge: use best_specs as base, fill gaps from full_specs
        if best_specs and full_specs:
            for f in best_specs.__dataclass_fields__:
                if f in ("device_name", "source_urls", "confidence"):
                    continue
                if not getattr(best_specs, f) and getattr(full_specs, f):
                    setattr(best_specs, f, getattr(full_specs, f))
            # Recalculate confidence
            n = best_specs.filled_count
            best_specs.confidence = min(1.0, n / 10.0)

        specs = best_specs or full_specs
    else:
        # Single source: validate device name
        if not _text_mentions_device(text, device_name):
            logger.info(
                "SpecExtractor: text doesn't mention device '%s', skipping",
                device_name[:40],
            )
            return None
        specs = _extract_from_text(text, device_name, source_urls)

    if specs is None:
        return None

    # Post-process: add units, clean up values
    _postprocess_specs(specs)

    n = specs.filled_count
    logger.info(
        "SpecExtractor: %d fields extracted (confidence=%.2f) for '%s'",
        n, specs.confidence, device_name[:50],
    )

    if n < 4:
        logger.info("SpecExtractor: too few fields (%d < 4), returning None", n)
        return None

    return specs


def _postprocess_specs(specs: DeviceSpecs) -> None:
    """Add missing units and clean up extracted values."""
    # Display size: add "дюймов" or "дюйма" if just a number (no letters/quotes)
    if specs.display_size and not re.search(r'[а-яА-Яa-zA-Z"]', specs.display_size):
        clean = specs.display_size.replace(',', '.')
        try:
            v = float(clean)
            unit = 'дюймов' if v >= 5 else 'дюйма'
        except ValueError:
            unit = 'дюйма'
        specs.display_size = clean + ' ' + unit

    # Display refresh: add "Гц" if just a number
    if specs.display_refresh and not re.search(r'[а-яА-Яa-zA-Z]', specs.display_refresh):
        specs.display_refresh = specs.display_refresh + ' Гц'

    # Display brightness: add "нит" if just a number
    if specs.display_brightness and not re.search(r'[а-яА-Яa-zA-Z]', specs.display_brightness):
        specs.display_brightness = specs.display_brightness + ' нит'

    # Battery: normalize "мА·ч" → "мАч" for consistency
    if specs.battery:
        specs.battery = specs.battery.replace('мА·ч', 'мАч').replace('мА.ч', 'мАч')

    # Charging: add "Вт" if just a number
    if specs.charging and not re.search(r'[а-яА-Яa-zA-Z]', specs.charging):
        specs.charging = specs.charging + ' Вт'

    # Weight: add appropriate unit if just a number
    if specs.weight and not re.search(r'[а-яА-Яa-zA-Z]', specs.weight):
        try:
            w = float(specs.weight.replace(',', '.'))
            specs.weight = specs.weight + (' кг' if w < 10 else ' г')
        except ValueError:
            specs.weight = specs.weight + ' г'

    # RAM: add "ГБ" if just a number
    if specs.ram and not re.search(r'[а-яА-Яa-zA-Z]', specs.ram):
        specs.ram = specs.ram + ' ГБ'

    # Clean up processor: strip trailing whitespace
    if specs.processor:
        specs.processor = specs.processor.strip()

    # GPU-specific postprocessing
    if specs.boost_clock and not re.search(r'[а-яА-Яa-zA-Z]', specs.boost_clock):
        specs.boost_clock = specs.boost_clock + ' МГц'
    if specs.base_clock and not re.search(r'[а-яА-Яa-zA-Z]', specs.base_clock):
        specs.base_clock = specs.base_clock + ' МГц'
    if specs.tdp and not re.search(r'[а-яА-Яa-zA-Z]', specs.tdp):
        specs.tdp = specs.tdp + ' Вт'
    if specs.vram and not re.search(r'[а-яА-Яa-zA-Z]', specs.vram):
        specs.vram = specs.vram + ' ГБ'
    if specs.memory_bus and not re.search(r'[а-яА-Яa-zA-Z]', specs.memory_bus):
        specs.memory_bus = specs.memory_bus + ' бит'

    # Generic cleanup: strip trailing dots and whitespace from all string fields
    _skip_clean = {"device_name", "source_urls", "confidence", "is_laptop", "is_gpu"}
    for fname in specs.__dataclass_fields__:
        if fname in _skip_clean:
            continue
        val = getattr(specs, fname)
        if isinstance(val, str) and val:
            # Strip trailing period(s) and whitespace
            val = val.strip().rstrip('.')
            setattr(specs, fname, val)

    # Recalculate confidence after postprocessing
    n = specs.filled_count
    specs.confidence = min(1.0, n / 10.0)


def _normalize_spec_table(text: str) -> str:
    """Normalize multi-line spec tables: 'Label, unit\\nvalue' → 'Label: value unit'.

    Many retail sites (doctorhead.ru, musicmarket.by, etc.) render spec tables
    as separate lines:
        Сопротивление, Ом
        32
    This merges them so regex extractors can match: Сопротивление: 32 Ом
    Also handles 'Label\\nvalue' lines without embedded units.
    """
    # "Label, UNIT\nNUMBER" → "Label: NUMBER UNIT"
    text = re.sub(
        r'([\w][\w\s]*?),\s*'
        r'(Гц|кГц|МГц|Ом|дБ|мм|см|м\b|г\b|кг|мВт|Вт|мАч|час\w*|°|%)'
        r'\s*\n\s*(\d[\d.,]*)',
        r'\1: \3 \2',
        text, flags=re.IGNORECASE,
    )
    # "Label\nshort-value" for non-numeric fields (e.g. "Тип акустического оформления\nзакрытые")
    text = re.sub(
        r'((?:Тип|Конструкци|Акустическ|Подключени|Разъ[её]м|Штекер|Коннектор|Микрофон|Шумоподавлен|Bluetooth|Цвет|Форма)\w*(?:\s+\w+){0,3})\s*\n\s*([а-яa-z\d][\w\s.,/\-]{1,40})(?=\n|$)',
        r'\1: \2',
        text, flags=re.IGNORECASE,
    )
    return text


def _extract_from_text(
    text: str,
    device_name: str = "",
    source_urls: Optional[List[str]] = None,
) -> Optional[DeviceSpecs]:
    """Extract specs from a single text block."""
    text = _normalize_spec_table(text)
    is_gpu = _is_gpu_query(device_name, text)
    is_headphone = False if is_gpu else _is_headphone_query(device_name, text)
    is_laptop = False if (is_gpu or is_headphone) else _is_laptop_query(device_name, text)
    specs = DeviceSpecs(
        device_name=device_name,
        is_laptop=is_laptop,
        is_gpu=is_gpu,
        is_headphone=is_headphone,
        source_urls=source_urls or [],
    )

    if is_gpu:
        _extract_gpu_specs(text, specs)
        # Also extract process_nm and dimensions from generic extractors
        _extract_processor(text, specs, device_name=device_name)
        _extract_body(text, specs)
    elif is_headphone:
        _extract_headphone_specs(text, specs)
    else:
        _extract_display(text, specs)
        _extract_processor(text, specs, device_name=device_name)
        _extract_memory(text, specs)
        _extract_battery(text, specs)
        if not is_laptop:
            _extract_camera(text, specs)
        _extract_body(text, specs)
        _extract_system(text, specs)
        if is_laptop:
            _extract_laptop_extras(text, specs)

    n = specs.filled_count
    specs.confidence = min(1.0, n / 10.0)
    return specs if n > 0 else None
