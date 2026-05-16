# -*- coding: utf-8 -*-
"""
Lina Core — System Control (Phase 22).

/system * команды для диагностики и управления.
Команд НЕ модифицирует engine-ы напрямую — только читает состояние.

Поддерживаемые команды:
  /system status       — общий статус
  /system config       — текущая конфигурация
  /system router       — статистика маршрутизации
  /system tools        — доступные инструменты
  /system memory       — использование памяти
  /system history      — статус истории
  /system budget       — контекстный бюджет
  /system performance  — производительность
  /system reload       — перезагрузка конфигурации
  /system safe-mode    — вкл/выкл безопасного режима
  /system trace        — последние execution traces (Phase 23)
  /system mode <name>  — переключение режима (Phase 23)
  /system drift        — проверка state drift (Phase 23)
  /system state        — runtime state snapshot (Phase 23)
  /system degradation  — degradation stats (Phase 23)
  /system guard        — production guard stats (Phase 23)
  /system orchestrator  — execution orchestrator stats (Phase 24)
  /system capabilities  — capability registry (Phase 24)
  /system priority      — priority resolver stats (Phase 24)
  /system integrity     — integrity checker stats (Phase 24)
  /system consistency   — consistency engine stats (Phase 25)
  /system stepmem       — step memory stats (Phase 25)
  /system semdrift      — semantic drift stats (Phase 25)
  /system intentlock    — intent lock stats (Phase 25)
  /system pipeline      — pipeline coordinator stats (Phase 26)
  /system lifecycle     — lifecycle manager stats (Phase 26)
  /system envelope      — last envelope summary (Phase 26)
"""

import time
import logging
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger("lina.core.system_control")


