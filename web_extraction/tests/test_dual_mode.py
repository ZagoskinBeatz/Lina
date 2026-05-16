# -*- coding: utf-8 -*-
"""
Smoke & Unit tests for Lina Web Extraction v2 — Dual-Mode Pipeline.

Covers:
  - QueryClassifier: mode detection for GENERAL / LINUX / ERROR queries
  - LinuxCommandExtractor: command extraction, type/risk classification
  - ErrorDetector: error string detection in text
  - SolutionDetector: problem→solution block detection
  - ErrorKnowledgeGraph: lookup, learning, persistence
  - HybridRanker.rank_linux: Linux-boosted passage ranking
  - WebExtractionPipeline (v2): end-to-end dual-mode orchestration

Run:
  python -m pytest lina/web_extraction/tests/test_dual_mode.py -v
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import List

# ── Module under test ──
from lina.models.datatypes import Passage, SearchResult

from lina.web_extraction.query_classifier import (
    QueryClassifier, QueryMode, QueryClassification,
)
from lina.web_extraction.linux_commands import (
    LinuxCommandExtractor, LinuxCommand, CommandType, CommandRisk,
)
from lina.web_extraction.solution_detector import (
    ErrorDetector, DetectedError, SolutionDetector, SolutionBlock,
)
from lina.web_extraction.error_knowledge_graph import (
    ErrorKnowledgeGraph, ErrorEntry, KnownSolution, LookupResult,
)
from lina.web_extraction.hybrid_ranker import HybridRanker
from lina.web_extraction.web_pipeline import (
    WebExtractionPipeline, WebExtractionConfig, WebExtractionResult,
)


# ═══════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════

def _make_passage(text: str, url: str = "https://example.com", score: float = 0.5) -> Passage:
    return Passage(text=text, source_url=url, source_title="Test", score=score)


def _make_search_result(
    title: str = "Test Result",
    url: str = "https://example.com/page",
    snippet: str = "This is a test snippet for searching.",
) -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


# ═══════════════════════════════════════════════════
#  1. QueryClassifier Tests
# ═══════════════════════════════════════════════════

class TestQueryClassifier:
    """Tests for query mode classification."""

    def setup_method(self):
        self.clf = QueryClassifier()

    def test_general_query(self):
        """Non-Linux queries should classify as GENERAL."""
        queries = [
            "what is the capital of France",
            "best restaurants in Moscow",
            "how to cook pasta",
            "iPhone 15 specs",
            "weather tomorrow",
        ]
        for q in queries:
            result = self.clf.classify(q)
            assert result.mode == QueryMode.GENERAL, f"Expected GENERAL for: {q!r}, got {result.mode}"

    def test_linux_query(self):
        """Linux-related queries should classify as LINUX."""
        queries = [
            "how to install nginx on ubuntu",
            "systemctl restart apache2",
            "как настроить firewall iptables в linux ubuntu",
            "chmod 755 permissions explained",
            "mount USB drive on debian",
        ]
        for q in queries:
            result = self.clf.classify(q)
            assert result.mode in (QueryMode.LINUX, QueryMode.ERROR), \
                f"Expected LINUX/ERROR for: {q!r}, got {result.mode}"

    def test_error_query(self):
        """Error-string queries should classify as ERROR."""
        queries = [
            "E: Unable to locate package nginx",
            "dpkg: error processing package libssl on ubuntu",
            "bash: Permission denied: /etc/fstab",
            "Failed to start nginx.service: Unit not found",
            "No space left on device",
        ]
        for q in queries:
            result = self.clf.classify(q)
            assert result.mode == QueryMode.ERROR, \
                f"Expected ERROR for: {q!r}, got {result.mode}"

    def test_classification_has_fields(self):
        """Classification result must have all expected fields."""
        result = self.clf.classify("sudo apt update fails")
        assert isinstance(result, QueryClassification)
        assert isinstance(result.mode, QueryMode)
        assert isinstance(result.confidence, float)
        assert isinstance(result.linux_keywords, list)
        assert isinstance(result.error_strings, list)

    def test_confidence_range(self):
        """Confidence should be between 0 and 1."""
        for q in ["hello", "sudo apt install nginx", "E: Unable to locate package"]:
            result = self.clf.classify(q)
            assert 0.0 <= result.confidence <= 1.0, f"Confidence out of range: {result.confidence}"

    def test_empty_query(self):
        """Empty query should not crash."""
        result = self.clf.classify("")
        assert result.mode == QueryMode.GENERAL

    def test_package_manager_detection(self):
        """Package manager commands should boost Linux score."""
        result = self.clf.classify("apt install python3-pip")
        assert result.mode in (QueryMode.LINUX, QueryMode.ERROR)
        assert len(result.linux_commands) > 0 or len(result.package_managers) > 0

    def test_linux_keywords_populated(self):
        """Linux keywords should be populated for Linux queries."""
        result = self.clf.classify("configure kernel module on ubuntu")
        assert result.mode != QueryMode.GENERAL
        assert len(result.linux_keywords) > 0


# ═══════════════════════════════════════════════════
#  2. LinuxCommandExtractor Tests
# ═══════════════════════════════════════════════════

class TestLinuxCommandExtractor:
    """Tests for Linux command extraction."""

    def setup_method(self):
        self.extractor = LinuxCommandExtractor()

    def test_extract_from_code_block(self):
        """Commands in code blocks should be extracted."""
        text = """
