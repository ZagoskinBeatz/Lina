"""
ConfirmationHandler — обработчик подтверждений пользователя.

Когда IntentRouter возвращает NEEDS_CONFIRM, создаётся EscalationRequest.
ConfirmationHandler — мост между UI и EscalationManager:
  - CLI: print prompt, wait for y/n, resolve
  - GUI: emit signal → QDialog → resolve
  - DBus: accept ConfirmEscalation(id, bool) → resolve

Поток:
  IntentResult(NEEDS_CONFIRM, escalation_id) →
    ConfirmationHandler.request_confirmation(result, ...) →
      UI prompt/dialog →
        ConfirmationHandler.resolve(escalation_id, confirmed) →
          EscalationManager.resolve() + AuditLogger.log()
            → (если confirmed) IntentRouter.process(intent) повторно

Phase: GOVERNANCE LAYER / Phase 2
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class ConfirmationHandler:
    """
    Handles NEEDS_CONFIRM responses from IntentRouter.

    Three modes:
      1. CLI/interactive: register prompt_fn, handler shows text and waits
      2. GUI: register Qt signal emitter, handler emits
      3. Programmatic: call resolve() directly (DBus, tests)

    Example (CLI):
        handler = get_confirmation_handler()
        handler.set_cli_mode()
        # Then in REPL loop:
        result = bridge.from_text(text, source="cli")
        if result.status == IntentStatus.NEEDS_CONFIRM:
            handler.handle_interactive(result)
    """

    def __init__(self) -> None:
        self._escalation_manager = None
        self._audit_logger = None
        self._intent_router = None
        self._mode = "cli"  # "cli" | "gui" | "dbus"
        self._gui_callback: Optional[Callable] = None
        self._pending_intents: Dict[str, Any] = {}  # escalation_id → Intent
        self._resolved: int = 0
        self._denied: int = 0
        self._initialized = False

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        try:
            from lina.governance.escalation import get_escalation_manager
            from lina.governance.audit_logger import get_audit_logger
            from lina.intent.router import get_intent_router
            self._escalation_manager = get_escalation_manager()
            self._audit_logger = get_audit_logger()
            self._intent_router = get_intent_router()
            self._initialized = True
        except Exception as e:
            logger.error("ConfirmationHandler: init failed: %s", e)
            self._initialized = True  # Don't retry on every call — fail once

    # ── Mode Config ──────────────────────────────────────

    def set_cli_mode(self) -> None:
        """Interactive CLI mode — prompts user via stdin."""
        self._mode = "cli"

    def set_gui_mode(self, callback: Callable) -> None:
        """
        GUI mode — calls callback(escalation_dict) instead of blocking.

        callback receives EscalationRequest.to_dict() and should:
          1. Show a confirmation dialog
          2. Call handler.resolve(esc_id, confirmed=True/False)
        """
        self._mode = "gui"
        self._gui_callback = callback

    def set_dbus_mode(self) -> None:
        """DBus mode — no interactive prompt, resolve() called externally."""
        self._mode = "dbus"

    # ── Store pending intent for re-execution after confirm ──

    def register_pending(self, escalation_id: str, intent: Any) -> None:
        """Save intent so we can re-execute after confirmation."""
        # Cap pending intents to prevent unbounded growth
        if len(self._pending_intents) >= 100:
            # Evict oldest entry
            oldest_key = next(iter(self._pending_intents))
            self._pending_intents.pop(oldest_key, None)
        self._pending_intents[escalation_id] = intent

    # ── Handle NEEDS_CONFIRM ─────────────────────────────

    def handle(self, result: Any, intent: Any = None) -> Any:
        """
        Handle a NEEDS_CONFIRM IntentResult.

        For CLI: blocks and returns final IntentResult after resolve.
        For GUI: emits callback and returns pending status.
        For DBus: returns pending status (resolve later).

        Args:
            result: IntentResult with status=NEEDS_CONFIRM
            intent: Original Intent (saved for re-execution)

        Returns:
            IntentResult — final result or pending marker.
        """
        self._ensure_init()

        esc_id = getattr(result, 'escalation_id', '') or ''
        if not esc_id:
            logger.warning("ConfirmationHandler: no escalation_id in result")
            return result

        # Save intent for re-execution
        if intent:
            self.register_pending(esc_id, intent)

        # Get escalation details
        esc_dict = self._get_escalation_dict(esc_id)

        if self._mode == "cli":
            return self._handle_cli(esc_id, esc_dict, intent)
        elif self._mode == "gui":
            return self._handle_gui(esc_id, esc_dict)
        else:
            # dbus/programmatic — caller will resolve later
            return result

    def _handle_cli(self, esc_id: str, esc_dict: Dict, intent: Any) -> Any:
        """
        CLI interactive confirmation.

        Prints confirmation prompt and waits for y/n.
        """
        from lina.intent.types import IntentResult, IntentStatus

        title = esc_dict.get("title_ru", "Подтверждение")
        desc = esc_dict.get("description_ru", "")
        action = esc_dict.get("proposed_action", "")
        risk = esc_dict.get("risk_level", "")

        # Format prompt
        lines = [
            "",
            f"⚠  {title}",
        ]
        if desc:
            lines.append(f"   {desc}")
        if action:
            lines.append(f"   Действие: {action}")
        if risk:
            lines.append(f"   Риск: {risk}")
        lines.append("")
        lines.append("   Подтвердить? [y/N] ")

        prompt_text = "\n".join(lines)

        try:
            answer = input(prompt_text).strip().lower()
            confirmed = answer in ("y", "yes", "д", "да")
        except (EOFError, KeyboardInterrupt):
            confirmed = False

        self.resolve(esc_id, confirmed)

        # If confirmed and we have the original intent → re-execute
        if confirmed and intent and self._intent_router:
            # Mark intent so governance doesn't re-confirm
            if hasattr(intent, 'params'):
                intent.params["_confirmed"] = True
            return self._intent_router.process(intent)

        if not confirmed:
            return IntentResult(
                intent_id=getattr(intent, 'id', esc_id) if intent else esc_id,
                status=IntentStatus.DENIED,
                response_text="Действие отменено пользователем.",
                policy_decision="user_rejected",
            )

        return IntentResult(
            intent_id=esc_id,
            status=IntentStatus.DENIED,
            response_text="Не удалось повторить выполнение.",
        )

    def _handle_gui(self, esc_id: str, esc_dict: Dict) -> Any:
        """GUI mode — emit callback, return pending."""
        from lina.intent.types import IntentResult, IntentStatus

        if self._gui_callback:
            try:
                self._gui_callback(esc_dict)
            except Exception as e:
                logger.error("ConfirmationHandler: gui callback failed: %s", e)

        return IntentResult(
            intent_id=esc_id,
            status=IntentStatus.NEEDS_CONFIRM,
            response_text="Ожидание подтверждения в GUI.",
            escalation_id=esc_id,
            metadata={"pending": True},
        )

    # ── Resolve ──────────────────────────────────────────

    def resolve(self, escalation_id: str, confirmed: bool,
                alternative_index: int = -1) -> bool:
        """
        Resolve an escalation — can be called from any mode.

        Args:
            escalation_id: ID of the escalation.
            confirmed: True = allow, False = deny.
            alternative_index: Alternative chosen (for CHOOSE level, -1=none).

        Returns:
            True if resolved successfully.
        """
        self._ensure_init()

        if confirmed:
            self._resolved += 1
        else:
            self._denied += 1

        # Resolve in EscalationManager
        if self._escalation_manager:
            self._escalation_manager.resolve(
                escalation_id,
                confirmed=confirmed,
                alternative_index=alternative_index,
            )
        else:
            logger.error("ConfirmationHandler.resolve: escalation_manager not initialized")

        # Audit
        if self._audit_logger:
            from lina.governance.audit_logger import AuditEvent
            self._audit_logger.log_decision(
                intent_id=escalation_id,
                decision="confirmed" if confirmed else "rejected",
                event_type=AuditEvent.CONFIRM_RESOLVED,
                metadata={
                    "alternative_index": alternative_index,
                },
            )

        # Cleanup pending
        self._pending_intents.pop(escalation_id, None)

        return True

    def resolve_and_execute(self, escalation_id: str, confirmed: bool) -> Any:
        """
        Resolve and re-execute the original intent (for GUI/DBus).

        Returns:
            IntentResult from re-execution, or denied result.
        """
        self._ensure_init()
        from lina.intent.types import IntentResult, IntentStatus

        intent = self._pending_intents.get(escalation_id)
        self.resolve(escalation_id, confirmed)

        if confirmed and intent and self._intent_router:
            if hasattr(intent, 'params'):
                intent.params["_confirmed"] = True
            return self._intent_router.process(intent)

        return IntentResult(
            intent_id=getattr(intent, 'id', escalation_id) if intent else escalation_id,
            status=IntentStatus.DENIED,
            response_text="Действие отменено." if not confirmed else "Не удалось повторить.",
            policy_decision="user_rejected" if not confirmed else "no_intent",
        )

    # ── Helpers ──────────────────────────────────────────

    def _get_escalation_dict(self, esc_id: str) -> Dict:
        """Get escalation details from manager."""
        if self._escalation_manager:
            pending = self._escalation_manager.get_pending()
            for esc in pending:
                if esc.id == esc_id:
                    return esc.to_dict()
        return {"id": esc_id}

    def get_stats(self) -> Dict[str, Any]:
        return {
            "mode": self._mode,
            "resolved": self._resolved,
            "denied": self._denied,
            "pending": len(self._pending_intents),
        }


# ─── Singleton ────────────────────────────────────────────────────────────────

_handler: Optional[ConfirmationHandler] = None


def get_confirmation_handler() -> ConfirmationHandler:
    """Получить единственный ConfirmationHandler."""
    global _handler
    if _handler is None:
        _handler = ConfirmationHandler()
    return _handler
