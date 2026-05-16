# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Solution Detector.

Detects structured solution blocks in web page text: problem + solution + commands.

Many technical pages follow patterns:
  - StackOverflow: question text → answer text → code blocks
  - Forum posts: "I had the same problem" → "Fixed it by ..."
  - Wiki/docs: Symptom → Cause → Resolution
  - Tutorials: Step 1 → Step 2 → Step 3

This module detects these structures and extracts them as SolutionBlock
objects containing:
  - Problem description
  - Solution description
  - Associated Linux commands
  - Confidence score for "is this a real solution?"

Also includes error string detection — extracting error messages from text
for Error Knowledge Graph lookup and cross-source matching.

No LLM calls. Fully deterministic.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

logger = logging.getLogger("lina.web_extraction.solution_detector")


# ═══════════════════════════════════════════════════
#  Error Detection
# ═══════════════════════════════════════════════════

@dataclass
class DetectedError:
    """An error string found in text."""
    raw: str                  # Original error text as found
    normalized: str = ""      # Cleaned version for matching
    error_type: str = ""      # Category (pkg, permission, service, network, etc.)
    severity: str = "error"   # error / warning / fatal
    source_line: str = ""     # Full line where error was found

    @property
    def match_key(self) -> str:
        """Normalized key for Knowledge Graph lookup."""
        return self.normalized.lower().strip()


# ── Error extraction regexes ──
# Broader than QueryClassifier patterns — these scan full page text