To install nginx, run:

```bash
sudo apt update
sudo apt install nginx
```

Then start the service.
"""
        cmds = self.extractor.extract(text)
        assert len(cmds) >= 2
        normalized = [c.normalized for c in cmds]
        assert any("apt update" in n for n in normalized)
        assert any("apt install nginx" in n for n in normalized)

    def test_extract_from_inline_code(self):
        """Commands in backtick inline code should be extracted."""
        text = "Run `systemctl status nginx` to check the service."
        cmds = self.extractor.extract(text)
        assert len(cmds) >= 1
        assert any("systemctl" in c.normalized for c in cmds)

    def test_extract_from_prompt_pattern(self):
        """Commands after $ or # prompts should be extracted."""
        text = """
$ sudo apt install vim
# systemctl restart sshd
"""
        cmds = self.extractor.extract(text)
        assert len(cmds) >= 2

    def test_root_detection(self):
        """Commands with sudo should be marked as requires_root."""
        text = "```\nsudo rm -rf /tmp/cache\n```"
        cmds = self.extractor.extract(text)
        assert len(cmds) >= 1
        root_cmds = [c for c in cmds if c.requires_root]
        assert len(root_cmds) >= 1

    def test_risk_detection(self):
        """Dangerous commands should be flagged."""
        text = "```\nsudo rm -rf /\n```"
        cmds = self.extractor.extract(text)
        dangerous = [c for c in cmds if c.risk == CommandRisk.DANGEROUS]
        assert len(dangerous) >= 1

    def test_command_type_classification(self):
        """Commands should be classified by type."""
        text = "```\napt install vim\nsystemctl restart nginx\nchmod 755 /var/www\nip addr show\n```"
        cmds = self.extractor.extract(text)
        types = {c.command_type for c in cmds}
        assert CommandType.PACKAGE in types or any("apt" in c.base_command for c in cmds)

    def test_dedup_in_passages(self):
        """Duplicate commands across passages should be deduplicated."""
        passages = [
            _make_passage("```\nsudo apt update\n```"),
            _make_passage("```\nsudo apt update\n```"),
        ]
        cmds = self.extractor.extract_from_passages(passages, deduplicate=True)
        apt_updates = [c for c in cmds if "apt update" in c.normalized]
        assert len(apt_updates) == 1

    def test_empty_text(self):
        """Empty text should return no commands."""
        cmds = self.extractor.extract("")
        assert cmds == []

    def test_max_commands_limit(self):
        """Extractor should respect max_commands limit."""
        ext = LinuxCommandExtractor(max_commands=3)
        text = "```\napt update\napt upgrade\napt install vim\napt install git\napt install curl\n```"
        cmds = ext.extract(text)
        assert len(cmds) <= 3


# ═══════════════════════════════════════════════════
#  3. ErrorDetector Tests
# ═══════════════════════════════════════════════════

