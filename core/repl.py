"""
Lina — REPL модуль (интерактивный цикл ввода-вывода).

Отделяет интерактивный цикл (read-eval-print loop) от логики
запуска и инициализации. Использует SafePrinter для безопасного
вывода, совместимого с fish/bash/zsh.

Phase 3: REPL routes ALL input through IntentBridge → governance.
Direct commander.process() calls are PROHIBITED.

Архитектура:
  - REPLSession: управляет одной сессией интерактивного взаимодействия
  - Обрабатывает: ввод → IntentBridge → governance → вывод
  - Корректно завершает: EOF, KeyboardInterrupt, __EXIT__
"""

import sys
import logging
from typing import Optional

from lina.core.output import SafePrinter, get_printer
from lina.shell.commander import Commander

logger = logging.getLogger("lina.core.repl")


class REPLSession:
    """
    Интерактивная REPL-сессия Lina.

    Управляет главным циклом ввода-вывода.
    Использует SafePrinter для всех операций вывода.

    Атрибуты:
        commander: Командный процессор.
        printer: Безопасный принтер.
        web_server: Опциональный веб-сервер (для shutdown).
    """

    def __init__(
        self,
        commander: Commander,
        printer: Optional[SafePrinter] = None,
        web_server=None,
        pipeline_handler=None,
    ):
        """
        Args:
            commander: Экземпляр Commander для обработки команд.
            printer: SafePrinter (None = глобальный).
            web_server: Веб-сервер для корректного останова.
            pipeline_handler: LLM/pipeline callback для chat passthrough.
        """
        self.commander = commander
        self.printer = printer or get_printer()
        self.web_server = web_server
        self._pipeline_handler = pipeline_handler
        self._running = False

    @property
    def is_running(self) -> bool:
        """True если REPL-цикл активен."""
        return self._running

    # ── Governance routing (Phase 3) ─────────────────────────────

    def _route_via_governance(self, text: str) -> str:
        """
        Route user input through IntentBridge → governance pipeline.

        Phase 3: REPL NEVER calls commander.process() for user input.
        Phase 4: ResponseFormatter for human-friendly output.

        Returns:
            Human-readable response text.
        """
        # Phase 4: Edge case — help command
        try:
            from lina.core.response_ux import get_response_formatter
            fmt = get_response_formatter()
            if fmt.is_help_command(text):
                return fmt.format_help()
        except ImportError:
            pass

        try:
            from lina.intent.bridge import get_intent_bridge
            from lina.intent.types import IntentStatus
            from lina.core.response_ux import get_response_formatter  # noqa: F811

            fmt = get_response_formatter()
            bridge = get_intent_bridge()
            result = bridge.from_text(
                text,
                source="cli",
                pipeline_handler=self._pipeline_handler,
            )

            # Handle NEEDS_CONFIRM — interactive CLI prompt
            if result.status == IntentStatus.NEEDS_CONFIRM:
                esc_id = result.escalation_id
                self.printer.print(
                    f"\n{fmt.format_result(result, domain=getattr(result, 'metadata', {}).get('domain', ''))}")
                try:
                    answer = input(
                        f"  /confirm {esc_id} или /deny {esc_id}: ").strip().lower()
                    if answer in ("y", "yes", "да", "д",
                                  f"/confirm {esc_id}", "/confirm"):
                        try:
                            from lina.governance.confirmation import (
                                get_confirmation_handler,
                            )
                            handler = get_confirmation_handler()
                            handler.set_cli_mode()
                            confirmed = handler.resolve(esc_id, True)
                            if confirmed:
                                return "✅ Подтверждено и выполнено."
                            return "⚠ Подтверждение не удалось."
                        except Exception as e:
                            logger.error("Confirmation handling failed: %s", e, exc_info=True)
                            return "⚠ Обработчик подтверждения недоступен."
                    else:
                        return fmt.format_cancel()
                except (EOFError, KeyboardInterrupt):
                    return fmt.format_cancel()

            # SUCCESS, DENIED, FAILED, etc — format via UX layer
            domain = getattr(result, 'metadata', {}).get('domain', '')
            action = getattr(result, 'metadata', {}).get('action', '')
            formatted = fmt.format_result(result, domain=domain, action=action)

            # Chat/query fallback — use pipeline handler directly
            if (result.status in (IntentStatus.CHAT_RESPONSE,
                                  IntentStatus.NOT_FOUND)
                    and not result.response_text):
                if self._pipeline_handler:
                    return self._pipeline_handler(text)
                return formatted

            return formatted

        except ImportError:
            # IntentBridge not available — fail-closed: deny request
            logger.error(
                "IntentBridge not available — request denied (fail-closed)"
            )
            return "⚠ Система маршрутизации недоступна. Запрос отклонён."

    # ── Legacy exit commands ─────────────────────────────────────

    _EXIT_COMMANDS = frozenset({
        "выход", "exit", "quit", "q", "/выход", "/exit", "/quit",
    })

    def run(self) -> None:
        """
        Запускает главный REPL-цикл.

        Phase 3: All input routed through IntentBridge → governance.

        Продолжает до:
          - Команда /выход (__EXIT__)
          - EOFError (Ctrl+D)
          - KeyboardInterrupt (Ctrl+C)
        """
        self._running = True
        prompt = self.printer.prompt_text("Lina")

        logger.info("REPL session started (governance-routed)")

        while self._running:
            try:
                user_input = input(prompt).strip()

                if not user_input:
                    # Проверка автовыгрузки при простое
                    llm = getattr(self.commander, 'llm', None)
                    if llm is not None:
                        llm.check_idle_unload()
                    continue

                # Check exit commands BEFORE governance
                if user_input.lower() in self._EXIT_COMMANDS:
                    self._shutdown()
                    break

                # Сбрасываем таймер auto-unload при ЛЮБОЙ активности
                llm = getattr(self.commander, 'llm', None)
                if llm is not None and llm.is_loaded:
                    llm._active.touch()

                # ── Phase 3: Route through governance ──
                response = self._route_via_governance(user_input)

                # Автовыгрузка после запроса
                llm = getattr(self.commander, 'llm', None)
                if llm is not None:
                    llm.check_idle_unload()

                # Legacy __EXIT__ compat (commander may still return it)
                if response == "__EXIT__":
                    self._shutdown()
                    break

                # Вывод ответа
                if response:
                    self.printer.print(f"\n{response}")

            except EOFError:
                self.printer.print("\n")
                self._shutdown(message="До встречи!")
                break

            except KeyboardInterrupt:
                self.printer.print("\n")
                self._shutdown(message="Прервано. До встречи!")
                break

        logger.info("REPL session ended")

    def run_oneshot(self, query: str) -> str:
        """
        Обрабатывает одноразовый запрос (без REPL-цикла).

        Phase 3: routed through governance pipeline.

        Args:
            query: Текст запроса.

        Returns:
            Ответ от governance pipeline.
        """
        logger.info("Oneshot query (governance-routed): %s", query[:80])

        response = self._route_via_governance(query)

        if response and response != "__EXIT__":
            self.printer.print(response)

        return response or ""

    def _shutdown(self, message: str = "До встречи! Lina завершает работу.") -> None:
        """
        Корректное завершение сессии.

        Выгружает модель, останавливает watchdog, веб-сервер.

        Args:
            message: Прощальное сообщение.
        """
        self._running = False
        self.printer.print(f"\n{message}")

        logger.info("Shutting down REPL session")

        # Выгружаем модель
        try:
            if self.commander.llm.is_loaded:
                self.commander.llm.unload()
        except Exception as e:
            logger.error("Error unloading LLM: %s", e)

        # Останавливаем watchdog
        try:
            self.commander.monitor.stop_watchdog()
        except Exception as e:
            logger.error("Error stopping watchdog: %s", e)

        # Останавливаем веб-сервер
        if self.web_server:
            try:
                self.web_server.stop()
            except Exception as e:
                logger.error("Error stopping web server: %s", e)

    def stop(self) -> None:
        """Останавливает REPL-цикл (для вызова из другого потока)."""
        self._running = False