_ERROR_EXTRACTION_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    # ── Package manager errors ──
    (re.compile(r'E:\s+(Unable to locate package\s+\S+)', re.I), "pkg_not_found", "error"),
    (re.compile(r'E:\s+(Unable to fetch some archives)', re.I), "pkg_fetch_fail", "error"),
    (re.compile(r'E:\s+(Unmet dependencies)', re.I), "unmet_deps", "error"),
    (re.compile(r'(dependency\s+\S+\s+is not satisfiable)', re.I), "dep_not_satisfiable", "error"),
    (re.compile(r'(dpkg was interrupted.*)', re.I), "dpkg_interrupted", "error"),
    (re.compile(r'(you might want to run.*apt.*--fix-broken)', re.I), "broken_install", "error"),
    (re.compile(r'error:\s+(target not found:\s+\S+)', re.I), "pacman_not_found", "error"),
    (re.compile(r'error:\s+(failed to commit transaction.*)', re.I), "pacman_transaction", "error"),
    (re.compile(r'(conflicting files.*)', re.I), "file_conflict", "error"),
    (re.compile(r'(No match for argument:\s+\S+)', re.I), "dnf_no_match", "error"),

    # ── Permission errors ──
    (re.compile(r'(\S+:\s+Permission denied)', re.I), "permission_denied", "error"),
    (re.compile(r'(Operation not permitted)', re.I), "not_permitted", "error"),
    (re.compile(r'(Authentication failure)', re.I), "auth_failure", "error"),
    (re.compile(r'(sudo:\s+.+:\s+command not found)', re.I), "sudo_cmd_not_found", "error"),

    # ── Systemd / service errors ──
    (re.compile(r'(Failed to start\s+.{5,80})', re.I), "service_start_fail", "error"),
    (re.compile(r'(Job for \S+ failed because.*)', re.I), "job_failed", "error"),
    (re.compile(r'(Unit \S+ could not be found)', re.I), "unit_not_found", "error"),
    (re.compile(r'(Unit \S+ is masked)', re.I), "unit_masked", "error"),
    (re.compile(r'(Main process exited, code=exited, status=\d+(?:/\w+)?)', re.I), "process_exited", "error"),
    (re.compile(r'(\S+\.service:\s+(?:Failed|Main|Control)\s+.{5,80})', re.I), "service_error", "error"),
    (re.compile(r'(Active:\s+failed\s+.*)', re.I), "service_active_failed", "error"),

    # ── Network errors ──
    (re.compile(r'(connect(?:ion)?\s+(?:refused|timed?\s*out|reset\s+by\s+peer))', re.I), "connection_error", "error"),
    (re.compile(r'(Network is unreachable)', re.I), "net_unreachable", "error"),
    (re.compile(r'(Name or service not known)', re.I), "dns_fail", "error"),
    (re.compile(r'(Could not resolve host\S*\s+\S+)', re.I), "dns_resolve", "error"),
    (re.compile(r'(No route to host)', re.I), "no_route", "error"),
    (re.compile(r'(Temporary failure in name resolution)', re.I), "dns_temp_fail", "error"),

    # ── Filesystem errors ──
    (re.compile(r'(No space left on device)', re.I), "no_space", "error"),
    (re.compile(r'(Read-only file system)', re.I), "readonly_fs", "error"),
    (re.compile(r'(Input/output error)', re.I), "io_error", "error"),
    (re.compile(r'(\S+:\s+No such file or directory)', re.I), "file_not_found", "error"),
    (re.compile(r'(Device or resource busy)', re.I), "device_busy", "error"),
    (re.compile(r'(Structure needs cleaning)', re.I), "fs_needs_cleaning", "error"),

    # ── Device / driver ──
    (re.compile(r'((?:device|hardware)\s+not found|no such device)', re.I), "device_not_found", "error"),
    (re.compile(r'(FATAL:\s+Module \S+ not found)', re.I), "module_not_found", "fatal"),
    (re.compile(r'(failed to (?:load|find)\s+(?:module|driver|firmware)\s+\S+)', re.I), "driver_fail", "error"),
    (re.compile(r'(firmware: failed to load\s+\S+)', re.I), "firmware_fail", "error"),

    # ── Kernel / boot ──
    (re.compile(r'(Kernel panic\s+-\s+not syncing.*)', re.I), "kernel_panic", "fatal"),
    (re.compile(r'(BUG:\s+(?:soft|hard)\s+lockup.*)', re.I), "lockup", "fatal"),
    (re.compile(r'(Out of memory:\s+Killed process\s+\d+)', re.I), "oom_killer", "fatal"),
    (re.compile(r'((?:Oops|segfault)\s+at\s+\S+)', re.I), "segfault", "fatal"),
    (re.compile(r'(general protection fault.*)', re.I), "gpf", "fatal"),

    # ── X11 / display ──
    (re.compile(r'(Cannot open display\S*\s*\S*)', re.I), "no_display", "error"),
    (re.compile(r'((?:EE|Fatal)\s+server error.*)', re.I), "xorg_fatal", "fatal"),
    (re.compile(r'(Xlib:\s+extension\s+\S+\s+missing)', re.I), "x11_ext_missing", "error"),

    # ── Generic ──
    (re.compile(r'(command not found:\s+\S+)', re.I), "cmd_not_found", "error"),
    (re.compile(r'(bash:\s+\S+:\s+command not found)', re.I), "bash_cmd_not_found", "error"),
    (re.compile(r'(syntax error near unexpected token)', re.I), "syntax_error", "error"),
    (re.compile(r'(Segmentation fault\s*(?:\(core dumped\))?)', re.I), "segfault_core", "fatal"),
]


