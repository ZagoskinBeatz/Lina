"""
Lina — Анализатор логов и оптимизатор.

Возможности:
  - Анализ паттернов использования из audit.log
  - Рекомендации по оптимизации
  - Выявление проблемных команд
  - Оптимизация цепочек командных макросов
"""

import json
import time
from collections import Counter, deque
from pathlib import Path
from typing import Optional

from lina.config import LOGS_DIR
from lina.system.logger import logger, AUDIT_FILE


class LogAnalyzer:
    """
    Анализирует аудит-логи Lina для выявления паттернов и оптимизации.
    """

    def __init__(self):
        self._entries: deque = deque(maxlen=5000)

    def load_audit_log(self, max_entries: int = 5000) -> int:
        """
        Загружает записи из аудит-лога.

        Returns:
            Количество загруженных записей.
        """
        _MAX_AUDIT_SIZE = 50 * 1024 * 1024  # 50 MB
        self._entries = deque(maxlen=max_entries)
        try:
            if AUDIT_FILE.exists():
                if AUDIT_FILE.stat().st_size > _MAX_AUDIT_SIZE:
                    logger.warning("Audit log too large (%d bytes), skipping",
                                   AUDIT_FILE.stat().st_size)
                    return 0
                with open(AUDIT_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                self._entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
        except IOError as e:
            logger.error("Failed to load audit log: %s", e)

        return len(self._entries)

    def analyze_usage_patterns(self) -> dict:
        """
        Анализ паттернов использования.

        Returns:
            dict: action_counts, peak_hours, avg_response_time, etc.
        """
        if not self._entries:
            self.load_audit_log()

        if not self._entries:
            return {"error": "Нет данных для анализа"}

        # Подсчёт действий по типам
        action_counts = Counter()
        hours = Counter()
        response_times = []
        failures = 0
        commands = []

        for e in self._entries:
            action_counts[e.get("action", "unknown")] += 1

            # Час использования
            ts = e.get("timestamp", 0)
            if ts:
                hour = time.localtime(ts).tm_hour
                hours[hour] += 1

            # Время отклика
            details = e.get("details", {})
            elapsed = details.get("elapsed_seconds")
            if elapsed is not None:
                response_times.append(elapsed)

            # Неудачи
            if not e.get("success", True):
                failures += 1

            # Команды
            cmd = e.get("command", "")
            if cmd:
                commands.append(cmd)

        # Пиковые часы
        peak = hours.most_common(3)

        # Средние времена
        avg_time = round(sum(response_times) / len(response_times), 3) if response_times else 0

        return {
            "total_entries": len(self._entries),
            "action_counts": dict(action_counts.most_common()),
            "peak_hours": [{"hour": h, "count": c} for h, c in peak],
            "avg_response_time": avg_time,
            "max_response_time": round(max(response_times), 3) if response_times else 0,
            "failure_rate": round(failures / max(len(self._entries), 1) * 100, 1),
            "unique_commands": len(set(commands)),
        }

    def find_slow_operations(self, threshold: float = 5.0) -> list:
        """Находит медленные операции (> threshold секунд)."""
        if not self._entries:
            self.load_audit_log()

        slow = []
        for e in self._entries:
            details = e.get("details", {})
            elapsed = details.get("elapsed_seconds", 0)
            if elapsed > threshold:
                slow.append({
                    "action": e.get("action"),
                    "command": e.get("command", "")[:100],
                    "elapsed": elapsed,
                    "time": e.get("time", ""),
                })

        slow.sort(key=lambda x: x["elapsed"], reverse=True)
        return slow[:20]

    def find_frequent_errors(self) -> list:
        """Находит часто повторяющиеся ошибки."""
        if not self._entries:
            self.load_audit_log()

        errors = []
        for e in self._entries:
            if not e.get("success", True):
                errors.append(e.get("command", e.get("action", "unknown")))

        freq = Counter(errors).most_common(10)
        return [{"command": cmd, "count": cnt} for cmd, cnt in freq]

    def suggest_macros(self, min_sequence: int = 2) -> list:
        """
        Анализирует последовательности команд и предлагает макросы.

        Находит часто повторяющиеся пары/тройки команд.
        """
        if not self._entries:
            self.load_audit_log()

        commands = [
            e.get("command", "").strip()
            for e in self._entries
            if e.get("action") == "command" and e.get("command", "").strip()
        ]

        if len(commands) < min_sequence:
            return []

        # Пары
        pairs = Counter()
        for i in range(len(commands) - 1):
            pair = (commands[i], commands[i + 1])
            pairs[pair] += 1

        # Тройки
        triples = Counter()
        for i in range(len(commands) - 2):
            triple = (commands[i], commands[i + 1], commands[i + 2])
            triples[triple] += 1

        suggestions = []

        for pair, count in pairs.most_common(5):
            if count >= 3:
                suggestions.append({
                    "type": "pair",
                    "commands": list(pair),
                    "frequency": count,
                    "suggested_name": f"макрос_{len(suggestions) + 1}",
                })

        for triple, count in triples.most_common(3):
            if count >= 2:
                suggestions.append({
                    "type": "triple",
                    "commands": list(triple),
                    "frequency": count,
                    "suggested_name": f"сложный_макрос_{len(suggestions) + 1}",
                })

        return suggestions

    def generate_report(self) -> str:
        """Генерирует текстовый отчёт анализа."""
        patterns = self.analyze_usage_patterns()
        slow = self.find_slow_operations()
        errors = self.find_frequent_errors()
        macros = self.suggest_macros()

        lines = [
            "📊 Отчёт анализа Lina",
            "=" * 40,
            "",
            f"Всего записей: {patterns.get('total_entries', 0)}",
            f"Среднее время отклика: {patterns.get('avg_response_time', 0)} сек",
            f"Процент ошибок: {patterns.get('failure_rate', 0)}%",
            f"Уникальных команд: {patterns.get('unique_commands', 0)}",
            "",
        ]

        # Действия по частоте
        actions = patterns.get("action_counts", {})
        if actions:
            lines.append("Действия по частоте:")
            for act, cnt in list(actions.items())[:10]:
                lines.append(f"  {act}: {cnt}")
            lines.append("")

        # Пиковые часы
        peak = patterns.get("peak_hours", [])
        if peak:
            lines.append("Пиковые часы:")
            for p in peak:
                lines.append(f"  {p['hour']}:00 — {p['count']} операций")
            lines.append("")

        # Медленные операции
        if slow:
            lines.append("⚠ Медленные операции:")
            for s in slow[:5]:
                lines.append(f"  {s['action']}: {s['elapsed']} сек — {s['command']}")
            lines.append("")

        # Частые ошибки
        if errors:
            lines.append("❌ Частые ошибки:")
            for e in errors[:5]:
                lines.append(f"  {e['command']}: {e['count']} раз")
            lines.append("")

        # Предложения макросов
        if macros:
            lines.append("💡 Предложения макросов:")
            for m in macros[:3]:
                cmds = " → ".join(m["commands"])
                lines.append(f"  {m['suggested_name']}: {cmds} ({m['frequency']}x)")

        return "\n".join(lines)
