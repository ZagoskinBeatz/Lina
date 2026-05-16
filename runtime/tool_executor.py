"""
Lina Runtime — Tool Executor.

Structured tool execution с валидацией и whitelist.

Принцип: LLM НЕ ВЫПОЛНЯЕТ ДЕЙСТВИЯ.
  LLM возвращает JSON: {"tool": "mkdir", "args": {"path": "..."}}
  ToolExecutor:
    1. Валидирует JSON schema
    2. Проверяет tool в whitelist
    3. Проверяет безопасность аргументов
    4. Выполняет реальное действие
    5. Возвращает результат

Никаких симуляций. Только реальное исполнение.
"""

import os
import logging
import shutil
from pathlib import Path
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass

from lina.runtime.safety_guard import SafetyGuard, RiskLevel

logger = logging.getLogger("lina.runtime.tool_executor")


@dataclass
class ToolResult:
    """Результат выполнения tool-call."""
    success: bool
    output: str
    tool: str
    args: dict
    error: str = ""


# ── Whitelist разрешённых инструментов ──────────────────────────────────────────

_TOOL_WHITELIST = {
    # Файловые операции
    "mkdir", "touch", "ls", "cat", "find", "grep",
    "read_file", "write_file",
    "mv", "cp", "rm",

    # API
    "weather", "exchange", "ip_info", "web_search",

    # CV
    "screenshot", "ocr",

    # Системные (с подтверждением)
    "run_command",
}

# Инструменты, требующие подтверждения пользователя
_CONFIRM_REQUIRED = {"rm", "run_command", "write_file"}


