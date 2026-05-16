"""
Lina Intent API — мост между UI и Governance.

UI не выполняет действия. UI генерирует Intent.
Governance решает и выполняет.

Phase: CONTROL PLANE / Intent Layer
"""

from lina.intent.types import Intent, IntentType, IntentResult, IntentStatus
from lina.intent.router import IntentRouter, get_intent_router
from lina.intent.bridge import IntentBridge, get_intent_bridge

__all__ = [
    "Intent", "IntentType", "IntentResult", "IntentStatus",
    "IntentRouter", "get_intent_router",
    "IntentBridge", "get_intent_bridge",
]
