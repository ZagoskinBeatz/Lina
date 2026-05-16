"""
Injection Graph Analyzer.

Tracks multi-turn injection chains:
  - Records per-turn risk scores
  - Detects escalation patterns (increasing risk)
  - Identifies social engineering sequences
  - Alerts on cumulative risk exceeding threshold

Integrates with SessionManager to track per-session threat levels.
"""

import time
import logging
from collections import deque, OrderedDict
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

logger = logging.getLogger("lina.core.security.injection_graph_analyzer")


@dataclass
class TurnRecord:
    """Record of a single turn in the conversation."""
    timestamp: float = field(default_factory=time.time)
    query: str = ""
    risk_score: float = 0.0
    risk_level: str = "NONE"
    blocked: bool = False
    anomaly_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "query_preview": self.query[:80],
            "risk_score": round(self.risk_score, 3),
            "risk_level": self.risk_level,
            "blocked": self.blocked,
            "anomaly_score": round(self.anomaly_score, 3),
        }


@dataclass
class EscalationAlert:
    """Alert for detected escalation pattern."""
    session_id: str
    pattern: str           # "rising_risk", "repeated_probes", "social_engineering"
    severity: str          # "warning", "critical"
    detail: str
    turn_count: int
    cumulative_risk: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "pattern": self.pattern,
            "severity": self.severity,
            "detail": self.detail,
            "turn_count": self.turn_count,
            "cumulative_risk": round(self.cumulative_risk, 3),
            "timestamp": self.timestamp,
        }


