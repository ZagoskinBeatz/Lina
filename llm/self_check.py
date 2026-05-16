# -*- coding: utf-8 -*-
"""
Lina LLM — Self-Check (v3).

v3 location: re-exports from llm/self_verifier.py.
Provides v3 pipeline-ready self-check interface.
"""

from lina.llm.self_verifier import (
    SelfVerifier,
    VerificationResult,
    get_self_verifier,
)

__all__ = ["SelfVerifier", "VerificationResult", "get_self_verifier"]
