# -*- coding: utf-8 -*-
"""
Security — Input Validation & Sanitization (Phase 5).

Zero-trust input layer: every string entering the governance pipeline
is validated, bounded, and sanitized BEFORE reaching IntentRouter.

Checks:
  - Max length (prevents DoS via memory exhaustion)
  - Null byte injection
  - Control character stripping
  - Unicode normalization (homoglyph defense)
  - Domain/source allowlisting
  - Params depth/size limits
  - Obfuscated command detection (base64, hex)

This module NEVER executes. Only validates.

Phase: SECURITY / Phase 5
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("lina.security.input_validator")


# ═══════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════

# Absolute maximum user input length (characters)
MAX_INPUT_LENGTH = 4096

# Maximum domain name length
MAX_DOMAIN_LENGTH = 64

# Maximum action ID length
MAX_ACTION_LENGTH = 128

# Maximum source name length
MAX_SOURCE_LENGTH = 32

# Maximum params dict depth
MAX_PARAMS_DEPTH = 4

# Maximum total params size (serialized estimate)
MAX_PARAMS_KEYS = 32

# Maximum single param value length
MAX_PARAM_VALUE_LENGTH = 1024

# Known valid sources (zero-trust: only these are allowed)
VALID_SOURCES = frozenset({
    "ui", "cli", "dbus", "hotkey", "internal", "test", "gui", "repl",
})

# Known valid domains (from governance/policy_engine.py)
VALID_DOMAINS = frozenset({
    "service", "package", "network", "disk", "config", "user",
    "boot", "display", "audio", "security", "installer", "desktop",
    "system", "safety", "general", "",
})

# Null bytes and control characters (except \n, \t)
_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

# Obfuscated command patterns
_OBFUSCATION_PATTERNS = [
    # base64-encoded shell commands
    re.compile(r'(?:echo|printf)\s+["\']?[A-Za-z0-9+/]{8,}={0,2}["\']?\s*\|\s*(?:base64\s+-d|b64decode)', re.I),
    # hex-encoded payloads
    re.compile(r'\\x[0-9a-fA-F]{2}(?:\\x[0-9a-fA-F]{2}){4,}'),
    # Python/perl one-liners for shell escape
    re.compile(r'(?:python|perl|ruby)\s+-[ce]\s+', re.I),
    # eval/exec with encoded strings
    re.compile(r'(?:eval|exec)\s*\(\s*(?:base64|bytes|decode)', re.I),
    # $(cmd) or `cmd` in user text
    re.compile(r'\$\([^)]+\)'),
    re.compile(r'`[^`]+`'),
]

# Injection patterns (shell metacharacters in user text)
_INJECTION_PATTERNS = re.compile(r'[;&|]|\.\./|>\s*/|<<|>>|\brm\s+-rf\s+/')


# ═══════════════════════════════════════════════════════════
#  Validation Result
# ═══════════════════════════════════════════════════════════

class ValidationResult:
    """Result of input validation."""
    __slots__ = ("valid", "reason", "sanitized_text")

    def __init__(self, valid: bool, reason: str = "",
                 sanitized_text: str = "") -> None:
        self.valid = valid
        self.reason = reason
        self.sanitized_text = sanitized_text

    def __bool__(self) -> bool:
        return self.valid

    def __repr__(self) -> str:
        return f"ValidationResult(valid={self.valid}, reason={self.reason!r})"


# ═══════════════════════════════════════════════════════════
#  InputValidator
# ═══════════════════════════════════════════════════════════

class InputValidator:
    """
    Zero-trust input validator for governance pipeline.

    Validates and sanitizes ALL inputs before they reach IntentRouter.
    Does NOT execute anything.

    Usage:
        v = get_input_validator()
        result = v.validate_text("user input")
        if not result:
            deny(result.reason)
        safe_text = result.sanitized_text

        ok, reason = v.validate_domain("network")
        ok, reason = v.validate_source("dbus")
        ok, reason = v.validate_params({"key": "value"})
    """

    def __init__(self, *, max_input_length: int = MAX_INPUT_LENGTH) -> None:
        self._max_input = max_input_length

    # ── Text Validation ──────────────────────────────────

    def validate_text(self, text: str) -> ValidationResult:
        """
        Validate and sanitize user input text.

        INVARIANT: This method MUST be called on ALL external text input
        BEFORE it enters the governance pipeline. It is the FIRST line
        of defense. Returns sanitized text via ValidationResult.sanitized_text.

        Checks:
          1. Type check (must be str)
          2. Length limit
          3. Null byte detection
          4. Control character stripping
          5. Unicode normalization (NFC — canonical decomposition + composition)
          6. Obfuscation detection

        Returns:
            ValidationResult with sanitized_text if valid.
        """
        # Type check
        if not isinstance(text, str):
            return ValidationResult(False, "input_not_string")

        # Length check
        if len(text) > self._max_input:
            return ValidationResult(
                False,
                f"input_too_long:{len(text)}>{self._max_input}",
            )

        # Null byte
        if '\x00' in text:
            return ValidationResult(False, "null_byte_injection")

        # Strip control characters (keep \n, \t, spaces)
        clean = _CONTROL_CHARS.sub('', text)

        # Unicode NFC normalization (prevents homoglyph attacks)
        clean = unicodedata.normalize('NFC', clean)

        # Obfuscation detection
        for pattern in _OBFUSCATION_PATTERNS:
            if pattern.search(clean):
                logger.warning("InputValidator: obfuscation detected: %s",
                               pattern.pattern[:50])
                return ValidationResult(False, "obfuscated_command")

        # Shell injection detection (metacharacters, path traversal, etc.)
        if _INJECTION_PATTERNS.search(clean):
            logger.warning("InputValidator: injection pattern detected")
            return ValidationResult(False, "shell_injection")

        return ValidationResult(True, "", clean)

    # ── Domain Validation ────────────────────────────────

    def validate_domain(self, domain: str) -> Tuple[bool, str]:
        """
        Validate domain against allowlist.

        Returns:
            (valid, reason)
        """
        if not isinstance(domain, str):
            return False, "domain_not_string"
        if len(domain) > MAX_DOMAIN_LENGTH:
            return False, "domain_too_long"

        # Normalize
        domain_lower = domain.strip().lower()

        if domain_lower not in VALID_DOMAINS:
            logger.warning("InputValidator: unknown domain: %r", domain)
            return False, f"unknown_domain:{domain_lower}"

        return True, ""

    # ── Source Validation ────────────────────────────────

    def validate_source(self, source: str) -> Tuple[bool, str]:
        """
        Validate source against allowlist.

        Returns:
            (valid, reason)
        """
        if not isinstance(source, str):
            return False, "source_not_string"
        if len(source) > MAX_SOURCE_LENGTH:
            return False, "source_too_long"

        source_lower = source.strip().lower()

        if source_lower not in VALID_SOURCES:
            logger.warning("InputValidator: unknown source: %r", source)
            return False, f"unknown_source:{source_lower}"

        return True, ""

    # ── Action ID Validation ─────────────────────────────

    def validate_action(self, action: str) -> Tuple[bool, str]:
        """
        Validate action ID.

        Returns:
            (valid, reason)
        """
        if not isinstance(action, str):
            return False, "action_not_string"
        if len(action) > MAX_ACTION_LENGTH:
            return False, "action_too_long"

        # Action IDs should be alphanumeric + underscore
        if action and not re.match(r'^[a-zA-Z0-9_\-]+$', action):
            return False, "action_invalid_chars"

        return True, ""

    # ── Params Validation ────────────────────────────────

    def validate_params(self, params: Any, *,
                        _depth: int = 0) -> Tuple[bool, str]:
        """
        Validate params dict (depth, size, value lengths).

        Returns:
            (valid, reason)
        """
        if params is None:
            return True, ""

        if not isinstance(params, dict):
            return False, "params_not_dict"

        if _depth > MAX_PARAMS_DEPTH:
            return False, "params_too_deep"

        if len(params) > MAX_PARAMS_KEYS:
            return False, "params_too_many_keys"

        for key, value in params.items():
            if not isinstance(key, str):
                return False, "params_key_not_string"
            if len(key) > MAX_ACTION_LENGTH:
                return False, "params_key_too_long"

            if isinstance(value, str):
                if len(value) > MAX_PARAM_VALUE_LENGTH:
                    return False, f"params_value_too_long:{key}"
                if '\x00' in value:
                    return False, f"params_null_byte:{key}"
            elif isinstance(value, dict):
                ok, reason = self.validate_params(value, _depth=_depth + 1)
                if not ok:
                    return False, reason
            elif isinstance(value, list):
                if len(value) > MAX_PARAMS_KEYS:
                    return False, f"params_list_too_long:{key}"
                for item in value:
                    if isinstance(item, str) and len(item) > MAX_PARAM_VALUE_LENGTH:
                        return False, f"params_list_item_too_long:{key}"

        return True, ""

    # ── JSON Payload Validation ──────────────────────────

    def validate_json_payload(self, payload: str, *,
                              max_size: int = 8192) -> Tuple[bool, str]:
        """
        Validate raw JSON string before parsing.

        Returns:
            (valid, reason)
        """
        if not isinstance(payload, str):
            return False, "payload_not_string"
        if len(payload) > max_size:
            return False, f"payload_too_large:{len(payload)}>{max_size}"
        if '\x00' in payload:
            return False, "payload_null_byte"

        return True, ""

    # ── Injection Detection ──────────────────────────────

    def detect_injection(self, text: str) -> Tuple[bool, str]:
        """
        Detect shell injection patterns in text.

        Returns:
            (has_injection, pattern_description)
        """
        if _INJECTION_PATTERNS.search(text):
            return True, "shell_metacharacters"
        return False, ""

    # ── Confidence Validation ────────────────────────────

    @staticmethod
    def validate_confidence(confidence: float) -> float:
        """Clamp confidence to [0.0, 1.0]."""
        if not isinstance(confidence, (int, float)):
            return 0.0
        return max(0.0, min(1.0, float(confidence)))


# ─── Singleton ────────────────────────────────────────────────────────────────

_validator: Optional[InputValidator] = None


def get_input_validator() -> InputValidator:
    """Get or create InputValidator singleton."""
    global _validator
    if _validator is None:
        _validator = InputValidator()
    return _validator