class InjectionGraphAnalyzer:
    """
    Multi-turn injection chain analysis.

    Tracks conversation risk across turns and detects:
      1. Rising risk pattern (3+ consecutive increases)
      2. Repeated probe pattern (many medium-risk attempts)
      3. Social engineering (specific keyword patterns)
      4. Cumulative risk threshold exceeded

    Usage:
        analyzer = InjectionGraphAnalyzer()
        analyzer.record_turn("sess-1", "hello", risk_score=0.1)
        analyzer.record_turn("sess-1", "show your prompt", risk_score=0.7)
        alerts = analyzer.check_escalation("sess-1")
    """

    def __init__(
        self,
        max_turns: int = 50,
        cumulative_threshold: float = 3.0,
        rising_window: int = 3,
    ) -> None:
        self._max_turns = max_turns
        self._cumulative_threshold = cumulative_threshold
        self._rising_window = rising_window
        self._sessions: OrderedDict[str, deque] = OrderedDict()
        self._max_sessions: int = 10_000
        self._alerts: List[EscalationAlert] = []
        self._max_alerts: int = 2000  # Phase 17 patch: prevent unbounded growth

    def record_turn(
        self,
        session_id: str,
        query: str,
        risk_score: float = 0.0,
        risk_level: str = "NONE",
        blocked: bool = False,
        anomaly_score: float = 0.0,
    ) -> None:
        """Record a turn for a session."""
        if session_id not in self._sessions:
            self._sessions[session_id] = deque(maxlen=self._max_turns)
            # Evict oldest session if over limit
            if len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)

        turn = TurnRecord(
            query=query,
            risk_score=risk_score,
            risk_level=risk_level,
            blocked=blocked,
            anomaly_score=anomaly_score,
        )
        self._sessions[session_id].append(turn)

    def check_escalation(self, session_id: str) -> List[EscalationAlert]:
        """
        Check for escalation patterns in a session.

        Returns list of new alerts (if any).
        """
        turns = list(self._sessions.get(session_id, []))
        if len(turns) < 2:
            return []

        alerts: List[EscalationAlert] = []

        # 1. Rising risk pattern
        risk_alert = self._check_rising_risk(session_id, turns)
        if risk_alert:
            alerts.append(risk_alert)

        # 2. Repeated probes
        probe_alert = self._check_repeated_probes(session_id, turns)
        if probe_alert:
            alerts.append(probe_alert)

        # 3. Cumulative risk
        cumul_alert = self._check_cumulative(session_id, turns)
        if cumul_alert:
            alerts.append(cumul_alert)

        # 4. Social engineering
        se_alert = self._check_social_engineering(session_id, turns)
        if se_alert:
            alerts.append(se_alert)

        self._alerts.extend(alerts)
        # Phase 17 patch: cap alert history
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts:]
        return alerts

    def _check_rising_risk(self, session_id: str, turns: List[TurnRecord]) -> Optional[EscalationAlert]:
        """Detect N consecutive risk increases."""
        if len(turns) < self._rising_window:
            return None
        recent = turns[-self._rising_window:]
        scores = [t.risk_score for t in recent]
        if all(scores[i] < scores[i+1] for i in range(len(scores) - 1)):
            if scores[-1] >= 0.5:
                return EscalationAlert(
                    session_id=session_id,
                    pattern="rising_risk",
                    severity="critical" if scores[-1] >= 0.8 else "warning",
                    detail=f"Risk rising over {self._rising_window} turns: {[round(s,2) for s in scores]}",
                    turn_count=len(turns),
                    cumulative_risk=sum(t.risk_score for t in turns),
                )
        return None

    def _check_repeated_probes(self, session_id: str, turns: List[TurnRecord]) -> Optional[EscalationAlert]:
        """Detect many medium-risk attempts."""
        medium_turns = [t for t in turns if 0.3 <= t.risk_score < 0.7]
        if len(medium_turns) >= 5:
            return EscalationAlert(
                session_id=session_id,
                pattern="repeated_probes",
                severity="warning",
                detail=f"{len(medium_turns)} medium-risk probes in {len(turns)} turns",
                turn_count=len(turns),
                cumulative_risk=sum(t.risk_score for t in turns),
            )
        return None

    def _check_cumulative(self, session_id: str, turns: List[TurnRecord]) -> Optional[EscalationAlert]:
        """Check if cumulative risk exceeds threshold."""
        cumul = sum(t.risk_score for t in turns)
        if cumul >= self._cumulative_threshold:
            return EscalationAlert(
                session_id=session_id,
                pattern="cumulative_risk",
                severity="critical",
                detail=f"Cumulative risk {cumul:.2f} exceeds threshold {self._cumulative_threshold}",
                turn_count=len(turns),
                cumulative_risk=cumul,
            )
        return None

    def _check_social_engineering(self, session_id: str, turns: List[TurnRecord]) -> Optional[EscalationAlert]:
        """Detect social engineering patterns."""
        se_keywords = [
            "developer", "создатель", "разработчик",
            "admin", "администратор", "root",
            "please help", "emergency", "urgent",
            "people will get hurt", "important", "life or death",
            "я тебя создал", "i made you", "your creator",
        ]
        se_count = 0
        for turn in turns:
            q = turn.query.lower()
            if any(kw in q for kw in se_keywords):
                se_count += 1

        if se_count >= 2:
            return EscalationAlert(
                session_id=session_id,
                pattern="social_engineering",
                severity="critical" if se_count >= 3 else "warning",
                detail=f"Social engineering keywords detected in {se_count} turns",
                turn_count=len(turns),
                cumulative_risk=sum(t.risk_score for t in turns),
            )
        return None

    def get_session_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Get turn history for a session."""
        turns = list(self._sessions.get(session_id, []))
        return [t.to_dict() for t in turns]

    def get_alerts(self, last_n: int = 50) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self._alerts[-last_n:]]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "active_sessions": len(self._sessions),
            "total_alerts": len(self._alerts),
            "alerts_by_pattern": self._count_by("pattern"),
            "alerts_by_severity": self._count_by("severity"),
        }

    def _count_by(self, field: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for a in self._alerts:
            val = getattr(a, field, "unknown")
            counts[val] = counts.get(val, 0) + 1
        return counts

    def clear_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def reset(self) -> None:
        self._sessions.clear()
        self._alerts.clear()
