"""
Diagnostics Domain Resolver — извлечение домена из текста.

Определяет домен диагностики из пользовательского текста:
  "не работает wifi" → domain="network"
  "нет звука" → domain="audio"
  "тормозит система" → domain="system"

Используется IntentBridge для обогащения Intent.domain
при IntentType.DIAGNOSE.

Phase: INTEGRATION LAYER / Phase 1
"""

from __future__ import annotations

import re
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Domain patterns ─────────────────────────────────────────────────────────

_DOMAIN_PATTERNS: Dict[str, List[re.Pattern]] = {
    "network": [
        re.compile(r"(интернет|сет[ьи]|wifi|wi-fi|ethernet|vpn|dns|ip|"
                    r"dhcp|ping|маршрут|firewall|прокси|proxy|"
                    r"не\s+подключ|не\s+работает\s+сеть|нет\s+интернет)",
                    re.IGNORECASE),
    ],
    "audio": [
        re.compile(r"(звук|аудио|audio|микрофон|наушники|динамик|"
                    r"pipewire|pulseaudio|alsa|bluetooth.*наушник|"
                    r"нет\s+звук|пропал\s+звук|тихий\s+звук)",
                    re.IGNORECASE),
    ],
    "display": [
        re.compile(r"(экран|дисплей|монитор|разрешен|display|xrandr|"
                    r"wayland|x11|gpu|видеокарт|nvidia|amd|intel.*graph|"
                    r"мерцает|мигает|чёрный\s+экран|артефакт)",
                    re.IGNORECASE),
    ],
    "bluetooth": [
        re.compile(r"(блютуз|bluetooth|bt|bluez|"
                    r"не\s+подключ.*bluetooth|bluetooth.*не\s+работ)",
                    re.IGNORECASE),
    ],
    "disk": [
        re.compile(r"(диск|hdd|ssd|nvme|раздел|файловая\s+систем|"
                    r"монтирован|mount|fstab|partition|"
                    r"нет\s+места|место\s+на\s+диск|btrfs|ext4|ntfs)",
                    re.IGNORECASE),
    ],
    "package": [
        re.compile(r"(пакет|pacman|yay|apt|dnf|flatpak|snap|"
                    r"обновлен|update|upgrade|зависимост|dependency|"
                    r"конфликт.*пакет|сломан.*пакет)",
                    re.IGNORECASE),
    ],
    "service": [
        re.compile(r"(сервис|служб|systemd|systemctl|daemon|"
                    r"не\s+запуска.*сервис|сервис.*не\s+работ|"
                    r"restart|reload|status.*service)",
                    re.IGNORECASE),
    ],
    "boot": [
        re.compile(r"(загрузк|boot|grub|initramfs|mkinitcpio|"
                    r"не\s+загруж|ядро|kernel|dracut|"
                    r"чёрный\s+экран.*загрузк|висит\s+на\s+загрузк)",
                    re.IGNORECASE),
    ],
    "usb": [
        re.compile(r"(usb|флешк|flash\s*drive|внешн.*диск|"
                    r"не\s+видит.*usb|usb.*не\s+работ|"
                    r"мышь|клавиатур|принтер|сканер)",
                    re.IGNORECASE),
    ],
    "performance": [
        re.compile(r"(тормоз|медленн|лагает|зависает|freeze|"
                    r"OOM|out\s+of\s+memory|swap|overh|"
                    r"нагрев|температур|перегрев|throttl|fan)",
                    re.IGNORECASE),
    ],
    "system": [
        re.compile(r"(систем|system|обзор|статус|health|"
                    r"crash|segfault|kernel\s*panic|"
                    r"журнал|логи|logs?|dmesg|journalctl)",
                    re.IGNORECASE),
    ],
}

# Приоритет доменов (более специфичные первые)
_DOMAIN_PRIORITY = [
    "bluetooth", "usb", "audio", "display", "network",
    "boot", "disk", "package", "service", "performance", "system",
]


def resolve_domain(text: str) -> Tuple[str, float]:
    """
    Определить домен диагностики из текста.

    Returns:
        (domain, confidence) — ("network", 0.9) или ("system", 0.3).
    """
    if not text:
        return "system", 0.1

    text_lower = text.lower()
    matches = []

    for domain in _DOMAIN_PRIORITY:
        for pattern in _DOMAIN_PATTERNS.get(domain, []):
            if pattern.search(text_lower):
                matches.append(domain)
                break

    if not matches:
        return "system", 0.3

    # Первый матч (по приоритету) — основной домен
    primary = matches[0]
    confidence = 0.9 if len(matches) == 1 else 0.7

    return primary, confidence


def get_available_domains() -> List[str]:
    """Список доступных диагностических доменов."""
    return list(_DOMAIN_PATTERNS.keys())


def get_domain_keywords(domain: str) -> List[str]:
    """Ключевые слова для домена (для UI подсказок)."""
    _KEYWORDS = {
        "network": ["WiFi", "Ethernet", "DNS", "VPN", "firewall"],
        "audio": ["звук", "микрофон", "наушники", "PipeWire"],
        "display": ["экран", "монитор", "GPU", "разрешение"],
        "bluetooth": ["Bluetooth", "BlueZ", "наушники BT"],
        "disk": ["HDD", "SSD", "разделы", "файловая система"],
        "package": ["пакеты", "обновления", "зависимости"],
        "service": ["systemd", "сервисы", "демоны"],
        "boot": ["загрузка", "GRUB", "initramfs", "ядро"],
        "usb": ["USB", "периферия", "принтер"],
        "performance": ["производительность", "перегрев", "память"],
        "system": ["журналы", "статус", "общая диагностика"],
    }
    return _KEYWORDS.get(domain, [])
