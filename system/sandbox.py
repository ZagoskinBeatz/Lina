"""
Lina — Песочница для subprocess.

Ограничение ресурсов и изоляция дочерних процессов.
"""

import subprocess
import shlex
import signal
import os
import re
from typing import Optional

from lina.config import config
from lina.system.logger import logger


# Жёсткие лимиты для песочницы
MAX_OUTPUT_SIZE = 1024 * 1024  # 1 MB макс. вывод
MAX_PIPE_DEPTH = 5             # Макс. цепочка пайпов


class SubprocessSandbox:
    """
    Расширенная песочница для запуска subprocess.

    Дополнительные меры изоляции поверх SecurityConfig:
    - Ограничение размера вывода
    - Блокировка подозрительных паттернов (fork-бомбы, рекурсия)
    - Логирование всех запусков через аудит
    - Предотвращение цепочек пайпов
    """

    # v0.8.0: regex patterns instead of fragile substring matching
    DANGEROUS_PATTERNS = [
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),  # fork-бомба
        re.compile(r"\|\s*rm\s"),            # пайп в rm
        re.compile(r";\s*rm\s+-rf"),         # ; rm -rf
        re.compile(r"&&\s*rm\s+-rf"),        # && rm -rf
        re.compile(r"curl\s*\|\s*(ba)?sh"),  # pipe-to-shell
        re.compile(r"wget\s+-O\s+-\s*\|\s*(ba)?sh"),
        re.compile(r">\s*/dev/sda"),
        re.compile(r"dd\s+if=/dev/"),
        re.compile(r"\bmkfs\."),
        re.compile(r"\bshred\b"),
        re.compile(r"\bwipefs\b"),
    ]

    def __init__(self):
        self.security = config.security
        self.timeout = config.resources.subprocess_timeout

    def is_safe(self, command: str) -> tuple:
        """
        Расширенная проверка безопасности.

        Returns:
            (is_safe: bool, reason: str)
        """
        cmd_lower = command.lower().strip()

        # 1. Базовая проверка SecurityConfig
        if not self.security.is_command_safe(command):
            reason = "Команда в чёрном списке"
            logger.audit_security("blocked_command", command=command)
            return False, reason

        # 2. Проверка опасных паттернов
        for pat in self.DANGEROUS_PATTERNS:
            if pat.search(cmd_lower):
                reason = f"Обнаружен опасный паттерн: {pat.pattern}"
                logger.audit_security("dangerous_pattern", command=command)
                return False, reason

        # 3. Проверка глубины пайпов
        pipe_count = cmd_lower.count("|")
        if pipe_count > MAX_PIPE_DEPTH:
            reason = f"Слишком много пайпов ({pipe_count} > {MAX_PIPE_DEPTH})"
            logger.audit_security("pipe_depth_exceeded", command=command)
            return False, reason

        return True, ""

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        max_output: int = MAX_OUTPUT_SIZE,
    ) -> dict:
        """
        Безопасный запуск команды в песочнице.

        Args:
            command: Комманда для выполнения.
            cwd: Рабочая директория.
            timeout: Таймаут (сек).
            max_output: Максимальный размер вывода (байт).

        Returns:
            dict: stdout, stderr, returncode, success, sandboxed
        """
        safe, reason = self.is_safe(command)
        if not safe:
            logger.warning(f"Sandbox blocked: {command} — {reason}")
            return {
                "stdout": "",
                "stderr": f"⛔ Sandbox: {reason}",
                "returncode": -1,
                "success": False,
                "sandboxed": True,
            }

        # Проверяем cwd
        if cwd and not self.security.is_path_allowed(cwd):
            logger.audit_security("blocked_cwd", command=f"cwd={cwd}")
            return {
                "stdout": "",
                "stderr": f"⛔ Sandbox: доступ к директории запрещён: {cwd}",
                "returncode": -1,
                "success": False,
                "sandboxed": True,
            }

        effective_timeout = timeout or self.timeout

        logger.debug(f"Sandbox execute: {command}")
        logger.audit("subprocess_execute", user_command=command, details={
            "cwd": cwd or os.getcwd(),
            "timeout": effective_timeout,
        })

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                timeout=effective_timeout,
                capture_output=True,
                text=True,
            )

            stdout = result.stdout
            stderr = result.stderr

            # Обрезаем вывод, если превышает лимит
            if len(stdout) > max_output:
                stdout = stdout[:max_output] + "\n... [вывод обрезан]"
                logger.warning(f"Output truncated for: {command}")

            return {
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
                "returncode": result.returncode,
                "success": result.returncode == 0,
                "sandboxed": True,
            }

        except subprocess.TimeoutExpired:
            logger.warning(f"Subprocess timeout: {command}")
            return {
                "stdout": "",
                "stderr": f"⏱ Sandbox: таймаут ({effective_timeout} сек)",
                "returncode": -2,
                "success": False,
                "sandboxed": True,
            }
        except Exception as e:
            logger.error(f"Subprocess error: {command} — {e}")
            return {
                "stdout": "",
                "stderr": f"❌ Sandbox ошибка: {e}",
                "returncode": -3,
                "success": False,
                "sandboxed": True,
            }
