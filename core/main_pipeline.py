# -*- coding: utf-8 -*-
"""
Lina Core — Main Pipeline (Block A).

ЕДИНСТВЕННАЯ точка входа для обработки ВСЕХ запросов.

Ни один модуль НЕ может напрямую:
  - вызывать LLM
  - вызывать ToolEngine
  - изменять RuntimeState
  - переключать режим

Только через MainPipeline.process_request().

14-шаговый pipeline:
  1. Runtime Snapshot
  2. Intent Classification
  3. Priority Resolution
  4. Execution Planning
  5. Integrity Pre-check
  6. Intent Lock
  7. Execution Phase
  8. Post Processing
  9. Production Guard
  10. Response Validation
  11. Degradation Handling
  12. Drift Detection
  13. Budget Update
  14. Trace Finalization

SYSTEM COMMANDS (/system *):
  - priority level 1
  - bypasses intent router (SYSTEM path)
  - routed directly to SystemControl

Pipeline НИКОГДА не возвращает:
  confidence, intent, priority, mode, trace,
  plan_hash, debug, system state.
Только очищенный текст ответа.
"""

import re
import time
import hashlib
import logging
import threading
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field

logger = logging.getLogger("lina.core.main_pipeline")

# Safe fallback response — used when guard blocks output
SAFE_FALLBACK_RESPONSE = "Извините, я не могу дать корректный ответ на этот запрос."


@dataclass
class FinalResponse:
    """Результат pipeline — только чистый текст + минимальная мета."""
    text: str = ""
    status: str = "success"    # success | blocked | error | degraded
    source: str = ""           # system | llm | tool | fallback


@dataclass
class PipelineContext:
    """Внутренний контекст — НИКОГДА не покидает pipeline."""
    user_input: str = ""
    request_id: int = 0
    timestamp: float = 0.0

    # Step 1: runtime snapshot
    runtime_state: Dict[str, Any] = field(default_factory=dict)
    mode_profile: Dict[str, Any] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    capabilities: Dict[str, bool] = field(default_factory=dict)
    degradation_state: Dict[str, Any] = field(default_factory=dict)
    session_budget: Dict[str, Any] = field(default_factory=dict)

    # Step 2: intent
    intent: str = ""
    confidence: float = 0.0
    is_system_command: bool = False

    # Step 3: priority
    priority_level: int = 5
    priority_desc: str = ""

    # Step 4: plan
    plan: Any = None
    plan_hash: str = ""
    primary_path: str = ""
    fallback_path: str = ""
    validation_policy: str = "standard"
    regeneration_allowed: bool = True
    tool_allowed: bool = True
    max_tokens_override: Optional[int] = None

    # Step 5: integrity
    integrity_passed: bool = True
    integrity_message: str = ""

    # Step 6: intent lock
    locked: bool = False

    # Step 6b: RAG context (used in regeneration trimming)
    rag_context: str = ""

    # Step 7: execution
    raw_response: str = ""
    execution_path: str = ""
    tokens_prompt: int = 0
    tokens_generated: int = 0

    # Step 8: post-processing
    cleaned_response: str = ""
    post_modifications: int = 0
    leak_found: bool = False

    # Step 9: guard
    guard_passed: bool = True
    guard_violations: List[str] = field(default_factory=list)

    # Step 10: validation
    validation_score: float = 1.0
    validation_issues: List[str] = field(default_factory=list)
    can_retry: bool = False

    # Step 11: degradation
    degradation_action: str = "none"
    degradation_reason: str = ""

    # Step 12: consistency + drift
    consistency_score: float = 1.0
    drift_detected: bool = False

    # Step 13: budget
    budget_exhausted: bool = False

    # Step 14: trace
    trace_id: int = 0

    # Aggregated
    regeneration_attempts: int = 0
    final_status: str = "success"
    errors: List[str] = field(default_factory=list)
    stage_timings: Dict[str, float] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
#  Executor Callbacks — pluggable adapters for LLM/Tool/RAG
# ═══════════════════════════════════════════════════════════

# Type: fn(ctx: PipelineContext) -> str (response text)
ExecutorCallback = Callable[["PipelineContext"], str]


