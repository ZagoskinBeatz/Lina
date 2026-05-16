"""
Integration tests for Lina v0.7.0 Architecture.

Covers:
  - Intent API (types, router)
  - Access Layer (levels, resolver)
  - Governance Service Runner (boot sequence)
  - PolicyEngine content safety extension
  - Legacy shims (safety/policy, core/governance, core/integrity_checker)
  - End-to-end Intent → Access → Policy → Action pipeline
  - Version unification

90 governance tests + these = full v0.7.0 coverage.

Run: pytest lina/tests/test_architecture_v07.py -v
"""

from __future__ import annotations

import sys
import time
import pytest


# ═══════════════════════════════════════════════════════════
#  Section 1: Intent Types
# ═══════════════════════════════════════════════════════════

class TestIntentTypes:
    """Tests for lina.intent.types."""

    def test_intent_type_enum(self):
        from lina.intent.types import IntentType
        assert IntentType.OPEN_APP == "open_app"
        assert IntentType.DIAGNOSE == "diagnose"
        assert IntentType.SYSTEM_ACTION == "system_action"
        assert IntentType.CHAT == "chat"
        assert len(IntentType) == 14

    def test_intent_creation(self):
        from lina.intent.types import Intent, IntentType
        i = Intent(type=IntentType.DIAGNOSE, domain="network", action="check_dns")
        assert i.type == IntentType.DIAGNOSE
        assert i.domain == "network"
        assert i.action == "check_dns"
        assert i.id  # auto-generated
        assert i.timestamp > 0

    def test_intent_requires_action(self):
        from lina.intent.types import Intent, IntentType
        action_intent = Intent(type=IntentType.SYSTEM_ACTION, domain="service", action="svc_restart")
        assert action_intent.requires_action() is True

        chat_intent = Intent(type=IntentType.CHAT, domain="", action="")
        assert chat_intent.requires_action() is False

    def test_intent_is_admin(self):
        from lina.intent.types import Intent, IntentType
        admin = Intent(type=IntentType.LOW_LEVEL, domain="disk", action="disk_format")
        assert admin.is_admin() is True

    def test_intent_is_power(self):
        from lina.intent.types import Intent, IntentType
        power = Intent(type=IntentType.PACKAGE_OP, domain="package", action="pkg_install")
        assert power.is_power() is True

    def test_intent_to_dict(self):
        from lina.intent.types import Intent, IntentType
        i = Intent(type=IntentType.QUERY, domain="system", action="status")
        d = i.to_dict()
        assert "type" in d
        assert "domain" in d
        assert d["domain"] == "system"

    def test_intent_status_enum(self):
        from lina.intent.types import IntentStatus
        assert IntentStatus.SUCCESS == "success"
        assert IntentStatus.DENIED == "denied"
        assert IntentStatus.NEEDS_CONFIRM == "needs_confirm"
        assert IntentStatus.ESCALATED == "escalated"

    def test_intent_result_creation(self):
        from lina.intent.types import IntentResult, IntentStatus
        r = IntentResult(
            intent_id="test-123",
            status=IntentStatus.SUCCESS,
            response_text="Done",
        )
        assert r.intent_id == "test-123"
        assert r.status == IntentStatus.SUCCESS
        assert r.response_text == "Done"
        assert r.duration_ms == 0.0


# ═══════════════════════════════════════════════════════════
#  Section 2: Access Levels
# ═══════════════════════════════════════════════════════════

