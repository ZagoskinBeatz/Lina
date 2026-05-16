"""
Lina — Модуль выполнения системных команд.

Безопасный запуск скриптов и команд через subprocess.
"""

import subprocess
import shlex
import logging
from typing import Optional

from lina.config import config

logger = logging.getLogger("lina.system.executor")


class CommandExecutor:
    """
    Исполнитель системных команд с проверкой безопасности.

    Все команды проходят проверку перед запуском:
    - Блокируются опасные команды (rm -rf /, shutdown и т.д.)
    - Устанавливаются таймауты
    - Ограничивается доступ к директориям
    """

    def __init__(self):
        self.security = config.security
        self.timeout = config.resources.subprocess_timeout

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        capture_stderr: bool = True,
    ) -> dict:
        """
        Выполняет команду в shell и возвращает результат.

        Args:
            command: Команда для выполнения.
            cwd: Рабочая директория.
            timeout: Таймаут в секундах (по умолчанию из конфига).
            capture_stderr: Захватывать stderr.

        Returns:
            dict с ключами: stdout, stderr, returncode, success
        """
        # Проверка безопасности
        if not self.security.is_command_safe(command):
            return {
                "stdout": "",
                "stderr": f"⛔ Команда заблокирована политикой безопасности: {command}",
                "returncode": -1,
                "success": False,
            }

        # Проверяем рабочую директорию
        if cwd and not self.security.is_path_allowed(cwd):
            return {
                "stdout": "",
                "stderr": f"⛔ Доступ к директории запрещён: {cwd}",
                "returncode": -1,
                "success": False,
            }

        effective_timeout = timeout or self.timeout

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                timeout=effective_timeout,
                capture_output=True,
                text=True,
                env=None,  # Наследуем текущее окружение
            )

            return {
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip() if capture_stderr else "",
                "returncode": result.returncode,
                "success": result.returncode == 0,
            }

        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"⏱ Команда превысила таймаут ({effective_timeout} сек): {command}",
                "returncode": -2,
                "success": False,
            }
        except Exception as e:
            logger.error("Command execution failed: %s", e, exc_info=True)
            return {
                "stdout": "",
                "stderr": "❌ Внутренняя ошибка выполнения команды",
                "returncode": -3,
                "success": False,
            }

    def execute_script(
        self, script_path: str, args: str = "", cwd: Optional[str] = None
    ) -> dict:
        """
        Запускает скрипт (Python, Bash и т.д.).

        Args:
            script_path: Путь к скрипту.
            args: Дополнительные аргументы.
            cwd: Рабочая директория.
        """
        if not self.security.is_path_allowed(script_path):
            return {
                "stdout": "",
                "stderr": f"⛔ Доступ к скрипту запрещён: {script_path}",
                "returncode": -1,
                "success": False,
            }

        # Определяем интерпретатор по расширению
        safe_args = " ".join(shlex.quote(a) for a in args.split()) if args.strip() else ""
        if script_path.endswith(".py"):
            command = f"python3 {shlex.quote(script_path)} {safe_args}"
        elif script_path.endswith(".sh"):
            command = f"bash {shlex.quote(script_path)} {safe_args}"
        else:
            command = f"{shlex.quote(script_path)} {safe_args}"

        return self.execute(command, cwd=cwd)