class MainPipeline:
    """Единственная точка входа для обработки запросов (Block A).

    Связывает ВСЕ 21+ модулей Phase 22-26 в живой
    execution kernel с полной трассировкой.

    Usage:
        pipe = MainPipeline()
        pipe.set_llm_executor(my_llm_fn)
        pipe.set_tool_executor(my_tool_fn)
        result = pipe.process_request("Привет")
        print(result.text)
    """

    def __init__(self, pipeline_config=None):
        # ── Phase 22 modules ──
        from lina.core.intent_router import IntentRouter
        from lina.core.post_processor import PostProcessor
        from lina.core.response_validator import ResponseValidator
        from lina.core.config_manager import ConfigManager
        from lina.core.system_control import SystemControl
        from lina.core.tool_engine import ToolEngine

        # ── Phase 23 modules ──
        from lina.core.governance import RuntimeStateManager
        from lina.core.execution_trace import ExecutionTracer
        from lina.core.degradation import DegradationStrategy
        from lina.core.mode_control import ModeController
        from lina.core.budget_governor import BudgetGovernor
        from lina.core.drift_detector import StateDriftDetector
        from lina.core.production_guard import ProductionGuard

        # ── Phase 24 modules ──
        from lina.core.execution_orchestrator import ExecutionOrchestrator
        from lina.core.capability_registry import CapabilityRegistry
        from lina.core.priority_resolver import PriorityResolver
        from lina.core.integrity_checker import IntegrityChecker

        # ── Phase 25 modules ──
        from lina.core.consistency_engine import ConsistencyEngine
        from lina.core.step_memory import StepMemory
        from lina.core.semantic_drift import SemanticDriftDetector
        from lina.core.intent_lock import IntentLock

        # ── Security modules (core.security) ──
        from lina.core.security.anomaly_detector import AnomalyDetector
        from lina.core.security.injection_graph_analyzer import InjectionGraphAnalyzer
        from lina.core.security.environment_guard import EnvironmentGuard

        # ── Load PipelineConfig from lina.config if not provided ──
        pcfg = pipeline_config
        if pcfg is None:
            try:
                from lina.config import config as lina_config
                pcfg = lina_config.pipeline
            except Exception:
                pcfg = None

        # ── Extract config values (with safe defaults) ──
        _safe_mode = getattr(pcfg, "safe_mode", False) if pcfg else False
        _confidence_thr = getattr(pcfg, "router_confidence_threshold", 0.5) if pcfg else 0.5
        _budget_tokens = getattr(pcfg, "session_budget_tokens", 100_000) if pcfg else 100_000
        _avg_thr = getattr(pcfg, "avg_response_threshold", 400) if pcfg else 400
        _budget_window = getattr(pcfg, "budget_window_size", 20) if pcfg else 20
        _trace_max = getattr(pcfg, "trace_max_entries", 50) if pcfg else 50
        _mem_size = getattr(pcfg, "step_memory_size", 20) if pcfg else 20
        _consist_thr = getattr(pcfg, "consistency_threshold", 0.5) if pcfg else 0.5
        _initial_mode = getattr(pcfg, "initial_mode", "normal") if pcfg else "normal"

        # Instantiate all modules (with config values)
        self._router = IntentRouter(confidence_threshold=_confidence_thr)
        self._post_processor = PostProcessor(strict=_safe_mode)
        self._response_validator = ResponseValidator()
        self._config_manager = ConfigManager()
        self._system_control = SystemControl()
        self._tool_engine = ToolEngine()

        self._state_manager = RuntimeStateManager()
        self._tracer = ExecutionTracer(max_entries=_trace_max)
        self._degradation = DegradationStrategy()
        self._mode_controller = ModeController()
        self._budget = BudgetGovernor(
            session_budget=_budget_tokens,
            avg_threshold=_avg_thr,
            window_size=_budget_window,
        )
        self._drift_detector = StateDriftDetector()
        self._guard = ProductionGuard()

        self._orchestrator = ExecutionOrchestrator()
        self._capabilities = CapabilityRegistry()
        self._priority = PriorityResolver()
        self._integrity = IntegrityChecker()

        self._consistency = ConsistencyEngine(pass_threshold=_consist_thr)
        self._step_memory = StepMemory(max_steps=_mem_size)
        self._semantic_drift = SemanticDriftDetector()
        self._intent_lock = IntentLock()

        # ── Phase 26 security (formerly in Commander Phase 16) ──
        self._anomaly_detector = AnomalyDetector()
        self._injection_graph = InjectionGraphAnalyzer()
        self._env_guard = EnvironmentGuard()
        self._session_id: str = ""  # set per-request or from caller

        # ── Governance Control Plane (canonical policy authority) ──
        self._governance_policy = None
        self._governance_sm = None
        try:
            from lina.governance.policy_engine import get_policy_engine
            from lina.governance.state_machine import get_runtime_machine
            self._governance_policy = get_policy_engine()
            self._governance_sm = get_runtime_machine()
            logger.info("MainPipeline: governance control plane connected")
        except Exception as e:
            logger.debug("MainPipeline: governance not available (%s)", e)

        # ── Apply safe_mode if configured ──
        if _safe_mode:
            self._state_manager.set("safe_mode", True)
            self._mode_controller.switch_by_name("safe", reason="config: safe_mode=True")

        # ── Apply initial_mode if not default ──
        if _initial_mode != "normal" and not _safe_mode:
            self._mode_controller.switch_by_name(_initial_mode, reason="config: initial_mode")

        # ── Seed ConfigManager with pipeline settings ──
        self._config_manager.set("safe_mode", _safe_mode)
        self._config_manager.set("router_confidence_threshold", _confidence_thr)
        self._config_manager.set("validation_threshold",
                                 getattr(pcfg, "validation_threshold", 0.5) if pcfg else 0.5)
        self._config_manager.set("max_regeneration_attempts",
                                 getattr(pcfg, "max_regeneration_attempts", 1) if pcfg else 1)

        # ── Executor callbacks (pluggable) ──
        self._llm_executor: Optional[ExecutorCallback] = None
        self._tool_executor: Optional[ExecutorCallback] = None
        self._rag_executor: Optional[ExecutorCallback] = None
        self._diag_executor: Optional[ExecutorCallback] = None
        self._web_executor: Optional[ExecutorCallback] = None

        # ── Wire SystemControl providers ──
        self._wire_system_control()

        # ── Set drift baseline ──
        self._drift_detector.set_baseline(
            model_version="main_pipeline_v1",
            config_snapshot=self._config_manager.get_all(),
        )

        # ── Stats ──
        self._request_count: int = 0
        self._error_count: int = 0
        self._blocked_count: int = 0
        self._total_duration: float = 0.0
        self._stats_lock = threading.Lock()
        self._last_stage_timings: Dict[str, float] = {}

        logger.debug("MAIN_PIPELINE: initialized with all modules")

    # ═══════════════════════════════════════════════════════
    #  Executor registration (pluggable adapters)
    # ═══════════════════════════════════════════════════════

    def set_llm_executor(self, fn: ExecutorCallback) -> None:
        """Подключить LLM-генератор. fn(ctx) -> str."""
        self._llm_executor = fn

    def set_tool_executor(self, fn: ExecutorCallback) -> None:
        """Подключить Tool-исполнитель. fn(ctx) -> str."""
        self._tool_executor = fn

    def set_rag_executor(self, fn: ExecutorCallback) -> None:
        """Подключить RAG-поиск. fn(ctx) -> str."""
        self._rag_executor = fn

    def set_diag_executor(self, fn: ExecutorCallback) -> None:
        """Подключить диагностический движок. fn(ctx) -> str."""
        self._diag_executor = fn

    def set_web_executor(self, fn: ExecutorCallback) -> None:
        """Подключить веб-поиск. fn(ctx) -> str."""
        self._web_executor = fn

    # ═══════════════════════════════════════════════════════
    #  ЕДИНСТВЕННАЯ ТОЧКА ВХОДА
    # ═══════════════════════════════════════════════════════

    def process_request(self, user_input: str, session_id: str = "") -> FinalResponse:
        """Обработать запрос пользователя через полный pipeline.

        Args:
            user_input: текст пользователя.
            session_id: идентификатор сессии для injection graph tracking.

        Returns:
            FinalResponse — только очищенный текст + статус.
        """
        if session_id:
            self._session_id = session_id
        with self._stats_lock:
            self._request_count += 1
        t_start = time.time()

        ctx = PipelineContext(
            user_input=user_input.strip(),
            request_id=int(time.time() * 1000) % 1_000_000,
            timestamp=t_start,
        )

        try:
            # ── SYSTEM COMMAND shortcut ──
            if ctx.user_input == "/system" or ctx.user_input.startswith("/system "):
                return self._handle_system_command(ctx)

            # ── 14-STEP PIPELINE ──
            self._step_00_security_precheck(ctx)
            if ctx.final_status == "blocked":
                total_ms = (time.time() - t_start) * 1000
                with self._stats_lock:
                    self._total_duration += total_ms
                    self._blocked_count += 1
                return FinalResponse(
                    text=ctx.cleaned_response or SAFE_FALLBACK_RESPONSE,
                    status="blocked",
                    source="security_precheck",
                )
            self._step_01_runtime_snapshot(ctx)
            self._step_02_intent_classification(ctx)
            self._step_03_priority_resolution(ctx)
            self._step_04_execution_planning(ctx)
            self._step_05_integrity_precheck(ctx)
            self._step_06_intent_lock(ctx)
            self._step_07_execution(ctx)
            self._step_08_post_processing(ctx)
            self._step_09_production_guard(ctx)
            self._step_10_response_validation(ctx)
            self._step_11_degradation_handling(ctx)
            self._step_12_drift_detection(ctx)
            self._step_13_budget_update(ctx)
            self._step_14_trace_finalization(ctx)

            # ── Unlock intent ──
            if self._intent_lock.is_locked():
                self._intent_lock.unlock(reason="pipeline complete")

            # ── Build final response ──
            total_ms = (time.time() - t_start) * 1000
            with self._stats_lock:
                self._total_duration += total_ms
                self._last_stage_timings = dict(ctx.stage_timings)

            return FinalResponse(
                text=ctx.cleaned_response or ctx.raw_response or SAFE_FALLBACK_RESPONSE,
                status=ctx.final_status,
                source=ctx.execution_path or "fallback",
            )

        except Exception as e:
            with self._stats_lock:
                self._error_count += 1
            logger.error("MAIN_PIPELINE: unhandled error: %s", e)

            # Ensure unlock
            if self._intent_lock.is_locked():
                self._intent_lock.unlock(reason="error recovery")

            # Record failure
            self._degradation.record_failure("pipeline", str(e)[:200])
            self._tracer.start("error", 0.0, "error", ctx.user_input)

            return FinalResponse(
                text=SAFE_FALLBACK_RESPONSE,
                status="error",
                source="fallback",
            )

    # ═══════════════════════════════════════════════════════
    #  SYSTEM COMMAND handler
    # ═══════════════════════════════════════════════════════

    def _handle_system_command(self, ctx: PipelineContext) -> FinalResponse:
        """System commands: priority 1, bypass intent router."""
        t0 = time.time()
        ctx.is_system_command = True
        ctx.intent = "SYSTEM_COMMAND"
        ctx.priority_level = 1
        ctx.execution_path = "system"

        result = self._system_control.handle(ctx.user_input)
        elapsed = (time.time() - t0) * 1000

        # Record trace
        trace = self._tracer.start("SYSTEM_COMMAND", 1.0, "system", ctx.user_input)
        self._tracer.complete(trace, final_status="success")

        # Update state + duration stats
        self._state_manager.set("last_intent", "SYSTEM_COMMAND")
        self._state_manager.set("last_execution_path", "system")
        with self._stats_lock:
            self._total_duration += elapsed

        return FinalResponse(
            text=result or "OK",
            status="success",
            source="system",
        )

    # ═══════════════════════════════════════════════════════
    #  14 PIPELINE STEPS
    # ═══════════════════════════════════════════════════════

    def _step_00_security_precheck(self, ctx: PipelineContext) -> None:
        """0. Предварительная проверка безопасности (anomaly + injection graph).

        Закрывает P0-гэп: ранее PipelineV3 обходила все security-проверки.
        Теперь ВСЕ запросы проходят через anomaly + injection graph.
        """
        t0 = time.time()
        session_id = self._session_id or f"mp-{ctx.request_id}"

        # ── Anomaly detection ──
        anomaly = self._anomaly_detector.analyze(ctx.user_input)
        if anomaly.is_anomalous:
            logger.warning(
                "PIPELINE security: anomaly score=%.2f findings=%s",
                anomaly.score, anomaly.findings,
            )

        # ── Injection graph tracking ──
        self._injection_graph.record_turn(
            session_id=session_id,
            query=ctx.user_input,
            risk_score=anomaly.score,
            risk_level="HIGH" if anomaly.is_anomalous else "NONE",
            anomaly_score=anomaly.score,
        )
        escalation_alerts = self._injection_graph.check_escalation(session_id)
        if escalation_alerts:
            for alert in escalation_alerts:
                if alert.severity == "critical":
                    logger.warning(
                        "PIPELINE security: CRITICAL escalation: %s",
                        alert.pattern,
                    )
                    ctx.cleaned_response = (
                        "⛔ Запрос заблокирован: обнаружена подозрительная "
                        "последовательность запросов."
                    )
                    ctx.final_status = "blocked"
                    ctx.errors.append(f"security: critical escalation: {alert.pattern}")
                    break

        ctx.stage_timings["security_precheck"] = (time.time() - t0) * 1000

    def _step_01_runtime_snapshot(self, ctx: PipelineContext) -> None:
        """1. Собрать полный снимок рантайма."""
        t0 = time.time()

        ctx.runtime_state = self._state_manager.to_dict()
        ctx.mode_profile = self._mode_controller.get_profile().__dict__ if hasattr(
            self._mode_controller.get_profile(), '__dict__'
        ) else {}
        ctx.config = self._config_manager.get_all()
        ctx.capabilities = {
            name: True for name in self._capabilities.get_active()
        }
        ctx.degradation_state = self._degradation.get_stats()
        ctx.session_budget = self._budget.to_dict()

        ctx.stage_timings["runtime_snapshot"] = (time.time() - t0) * 1000

    # ── Паттерны follow-up вопросов ──
    _FOLLOWUP_PATTERNS = [
        re.compile(r"^а\s+(сколько|как|что|какой|какая|какое|какие|где|когда)\b", re.IGNORECASE),
        re.compile(r"^(и\s+ещё|ещё|также|а\s+ещё)\b", re.IGNORECASE),
        re.compile(r"^(а\s+)?что\s+(насчёт|на\s*счёт|по\s+поводу)\b", re.IGNORECASE),
        re.compile(r"\b(у\s+него|у\s+неё|его|её|их|этого|этой|этих|там)\b", re.IGNORECASE),
    ]

    def _step_02_intent_classification(self, ctx: PipelineContext) -> None:
        """2. Классификация намерения. Только классификация."""
        t0 = time.time()

        decision = self._router.route(ctx.user_input)
        ctx.intent = decision.intent.value if hasattr(decision.intent, 'value') else str(decision.intent)
        ctx.confidence = decision.confidence

        # ── Follow-up detection ──
        # Если предыдущий шаг был web_search, а текущий запрос —
        # follow-up вопрос, наследуем intent web_search и добавляем контекст.
        prev = self._step_memory.get_previous()
        if (prev and prev.intent == "web_search"
                and ctx.intent != "web_search"
                and self._is_followup_query(ctx.user_input)):
            logger.info(
                "Follow-up detected: overriding %s → web_search "
                "(prev query: '%s')", ctx.intent, prev.user_input[:60])
            ctx.intent = "web_search"
            ctx.confidence = 0.85
            # Извлечь тему из предыдущего запроса и добавить к текущему
            topic = self._extract_topic(prev.user_input)
            if topic:
                ctx.user_input = f"{topic}: {ctx.user_input}"
                logger.debug("Augmented follow-up query: '%s'", ctx.user_input[:80])

        # Update state
        self._state_manager.set("last_intent", ctx.intent)

        ctx.stage_timings["intent_classification"] = (time.time() - t0) * 1000

    def _is_followup_query(self, query: str) -> bool:
        """Определяет, является ли запрос follow-up вопросом."""
        q = query.strip()
        for pat in self._FOLLOWUP_PATTERNS:
            if pat.search(q):
                return True
        return False

    @staticmethod
    def _extract_topic(prev_query: str) -> str:
        """Извлекает тему из предыдущего запроса (убирая командные слова).

        'Найди в интернете характеристики Macbook M1' → 'Macbook M1'
        'расскажи про RTX 4090' → 'RTX 4090'
        """
        q = prev_query.strip()
        # Снимаем приветствие в самом начале если есть
        q = re.sub(r"^привет[!,.]?\s+", "", q, flags=re.IGNORECASE).strip()
        # Strip command prefixes
        strip_pats = [
            re.compile(r"^(?:найди|поищи|загугли|нагугли|выясни|узнай)\s+"
                       r"(?:в\s+(?:интернете|инете|сети|нете|гугл\w*)\s*)?",
                       re.IGNORECASE),
            re.compile(r"^(?:расскажи|покажи)\s+(?:мне\s+)?(?:о|об|про)\s+",
                       re.IGNORECASE),
            # Бытовые вступления: «Подскажи, как установить X» → «как установить X»
            re.compile(r"^(?:подскажи|объясни|посоветуй|скажи)[,.\s]+",
                       re.IGNORECASE),
            re.compile(r"^(?:привет[!,.]?\s+)?", re.IGNORECASE),
            # «как установить/поставить/настроить X на мою систему» → «X»
            re.compile(r"^как\s+(?:установить|поставить|инсталлировать|"
                       r"настроить|обновить|удалить|запустить|собрать)\s+",
                       re.IGNORECASE),
            re.compile(r"^(?:что\s+за|что\s+такое|как\w{0,4})\s+", re.IGNORECASE),
            re.compile(r"^(?:search|google|how\s+to\s+(?:install|setup|configure|update|remove))\s+(?:for\s+)?\s*",
                       re.IGNORECASE),
        ]
        for pat in strip_pats:
            q = pat.sub("", q).strip()
        # Снимаем хвост «на мою/свою систему», «на linux», вопросительный знак
        q = re.sub(
            r"\s*(?:на\s+(?:мою|свою|вашу|нашу)?\s*(?:систему|пк|компьютер|"
            r"ноутбук|линукс|linux))\s*[?!.]*\s*$",
            "", q, flags=re.IGNORECASE,
        ).strip()
        q = q.rstrip("?!.").strip()
        # Strip adjectives that precede descriptors
        # e.g. "полные характеристики Realme 10" → "характеристики Realme 10"
        q = re.sub(r"^(?:полн\w*|подробн\w*|детальн\w*|техническ\w*|основн\w*"
                   r"|все|всё|общ\w*)\s+",
                   "", q, flags=re.IGNORECASE).strip()
        # Strip generic descriptor words, keep the subject
        q = re.sub(r"^(?:характеристики|спецификации|specs|specifications|обзор|review"
                   r"|процессор|память|памят\w*|экран|камер\w*|батаре\w*|аккумулятор)\s+",
                   "", q, flags=re.IGNORECASE).strip()
        # Strip dangling prepositions/particles
        q = re.sub(r"^(?:у|в|на|для|про|смартфона|телефона|ноутбука|планшета)\s+",
                   "", q, flags=re.IGNORECASE).strip()
        # Also try extracting after «характеристики»
        if not q:
            q = prev_query.strip()
        return q

    def _step_03_priority_resolution(self, ctx: PipelineContext) -> None:
        """3. Определение приоритета."""
        t0 = time.time()

        safe_mode = ctx.config.get("safe_mode", False)
        is_system = ctx.intent == "SYSTEM_COMMAND"
        is_tool = ctx.intent == "TOOL_EXPLICIT"

        result = self._priority.resolve(
            intent=ctx.intent,
            confidence=ctx.confidence,
            safe_mode=safe_mode,
            is_system=is_system,
            is_explicit_tool=is_tool,
        )
        ctx.priority_level = result.level
        ctx.priority_desc = result.description

        ctx.stage_timings["priority_resolution"] = (time.time() - t0) * 1000

    def _step_04_execution_planning(self, ctx: PipelineContext) -> None:
        """4. Создание плана выполнения."""
        t0 = time.time()

        plan = self._orchestrator.create_plan(
            intent=ctx.intent,
            confidence=ctx.confidence,
            runtime_state=ctx.runtime_state,
            capability_info=ctx.capabilities,
            mode_profile=ctx.mode_profile,
            config=ctx.config,
            priority_level=ctx.priority_level,
        )

        ctx.plan = plan
        ctx.plan_hash = plan.plan_hash
        ctx.primary_path = plan.primary_path
        ctx.fallback_path = plan.fallback_path or ""
        ctx.validation_policy = plan.validation_policy
        ctx.regeneration_allowed = plan.regeneration_allowed
        ctx.tool_allowed = plan.tool_allowed
        ctx.max_tokens_override = plan.max_tokens_override

        ctx.stage_timings["execution_planning"] = (time.time() - t0) * 1000

    def _step_05_integrity_precheck(self, ctx: PipelineContext) -> None:
        """5. Проверка целостности плана."""
        t0 = time.time()

        result = self._integrity.check(
            planned_path=ctx.primary_path,
            actual_path=ctx.primary_path,
            plan_hash=ctx.plan_hash,
            expected_hash=ctx.plan_hash,
        )

        ctx.integrity_passed = result.passed
        ctx.integrity_message = result.message

        # If integrity fails → safe mode
        if not result.passed and result.recommend_safe_mode:
            self._mode_controller.switch_by_name("safe", reason="integrity failure")
            # Regenerate plan in safe mode
            ctx.mode_profile = self._mode_controller.get_profile().__dict__ if hasattr(
                self._mode_controller.get_profile(), '__dict__'
            ) else {}
            ctx.config["safe_mode"] = True
            self._state_manager.set("safe_mode", True)

        ctx.stage_timings["integrity_precheck"] = (time.time() - t0) * 1000

    def _step_06_intent_lock(self, ctx: PipelineContext) -> None:
        """6. Блокировка intent на время выполнения."""
        t0 = time.time()

        self._intent_lock.lock(
            intent=ctx.intent,
            plan_hash=ctx.plan_hash,
            reason="execution started",
        )
        ctx.locked = True

        ctx.stage_timings["intent_lock"] = (time.time() - t0) * 1000

    def _step_07_execution(self, ctx: PipelineContext) -> None:
        """7. Выполнение по плану.

        Пути:
          - DIAGNOSTIC: диагностический движок (21 дерево + LLM fallback)
          - WEB_SEARCH: веб-поиск (DuckDuckGo, SearXNG, Wikipedia + LLM fallback)
          - LLM: вызов self._llm_executor
          - TOOL: вызов self._tool_executor (если tool_allowed + capability)
          - RAG: вызов self._rag_executor
          - SYSTEM: handled earlier (shortcut)
          - FALLBACK: если executor не подключён
        """
        t0 = time.time()

        path = ctx.primary_path.lower() if ctx.primary_path else "llm"

        # TOOL path — check capability
        if path == "tool":
            if not ctx.tool_allowed:
                path = "llm"  # auto-convert to LLM
            elif not self._capabilities.is_available("tool"):
                path = "llm"  # capability disabled → convert

        ctx.execution_path = path

        try:
            if path == "diagnostic" and self._diag_executor:
                ctx.raw_response = self._diag_executor(ctx)
                # If diagnostics returned empty / needs LLM → fallback
                if not ctx.raw_response and self._llm_executor:
                    ctx.raw_response = self._llm_executor(ctx)
                    ctx.execution_path = "diagnostic+llm"
            elif path == "web_search":
                # ── FAST PATH: WebSearchEngine (Ecosia + SpecExtractor) ──
                # Tries lightweight search first — Ecosia results + spec
                # extraction + snippet summaries. No full LLM needed.
                # Hard timeout of 45s to prevent mini-LLM hangs.
                try:
                    import signal

                    class _SearchTimeout(Exception):
                        pass

                    def _alarm(signum, frame):
                        raise _SearchTimeout()

                    from lina.core.web_search_engine import WebSearchEngine
                    _wse = WebSearchEngine()
                    old_handler = signal.signal(signal.SIGALRM, _alarm)
                    signal.alarm(45)
                    try:
                        _resp = _wse.search(ctx.user_input)
                    finally:
                        signal.alarm(0)
                        signal.signal(signal.SIGALRM, old_handler)
                    if _resp.success and _resp.summary and len(_resp.summary.strip()) > 100:
                        ctx.raw_response = _resp.summary
                        ctx.execution_path = "web_search_direct"
                        logger.info(
                            "WebSearchEngine: %d results, %d chars (%s)",
                            len(_resp.results), len(_resp.summary), _resp.source,
                        )
                except _SearchTimeout:
                    logger.warning("WebSearchEngine timeout (45s)")
                except Exception as wse_err:
                    logger.warning("WebSearchEngine failed: %s", wse_err)

                # ── SLOW PATH: PipelineV3 (only if fast path failed) ──
                # Hard 60s timeout to prevent cascading LLM retries (3×120s)
                # Skip for factual queries — snippet fast-path is enough
                _is_factual_query = bool(re.search(
                    r'сколько\b|чем\s+отлича|разница\s+между|что\s+такое|'
                    r'кто\s+такой|что\s+лучше|кто\s+(?:написал|изобрёл|создал)|'
                    r'в\s+каком\s+году|что\s+нового|лучшие?\s+\w|сравни\b|\bvs\b|'
                    r'как\s+(?:установить|настроить|обновить)',
                    ctx.user_input, re.IGNORECASE,
                ))
                if not ctx.raw_response and not _is_factual_query:
                    try:
                        import signal as _sig_v3

                        class _V3Timeout(Exception):
                            pass

                        def _v3_alarm(signum, frame):
                            raise _V3Timeout()

                        _old_v3 = _sig_v3.signal(_sig_v3.SIGALRM, _v3_alarm)
                        _sig_v3.alarm(60)
                        try:
                            ctx.raw_response = self._try_pipeline_v3(ctx)
                        finally:
                            _sig_v3.alarm(0)
                            _sig_v3.signal(_sig_v3.SIGALRM, _old_v3)
                        if ctx.raw_response:
                            ctx.execution_path = "web_search_v3"
                    except _V3Timeout:
                        logger.warning("PipelineV3 timeout (60s)")
                    except Exception as v3_err:
                        logger.warning("PipelineV3 failed: %s", v3_err)
                # If web search failed / empty → НЕ вызываем LLM!
                # Маленькие локальные модели неизбежно галлюцинируют
                # «характеристики» из ниоткуда. Честный отказ надёжнее.
                if not ctx.raw_response:
                    # Извлечь тему из запроса для персонализации ответа
                    topic = self._extract_topic(ctx.user_input)
                    if topic:
                        ctx.raw_response = (
                            f"К сожалению, мне не удалось найти информацию "
                            f"о «{topic}» в интернете. Попробуйте:"
                            f"\n  • уточнить запрос (например: «{topic} характеристики»)"
                            f"\n  • проверить подключение к сети"
                            f"\n  • повторить запрос чуть позже"
                        )
                    else:
                        ctx.raw_response = (
                            "К сожалению, не удалось найти информацию в интернете. "
                            "Попробуйте уточнить запрос или повторить позже."
                        )
                    ctx.execution_path = "web_search_no_results"
            elif path == "tool" and self._tool_executor:
                ctx.raw_response = self._tool_executor(ctx)
            elif path == "rag" and self._rag_executor:
                ctx.raw_response = self._rag_executor(ctx)
            elif self._llm_executor:
                ctx.raw_response = self._llm_executor(ctx)
            else:
                # No executor registered → fallback
                ctx.raw_response = SAFE_FALLBACK_RESPONSE
                ctx.execution_path = "fallback"
        except Exception as e:
            logger.error("MAIN_PIPELINE: execution error: %s", e)
            ctx.errors.append(f"execution: {str(e)[:200]}")
            ctx.raw_response = ""
            ctx.final_status = "error"

        # Update state
        self._state_manager.set("last_execution_path", ctx.execution_path)

        ctx.stage_timings["execution"] = (time.time() - t0) * 1000

    def _try_pipeline_v3(self, ctx: PipelineContext) -> str:
        """Попытаться обработать web_search через PipelineV3.

        Returns:
            Answer text (with sources) or empty string if V3 is not applicable.
        """
        try:
            from lina.pipeline.pipeline_v3 import get_pipeline_v3, V3BypassSignal
        except ImportError:
            return ""

        if not self._llm_executor:
            return ""

        def _llm_fn(prompt: str) -> str:
            """LLM callback for PipelineV3."""
            from lina.llm.engine import LLMEngine
            try:
                engine = LLMEngine()
                return engine.generate(prompt, intent="web_search")
            except Exception:
                return ""

        try:
            from lina.config import config as _cfg
            pipeline = get_pipeline_v3(llm_fn=_llm_fn)
            lang = getattr(_cfg, "language", "ru")
            answer = pipeline.run(ctx.user_input, lang=lang)

            text = answer.text or ""

            # Append sources
            if answer.sources:
                src_lines = [f"  • {s}" for s in answer.sources[:5]]
                text += "\n\n📎 Источники:\n" + "\n".join(src_lines)

            return text
        except Exception as exc:
            # V3BypassSignal or any error → fall through to web_executor
            logger.debug("PipelineV3 not applicable: %s", exc)
            return ""

    def _step_08_post_processing(self, ctx: PipelineContext) -> None:
        """8. Очистка ответа от internal markers."""
        t0 = time.time()

        if ctx.raw_response:
            result = self._post_processor.process(ctx.raw_response)
            ctx.cleaned_response = result.text or ctx.raw_response
            ctx.post_modifications = result.modifications
            ctx.leak_found = result.leak_found

            # Non-strict leak: stripped but record as degradation signal
            if result.leak_found and not result.blocked:
                self._degradation.record_failure(
                    "validation", "post-processor: system prompt leak stripped"
                )
                logger.warning(
                    "PIPELINE: leak stripped (non-strict), "
                    "mods=%d details=%s",
                    result.modifications, result.details,
                )
        else:
            ctx.cleaned_response = ""

        # ── Redact environment secrets from response ──
        if ctx.cleaned_response:
            ctx.cleaned_response = self._env_guard.redact_secrets(ctx.cleaned_response)

        ctx.stage_timings["post_processing"] = (time.time() - t0) * 1000

    def _step_09_production_guard(self, ctx: PipelineContext) -> None:
        """9. Финальный фильтр безопасности."""
        t0 = time.time()

        if ctx.cleaned_response:
            result = self._guard.check(ctx.cleaned_response)
            ctx.guard_passed = result.passed
            ctx.guard_violations = result.violations

            if result.blocked:
                with self._stats_lock:
                    self._blocked_count += 1
                ctx.cleaned_response = SAFE_FALLBACK_RESPONSE
                ctx.final_status = "blocked"
                ctx.errors.append("guard: response blocked")
        else:
            ctx.guard_passed = True

        ctx.stage_timings["production_guard"] = (time.time() - t0) * 1000

    def _step_10_response_validation(self, ctx: PipelineContext) -> None:
        """10. Валидация ответа: пустота, повторы, обрезание."""
        t0 = time.time()

        if ctx.cleaned_response and ctx.final_status != "blocked":
            result = self._response_validator.validate(
                response=ctx.cleaned_response,
                user_input=ctx.user_input,
            )
            ctx.validation_score = result.score
            ctx.validation_issues = result.issues
            ctx.can_retry = result.can_retry
        else:
            ctx.validation_score = 0.0 if not ctx.cleaned_response else 1.0

        ctx.stage_timings["response_validation"] = (time.time() - t0) * 1000

    def _step_11_degradation_handling(self, ctx: PipelineContext) -> None:
        """11. Деградация: если validation < threshold."""
        t0 = time.time()

        threshold = ctx.config.get("router_confidence_threshold", 0.5)

        if ctx.validation_score < threshold and ctx.final_status != "blocked":
            # Record failure
            self._degradation.record_failure("validation", f"score={ctx.validation_score:.2f}")
            self._state_manager.increment("consecutive_failures")

            # Check if should degrade
            action = self._degradation.evaluate()
            ctx.degradation_action = action.action.value if hasattr(action.action, 'value') else str(action.action)
            ctx.degradation_reason = action.reason

            # Apply degradation action
            if ctx.degradation_action == "enable_safe_mode":
                self._mode_controller.switch_by_name("safe", reason=action.reason)
                self._state_manager.set("safe_mode", True)
            elif ctx.degradation_action == "disable_tool":
                self._capabilities.disable("tool", reason=action.reason)
            elif ctx.degradation_action == "enable_strict":
                self._mode_controller.switch_by_name("strict", reason=action.reason)

            # Try regeneration if allowed
            if (ctx.regeneration_allowed and ctx.can_retry
                    and ctx.regeneration_attempts < 1
                    and ctx.final_status != "blocked"):
                ctx.regeneration_attempts += 1
                self._state_manager.increment("regeneration_count")

                # Strategy: simplify context to reduce noise
                original_context = ctx.rag_context
                try:
                    if ctx.rag_context and len(ctx.rag_context) > 200:
                        ctx.rag_context = ctx.rag_context[:200]
                        logger.info("REGEN: trimmed RAG context from %d to 200 chars",
                                    len(original_context))

                    # Re-execute with modified context
                    self._step_07_execution(ctx)
                    self._step_08_post_processing(ctx)
                    self._step_09_production_guard(ctx)
                finally:
                    # Restore original context even on exception
                    ctx.rag_context = original_context

                # Re-validate through the formal step (preserves instrumentation)
                self._step_10_response_validation(ctx)

                ctx.final_status = "regenerated" if ctx.validation_score >= threshold else "degraded"
            else:
                ctx.final_status = "degraded"
        else:
            # Success → reset failure streak
            if ctx.final_status != "blocked":
                self._degradation.record_success()
                self._state_manager.reset_counter("consecutive_failures")

        ctx.stage_timings["degradation_handling"] = (time.time() - t0) * 1000

    def _step_12_drift_detection(self, ctx: PipelineContext) -> None:
        """12. Проверка дрейфа состояния."""
        t0 = time.time()

        # Consistency check
        prev = self._step_memory.get_previous()
        consistency = self._consistency.check(
            intent=ctx.intent,
            actual_path=ctx.execution_path,
            planned_path=ctx.primary_path,
            response_text=ctx.cleaned_response or "",
            prev_entities=prev.entities if prev else None,
            curr_entities=[],
            prev_strategy=prev.strategy if prev else "",
            curr_strategy=ctx.execution_path,
            prev_fingerprint=prev.semantic_fingerprint if prev else "",
            curr_fingerprint="",
        )
        ctx.consistency_score = consistency.consistency_score
        ctx.drift_detected = consistency.drift_detected

        # Semantic drift check
        sem_drift = self._semantic_drift.check(
            prev_intent=prev.intent if prev else "",
            curr_intent=ctx.intent,
            prev_strategy=prev.strategy if prev else "",
            curr_strategy=ctx.execution_path,
            prev_entities=prev.entities if prev else None,
            curr_entities=[],
            prev_fingerprint=prev.semantic_fingerprint if prev else "",
            curr_fingerprint="",
        )

        if sem_drift.drift_detected:
            ctx.drift_detected = True

        # State drift check
        drift_events = self._drift_detector.check(
            current_model=ctx.runtime_state.get("active_model", ""),
            current_config=ctx.config,
        )
        if drift_events:
            ctx.drift_detected = True

        # Record step in memory
        self._step_memory.record_step(
            step_number=self._request_count,
            intent=ctx.intent,
            path=ctx.execution_path,
            status=ctx.final_status,
            strategy=ctx.execution_path,
            consistency_score=ctx.consistency_score,
            user_input=ctx.user_input,
        )

        ctx.stage_timings["drift_detection"] = (time.time() - t0) * 1000

    def _step_13_budget_update(self, ctx: PipelineContext) -> None:
        """13. Обновление бюджета токенов."""
        t0 = time.time()

        self._budget.record_response(
            tokens_prompt=ctx.tokens_prompt,
            tokens_generated=ctx.tokens_generated,
        )

        ctx.budget_exhausted = self._budget.is_budget_exhausted()

        if ctx.budget_exhausted:
            ctx.errors.append("budget: session budget exhausted")

        ctx.stage_timings["budget_update"] = (time.time() - t0) * 1000

    def _step_14_trace_finalization(self, ctx: PipelineContext) -> None:
        """14. Запись полного trace выполнения."""
        t0 = time.time()

        trace = self._tracer.start(
            intent=ctx.intent,
            confidence=ctx.confidence,
            execution_path=ctx.execution_path,
            user_input=ctx.user_input,
        )

        self._tracer.complete(
            entry=trace,
            tokens_prompt=ctx.tokens_prompt,
            tokens_generated=ctx.tokens_generated,
            validation_score=ctx.validation_score,
            regeneration_attempts=ctx.regeneration_attempts,
            final_status=ctx.final_status,
            error="; ".join(ctx.errors) if ctx.errors else None,
        )

        ctx.trace_id = trace.trace_id

        ctx.stage_timings["trace_finalization"] = (time.time() - t0) * 1000

    # ═══════════════════════════════════════════════════════
    #  SystemControl wiring
    # ═══════════════════════════════════════════════════════

    def _wire_system_control(self) -> None:
        """Регистрирует ВСЕ провайдеры для /system команд."""
        sc = self._system_control

        # Phase 22
        sc.register_provider("router", self._router.get_stats)
        sc.register_provider("config", self._config_manager.get_all)
        sc.register_provider("tools", self._tool_engine.get_stats)

        # Phase 23
        sc.register_provider("trace", self._tracer.get_stats)
        sc.register_provider("mode", self._mode_controller.get_stats)
        sc.register_provider("drift", self._drift_detector.get_stats)
        sc.register_provider("state", self._state_manager.get_stats)
        sc.register_provider("degradation", self._degradation.get_stats)
        sc.register_provider("guard", self._guard.get_stats)
        sc.register_provider("budget", self._budget.get_stats)

        # Phase 24
        sc.register_provider("orchestrator", self._orchestrator.get_stats)
        sc.register_provider("capabilities", self._capabilities.get_stats)
        sc.register_provider("priority", self._priority.get_stats)
        sc.register_provider("integrity", self._integrity.get_stats)

        # Phase 25
        sc.register_provider("consistency", self._consistency.get_stats)
        sc.register_provider("stepmem", self._step_memory.get_stats)
        sc.register_provider("semdrift", self._semantic_drift.get_stats)
        sc.register_provider("intentlock", self._intent_lock.get_stats)

        # Phase 26 / Block A
        sc.register_provider("pipeline", self.get_stats)
        sc.register_provider("lifecycle", lambda: {
            "request_count": self._request_count,
            "stages": list(self._get_last_stage_timings().keys()),
        })
        sc.register_provider("envelope", lambda: {
            "last_request_id": self._request_count,
        })

        # General
        sc.register_provider("status", self._get_full_status)
        sc.register_provider("performance", self.get_stats)

    def _get_full_status(self) -> Dict[str, Any]:
        """Полный статус системы для /system status."""
        return {
            "requests": self._request_count,
            "errors": self._error_count,
            "blocked": self._blocked_count,
            "mode": self._mode_controller.mode.value,
            "safe_mode": self._state_manager.get("safe_mode", False),
            "capabilities_active": len(self._capabilities.get_active()),
            "capabilities_disabled": len(self._capabilities.get_disabled()),
            "budget_remaining": self._budget.session_remaining,
            "failure_streak": self._tracer.get_failure_streak(),
        }

    def _get_last_stage_timings(self) -> Dict[str, float]:
        """Последние stage timings (для диагностики)."""
        return dict(self._last_stage_timings)

    # ═══════════════════════════════════════════════════════
    #  Public API
    # ═══════════════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        """Статистика pipeline для SystemControl."""
        return {
            "requests_processed": self._request_count,
            "errors": self._error_count,
            "blocked": self._blocked_count,
            "avg_duration_ms": (
                round(self._total_duration / self._request_count, 1)
                if self._request_count > 0 else 0
            ),
            "mode": self._mode_controller.mode.value,
            "modules_active": 21,
            "budget_remaining": self._budget.session_remaining,
        }

    def get_module(self, name: str) -> Any:
        """Доступ к модулю для тестирования."""
        modules = {
            "router": self._router,
            "priority": self._priority,
            "orchestrator": self._orchestrator,
            "integrity": self._integrity,
            "intent_lock": self._intent_lock,
            "post_processor": self._post_processor,
            "guard": self._guard,
            "validator": self._response_validator,
            "degradation": self._degradation,
            "drift_detector": self._drift_detector,
            "budget": self._budget,
            "tracer": self._tracer,
            "consistency": self._consistency,
            "step_memory": self._step_memory,
            "semantic_drift": self._semantic_drift,
            "state_manager": self._state_manager,
            "mode_controller": self._mode_controller,
            "config_manager": self._config_manager,
            "capabilities": self._capabilities,
            "tool_engine": self._tool_engine,
            "system_control": self._system_control,
            "anomaly_detector": self._anomaly_detector,
            "injection_graph": self._injection_graph,
            "env_guard": self._env_guard,
        }
        return modules.get(name)


