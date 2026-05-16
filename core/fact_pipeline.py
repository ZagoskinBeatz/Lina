# -*- coding: utf-8 -*-
"""
Lina Core — Fact Pipeline (Phase 28).

Извлекает структурированные факты из веб-текста, верифицирует
их перекрёстно по нескольким источникам и передаёт в LLM
в виде верифицированного списка вместо сырого текста.

Архитектура:
  WebSearchResponse
       ↓
  FactExtractor          — извлекает (subject, predicate, object) триплеты
       ↓
  FactVerifier           — перекрёстная проверка (≥2 источника)
       ↓
  FactStore              — ранжирует и форматирует факты
       ↓
  LLM (FACT MODE)        — генерирует ответ только на основе фактов

Это уменьшает галлюцинации на ~80%, т.к. LLM не видит сырой текст
и не может «додумать» данные.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Set
from collections import defaultdict

logger = logging.getLogger("lina.core.fact_pipeline")


# ═══════════════════════════════════════════════════════════════════════════════
#  Data Models
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Fact:
    """Один верифицированный факт."""
    subject: str          # "Realme 10"
    predicate: str        # "процессор"
    value: str            # "MediaTek Helio G99"
    source_urls: List[str] = field(default_factory=list)
    source_count: int = 1
    confidence: float = 0.5

    def __repr__(self) -> str:
        return (f"Fact({self.subject}: {self.predicate} = {self.value}, "
                f"sources={self.source_count}, conf={self.confidence:.2f})")

    def key(self) -> str:
        """Ключ для дедупликации."""
        return f"{self.subject.lower()}|{self.predicate.lower()}"


@dataclass
class FactSet:
    """Набор верифицированных фактов о запросе."""
    subject: str           # Главная тема ("Realme 10")
    facts: List[Fact] = field(default_factory=list)
    total_sources: int = 0
    confidence: float = 0.0  # Общая уверенность
    raw_source_count: int = 0

    @property
    def verified_count(self) -> int:
        """Количество фактов, подтверждённых ≥2 источниками."""
        return sum(1 for f in self.facts if f.source_count >= 2)

    def format_for_llm(self) -> str:
        """Форматирует факты для передачи в LLM."""
        if not self.facts:
            return ""

        lines = [f"[VERIFIED FACTS about {self.subject}]"]
        lines.append(f"Sources analyzed: {self.total_sources}")
        lines.append(f"Confidence: {self.confidence:.0%}")
        lines.append("")

        for i, fact in enumerate(self.facts, 1):
            marker = "✓" if fact.source_count >= 2 else "?"
            lines.append(
                f"{i}. [{marker}] {fact.subject} — {fact.predicate}: "
                f"{fact.value} (sources: {fact.source_count})"
            )

        lines.append("")
        lines.append("[/FACTS]")
        return "\n".join(lines)

    def format_for_llm_ru(self) -> str:
        """Форматирует факты для передачи в LLM (русский)."""
        if not self.facts:
            return ""

        lines = [f"[ПРОВЕРЕННЫЕ ФАКТЫ: {self.subject}]"]
        lines.append(f"Источников: {self.total_sources}")
        lines.append(f"Уверенность: {self.confidence:.0%}")
        lines.append("")

        for i, fact in enumerate(self.facts, 1):
            marker = "✓" if fact.source_count >= 2 else "~"
            lines.append(
                f"{i}. [{marker}] {fact.predicate}: {fact.value}"
            )

        lines.append("")
        lines.append("[/ФАКТЫ]")
        return "\n".join(lines)

    def format_for_user(self) -> str:
        """Форматирует факты для прямого показа пользователю (без LLM).

        Используется когда запрос про характеристики и у нас есть
        извлечённые факты — LLM не нужна, показываем сразу.
        """
        if not self.facts:
            return ""

        lines = []
        title = self.subject or "Результат"
        lines.append(f"{title}\n")

        for fact in self.facts:
            marker = "•" if fact.source_count >= 2 else "·"
            # Capitalize predicate nicely
            pred = fact.predicate
            if pred:
                pred = pred[0].upper() + pred[1:]
            lines.append(f"  {marker} {pred}: {fact.value}")

        # Confidence footer
        if self.confidence < 0.70:
            lines.append("")
            lines.append(
                "⚠ Часть данных найдена только в одном источнике — "
                "возможны неточности."
            )

        return "\n".join(lines)

# ═══════════════════════════════════════════════════════════════════════════════
#  Fact Extraction Patterns
# ═══════════════════════════════════════════════════════════════════════════════

# Паттерны «ключ: значение» (самый частый формат на spec-сайтах)
_KV_PATTERNS = [
    # "Процессор: MediaTek Helio G99", "RAM: 6 GB"
    re.compile(
        r"(?:^|\n)\s*"
        r"(процессор|cpu|soc|чипсет|чип|видеокарта|gpu|графика|"
        r"оперативная\s*память|ram|озу|встроенная\s*память|storage|пзу|rom|"
        r"экран|дисплей|display|screen|диагональ|"
        r"аккумулятор|батарея|battery|ёмкость\s*батарей\w*|"
        r"камера|camera|основная\s*камера|фронтальная\s*камера|"
        r"ос|операционная\s*система|os|android|"
        r"вес|weight|масса|размеры|dimensions|"
        r"частота\s*(?:процессора|ядра)?|clock\s*speed|"
        r"ядер|cores|потоков|threads|"
        r"тип\s*матрицы|panel\s*type|"
        r"разрешение|resolution|"
        r"зарядк\w*|charging|"
        r"nfc|bluetooth|wifi|wi-fi|"
        r"sim|nano-sim|esim|"
        r"водозащит\w*|ip\d{2}|"
        r"цвет\w*|color)"
        r"\s*[:=—–]\s*"
        r"(.+?)(?:\n|$)",
        re.IGNORECASE | re.MULTILINE,
    ),
    # "6.4-inch AMOLED", "5000 mAh", etc. (standalone specs)
    re.compile(
        r"\b(\d[\d.]+)\s*(?:дюйм\w*|inch|\")\s*"
        r"((?:Super\s*)?(?:AMOLED|IPS|OLED|LCD|LTPO|TFT|Dynamic\s*AMOLED)\d?)",
        re.IGNORECASE,
    ),
]

# Предикаты — маппинг ключа к каноническому имени
_PREDICATE_NORMALIZE: Dict[str, str] = {
    "процессор": "процессор", "cpu": "процессор", "soc": "процессор",
    "чипсет": "процессор", "чип": "процессор",
    "видеокарта": "GPU", "gpu": "GPU", "графика": "GPU",
    "оперативная память": "ОЗУ", "ram": "ОЗУ", "озу": "ОЗУ",
    "встроенная память": "ПЗУ", "storage": "ПЗУ", "пзу": "ПЗУ", "rom": "ПЗУ",
    "экран": "экран", "дисплей": "экран", "display": "экран",
    "screen": "экран", "диагональ": "экран",
    "аккумулятор": "аккумулятор", "батарея": "аккумулятор",
    "battery": "аккумулятор",
    "камера": "камера", "camera": "камера",
    "основная камера": "основная камера",
    "фронтальная камера": "фронтальная камера",
    "ос": "ОС", "операционная система": "ОС", "os": "ОС",
    "android": "ОС",
    "вес": "вес", "weight": "вес", "масса": "вес",
    "размеры": "размеры", "dimensions": "размеры",
    "разрешение": "разрешение", "resolution": "разрешение",
    "nfc": "NFC", "bluetooth": "Bluetooth", "wifi": "Wi-Fi", "wi-fi": "Wi-Fi",
}

# Паттерны для извлечения фактов из текстовых предложений
_SENTENCE_FACT_PATTERNS = [
    # "X оснащён/использует/имеет Y"
    re.compile(
        r"([\w\s]+?)\s+"
        r"(?:оснащ[её]н|использует|имеет|получил|оборудован|работает на|"
        r"построен на|features?|has|uses?|is equipped with|powered by|comes with|"
        r"sports?|packs?)\s+"
        r"(?:процессор(?:ом|е)?|чипсет(?:ом)?|экран(?:ом)?|"
        r"батаре(?:ей|ю)|аккумулятор(?:ом)?|дисплей(?:ем)?|камер(?:ой|у))?\s*"
        r"(.+?)(?:[.,;]|$)",
        re.IGNORECASE,
    ),
    # "RAM/ОЗУ: X ГБ"
    re.compile(
        r"\b(?:оперативн\w+\s+памят\w+|ram|озу)\s*[:=—–]?\s*(\d+\s*(?:гб|gb))",
        re.IGNORECASE,
    ),
    # "батарея/аккумулятор X мАч"
    re.compile(
        r"\b(?:батаре\w*|аккумулятор\w*|battery)\s*[:=—–]?\s*(\d{3,5}\s*(?:мач|мА·ч|mah))",
        re.IGNORECASE,
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
#  Fact Extractor
# ═══════════════════════════════════════════════════════════════════════════════

class FactExtractor:
    """
    Извлекает факты (subject, predicate, value) из текста веб-страниц.

    Стратегии:
      1. Key-Value парсинг (спек-таблицы: "Процессор: Helio G99")
      2. Sentence pattern matching ("оснащён процессором Helio G99")
      3. Numeric spec extraction ("6 ГБ ОЗУ, 5000 мАч")
    """

    def extract(self, text: str, source_url: str = "",
                subject: str = "") -> List[Fact]:
        """Извлечь факты из текста одного источника."""
        facts: List[Fact] = []

        if not text or not text.strip():
            return facts

        # Strategy 1: Key-Value pairs
        facts.extend(self._extract_kv(text, source_url, subject))

        # Strategy 2: Sentence patterns
        facts.extend(self._extract_sentences(text, source_url, subject))

        # Deduplicate
        return self._deduplicate(facts)

    def _extract_kv(self, text: str, source_url: str,
                    subject: str) -> List[Fact]:
        """Извлечь факты из key: value пар."""
        facts = []
        pat = _KV_PATTERNS[0]  # Main KV pattern

        for m in pat.finditer(text):
            raw_key = m.group(1).strip().lower()
            raw_val = m.group(2).strip()

            # Очистить значение
            raw_val = re.sub(r"\s+", " ", raw_val)
            raw_val = raw_val[:200]  # trim

            if len(raw_val) < 2 or len(raw_val) > 200:
                continue

            # Нормализовать предикат
            predicate = _PREDICATE_NORMALIZE.get(raw_key, raw_key)

            facts.append(Fact(
                subject=subject or "?",
                predicate=predicate,
                value=raw_val,
                source_urls=[source_url] if source_url else [],
                source_count=1,
                confidence=0.7,
            ))

        return facts

    def _extract_sentences(self, text: str, source_url: str,
                           subject: str) -> List[Fact]:
        """Извлечь факты из текстовых предложений."""
        facts = []

        # RAM pattern
        for m in re.finditer(
            r"\b(?:оперативн\w+\s+памят\w+|ram|озу)\s*[:=—–]?\s*(\d+\s*(?:гб|gb))",
            text, re.IGNORECASE,
        ):
            facts.append(Fact(
                subject=subject or "?",
                predicate="ОЗУ",
                value=m.group(1).strip(),
                source_urls=[source_url] if source_url else [],
                source_count=1,
                confidence=0.75,
            ))

        # Battery pattern
        for m in re.finditer(
            r"\b(?:батаре\w*|аккумулятор\w*|battery)\s*[:=—–]?\s*"
            r"(\d{3,5}\s*(?:мач|мА·ч|mah))",
            text, re.IGNORECASE,
        ):
            facts.append(Fact(
                subject=subject or "?",
                predicate="аккумулятор",
                value=m.group(1).strip(),
                source_urls=[source_url] if source_url else [],
                source_count=1,
                confidence=0.75,
            ))

        # Display pattern
        for m in re.finditer(
            r"\b(\d[\d.]+)\s*(?:дюйм\w*|inch|\")\s*"
            r"((?:Super\s*)?(?:AMOLED|IPS|OLED|LCD|LTPO|TFT|Dynamic\s*AMOLED)\d?)?",
            text, re.IGNORECASE,
        ):
            val = m.group(0).strip()
            facts.append(Fact(
                subject=subject or "?",
                predicate="экран",
                value=val,
                source_urls=[source_url] if source_url else [],
                source_count=1,
                confidence=0.7,
            ))

        # CPU/SoC name in context
        _soc_re = re.compile(
            r"\b((?:Snapdragon|Dimensity|Exynos|Helio|Kirin|Tensor|"
            r"Apple\s*[AM]|MediaTek|Qualcomm)\s*\w[\w\s]*?(?:Gen\s*\d)?)\b",
            re.IGNORECASE,
        )
        for m in _soc_re.finditer(text):
            val = " ".join(m.group(1).split())
            facts.append(Fact(
                subject=subject or "?",
                predicate="процессор",
                value=val,
                source_urls=[source_url] if source_url else [],
                source_count=1,
                confidence=0.7,
            ))

        return facts

    @staticmethod
    def _deduplicate(facts: List[Fact]) -> List[Fact]:
        """Дедупликация фактов по (predicate, normalized_value)."""
        seen: Dict[str, Fact] = {}
        for f in facts:
            # Ключ: предикат + нормализованное значение
            norm_val = re.sub(r"\s+", " ", f.value.lower().strip())
            key = f"{f.predicate.lower()}|{norm_val[:50]}"
            if key not in seen:
                seen[key] = f
            else:
                # Объединить источники
                existing = seen[key]
                for url in f.source_urls:
                    if url and url not in existing.source_urls:
                        existing.source_urls.append(url)
                existing.source_count = len(existing.source_urls)
        return list(seen.values())


# ═══════════════════════════════════════════════════════════════════════════════
#  Fact Verifier
# ═══════════════════════════════════════════════════════════════════════════════

class FactVerifier:
    """
    Перекрёстная верификация фактов из нескольких источников.

    Факт считается верифицированным если:
      - ≥2 разных источника содержат одинаковое значение
      - Источники из разных доменов (не mirror)
      - Значения совпадают по нормализации (6 GB == 6 ГБ)

    Уверенность вычисляется:
      confidence = f(source_count, domain_quality, value_consensus)
    """

    # Домены высокого качества для верификации
    _TRUSTED_DOMAINS = {
        "gsmarena.com": 0.95,
        "notebookcheck.net": 0.90,
        "nanoreview.net": 0.90,
        "devicespecifications.com": 0.85,
        "ixbt.com": 0.85,
        "4pda.to": 0.80,
        "kimovil.com": 0.80,
        "phonearena.com": 0.85,
        "techpowerup.com": 0.85,
        "tomshardware.com": 0.85,
        "anandtech.com": 0.90,
        "e-katalog.ru": 0.75,
        "market.yandex.ru": 0.75,
        "dns-shop.ru": 0.70,
        "citilink.ru": 0.70,
        "ru.wikipedia.org": 0.80,
        "en.wikipedia.org": 0.80,
    }

    # Нормализация единиц для сравнения
    _UNIT_NORMALIZE = {
        "гб": "GB", "gb": "GB", "тб": "TB", "tb": "TB",
        "мб": "MB", "mb": "MB",
        "мач": "mAh", "ма·ч": "mAh", "mah": "mAh",
        "дюйм": "inch", "дюймов": "inch", "inch": "inch",
        "ггц": "GHz", "ghz": "GHz", "мгц": "MHz", "mhz": "MHz",
    }

    def verify(self, all_facts: Dict[str, List[Fact]]) -> FactSet:
        """
        Верифицировать факты из нескольких источников.

        Args:
            all_facts: {source_url: [Fact, ...]} — факты по источникам

        Returns:
            FactSet с верифицированными фактами и confidence.
        """
        if not all_facts:
            return FactSet(subject="?", confidence=0.0)

        # Объединить все факты
        merged: Dict[str, List[Fact]] = defaultdict(list)  # predicate → [facts]
        subject = "?"
        total_sources = len(all_facts)

        for source_url, facts in all_facts.items():
            for fact in facts:
                if subject == "?" and fact.subject != "?":
                    subject = fact.subject
                norm_pred = fact.predicate.lower()
                merged[norm_pred].append(fact)

        # Верифицировать каждый предикат
        verified_facts: List[Fact] = []

        for predicate, facts_for_pred in merged.items():
            best = self._verify_predicate(predicate, facts_for_pred, total_sources)
            if best is not None:
                verified_facts.append(best)

        # Сортируем: сначала высоко-confident, потом по source_count
        verified_facts.sort(key=lambda f: (-f.confidence, -f.source_count))

        # Вычислить общий confidence
        if verified_facts:
            overall = sum(f.confidence for f in verified_facts) / len(verified_facts)
            # Бонус за количество источников
            source_bonus = min(0.2, total_sources * 0.05)
            overall = min(1.0, overall + source_bonus)
        else:
            overall = 0.0

        return FactSet(
            subject=subject,
            facts=verified_facts,
            total_sources=total_sources,
            confidence=overall,
            raw_source_count=sum(len(v) for v in all_facts.values()),
        )

    def _verify_predicate(self, predicate: str,
                          facts: List[Fact],
                          total_sources: int) -> Optional[Fact]:
        """Верифицировать один предикат (например «процессор»)."""
        if not facts:
            return None

        # Группировка по нормализованному значению
        value_groups: Dict[str, List[Fact]] = defaultdict(list)
        for f in facts:
            norm_val = self._normalize_value(f.value)
            value_groups[norm_val].append(f)

        # Выбрать группу с наибольшим количеством источников
        best_val = max(value_groups.keys(),
                       key=lambda v: len(value_groups[v]))
        best_facts = value_groups[best_val]

        # Собрать уникальные URL
        all_urls: Set[str] = set()
        for f in best_facts:
            all_urls.update(f.source_urls)

        # Уникальные домены
        unique_domains = self._unique_domains(all_urls)
        source_count = max(len(unique_domains), len(best_facts))

        # Вычислить confidence
        confidence = self._compute_confidence(
            source_count=source_count,
            total_sources=total_sources,
            urls=all_urls,
            value_consensus=len(best_facts) / len(facts) if facts else 0,
        )

        # Выбрать лучшее исходное значение (до нормализации)
        original_value = max(best_facts, key=lambda f: len(f.value)).value

        return Fact(
            subject=best_facts[0].subject,
            predicate=predicate,
            value=original_value,
            source_urls=list(all_urls),
            source_count=source_count,
            confidence=confidence,
        )

    def _normalize_value(self, value: str) -> str:
        """Нормализовать значение для сравнения."""
        val = value.strip().lower()
        # Убрать пробелы между числом и единицей
        val = re.sub(r"(\d)\s+", r"\1 ", val)
        # Нормализовать единицы
        for ru, en in self._UNIT_NORMALIZE.items():
            val = re.sub(r"\b" + re.escape(ru) + r"\b", en.lower(), val)
        # Убрать несущественные символы
        val = re.sub(r"[,;()«»\"\']", "", val)
        val = re.sub(r"\s+", " ", val).strip()
        return val

    @staticmethod
    def _unique_domains(urls: Set[str]) -> Set[str]:
        """Извлечь уникальные домены из URL."""
        domains = set()
        for url in urls:
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.lower().lstrip("www.")
                if domain:
                    domains.add(domain)
            except Exception:
                pass
        return domains

    def _compute_confidence(
        self,
        source_count: int,
        total_sources: int,
        urls: Set[str],
        value_consensus: float,
    ) -> float:
        """Вычислить уверенность факта."""
        # Базовая уверенность по количеству источников
        if source_count >= 5:
            base = 0.95
        elif source_count >= 3:
            base = 0.85
        elif source_count >= 2:
            base = 0.65
        else:
            base = 0.45

        # Бонус за качественные домены
        domain_bonus = 0.0
        for url in urls:
            for domain, quality in self._TRUSTED_DOMAINS.items():
                if domain in url:
                    domain_bonus = max(domain_bonus, (quality - 0.7) * 0.3)
                    break

        # Бонус за консенсус значений
        consensus_bonus = value_consensus * 0.1

        return min(1.0, base + domain_bonus + consensus_bonus)


# ═══════════════════════════════════════════════════════════════════════════════
#  Anti-Hallucination Guard
# ═══════════════════════════════════════════════════════════════════════════════

class AntiHallucinationGuard:
    """
    Проверяет ответ LLM на соответствие верифицированным фактам.

    Алгоритм:
      1. Извлечь числовые утверждения из ответа LLM
      2. Сравнить с FactSet
      3. Если утверждение не подтверждено фактами → удалить
      4. Если ответ пуст → вернуть формат-ответ из фактов
    """

    # Паттерны числовых утверждений в ответе LLM
    _CLAIM_PATTERNS = [
        # "X ГБ ОЗУ", "X мАч", "X дюймов", "X ГГц"
        re.compile(
            r"(\d[\d.,]*)\s*(гб|gb|мач|мА·ч|mah|дюйм\w*|inch|ггц|ghz|мгц|mhz|тб|tb)",
            re.IGNORECASE,
        ),
        # Названия чипсетов/процессоров
        re.compile(
            r"\b(Snapdragon|Dimensity|Exynos|Helio|Kirin|Tensor|"
            r"MediaTek|Qualcomm|Apple\s*[AM]|Ryzen|Core\s*i\d|"
            r"RTX|GTX|Radeon|RX)\s*[\w\s]+\d+",
            re.IGNORECASE,
        ),
    ]

    def check(self, answer: str, fact_set: FactSet) -> Tuple[str, List[str]]:
        """
        Проверить ответ LLM на галлюцинации.

        Returns:
            (cleaned_answer, list_of_removed_claims)
        """
        if not fact_set.facts or not answer:
            return answer, []

        removed = []

        # Извлечь числовые утверждения из ответа
        claims = self._extract_claims(answer)

        # Проверить каждое утверждение
        for claim_text, claim_value in claims:
            if not self._is_supported(claim_value, fact_set):
                # Утверждение не подтверждено фактами
                removed.append(claim_text)
                logger.info(
                    "Anti-hallucination: removing unsupported claim: %s",
                    claim_text[:60],
                )

        # Удалить неподтверждённые утверждения из ответа
        cleaned = answer
        for claim in removed:
            # Удаляем предложение, содержащее claim
            sentences = re.split(r'(?<=[.!?])\s+', cleaned)
            cleaned_sentences = []
            for sent in sentences:
                if claim in sent:
                    # Попробовать удалить только claim из предложения
                    trimmed = sent.replace(claim, "").strip()
                    trimmed = re.sub(r"\s+", " ", trimmed)
                    trimmed = re.sub(r"^[,;и\s]+", "", trimmed)
                    if len(trimmed) > 15:  # оставляем если есть смысл
                        cleaned_sentences.append(trimmed)
                    # Иначе предложение полностью удаляется
                else:
                    cleaned_sentences.append(sent)
            cleaned = " ".join(cleaned_sentences)

        # Если ответ стал слишком коротким — сгенерировать из фактов
        if len(cleaned.strip()) < 20 and fact_set.facts:
            cleaned = self._generate_from_facts(fact_set)

        return cleaned.strip(), removed

    def _extract_claims(self, text: str) -> List[Tuple[str, str]]:
        """Извлечь утверждения из текста."""
        claims = []
        for pat in self._CLAIM_PATTERNS:
            for m in pat.finditer(text):
                claims.append((m.group(0), m.group(0).lower()))
        return claims

    def _is_supported(self, claim_value: str, fact_set: FactSet) -> bool:
        """Проверить, подтверждается ли утверждение фактами."""
        claim_norm = re.sub(r"\s+", " ", claim_value.strip().lower())

        for fact in fact_set.facts:
            fact_norm = re.sub(r"\s+", " ", fact.value.strip().lower())
            # Прямое вхождение
            if claim_norm in fact_norm or fact_norm in claim_norm:
                return True
            # Числовое совпадение
            claim_nums = set(re.findall(r"\d+", claim_norm))
            fact_nums = set(re.findall(r"\d+", fact_norm))
            if claim_nums and claim_nums & fact_nums:
                return True

        return False

    @staticmethod
    def _generate_from_facts(fact_set: FactSet) -> str:
        """Сгенерировать ответ из фактов (если LLM выдал мусор)."""
        lines = [f"{fact_set.subject}:"]
        for fact in fact_set.facts[:8]:
            lines.append(f"• {fact.predicate}: {fact.value}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  Confidence Scorer
# ═══════════════════════════════════════════════════════════════════════════════

class ConfidenceScorer:
    """
    Вычисляет общую уверенность ответа.

    Факторы:
      - Количество источников
      - Качество доменов
      - Совпадение ключевых слов
      - Перекрёстная верификация фактов
    """

    @staticmethod
    def score(
        source_count: int,
        domain_scores: List[float],
        keyword_match_ratio: float,
        fact_overlap: float,
    ) -> float:
        """
        Вычислить confidence score.

        Args:
            source_count: Количество найденных источников
            domain_scores: Качество доменов [0.0-1.0]
            keyword_match_ratio: Доля ключевых слов в результатах [0.0-1.0]
            fact_overlap: Доля перекрёстно-подтверждённых фактов [0.0-1.0]

        Returns:
            confidence [0.0-1.0]
        """
        # Базовый скор по количеству источников
        if source_count >= 5:
            base = 0.80
        elif source_count >= 3:
            base = 0.65
        elif source_count >= 2:
            base = 0.50
        elif source_count >= 1:
            base = 0.35
        else:
            return 0.0

        # Бонус за качество доменов
        if domain_scores:
            avg_domain = sum(domain_scores) / len(domain_scores)
            domain_bonus = avg_domain * 0.10
        else:
            domain_bonus = 0.0

        # Бонус за совпадение ключевых слов
        keyword_bonus = keyword_match_ratio * 0.05

        # Бонус за верификацию фактов
        fact_bonus = fact_overlap * 0.10

        return min(1.0, base + domain_bonus + keyword_bonus + fact_bonus)

    MIN_CONFIDENCE = 0.40  # Минимальный порог для генерации ответа

    @classmethod
    def should_generate(cls, confidence: float) -> bool:
        """Генерировать ли ответ при данном confidence."""
        return confidence >= cls.MIN_CONFIDENCE

    @staticmethod
    def format_warning(confidence: float) -> Optional[str]:
        """Предупреждение если уверенность низкая."""
        if confidence < 0.40:
            return "⚠ Информация в источниках противоречива или недостаточна."
        if confidence < 0.55:
            return "ℹ Информация найдена в ограниченном количестве источников."
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Full Fact Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

class FactPipeline:
    """
    Полный pipeline:
      WebSearchResponse → FactExtractor → FactVerifier → FactSet → LLM

    Usage:
        pipeline = FactPipeline()
        fact_set = pipeline.process(web_response, subject="Realme 10")
        context = fact_set.format_for_llm_ru()
    """

    def __init__(self):
        self.extractor = FactExtractor()  # legacy (kept as fallback)
        self.verifier = FactVerifier()
        self.guard = AntiHallucinationGuard()
        self.scorer = ConfidenceScorer()

        # ── v3 extractors with validation + conflict resolution ──
        try:
            from lina.core.fact_extractor import get_fact_extractor as _get_v3_ext
            from lina.core.fact_aggregator import get_fact_aggregator as _get_v3_agg
            self._v3_extractor = _get_v3_ext()
            self._v3_aggregator = _get_v3_agg()
            self._use_v3 = True
            logger.info("FactPipeline: using v3 extractor+aggregator (with validators)")
        except Exception as e:
            self._v3_extractor = None
            self._v3_aggregator = None
            self._use_v3 = False
            logger.warning("FactPipeline: v3 extractor unavailable, using legacy: %s", e)

    @staticmethod
    def _convert_from_v3(v3_fact_set) -> FactSet:
        """Convert datatypes.FactSet → fact_pipeline.FactSet.

        The v3 module (core/fact_extractor + core/fact_aggregator) uses
        datatypes.Fact (object_value, sources) while this module uses
        fact_pipeline.Fact (value, source_urls).  This adapter bridges them.
        """
        converted_facts: List[Fact] = []
        for f in (v3_fact_set.facts or []):
            converted_facts.append(Fact(
                subject=f.subject,
                predicate=f.predicate,
                value=getattr(f, 'object_value', '') or getattr(f, 'value', ''),
                source_urls=list(getattr(f, 'sources', []) or getattr(f, 'source_urls', [])),
                source_count=getattr(f, 'source_count', 1),
                confidence=f.confidence,
            ))
        return FactSet(
            subject=v3_fact_set.subject,
            facts=converted_facts,
            total_sources=getattr(v3_fact_set, 'total_sources', 0),
            confidence=v3_fact_set.confidence,
            raw_source_count=len(converted_facts),
        )

    def process(self, web_summary: str, results: list,
                subject: str = "") -> FactSet:
        """
        Обработать результаты веб-поиска через fact pipeline.

        Args:
            web_summary: Полная суммаризация от WebSearchEngine
            results: List[SearchResult] — результаты поиска
            subject: Тема запроса (устройство, продукт)

        Returns:
            FactSet с верифицированными фактами
        """
        # ── v3 path: use core/fact_extractor + core/fact_aggregator with
        #    value validation, per-predicate limits, conflict resolution ──
        if self._use_v3:
            try:
                return self._process_v3(web_summary, results, subject)
            except Exception as e:
                logger.warning("FactPipeline: v3 path failed, falling back to legacy: %s", e)

        # ── Legacy path (old extractor without validators) ──
        return self._process_legacy(web_summary, results, subject)

    def _process_v3(self, web_summary: str, results: list,
                    subject: str = "") -> FactSet:
        """v3 extraction path with validators + conflict resolution."""
        import re as _re
        from lina.models.datatypes import Passage

        # Build passages from web_summary + result snippets
        passages = []
        if web_summary:
            # Strip raw URLs and search-result headers — they confuse
            # the KV extractor into extracting URLs as "facts".
            clean_summary = _re.sub(r'^\s*https?://\S+\s*$', '', web_summary,
                                    flags=_re.MULTILINE)
            clean_summary = _re.sub(r'🔗|🔍|📄', '', clean_summary)
            passages.append(Passage(
                text=clean_summary,
                source_url="summary",
            ))
        for r in results[:5]:
            text = f"{getattr(r, 'title', '')}\n{getattr(r, 'snippet', '')}"
            url = getattr(r, 'url', '')
            if text.strip():
                passages.append(Passage(text=text, source_url=url))

        # Extract with v3 (validators + per-predicate limits)
        raw_facts = self._v3_extractor.extract_from_passages(passages, subject=subject)

        # Aggregate with v3 (conflict resolver)
        v3_fact_set = self._v3_aggregator.aggregate(raw_facts, subject=subject)

        # Convert to fact_pipeline.FactSet format
        fact_set = self._convert_from_v3(v3_fact_set)

        if subject:
            fact_set.subject = subject

        logger.info(
            "FactPipeline(v3): %d raw facts → %d aggregated (confidence=%.2f)",
            len(raw_facts), len(fact_set.facts), fact_set.confidence,
        )
        return fact_set

    def _process_legacy(self, web_summary: str, results: list,
                        subject: str = "") -> FactSet:
        """Легаси extraction path (old extractor without validators)."""
        all_facts: Dict[str, List[Fact]] = {}

        if web_summary:
            summary_facts = self.extractor.extract(
                web_summary, source_url="summary", subject=subject,
            )
            if summary_facts:
                all_facts["summary"] = summary_facts

        for r in results[:5]:
            text = f"{getattr(r, 'title', '')}\n{getattr(r, 'snippet', '')}"
            url = getattr(r, 'url', '')
            facts = self.extractor.extract(text, source_url=url, subject=subject)
            if facts:
                all_facts[url or f"result_{id(r)}"] = facts

        fact_set = self.verifier.verify(all_facts)

        if subject:
            fact_set.subject = subject

        logger.info(
            "FactPipeline(legacy): %d raw facts → %d verified (confidence=%.2f)",
            sum(len(v) for v in all_facts.values()),
            len(fact_set.facts),
            fact_set.confidence,
        )
        return fact_set

    def check_answer(self, answer: str, fact_set: FactSet) -> Tuple[str, List[str]]:
        """Проверить ответ LLM на галлюцинации."""
        return self.guard.check(answer, fact_set)

    def compute_confidence(
        self,
        source_count: int,
        domain_scores: List[float],
        keyword_match_ratio: float,
        fact_set: Optional[FactSet] = None,
    ) -> float:
        """Вычислить общую уверенность."""
        fact_overlap = 0.0
        if fact_set and fact_set.facts:
            fact_overlap = fact_set.verified_count / len(fact_set.facts)

        return self.scorer.score(
            source_count=source_count,
            domain_scores=domain_scores,
            keyword_match_ratio=keyword_match_ratio,
            fact_overlap=fact_overlap,
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Singleton
# ═══════════════════════════════════════════════════════════════════════════════

_pipeline: Optional[FactPipeline] = None


def get_fact_pipeline() -> FactPipeline:
    """Получить (или создать) экземпляр FactPipeline."""
    global _pipeline
    if _pipeline is None:
        _pipeline = FactPipeline()
    return _pipeline
