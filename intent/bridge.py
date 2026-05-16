"""
Intent Bridge — мост между entry points и governance IntentRouter.

Все точки входа (GUI, DBus, Hotkey, CLI) используют IntentBridge
для преобразования сырого ввода → Intent → IntentResult.

Поток:
  Entry Point → IntentBridge.(from_text|from_action|from_domain) → Intent
  Intent → IntentRouter.process(intent) → IntentResult
  IntentResult → Entry Point (для отображения)

Внутри:
  - Используем core.intent_router (keyword classifier) для определения IntentType
  - Мапим IntentType → governance Intent
  - Передаём в governance IntentRouter

Phase: INTEGRATION LAYER / Phase 1
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from lina.intent.types import Intent, IntentType, IntentResult, IntentStatus

logger = logging.getLogger(__name__)

# ── Mapping: core.intent_router.Intent → governance IntentType ────────────────

_CORE_INTENT_TO_GOV: Dict[str, IntentType] = {
    "chat": IntentType.CHAT,
    "math": IntentType.CHAT,
    "system_command": IntentType.SYSTEM_ACTION,
    "file_operation": IntentType.SYSTEM_ACTION,
    "open_application": IntentType.OPEN_APP,
    "web_search": IntentType.SEARCH,
    "web": IntentType.SEARCH,
    "weather_query": IntentType.QUERY,
    "install_application": IntentType.PACKAGE_OP,
    "rag": IntentType.QUERY,
    "cv": IntentType.QUERY,
    "tool_explicit": IntentType.SYSTEM_ACTION,
    "meta": IntentType.SET_MODE,
    "chain": IntentType.SYSTEM_ACTION,
    "macro": IntentType.SYSTEM_ACTION,
    "system_diagnostic": IntentType.DIAGNOSE,
}

# ── Domain hints по IntentType ────────────────────────────────────────────────

_TYPE_TO_DOMAIN: Dict[IntentType, str] = {
    IntentType.OPEN_APP: "desktop",
    IntentType.PACKAGE_OP: "package",
    IntentType.DISK_OP: "disk",
    IntentType.INSTALLER: "installer",
    IntentType.LOW_LEVEL: "security",
    IntentType.DIAGNOSE: "system",
    IntentType.SEARCH: "desktop",
    IntentType.CONFIGURE: "config",
}


class IntentBridge:
    """
    Мост: сырой ввод → governance Intent → IntentResult.

    UI НЕ выполняет. UI генерирует Intent через bridge.
    Bridge передаёт в governance IntentRouter.
    IntentResult возвращается UI для отображения.

    Пример:
        bridge = get_intent_bridge()
        result = bridge.from_text("Перезапусти NetworkManager", source="ui")
        if result.status == IntentStatus.NEEDS_CONFIRM:
            show_confirmation_dialog(result)
        elif result.status == IntentStatus.SUCCESS:
            display_response(result.response_text)
    """

    def __init__(self) -> None:
        self._classifier = None   # core.intent_router.IntentRouter (keyword)
        self._gov_router = None   # intent.router.IntentRouter (governance)
        self._pipeline = None     # core.main_pipeline.MainPipeline (LLM fallback)
        self._initialized = False
        self._stats = {"total": 0, "chat": 0, "action": 0, "denied": 0}

    # ── Lazy Init ────────────────────────────────────────

    def _ensure_init(self) -> None:
        """Lazy init: classifier + governance router."""
        if self._initialized:
            return
        try:
            from lina.core.intent_router import IntentRouter as CoreRouter
            self._classifier = CoreRouter()
        except Exception as e:
            logger.warning("IntentBridge: core router not available: %s", e)

        try:
            from lina.intent.router import get_intent_router
            self._gov_router = get_intent_router()
        except Exception as e:
            logger.warning("IntentBridge: governance router not available: %s", e)

        self._initialized = True
        logger.info("IntentBridge: initialized (classifier=%s, gov=%s)",
                     self._classifier is not None, self._gov_router is not None)

    # ── Public API ───────────────────────────────────────

    def from_text(self, text: str, *,
                  source: str = "ui",
                  pipeline_handler: Any = None) -> IntentResult:
        """
        Создать Intent из произвольного текста (основной метод для GUI/CLI).

        1. Classify text → IntentType (через core keyword router)
        2. Если action-тип → governance pipeline
        3. Если chat-тип → pipeline handler (LLM)

        Args:
            text: Пользовательский ввод.
            source: Источник ("ui", "cli", "dbus", "hotkey").
            pipeline_handler: Callable(str) → str для LLM fallback.

        Returns:
            IntentResult с результатом обработки.
        """
        self._ensure_init()
        self._stats["total"] += 1
        text = text.strip()

        if not text:
            return IntentResult(
                intent_id="empty",
                status=IntentStatus.FAILED,
                response_text="Пустой ввод.",
            )

        # INVARIANT: ALL external text MUST pass InputValidator BEFORE governance.
        # This is the FIRST enforcement point. No text reaches IntentRouter unvalidated.
        # Phase 5: Input validation (zero-trust)
        from lina.security.input_validator import get_input_validator
        _iv = get_input_validator()
        vr = _iv.validate_text(text)
        if not vr:
            logger.warning("IntentBridge: input rejected: %s", vr.reason)
            return IntentResult(
                intent_id="invalid",
                status=IntentStatus.DENIED,
                response_text="Ввод отклонён: недопустимые данные.",
                policy_decision=f"input_validation:{vr.reason}",
            )
        text = vr.sanitized_text

        src_ok, src_reason = _iv.validate_source(source)
        if not src_ok:
            return IntentResult(
                intent_id="invalid",
                status=IntentStatus.DENIED,
                response_text="Недопустимый источник.",
                policy_decision=f"source_validation:{src_reason}",
            )

        # 1. Classify
        intent_type, domain, action, confidence = self._classify(text)

        # 2. Build Intent
        intent = Intent(
            type=intent_type,
            domain=domain,
            action=action,
            source=source,
            user_text=text,
            confidence=confidence,
        )

        # 3. Chat/Query → LLM pipeline (не требуют governance)
        if not intent.requires_action():
            self._stats["chat"] += 1
            return self._handle_chat(intent, pipeline_handler)

        # 4. Action → governance pipeline
        self._stats["action"] += 1
        return self._handle_action(intent, pipeline_handler)

    def from_action(self, action_id: str, *,
                    domain: str = "",
                    params: Optional[Dict[str, Any]] = None,
                    source: str = "ui") -> IntentResult:
        """
        Создать Intent из конкретного действия (для Tray, DBus, программных вызовов).

        Args:
            action_id: ID действия (svc_restart, pkg_install, ...)
            domain: Домен действия (service, package, ...)
            params: Параметры действия.
            source: Источник.

        Returns:
            IntentResult.
        """
        self._ensure_init()
        self._stats["total"] += 1
        self._stats["action"] += 1

        # Phase 5: Input validation (zero-trust)
        from lina.security.input_validator import get_input_validator
        _iv = get_input_validator()
        act_ok, act_reason = _iv.validate_action(action_id)
        if not act_ok:
            return IntentResult(
                intent_id="invalid",
                status=IntentStatus.DENIED,
                response_text="Недопустимый action ID.",
                policy_decision=f"action_validation:{act_reason}",
            )
        dom_ok, dom_reason = _iv.validate_domain(domain)
        if not dom_ok:
            return IntentResult(
                intent_id="invalid",
                status=IntentStatus.DENIED,
                response_text="Недопустимый домен.",
                policy_decision=f"domain_validation:{dom_reason}",
            )
        par_ok, par_reason = _iv.validate_params(params)
        if not par_ok:
            return IntentResult(
                intent_id="invalid",
                status=IntentStatus.DENIED,
                response_text="Недопустимые параметры.",
                policy_decision=f"params_validation:{par_reason}",
            )

        intent = Intent(
            type=IntentType.SYSTEM_ACTION,
            domain=domain,
            action=action_id,
            params=params or {},
            source=source,
        )

        if self._gov_router:
            result = self._gov_router.process(intent)
            if result.status == IntentStatus.DENIED:
                self._stats["denied"] += 1
            return result

        return IntentResult(
            intent_id=intent.id,
            status=IntentStatus.FAILED,
            response_text="Governance router недоступен.",
        )

    def from_diagnose(self, domain: str, *,
                      user_text: str = "",
                      source: str = "ui",
                      tags: Optional[list] = None) -> IntentResult:
        """
        Создать diagnostic Intent.

        Args:
            domain: Домен диагностки (network, audio, system, ...)
            user_text: Описание проблемы.
            source: Источник.

        Returns:
            IntentResult.
        """
        self._ensure_init()
        self._stats["total"] += 1
        self._stats["action"] += 1

        # Phase 5: Input validation (zero-trust)
        from lina.security.input_validator import get_input_validator
        _iv = get_input_validator()
        dom_ok, dom_reason = _iv.validate_domain(domain)
        if not dom_ok:
            return IntentResult(
                intent_id="invalid",
                status=IntentStatus.DENIED,
                response_text="Недопустимый домен для диагностики.",
                policy_decision=f"domain_validation:{dom_reason}",
            )
        if user_text:
            vr = _iv.validate_text(user_text)
            if not vr:
                return IntentResult(
                    intent_id="invalid",
                    status=IntentStatus.DENIED,
                    response_text="Ввод отклонён: недопустимые данные.",
                    policy_decision=f"input_validation:{vr.reason}",
                )
            user_text = vr.sanitized_text

        intent = Intent(
            type=IntentType.DIAGNOSE,
            domain=domain,
            action="diagnose",
            source=source,
            user_text=user_text,
            params={"tags": tags or []},
        )

        if self._gov_router:
            result = self._gov_router.process(intent)
            # Если governance KB не нашёл → попробовать diagnostics/integration
            if result.status == IntentStatus.NOT_FOUND:
                return self._fallback_diagnostics(intent, user_text)
            return result

        # Fallback: direct diagnostics
        return self._fallback_diagnostics(intent, user_text)

    # ── Internal ─────────────────────────────────────────

    def _classify(self, text: str) -> tuple:
        """
        Classify text → (IntentType, domain, action, confidence).
        Uses core keyword router first, then maps to governance types.
        Для DIAGNOSE: дополнительно определяем domain через domain_resolver.
        """
        self._ensure_init()
        intent_type = IntentType.CHAT
        domain = ""
        action = ""
        confidence = 0.5

        if self._classifier:
            try:
                decision = self._classifier.route(text)
                core_intent = decision.intent.value  # str: "chat", "system_command", etc.
                decision_reason = getattr(decision, "reason", "") or ""
                confidence = decision.confidence

                intent_type = _CORE_INTENT_TO_GOV.get(core_intent, IntentType.CHAT)

                # Safe read-only local queries should bypass governance action flow
                # and go through the normal pipeline/Commander path instead.
                if core_intent == "system_command" and decision_reason in {
                    "system info pattern",
                    "datetime pattern",
                }:
                    intent_type = IntentType.QUERY

                # Extract domain hints from metadata
                domain = decision.metadata.get("domain", "")
                action = decision.metadata.get("action", "")
                if not domain:
                    domain = _TYPE_TO_DOMAIN.get(intent_type, "")

            except Exception as e:
                logger.debug("IntentBridge: classification failed: %s", e)

        # DIAGNOSE: always resolve domain from text for precision
        if intent_type == IntentType.DIAGNOSE:
            try:
                from lina.diagnostics.domain_resolver import resolve_domain
                resolved_domain, _conf = resolve_domain(text)
                if resolved_domain and _conf > 0.3:
                    domain = resolved_domain
            except Exception:
                if not domain:
                    domain = "system"

        return intent_type, domain, action, confidence

    def _handle_chat(self, intent: Intent, pipeline_handler: Any) -> IntentResult:
        """Chat flow: governance passthrough, then LLM."""
        # Query governance for logging/telemetry
        if self._gov_router:
            gov_result = self._gov_router.process(intent)
            # Governance returns CHAT_RESPONSE → LLM pipeline should handle
            if pipeline_handler and gov_result.status == IntentStatus.CHAT_RESPONSE:
                try:
                    response = pipeline_handler(intent.user_text)
                    return IntentResult(
                        intent_id=intent.id,
                        status=IntentStatus.CHAT_RESPONSE,
                        response_text=response if isinstance(response, str) else str(response),
                    )
                except Exception as e:
                    logger.error("LLM pipeline error: %s", e, exc_info=True)
                    return IntentResult(
                        intent_id=intent.id,
                        status=IntentStatus.FAILED,
                        response_text="Внутренняя ошибка при обработке запроса.",
                    )
            return gov_result

        # No governance → direct pipeline
        if pipeline_handler:
            try:
                response = pipeline_handler(intent.user_text)
                return IntentResult(
                    intent_id=intent.id,
                    status=IntentStatus.CHAT_RESPONSE,
                    response_text=response if isinstance(response, str) else str(response),
                )
            except Exception as e:
                logger.error("Direct pipeline error: %s", e, exc_info=True)
                return IntentResult(
                    intent_id=intent.id,
                    status=IntentStatus.FAILED,
                    response_text="Внутренняя ошибка при обработке запроса.",
                )

        return IntentResult(
            intent_id=intent.id,
            status=IntentStatus.CHAT_RESPONSE,
            response_text="",
        )

    def _handle_action(self, intent: Intent, pipeline_handler: Any) -> IntentResult:
        """Action flow: governance pipeline with full access/policy checks."""
        if self._gov_router:
            result = self._gov_router.process(intent)
            if result.status == IntentStatus.DENIED:
                self._stats["denied"] += 1
            return result

        # Fallback: reject without governance
        return IntentResult(
            intent_id=intent.id,
            status=IntentStatus.DENIED,
            response_text="Governance не инициализирован. Действие заблокировано.",
        )

    def _fallback_diagnostics(self, intent: Intent, user_text: str) -> IntentResult:
        """Fallback to diagnostics/integration if governance KB has no data."""
        try:
            from lina.diagnostics.integration import diagnose as diag_fn
            query = user_text or intent.domain
            result = diag_fn(query)
            if result.get("matched"):
                return IntentResult(
                    intent_id=intent.id,
                    status=IntentStatus.SUCCESS,
                    response_text=result.get("formatted", "Диагностика завершена."),
                    action_result=result.get("report", {}),
                    metadata={"source": "diagnostics_engine"},
                )
            # Not matched — still return what we have
            return IntentResult(
                intent_id=intent.id,
                status=IntentStatus.NOT_FOUND,
                response_text="Диагностика не нашла известных решений. "
                              "Попробуйте описать проблему подробнее.",
                metadata={"needs_llm": result.get("needs_llm", True)},
            )
        except Exception as e:
            logger.error("Diagnostics error: %s", e, exc_info=True)
            return IntentResult(
                intent_id=intent.id,
                status=IntentStatus.FAILED,
                response_text="Внутренняя ошибка диагностики.",
            )

    # ── Stats ────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)


# ─── Singleton ────────────────────────────────────────────────────────────────

_bridge: Optional[IntentBridge] = None


def get_intent_bridge() -> IntentBridge:
    """Получить единственный IntentBridge."""
    global _bridge
    if _bridge is None:
        _bridge = IntentBridge()
    return _bridge