class ErrorDetector:
    """
    Extract error strings from text (page content or user query).

    Scans text for known Linux error patterns and returns structured
    DetectedError objects suitable for Error Knowledge Graph lookup.

    Usage:
        detector = ErrorDetector()
        errors = detector.detect("E: Unable to locate package nginx-extras")
        # errors[0].error_type == "pkg_not_found"
        # errors[0].normalized == "unable to locate package nginx-extras"
    """

    def detect(self, text: str) -> List[DetectedError]:
        """
        Detect error strings in text.

        Args:
            text: Any text (query, page content, log output).

        Returns:
            List of DetectedError objects found.
        """
        if not text:
            return []

        errors: List[DetectedError] = []
        seen_normalized: Set[str] = set()

        for pattern, error_type, severity in _ERROR_EXTRACTION_PATTERNS:
            for match in pattern.finditer(text):
                raw = match.group(1) if match.lastindex else match.group(0)
                raw = raw.strip()
                normalized = re.sub(r'\s+', ' ', raw.lower()).strip()

                if normalized in seen_normalized:
                    continue
                seen_normalized.add(normalized)

                # Get the full line for context
                start = text.rfind('\n', 0, match.start()) + 1
                end = text.find('\n', match.end())
                if end == -1:
                    end = min(match.end() + 200, len(text))
                source_line = text[start:end].strip()

                errors.append(DetectedError(
                    raw=raw,
                    normalized=normalized,
                    error_type=error_type,
                    severity=severity,
                    source_line=source_line,
                ))

        return errors

    def extract_error_keys(self, text: str) -> List[str]:
        """
        Extract normalized error keys suitable for Knowledge Graph lookup.

        Returns:
            List of unique normalized error strings.
        """
        errors = self.detect(text)
        return list(dict.fromkeys(e.normalized for e in errors))


# ═══════════════════════════════════════════════════
#  Solution Block Detection
# ═══════════════════════════════════════════════════

@dataclass
class SolutionBlock:
    """A detected problem → solution block, extracted from page text."""
    problem: str = ""             # Problem description / error
    solution: str = ""            # Solution description
    commands: List[str] = field(default_factory=list)  # Associated commands
    steps: List[str] = field(default_factory=list)     # Ordered steps
    confidence: float = 0.0       # How confident we are this is a real solution
    source_url: str = ""
    source_title: str = ""
    position_in_document: int = 0 # Character offset

    @property
    def has_commands(self) -> bool:
        return bool(self.commands)

    @property
    def step_count(self) -> int:
        return len(self.steps)


# ── Solution signal patterns ──
_SOLUTION_START_PATTERNS: List[re.Pattern] = [
    # English
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:solution|fix|resolution|workaround|how to fix|to fix|to solve|to resolve)\s*[:\-]?\s*', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:the (?:fix|solution|answer|workaround) (?:is|was))\s*[:\-]?\s*', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:i (?:fixed|solved|resolved) (?:it|this) by)\s', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:this (?:can be|is) (?:fixed|solved|resolved) by)\s', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:steps?\s+to\s+(?:fix|solve|resolve|reproduce))\s*[:\-]?\s*', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:try (?:running|executing|the following|this))\s', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:you (?:can|need to|should|must|might)\s+(?:run|try|execute|install|use))\s', re.I),

    # Russian
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:решение|исправление|способ|как (?:исправить|решить|починить|устранить))\s*[:\-]?\s*', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:я (?:исправил|решил|починил)\s+(?:это|проблему))\s', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:нужно|необходимо|попробуйте|выполните|запустите)\s', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:шаги?\s+(?:для|по)\s+(?:решени|исправлени|устранени))', re.I),
]

_PROBLEM_START_PATTERNS: List[re.Pattern] = [
    # English
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:problem|issue|error|bug|symptom)\s*[:\-]?\s*', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:i (?:have|had|get|got|am (?:getting|having))\s+(?:a |an |the )?(?:problem|issue|error))', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:when i (?:run|try|execute|install|start|use))\s', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:i(?:\'m| am) (?:getting|seeing|having|experiencing))\s', re.I),

    # Russian
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:проблема|ошибка|баг|сбой)\s*[:\-]?\s*', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:при (?:запуске|установке|обновлении|использовании))\s', re.I),
    re.compile(r'(?:^|\n)\s*(?:#{1,4}\s+)?(?:у меня (?:возникла|появилась|ошибка))\s', re.I),
]

