# -*- coding: utf-8 -*-
"""
Lina Core — Orchestrator (Single Heavy Model Architecture).

Phase 20: Одна full-модель для всех задач.

Архитектура:
  User
    ↓
  RAG Heuristic Check (keyword-based)
    ↓
  FullModel.generate()
    ↓
  HumanResponseLayer
    ↓
  User

Нет mini модели.
Нет IntentClassifier.
Нет JSON routing.
Нет dual-mode.

Гарантии:
  - Пользователь НИКОГДА не видит JSON, routing, debug
  - 100% ответов — связный, вежливый, завершённый текст
  - Все логи → logger.debug
  - Fallback: перегенерация 1 раз при мусоре

SYSTEM DIRECTIVE — SINGLE MODEL ORCHESTRATION.
"""

import logging
import time
import re
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, List

from lina.core.human_response import (
    HumanResponseLayer,
    SAFE_FALLBACK_RESPONSE,
)
from lina.core.context_budget import ContextBudgetManager

logger = logging.getLogger("lina.core.orchestrator")


# ═══════════════════════════════════════════════════════════
#  RAG Heuristic
# ═══════════════════════════════════════════════════════════

# Ключевые слова для RAG retrieval
RAG_KEYWORDS = [
    "документ", "база", "config", "лог", "файл",
    "knowledge", "знани", "конфиг", "настройк",
    "в базе", "в документ", "из файл",
]

# Минимальный порог релевантности RAG-контекста
RAG_RELEVANCE_THRESHOLD = 0.05


def _check_rag_heuristic(query: str) -> bool:
    """Проверяет, нужен ли RAG retrieval по ключевым словам.

    Без intent JSON. Без classifier. Простая эвристика.

    Args:
        query: Запрос пользователя.

    Returns:
        True если запрос содержит RAG-ключевые слова.
    """
    q = query.lower().strip()
    return any(kw in q for kw in RAG_KEYWORDS)


# ═══════════════════════════════════════════════════════════
#  Tool Safety Layer
# ═══════════════════════════════════════════════════════════

# Запрещённые паттерны команд
BLOCKED_PATTERNS = [
    re.compile(r"rm\s+-rf\s+/", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\bhalt\b", re.IGNORECASE),
    re.compile(r"\bpoweroff\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if\s*=", re.IGNORECASE),
    re.compile(r"chmod\s+777\s+/", re.IGNORECASE),
    re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE),
    re.compile(r"rm\s+-rf\s+~", re.IGNORECASE),
    re.compile(r"rm\s+-rf\s+\*", re.IGNORECASE),
]


@dataclass
class SafetyVerdict:
    """Вердикт безопасности."""
    safe: bool = True
    blocked_pattern: str = ""
    plan: str = ""
    query: str = ""

    def to_dict(self) -> dict:
        return {
            "safe": self.safe,
            "blocked_pattern": self.blocked_pattern,
            "plan": self.plan,
        }


class ToolSafetyLayer:
    """Слой безопасности инструментов."""

    def __init__(self, extra_patterns: Optional[List[re.Pattern]] = None):
        self._patterns = list(BLOCKED_PATTERNS)
        if extra_patterns:
            self._patterns.extend(extra_patterns)
        self._stats = {"checked": 0, "blocked": 0, "allowed": 0}
        self._stats_lock = threading.Lock()

    def check(self, query: str) -> SafetyVerdict:
        with self._stats_lock:
            self._stats["checked"] += 1
        for pattern in self._patterns:
            if pattern.search(query):
                with self._stats_lock:
                    self._stats["blocked"] += 1
                return SafetyVerdict(
                    safe=False,
                    blocked_pattern=pattern.pattern,
                    query=query,
                )
        with self._stats_lock:
            self._stats["allowed"] += 1
        return SafetyVerdict(
            safe=True,
            plan=f"Выполнить: {query[:100]}",
            query=query,
        )

    def get_stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)


