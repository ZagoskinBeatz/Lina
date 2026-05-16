"""
Lina — Движок диагностики (Diagnostic Engine).

Гибридный подход:
  1. Быстрые деревья решений (JSON) — покрывают 80% типичных проблем
  2. LLM-fallback — для сложных / неизвестных проблем

Класс DiagnosticEngine:
  - load_trees(path)         — загрузить деревья из JSON-файлов
  - match_problem(user_input)— найти подходящее дерево по ключевым словам
  - run_diagnostic(tree_id)  — выполнить проверки пошагово
  - get_report()             — структурированный отчёт
  - collect_system_context()  — собрать контекст для LLM-fallback

Класс DiagnosticStep:
  - Один шаг дерева: команда + regex + ветвление

Формат дерева (JSON):
  {
    "id": "wifi_not_working",
    "name": "WiFi не работает",
    "category": "network",
    "triggers": ["wifi не работает", "нет wifi", ...],
    "steps": [
      {
        "id": "check_rfkill",
        "description": "Проверка RF-блокировки",
        "check": "rfkill list wifi",
        "parse": "Soft blocked: yes",
        "if_match": {
          "diagnosis": "WiFi заблокирован программно",
          "solution": "rfkill unblock wifi",
          "explanation": "...",
          "severity": "medium",
          "requires_root": false,
          "next": null
        },
        "if_no_match": {"next": "check_interface"}
      },
      ...
    ]
  }
"""

import json
import re
import subprocess
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ─── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class StepResult:
    """Результат выполнения одного шага диагностики."""
    step_id: str
    description: str
    command: str
    output: str
    matched: bool
    diagnosis: str = ""
    solution: str = ""
    explanation: str = ""
    severity: str = ""  # info, low, medium, high, critical
    requires_root: bool = False


@dataclass
class DiagnosticReport:
    """Полный отчёт о диагностике."""
    tree_id: str
    tree_name: str
    category: str
    steps_executed: List[StepResult] = field(default_factory=list)
    final_diagnosis: str = ""
    final_solution: str = ""
    final_explanation: str = ""
    severity: str = "unknown"
    confidence: float = 0.0  # 0.0 - 1.0
    requires_root: bool = False
    resolved: bool = False
    duration_ms: int = 0

    def to_dict(self) -> Dict:
        return {
            "tree_id": self.tree_id,
            "tree_name": self.tree_name,
            "category": self.category,
            "steps": [
                {
                    "step_id": s.step_id,
                    "description": s.description,
                    "command": s.command,
                    "matched": s.matched,
                    "diagnosis": s.diagnosis,
                }
                for s in self.steps_executed
            ],
            "diagnosis": self.final_diagnosis,
            "solution": self.final_solution,
            "explanation": self.final_explanation,
            "severity": self.severity,
            "confidence": self.confidence,
            "requires_root": self.requires_root,
            "resolved": self.resolved,
            "duration_ms": self.duration_ms,
        }

    def format_text(self) -> str:
        """Форматированный текстовый отчёт."""
        lines = [
            f"═══ Диагностика: {self.tree_name} ═══",
            "",
        ]

        for i, step in enumerate(self.steps_executed, 1):
            icon = "✅" if step.matched else "⬜"
            lines.append(f"  {icon} Шаг {i}: {step.description}")
            if step.diagnosis:
                lines.append(f"     → {step.diagnosis}")

        lines.append("")
        if self.final_diagnosis:
            sev_icon = {
                "critical": "🔴",
                "high": "🟠",
                "medium": "🟡",
                "low": "🟢",
                "info": "ℹ️",
            }.get(self.severity, "❓")
            lines.append(f"{sev_icon} Диагноз: {self.final_diagnosis}")
        else:
            lines.append("❓ Проблема не найдена автоматически.")

        if self.final_solution:
            lines.append(f"\n💡 Решение: {self.final_solution}")

        if self.final_explanation:
            lines.append(f"\n📝 {self.final_explanation}")

        if self.requires_root:
            lines.append("\n⚠️  Для исправления нужны права root (sudo).")

        lines.append(f"\n🎯 Уверенность: {self.confidence:.0%}")
        return "\n".join(lines)


# ─── Engine ───────────────────────────────────────────────────────────────────

