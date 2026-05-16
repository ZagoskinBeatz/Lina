"""
Lina GUI — Worker Threads.

QThread-based workers for non-blocking intent pipeline execution.
GUI NEVER calls intent pipeline on the main thread.

Rules:
  - Worker emits Qt signals; main thread updates UI
  - No governance bypass — worker calls ChatController.send_user_message()
  - All exceptions caught and forwarded as error signals
"""

from __future__ import annotations

import logging
import traceback
from typing import Optional, Callable

logger = logging.getLogger("lina.gui.workers")


def _get_qt():
    """Import Qt modules lazily."""
    from lina.gui import get_qt_modules
    return get_qt_modules()


# ─── Chat Worker ──────────────────────────────────────────────────────────────

def create_chat_worker_class():
    """Factory: returns ChatWorker class bound to available Qt backend.

    Using factory because Qt metaclass (QObject) must come from the
    same backend (PyQt6 or PySide6) — can't be resolved at import time.
    """
    QtWidgets, QtCore, QtGui = _get_qt()

    class ChatWorker(QtCore.QThread):
        """Runs intent processing off the UI thread.

        CRITICAL: This worker must NEVER call methods that trigger
        Qt UI callbacks (add_message, update_message, etc.).
        It only calls _process_via_intent() which is pure computation.
        All UI updates happen on the main thread via signals.

        Signals:
            finished(str)   — response text on success
            error(str)      — error message on failure
        """

        finished = QtCore.Signal(str) if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal(str)
        error = QtCore.Signal(str) if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal(str)

        def __init__(self, controller, text: str, parent=None):
            super().__init__(parent)
            self._controller = controller
            self._text = text

        def run(self):
            """Execute on worker thread. Never touch Qt widgets here."""
            try:
                # ONLY computation — no UI callbacks.
                # _process_via_intent routes through
                # IntentBridge → governance — full pipeline.
                # Check for /confirm /deny first
                confirm_resp = self._controller._handle_confirm_deny(self._text)
                if confirm_resp is not None:
                    self.finished.emit(confirm_resp)
                    return

                response = self._controller._process_via_intent(self._text)
                self.finished.emit(response)
            except Exception as e:
                logger.error("ChatWorker error: %s\n%s", e, traceback.format_exc())
                self.error.emit(str(e))

    return ChatWorker


# ─── Streaming Chat Worker ────────────────────────────────────────────────────

def create_streaming_worker_class():
    """Factory: returns StreamingChatWorker that emits tokens one-by-one."""
    QtWidgets, QtCore, QtGui = _get_qt()

    class StreamingChatWorker(QtCore.QThread):
        """Streams LLM tokens via signal, supports cancellation.

        Signals:
            token(str)      — single token
            finished(str)   — full response on completion
            error(str)      — error message
        """

        token = QtCore.Signal(str) if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal(str)
        finished = QtCore.Signal(str) if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal(str)
        error = QtCore.Signal(str) if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal(str)

        def __init__(self, stream_handler, text: str, parent=None):
            super().__init__(parent)
            self._stream_handler = stream_handler
            self._text = text
            self._cancel_flag = [False]

        def cancel(self):
            self._cancel_flag[0] = True

        def run(self):
            try:
                tokens = []
                for tok in self._stream_handler(self._text, self._cancel_flag):
                    if self._cancel_flag[0]:
                        break
                    tokens.append(tok)
                    self.token.emit(tok)
                full = "".join(tokens).strip()
                self.finished.emit(full)
            except Exception as e:
                logger.error("StreamingWorker error: %s\n%s", e, traceback.format_exc())
                self.error.emit(str(e))

    return StreamingChatWorker


# ─── Voice Worker ─────────────────────────────────────────────────────────────