# ═══════════════════════════════════════════════════════════
#  Orchestrator Result
# ═══════════════════════════════════════════════════════════

@dataclass
class OrchestratorResult:
    """Результат оркестратора.

    response — ВСЕГДА очищенный человеческий текст.
    """
    response: str = ""
    rag_used: bool = False
    model_used: str = "full"
    is_fallback: bool = False
    elapsed: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "response_length": len(self.response),
            "rag_used": self.rag_used,
            "model_used": self.model_used,
            "is_fallback": self.is_fallback,
            "elapsed": round(self.elapsed, 3),
        }


# ═══════════════════════════════════════════════════════════
#  Lina Orchestrator — Single Heavy Model
# ═══════════════════════════════════════════════════════════

class LinaOrchestrator:
    """Главный оркестратор Lina — Phase 20.

    Single Heavy Model Architecture:
      User → RAG heuristic → FullModel.generate() → HumanResponseLayer → User

    Одна full-модель для всех задач.
    Нет mini. Нет IntentClassifier. Нет JSON routing.

    Гарантии:
      - 100% ответов — связный человеческий текст
      - 0% JSON/routing/debug в финальном output
      - Все debug → logger.debug
      - Fallback: перегенерация 1 раз при мусоре
    """

    def __init__(
        self,
        generate_fn: Optional[Callable] = None,
        rag_fn: Optional[Callable] = None,
        tool_execute_fn: Optional[Callable] = None,
    ):
        """Инициализация оркестратора.

        Args:
            generate_fn: Генерация через full-модель.
                fn(query, context, system_prompt) → str
            rag_fn: RAG-поиск.
                fn(query) → str (контекст)
            tool_execute_fn: Выполнение инструментов.
                fn(query) → str (результат)
        """
        self.safety = ToolSafetyLayer()
        self._generate_fn = generate_fn
        self._rag_fn = rag_fn
        self._tool_execute_fn = tool_execute_fn

        # Phase 20.2: Context Budget Manager
        self.context_budget = ContextBudgetManager()

        # HumanResponseLayer: fallback = перегенерация 1 раз
        self.human_layer = HumanResponseLayer(
            fallback_fn=self._regenerate_once,
        )

        self._stats = {
            "total_requests": 0,
            "successful": 0,
            "failed": 0,
            "rag_used": 0,
            "regenerations": 0,
        }
        self._stats_lock = threading.Lock()
        self._local = threading.local()  # per-thread current_query

    # ───────────────────────────────────────────────────────
    #  Основной метод
    # ───────────────────────────────────────────────────────

    def process(self, query: str) -> OrchestratorResult:
        """Обрабатывает запрос.

        Pipeline:
          1. RAG heuristic → optional context injection
          2. FullModel.generate(query, context)
          3. HumanResponseLayer → clean response
          4. OrchestratorResult

        Args:
            query: Текст запроса пользователя.

        Returns:
            OrchestratorResult — response ВСЕГДА человеческий текст.
        """
        start_time = time.time()
        with self._stats_lock:
            self._stats["total_requests"] += 1
        self._local.current_query = query

        try:
            # ── 1. RAG heuristic ──
            rag_context = ""
            rag_used = False

            if _check_rag_heuristic(query) and self._rag_fn is not None:
                try:
                    rag_context = self._rag_fn(query) or ""
                    if rag_context.strip():
                        rag_used = True
                        with self._stats_lock:
                            self._stats["rag_used"] += 1
                        logger.debug(
                            "RAG context injected: %d chars",
                            len(rag_context),
                        )
                except Exception as e:
                    logger.debug("RAG retrieval failed: %s", e)

            # ── 2. Safety check ──
            safety_verdict = self.safety.check(query)
            if not safety_verdict.safe:
                logger.warning("Safety blocked query: %s → %s", query[:80], safety_verdict.blocked_pattern)
                elapsed = time.time() - start_time
                with self._stats_lock:
                    self._stats["failed"] += 1
                return OrchestratorResult(
                    response="⛔ Команда заблокирована по правилу безопасности.",
                    rag_used=rag_used,
                    model_used="none",
                    is_fallback=False,
                    elapsed=elapsed,
                    metadata={"blocked": True, "pattern": safety_verdict.blocked_pattern},
                )

            # ── 3. Generate ──
            raw_response = self._generate(query, rag_context)
            logger.debug(
                "Generated: %d chars, rag=%s",
                len(raw_response),
                rag_used,
            )

            # ── 3. HumanResponseLayer ──
            sanitized = self.human_layer.sanitize(raw_response, query)
            logger.debug(
                "Sanitize: was_sanitized=%s leakage=%s fallback=%s",
                sanitized.was_sanitized,
                sanitized.leakage_detected,
                sanitized.fallback_used,
            )

            elapsed = time.time() - start_time
            with self._stats_lock:
                self._stats["successful"] += 1

            return OrchestratorResult(
                response=sanitized.text,
                rag_used=rag_used,
                model_used="full",
                is_fallback=sanitized.fallback_used,
                elapsed=elapsed,
                metadata={
                    "rag_context_length": len(rag_context),
                    "sanitize": sanitized.to_dict(),
                },
            )

        except Exception as e:
            # ── Failsafe ──
            elapsed = time.time() - start_time
            with self._stats_lock:
                self._stats["failed"] += 1

            logger.debug(
                "Orchestrator error: %s → safe fallback", str(e)[:100],
            )

            fallback_response = self._safe_generate(query)
            sanitized = self.human_layer.sanitize(fallback_response, query)

            return OrchestratorResult(
                response=sanitized.text,
                rag_used=False,
                model_used="full",
                is_fallback=True,
                elapsed=elapsed,
                metadata={
                    "failsafe_error": str(e)[:200],
                    "sanitize": sanitized.to_dict(),
                },
            )

    # ───────────────────────────────────────────────────────
    #  Generation
    # ───────────────────────────────────────────────────────

    def _generate(self, query: str, context: str = "") -> str:
        """Генерация через full-модель.

        Args:
            query: Запрос.
            context: RAG-контекст (может быть пустым).

        Returns:
            Сырой ответ модели.
        """
        if self._generate_fn is None:
            return SAFE_FALLBACK_RESPONSE

        return self._generate_fn(
            query=query,
            context=context,
            system_prompt="",
        )

    def _safe_generate(self, query: str) -> str:
        """Безопасная генерация с перехватом ошибок."""
        if self._generate_fn is not None:
            try:
                return self._generate_fn(
                    query=query,
                    context="",
                    system_prompt="",
                )
            except Exception as e:
                logger.debug("Safe generate failed: %s", e)  # noqa

        return SAFE_FALLBACK_RESPONSE

    def _regenerate_once(self, query: str) -> str:
        """Перегенерация 1 раз (fallback для HumanResponseLayer).

        Вызывается когда первый ответ был мусором.
        """
        with self._stats_lock:
            self._stats["regenerations"] += 1
        target = query or getattr(self._local, 'current_query', '')
        logger.debug("Regenerating for: %s", target[:50])
        return self._safe_generate(target)

    # ───────────────────────────────────────────────────────
    #  Статистика
    # ───────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Полная статистика оркестратора."""
        with self._stats_lock:
            snap = dict(self._stats)
        return {
            "orchestrator": snap,
            "safety": self.safety.get_stats(),
            "human_layer": self.human_layer.get_stats(),
        }

    def format_status(self) -> str:
        """Форматированный статус."""
        with self._stats_lock:
            s = dict(self._stats)
        return (
            f"🎯 Orchestrator: {s['total_requests']} requests "
            f"({s['successful']} ok, {s['failed']} failed, "
            f"rag={s['rag_used']}, regen={s['regenerations']})"
        )