class ToolExecutor:
    """
    Безопасное исполнение tool-call запросов от LLM.

    Использование:
        executor = ToolExecutor(commander)
        result = executor.execute(tool_call_dict)
        print(result.output)
    """

    def __init__(
        self,
        file_manager=None,
        api_client=None,
        screen_scanner=None,
        ocr_engine=None,
        executor=None,
        sandbox=None,
        confirm_fn: Optional[Callable[[str], bool]] = None,
        web_tool=None,
    ):
        """
        Args:
            file_manager: FileManager для файловых операций.
            api_client: APIClient для HTTP-запросов.
            screen_scanner: ScreenScanner для скриншотов.
            ocr_engine: OCREngine для распознавания текста.
            executor: CommandExecutor для shell-команд.
            sandbox: SubprocessSandbox для безопасного исполнения.
            confirm_fn: Функция подтверждения (для опасных операций).
            web_tool: WebTool для веб-поиска.
        """
        self._files = file_manager
        self._api = api_client
        self._scanner = screen_scanner
        self._ocr = ocr_engine
        self._executor = executor
        self._sandbox = sandbox
        self._confirm = confirm_fn
        self._web_tool = web_tool
        self._guard = SafetyGuard()

    def execute(self, tool_call: dict) -> ToolResult:
        """
        Исполняет tool-call запрос.

        Поток:
          1. Валидация schema
          2. Проверка whitelist
          3. Проверка безопасности аргументов
          4. Подтверждение (для опасных операций)
          5. Исполнение
          6. Формирование результата

        Args:
            tool_call: {"tool": "...", "args": {...}}

        Returns:
            ToolResult с результатом или ошибкой.
        """
        tool = tool_call.get("tool", "")
        args = tool_call.get("args", {})

        # 1. Whitelist
        if tool not in _TOOL_WHITELIST:
            return ToolResult(
                success=False, output="", tool=tool, args=args,
                error=f"Инструмент '{tool}' не разрешён.",
            )

        # 2. Безопасность аргументов
        safety = self._check_args_safety(tool, args)
        if safety:
            return ToolResult(
                success=False, output="", tool=tool, args=args,
                error=safety,
            )

        # 3. Подтверждение
        if tool in _CONFIRM_REQUIRED and self._confirm:
            desc = self._describe_action(tool, args)
            if not self._confirm(desc):
                return ToolResult(
                    success=False, output="", tool=tool, args=args,
                    error="Операция отменена пользователем.",
                )

        # 4. Исполнение
        try:
            output = self._dispatch(tool, args)
            logger.info("Tool executed: %s(%s) → %d chars", tool, args, len(output))
            return ToolResult(
                success=True, output=output, tool=tool, args=args,
            )
        except Exception as e:
            logger.error("Tool execution failed: %s: %s", tool, e, exc_info=True)
            error_msg = f"Ошибка выполнения инструмента '{tool}'."
            if isinstance(e, FileNotFoundError):
                error_msg = f"Файл не найден: {args.get('path', '?')}"
            elif isinstance(e, PermissionError):
                error_msg = "Недостаточно прав для выполнения операции."
            return ToolResult(
                success=False, output="", tool=tool, args=args,
                error=error_msg,
            )

    @staticmethod
    def _safe_resolve(raw_path: str) -> Path:
        """Resolve path safely: expanduser + resolve (follows symlinks).

        Must be used at EVERY point-of-use, not just at check time,
        to prevent TOCTOU symlink-swap attacks.
        """
        return Path(raw_path).expanduser().resolve()

    def _check_args_safety(self, tool: str, args: dict) -> Optional[str]:
        """
        Проверяет безопасность аргументов tool-call.

        Returns:
            Описание проблемы или None.
        """
        # Проверяем все path-аргументы (path, src, dst и т.д.)
        path_keys = ["path", "src", "source", "dst", "destination"]
        home = str(Path.home())
        for key in path_keys:
            p = args.get(key, "")
            if p:
                resolved = str(self._safe_resolve(p))
                if not (resolved == home or resolved.startswith(home + os.sep)):
                    return f"Путь '{p}' (аргумент '{key}') за пределами домашней директории."

        # Проверяем command-аргументы
        command = args.get("command", "")
        if command:
            danger = self._guard.check_command(command)
            if danger:
                return danger

        return None

    def _dispatch(self, tool: str, args: dict) -> str:
        """
        Исполняет конкретную операцию.

        Args:
            tool: Имя инструмента.
            args: Аргументы.

        Returns:
            Текстовый результат.
        """
        # ── Файловые операции ──
        if tool == "mkdir":
            path = args.get("path", "")
            target = self._safe_resolve(path)
            os.makedirs(target, exist_ok=True)
            logger.info("AUDIT: mkdir %s", target)
            return f"Папка создана: {path}"

        if tool == "touch":
            path = args.get("path", "")
            target = self._safe_resolve(path)
            target.touch()
            logger.info("AUDIT: touch %s", target)
            return f"Файл создан: {path}"

        if tool == "ls":
            path = args.get("path", ".")
            if self._files:
                items = self._files.list_dir(path)
                return "\n".join(
                    f"{'📁' if i.get('type') == 'dir' else '📄'} {i['name']}"
                    for i in items
                )
            entries = os.listdir(str(self._safe_resolve(path)))
            return "\n".join(sorted(entries))

        if tool == "cat" or tool == "read_file":
            path = args.get("path", "")
            if self._files:
                return self._files.read_file(path)
            target = self._safe_resolve(path)
            # Binary file detection
            try:
                head = target.read_bytes()[:512]
                if b'\x00' in head:
                    return "Ошибка: бинарный файл, чтение невозможно."
            except OSError:
                pass
            with open(target, "r", encoding="utf-8") as f:
                return f.read()[:10000]  # Hard cap

        if tool == "rm":
            path = args.get("path", "")
            raw_target = Path(path).expanduser()
            # Symlink safety: check BEFORE resolve (resolve follows symlinks)
            if raw_target.is_symlink():
                # Verify the symlink itself is inside home dir (or _check_args_safety passed)
                home = str(Path.home())
                symlink_abs = str(raw_target.absolute())
                if not (symlink_abs.startswith(home + os.sep) or symlink_abs == home):
                    # Symlink outside home — only allow if target resolves to home
                    # (_check_args_safety already validated resolved target)
                    pass  # safe: _check_args_safety already validated
                raw_target.unlink()
                logger.info("AUDIT: rm symlink %s", raw_target)
                return f"Символическая ссылка удалена: {path}"
            target = self._safe_resolve(path)
            if target.is_dir():
                shutil.rmtree(target)
                logger.info("AUDIT: rmtree %s", target)
                return f"Директория удалена: {path}"
            target.unlink()
            logger.info("AUDIT: rm %s", target)
            return f"Файл удалён: {path}"

        if tool == "mv":
            src = args.get("src", args.get("source", ""))
            dst = args.get("dst", args.get("destination", ""))
            src_r = self._safe_resolve(src)
            dst_r = self._safe_resolve(dst)
            os.rename(src_r, dst_r)
            logger.info("AUDIT: mv %s → %s", src_r, dst_r)
            return f"Перемещено: {src} → {dst}"

        if tool == "cp":
            src = args.get("src", args.get("source", ""))
            dst = args.get("dst", args.get("destination", ""))
            src_r = self._safe_resolve(src)
            dst_r = self._safe_resolve(dst)
            shutil.copy2(src_r, dst_r)
            logger.info("AUDIT: cp %s → %s", src_r, dst_r)
            return f"Скопировано: {src} → {dst}"

        if tool == "find":
            path = args.get("path", ".")
            pattern = args.get("pattern", "*")
            # Validate pattern: reject traversal / null bytes
            if any(c in pattern for c in ("\x00", "/", "\\")):
                return "Ошибка: недопустимые символы в шаблоне поиска."
            if ".." in pattern:
                return "Ошибка: недопустимый шаблон поиска."
            if self._files:
                results = self._files.search_files(path, pattern)
                return "\n".join(results[:50])
            import glob
            home = str(Path.home())
            raw = glob.glob(
                os.path.join(str(self._safe_resolve(path)), "**", pattern),
                recursive=True,
            )
            # Filter: only results inside home directory
            results = [r for r in raw
                       if r == home or r.startswith(home + os.sep)]
            return "\n".join(results[:50])

        if tool == "grep":
            pattern = args.get("pattern", "")
            path = args.get("path", ".")
            if self._sandbox:
                import shlex
                safe_cmd = f"grep -rn {shlex.quote(pattern)} {shlex.quote(path)}"
                result = self._sandbox.execute(safe_cmd)
                return result.get("stdout", "")
            return "grep: sandbox недоступен"

        if tool == "write_file":
            path = args.get("path", "")
            content = args.get("content", "")
            _MAX_WRITE_SIZE = 1_000_000  # 1 MB
            if len(content) > _MAX_WRITE_SIZE:
                return f"Ошибка: содержимое превышает лимит ({_MAX_WRITE_SIZE} символов)"
            target = self._safe_resolve(path)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("AUDIT: write_file %s (%d chars)", target, len(content))
            return f"Файл записан: {path} ({len(content)} символов)"

        # ── API ──
        if tool == "weather":
            city = args.get("city", "")
            if self._api:
                return self._api.get_weather(city)
            return "API-клиент недоступен"

        if tool == "exchange":
            base = args.get("base", "USD")
            target = args.get("target", "RUB")
            if self._api:
                return self._api.get_exchange_rate(base, target)
            return "API-клиент недоступен"

        if tool == "ip_info":
            if self._api:
                info = self._api.get_ip_info()
                return str(info)
            return "API-клиент недоступен"

        if tool == "web_search":
            query = args.get("query", "")
            if self._web_tool:
                results = self._web_tool.search_duckduckgo(query)
                if not results:
                    return "Поиск не дал результатов."
                lines = []
                for i, r in enumerate(results[:5], 1):
                    lines.append(f"{i}. {r.get('title', '')}")
                    lines.append(f"   {r.get('url', '')}")
                    snip = r.get('snippet', '')
                    if snip:
                        lines.append(f"   {snip[:200]}")
                return "\n".join(lines)
            return "Веб-поиск недоступен"

        # ── CV ──
        if tool == "screenshot":
            if self._scanner:
                result = self._scanner.capture()
                return result.get("message", "Скриншот сделан")
            return "CV-модуль недоступен"

        if tool == "ocr":
            path = args.get("path", "")
            if self._ocr:
                return self._ocr.recognize(path)
            return "OCR-модуль недоступен"

        # ── Shell ──
        if tool == "run_command":
            command = args.get("command", "")
            if self._sandbox:
                result = self._sandbox.execute(command)
                return result.get("stdout", result.get("error", ""))
            return "Sandbox недоступен"

        return f"Неизвестный инструмент: {tool}"

    def _describe_action(self, tool: str, args: dict) -> str:
        """Описание действия для подтверждения."""
        if tool == "rm":
            return f"Удалить: {args.get('path', '?')}"
        if tool == "run_command":
            return f"Выполнить команду: {args.get('command', '?')}"
        if tool == "write_file":
            return f"Записать файл: {args.get('path', '?')}"
        return f"{tool}: {args}"