class SystemControl:
    """Обработчик /system команд (Phase 22).

    Изолирован от engine-ов. Получает данные через
    зарегистрированные провайдеры (callbacks).

    Usage:
        sc = SystemControl()
        sc.register_provider("router", lambda: router.get_stats())
        sc.register_provider("config", lambda: config.get_all())
        result = sc.handle("/system status")
    """

    def __init__(self):
        self._providers: Dict[str, Callable[[], Dict[str, Any]]] = {}
        self._start_time = time.time()
        self._command_count = 0

    def register_provider(self, name: str, provider: Callable[[], Dict[str, Any]]) -> None:
        """Регистрирует провайдера данных.

        Args:
            name: Имя провайдера (router, config, tools, etc.)
            provider: Callable, возвращающий dict с данными.
        """
        self._providers[name] = provider
        logger.debug("SYSTEM_CONTROL: registered provider '%s'", name)

    def handle(self, command: str) -> Optional[str]:
        """Обрабатывает /system команду.

        Args:
            command: Полная команда (напр. "/system status").

        Returns:
            Строка с отчётом или None если не /system команда.
        """
        if not command.strip().startswith("/system"):
            return None

        parts = command.strip().split(maxsplit=2)
        subcommand = parts[1] if len(parts) > 1 else "status"
        self._command_count += 1

        handler = {
            "status": self._cmd_status,
            "config": self._cmd_config,
            "router": self._cmd_router,
            "tools": self._cmd_tools,
            "memory": self._cmd_memory,
            "history": self._cmd_history,
            "budget": self._cmd_budget,
            "performance": self._cmd_performance,
            "reload": self._cmd_reload,
            "safe-mode": self._cmd_safe_mode,
            "trace": self._cmd_trace,
            "mode": self._cmd_mode,
            "drift": self._cmd_drift,
            "state": self._cmd_state,
            "degradation": self._cmd_degradation,
            "guard": self._cmd_guard,
            "orchestrator": self._cmd_orchestrator,
            "capabilities": self._cmd_capabilities,
            "priority": self._cmd_priority,
            "integrity": self._cmd_integrity,
            "consistency": self._cmd_consistency,
            "stepmem": self._cmd_stepmem,
            "semdrift": self._cmd_semdrift,
            "intentlock": self._cmd_intentlock,
            "pipeline": self._cmd_pipeline,
            "lifecycle": self._cmd_lifecycle,
            "envelope": self._cmd_envelope,
        }.get(subcommand)

        if not handler:
            available = ", ".join(sorted([
                "status", "config", "router", "tools", "memory",
                "history", "budget", "performance", "reload", "safe-mode",
                "trace", "mode", "drift", "state", "degradation", "guard",
                "orchestrator", "capabilities", "priority", "integrity",
                "consistency", "stepmem", "semdrift", "intentlock",
                "pipeline", "lifecycle", "envelope",
            ]))
            return f"⚠️ Неизвестная подкоманда: {subcommand}\nДоступные: {available}"

        args = parts[2] if len(parts) > 2 else ""
        return handler(args)

    def _get_data(self, provider_name: str) -> Dict[str, Any]:
        """Получить данные от провайдера."""
        if provider_name in self._providers:
            try:
                return self._providers[provider_name]()
            except Exception as e:
                logger.error("Provider '%s' failed: %s", provider_name, e, exc_info=True)
                return {"error": "internal provider error"}
        return {"status": "provider not registered"}

    def _cmd_status(self, args: str) -> str:
        """Общий статус системы."""
        uptime = time.time() - self._start_time
        hours, rem = divmod(int(uptime), 3600)
        minutes, secs = divmod(rem, 60)

        lines = [
            "═══ LINA STATUS ═══",
            f"⏱  Uptime: {hours}h {minutes}m {secs}s",
            f"📊 System commands: {self._command_count}",
            f"📦 Providers: {', '.join(sorted(self._providers)) or 'none'}",
        ]

        # Add brief data from each provider
        for name in sorted(self._providers):
            data = self._get_data(name)
            if "error" not in data:
                summary = ", ".join(f"{k}={v}" for k, v in list(data.items())[:3])
                lines.append(f"  [{name}] {summary}")

        return "\n".join(lines)

    def _cmd_config(self, args: str) -> str:
        """Конфигурация."""
        data = self._get_data("config")
        lines = ["═══ CONFIG ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_router(self, args: str) -> str:
        """Статистика роутера."""
        data = self._get_data("router")
        lines = ["═══ ROUTER STATS ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_tools(self, args: str) -> str:
        """Доступные инструменты."""
        data = self._get_data("tools")
        lines = ["═══ TOOLS ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_memory(self, args: str) -> str:
        """Использование памяти."""
        try:
            import psutil
            proc = psutil.Process()
            mem = proc.memory_info()
            lines = [
                "═══ MEMORY ═══",
                f"  RSS: {mem.rss / 1024 / 1024:.1f} MB",
                f"  VMS: {mem.vms / 1024 / 1024:.1f} MB",
            ]
        except ImportError:
            lines = ["═══ MEMORY ═══", "  psutil not available"]
        data = self._get_data("memory")
        if "status" not in data and "error" not in data:
            for k, v in data.items():
                lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_history(self, args: str) -> str:
        """Статус истории."""
        data = self._get_data("history")
        lines = ["═══ HISTORY ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_budget(self, args: str) -> str:
        """Контекстный бюджет."""
        data = self._get_data("budget")
        lines = ["═══ BUDGET ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_performance(self, args: str) -> str:
        """Производительность."""
        data = self._get_data("performance")
        lines = ["═══ PERFORMANCE ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_reload(self, args: str) -> str:
        """Перезагрузка конфигурации."""
        data = self._get_data("config")
        return "✅ Configuration reloaded"

    def _cmd_safe_mode(self, args: str) -> str:
        """Безопасный режим."""
        data = self._get_data("config")
        current = data.get("safe_mode", False)
        return f"🔒 Safe mode: {'ON' if current else 'OFF'}"

    # ─── Phase 23 commands ────────────────────────────────

    def _cmd_trace(self, args: str) -> str:
        """Execution trace."""
        data = self._get_data("trace")
        if isinstance(data, dict) and "formatted" in data:
            return data["formatted"]
        lines = ["═══ TRACE ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_mode(self, args: str) -> str:
        """Режим работы."""
        data = self._get_data("mode")
        if args.strip():
            # Переключение запрошено — провайдер должен обработать
            return f"Mode switch requested: {args.strip()}\n" + \
                   "\n".join(f"  {k}: {v}" for k, v in sorted(data.items()))
        lines = ["═══ MODE ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_drift(self, args: str) -> str:
        """State drift detection."""
        data = self._get_data("drift")
        lines = ["═══ DRIFT ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_state(self, args: str) -> str:
        """Runtime state snapshot."""
        data = self._get_data("state")
        lines = ["═══ STATE ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_degradation(self, args: str) -> str:
        """Degradation stats."""
        data = self._get_data("degradation")
        lines = ["═══ DEGRADATION ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_guard(self, args: str) -> str:
        """Production guard stats."""
        data = self._get_data("guard")
        lines = ["═══ PRODUCTION GUARD ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    # ─── Phase 24 commands ────────────────────────────────

    def _cmd_orchestrator(self, args: str) -> str:
        """Execution orchestrator stats."""
        data = self._get_data("orchestrator")
        lines = ["═══ ORCHESTRATOR ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_capabilities(self, args: str) -> str:
        """Capability registry."""
        data = self._get_data("capabilities")
        lines = ["═══ CAPABILITIES ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_priority(self, args: str) -> str:
        """Priority resolver stats."""
        data = self._get_data("priority")
        lines = ["═══ PRIORITY ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_integrity(self, args: str) -> str:
        """Integrity checker stats."""
        data = self._get_data("integrity")
        lines = ["═══ INTEGRITY ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    # ─── Phase 25 commands ────────────────────────────────

    def _cmd_consistency(self, args: str) -> str:
        """Consistency engine stats."""
        data = self._get_data("consistency")
        lines = ["═══ CONSISTENCY ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_stepmem(self, args: str) -> str:
        """Step memory stats."""
        data = self._get_data("stepmem")
        lines = ["═══ STEP MEMORY ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_semdrift(self, args: str) -> str:
        """Semantic drift stats."""
        data = self._get_data("semdrift")
        lines = ["═══ SEMANTIC DRIFT ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_intentlock(self, args: str) -> str:
        """Intent lock stats."""
        data = self._get_data("intentlock")
        lines = ["═══ INTENT LOCK ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    # ─── Phase 26 commands ────────────────────────────────

    def _cmd_pipeline(self, args: str) -> str:
        """Coordinator stats."""
        data = self._get_data("pipeline")
        lines = ["═══ PIPELINE COORDINATOR ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_lifecycle(self, args: str) -> str:
        """Lifecycle manager stats."""
        data = self._get_data("lifecycle")
        lines = ["═══ LIFECYCLE ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _cmd_envelope(self, args: str) -> str:
        """Last envelope summary."""
        data = self._get_data("envelope")
        lines = ["═══ ENVELOPE ═══"]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """Статистика SystemControl."""
        return {
            "command_count": self._command_count,
            "uptime_seconds": int(time.time() - self._start_time),
            "providers": list(self._providers.keys()),
        }
