"""
Lina GUI — D-Bus Bridge.

Exposes Lina GUI over the session D-Bus:
  - org.lina.Assistant.ToggleWindow  — show/hide main window
  - org.lina.Assistant.Query(text)   — send query to chat
  - org.lina.Assistant.GetStatus()   — JSON status

Works via QDBusConnection (PyQt6) when available,
falls back to simple named-pipe IPC.

Usage:
    # From terminal (KDE/GNOME):
    qdbus org.lina.Assistant /org/lina/Assistant org.lina.Assistant.ToggleWindow
    qdbus org.lina.Assistant /org/lina/Assistant org.lina.Assistant.Query "громкость 50%"
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from lina.gui.chat import ChatController

logger = logging.getLogger(__name__)

# ── IPC paths ────────────────────────────────────────────────────────────────

_RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "lina"
_PIPE_PATH = _RUNTIME_DIR / "gui.sock"

DBUS_INTERFACE = "org.lina.Assistant"
DBUS_PATH = "/org/lina/Assistant"


def start_dbus_listener(window, controller: ChatController) -> Optional[object]:
    """Start D-Bus listener integrated with Qt event loop.

    Tries QtDBus first (native Qt integration), then falls back
    to a lightweight pipe-based IPC.

    Returns:
        An object to keep alive (prevent GC), or None.
    """
    # Try QtDBus (PyQt6)
    listener = _try_qdbus(window, controller)
    if listener is not None:
        return listener

    # Fallback: pipe-based IPC
    return _start_pipe_listener(window, controller)


# ── QtDBus approach ──────────────────────────────────────────────────────────

def _try_qdbus(window, controller):
    """Try to register on session D-Bus via PyQt6.QtDBus."""
    try:
        from PyQt6.QtDBus import (
            QDBusConnection,
            QDBusMessage,
        )
        from PyQt6.QtCore import QObject, pyqtSlot, Q_CLASSINFO

        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            logger.debug("QtDBus: session bus not connected")
            return None

        if not bus.registerService(DBUS_INTERFACE):
            logger.debug("QtDBus: service name already taken")
            return None

        class LinaDBusAdaptor(QObject):
            """D-Bus adaptor exposing Lina GUI methods."""

            def __init__(self, win, ctrl, parent=None):
                super().__init__(parent)
                self._window = win
                self._controller = ctrl

            @pyqtSlot(result=str)
            def ToggleWindow(self) -> str:
                """Show or hide the Lina main window."""
                try:
                    self._window.toggle_visibility()
                    visible = self._window.isVisible()
                    return json.dumps({"ok": True, "visible": visible})
                except Exception as e:
                    logger.error("D-Bus ToggleWindow error: %s", e)
                    return json.dumps({"ok": False, "error": "internal error"})

            @pyqtSlot(str, result=str)
            def Query(self, text: str) -> str:
                """Send a query to the chat controller (routed through governance)."""
                try:
                    process = getattr(self._controller, '_process_via_intent', None)
                    if process:
                        result = process(text)
                        return json.dumps({"ok": True, "response": result[:500]})
                    return json.dumps({"ok": False, "error": "no handler"})
                except Exception as e:
                    logger.error("D-Bus Query error: %s", e)
                    return json.dumps({"ok": False, "error": "internal error"})

            @pyqtSlot(result=str)
            def GetStatus(self) -> str:
                """Get Lina status."""
                return json.dumps({
                    "ok": True,
                    "visible": self._window.isVisible(),
                    "version": "0.7.0",
                })

        adaptor = LinaDBusAdaptor(window, controller, window)
        if bus.registerObject(DBUS_PATH, adaptor):
            logger.info("QtDBus adaptor registered at %s", DBUS_PATH)
            return adaptor
        else:
            logger.debug("QtDBus: registerObject failed")
            return None

    except ImportError:
        logger.debug("PyQt6.QtDBus not available")
        return None
    except Exception as e:
        logger.debug("QtDBus init error: %s", e)
        return None


# ── Pipe-based fallback ──────────────────────────────────────────────────────

def _start_pipe_listener(window, controller) -> Optional[object]:
    """Simple file-based IPC: watch for commands in a named pipe.

    External tools write JSON to the pipe:
      {"action": "toggle"}
      {"action": "query", "text": "громкость 50%"}
    """
    try:
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

        # Clean up stale pipe
        if _PIPE_PATH.exists():
            _PIPE_PATH.unlink()

        os.mkfifo(str(_PIPE_PATH), 0o600)

        class PipeWatcher:
            def __init__(self):
                self._running = True
                self._thread = threading.Thread(
                    target=self._watch, daemon=True, name="lina-pipe-ipc"
                )
                self._thread.start()

            def _watch(self):
                while self._running:
                    try:
                        with open(_PIPE_PATH, "r") as pipe:
                            data = pipe.read().strip()
                            if not data:
                                continue
                            self._handle(data)
                    except OSError:
                        break
                    except Exception as e:
                        logger.debug("Pipe IPC error: %s", e)

            def _handle(self, data: str):
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    return

                action = msg.get("action", "")
                if action == "toggle":
                    # Must dispatch to Qt main thread
                    from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
                    QMetaObject.invokeMethod(
                        window, "toggle_visibility",
                        Qt.ConnectionType.QueuedConnection,
                    )
                elif action == "query":
                    text = msg.get("text", "")
                    if text:
                        process = getattr(controller, '_process_via_intent', None)
                        if process:
                            process(text)

            def stop(self):
                self._running = False
                try:
                    _PIPE_PATH.unlink(missing_ok=True)
                except Exception:
                    pass

        watcher = PipeWatcher()
        logger.info("Pipe IPC listener started: %s", _PIPE_PATH)
        return watcher

    except Exception as e:
        logger.debug("Pipe IPC failed: %s", e)
        return None


def cleanup():
    """Remove IPC pipe on shutdown."""
    try:
        _PIPE_PATH.unlink(missing_ok=True)
    except Exception:
        pass