def create_voice_worker_class():
    """Factory: returns VoiceWorker that records audio and runs STT."""
    QtWidgets, QtCore, QtGui = _get_qt()

    class VoiceWorker(QtCore.QThread):
        """Records audio via STT, emits recognized text.

        Signals:
            text_recognized(str) — recognized text
            error(str)           — error message
            recording_started()  — recording has begun
        """

        text_recognized = QtCore.Signal(str) if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal(str)
        error = QtCore.Signal(str) if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal(str)
        recording_started = QtCore.Signal() if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal()

        def __init__(self, stt, listen_seconds: float = 10.0, parent=None):
            super().__init__(parent)
            self._stt = stt
            self._listen_seconds = listen_seconds
            self._cancelled = False

        def cancel(self):
            self._cancelled = True
            if self._stt and self._stt.is_listening():
                self._stt.stop_listening()

        def run(self):
            try:
                self.recording_started.emit()
                text = self._stt.listen_for(self._listen_seconds)
                if self._cancelled:
                    return
                if text:
                    if self._stt.is_cancel_word(text):
                        return  # user said cancel word
                    self.text_recognized.emit(text)
                else:
                    self.error.emit("Не удалось распознать речь")
            except Exception as e:
                logger.error("VoiceWorker error: %s\n%s", e, traceback.format_exc())
                self.error.emit(str(e))

    return VoiceWorker


# ─── Confirmation Worker ──────────────────────────────────────────────────────

def create_confirm_worker_class():
    """Factory: returns ConfirmWorker class."""
    QtWidgets, QtCore, QtGui = _get_qt()

    class ConfirmWorker(QtCore.QThread):
        """Resolves a confirmation request off the UI thread.

        Signals:
            finished(str)  — result text
            error(str)     — error message
        """

        finished = QtCore.Signal(str) if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal(str)
        error = QtCore.Signal(str) if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal(str)

        def __init__(self, escalation_id: str, approved: bool, parent=None):
            super().__init__(parent)
            self._esc_id = escalation_id
            self._approved = approved

        def run(self):
            try:
                from lina.governance.confirmation import get_confirmation_handler
                handler = get_confirmation_handler()
                resolved = handler.resolve(self._esc_id, self._approved)
                if self._approved:
                    if resolved:
                        self.finished.emit("✅ Подтверждено и выполнено.")
                    else:
                        self.finished.emit(
                            "⚠ Подтверждение не удалось (ID не найден или истёк).")
                else:
                    self.finished.emit("🚫 Операция отклонена.")
            except ImportError:
                self.error.emit("⚠ Обработчик подтверждений недоступен.")
            except Exception as e:
                logger.error("ConfirmWorker error: %s", e)
                self.error.emit(f"❌ Ошибка подтверждения: {e}")

    return ConfirmWorker


# ─── Status Poller ────────────────────────────────────────────────────────────

def create_status_poller_class():
    """Factory: returns StatusPoller — periodic status updater."""
    QtWidgets, QtCore, QtGui = _get_qt()

    class StatusPoller(QtCore.QObject):
        """Periodically emits system status for the status bar.

        Uses QTimer (runs on main thread, lightweight).
        """

        status_updated = QtCore.Signal(dict) if hasattr(QtCore, 'Signal') else QtCore.pyqtSignal(dict)

        def __init__(self, interval_ms: int = 5000, parent=None):
            super().__init__(parent)
            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(interval_ms)
            self._timer.timeout.connect(self._poll)

        def start(self):
            self._timer.start()

        def stop(self):
            self._timer.stop()

        def _poll(self):
            """Collect lightweight status info."""
            try:
                import os
                import psutil
                proc = psutil.Process(os.getpid())
                mem = proc.memory_info().rss / (1024 * 1024)
                status = {
                    "ram_mb": round(mem, 1),
                    "cpu_percent": proc.cpu_percent(interval=0),
                }
            except ImportError:
                status = {"ram_mb": 0, "cpu_percent": 0}
            except Exception:
                status = {"ram_mb": 0, "cpu_percent": 0}

            self.status_updated.emit(status)

    return StatusPoller
