# -*- coding: utf-8 -*-
"""
Lina Core — Response UX Layer (Phase 4).

Transforms IntentResult → human-friendly messages.
Provides:
  - Status-specific formatting (SUCCESS, DENIED, ERROR, etc.)
  - Domain-specific diagnostics advice
  - Edge case handling (empty input, network fail, DBus fail)
  - Graceful degradation messages
  - Progress indicators

ResponseFormatter ONLY formats — NEVER executes.
Governance remains the truth source. This layer translates.

Поток:
  IntentResult → ResponseFormatter.format() → str (human-friendly)

Phase: UX LAYER / Phase 4
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("lina.core.response_ux")


# ═══════════════════════════════════════════════════════════
#  Domain Advice — actionable diagnostics tips
# ═══════════════════════════════════════════════════════════

_DOMAIN_ADVICE: Dict[str, Dict[str, Any]] = {
    "network": {
        "icon": "🌐",
        "tips": [
            "ping 8.8.8.8",
            "nmcli device status",
            "systemctl restart NetworkManager",
        ],
        "check_hint": "диагностика: network",
    },
    "audio": {
        "icon": "🔊",
        "tips": [
            "PipeWire / PulseAudio работает?",
            "pactl info",
            "устройство вывода выбрано?",
        ],
        "check_hint": "диагностика: audio",
    },
    "disk": {
        "icon": "💾",
        "tips": [
            "df -h (свободное место)",
            "lsblk (разделы)",
            "освободите место или расширьте раздел",
        ],
        "check_hint": "диагностика: disk",
    },
    "display": {
        "icon": "🖥",
        "tips": [
            "xrandr или wlr-randr",
            "драйвер видеокарты установлен?",
            "journalctl -b | grep -i gpu",
        ],
        "check_hint": "диагностика: display",
    },
    "service": {
        "icon": "⚙",
        "tips": [
            "systemctl status <сервис>",
            "journalctl -u <сервис> --no-pager -n 20",
            "systemctl restart <сервис>",
        ],
        "check_hint": "диагностика: service",
    },
    "package": {
        "icon": "📦",
        "tips": [
            "pacman -Syu (обновление)",
            "проверьте зеркала: /etc/pacman.d/mirrorlist",
            "pacman -Qk (целостность пакетов)",
        ],
        "check_hint": "диагностика: package",
    },
    "boot": {
        "icon": "🔄",
        "tips": [
            "journalctl -b -p err (ошибки загрузки)",
            "systemd-analyze blame (время загрузки)",
            "mkinitcpio -P (пересборка initramfs)",
        ],
        "check_hint": "диагностика: boot",
    },
    "security": {
        "icon": "🔒",
        "tips": [
            "проверьте права: ls -la",
            "sudo -v (активен ли sudo)",
            "journalctl -b | grep -i denied",
        ],
        "check_hint": "диагностика: security",
    },
    "system": {
        "icon": "🖥",
        "tips": [
            "uname -a",
            "free -h (память)",
            "htop (процессы)",
        ],
        "check_hint": "диагностика: system",
    },
}

# Default advice for unknown domains
_DEFAULT_ADVICE = {
    "icon": "ℹ",
    "tips": [],
    "check_hint": "",
}


# ═══════════════════════════════════════════════════════════
#  Edge Case Messages
# ═══════════════════════════════════════════════════════════

_EMPTY_INPUT_MSG = (
    "Не понял запрос. Попробуйте:\n"
    "  • диагностика сети\n"
    "  • открыть приложение\n"
    "  • помощь"
)

_HELP_COMMANDS = frozenset({
    "помощь", "help", "?", "/help", "/помощь",
})

_HELP_MSG = (
    "Lina — локальный ИИ-помощник для Linux.\n\n"
    "Примеры команд:\n"
    "  • диагностика сети\n"
    "  • перезапусти NetworkManager\n"
    "  • открой Firefox\n"
    "  • проверь звук\n"
    "  • свободное место на диске\n\n"
    "Управление:\n"
    "  • /confirm <id> — подтвердить действие\n"
    "  • /deny <id>   — отклонить действие\n"
    "  • выход         — завершить сеанс"
)

# ═══════════════════════════════════════════════════════════
#  Degradation Messages
# ═══════════════════════════════════════════════════════════

_DEGRADATION_MSGS: Dict[str, str] = {
    "dbus": (
        "DBus недоступен.\n"
        "Используется локальный режим."
    ),
    "network": (
        "Сеть недоступна.\n"
        "Локальные функции работают.\n"
        "Диагностика сети: network"
    ),
    "llm": (
        "Языковая модель недоступна.\n"
        "Работают встроенные команды и диагностика."
    ),
    "governance": (
        "Governance pipeline недоступен.\n"
        "Действия заблокированы до восстановления."
    ),
}


# ═══════════════════════════════════════════════════════════
#  Progress Templates
# ═══════════════════════════════════════════════════════════

_PROGRESS_MSGS: Dict[str, str] = {
    "thinking": "⏳ Думаю...",
    "analyzing": "🔍 Анализ...",
    "executing": "⚙ Выполняется...",
    "diagnosing": "🔍 Диагностика...",
    "confirming": "⏳ Ожидание подтверждения...",
}


# ═══════════════════════════════════════════════════════════
#  Response Formatter
# ═══════════════════════════════════════════════════════════

class ResponseFormatter:
    """
    Formats IntentResult into human-friendly messages.

    Phase 4: UX layer between governance and user.
    Governance decides → ResponseFormatter explains.

    Usage:
        fmt = get_response_formatter()
        text = fmt.format_result(intent_result)
        text = fmt.format_edge_case("empty_input")
        text = fmt.format_degradation("dbus")
    """

    def __init__(self) -> None:
        self._domain_advice = dict(_DOMAIN_ADVICE)

    # ── Main formatter ───────────────────────────────────

    def format_result(self, result, *, domain: str = "",
                      action: str = "") -> str:
        """
        Format IntentResult → human-friendly text.

        Args:
            result: IntentResult from governance pipeline.
            domain: Domain of the intent (for advice).
            action: Action ID (for context).

        Returns:
            Formatted string for user display.
        """
        from lina.intent.types import IntentStatus

        status = result.status
        text = result.response_text or ""

        if status == IntentStatus.SUCCESS:
            return self._format_success(text, domain, action)

        if status == IntentStatus.DENIED:
            return self._format_denied(text, domain)

        if status == IntentStatus.NEEDS_CONFIRM:
            esc_id = getattr(result, 'escalation_id', '')
            return self._format_confirm(text, esc_id)

        if status == IntentStatus.ESCALATED:
            return self._format_escalated(text)

        if status == IntentStatus.FAILED:
            return self._format_error(text, domain)

        if status == IntentStatus.NOT_FOUND:
            return self._format_not_found(text, domain)

        if status == IntentStatus.CHAT_RESPONSE:
            return text  # LLM response — pass through

        return text or ""

    # ── Status-specific formatters ───────────────────────

    def _format_success(self, text: str, domain: str,
                        action: str) -> str:
        """Format SUCCESS result."""
        advice = self._domain_advice.get(domain, _DEFAULT_ADVICE)
        icon = advice.get("icon", "✅")
        hint = advice.get("check_hint", "")

        if text:
            msg = f"✅ {text}"
        elif action:
            msg = f"✅ Действие выполнено: {action}."
        else:
            msg = "✅ Готово."

        if hint and domain:
            msg += f"\nЕсли проблема сохраняется — {hint}."

        return msg

    def _format_denied(self, text: str, domain: str) -> str:
        """Format DENIED result."""
        if text:
            msg = f"🚫 Действие отклонено: {text}"
        else:
            msg = "🚫 Действие отклонено."

        if domain:
            msg += f"\nДомен: {domain}."

        return msg

    def _format_confirm(self, text: str, esc_id: str) -> str:
        """Format NEEDS_CONFIRM result."""
        msg = "⚠ Требуется подтверждение"
        if text:
            msg += f":\n{text}"

        if esc_id:
            msg += (
                f"\n\n/confirm {esc_id} — подтвердить\n"
                f"/deny {esc_id} — отклонить"
            )

        return msg

    def _format_escalated(self, text: str) -> str:
        """Format ESCALATED result."""
        if text:
            return f"⏫ Эскалировано: {text}"
        return "⏫ Запрос передан на рассмотрение."

    def _format_error(self, text: str, domain: str) -> str:
        """
        Format ERROR/FAILED result — actionable, no stack traces.
        """
        # Strip any stack trace that might have leaked
        clean = self._strip_traceback(text)

        msg = f"❌ Ошибка: {clean}" if clean else "❌ Произошла ошибка."

        # Add domain-specific advice
        advice = self._domain_advice.get(domain, _DEFAULT_ADVICE)
        tips = advice.get("tips", [])
        if tips:
            msg += "\n\nВозможные решения:"
            for tip in tips[:3]:
                msg += f"\n  • {tip}"

        return msg

    def _format_not_found(self, text: str, domain: str) -> str:
        """Format NOT_FOUND result."""
        if text:
            msg = f"ℹ {text}"
        else:
            msg = "ℹ Не удалось найти подходящее действие."

        advice = self._domain_advice.get(domain, _DEFAULT_ADVICE)
        hint = advice.get("check_hint", "")
        if hint:
            msg += f"\nПопробуйте: {hint}"

        return msg

    # ── Edge cases ───────────────────────────────────────

    def format_empty_input(self) -> str:
        """Format response for empty user input."""
        return _EMPTY_INPUT_MSG

    def format_help(self) -> str:
        """Format help message."""
        return _HELP_MSG

    def is_help_command(self, text: str) -> bool:
        """Check if text is a help command."""
        return text.strip().lower() in _HELP_COMMANDS

    def format_cancel(self) -> str:
        """Format confirmation cancel."""
        return "🚫 Действие отменено пользователем."

    def format_permission_error(self, domain: str = "") -> str:
        """Format permission denied."""
        msg = "🚫 Недостаточно прав."
        if domain:
            msg += f"\nДомен: {domain}. Доступ ограничен."
        return msg

    # ── Degradation ──────────────────────────────────────

    def format_degradation(self, component: str) -> str:
        """Format degradation message for a component."""
        return _DEGRADATION_MSGS.get(
            component,
            f"⚠ Компонент '{component}' недоступен.\n"
            "Работа продолжается в ограниченном режиме."
        )

    # ── Diagnostics advice ───────────────────────────────

    def format_diagnostics_advice(self, domain: str, *,
                                  problem: str = "") -> str:
        """
        Format domain-specific diagnostics advice.

        No commands are executed — only advice is given.
        """
        advice = self._domain_advice.get(domain, _DEFAULT_ADVICE)
        icon = advice.get("icon", "🔍")
        tips = advice.get("tips", [])

        if problem:
            msg = f"{icon} {problem}"
        else:
            msg = f"{icon} Диагностика: {domain}"

        if tips:
            msg += "\n\nПопробуйте:"
            for tip in tips:
                msg += f"\n  • {tip}"

        return msg

    def get_domain_advice(self, domain: str) -> Dict[str, Any]:
        """Get raw advice data for a domain."""
        return dict(self._domain_advice.get(domain, _DEFAULT_ADVICE))

    # ── Progress ─────────────────────────────────────────

    def format_progress(self, stage: str = "thinking") -> str:
        """Format a progress indicator."""
        return _PROGRESS_MSGS.get(stage, "⏳ Обработка...")

    # ── Helpers ──────────────────────────────────────────

    @staticmethod
    def _strip_traceback(text: str) -> str:
        """Remove Python tracebacks from error messages."""
        if not text:
            return ""
        lines = text.split('\n')
        clean = []
        skip = False
        for line in lines:
            if line.strip().startswith('Traceback (most recent'):
                skip = True
                continue
            if skip:
                if line and not line.startswith(' '):
                    # End of traceback — keep this line (the exception msg)
                    skip = False
                    # Extract just the exception message
                    if ':' in line:
                        clean.append(line.split(':', 1)[1].strip())
                    else:
                        clean.append(line.strip())
                continue
            clean.append(line)
        return '\n'.join(clean).strip()


# ─── Singleton ────────────────────────────────────────────────────────────────

_formatter: Optional[ResponseFormatter] = None


def get_response_formatter() -> ResponseFormatter:
    """Get or create ResponseFormatter singleton."""
    global _formatter
    if _formatter is None:
        _formatter = ResponseFormatter()
    return _formatter