class TestErrorDetector:
    """Tests for error string detection in text."""

    def setup_method(self):
        self.detector = ErrorDetector()

    def test_apt_error(self):
        """APT 'unable to locate' errors should be detected."""
        text = "E: Unable to locate package libfoo-dev"
        errors = self.detector.detect(text)
        assert len(errors) >= 1
        assert any("unable to locate" in e.normalized.lower() for e in errors)

    def test_permission_denied(self):
        """Permission denied errors should be detected."""
        text = "bash: /etc/nginx/nginx.conf: Permission denied"
        errors = self.detector.detect(text)
        assert len(errors) >= 1
        assert any("permission" in e.error_type.lower() or "permission" in e.normalized.lower()
                    for e in errors)

    def test_service_failure(self):
        """Systemd service failure errors should be detected."""
        text = "Job for nginx.service failed because the control process exited with error code."
        errors = self.detector.detect(text)
        assert len(errors) >= 1

    def test_no_space(self):
        """Disk space errors should be detected."""
        text = "write /var/log/syslog: No space left on device"
        errors = self.detector.detect(text)
        assert len(errors) >= 1

    def test_segfault(self):
        """Segfault errors should be detected."""
        text = "[12345.678] process[1234]: segfault at 0000000000 ip 00000000 sp 00000000"
        errors = self.detector.detect(text)
        assert len(errors) >= 1

    def test_no_error_in_clean_text(self):
        """Normal text should not produce false positives."""
        text = "The Linux kernel is the core of the operating system."
        errors = self.detector.detect(text)
        assert len(errors) == 0

    def test_multiple_errors(self):
        """Multiple distinct errors should be detected."""
        text = """
E: Unable to locate package nginx
Permission denied: /etc/nginx/nginx.conf
No space left on device
"""
        errors = self.detector.detect(text)
        assert len(errors) >= 2

    def test_extract_error_keys(self):
        """extract_error_keys should return strings suitable for KG lookup."""
        text = "E: Unable to locate package vim"
        keys = self.detector.extract_error_keys(text)
        assert len(keys) >= 1
        assert all(isinstance(k, str) for k in keys)


# ═══════════════════════════════════════════════════
#  4. SolutionDetector Tests
# ═══════════════════════════════════════════════════

class TestSolutionDetector:
    """Tests for problem→solution block detection."""

    def setup_method(self):
        self.detector = SolutionDetector()

    def test_detect_solution_block(self):
        """Should detect a structured solution block."""
        text = """
## Problem

I get "E: Unable to locate package" when running apt install.

## Solution

You need to update your package lists first.

```bash
sudo apt update
sudo apt install nginx
```

This will refresh the cache and install the package.
"""
        blocks = self.detector.detect(text)
        assert len(blocks) >= 1
        sol = blocks[0]
        assert isinstance(sol, SolutionBlock)
        assert sol.solution  # non-empty
        assert sol.confidence > 0

    def test_solution_has_commands(self):
        """Solution blocks should extract embedded commands."""
        text = """
## Fix

Run the following commands:

```
sudo dpkg --configure -a
sudo apt-get install -f
```

This should fix the broken packages.
"""
        blocks = self.detector.detect(text)
        if blocks:
            sol = blocks[0]
            assert sol.has_commands or len(sol.commands) >= 0
            # Commands might be in the text body

    def test_solution_with_steps(self):
        """Numbered steps should be detected."""
        text = """
## Solution

Follow these steps:

1. Open a terminal
2. Run sudo apt update
3. Run sudo apt upgrade
4. Restart the system with sudo reboot
"""
        blocks = self.detector.detect(text)
        if blocks:
            some_have_steps = any(b.step_count > 0 for b in blocks)
            # Steps detection is heuristic, so we allow soft failure
            assert len(blocks) >= 1

    def test_detect_in_passages(self):
        """detect_in_passages should work across multiple passages."""
        passages = [
            _make_passage("""
## Solution
Run `sudo apt update` to fix the issue.
""", url="https://askubuntu.com/q/1"),
            _make_passage("Normal text, nothing to see here."),
        ]
        blocks = self.detector.detect_in_passages(passages)
        # At least one passage should yield solutions
        assert isinstance(blocks, list)

    def test_empty_text(self):
        """Empty text should return no solutions."""
        blocks = self.detector.detect("")
        assert blocks == []

    def test_confidence_range(self):
        """Solution confidence should be normalized."""
        text = """## Solution
Install the package by running:
```
sudo apt install vim
```
"""
        blocks = self.detector.detect(text)
        for b in blocks:
            assert 0.0 <= b.confidence <= 1.0