# Step patterns (numbered lists, bullet points)
_STEP_PATTERN = re.compile(
    r'(?:^|\n)\s*(?:(\d+)[.)]\s+|[-•*]\s+|Step\s+\d+\s*[:.]\s+|Шаг\s+\d+\s*[:.]\s+)(.+)',
    re.I | re.MULTILINE,
)

# Command extraction inside solution blocks (simplified)
_CODE_IN_SOLUTION = re.compile(r'```(?:bash|sh|shell|console)?\s*\n(.*?)```', re.DOTALL)
_INLINE_CMD = re.compile(r'`([^`]{4,120})`')
_PROMPT_CMD = re.compile(r'^\s*[$#>]\s+(.+)$', re.MULTILINE)


class SolutionDetector:
    """
    Detect structured solution blocks in web page text.

    Identifies patterns:
      - problem → solution → commands
      - numbered steps
      - "To fix this, run..."
      - StackOverflow-style answers

    No LLM calls. Fully deterministic.

    Usage:
        detector = SolutionDetector()
        blocks = detector.detect(page_text)
        if blocks:
            top = blocks[0]  # Highest confidence solution
            print(f"Problem: {top.problem}")
            print(f"Solution: {top.solution}")
            for cmd in top.commands:
                print(f"  $ {cmd}")
    """

    def __init__(
        self,
        min_solution_length: int = 30,
        max_blocks: int = 10,
    ):
        self._min_solution_length = min_solution_length
        self._max_blocks = max_blocks

    def detect(
        self,
        text: str,
        source_url: str = "",
        source_title: str = "",
    ) -> List[SolutionBlock]:
        """
        Detect solution blocks in text.

        Algorithm:
          1. Find solution-start markers
          2. For each marker, extract problem (text before) + solution (text after)
          3. Extract commands and steps from solution
          4. Score each block by confidence
          5. Return sorted by confidence

        Args:
            text: Page text (output of ContentExtractor).
            source_url: Source URL for provenance.
            source_title: Source title.

        Returns:
            List of SolutionBlock objects, sorted by confidence (highest first).
        """
        if not text or len(text) < self._min_solution_length:
            return []

        blocks: List[SolutionBlock] = []

        # Strategy 1: Find explicit solution markers
        for pattern in _SOLUTION_START_PATTERNS:
            for match in pattern.finditer(text):
                block = self._extract_block_at(text, match.start(), match.end())
                if block:
                    block.source_url = source_url
                    block.source_title = source_title
                    block.position_in_document = match.start()
                    blocks.append(block)

        # Strategy 2: Detect step-by-step instructions
        step_block = self._detect_step_sequence(text)
        if step_block and step_block.confidence > 0.30:
            step_block.source_url = source_url
            step_block.source_title = source_title
            blocks.append(step_block)

        # Deduplicate overlapping blocks
        blocks = self._deduplicate_blocks(blocks)

        # Score and sort
        for block in blocks:
            block.confidence = self._score_block(block)
        blocks.sort(key=lambda b: b.confidence, reverse=True)

        return blocks[:self._max_blocks]

    def detect_in_passages(
        self,
        passages: list,
    ) -> List[SolutionBlock]:
        """
        Detect solution blocks across multiple passages.

        Args:
            passages: List of Passage objects.

        Returns:
            All detected solution blocks from all passages.
        """
        all_blocks: List[SolutionBlock] = []
        for passage in passages:
            text = passage.text if hasattr(passage, 'text') else str(passage)
            url = passage.source_url if hasattr(passage, 'source_url') else ""
            title = passage.source_title if hasattr(passage, 'source_title') else ""
            blocks = self.detect(text, source_url=url, source_title=title)
            all_blocks.extend(blocks)

        all_blocks.sort(key=lambda b: b.confidence, reverse=True)
        return all_blocks

    # ═══════════════════════════════════════════════
    #  Block Extraction
    # ═══════════════════════════════════════════════

    def _extract_block_at(
        self,
        text: str,
        marker_start: int,
        marker_end: int,
    ) -> Optional[SolutionBlock]:
        """Extract a solution block around a found marker."""
        # Get problem text: look backwards for problem markers or use
        # the preceding paragraph
        problem_text = self._extract_problem_before(text, marker_start)

        # Get solution text: from marker to next heading/section or end
        solution_text = self._extract_solution_after(text, marker_end)

        if not solution_text or len(solution_text.strip()) < self._min_solution_length:
            return None

        # Extract commands from solution text
        commands = self._extract_commands_from_text(solution_text)

        # Extract steps
        steps = self._extract_steps(solution_text)

        return SolutionBlock(
            problem=problem_text,
            solution=solution_text.strip(),
            commands=commands,
            steps=steps,
        )

    def _extract_problem_before(self, text: str, position: int) -> str:
        """Extract problem description before a solution marker."""
        # Look backwards up to 1000 chars
        start = max(0, position - 1000)
        before = text[start:position]

        # Try to find an explicit problem marker
        last_problem_pos = -1
        for pattern in _PROBLEM_START_PATTERNS:
            for match in pattern.finditer(before):
                if match.start() > last_problem_pos:
                    last_problem_pos = match.start()

        if last_problem_pos >= 0:
            problem = before[last_problem_pos:].strip()
        else:
            # Take the last paragraph before the solution marker
            paragraphs = before.rsplit('\n\n', 2)
            problem = paragraphs[-1].strip() if paragraphs else ""

        # Trim to reasonable length
        if len(problem) > 500:
            problem = problem[:500].rsplit('.', 1)[0] + '.'

        return problem

    def _extract_solution_after(self, text: str, position: int) -> str:
        """Extract solution text after a marker."""
        remaining = text[position:]

        # Find the end of this solution block:
        # - next heading (## / ###)
        # - next solution marker
        # - large gap
        end_patterns = [
            re.compile(r'\n#{1,4}\s+\w', re.M),
            re.compile(r'\n\s*---\s*\n'),
            re.compile(r'\n(?:Related|See also|Share|Comments?|Ответить|Поделиться)\s*[\n:]', re.I),
        ]

        end_pos = len(remaining)
        for ep in end_patterns:
            match = ep.search(remaining)
            if match and match.start() > 50:  # Min 50 chars
                end_pos = min(end_pos, match.start())

        # Also cap at 2000 chars
        end_pos = min(end_pos, 2000)

        return remaining[:end_pos]

    def _detect_step_sequence(self, text: str) -> Optional[SolutionBlock]:
        """Detect a numbered step sequence."""
        steps: List[str] = []
        commands: List[str] = []

        for match in _STEP_PATTERN.finditer(text):
            step_text = match.group(2).strip() if match.group(2) else match.group(0).strip()
            if len(step_text) > 10:
                steps.append(step_text)
                # Check if step contains a command
                inline_cmds = _INLINE_CMD.findall(step_text)
                commands.extend(inline_cmds)

        if len(steps) < 2:
            return None

        # Build a solution block from steps
        solution = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))

        # Also extract commands from code blocks near the steps
        block_cmds = self._extract_commands_from_text(text)
        commands.extend(block_cmds)
        commands = list(dict.fromkeys(commands))  # Deduplicate preserving order

        return SolutionBlock(
            problem="",
            solution=solution,
            commands=commands,
            steps=steps,
            confidence=0.40 + 0.05 * min(len(steps), 6),
        )

    # ═══════════════════════════════════════════════
    #  Command Extraction from Solution Text
    # ═══════════════════════════════════════════════

    def _extract_commands_from_text(self, text: str) -> List[str]:
        """Extract commands from solution text (code blocks, inline, prompts)."""
        commands: List[str] = []
        seen: Set[str] = set()

        # Code blocks
        for match in _CODE_IN_SOLUTION.finditer(text):
            block = match.group(1).strip()
            for line in block.split('\n'):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # Remove prompt
                line = re.sub(r'^[$#>]\s+', '', line)
                if line and line not in seen:
                    seen.add(line)
                    commands.append(line)

        # Prompt lines
        for match in _PROMPT_CMD.finditer(text):
            cmd = match.group(1).strip()
            if cmd and cmd not in seen:
                seen.add(cmd)
                commands.append(cmd)

        # Inline commands (only if they look like actual commands)
        for match in _INLINE_CMD.finditer(text):
            inline = match.group(1).strip()
            first_word = inline.split()[0] if inline.split() else ""
            # Simple check: does it start with a known command or sudo?
            if first_word in ('sudo', 'apt', 'apt-get', 'pacman', 'dnf', 'yum',
                              'systemctl', 'journalctl', 'service', 'chmod',
                              'chown', 'mount', 'umount', 'modprobe',
                              'iptables', 'ufw', 'nmcli', 'ip',
                              'kill', 'pkill', 'useradd', 'passwd',
                              'grub-install', 'update-grub', 'mkinitcpio',
                              'sed', 'tee', 'echo', 'cat', 'grep', 'find',
                              'make', 'cmake', 'git', 'wget', 'curl',
                              'pip', 'pip3', 'npm', 'cargo'):
                if inline not in seen:
                    seen.add(inline)
                    commands.append(inline)

        return commands

    def _extract_steps(self, text: str) -> List[str]:
        """Extract ordered steps from text."""
        steps: List[str] = []
        for match in _STEP_PATTERN.finditer(text):
            step_text = match.group(2).strip() if match.group(2) else match.group(0).strip()
            if len(step_text) > 5:
                steps.append(step_text)
        return steps

    # ═══════════════════════════════════════════════
    #  Scoring
    # ═══════════════════════════════════════════════

    def _score_block(self, block: SolutionBlock) -> float:
        """Score a solution block's confidence."""
        score = 0.30  # Base

        # Has commands → more likely a real solution
        if block.commands:
            score += 0.15 + 0.05 * min(len(block.commands), 5)

        # Has steps → structured solution
        if block.steps:
            score += 0.10 + 0.03 * min(len(block.steps), 5)

        # Problem described → complete answer
        if block.problem and len(block.problem) > 20:
            score += 0.10

        # Solution length — too short = suspicious, sweet spot = 100-500 chars
        sol_len = len(block.solution)
        if 100 <= sol_len <= 500:
            score += 0.10
        elif 500 < sol_len <= 1500:
            score += 0.05

        return min(score, 0.99)

    def _deduplicate_blocks(self, blocks: List[SolutionBlock]) -> List[SolutionBlock]:
        """Remove overlapping solution blocks (keep highest-scoring)."""
        if len(blocks) <= 1:
            return blocks

        # Sort by position
        blocks.sort(key=lambda b: b.position_in_document)

        result: List[SolutionBlock] = [blocks[0]]
        for block in blocks[1:]:
            prev = result[-1]
            # Check overlap: if positions are within 200 chars, merge
            if abs(block.position_in_document - prev.position_in_document) < 200:
                # Keep the one with more commands/steps
                if len(block.commands) + len(block.steps) > len(prev.commands) + len(prev.steps):
                    result[-1] = block
            else:
                result.append(block)

        return result


# ═══════════════════════════════════════════════════
#  Singletons
# ═══════════════════════════════════════════════════

_error_detector: ErrorDetector | None = None
_solution_detector: SolutionDetector | None = None


def get_error_detector() -> ErrorDetector:
    global _error_detector
    if _error_detector is None:
        _error_detector = ErrorDetector()
    return _error_detector


def get_solution_detector() -> SolutionDetector:
    global _solution_detector
    if _solution_detector is None:
        _solution_detector = SolutionDetector()
    return _solution_detector