class DiagnosticEngine:
    """
    Движок диагностики Lina.

    Загружает деревья решений из JSON, сопоставляет проблему
    по ключевым словам, выполняет пошаговые проверки.
    """

    def __init__(self, trees_dir: Optional[str] = None):
        self._trees: Dict[str, Dict] = {}
        self._trigger_index: Dict[str, str] = {}  # trigger → tree_id
        self._last_report: Optional[DiagnosticReport] = None

        if trees_dir:
            self.load_trees(trees_dir)
        else:
            # Дефолтный путь: lina/diagnostics/trees/
            default_dir = Path(__file__).parent / "trees"
            if default_dir.exists():
                self.load_trees(str(default_dir))

    # ── Loading ──

    def load_trees(self, path: str) -> int:
        """
        Загрузить деревья из директории с JSON-файлами.

        Args:
            path: Путь к директории с .json файлами.

        Returns:
            Количество загруженных деревьев.
        """
        loaded = 0
        p = Path(path)

        if p.is_file() and p.suffix == ".json":
            loaded += self._load_file(p)
        elif p.is_dir():
            for file in sorted(p.glob("*.json")):
                loaded += self._load_file(file)

        return loaded

    def _load_file(self, path: Path) -> int:
        """Загрузить одно дерево из файла."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            return 0

        # Файл может содержать одно дерево или список
        trees = data if isinstance(data, list) else [data]
        count = 0
        for tree in trees:
            tree_id = tree.get("id")
            if not tree_id:
                continue
            self._trees[tree_id] = tree

            # Индексируем триггеры
            triggers = tree.get("triggers", [])
            for trigger in triggers:
                self._trigger_index[trigger.lower().strip()] = tree_id

            count += 1

        return count

    def load_tree_from_dict(self, tree: Dict) -> bool:
        """Загрузить дерево напрямую из словаря (для тестов)."""
        tree_id = tree.get("id")
        if not tree_id:
            return False
        self._trees[tree_id] = tree
        for trigger in tree.get("triggers", []):
            self._trigger_index[trigger.lower().strip()] = tree_id
        return True

    # ── Matching ──

    def match_problem(self, user_input: str) -> Optional[str]:
        """
        Найти подходящее дерево по пользовательскому вводу.

        Стратегия (по приоритету):
          1. Точное совпадение триггера
          2. Все слова триггера содержатся во вводе
          3. Нечёткое сопоставление (>50% слов совпали)

        Args:
            user_input: Текст пользователя.

        Returns:
            tree_id или None.
        """
        text = user_input.lower().strip()
        if not text:
            return None

        # 1. Точное совпадение
        if text in self._trigger_index:
            return self._trigger_index[text]

        # 2. Подстрока
        for trigger, tree_id in self._trigger_index.items():
            if trigger in text:
                return tree_id

        # 3. Пословное совпадение
        text_words = set(text.split())
        best_id = None
        best_score = 0.0

        for trigger, tree_id in self._trigger_index.items():
            trigger_words = set(trigger.split())
            if not trigger_words:
                continue
            overlap = len(text_words & trigger_words)
            score = overlap / len(trigger_words)
            if score > best_score and score >= 0.5:
                best_score = score
                best_id = tree_id

        return best_id

    def get_tree_ids(self) -> List[str]:
        """Список ID всех загруженных деревьев."""
        return list(self._trees.keys())

    def get_tree(self, tree_id: str) -> Optional[Dict]:
        """Получить дерево по ID."""
        return self._trees.get(tree_id)

    def get_categories(self) -> List[str]:
        """Уникальные категории загруженных деревьев."""
        return list(set(t.get("category", "") for t in self._trees.values()))

    def list_trees(self, category: Optional[str] = None) -> List[Dict]:
        """Список деревьев с кратким описанием."""
        result = []
        for tree_id, tree in self._trees.items():
            if category and tree.get("category") != category:
                continue
            result.append({
                "id": tree_id,
                "name": tree.get("name", tree_id),
                "category": tree.get("category", ""),
                "triggers": tree.get("triggers", []),
                "steps_count": len(tree.get("steps", [])),
            })
        return result

    # ── Execution ──

    def run_diagnostic(self, tree_id: str) -> DiagnosticReport:
        """
        Выполнить диагностику по дереву.

        Проходит по шагам, выполняет команды, проверяет regex,
        переходит к следующему шагу или возвращает диагноз.

        Args:
            tree_id: ID дерева.

        Returns:
            DiagnosticReport с результатами.
        """
        tree = self._trees.get(tree_id)
        if not tree:
            report = DiagnosticReport(
                tree_id=tree_id,
                tree_name="Unknown",
                category="",
                final_diagnosis=f"Дерево '{tree_id}' не найдено.",
                severity="info",
            )
            self._last_report = report
            return report

        t0 = time.time()
        report = DiagnosticReport(
            tree_id=tree_id,
            tree_name=tree.get("name", tree_id),
            category=tree.get("category", ""),
        )

        steps = tree.get("steps", [])
        if not steps:
            report.final_diagnosis = "Дерево не содержит шагов."
            self._last_report = report
            return report

        # Построим индекс шагов по id
        step_map: Dict[str, Dict] = {}
        for s in steps:
            sid = s.get("id", "")
            if sid:
                step_map[sid] = s

        # Начинаем с первого шага
        current_step = steps[0]
        visited = set()  # защита от циклов
        max_steps = 30

        for _ in range(max_steps):
            step_id = current_step.get("id", f"step_{len(report.steps_executed)}")

            if step_id in visited:
                break
            visited.add(step_id)

            # Выполняем команду
            command = current_step.get("check", "")
            output = self._execute_check(command) if command else ""

            # Проверяем паттерн
            parse_pattern = current_step.get("parse", "")
            matched = self._check_pattern(output, parse_pattern) if parse_pattern else bool(output.strip())

            # Формируем StepResult
            step_result = StepResult(
                step_id=step_id,
                description=current_step.get("description", step_id),
                command=command,
                output=output[:500],  # Ограничиваем вывод
                matched=matched,
            )

            # Определяем ветку
            branch = current_step.get("if_match") if matched else current_step.get("if_no_match")

            if branch:
                step_result.diagnosis = branch.get("diagnosis", "")
                step_result.solution = branch.get("solution", "")
                step_result.explanation = branch.get("explanation", "")
                step_result.severity = branch.get("severity", "")
                step_result.requires_root = branch.get("requires_root", False)

                # Если есть diagnosis — это финальный (или промежуточный) диагноз
                if step_result.diagnosis:
                    report.final_diagnosis = step_result.diagnosis
                    report.final_solution = step_result.solution or report.final_solution
                    report.final_explanation = step_result.explanation or report.final_explanation
                    report.severity = step_result.severity or report.severity
                    report.requires_root = step_result.requires_root or report.requires_root

                # Переход к следующему шагу
                next_id = branch.get("next")
                report.steps_executed.append(step_result)

                if next_id is None:
                    # Конец цепочки
                    report.resolved = bool(report.final_diagnosis)
                    break

                next_step = step_map.get(next_id)
                if next_step:
                    current_step = next_step
                else:
                    # Не нашли следующий шаг
                    break
            else:
                report.steps_executed.append(step_result)
                # Нет ветки — переходим к следующему шагу по порядку
                idx = steps.index(current_step) if current_step in steps else -1
                if idx >= 0 and idx + 1 < len(steps):
                    current_step = steps[idx + 1]
                else:
                    break

        # Confidence
        report.confidence = self._calc_confidence(report)
        report.duration_ms = int((time.time() - t0) * 1000)

        self._last_report = report
        return report

    def _execute_check(self, command: str, timeout: int = 10) -> str:
        """Выполняет команду проверки (read-only)."""
        if not command or not command.strip():
            return ""

        import shlex
        # Безопасность: allowlist разрешённых команд
        _ALLOWED_PREFIXES = (
            "rfkill", "ip ", "ip\n", "nmcli", "systemctl", "journalctl",
            "cat ", "grep ", "ls ", "lspci", "lsusb", "lsblk", "findmnt",
            "uname", "hostname", "uptime", "free", "df ", "mount",
            "ps ", "pgrep", "bluetoothctl", "pactl", "pw-cli",
            "xrandr", "wlr-randr", "loginctl", "timedatectl",
            "resolvectl", "networkctl", "iw ", "iwconfig",
            "dmesg", "sysctl ", "modinfo", "lsmod",
            "sensors", "nvidia-smi", "nproc", "getconf",
            "test ", "[", "echo ",
        )
        cmd_stripped = command.strip()
        # Strip shell redirections (2>/dev/null, 2>&1) — not supported by shell=False
        cmd_stripped = re.sub(r'\s*2>/dev/null', '', cmd_stripped)
        cmd_stripped = re.sub(r'\s*2>&1', '', cmd_stripped)
        cmd_stripped = cmd_stripped.strip()

        if not any(cmd_stripped.startswith(p) for p in _ALLOWED_PREFIXES):
            return f"[BLOCKED: command not in allowlist]"

        try:
            # Handle shell pipes: split into pipeline stages
            if '|' in cmd_stripped:
                stages = [s.strip() for s in cmd_stripped.split('|') if s.strip()]
                if not stages:
                    return ""
                # Verify first stage is allowed
                prev_output = None
                for stage in stages:
                    args = shlex.split(stage)
                    proc = subprocess.run(
                        args, shell=False,
                        input=prev_output,
                        capture_output=True, text=True,
                        timeout=timeout,
                    )
                    prev_output = proc.stdout
                return (prev_output or "").strip()

            args = shlex.split(cmd_stripped)
            result = subprocess.run(
                args, shell=False,
                capture_output=True, text=True,
                timeout=timeout,
            )
            return result.stdout.strip() + ("\n" + result.stderr.strip() if result.stderr.strip() else "")
        except subprocess.TimeoutExpired:
            return "[TIMEOUT]"
        except (FileNotFoundError, OSError, ValueError):
            return ""

    def _check_pattern(self, output: str, pattern: str) -> bool:
        """Проверяет regex или подстроку в выводе."""
        if not output or not pattern:
            return False

        # Сначала regex (с защитой от ReDoS)
        try:
            compiled = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
            # Ограничиваем длину строки для regex-поиска
            if compiled.search(output[:8192]):
                return True
        except re.error:
            pass

        # Фолбэк: подстрока
        return pattern.lower() in output.lower()

    def _calc_confidence(self, report: DiagnosticReport) -> float:
        """Рассчитывает уверенность диагноза (0.0 — 1.0)."""
        if not report.steps_executed:
            return 0.0

        if not report.final_diagnosis:
            return 0.1

        # Базовый — 0.5 за наличие диагноза
        conf = 0.5

        # +0.1 за каждый match (макс 0.3)
        matches = sum(1 for s in report.steps_executed if s.matched)
        conf += min(matches * 0.1, 0.3)

        # +0.1 за наличие solution
        if report.final_solution:
            conf += 0.1

        # +0.1 за наличие explanation
        if report.final_explanation:
            conf += 0.1

        return min(conf, 1.0)

    # ── State ──

    def get_report(self) -> Optional[DiagnosticReport]:
        """Последний отчёт."""
        return self._last_report

    # ── System context для LLM fallback ──

    def collect_system_context(self, max_lines: int = 50) -> Dict:
        """
        Собирает системный контекст для LLM-fallback.

        Returns:
            {journal_errors, dmesg_tail, failed_services,
             memory, disk, load_avg, kernel, uptime}
        """
        ctx: Dict = {}

        # Journal errors (последний час)
        journal = self._execute_check(
            "journalctl --no-pager -p err --since='-1h' -n 20 --output=short 2>/dev/null"
        )
        ctx["journal_errors"] = journal[:2000] if journal else ""

        # dmesg tail
        dmesg = self._execute_check("dmesg -T 2>/dev/null | tail -20")
        ctx["dmesg_tail"] = dmesg[:1500] if dmesg else ""

        # Failed services
        ctx["failed_services"] = self._execute_check(
            "systemctl --failed --no-pager --no-legend 2>/dev/null"
        )

        # Memory
        ctx["memory"] = self._execute_check("free -h 2>/dev/null")

        # Disk
        ctx["disk"] = self._execute_check("df -h / /home 2>/dev/null")

        # Load
        ctx["load_avg"] = self._execute_check("cat /proc/loadavg")

        # Kernel
        ctx["kernel"] = self._execute_check("uname -r")

        # Uptime
        ctx["uptime"] = self._execute_check("uptime -p 2>/dev/null")

        return ctx

    def build_llm_prompt(self, user_input: str, context: Optional[Dict] = None) -> str:
        """
        Генерирует промпт для LLM-анализа проблемы.

        Args:
            user_input: Вопрос/жалоба пользователя.
            context: Системный контекст (если None — собирается).

        Returns:
            Промпт для LLM.
        """
        if context is None:
            context = self.collect_system_context()

        parts = [
            "Ты — Lina, AI-ассистент для Linux. Пользователь описал проблему.",
            "Проанализируй системную информацию и дай пошаговую рекомендацию.",
            "",
            f"Проблема пользователя: {user_input}",
            "",
            "=== Системный контекст ===",
        ]

        for key, value in context.items():
            if value and value.strip():
                parts.append(f"\n--- {key} ---")
                parts.append(value.strip())

        parts.extend([
            "",
            "=== Формат ответа ===",
            "1. Диагноз (одним предложением)",
            "2. Пошаговое решение (команды с пояснениями)",
            "3. Что проверить, если не помогло",
        ])

        return "\n".join(parts)

    # ── Quick diagnostics (без дерева) ──

    def quick_diagnose(self, user_input: str) -> DiagnosticReport:
        """
        Полный цикл: match → run → report.

        Если дерево найдено — используется dерево.
        Если нет — возвращается пустой report с collected context.

        Args:
            user_input: Проблема пользователя.

        Returns:
            DiagnosticReport.
        """
        tree_id = self.match_problem(user_input)

        if tree_id:
            return self.run_diagnostic(tree_id)

        # Нет дерева → fallback report
        report = DiagnosticReport(
            tree_id="__fallback__",
            tree_name="LLM Fallback",
            category="unknown",
            final_diagnosis="Проблема не соответствует известным шаблонам.",
            severity="info",
            confidence=0.1,
        )
        self._last_report = report
        return report