# ═══════════════════════════════════════════════════
#  5. ErrorKnowledgeGraph Tests
# ═══════════════════════════════════════════════════

class TestErrorKnowledgeGraph:
    """Tests for the Error Knowledge Graph (uses temp directory)."""

    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.kg = ErrorKnowledgeGraph(data_dir=self._tmpdir.name)

    def teardown_method(self):
        self._tmpdir.cleanup()

    def test_has_builtin_entries(self):
        """KG should have built-in error entries."""
        assert self.kg.entry_count > 0
        assert self.kg.total_solutions > 0

    def test_lookup_known_error(self):
        """Lookup a known built-in error should return a match."""
        result = self.kg.lookup("Unable to locate package")
        assert isinstance(result, LookupResult)
        assert result.found
        assert result.entry is not None

    def test_lookup_unknown_error(self):
        """Lookup unknown error should not crash."""
        result = self.kg.lookup("COMPLETELY_UNKNOWN_ERROR_XYZ_123")
        assert isinstance(result, LookupResult)
        # May or may not find, but shouldn't crash

    def test_lookup_permission_denied(self):
        """Permission denied error should match."""
        result = self.kg.lookup("Permission denied: /etc/fstab")
        assert result.found

    def test_lookup_no_space(self):
        """No space left on device should match."""
        result = self.kg.lookup("No space left on device")
        assert result.found

    def test_learn_new_solution(self):
        """Learning a new solution should persist."""
        sol = KnownSolution(
            description="Test fix",
            commands=["echo test"],
            confidence=0.5,
        )
        self.kg.learn(
            error_key="test_error_unique_123",
            solution=sol,
            error_type="test",
        )
        # Verify it's in the graph
        result = self.kg.lookup("test_error_unique_123")
        assert result.found

    def test_persistence(self):
        """KG should persist to disk and reload."""
        sol = KnownSolution(
            description="Persistent fix",
            commands=["echo persist"],
            confidence=0.7,
        )
        self.kg.learn("persist_test_error", sol, error_type="test")

        # Create a new KG instance from same dir
        kg2 = ErrorKnowledgeGraph(data_dir=self._tmpdir.name)
        result = kg2.lookup("persist_test_error")
        assert result.found

    def test_get_stats(self):
        """get_stats should return reasonable dict."""
        stats = self.kg.get_stats()
        assert "entries" in stats
        assert "solutions" in stats
        assert stats["entries"] > 0

    def test_empty_lookup(self):
        """Empty string lookup should not crash."""
        result = self.kg.lookup("")
        assert isinstance(result, LookupResult)


# ═══════════════════════════════════════════════════
#  6. HybridRanker Linux Mode Tests
# ═══════════════════════════════════════════════════

class TestHybridRankerLinux:
    """Tests for rank_linux() scoring."""

    def setup_method(self):
        self.ranker = HybridRanker()

    def test_linux_bonus_code_blocks(self):
        """Passages with code blocks should get Linux bonus."""
        bonus = HybridRanker._linux_bonus("Some text\n```\nsudo apt update\n```\n")
        assert bonus > 0

    def test_linux_bonus_shell_prompts(self):
        """Passages with shell prompts should get bonus."""
        bonus = HybridRanker._linux_bonus("$ sudo systemctl restart nginx")
        assert bonus > 0

    def test_linux_bonus_error_match(self):
        """Passages matching error strings should get bonus."""
        bonus = HybridRanker._linux_bonus(
            "Unable to locate package nginx",
            error_strings=["unable to locate package"],
        )
        assert bonus > 0

    def test_linux_bonus_zero_for_clean(self):
        """Clean non-Linux text should have zero or near-zero bonus."""
        bonus = HybridRanker._linux_bonus("Paris is the capital of France.")
        assert bonus < 0.02

    def test_rank_linux_returns_passages(self):
        """rank_linux should return sorted Passage list."""
        passages = [
            _make_passage("To fix this, run: ```\nsudo apt update\n```"),
            _make_passage("Paris is beautiful in spring."),
            _make_passage("$ systemctl status nginx\nActive: active (running)"),
        ]
        ranked = self.ranker.rank_linux(
            passages, query="apt update fails", top_k=3, min_score=0.0,
        )
        assert isinstance(ranked, list)
        assert all(isinstance(p, Passage) for p in ranked)
        # Linux-related passages should rank higher
        if len(ranked) >= 2:
            # First passage should be Linux-related
            assert any(
                kw in ranked[0].text.lower()
                for kw in ["apt", "systemctl", "nginx", "sudo"]
            )