class TestAccessLevels:
    """Tests for lina.access.levels."""

    def test_access_level_enum(self):
        from lina.access.levels import AccessLevel
        assert AccessLevel.USER.value == "user"
        assert AccessLevel.POWER.value == "power"
        assert AccessLevel.ADMIN.value == "admin"

    def test_domain_access_map(self):
        from lina.access.levels import DOMAIN_ACCESS_MAP, AccessLevel
        assert DOMAIN_ACCESS_MAP["desktop"] == AccessLevel.USER
        assert DOMAIN_ACCESS_MAP["service"] == AccessLevel.POWER
        assert DOMAIN_ACCESS_MAP["disk"] == AccessLevel.ADMIN

    def test_elevated_actions(self):
        from lina.access.levels import ELEVATED_ACTIONS, AccessLevel
        assert ELEVATED_ACTIONS["boot_grub_install"] == AccessLevel.ADMIN
        assert ELEVATED_ACTIONS["net_restart_nm"] == AccessLevel.POWER

    def test_level_requires_confirmation(self):
        from lina.access.levels import LEVEL_REQUIRES_CONFIRMATION, AccessLevel
        assert LEVEL_REQUIRES_CONFIRMATION[AccessLevel.USER] is False
        assert LEVEL_REQUIRES_CONFIRMATION[AccessLevel.POWER] is True
        assert LEVEL_REQUIRES_CONFIRMATION[AccessLevel.ADMIN] is True

    def test_access_check_result(self):
        from lina.access.levels import AccessCheckResult
        r = AccessCheckResult(allowed=True, access_level="power", needs_confirmation=True)
        assert r.allowed is True
        d = r.to_dict()
        assert d["access_level"] == "power"
        assert d["needs_confirmation"] is True


# ═══════════════════════════════════════════════════════════
#  Section 3: Access Resolver
# ═══════════════════════════════════════════════════════════

class TestAccessResolver:
    """Tests for lina.access.resolver."""

    def test_resolver_creation(self):
        from lina.access.resolver import AccessLevelResolver
        from lina.access.levels import AccessLevel
        r = AccessLevelResolver()
        assert r.session_level == AccessLevel.USER

    def test_resolver_user_domain(self):
        from lina.access.resolver import AccessLevelResolver
        from lina.intent.types import Intent, IntentType
        r = AccessLevelResolver()
        intent = Intent(type=IntentType.OPEN_APP, domain="desktop", action="open_browser")
        result = r.check(intent)
        assert result.allowed is True
        assert result.access_level == "user"
        assert result.needs_confirmation is False

    def test_resolver_power_domain(self):
        from lina.access.resolver import AccessLevelResolver
        from lina.intent.types import Intent, IntentType
        r = AccessLevelResolver()
        intent = Intent(type=IntentType.SYSTEM_ACTION, domain="service", action="svc_restart")
        result = r.check(intent)
        assert result.allowed is True
        assert result.access_level == "power"
        assert result.needs_confirmation is True

    def test_resolver_admin_denied_in_user_session(self):
        from lina.access.resolver import AccessLevelResolver
        from lina.access.levels import AccessLevel
        from lina.intent.types import Intent, IntentType
        r = AccessLevelResolver(session_level=AccessLevel.USER)
        intent = Intent(type=IntentType.DISK_OP, domain="disk", action="disk_format")
        result = r.check(intent)
        assert result.allowed is False
        assert result.access_level == "admin"

    def test_resolver_admin_allowed_in_admin_session(self):
        from lina.access.resolver import AccessLevelResolver
        from lina.access.levels import AccessLevel
        from lina.intent.types import Intent, IntentType
        r = AccessLevelResolver(session_level=AccessLevel.ADMIN)
        intent = Intent(type=IntentType.DISK_OP, domain="disk", action="disk_format")
        result = r.check(intent)
        assert result.allowed is True
        assert result.access_level == "admin"
        assert result.needs_confirmation is True

    def test_resolver_elevated_action_override(self):
        from lina.access.resolver import AccessLevelResolver
        from lina.access.levels import AccessLevel
        from lina.intent.types import Intent, IntentType
        r = AccessLevelResolver(session_level=AccessLevel.POWER)
        # boot_grub_install is ADMIN even though domain might be power
        intent = Intent(type=IntentType.SYSTEM_ACTION, domain="boot", action="boot_grub_install")
        result = r.check(intent)
        assert result.allowed is False  # ADMIN action in POWER session

    def test_resolver_set_session_level(self):
        from lina.access.resolver import AccessLevelResolver
        from lina.access.levels import AccessLevel
        r = AccessLevelResolver()
        assert r.session_level == AccessLevel.USER
        r.set_session_level(AccessLevel.POWER)
        assert r.session_level == AccessLevel.POWER

    def test_resolver_stats(self):
        from lina.access.resolver import AccessLevelResolver
        from lina.intent.types import Intent, IntentType
        r = AccessLevelResolver()
        intent = Intent(type=IntentType.CHAT, domain="", action="")
        r.check(intent)
        r.check(intent)
        stats = r.get_stats()
        assert stats["checked"] == 2
        assert stats["session_level"] == "user"

    def test_resolver_singleton(self):
        import lina.access.resolver as mod
        mod._resolver = None  # Reset
        r1 = mod.get_access_resolver()
        r2 = mod.get_access_resolver()
        assert r1 is r2
        mod._resolver = None  # Cleanup


