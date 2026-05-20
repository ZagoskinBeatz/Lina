"""
Lina GUI — Встроенный терминал и панель подтверждения команд.

Модули:
  - CommandActionBar  — панель с командой + кнопки [Выполнить] [Закрыть]
  - EmbeddedTerminal  — PTY-терминал внутри GUI для выполнения команд

Используется для:
  - Установки пакетов (sudo pacman -S ...)
  - Любых команд, требующих интерактивного ввода (sudo password)
  - Отображения реального вывода процесса в реальном времени

Безопасность:
  - Команда показывается пользователю ДО выполнения
  - Пользователь явно нажимает «Выполнить»
  - Можно остановить процесс в любой момент
"""

from __future__ import annotations

import logging
import os
import fcntl
import pty
import re
import signal
import struct
import subprocess
import termios
from typing import Optional

logger = logging.getLogger("lina.gui.terminal")

# ANSI escape stripper
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\x1b\[[\d;]*m|\r")

# Pattern to detect executable install commands in bot responses
_INSTALL_CMD_RE = re.compile(
    r"\[(?:PACMAN|APT|DNF|ZYPPER|FLATPAK|SNAP|AUR|PARU|YAY)\]\s+"
    r"((?:sudo\s+)?(?:pacman|apt|apt-get|dnf|zypper|flatpak|snap|paru|yay)\s+\S+[^\n]*)",
    re.IGNORECASE,
)


def extract_executable_commands(text: str) -> list[str]:
    """Extract install commands from bot response text.

    Looks for [PACMAN] sudo pacman -S ... patterns.
    Returns list of raw shell commands (stripped of source annotations).
    """
    commands = []
    for match in _INSTALL_CMD_RE.finditer(text):
        cmd = match.group(1).strip()
        # Remove trailing note like " — описание"
        cmd = re.sub(r"\s*—\s+.*$", "", cmd)
        # Remove trailing source annotation like (extra), (core)
        cmd = re.sub(r"\s*\([a-z0-9_-]+\)\s*$", "", cmd, flags=re.I)
        if cmd:
            commands.append(cmd.strip())
    return commands


def _get_qt():
    from lina.gui import get_qt_modules
    return get_qt_modules()


# ═════════════════════════════════════════════════════════════════════════════
# CommandActionBar — inline bar showing a detected command
# ═════════════════════════════════════════════════════════════════════════════

def create_command_action_bar_class():
    """Factory: returns CommandActionBar class with correct Qt backend."""
    QtWidgets, QtCore, QtGui = _get_qt()

    Signal = QtCore.Signal if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal

    class CommandActionBar(QtWidgets.QWidget):
        """Inline bar: shows command with Execute / Dismiss buttons.

        ┌────────────────────────────────────────────────────┐
        │ 📦 sudo pacman -S firefox   [▶ Выполнить] [✕]     │
        └────────────────────────────────────────────────────┘
        """

        execute_requested = Signal(str)  # command string
        dismissed = Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            self.setObjectName("commandBar")
            self._current_command: Optional[str] = None
            self._all_commands: list[str] = []
            self._setup_ui()
            self.hide()

        def _setup_ui(self):
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(12, 6, 12, 6)
            layout.setSpacing(8)

            self._icon = QtWidgets.QLabel("📦")
            self._icon.setFixedWidth(24)
            layout.addWidget(self._icon)

            self._label = QtWidgets.QLabel("")
            self._label.setObjectName("commandLabel")
            self._label.setWordWrap(False)
            font = self._label.font()
            font.setFamily("JetBrains Mono, Fira Code, monospace")
            font.setPointSize(11)
            self._label.setFont(font)
            layout.addWidget(self._label, stretch=1)

            # Command selector (if multiple)
            self._selector = QtWidgets.QComboBox()
            self._selector.setObjectName("commandSelector")
            self._selector.setMaximumWidth(200)
            self._selector.currentIndexChanged.connect(self._on_selection_changed)
            self._selector.hide()
            layout.addWidget(self._selector)

            self._exec_btn = QtWidgets.QPushButton("▶ Выполнить")
            self._exec_btn.setObjectName("execButton")
            self._exec_btn.setFixedWidth(120)
            self._exec_btn.setCursor(
                QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            self._exec_btn.clicked.connect(self._on_execute)
            layout.addWidget(self._exec_btn)

            self._close_btn = QtWidgets.QPushButton("✕")
            self._close_btn.setObjectName("commandCloseBtn")
            self._close_btn.setFixedWidth(32)
            self._close_btn.setCursor(
                QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            self._close_btn.clicked.connect(self._on_dismiss)
            layout.addWidget(self._close_btn)

        def show_commands(self, commands: list[str]):
            """Show bar with one or more executable commands."""
            if not commands:
                return
            self._all_commands = commands
            self._current_command = commands[0]

            if len(commands) == 1:
                self._label.setText(commands[0])
                self._selector.hide()
            else:
                self._label.setText("Варианты:")
                self._selector.clear()
                for i, cmd in enumerate(commands):
                    self._selector.addItem(f"{i + 1}. {cmd}", cmd)
                self._selector.show()

            self.show()

        def _on_selection_changed(self, index: int):
            if 0 <= index < len(self._all_commands):
                self._current_command = self._all_commands[index]

        def _on_execute(self):
            if self._current_command:
                self.execute_requested.emit(self._current_command)
            self.hide()

        def _on_dismiss(self):
            self._current_command = None
            self._all_commands.clear()
            self.dismissed.emit()
            self.hide()

    return CommandActionBar


# ═════════════════════════════════════════════════════════════════════════════
# EmbeddedTerminal — PTY-based terminal widget
# ═════════════════════════════════════════════════════════════════════════════

def create_embedded_terminal_class():
    """Factory: returns EmbeddedTerminal class with correct Qt backend."""
    QtWidgets, QtCore, QtGui = _get_qt()

    Signal = QtCore.Signal if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal

    class EmbeddedTerminal(QtWidgets.QWidget):
        """Embedded PTY terminal for running commands inside Lina.

        Layout:
        ┌──────────────────────────────────────────────────┐
        │ ▶ sudo pacman -S firefox          [■ Стоп] [✕]  │  ← header
        ├──────────────────────────────────────────────────┤
        │ :: Synchronizing package databases...            │
        │ resolving dependencies...                        │  ← output area
        │ [sudo] password for user: █                      │
        ├──────────────────────────────────────────────────┤
        │ [password input ••••••]               [Enter]    │  ← input bar
        └──────────────────────────────────────────────────┘
        """

        command_finished = Signal(int, str, str)  # exit_code, command, output
        command_started = Signal(str)  # command

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            self.setObjectName("embeddedTerminal")
            self._master_fd: Optional[int] = None
            self._child_pid: Optional[int] = None
            self._proc: Optional[subprocess.Popen] = None
            self._notifier = None  # QSocketNotifier (optional)
            self._timer = None     # QTimer (optional)
            self._current_command: str = ""
            self._output_buffer: str = ""
            self._is_password_prompt = False
            self._setup_ui()
            self.hide()

        def _setup_ui(self):
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # ── Header ──
            header = QtWidgets.QWidget()
            header.setObjectName("terminalHeader")
            header_layout = QtWidgets.QHBoxLayout(header)
            header_layout.setContentsMargins(12, 6, 12, 6)

            self._header_icon = QtWidgets.QLabel("▶")
            self._header_icon.setFixedWidth(20)
            header_layout.addWidget(self._header_icon)

            self._header_label = QtWidgets.QLabel("")
            self._header_label.setObjectName("terminalHeaderLabel")
            font = self._header_label.font()
            font.setFamily("JetBrains Mono, Fira Code, monospace")
            font.setPointSize(10)
            self._header_label.setFont(font)
            header_layout.addWidget(self._header_label, stretch=1)

            self._stop_btn = QtWidgets.QPushButton("■ Стоп")
            self._stop_btn.setObjectName("terminalStopBtn")
            self._stop_btn.setFixedWidth(80)
            self._stop_btn.clicked.connect(self.stop)
            header_layout.addWidget(self._stop_btn)

            self._close_btn = QtWidgets.QPushButton("✕")
            self._close_btn.setObjectName("terminalCloseBtn")
            self._close_btn.setFixedWidth(32)
            self._close_btn.clicked.connect(self.close_terminal)
            header_layout.addWidget(self._close_btn)

            layout.addWidget(header)

            # ── Output area ──
            self._output = QtWidgets.QPlainTextEdit()
            self._output.setObjectName("terminalOutput")
            self._output.setReadOnly(True)
            self._output.setMaximumBlockCount(5000)
            font = QtGui.QFont("JetBrains Mono, Fira Code, monospace", 11)
            self._output.setFont(font)
            self._output.setMinimumHeight(120)
            self._output.setMaximumHeight(300)
            layout.addWidget(self._output)

            # ── Input bar ──
            input_widget = QtWidgets.QWidget()
            input_widget.setObjectName("terminalInputBar")
            input_layout = QtWidgets.QHBoxLayout(input_widget)
            input_layout.setContentsMargins(12, 4, 12, 6)

            self._prompt_label = QtWidgets.QLabel("►")
            self._prompt_label.setFixedWidth(20)
            input_layout.addWidget(self._prompt_label)

            self._input = QtWidgets.QLineEdit()
            self._input.setObjectName("terminalInput")
            self._input.setPlaceholderText("Ввод (пароль sudo, ответ на вопрос)…")
            self._input.returnPressed.connect(self._send_input)
            input_layout.addWidget(self._input, stretch=1)

            self._send_btn = QtWidgets.QPushButton("⏎")
            self._send_btn.setObjectName("terminalSendBtn")
            self._send_btn.setFixedWidth(40)
            self._send_btn.clicked.connect(self._send_input)
            input_layout.addWidget(self._send_btn)

            layout.addWidget(input_widget)

            self.setFixedHeight(0)  # collapsed initially

        def run_command(self, command: str):
            """Execute command in PTY — shows real-time output."""
            self._run_internal(command)

        def _run_internal(self, command: str):
            # Clean up previous
            self._cleanup()

            self._current_command = command
            self._output_buffer = ""
            self._output.clear()
            self._header_label.setText(command)
            self._header_icon.setText("▶")
            self._stop_btn.setEnabled(True)
            self._input.clear()
            self._input.setEnabled(True)
            self._input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Normal)
            self._is_password_prompt = False

            self._output.appendPlainText(f"$ {command}\n")

            try:
                # Create PTY pair
                master, slave = pty.openpty()

                # Set window size on PTY so pacman etc. get reasonable width
                winsize = struct.pack('HHHH', 24, 120, 0, 0)
                fcntl.ioctl(slave, termios.TIOCSWINSZ, winsize)

                # Start subprocess attached to PTY
                self._proc = subprocess.Popen(
                    ["bash", "-c", command],
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                    start_new_session=True,
                    env={**os.environ, "TERM": "dumb", "LANG": "C.UTF-8"},
                )
                os.close(slave)  # parent doesn't need slave
                self._master_fd = master

                # Set master to non-blocking
                flags = fcntl.fcntl(master, fcntl.F_GETFL)
                fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)

                # Password injection отключена в текущей архитектуре:
                # sudo-credentials валидируются в InstallWorkflow заранее
                # через `sudo -v -S` и кэшируются в timestamp. PTY-команды
                # используют `sudo -n` и не просят пароль вообще.

                # Poll with QTimer (more reliable than QSocketNotifier for PTY)
                self._timer = QtCore.QTimer()
                self._timer.setInterval(50)  # 20 fps
                self._timer.timeout.connect(self._poll_output)
                self._timer.start()

                # Animate open
                self._animate_height(350)
                self.show()
                self.command_started.emit(command)

            except Exception as e:
                logger.error("Failed to start terminal: %s", e)
                self._output.appendPlainText(f"\n❌ Ошибка запуска: {e}")
                self._stop_btn.setEnabled(False)

        def _poll_output(self):
            """Read available data from PTY master fd."""
            if self._master_fd is None:
                return

            try:
                data = os.read(self._master_fd, 4096)
                if data:
                    text = data.decode("utf-8", errors="replace")
                    text = _ANSI_RE.sub("", text)  # strip ANSI
                    self._output_buffer += text
                    self._output.moveCursor(
                        QtGui.QTextCursor.MoveOperation.End)
                    self._output.insertPlainText(text)
                    self._output.moveCursor(
                        QtGui.QTextCursor.MoveOperation.End)

                    # Detect password prompt
                    lower = text.lower()
                    if "password" in lower or "пароль" in lower:
                        # Активируем UI password-input. В install workflow
                        # этот код не сработает — там команды идут под
                        # `sudo -n` и пароль не запрашивается. Но если
                        # пользователь сам выполнил `sudo …` через
                        # CommandActionBar — нужно дать ему ввести.
                        self._is_password_prompt = True
                        self._input.setEchoMode(
                            QtWidgets.QLineEdit.EchoMode.Password)
                        self._input.setPlaceholderText("Введите пароль…")
                        self._input.setFocus()
                    elif "[y/n]" in lower or "[д/н]" in lower:
                        self._input.setEchoMode(
                            QtWidgets.QLineEdit.EchoMode.Normal)
                        self._input.setPlaceholderText("y/n…")
                        self._input.setFocus()

            except OSError:
                pass  # EAGAIN on non-blocking read

            # Check if process finished
            if self._proc is not None:
                retcode = self._proc.poll()
                if retcode is not None:
                    # Drain remaining output
                    try:
                        while True:
                            data = os.read(self._master_fd, 4096)
                            if not data:
                                break
                            text = data.decode("utf-8", errors="replace")
                            text = _ANSI_RE.sub("", text)
                            self._output.moveCursor(
                                QtGui.QTextCursor.MoveOperation.End)
                            self._output.insertPlainText(text)
                    except OSError:
                        pass

                    self._on_process_finished(retcode)

        def _on_process_finished(self, exit_code: int):
            """Process has exited.

            Idempotent: if _proc is already None (already finished) —
            no-op. Это защищает от двойной эмиссии command_finished
            (например, _cleanup → stop → _on_process_finished, а потом
            poll-loop тоже зовёт _on_process_finished).
            """
            if self._proc is None:
                return
            # Mark finished BEFORE emitting, чтобы рекурсивные вызовы
            # (через subscribers) видели finished state.
            self._proc = None

            if self._timer:
                self._timer.stop()

            self._stop_btn.setEnabled(False)
            self._input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Normal)
            self._is_password_prompt = False

            if exit_code == 0:
                self._header_icon.setText("✅")
                self._output.appendPlainText(
                    "\n━━━ Команда выполнена успешно ━━━")
            else:
                self._header_icon.setText("❌")
                self._output.appendPlainText(
                    f"\n━━━ Ошибка (код {exit_code}) ━━━")

            self._input.setPlaceholderText("Команда завершена")
            self._input.setEnabled(False)

            self.command_finished.emit(exit_code, self._current_command, self._output_buffer)
            self._close_fd()

        def _send_input(self):
            """Send user input to the PTY (for sudo password, y/n, etc.)."""
            text = self._input.text()
            if self._master_fd is None or not text and not self._is_password_prompt:
                return

            try:
                os.write(self._master_fd, (text + "\n").encode())
            except OSError as e:
                logger.error("Failed to write to PTY: %s", e)

            self._input.clear()
            # Reset to normal mode after password
            if self._is_password_prompt:
                self._is_password_prompt = False
                self._input.setEchoMode(
                    QtWidgets.QLineEdit.EchoMode.Normal)
                self._input.setPlaceholderText(
                    "Ввод (пароль sudo, ответ на вопрос)…")

        def stop(self):
            """Kill the running process and emit command_finished
            so subscribers (like InstallWorkflow) don't wait forever.

            Idempotent: if no process is running — no-op.
            """
            if self._proc is None:
                return
            still_running = self._proc.poll() is None
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                self._proc.kill()
            except (ProcessLookupError, OSError):
                pass
            if still_running:
                self._output.appendPlainText("\n⚠ Процесс остановлен")
                # Emit synthetic finished event with code -1 — subscribers
                # treat it as failure and can re-queue or fail clean.
                self._on_process_finished(-1)

        def close_terminal(self):
            """Stop process if running, clean up, hide widget."""
            if self._proc is not None and self._proc.poll() is None:
                self.stop()
            self._cleanup()
            self._animate_height(0)
            QtCore.QTimer.singleShot(300, self.hide)

        def _animate_height(self, target: int):
            """Smooth animation for terminal panel height."""
            try:
                anim = QtCore.QPropertyAnimation(self, b"maximumHeight")
                anim.setDuration(250)
                anim.setStartValue(self.height())
                anim.setEndValue(target)
                anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
                # Keep reference so it doesn't get GC'd
                self._anim = anim
                if target > 0:
                    self.setFixedHeight(self.height() or 1)
                    self.setMinimumHeight(0)
                    self.setMaximumHeight(16777215)
                anim.start()
            except Exception:
                # Fallback: just set height directly
                if target > 0:
                    self.setFixedHeight(target)
                else:
                    self.setFixedHeight(0)

        def _close_fd(self):
            """Close the master fd if open."""
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None

        def _cleanup(self):
            """Full cleanup of PTY and process.

            If a process is still running we MUST notify subscribers via
            command_finished — иначе они ждут вечно. _on_process_finished
            эмитит сигнал и сам зовёт _close_fd().
            """
            if self._timer:
                self._timer.stop()
                self._timer = None

            had_running_proc = (
                self._proc is not None
                and self._proc.poll() is None
            )

            if had_running_proc:
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    self._proc.kill()
                except (ProcessLookupError, OSError):
                    pass
                # Emit synthetic finished BEFORE clearing _proc, so
                # subscribers see the right command/output.
                try:
                    self._on_process_finished(-1)
                except Exception as e:
                    logger.error("cleanup _on_process_finished failed: %s", e)

            # _on_process_finished above already calls _close_fd().
            # If proc was already dead — explicit cleanup of fd/proc.
            if not had_running_proc:
                self._close_fd()
                if self._proc is not None:
                    self._proc = None

            self._input.setEnabled(True)
            self._input.clear()
            self._is_password_prompt = False

    return EmbeddedTerminal