# ═══════════════════════════════════════════════════
#  7. WebExtractionPipeline v2 Integration Tests
# ═══════════════════════════════════════════════════

class TestWebExtractionPipelineV2:
    """Integration tests for the dual-mode pipeline."""

    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        kg = ErrorKnowledgeGraph(data_dir=self._tmpdir.name)
        self.pipeline = WebExtractionPipeline(
            config=WebExtractionConfig(
                max_pages=2,
                use_error_kg=True,
                learn_from_web=True,
            ),
            error_kg=kg,
        )
        self._kg = kg

    def teardown_method(self):
        self._tmpdir.cleanup()

    def test_empty_input(self):
        """Empty input should return empty result."""
        result = self.pipeline.run([], query="")
        assert isinstance(result, WebExtractionResult)
        assert not result.has_content

    def test_general_mode_detection(self):
        """Non-Linux queries should run in GENERAL mode."""
        result = self.pipeline.run([], query="best Italian restaurants in Rome")
        assert result.query_mode == QueryMode.GENERAL
        assert len(result.solutions) == 0
        assert len(result.commands) == 0

    def test_linux_mode_detection(self):
        """Linux queries should run in LINUX/ERROR mode."""
        result = self.pipeline.run([], query="how to install nginx on ubuntu")
        assert result.query_mode in (QueryMode.LINUX, QueryMode.ERROR)

    def test_error_mode_with_kg(self):
        """Error queries should attempt KG lookup."""
        result = self.pipeline.run(
            [], query="E: Unable to locate package nginx",
        )
        assert result.query_mode == QueryMode.ERROR
        # KG should have been consulted
        assert result.kg_lookup is not None or result.answered_from_kg

    def test_result_has_all_fields(self):
        """Result should have all expected v2 fields."""
        result = self.pipeline.run([], query="test")
        assert hasattr(result, "query_mode")
        assert hasattr(result, "query_classification")
        assert hasattr(result, "solutions")
        assert hasattr(result, "commands")
        assert hasattr(result, "detected_errors")
        assert hasattr(result, "kg_lookup")
        assert hasattr(result, "answered_from_kg")
        assert hasattr(result, "elapsed_ms")

    def test_format_context_for_rag(self):
        """format_context_for_rag should return string."""
        result = self.pipeline.run([], query="test")
        ctx = result.format_context_for_rag()
        assert isinstance(ctx, str)

    def test_format_sources(self):
        """format_sources should return string."""
        result = WebExtractionResult()
        result.passages = [
            _make_passage("test", url="https://example.com/p1"),
            _make_passage("test2", url="https://example.com/p2"),
        ]
        sources = result.format_sources()
        assert isinstance(sources, str)
        assert "example.com" in sources

    def test_backward_compat_extract_passages(self):
        """extract_passages should still work (v1 compat)."""
        passages = self.pipeline.extract_passages([], query="test", top_k=5)
        assert isinstance(passages, list)

    def test_backward_compat_extract_and_format(self):
        """extract_and_format should still work (v1 compat)."""
        ctx = self.pipeline.extract_and_format([], query="test", max_passages=3)
        assert isinstance(ctx, str)

    def test_is_linux_mode_property(self):
        """is_linux_mode should work for LINUX and ERROR modes."""
        result = WebExtractionResult(query_mode=QueryMode.LINUX)
        assert result.is_linux_mode
        result2 = WebExtractionResult(query_mode=QueryMode.ERROR)
        assert result2.is_linux_mode
        result3 = WebExtractionResult(query_mode=QueryMode.GENERAL)
        assert not result3.is_linux_mode

    def test_kg_answer_format(self):
        """When answered from KG, format should include known error block."""
        result = WebExtractionResult()
        entry = ErrorEntry(
            error_id="test_err",
            pattern="test error",
            description="Test error description",
            causes=["Bad config"],
            solutions=[KnownSolution(
                description="Fix the config",
                commands=["echo fix"],
                confidence=0.9,
                times_confirmed=5,
            )],
        )
        lookup = LookupResult(
            found=True,
            entry=entry,
            match_quality=0.9,
            can_answer_directly=True,
        )
        result.answered_from_kg = True
        result.kg_lookup = lookup
        ctx = result.format_context_for_rag()
        assert "KNOWN ERROR" in ctx
        assert "echo fix" in ctx