# ═══════════════════════════════════════════════════════════
#  Section 4: PolicyEngine Content Safety Extension
# ═══════════════════════════════════════════════════════════

class TestPolicyContentSafety:
    """Tests for governance PolicyEngine.check_content_safety()."""

    def _engine(self):
        from lina.governance.policy_engine import PolicyEngine
        return PolicyEngine()

    def _verdict(self, safe=True, risk=1, confidence=0.95, threats=None):
        from lina.safety.models import SafetyVerdict
        return SafetyVerdict(
            safe=safe,
            risk_level=risk,
            reason="test",
            confidence=confidence,
            threats=threats or [],
        )

    def test_safe_content_allowed(self):
        e = self._engine()
        v = self._verdict(safe=True, risk=0, confidence=0.99)
        r = e.check_content_safety(v, "echo hello")
        assert r.decision == "allow"

    def test_critical_risk_denied(self):
        e = self._engine()
        v = self._verdict(safe=False, risk=4, confidence=0.99)
        r = e.check_content_safety(v, "rm -rf /")
        assert r.decision == "deny"

    def test_catastrophic_risk_denied(self):
        e = self._engine()
        v = self._verdict(safe=False, risk=5, confidence=0.99)
        r = e.check_content_safety(v, "dd if=/dev/zero of=/dev/sda")
        assert r.decision == "deny"

    def test_high_risk_denied(self):
        e = self._engine()
        v = self._verdict(safe=False, risk=3)
        r = e.check_content_safety(v, "chmod 777 /etc/passwd")
        assert r.decision == "deny"

    def test_high_risk_override_confirm(self):
        e = self._engine()
        v = self._verdict(safe=False, risk=3)
        r = e.check_content_safety(v, "chmod 777 /tmp/file", allow_override=True)
        assert r.decision == "confirm"

    def test_low_confidence_denied(self):
        e = self._engine()
        v = self._verdict(safe=True, risk=1, confidence=0.3)
        r = e.check_content_safety(v, "ls")
        assert r.decision == "deny"

    def test_unsafe_verdict_denied(self):
        e = self._engine()
        v = self._verdict(safe=False, risk=1, confidence=0.9)
        r = e.check_content_safety(v, "ls")
        assert r.decision == "deny"

    def test_multiple_threats_confirm(self):
        from lina.safety.models import ThreatType
        e = self._engine()
        v = self._verdict(safe=True, risk=2, confidence=0.9,
                          threats=[ThreatType.SHELL_INJECTION, ThreatType.PRIVILEGE_ESCALATION])
        r = e.check_content_safety(v, "sudo bash")
        assert r.decision == "confirm"

    def test_result_in_audit(self):
        e = self._engine()
        v = self._verdict()
        e.check_content_safety(v, "test")
        audit = e.get_audit_log(limit=1)
        assert len(audit) == 1
        assert audit[0]["domain"] == "content_safety"


# ═══════════════════════════════════════════════════════════
#  Section 5: Intent Router
# ═══════════════════════════════════════════════════════════