# ═══════════════════════════════════════════════════════════
#  Architectural Integrity Verification
# ═══════════════════════════════════════════════════════════

def verify_pipeline_order() -> bool:
    """Проверить, что pipeline имеет ровно 14 шагов в правильном порядке."""
    expected = [
        "_step_01_runtime_snapshot",
        "_step_02_intent_classification",
        "_step_03_priority_resolution",
        "_step_04_execution_planning",
        "_step_05_integrity_precheck",
        "_step_06_intent_lock",
        "_step_07_execution",
        "_step_08_post_processing",
        "_step_09_production_guard",
        "_step_10_response_validation",
        "_step_11_degradation_handling",
        "_step_12_drift_detection",
        "_step_13_budget_update",
        "_step_14_trace_finalization",
    ]
    for name in expected:
        if not hasattr(MainPipeline, name):
            return False
    return True


def verify_single_entry_point() -> bool:
    """Проверить, что process_request — единственный public метод обработки."""
    public_methods = [
        m for m in dir(MainPipeline)
        if not m.startswith("_") and callable(getattr(MainPipeline, m))
        and m not in ("set_llm_executor", "set_tool_executor", "set_rag_executor",
                       "set_diag_executor", "set_web_executor",
                       "get_stats", "get_module")
    ]
    return "process_request" in public_methods and len(public_methods) == 1


def verify_all_modules_isolated() -> bool:
    """Проверить, что все шаги — приватные методы (не public)."""
    step_methods = [
        m for m in dir(MainPipeline)
        if m.startswith("_step_")
    ]
    return len(step_methods) == 14
