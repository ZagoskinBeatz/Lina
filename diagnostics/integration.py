"""
Lina — Diagnostic Integration.

Связывает DiagnosticEngine с остальной системой:
  - API для core/main_pipeline.py
  - Кэш контекста
  - Форматирование для пользователя
  - LLM fallback enrichment
"""

from pathlib import Path
from typing import Dict, Optional, List

from lina.diagnostics.engine import DiagnosticEngine, DiagnosticReport


# Синглтон движка (инициализируется один раз)
_engine: Optional[DiagnosticEngine] = None


def get_engine() -> DiagnosticEngine:
    """Получить (или создать) экземпляр DiagnosticEngine."""
    global _engine
    if _engine is None:
        trees_dir = Path(__file__).parent / "trees"
        _engine = DiagnosticEngine(str(trees_dir) if trees_dir.exists() else None)
    return _engine


def diagnose(user_input: str) -> Dict:
    """
    Основной API для диагностики — вызывается из pipeline.

    Args:
        user_input: Текст пользователя (проблема).

    Returns:
        {
            matched: bool,
            tree_id: str,
            report: DiagnosticReport.to_dict(),
            formatted: str,  # текстовый отчёт
            needs_llm: bool,
            llm_prompt: str,  # если needs_llm=True
        }
    """
    engine = get_engine()
    tree_id = engine.match_problem(user_input)

    if tree_id:
        report = engine.run_diagnostic(tree_id)
        return {
            "matched": True,
            "tree_id": tree_id,
            "report": report.to_dict(),
            "formatted": report.format_text(),
            "needs_llm": not report.resolved or report.confidence < 0.5,
            "llm_prompt": "",
        }
    else:
        # Нет дерева — LLM fallback
        context = engine.collect_system_context()
        prompt = engine.build_llm_prompt(user_input, context)
        return {
            "matched": False,
            "tree_id": "",
            "report": {},
            "formatted": "",
            "needs_llm": True,
            "llm_prompt": prompt,
        }


def list_available_diagnostics(category: Optional[str] = None) -> List[Dict]:
    """Список доступных диагностик."""
    engine = get_engine()
    return engine.list_trees(category)


def get_categories() -> List[str]:
    """Список категорий диагностик."""
    engine = get_engine()
    return engine.get_categories()


def run_specific(tree_id: str) -> Dict:
    """Запустить конкретное дерево по ID."""
    engine = get_engine()
    report = engine.run_diagnostic(tree_id)
    return {
        "tree_id": tree_id,
        "report": report.to_dict(),
        "formatted": report.format_text(),
    }


def reset_engine():
    """Сбросить и пересоздать движок (для тестов)."""
    global _engine
    _engine = None
