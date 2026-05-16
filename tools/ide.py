"""
Lina — Интеграция с IDE / компилятором / линтером.

Возможности:
  - Запуск скриптов (Python, Bash, Node.js)
  - Линтинг кода (pylint, flake8, mypy, shellcheck)
  - Git-операции
  - Форматирование кода (black, isort)
"""

import subprocess
import shlex
import logging
import os
from pathlib import Path
from typing import Optional

from lina.config import config
from lina.system.logger import logger

_log = logging.getLogger(__name__)


class IDETool:
    """
    Инструменты разработки: запуск кода, линтинг, git.

    Все запуски через subprocess с проверкой безопасности.
    """

    def __init__(self):
        self.security = config.security
        self.timeout = config.resources.subprocess_timeout
        self._available_tools: Optional[dict] = None

    def detect_tools(self) -> dict:
        """Обнаруживает доступные инструменты разработки."""
        if self._available_tools is not None:
            return self._available_tools

        tools = {}
        checks = {
            "python": "python3 --version",
            "pip": "pip3 --version",
            "git": "git --version",
            "pylint": "pylint --version",
            "flake8": "flake8 --version",
            "mypy": "mypy --version",
            "black": "black --version",
            "isort": "isort --version-number",
            "node": "node --version",
            "npm": "npm --version",
            "shellcheck": "shellcheck --version",
            "gcc": "gcc --version",
            "make": "make --version",
        }

        for name, cmd in checks.items():
            try:
                r = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=5
                )
                tools[name] = r.returncode == 0
            except Exception:
                tools[name] = False

        self._available_tools = tools
        logger.info(f"IDE tools detected: {[k for k, v in tools.items() if v]}")
        return tools

    def run_script(self, filepath: str, args: str = "") -> dict:
        """
        Запускает скрипт и возвращает вывод.

        Args:
            filepath: Путь к файлу.
            args: Дополнительные аргументы.
        """
        path = Path(filepath).resolve()

        if not path.exists():
            return {"success": False, "error": f"Файл не найден: {filepath}"}

        if not self.security.is_path_allowed(str(path)):
            return {"success": False, "error": f"Доступ запрещён: {filepath}"}

        ext = path.suffix.lower()
        interpreters = {
            ".py": "python3",
            ".sh": "bash",
            ".js": "node",
            ".ts": "npx ts-node",
            ".rb": "ruby",
            ".pl": "perl",
        }

        interp = interpreters.get(ext, "")
        if interp:
            cmd_parts = shlex.split(interp) + [str(path)]
        else:
            cmd_parts = [str(path)]
        if args:
            cmd_parts.extend(shlex.split(args))

        logger.audit("ide_run", details={"file": str(path), "interpreter": interp})

        try:
            result = subprocess.run(
                cmd_parts, shell=False, capture_output=True, text=True,
                timeout=self.timeout, cwd=str(path.parent),
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Таймаут ({self.timeout} сек)"}
        except Exception as e:
            _log.error("run_script error: %s", e, exc_info=True)
            return {"success": False, "error": "Внутренняя ошибка при запуске скрипта."}

    def lint_python(self, filepath: str) -> dict:
        """Запускает pylint/flake8 на Python-файле."""
        tools = self.detect_tools()
        results = {}

        if tools.get("flake8"):
            try:
                r = subprocess.run(
                    ["flake8", "--max-line-length=120", filepath],
                    shell=False, capture_output=True, text=True, timeout=30
                )
                results["flake8"] = {
                    "issues": r.stdout.strip().split("\n") if r.stdout.strip() else [],
                    "clean": r.returncode == 0,
                }
            except Exception as e:
                _log.error("flake8 error: %s", e)
                results["flake8"] = {"error": "Ошибка запуска flake8."}

        if tools.get("pylint"):
            try:
                r = subprocess.run(
                    ["pylint", "--disable=C0114,C0115,C0116", "--score=no", filepath],
                    shell=False, capture_output=True, text=True, timeout=30
                )
                results["pylint"] = {
                    "issues": r.stdout.strip().split("\n") if r.stdout.strip() else [],
                    "clean": r.returncode == 0,
                }
            except Exception as e:
                _log.error("pylint error: %s", e)
                results["pylint"] = {"error": "Ошибка запуска pylint."}

        if not results:
            return {"success": False, "error": "Линтеры не найдены (flake8, pylint)"}

        return {"success": True, "results": results}

    def git_status(self, repo_path: str = ".") -> dict:
        """Git status для репозитория."""
        try:
            r = subprocess.run(
                ["git", "status", "--short"],
                shell=False, capture_output=True, text=True,
                timeout=10, cwd=repo_path
            )
            return {
                "success": r.returncode == 0,
                "output": r.stdout.strip(),
                "is_repo": r.returncode == 0,
            }
        except Exception as e:
            _log.error("git_status error: %s", e)
            return {"success": False, "error": "Ошибка выполнения git status."}

    def git_log(self, repo_path: str = ".", n: int = 10) -> dict:
        """Последние N коммитов."""
        n = max(1, min(int(n), 500))
        try:
            r = subprocess.run(
                ["git", "log", "--oneline", "-n", str(n)],
                shell=False, capture_output=True, text=True,
                timeout=10, cwd=repo_path
            )
            return {
                "success": r.returncode == 0,
                "commits": r.stdout.strip().split("\n") if r.stdout.strip() else [],
            }
        except Exception as e:
            _log.error("git_log error: %s", e)
            return {"success": False, "error": "Ошибка выполнения git log."}

    def format_tools_report(self) -> str:
        """Форматирует список доступных инструментов."""
        tools = self.detect_tools()
        lines = ["🔧 Инструменты разработки:"]
        for name, available in sorted(tools.items()):
            icon = "✅" if available else "❌"
            lines.append(f"  {icon} {name}")
        return "\n".join(lines)