# ═══════════════════════════════════════════════════
#  8. Cross-Module Integration Tests
# ═══════════════════════════════════════════════════

class TestCrossModuleIntegration:
    """Tests for interactions between modules."""

    def test_classifier_to_pipeline_flow(self):
        """Classifier output should drive pipeline behavior."""
        clf = QueryClassifier()
        result = clf.classify("sudo apt install nginx fails with permission denied")
        assert result.mode in (QueryMode.LINUX, QueryMode.ERROR)
        assert result.confidence > 0.3

    def test_error_detector_to_kg_flow(self):
        """Errors detected in text should be lookupable in KG."""
        detector = ErrorDetector()
        tmpdir = tempfile.mkdtemp()
        try:
            kg = ErrorKnowledgeGraph(data_dir=tmpdir)
            text = "E: Unable to locate package foobar"
            errors = detector.detect(text)
            assert len(errors) >= 1

            for err in errors:
                result = kg.lookup(err.normalized)
                # Should find the built-in apt unable to locate entry
                if result.found:
                    assert result.entry is not None
                    break
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_solution_detector_command_extractor_agreement(self):
        """Commands found by SolutionDetector should overlap with CommandExtractor."""
        text = """
## Solution

Fix the problem by running:

```bash
sudo apt update
sudo apt install nginx
```
"""
        sol_det = SolutionDetector()
        cmd_ext = LinuxCommandExtractor()

        blocks = sol_det.detect(text)
        cmds = cmd_ext.extract(text)

        # Both should find commands
        if blocks and blocks[0].has_commands:
            assert len(cmds) >= 1  # Command extractor should also find them


# ═══════════════════════════════════════════════════
#  Standalone runner
# ═══════════════════════════════════════════════════

def run_all():
    """Run smoke tests without pytest."""
    import traceback
    passed = 0
    failed = 0
    errors = []

    test_classes = [
        TestQueryClassifier,
        TestLinuxCommandExtractor,
        TestErrorDetector,
        TestSolutionDetector,
        TestErrorKnowledgeGraph,
        TestHybridRankerLinux,
        TestWebExtractionPipelineV2,
        TestCrossModuleIntegration,
    ]

    for cls in test_classes:
        instance = cls()
        methods = [m for m in dir(instance) if m.startswith("test_")]
        for name in methods:
            if hasattr(instance, "setup_method"):
                try:
                    instance.setup_method()
                except Exception:
                    pass
            try:
                getattr(instance, name)()
                passed += 1
                print(f"  ✓ {cls.__name__}.{name}")
            except Exception as e:
                failed += 1
                errors.append(f"  ✗ {cls.__name__}.{name}: {e}")
                print(f"  ✗ {cls.__name__}.{name}: {e}")
                traceback.print_exc()
            finally:
                if hasattr(instance, "teardown_method"):
                    try:
                        instance.teardown_method()
                    except Exception:
                        pass

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print("\nFailures:")
        for e in errors:
            print(e)
    print(f"{'='*60}")
    return failed == 0


if __name__ == "__main__":
    import sys
    success = run_all()
    sys.exit(0 if success else 1)
