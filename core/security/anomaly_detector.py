"""
Anomaly Detector.

Detects anomalous patterns in user input:
  - Entropy scoring (high entropy = possible encoded attack)
  - Repetition anomaly (repeated tokens = DoS / confusion)
  - Suspicious token patterns (shellcode, escape sequences)
  - Length anomaly (unusually long inputs)

Returns AnomalyReport for risk engine integration.
"""

import re
import math
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

logger = logging.getLogger("lina.core.security.anomaly_detector")


@dataclass
class AnomalyReport:
    """Result of anomaly detection."""
    is_anomalous: bool
    score: float            # 0.0 (normal) → 1.0 (highly anomalous)
    findings: List[str] = field(default_factory=list)
    entropy: float = 0.0
    repetition_ratio: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_anomalous": self.is_anomalous,
            "score": round(self.score, 4),
            "entropy": round(self.entropy, 4),
            "repetition_ratio": round(self.repetition_ratio, 4),
            "findings": self.findings,
        }


# ── Suspicious Patterns ──

_SUSPICIOUS_PATTERNS = [
    (r"\\x[0-9a-fA-F]{2}", "hex escape sequences"),
    (r"&#\d+;", "HTML numeric entities"),
    (r"&#x[0-9a-fA-F]+;", "HTML hex entities"),
    (r"%[0-9a-fA-F]{2}", "URL encoding"),
    (r"\\u[0-9a-fA-F]{4}", "unicode escapes"),
    (r"\x00", "null bytes"),
    (r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "control characters"),
    (r"\beval\s*\(", "eval() call"),
    (r"\bexec\s*\(", "exec() call"),
    (r"__import__\s*\(", "__import__() call"),
    (r"\bos\.system\s*\(", "os.system() call"),
    (r"\bsubprocess\.", "subprocess module"),
    (r"<script", "HTML script tag"),
    (r"javascript:", "javascript: URI"),
    (r"\bon\w+\s*=", "HTML event handler"),
]

_ZERO_WIDTH_CHARS = {
    "\u200B",  # zero-width space
    "\u200C",  # zero-width non-joiner
    "\u200D",  # zero-width joiner
    "\u2060",  # word joiner
    "\uFEFF",  # BOM
    "\u00AD",  # soft hyphen
    "\u200E",  # LTR mark
    "\u200F",  # RTL mark
    "\u202A",  # LTR embedding
    "\u202B",  # RTL embedding
    "\u202C",  # pop directional formatting
    "\u202D",  # LTR override
    "\u202E",  # RTL override
}


class AnomalyDetector:
    """
    Detects anomalous input patterns.

    Thresholds:
      entropy > 4.5   → suspicious (English text ~4.0, random ~6.0)
      repetition > 0.5 → repetitive (possible DoS)
      score > 0.6      → is_anomalous=True

    Usage:
        detector = AnomalyDetector()
        report = detector.analyze("normal question about Python")
        assert not report.is_anomalous

        report = detector.analyze("\\x00" * 100 + "aaaa" * 50)
        assert report.is_anomalous
    """

    def __init__(
        self,
        anomaly_threshold: float = 0.6,
        max_entropy: float = 4.5,
        max_repetition_ratio: float = 0.5,
        max_length: int = 10000,
    ) -> None:
        self._threshold = anomaly_threshold
        self._max_entropy = max_entropy
        self._max_repetition = max_repetition_ratio
        self._max_length = max_length

    def analyze(self, text: str) -> AnomalyReport:
        """
        Analyze text for anomalies.

        Args:
            text: Input text to analyze.

        Returns:
            AnomalyReport with detailed findings.
        """
        score = 0.0
        findings: List[str] = []

        if not text.strip():
            return AnomalyReport(is_anomalous=False, score=0.0)

        # 1. Length check
        if len(text) > self._max_length:
            score += 0.3
            findings.append(f"Excessive length: {len(text)} chars (max {self._max_length})")

        # 2. Entropy scoring
        entropy = self._calculate_entropy(text)
        if entropy > self._max_entropy:
            contribution = min(0.3, (entropy - self._max_entropy) / 3.0)
            score += contribution
            findings.append(f"High entropy: {entropy:.2f} (threshold {self._max_entropy})")

        # 3. Repetition detection
        repetition = self._calculate_repetition(text)
        if repetition > self._max_repetition:
            contribution = min(0.3, (repetition - self._max_repetition) * 0.6)
            score += contribution
            findings.append(f"High repetition: {repetition:.2f} (threshold {self._max_repetition})")

        # 4. Suspicious patterns
        pattern_hits = self._check_patterns(text)
        if pattern_hits:
            score += min(0.4, len(pattern_hits) * 0.1)
            for pat_name in pattern_hits:
                findings.append(f"Suspicious pattern: {pat_name}")

        # 5. Zero-width characters
        zw_count = sum(1 for ch in text if ch in _ZERO_WIDTH_CHARS)
        if zw_count > 0:
            score += min(0.3, zw_count * 0.05)
            findings.append(f"Zero-width characters: {zw_count} found")

        # 6. Mixed script detection
        if self._has_mixed_scripts(text):
            score += 0.15
            findings.append("Mixed Unicode scripts detected")

        score = min(1.0, score)
        is_anomalous = score >= self._threshold

        return AnomalyReport(
            is_anomalous=is_anomalous,
            score=score,
            findings=findings,
            entropy=entropy,
            repetition_ratio=repetition,
        )

    def _calculate_entropy(self, text: str) -> float:
        """Shannon entropy of character distribution."""
        if not text:
            return 0.0
        freq = Counter(text)
        total = len(text)
        entropy = 0.0
        for count in freq.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        return entropy

    def _calculate_repetition(self, text: str) -> float:
        """Ratio of repeated n-grams (bigrams)."""
        if len(text) < 4:
            return 0.0
        bigrams = [text[i:i+2] for i in range(len(text) - 1)]
        freq = Counter(bigrams)
        repeated = sum(c - 1 for c in freq.values() if c > 1)
        return repeated / len(bigrams)

    def _check_patterns(self, text: str) -> List[str]:
        """Check for suspicious patterns."""
        hits = []
        for pattern, name in _SUSPICIOUS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                hits.append(name)
        return hits

    def _has_mixed_scripts(self, text: str) -> bool:
        """Detect mixing of Latin and Cyrillic scripts (homoglyph attack indicator)."""
        has_latin = bool(re.search(r"[a-zA-Z]", text))
        has_cyrillic = bool(re.search(r"[\u0400-\u04FF]", text))
        if has_latin and has_cyrillic:
            # Check if it's a natural bilingual text or suspicious mixing
            words = text.split()
            mixed_words = 0
            for word in words:
                w_latin = bool(re.search(r"[a-zA-Z]", word))
                w_cyrillic = bool(re.search(r"[\u0400-\u04FF]", word))
                if w_latin and w_cyrillic:
                    mixed_words += 1
            return mixed_words >= 2
        return False