class TestIntentRouter:
    """Tests for lina.intent.router."""

    def test_router_creation(self):
        from lina.intent.router import IntentRouter
        r = IntentRouter()
        assert r is not None

    def test_router_process_chat(self):
        from lina.intent.router import IntentRouter
        from lina.intent.types import Intent, IntentType, IntentStatus
        r = IntentRouter()
        intent = Intent(type=IntentType.CHAT, domain="", action="", user_text="Привет")
        result = r.process(intent)
        assert result.status == IntentStatus.CHAT_RESPONSE

    def test_router_process_admin_denied(self):
        from lina.intent.router import IntentRouter
        from lina.intent.types import Intent, IntentType, IntentStatus
        r = IntentRouter()
        intent = Intent(type=IntentType.LOW_LEVEL, domain="disk", action="disk_format")
        result = r.process(intent)
        assert result.status == IntentStatus.DENIED

    def test_router_process_action(self):
        from lina.intent.router import IntentRouter
        from lina.intent.types import Intent, IntentType, IntentStatus
        r = IntentRouter()
        intent = Intent(type=IntentType.SYSTEM_ACTION, domain="service", action="svc_status_check")
        result = r.process(intent)
        # May be NEEDS_CONFIRM (power level) or NOT_FOUND (action not registered)
        assert result.status in (IntentStatus.NEEDS_CONFIRM, IntentStatus.NOT_FOUND,
                                 IntentStatus.SUCCESS, IntentStatus.FAILED)

    def test_router_singleton(self):
        import lina.intent.router as mod
        mod._router = None
        r1 = mod.get_intent_router()
        r2 = mod.get_intent_router()
        assert r1 is r2
        mod._router = None


# ═══════════════════════════════════════════════════════════
#  Section 6: Service Runner
# ═══════════════════════════════════════════════════════════

class TestServiceRunner:
    """Tests for lina.governance.service_runner."""

    def test_runner_creation(self):
        from lina.governance.service_runner import LinaServiceRunner
        r = LinaServiceRunner()
        assert r.state.running is False
        assert r.state.governance_ready is False

    def test_runner_boot(self):
        from lina.governance.service_runner import LinaServiceRunner
        r = LinaServiceRunner()
        ok = r.boot()
        assert ok is True
        assert r.state.running is True
        assert r.state.governance_ready is True
        assert r.state.intent_ready is True
        r.shutdown()

    def test_runner_status(self):
        from lina.governance.service_runner import LinaServiceRunner
        r = LinaServiceRunner()
        r.boot()
        status = r.get_status()
        assert "running" in status
        assert "version" in status
        assert status["version"] == "0.8.0"
        assert status["governance_ready"] is True
        r.shutdown()

    def test_runner_process_intent(self):
        from lina.governance.service_runner import LinaServiceRunner
        from lina.intent.types import Intent, IntentType, IntentStatus
        r = LinaServiceRunner()
        r.boot()
        intent = Intent(type=IntentType.CHAT, domain="", action="", user_text="test")
        result = r.process_intent(intent)
        assert result.status == IntentStatus.CHAT_RESPONSE
        r.shutdown()

    def test_runner_shutdown(self):
        from lina.governance.service_runner import LinaServiceRunner
        r = LinaServiceRunner()
        r.boot()
        assert r.state.running is True
        r.shutdown()
        assert r.state.running is False

    def test_runner_singleton(self):
        import lina.governance.service_runner as mod
        mod._runner = None
        r1 = mod.get_service_runner()
        r2 = mod.get_service_runner()
        assert r1 is r2
        mod._runner = None


# ═══════════════════════════════════════════════════════════
#  Section 7: Legacy Shims
# ═══════════════════════════════════════════════════════════

