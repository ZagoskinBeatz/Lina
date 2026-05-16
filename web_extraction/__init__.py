# -*- coding: utf-8 -*-
"""
Lina Web Extraction — Production-Grade Deterministic Pipeline (v2: Dual-Mode).

Architecture:
    URL → HTTP fetch → Content-Type detect → DOM parse → Boilerplate removal
    → Main content extraction → Text normalization → Semantic chunking
    → Hybrid ranking → Top-K selection → Fact extraction (LLM only here)

    v2 additions:
    → Query classification → mode routing (GENERAL / LINUX / ERROR)
    → Error Knowledge Graph lookup (ERROR mode)
    → Linux command extraction + solution detection (LINUX/ERROR mode)
    → Error KG learning from web results

Design principles:
  - Deterministic: identical input → identical output (no LLM until final stage)
  - Layered: each stage has single responsibility and can be tested independently
  - Parallel: pages download and process concurrently (ThreadPoolExecutor)
  - Fault-tolerant: graceful degradation at every stage
  - Zero hard dependencies: stdlib fallbacks for everything

Modules:
  - content_extractor: DOM-based main content extraction with text density analysis
  - semantic_chunker: Token-aware semantic chunking optimized for RAG
  - hybrid_ranker: BM25 + embedding two-stage ranking (+ Linux-boosted mode)
  - source_trust: Domain reputation and cross-source fact confidence
  - page_processor: Parallel page download + processing pipeline
  - web_pipeline: Top-level dual-mode orchestrator wiring all stages together
  - query_classifier: Deterministic query routing into GENERAL/LINUX/ERROR modes
  - linux_commands: Linux command extraction with type/risk classification
  - solution_detector: Problem→solution structure detection + error pattern matching
  - error_knowledge_graph: Persistent structured DB of Linux errors → solutions
"""

# ── Core modules (v1) ──
from lina.web_extraction.content_extractor import ContentExtractor
from lina.web_extraction.semantic_chunker import SemanticChunker
from lina.web_extraction.hybrid_ranker import HybridRanker
from lina.web_extraction.source_trust import SourceTrustScorer
from lina.web_extraction.page_processor import PageProcessor, PageResult
from lina.web_extraction.web_pipeline import (
    WebExtractionPipeline, WebExtractionConfig, WebExtractionResult,
)

# ── Linux dual-mode modules (v2) ──
from lina.web_extraction.query_classifier import (
    QueryClassifier, QueryMode, QueryClassification,
)
from lina.web_extraction.linux_commands import (
    LinuxCommandExtractor, LinuxCommand, CommandType, CommandRisk,
)
from lina.web_extraction.solution_detector import (
    SolutionDetector, SolutionBlock, ErrorDetector, DetectedError,
)
from lina.web_extraction.error_knowledge_graph import (
    ErrorKnowledgeGraph, ErrorEntry, KnownSolution, LookupResult,
)

__all__ = [
    # Core (v1)
    "ContentExtractor",
    "SemanticChunker",
    "HybridRanker",
    "SourceTrustScorer",
    "PageProcessor",
    "PageResult",
    "WebExtractionPipeline",
    "WebExtractionConfig",
    "WebExtractionResult",
    # Query classification (v2)
    "QueryClassifier",
    "QueryMode",
    "QueryClassification",
    # Linux commands (v2)
    "LinuxCommandExtractor",
    "LinuxCommand",
    "CommandType",
    "CommandRisk",
    # Solution detection (v2)
    "SolutionDetector",
    "SolutionBlock",
    "ErrorDetector",
    "DetectedError",
    # Error Knowledge Graph (v2)
    "ErrorKnowledgeGraph",
    "ErrorEntry",
    "KnownSolution",
    "LookupResult",
]
