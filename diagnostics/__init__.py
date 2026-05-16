"""Lina — Diagnostic Engine: деревья решений + PROBLEM TERMINATOR + SYSTEM OVERLORD."""

from lina.diagnostics.scanner import SystemStateScanner, get_scanner
from lina.diagnostics.log_engine import LogIntelligenceEngine, get_log_engine
from lina.diagnostics.classifier import ErrorClassifier, get_classifier
from lina.diagnostics.autofix import AutoFixEngine, get_autofix
from lina.diagnostics.memory import ContextMemoryEngine, get_memory
from lina.diagnostics.control import FullSystemControlLayer, get_control

# Interactive diagnostics (v0.8.0)
from lina.diagnostics.session import DiagnosticSession, get_session, new_session

# SYSTEM OVERLORD modules
from lina.diagnostics.risk_engine import RiskEngine, get_risk_engine
from lina.diagnostics.root_agent import RootAgent, get_root_agent
from lina.diagnostics.predictor import PredictiveMonitor, get_predictor
from lina.diagnostics.drift import MachineDriftDetector, get_drift_detector
from lina.diagnostics.self_healer import SelfHealer, get_self_healer
from lina.diagnostics.integrity import IntegrityGuard, get_integrity_guard
from lina.diagnostics.snapshot import SnapshotManager, get_snapshot_manager
from lina.diagnostics.web_intel import WebIntelSandbox, get_web_intel_sandbox

__all__ = [
    # PROBLEM TERMINATOR
    "SystemStateScanner", "get_scanner",
    "LogIntelligenceEngine", "get_log_engine",
    "ErrorClassifier", "get_classifier",
    "AutoFixEngine", "get_autofix",
    "ContextMemoryEngine", "get_memory",
    "FullSystemControlLayer", "get_control",
    # Interactive diagnostics (v0.8.0)
    "DiagnosticSession", "get_session", "new_session",
    # SYSTEM OVERLORD
    "RiskEngine", "get_risk_engine",
    "RootAgent", "get_root_agent",
    "PredictiveMonitor", "get_predictor",
    "MachineDriftDetector", "get_drift_detector",
    "SelfHealer", "get_self_healer",
    "IntegrityGuard", "get_integrity_guard",
    "SnapshotManager", "get_snapshot_manager",
    "WebIntelSandbox", "get_web_intel_sandbox",
]
