"""
StrategySelector — выбор оптимальной стратегии из Knowledge Base.

Логика:
  1. Получить сигнатуру ошибки (ErrorSignature)
  2. Поиск в UserKB → LocalKB (точное → нечёткое совпадение)
  3. Фильтр по PolicyEngine (risk, domain)
  4. Ранжирование по: confidence, success_rate, risk_level, freshness
  5. Возврат лучшей стратегии с fallback'ами

Phase: GOVERNANCE LAYER / Module 4
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class Strategy:
    """Одна стратегия решения проблемы."""
    id: str
    name: str
    domain: str
    description: str = ""
    description_ru: str = ""
    actions: List[str] = field(default_factory=list)
    action_params: List[Dict[str, str]] = field(default_factory=list)
    risk_level: str = "low"
    confidence: float = 0.8
    success_rate: float = 0.0
    total_attempts: int = 0
    total_successes: int = 0
    requires_reboot: bool = False
    reversible: bool = True
    reverse_strategy: str = ""
    tags: List[str] = field(default_factory=list)
    source: str = "local_kb"   # local_kb | user_kb | inferred
    created: float = 0.0
    updated: float = 0.0

    def __post_init__(self) -> None:
        if not self.created:
            self.created = time.time()
        if not self.updated:
            self.updated = self.created

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def record_attempt(self, success: bool) -> None:
        """Записать результат попытки."""
        self.total_attempts += 1
        if success:
            self.total_successes += 1
        if self.total_attempts > 0:
            self.success_rate = self.total_successes / self.total_attempts
        self.updated = time.time()


@dataclass
class StrategyMatch:
    """Результат поиска стратегии."""
    strategy: Strategy
    score: float
    match_type: str = "exact"     # exact | fuzzy | inferred
    match_details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy.id,
            "strategy_name": self.strategy.name,
            "score": round(self.score, 3),
            "match_type": self.match_type,
            "match_details": self.match_details,
            "risk_level": self.strategy.risk_level,
            "actions": self.strategy.actions,
        }


@dataclass
class SelectionResult:
    """Результат выбора стратегии."""
    best: Optional[StrategyMatch] = None
    alternatives: List[StrategyMatch] = field(default_factory=list)
    total_candidates: int = 0
    search_time: float = 0.0
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best": self.best.to_dict() if self.best else None,
            "alternatives": [a.to_dict() for a in self.alternatives[:5]],
            "total_candidates": self.total_candidates,
            "search_time": round(self.search_time, 3),
            "source": self.source,
        }


# ─── Risk weights ────────────────────────────────────────────────────────────

_RISK_SCORE = {"none": 0, "low": 0.1, "medium": 0.3, "high": 0.6, "critical": 1.0}


# ─── StrategySelector ────────────────────────────────────────────────────────

class StrategySelector:
    """
    Выбирает оптимальную стратегию из набора кандидатов.

    Алгоритм ранжирования:
      score = (confidence * 0.3)
            + (success_rate * 0.3)
            + ((1 - risk_factor) * 0.2)
            + (freshness * 0.1)
            + (match_quality * 0.1)

    Пример:
        selector = get_strategy_selector()
        result = selector.select(
            domain="network",
            signature_tags=["dns", "timeout", "resolved"],
            candidates=strategies_list,
        )
    """

    def __init__(self) -> None:
        self._strategies: Dict[str, Strategy] = {}
        self._selection_log: List[Dict[str, Any]] = []
        self._max_log = 1000

    # ── Repository ───────────────────────────────────────

    def add_strategy(self, strategy: Strategy) -> None:
        """Добавить стратегию в локальный индекс."""
        self._strategies[strategy.id] = strategy

    def get_strategy(self, strategy_id: str) -> Optional[Strategy]:
        """Получить стратегию по ID."""
        return self._strategies.get(strategy_id)

    def remove_strategy(self, strategy_id: str) -> bool:
        """Удалить стратегию."""
        if strategy_id in self._strategies:
            del self._strategies[strategy_id]
            return True
        return False

    def list_strategies(self, domain: str = "") -> List[Strategy]:
        """Список стратегий."""
        result = list(self._strategies.values())
        if domain:
            result = [s for s in result if s.domain == domain]
        return sorted(result, key=lambda s: -s.confidence)

    # ── Selection ────────────────────────────────────────

    def select(self, *,
               domain: str = "",
               signature_tags: Optional[List[str]] = None,
               candidates: Optional[List[Strategy]] = None,
               max_risk: str = "high",
               min_confidence: float = 0.3,
               limit: int = 5) -> SelectionResult:
        """
        Выбрать лучшую стратегию.

        Args:
            domain: домен проблемы (фильтр)
            signature_tags: теги из сигнатуры ошибки
            candidates: явный список кандидатов (или из внутреннего репо)
            max_risk: максимальный допустимый уровень риска
            min_confidence: минимальная уверенность
            limit: максимум альтернатив
        """
        t0 = time.monotonic()
        tags = set(signature_tags or [])

        # Gather candidates
        pool = candidates if candidates is not None else list(self._strategies.values())
        if domain:
            pool = [s for s in pool if s.domain == domain]

        # Filter by risk
        max_risk_score = _RISK_SCORE.get(max_risk, 1.0)
        pool = [s for s in pool
                if _RISK_SCORE.get(s.risk_level, 0) <= max_risk_score]

        # Filter by confidence
        pool = [s for s in pool if s.confidence >= min_confidence]

        total = len(pool)

        # Score each
        scored: List[StrategyMatch] = []
        for strategy in pool:
            match_type, match_quality = self._calc_match(strategy, tags)
            score = self._calc_score(strategy, match_quality)
            scored.append(StrategyMatch(
                strategy=strategy, score=score,
                match_type=match_type,
                match_details=f"tags_overlap={match_quality:.2f}",
            ))

        # Sort by score descending
        scored.sort(key=lambda m: -m.score)
        search_time = time.monotonic() - t0

        result = SelectionResult(
            best=scored[0] if scored else None,
            alternatives=scored[1:limit] if len(scored) > 1 else [],
            total_candidates=total,
            search_time=search_time,
            source="local",
        )

        # Log
        self._log_selection(domain, tags, result)
        return result

    def _calc_match(self, strategy: Strategy,
                    tags: set) -> tuple[str, float]:
        """Вычислить качество совпадения тегов."""
        if not tags or not strategy.tags:
            return "inferred", 0.3

        stags = set(strategy.tags)
        overlap = tags & stags
        union = tags | stags

        if not union:
            return "inferred", 0.3

        jaccard = len(overlap) / len(union)

        if jaccard >= 0.8:
            return "exact", jaccard
        elif jaccard >= 0.3:
            return "fuzzy", jaccard
        else:
            return "inferred", jaccard

    def _calc_score(self, strategy: Strategy,
                    match_quality: float) -> float:
        """
        Вычислить общий скор стратегии.

        score = confidence * 0.3 + success_rate * 0.3
              + (1 - risk) * 0.2 + freshness * 0.1
              + match_quality * 0.1
        """
        risk_factor = _RISK_SCORE.get(strategy.risk_level, 0)
        age = time.time() - strategy.updated
        # Freshness: 1.0 for < 1h, decays to 0.1 over 30 days
        freshness = max(0.1, 1.0 - (age / (30 * 86400)))

        return (
            strategy.confidence * 0.3
            + strategy.success_rate * 0.3
            + (1.0 - risk_factor) * 0.2
            + freshness * 0.1
            + match_quality * 0.1
        )

    def _log_selection(self, domain: str, tags: set,
                       result: SelectionResult) -> None:
        """Записать в лог."""
        entry = {
            "timestamp": time.time(),
            "domain": domain,
            "tags": sorted(tags),
            "best": result.best.strategy.id if result.best else None,
            "candidates": result.total_candidates,
            "search_time": result.search_time,
        }
        self._selection_log.append(entry)
        if len(self._selection_log) > self._max_log:
            self._selection_log = self._selection_log[-self._max_log:]

    # ── Feedback ─────────────────────────────────────────

    def record_outcome(self, strategy_id: str, success: bool) -> None:
        """Записать результат применения стратегии."""
        s = self._strategies.get(strategy_id)
        if s:
            s.record_attempt(success)
            logger.info("StrategySelector: %s attempt=%d success_rate=%.2f",
                        strategy_id, s.total_attempts, s.success_rate)

    # ── Stats ────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Статистика."""
        return {
            "total_strategies": len(self._strategies),
            "selection_log": len(self._selection_log),
            "domains": list(set(s.domain for s in self._strategies.values())),
        }

    def get_selection_log(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self._selection_log[-limit:]


# ─── Singleton ─────────────────────────────────────────────────────────────────

_selector: Optional[StrategySelector] = None

def get_strategy_selector() -> StrategySelector:
    """Получить единственный экземпляр StrategySelector."""
    global _selector
    if _selector is None:
        _selector = StrategySelector()
    return _selector