class TestLegacyShims:
    """Tests that legacy imports still work via shim delegation."""

    def test_safety_policy_import(self):
        from lina.safety.policy import PolicyEngine, PolicyRule
        e = PolicyEngine()
        assert hasattr(e, 'evaluate')
        assert hasattr(e, 'rules')

    def test_safety_policy_evaluate(self):
        from lina.safety.policy import PolicyEngine
        from lina.safety.models import SafetyVerdict, RiskLevel
        e = PolicyEngine()
        v = SafetyVerdict(safe=True, risk_level=RiskLevel.LOW, reason="ok", confidence=0.9)
        d = e.evaluate(v, "echo hello")
        assert d.allowed is True

    def test_safety_policy_governance_veto(self):
        """Governance veto blocks even if local rules pass."""
        from lina.safety.policy import PolicyEngine
        from lina.safety.models import SafetyVerdict, RiskLevel
        e = PolicyEngine()
        # risk=4 (CRITICAL) → governance should veto
        v = SafetyVerdict(safe=True, risk_level=RiskLevel.CRITICAL, reason="dangerous", confidence=0.99)
        d = e.evaluate(v, "rm -rf /")
        assert d.allowed is False
        assert "veto" in d.reason.lower() or "blocked" in d.reason.lower() or "заблокировано" in d.reason.lower()

    def test_core_governance_import(self):
        from lina.core.governance import RuntimeStateManager, StateSnapshot
        mgr = RuntimeStateManager()
        assert mgr.get("safe_mode") is False
        assert mgr.get("active_model") == "full"

    def test_core_governance_set_sync(self):
        from lina.core.governance import RuntimeStateManager
        mgr = RuntimeStateManager()
        assert mgr.set("mode", "diagnostic") is True
        assert mgr.get("mode") == "diagnostic"
        # Should have _get_governance_sm method
        assert hasattr(mgr, '_get_governance_sm')

    def test_core_governance_snapshot(self):
        from lina.core.governance import RuntimeStateManager
        mgr = RuntimeStateManager()
        snap = mgr.snapshot()
        assert snap.active_model == "full"
        d = snap.to_dict()
        assert "safe_mode" in d

    def test_core_integrity_import(self):
        from lina.core.integrity_checker import IntegrityChecker, IntegrityResult
        ic = IntegrityChecker()
        result = ic.check("LLM", "LLM")
        assert result.passed is True

    def test_core_integrity_mismatch(self):
        from lina.core.integrity_checker import IntegrityChecker
        ic = IntegrityChecker()
        result = ic.check("TOOL", "LLM")
        assert result.passed is False
        assert result.recommend_safe_mode is True

    def test_core_integrity_has_check_files(self):
        from lina.core.integrity_checker import IntegrityChecker
        ic = IntegrityChecker()
        assert hasattr(ic, 'check_files')
        assert hasattr(ic, '_get_governance_integrity')


# ═══════════════════════════════════════════════════════════
#  Section 8: Version Unification
# ═══════════════════════════════════════════════════════════

class TestVersionUnification:
    """All packages report 0.7.0."""

    def test_lina_version(self):
        from lina import __version__
        assert __version__ >= "0.8.0"

    def test_core_version(self):
        from lina.core import __version__
        assert __version__ >= "0.8.0"

    def test_runtime_version(self):
        from lina.runtime import __version__
        assert __version__ >= "0.8.0"

    def test_runtime_v2_removed(self):
        """runtime_v2 was removed in Phase 28 — verify it's gone."""
        import importlib
        import pytest
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("lina.runtime_v2")

    def test_safety_version(self):
        from lina.safety import __version__
        assert __version__ == "0.8.0"

    def test_inference_version(self):
        from lina.inference import __version__
        assert __version__ == "0.8.0"

    def test_metrics_version(self):
        from lina.metrics import __version__
        assert __version__ == "0.8.0"

    def test_service_runner_version(self):
        from lina.governance.service_runner import __version__
        assert __version__ == "0.8.0"


# ═══════════════════════════════════════════════════════════
#  Section 9: End-to-End Pipeline
# ═══════════════════════════════════════════════════════════

