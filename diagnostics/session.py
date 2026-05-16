"""
Lina — Interactive Diagnostic Session Controller.

Обеспечивает интерактивный пошаговый режим диагностики:
  - Пользователь описывает проблему
  - Система сопоставляет с деревом решений
  - Пошаговые проверки с визуальным прогрессом
  - На каждом шаге — статус, объяснение, предложения
  - Финальный отчёт с диагнозом и решением

Архитектура:
  DiagnosticSession оборачивает DiagnosticEngine,
  добавляя управление состоянием сессии, форматирование
  вывода и возможность пошагового исполнения.

Модуль: v0.8.0  — ключевой компонент интерактивной диагностики.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from lina.diagnostics.engine import DiagnosticEngine, DiagnosticReport, StepResult

logger = logging.getLogger("lina.diagnostics.session")


# ─── Session states ──────────────────────────────────────────────────────────

class SessionState(Enum):
    """Состояние интерактивной сессии диагностики."""
    IDLE = "idle"                # Сессия создана, диагностика не начата
    MATCHING = "matching"        # Поиск подходящего дерева
    RUNNING = "running"          # Выполнение шагов
    AWAITING_INPUT = "awaiting"  # Ожидание пользовательского ввода
    COMPLETED = "completed"      # Диагностика завершена
    FAILED = "failed"            # Ошибка
    CANCELLED = "cancelled"      # Отменена пользователем


# ─── Step snapshot ───────────────────────────────────────────────────────────

@dataclass
class StepSnapshot:
    """Снимок текущего шага для отображения пользователю."""
    step_number: int
    total_steps: int
    step_id: str
    description: str
    command: str
    output: str
    matched: bool
    diagnosis: str = ""
    solution: str = ""
    explanation: str = ""
    severity: str = ""
    is_final: bool = False

    def format_text(self) -> str:
        """Форматированный вывод одного шага."""
        icon = "✅" if self.matched else "❌"
        progress = f"[{self.step_number}/{self.total_steps}]"
        lines = [f"  {icon} {progress} {self.description}"]

        if self.command:
            lines.append(f"     $ {self.command}")

        if self.output and len(self.output.strip()) > 0:
            # Ограничиваем показ вывода
            out_preview = self.output.strip()
            if len(out_preview) > 200:
                out_preview = out_preview[:200] + "..."
            lines.append(f"     → {out_preview}")

        if self.diagnosis:
            sev_icon = {
                "critical": "🔴", "high": "🟠",
                "medium": "🟡", "low": "🟢", "info": "ℹ️",
            }.get(self.severity, "❓")
            lines.append(f"     {sev_icon} {self.diagnosis}")

        if self.solution:
            lines.append(f"     💡 {self.solution}")

        return "\n".join(lines)


# ─── Interactive Session ─────────────────────────────────────────────────────

class DiagnosticSession:
    """
    Контроллер интерактивной диагностической сессии.

    Пример использования (CLI):
        session = DiagnosticSession()
        result = session.start("нет интернета")
        # result содержит шаги сессии + финальный отчёт

    Пример использования (пошаговый):
        session = DiagnosticSession()
        session.begin("нет интернета")
        while session.state == SessionState.RUNNING:
            snapshot = session.step_forward()
            print(snapshot.format_text())
        report = session.get_report()
    """

    def __init__(self, engine: Optional[DiagnosticEngine] = None):
        """
        Args:
            engine: DiagnosticEngine (None = создаётся с default trees).
        """
        self._engine = engine or DiagnosticEngine()
        self._state = SessionState.IDLE
        self._tree_id: Optional[str] = None
        self._tree: Optional[Dict] = None
        self._step_map: Dict[str, Dict] = {}
        self._steps: List[Dict] = []
        self._current_step_idx: int = 0
        self._current_step: Optional[Dict] = None
        self._visited: set = set()
        self._step_results: List[StepResult] = []
        self._step_snapshots: List[StepSnapshot] = []
        self._report: Optional[DiagnosticReport] = None
        self._problem_text: str = ""
        self._start_time: float = 0.0
        self._alternatives: List[str] = []

    # ── Properties ──

    @property
    def state(self) -> SessionState:
        """Текущее состояние сессии."""
        return self._state

    @property
    def tree_id(self) -> Optional[str]:
        """ID текущего дерева."""
        return self._tree_id

    @property
    def progress(self) -> float:
        """Прогресс (0.0 — 1.0)."""
        if not self._steps:
            return 0.0
        if self._state == SessionState.COMPLETED:
            return 1.0
        return min(len(self._step_results) / max(len(self._steps), 1), 1.0)

    @property
    def steps_completed(self) -> int:
        """Количество завершённых шагов."""
        return len(self._step_results)

    @property
    def total_steps(self) -> int:
        """Общее количество шагов в дереве."""
        return len(self._steps)

    # ── Start / Begin ──

    def start(self, problem_text: str) -> DiagnosticReport:
        """
        Запускает полный цикл диагностики (неинтерактивный).

        Выполняет все шаги автоматически и возвращает отчёт.

        Args:
            problem_text: Описание проблемы от пользователя.

        Returns:
            DiagnosticReport с результатами.
        """
        self.begin(problem_text)

        if self._state == SessionState.FAILED:
            return self._make_fallback_report(problem_text)

        while self._state == SessionState.RUNNING:
            self.step_forward()

        return self.get_report()

    def begin(self, problem_text: str) -> bool:
        """
        Начинает диагностическую сессию (для пошагового режима).

        Сопоставляет проблему с деревом и подготавливает шаги.

        Args:
            problem_text: Описание проблемы.

        Returns:
            True если дерево найдено и сессия запущена.
        """
        self._problem_text = problem_text.strip()
        self._start_time = time.time()
        self._state = SessionState.MATCHING

        logger.info("Diagnostic session begun: %s", self._problem_text[:80])

        # Поиск дерева
        self._tree_id = self._engine.match_problem(self._problem_text)

        if not self._tree_id:
            logger.info("No tree matched for: %s", self._problem_text[:80])
            self._state = SessionState.FAILED
            self._alternatives = self._suggest_alternatives(self._problem_text)
            return False

        self._tree = self._engine.get_tree(self._tree_id)
        if not self._tree:
            self._state = SessionState.FAILED
            return False

        # Подготовка шагов
        self._steps = self._tree.get("steps", [])
        self._step_map = {}
        for s in self._steps:
            sid = s.get("id", "")
            if sid:
                self._step_map[sid] = s

        if not self._steps:
            self._state = SessionState.FAILED
            return False

        self._current_step = self._steps[0]
        self._current_step_idx = 0
        self._visited = set()
        self._step_results = []
        self._step_snapshots = []
        self._state = SessionState.RUNNING

        logger.info("Session started: tree=%s steps=%d",
                     self._tree_id, len(self._steps))
        return True

    # ── Step execution ──

    def step_forward(self) -> Optional[StepSnapshot]:
        """
        Выполняет один шаг диагностики.

        Returns:
            StepSnapshot с результатами шага, или None если сессия завершена.
        """
        if self._state != SessionState.RUNNING:
            return None

        if self._current_step is None:
            self._finalize()
            return None

        step = self._current_step
        step_id = step.get("id", f"step_{len(self._step_results)}")

        # Защита от циклов
        if step_id in self._visited or len(self._step_results) >= 30:
            self._finalize()
            return None

        self._visited.add(step_id)

        # Выполняем проверку
        command = step.get("check", "")
        output = self._engine._execute_check(command) if command else ""

        # Проверяем паттерн
        parse_pattern = step.get("parse", "")
        matched = (self._engine._check_pattern(output, parse_pattern)
                   if parse_pattern else bool(output.strip()))

        # StepResult
        step_result = StepResult(
            step_id=step_id,
            description=step.get("description", step_id),
            command=command,
            output=output[:500],
            matched=matched,
        )

        # Branching
        branch = step.get("if_match") if matched else step.get("if_no_match")

        diagnosis = ""
        solution = ""
        explanation = ""
        severity = ""
        is_final = False

        if branch:
            step_result.diagnosis = branch.get("diagnosis", "")
            step_result.solution = branch.get("solution", "")
            step_result.explanation = branch.get("explanation", "")
            step_result.severity = branch.get("severity", "")
            step_result.requires_root = branch.get("requires_root", False)

            diagnosis = step_result.diagnosis
            solution = step_result.solution
            explanation = step_result.explanation
            severity = step_result.severity

            next_id = branch.get("next")

            self._step_results.append(step_result)

            if next_id is None:
                # Конец цепочки
                is_final = True
                self._current_step = None
            else:
                next_step = self._step_map.get(next_id)
                if next_step:
                    self._current_step = next_step
                else:
                    is_final = True
                    self._current_step = None
        else:
            self._step_results.append(step_result)
            # Переход к следующему по порядку
            idx = (self._steps.index(step)
                   if step in self._steps else -1)
            if idx >= 0 and idx + 1 < len(self._steps):
                self._current_step = self._steps[idx + 1]
            else:
                is_final = True
                self._current_step = None

        # Snapshot
        snapshot = StepSnapshot(
            step_number=len(self._step_results),
            total_steps=len(self._steps),
            step_id=step_id,
            description=step.get("description", step_id),
            command=command,
            output=output[:500],
            matched=matched,
            diagnosis=diagnosis,
            solution=solution,
            explanation=explanation,
            severity=severity,
            is_final=is_final,
        )
        self._step_snapshots.append(snapshot)

        if is_final:
            self._finalize()

        return snapshot

    # ── Finalization ──

    def _finalize(self) -> None:
        """Завершает сессию и формирует отчёт."""
        if self._state == SessionState.COMPLETED:
            return

        self._state = SessionState.COMPLETED
        duration_ms = int((time.time() - self._start_time) * 1000)

        # Собираем финальный диагноз из последнего шага с diagnosis
        final_diagnosis = ""
        final_solution = ""
        final_explanation = ""
        final_severity = "info"
        requires_root = False

        for sr in reversed(self._step_results):
            if sr.diagnosis:
                final_diagnosis = sr.diagnosis
                final_solution = sr.solution or final_solution
                final_explanation = sr.explanation or final_explanation
                final_severity = sr.severity or final_severity
                requires_root = sr.requires_root or requires_root
                break

        self._report = DiagnosticReport(
            tree_id=self._tree_id or "__unknown__",
            tree_name=(self._tree or {}).get("name", ""),
            category=(self._tree or {}).get("category", ""),
            steps_executed=list(self._step_results),
            final_diagnosis=final_diagnosis,
            final_solution=final_solution,
            final_explanation=final_explanation,
            severity=final_severity,
            requires_root=requires_root,
            resolved=bool(final_diagnosis),
            duration_ms=duration_ms,
        )
        self._report.confidence = self._engine._calc_confidence(self._report)

        logger.info("Session completed: tree=%s diagnosis=%s confidence=%.0f%%",
                     self._tree_id,
                     final_diagnosis[:60] if final_diagnosis else "none",
                     self._report.confidence * 100)

    # ── Reports ──

    def get_report(self) -> DiagnosticReport:
        """
        Возвращает финальный отчёт.

        Если сессия не завершена — финализирует.

        Returns:
            DiagnosticReport.
        """
        if self._report is None:
            self._finalize()
        return self._report  # type: ignore[return-value]

    def get_snapshots(self) -> List[StepSnapshot]:
        """Все снимки шагов сессии."""
        return list(self._step_snapshots)

    def get_step_results(self) -> List[StepResult]:
        """Все результаты шагов."""
        return list(self._step_results)

    # ── Alternatives ──

    def get_alternatives(self) -> List[str]:
        """Список альтернативных деревьев (при неудачном сопоставлении)."""
        return self._alternatives

    def _suggest_alternatives(self, text: str) -> List[str]:
        """Подбирает похожие деревья по ключевым словам."""
        text_words = set(text.lower().split())
        scored: List[tuple] = []

        for tree_id, tree in self._engine._trees.items():
            triggers = tree.get("triggers", [])
            score = 0
            for trigger in triggers:
                trigger_words = set(trigger.lower().split())
                overlap = len(text_words & trigger_words)
                score = max(score, overlap)
            if score > 0:
                scored.append((score, tree_id, tree.get("name", tree_id)))

        scored.sort(key=lambda x: -x[0])
        return [f"{name} ({tid})" for _, tid, name in scored[:5]]

    # ── Fallback ──

    def _make_fallback_report(self, problem_text: str) -> DiagnosticReport:
        """Создаёт fallback-отчёт когда дерево не найдено."""
        alts = self._alternatives
        alt_text = ""
        if alts:
            alt_text = "\nВозможно, вы имели в виду: " + ", ".join(alts[:3])

        report = DiagnosticReport(
            tree_id="__fallback__",
            tree_name="Ручная диагностика",
            category="unknown",
            final_diagnosis=(
                f"Не найдено дерево для: «{problem_text[:100]}».{alt_text}"
            ),
            final_solution=(
                "Попробуйте уточнить проблему или выбрать из списка: "
                + ", ".join(t.get("name", t.get("id", ""))
                           for t in self._engine._trees.values())[:200]
            ),
            severity="info",
            confidence=0.1,
        )
        self._report = report
        self._state = SessionState.COMPLETED
        return report

    # ── Cancel ──

    def cancel(self) -> None:
        """Отменяет текущую сессию."""
        if self._state in (SessionState.RUNNING, SessionState.AWAITING_INPUT):
            self._state = SessionState.CANCELLED
            logger.info("Session cancelled by user")

    # ── Formatting ──

    def format_session(self) -> str:
        """
        Форматирует полный вывод сессии (для CLI).

        Returns:
            Многострочный текст с шагами и отчётом.
        """
        lines = []

        # Заголовок
        tree_name = (self._tree or {}).get("name", self._tree_id or "?")
        lines.append(f"═══ Диагностика: {tree_name} ═══")
        lines.append("")

        # Шаги
        for snap in self._step_snapshots:
            lines.append(snap.format_text())

        lines.append("")

        # Отчёт
        report = self.get_report()
        if report.final_diagnosis:
            sev_icon = {
                "critical": "🔴", "high": "🟠",
                "medium": "🟡", "low": "🟢", "info": "ℹ️",
            }.get(report.severity, "❓")
            lines.append(f"{sev_icon} Диагноз: {report.final_diagnosis}")
        else:
            lines.append("❓ Проблема не определена автоматически.")

        if report.final_solution:
            lines.append(f"\n💡 Решение: {report.final_solution}")

        if report.final_explanation:
            lines.append(f"\n📝 {report.final_explanation}")

        if report.requires_root:
            lines.append("\n⚠️  Для исправления нужны права root (sudo).")

        lines.append(f"\n🎯 Уверенность: {report.confidence:.0%}")
        lines.append(f"⏱  Время: {report.duration_ms} мс")

        return "\n".join(lines)

    def format_progress_bar(self, width: int = 30) -> str:
        """Текстовый прогресс-бар."""
        p = self.progress
        filled = int(width * p)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}] {p:.0%}"

    # ── List available trees ──

    def list_available(self, category: Optional[str] = None) -> str:
        """Список доступных деревьев диагностики."""
        trees = self._engine.list_trees(category)
        if not trees:
            return "Нет доступных деревьев диагностики."

        lines = ["📋 Доступные диагностики:"]
        by_cat: Dict[str, List] = {}
        for t in trees:
            cat = t.get("category", "other")
            by_cat.setdefault(cat, []).append(t)

        for cat, items in sorted(by_cat.items()):
            lines.append(f"\n  [{cat}]")
            for t in items:
                triggers = ", ".join(t["triggers"][:3])
                lines.append(f"    • {t['name']} ({t['steps_count']} шагов)")
                if triggers:
                    lines.append(f"      → {triggers}")

        return "\n".join(lines)


# ─── Singleton ───────────────────────────────────────────────────────────────

_session_instance: Optional[DiagnosticSession] = None


def get_session(engine: Optional[DiagnosticEngine] = None) -> DiagnosticSession:
    """Получить (или создать) глобальный экземпляр сессии."""
    global _session_instance
    if _session_instance is None:
        _session_instance = DiagnosticSession(engine)
    return _session_instance


def new_session(engine: Optional[DiagnosticEngine] = None) -> DiagnosticSession:
    """Создать новую сессию (сбрасывает глобальную)."""
    global _session_instance
    _session_instance = DiagnosticSession(engine)
    return _session_instance
