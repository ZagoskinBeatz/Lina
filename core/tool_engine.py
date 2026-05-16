# -*- coding: utf-8 -*-
"""
Lina Core — Tool Engine Wrapper (Phase 22).

Обёртка для безопасной работы с инструментами.
Sanitizer → Executor → Formatter → Validator.

ToolEngine:
  - Не пишет напрямую в историю
  - Не регистрирует знания без подтверждения
  - Санитизирует вход и выход
  - Ограничивает длину вывода (max_tool_output_tokens)
"""

import re
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Callable, List

logger = logging.getLogger("lina.core.tool_engine")


# ═══════════════════════════════════════════════════════════
#  Tool Result
# ═══════════════════════════════════════════════════════════

@dataclass
class ToolResult:
    """Результат выполнения инструмента."""
    success: bool = True
    output: str = ""
    error: Optional[str] = None
    tool_name: str = ""
    sanitized: bool = False       # True = output был обрезан/очищен
    truncated: bool = False       # True = output был урезан по лимиту
    raw_length: int = 0           # длина до обрезки

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "tool_name": self.tool_name,
            "truncated": self.truncated,
        }


# ═══════════════════════════════════════════════════════════
#  Tool Engine
# ═══════════════════════════════════════════════════════════

class ToolEngine:
    """Обёртка для инструментов (Phase 22).

    Поток: Input → Sanitize → Execute → Format → Validate → Output.
    НИКОГДА не пишет в историю и не регистрирует знания.

    Usage:
        engine = ToolEngine(max_output_tokens=300)
        engine.register("system_info", get_system_info_fn)
        result = engine.execute("system_info", {"detail": "cpu"})
    """

    def __init__(self, max_output_tokens: int = 300):
        self.max_output_tokens = max_output_tokens
        self._tools: Dict[str, Callable] = {}
        self._tools_lock = threading.Lock()  # v0.8.0: guard _tools mutations
        self._allowed_tools: Optional[set] = None  # None = all allowed
        self._stats = {"executions": 0, "errors": 0, "truncated": 0}
        self._stats_lock = threading.Lock()

    def register(self, name: str, handler: Callable, safe_mode_allowed: bool = True) -> None:
        """Регистрирует инструмент.

        Args:
            name: Имя инструмента.
            handler: Callable, принимающий kwargs, возвращающий str или dict.
            safe_mode_allowed: Можно ли использовать в safe mode.
        """
        with self._tools_lock:
            self._tools[name] = {
                "handler": handler,
                "safe_mode_allowed": safe_mode_allowed,
            }
        logger.debug("TOOL_ENGINE: registered tool '%s'", name)

    def set_allowed_tools(self, tools: Optional[List[str]]) -> None:
        """Устанавливает whitelist инструментов. None = все разрешены."""
        self._allowed_tools = set(tools) if tools else None

    def execute(
        self, tool_name: str, args: Optional[Dict] = None,
        safe_mode: bool = False,
    ) -> ToolResult:
        """Выполняет инструмент с sanitization.

        Args:
            tool_name: Имя инструмента.
            args: Аргументы.
            safe_mode: True → только safe_mode_allowed tools.

        Returns:
            ToolResult с очищенным output.
        """
        with self._stats_lock:
            self._stats["executions"] += 1
        args = args or {}

        # 1. Check tool exists
        if tool_name not in self._tools:
            with self._stats_lock:
                self._stats["errors"] += 1
            return ToolResult(
                success=False, error=f"unknown tool: {tool_name}",
                tool_name=tool_name,
            )

        tool = self._tools[tool_name]

        # 2. Check whitelist
        if self._allowed_tools and tool_name not in self._allowed_tools:
            with self._stats_lock:
                self._stats["errors"] += 1
            return ToolResult(
                success=False, error=f"tool not in whitelist: {tool_name}",
                tool_name=tool_name,
            )

        # 3. Check safe mode
        if safe_mode and not tool.get("safe_mode_allowed", True):
            with self._stats_lock:
                self._stats["errors"] += 1
            return ToolResult(
                success=False, error=f"tool not allowed in safe mode: {tool_name}",
                tool_name=tool_name,
            )

        # 4. Strip control chars from input (NOT full sanitization)
        sanitized_args = self._strip_control_chars(args)

        # 5. Execute
        try:
            raw_output = tool["handler"](**sanitized_args)
            if isinstance(raw_output, dict):
                raw_output = json.dumps(raw_output, ensure_ascii=False, indent=2)
            raw_output = str(raw_output) if raw_output else ""
        except Exception as e:
            with self._stats_lock:
                self._stats["errors"] += 1
            logger.warning("TOOL_ENGINE: error in '%s': %s", tool_name, e, exc_info=True)
            return ToolResult(
                success=False, error=f"Tool '{tool_name}' failed",
                tool_name=tool_name,
            )

        # 6. Format + truncate output
        output, truncated = self._format_output(raw_output)

        if truncated:
            with self._stats_lock:
                self._stats["truncated"] += 1

        return ToolResult(
            success=True, output=output,
            tool_name=tool_name,
            sanitized=True, truncated=truncated,
            raw_length=len(raw_output),
        )

    def _strip_control_chars(self, args: Dict) -> Dict:
        """Strip C0 control characters from input args (recursive).

        NOTE: this does NOT sanitize for path traversal, shell injection,
        or other security concerns — only removes \\x00-\\x08, \\x0b, \\x0c, \\x0e-\\x1f.
        """
        return self._strip_control_chars_value(args)

    def _strip_control_chars_value(self, value: Any) -> Any:
        """Recursively strip control chars from nested structures."""
        if isinstance(value, str):
            return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
        if isinstance(value, dict):
            return {k: self._strip_control_chars_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._strip_control_chars_value(item) for item in value]
        return value

    def _format_output(self, raw: str) -> tuple:
        """Форматирование и обрезка вывода.

        Returns:
            (output, was_truncated)
        """
        # Estimate chars from token limit (using 2.2 chars/token for Russian)
        max_chars = int(self.max_output_tokens * 2.2)

        if len(raw) <= max_chars:
            return raw.strip(), False

        # Truncate with indicator
        truncated = raw[:max_chars].rsplit("\n", 1)[0]  # cut at last newline
        truncated += f"\n\n... [обрезано: показано {len(truncated)}/{len(raw)} символов]"
        return truncated.strip(), True

    def list_tools(self) -> Dict[str, bool]:
        """Список доступных инструментов."""
        return {
            name: info.get("safe_mode_allowed", True)
            for name, info in self._tools.items()
        }

    def get_stats(self) -> Dict[str, Any]:
        with self._stats_lock:
            return dict(self._stats)

    def reset_stats(self):
        with self._stats_lock:
            for k in self._stats:
                self._stats[k] = 0