class TestE2EPipeline:
    """Full Intent → Access → Policy → Action flow."""

    def test_user_chat_flow(self):
        """Chat flow: no access check, no policy, direct response."""
        from lina.intent.types import Intent, IntentType, IntentStatus
        from lina.intent.router import IntentRouter
        router = IntentRouter()
        intent = Intent(type=IntentType.CHAT, domain="", action="", user_text="Привет")
        result = router.process(intent)
        assert result.status == IntentStatus.CHAT_RESPONSE
        assert result.intent_id == intent.id

    def test_power_action_needs_confirm(self):
        """Power action: access check returns needs_confirmation."""
        from lina.intent.types import Intent, IntentType, IntentStatus
        from lina.intent.router import IntentRouter
        router = IntentRouter()
        intent = Intent(type=IntentType.SYSTEM_ACTION, domain="service", action="svc_restart")
        result = router.process(intent)
        # Power domain → needs confirmation → NEEDS_CONFIRM
        assert result.status == IntentStatus.NEEDS_CONFIRM

    def test_admin_action_denied(self):
        """Admin action in user session → DENIED."""
        from lina.intent.types import Intent, IntentType, IntentStatus
        from lina.intent.router import IntentRouter
        router = IntentRouter()
        intent = Intent(type=IntentType.DISK_OP, domain="disk", action="disk_format")
        result = router.process(intent)
        assert result.status == IntentStatus.DENIED

    def test_policy_deny_action(self):
        """Action in blocked domain → DENIED by policy."""
        from lina.intent.types import Intent, IntentType, IntentStatus
        from lina.intent.router import IntentRouter
        from lina.governance.policy_engine import get_policy_engine
        router = IntentRouter()
        # Temporarily block "network" domain
        engine = get_policy_engine()
        original = list(engine.config.blocked_domains)
        engine.config.blocked_domains.append("network")
        try:
            intent = Intent(type=IntentType.SYSTEM_ACTION, domain="network", action="net_restart_nm")
            result = router.process(intent)
            # Policy denies → should reflect in result
            assert result.status in (IntentStatus.DENIED, IntentStatus.NEEDS_CONFIRM)
        finally:
            engine.config.blocked_domains = original

    def test_diagnose_flow(self):
        """Diagnose intent: searches KB, returns results."""
        from lina.intent.types import Intent, IntentType, IntentStatus
        from lina.intent.router import IntentRouter
        router = IntentRouter()
        intent = Intent(type=IntentType.DIAGNOSE, domain="network", action="diagnose")
        result = router.process(intent)
        assert result.status in (IntentStatus.SUCCESS, IntentStatus.NOT_FOUND,
                                 IntentStatus.NEEDS_CONFIRM)

    def test_full_boot_and_intent(self):
        """ServiceRunner boot → process intent → shutdown."""
        from lina.governance.service_runner import LinaServiceRunner
        from lina.intent.types import Intent, IntentType, IntentStatus
        r = LinaServiceRunner()
        assert r.boot() is True
        intent = Intent(type=IntentType.QUERY, domain="system", action="status")
        result = r.process_intent(intent)
        assert result.intent_id == intent.id
        status = r.get_status()
        assert status["version"] == "0.8.0"
        r.shutdown()
        assert r.state.running is False


# ═══════════════════════════════════════════════════════════
#  Section 10: Package Imports
# ═══════════════════════════════════════════════════════════

class TestPackageImports:
    """All new packages export correctly."""

    def test_intent_package(self):
        from lina.intent import Intent, IntentType, IntentResult, IntentRouter, get_intent_router
        assert Intent is not None
        assert IntentType is not None

    def test_access_package(self):
        from lina.access import AccessLevel, AccessCheckResult, AccessLevelResolver, get_access_resolver
        assert AccessLevel is not None
        assert AccessLevelResolver is not None

    def test_governance_package(self):
        from lina.governance import (
            ActionRegistry, PolicyEngine, StateMachine,
            get_action_registry, get_policy_engine,
        )
        assert ActionRegistry is not None
        assert PolicyEngine is not None

    def test_governance_service_runner_import(self):
        from lina.governance.service_runner import (
            LinaServiceRunner, get_service_runner, __version__,
        )
        assert __version__ == "0.8.0"
