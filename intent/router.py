"""
IntentRouter — маршрутизатор Intent через governance pipeline.

Поток:
    Intent → AccessLevelResolver → PolicyEngine → ActionRegistry → Execution

IntentRouter — единственный путь от UI к выполнению.
UI вызывает router.process(intent). Всё остальное — внутри.

Phase: CONTROL PLANE / Intent Layer
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from lina.intent.types import Intent, IntentResult, IntentStatus, IntentType

logger = logging.getLogger(__name__)


class IntentRouter:
    """
    Маршрутизатор Intent через governance control plane.

    Единственная точка входа для обработки пользовательских намерений.
    UI вызывает process(intent) — router делает всё остальное.

    Пример:
        router = get_intent_router()
        intent = Intent(type=IntentType.DIAGNOSE, domain="network")
        result = router.process(intent)
        if result.status == IntentStatus.NEEDS_CONFIRM:
            # показать диалог подтверждения
            ...
    """

    def __init__(self) -> None:
        self._processed: int = 0
        self._denied: int = 0
        self._failed: int = 0
        self._access_resolver = None
        self._policy_engine = None
        self._action_registry = None
        self._state_machine = None
        self._escalation_manager = None
        self._signature_collector = None
        self._strategy_selector = None
        self._kb_local = None
        self._kb_user = None
        self._fuzzy_matcher = None
        self._telemetry = None
        self._audit = None
        self._initialized = False

    # ── Lazy Init ────────────────────────────────────────

    def _ensure_init(self) -> None:
        """Отложенная инициализация governance-компонетов."""
        if self._initialized:
            return

        try:
            from lina.governance.policy_engine import get_policy_engine
            from lina.governance.action_registry import get_action_registry
            from lina.governance.state_machine import get_runtime_machine
            from lina.governance.escalation import get_escalation_manager
            from lina.governance.signature_collector import get_signature_collector
            from lina.governance.strategy_selector import get_strategy_selector
            from lina.governance.fuzzy_matcher import get_fuzzy_matcher
            from lina.governance.telemetry import get_telemetry_engine
            from lina.governance.audit_logger import get_audit_logger
            from lina.governance.kb.local_kb import get_local_kb
            from lina.governance.kb.user_kb import get_user_kb
            from lina.access.resolver import get_access_resolver

            self._policy_engine = get_policy_engine()
            self._action_registry = get_action_registry()
            self._state_machine = get_runtime_machine()
            self._escalation_manager = get_escalation_manager()
            self._signature_collector = get_signature_collector()
            self._strategy_selector = get_strategy_selector()
            self._fuzzy_matcher = get_fuzzy_matcher()
            self._telemetry = get_telemetry_engine()
            self._audit = get_audit_logger()
            self._kb_local = get_local_kb()
            self._kb_user = get_user_kb()
            self._access_resolver = get_access_resolver()
            self._initialized = True
            logger.info("IntentRouter: governance components initialized")
        except Exception as e:
            logger.error("IntentRouter: init failed: %s", e)

    # ── Main Processing ──────────────────────────────────

    def process(self, intent: Intent) -> IntentResult:
        """
        Обработать intent через governance pipeline.

        Поток:
          1. Access level check
          2. Policy check
          3. KB search (если diagnose)
          4. Strategy selection
          5. Action execution (если разрешено)
          6. Telemetry
          7. Результат + metadata enrichment (Phase 4)

        Returns:
            IntentResult с результатом обработки.
        """
        # INVARIANT: IntentRouter is the ONLY path from UI to execution.
        # All intents MUST pass through Access → Policy → Audit chain.
        # Bypass of this method = security violation.
        self._ensure_init()
        start = time.monotonic()
        self._processed += 1
        _outcome_success = True  # Phase 3: track actual outcome for telemetry
        _result: Optional[IntentResult] = None

        # INVARIANT: Every intent is audit-logged, even if denied.
        if self._audit:
            self._audit.log_intent(intent)

        try:
            # 1. Chat/Query — не требует governance
            if not intent.requires_action():
                if self._audit:
                    self._audit.log_decision(
                        intent.id, "chat_passthrough",
                        domain=intent.domain,
                        action=intent.action or "",
                        source=intent.source,
                        event_type="chat_passthrough",
                    )
                _result = IntentResult(
                    intent_id=intent.id,
                    status=IntentStatus.CHAT_RESPONSE,
                    response_text="",  # LLM заполнит
                    metadata={"type": "chat_passthrough"},
                )
                return _result

            # 2. Access level check
            if self._access_resolver:
                access_result = self._access_resolver.check(intent)
                if not access_result.allowed:
                    self._denied += 1
                    if self._audit:
                        self._audit.log_decision(
                            intent.id, "access_denied",
                            access_level=str(access_result.access_level),
                            domain=intent.domain,
                            action=intent.action,
                            source=intent.source,
                            event_type="access_checked",
                        )
                    _result = IntentResult(
                        intent_id=intent.id,
                        status=IntentStatus.DENIED,
                        response_text=access_result.reason_ru,
                        policy_decision="access_denied",
                    )
                    return _result
                if access_result.needs_confirmation:
                    # Создать эскалацию
                    if self._escalation_manager:
                        esc = self._escalation_manager.create_escalation(
                            title_ru=f"Подтверждение: {intent.action}",
                            description_ru=access_result.reason_ru,
                            domain=intent.domain,
                            risk_level=access_result.access_level,
                            proposed_action=intent.action,
                        )
                        if self._audit:
                            self._audit.log_decision(
                                intent.id, "needs_confirm",
                                access_level=str(access_result.access_level),
                                domain=intent.domain,
                                action=intent.action,
                                source=intent.source,
                                event_type="confirm_requested",
                                metadata={"escalation_id": esc.id},
                            )
                        _result = IntentResult(
                            intent_id=intent.id,
                            status=IntentStatus.NEEDS_CONFIRM,
                            response_text=access_result.reason_ru,
                            escalation_id=esc.id,
                            policy_decision="needs_confirm",
                        )
                        return _result

            # 3. Policy check
            if self._policy_engine and intent.action:
                from lina.governance.policy_engine import PolicyDecision
                risk = "high" if intent.is_admin() else (
                    "medium" if intent.is_power() else "low")
                policy_result = self._policy_engine.check(
                    intent.action, domain=intent.domain, risk_level=risk)
                if policy_result.decision == PolicyDecision.DENY:
                    self._denied += 1
                    if self._audit:
                        self._audit.log_decision(
                            intent.id, "deny",
                            domain=intent.domain,
                            action=intent.action,
                            source=intent.source,
                            event_type="policy_checked",
                            metadata={"reason": policy_result.reason},
                        )
                    _result = IntentResult(
                        intent_id=intent.id,
                        status=IntentStatus.DENIED,
                        response_text=f"Действие запрещено политикой: {policy_result.reason}",
                        policy_decision="deny",
                    )
                    return _result
                if policy_result.decision == PolicyDecision.CONFIRM:
                    if self._escalation_manager:
                        esc = self._escalation_manager.create_escalation(
                            title_ru=f"Подтверждение: {intent.action}",
                            domain=intent.domain,
                            risk_level=risk,
                            proposed_action=intent.action,
                        )
                        if self._audit:
                            self._audit.log_decision(
                                intent.id, "confirm",
                                domain=intent.domain,
                                action=intent.action,
                                source=intent.source,
                                event_type="confirm_requested",
                                metadata={"escalation_id": esc.id, "reason": policy_result.reason},
                            )
                        _result = IntentResult(
                            intent_id=intent.id,
                            status=IntentStatus.NEEDS_CONFIRM,
                            response_text=policy_result.reason,
                            escalation_id=esc.id,
                            policy_decision="confirm",
                        )
                        return _result

            # 4. Diagnose flow — KB + strategy
            if intent.type == IntentType.DIAGNOSE:
                _result = self._process_diagnose(intent)
                return _result

            # 5. Direct action execution
            if intent.action and self._action_registry:
                _result = self._process_action(intent)
                return _result

            # 6. Fallback — unknown intent with action requirement
            if self._audit:
                self._audit.log_decision(
                    intent.id, "not_found",
                    domain=intent.domain,
                    action=intent.action or "",
                    source=intent.source,
                    event_type="action_not_found",
                )
            _result = IntentResult(
                intent_id=intent.id,
                status=IntentStatus.NOT_FOUND,
                response_text="Не удалось определить действие для выполнения.",
            )
            return _result

        except Exception as e:
            self._failed += 1
            _outcome_success = False
            logger.error("IntentRouter: process failed: %s", e)
            if self._audit:
                self._audit.log_execution(
                    intent.id, success=False,
                    action=intent.action, domain=intent.domain,
                    metadata={"error": type(e).__name__},
                )
            _result = IntentResult(
                intent_id=intent.id,
                status=IntentStatus.FAILED,
                response_text="Внутренняя ошибка обработки.",
            )
            return _result
        finally:
            elapsed = (time.monotonic() - start) * 1000
            # Phase 4: Enrich result metadata with domain/action for UX layer
            if _result is not None:
                if _result.metadata is None:
                    _result.metadata = {}
                _result.metadata.setdefault("domain", intent.domain)
                _result.metadata.setdefault("action", intent.action or "")
                _result.duration_ms = elapsed
            if self._telemetry:
                self._telemetry.record_action(
                    intent.action or intent.type.value,
                    domain=intent.domain,
                    success=_outcome_success,
                    duration=elapsed / 1000,
                )

    # ── Diagnose Flow ────────────────────────────────────

    def _process_diagnose(self, intent: Intent) -> IntentResult:
        """Диагностика через KB + SignatureCollector + StrategySelector."""
        # Поиск в KB
        results = []
        if self._kb_local:
            results.extend(self._kb_local.search(
                domain=intent.domain,
                tags=intent.params.get("tags", []),
            ))
        if self._kb_user:
            results.extend(self._kb_user.search(
                domain=intent.domain,
                tags=intent.params.get("tags", []),
            ))

        if not results:
            if self._audit:
                self._audit.log_execution(
                    intent.id, success=False,
                    action="diagnose", domain=intent.domain,
                    metadata={"reason": "no_kb_results"},
                )
            return IntentResult(
                intent_id=intent.id,
                status=IntentStatus.NOT_FOUND,
                response_text=f"Нет известных решений для домена '{intent.domain}'.",
                metadata={"kb_searched": True, "matches": 0},
            )

        # Лучший результат
        best = results[0]
        actions = getattr(best.entry, 'actions', []) if best.entry else []

        if self._audit:
            self._audit.log_execution(
                intent.id, success=True,
                action="diagnose", domain=intent.domain,
                metadata={"kb_matches": len(results),
                           "best_score": best.score},
            )

        return IntentResult(
            intent_id=intent.id,
            status=IntentStatus.SUCCESS,
            response_text=getattr(best.entry, 'diagnosis_ru', '') or '',
            action_result={
                "kb_entry_id": best.entry.id if best.entry else "",
                "score": best.score,
                "proposed_actions": actions,
                "domain": intent.domain,
            },
            metadata={"kb_matches": len(results)},
        )

    # ── Action Execution ─────────────────────────────────

    def _process_action(self, intent: Intent) -> IntentResult:
        """Выполнить действие через ActionRegistry."""
        from lina.governance.action_registry import ExecStatus

        result = self._action_registry.execute(
            intent.action,
            params=intent.params,
            dry_run=False,
        )

        if result.status == ExecStatus.SUCCESS:
            # Обучить UserKB
            if self._kb_user:
                self._kb_user.learn_from_action(
                    domain=intent.domain,
                    tags=intent.params.get("tags", []),
                    actions=[intent.action],
                    success=True,
                )
            if self._audit:
                self._audit.log_execution(
                    intent.id, success=True,
                    action=intent.action, domain=intent.domain,
                )
            return IntentResult(
                intent_id=intent.id,
                status=IntentStatus.SUCCESS,
                response_text=f"Действие выполнено: {intent.action}",
                action_result=result.to_dict() if hasattr(result, 'to_dict') else {},
            )
        elif result.status == ExecStatus.NEEDS_CONFIRM:
            if self._audit:
                self._audit.log_decision(
                    intent.id, "confirm",
                    domain=intent.domain,
                    action=intent.action,
                    source=intent.source,
                    event_type="action_needs_confirm",
                )
            return IntentResult(
                intent_id=intent.id,
                status=IntentStatus.NEEDS_CONFIRM,
                response_text="Требуется подтверждение.",
                policy_decision="confirm",
            )
        else:
            self._failed += 1
            if self._audit:
                self._audit.log_execution(
                    intent.id, success=False,
                    action=intent.action, domain=intent.domain,
                    metadata={"message": result.message if hasattr(result, 'message') else ""},
                )
            return IntentResult(
                intent_id=intent.id,
                status=IntentStatus.FAILED,
                response_text="Ошибка выполнения действия.",
                action_result=result.to_dict() if hasattr(result, 'to_dict') else {},
            )

    # ── Query ────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Статистика."""
        return {
            "processed": self._processed,
            "denied": self._denied,
            "failed": self._failed,
            "initialized": self._initialized,
        }


# ─── Singleton ────────────────────────────────────────────────────────────────

_router: Optional[IntentRouter] = None


def get_intent_router() -> IntentRouter:
    """Получить единственный IntentRouter."""
    global _router
    if _router is None:
        _router = IntentRouter()
    return _router
