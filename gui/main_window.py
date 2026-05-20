"""
Lina GUI — LinaMainWindow (MainWindow) with sidebar chat history.

Production-ready Qt desktop GUI.

Block map:
  Block S — ChatSidebar       (session list, search, new-chat)
  Block T — TitleBar           (avatar, status, Theme toggle)
  Block C — ChatView           (message bubbles, timestamps)
  Block B — InputBar           (text input, send button)
  Block E — Confirmation bar   (confirm / deny escalations)
  Block F — LinaStatusBar / StatusBar (mode indicator)
  Block D — Controller wiring  (message callbacks)
  Block J — Error handling     (_show_error, _on_worker_error)
  Block A — LinaMainWindow     (root layout, session management)

Layout:
  ┌─────────┬────────────────────────────┐
  │ Sidebar  │  Title Bar                 │
  │ ─────── │  ──────────────────────────  │
  │ Search   │                            │
  │ Сегодня  │  Chat Messages             │
  │  Chat 1  │   (bubbles + timestamps)   │
  │  Chat 2  │                            │
  │ Вчера    │                            │
  │  Chat 3  │ ────────────────────────── │
  │          │  InputBar [+][text][😊][📎][▶]│
  │ +Новый   │  StatusBar                 │
  └─────────┴────────────────────────────┘

CRITICAL RULES:
  - GUI NEVER calls execution layer directly
  - All user input goes through ChatController → IntentBridge → governance
  - Heavy work runs on QThread (see gui/workers.py)
  - Exceptions caught and displayed as user-friendly messages
"""

from __future__ import annotations

import html
import logging
import os
import time
from datetime import datetime
from typing import Optional, List


def _get_username() -> str:
    """Return OS login name for display in chat."""
    try:
        return os.getlogin()
    except OSError:
        pass
    return os.environ.get('USER') or os.environ.get('USERNAME') or 'User'

logger = logging.getLogger("lina.gui.main_window")


def _get_qt():
    from lina.gui import get_qt_modules
    return get_qt_modules()


# ═════════════════════════════════════════════════════════════════════════════
# Factory — call create_main_window() to instantiate
# ═════════════════════════════════════════════════════════════════════════════

def create_main_window(controller=None, settings=None, tray_controller=None):
    """Create and return the main application window."""
    QtWidgets, QtCore, QtGui = _get_qt()
    from lina.gui.chat import ChatController, MessageRole, MessageStatus
    from lina.gui.theme import get_theme, build_stylesheet, GUIConfig, ThemeColors
    from lina.gui.history import get_history_manager, ChatSession

    if controller is None:
        controller = ChatController()
    if settings is None:
        from lina.gui.settings import get_settings
        settings = get_settings()

    gui_config = GUIConfig(
        theme_name=settings.gui.theme,
        window_width=settings.gui.window_width,
        window_height=settings.gui.window_height,
        font_size=settings.gui.font_size,
        opacity=settings.gui.opacity,
    )

    history_mgr = get_history_manager()

    # ─────────────────────────────────────────────────────────────────────
    # Block S — Sidebar (Chat History)
    # ─────────────────────────────────────────────────────────────────────

    class ChatSidebar(QtWidgets.QWidget):
        """Left sidebar with search and chat session list.

        Signals:
            session_selected(str) — emitted when user clicks a chat session
            new_chat_requested() — emitted when user clicks "+ Новый чат"
        """

        session_selected = (QtCore.Signal(str) if hasattr(QtCore, 'Signal')
                            else QtCore.pyqtSignal(str))
        new_chat_requested = (QtCore.Signal() if hasattr(QtCore, 'Signal')
                              else QtCore.pyqtSignal())
        session_deleted = (QtCore.Signal(str) if hasattr(QtCore, 'Signal')
                           else QtCore.pyqtSignal(str))

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("sidebar")
            self.setFixedWidth(gui_config.sidebar_width)
            self.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            self._active_session_id: Optional[str] = None
            self._setup_ui()

        def _setup_ui(self):
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(12, 14, 12, 12)
            layout.setSpacing(8)

            # ── Search Box ──
            self.search = QtWidgets.QLineEdit()
            self.search.setObjectName("searchBox")
            self.search.setPlaceholderText("🔍 Поиск чатов...")
            self.search.textChanged.connect(self._on_search)
            layout.addWidget(self.search)

            layout.addSpacing(8)

            # ── Chat List (scrollable) ──
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

            self._list_widget = QtWidgets.QWidget()
            self._list_layout = QtWidgets.QVBoxLayout(self._list_widget)
            self._list_layout.setContentsMargins(0, 0, 0, 0)
            self._list_layout.setSpacing(6)
            self._list_layout.addStretch()

            scroll.setWidget(self._list_widget)
            layout.addWidget(scroll, stretch=1)

            # ── New Chat button ──
            new_btn = QtWidgets.QPushButton("＋  Новый чат")
            new_btn.setObjectName("newChatBtn")
            new_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            new_btn.clicked.connect(self.new_chat_requested.emit)
            layout.addWidget(new_btn)

        def refresh(self, active_id: Optional[str] = None):
            """Rebuild the full chat list from history manager."""
            self._active_session_id = active_id

            # Clear existing items (keep stretch at end)
            while self._list_layout.count() > 1:
                item = self._list_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()

            groups = history_mgr.list_grouped()
            search_text = self.search.text().lower().strip()

            for group_name, sessions in groups.items():
                if not sessions:
                    continue
                # Filter by search
                if search_text:
                    sessions = [
                        s for s in sessions
                        if search_text in s.title.lower()
                        or search_text in s.preview().lower()
                    ]
                    if not sessions:
                        continue

                # Section label
                label = QtWidgets.QLabel(group_name)
                label.setObjectName("sectionLabel")
                idx = self._list_layout.count() - 1
                self._list_layout.insertWidget(idx, label)

                for session in sessions:
                    btn = QtWidgets.QPushButton(session.title)
                    btn.setToolTip(session.preview())
                    btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

                    if session.id == self._active_session_id:
                        btn.setObjectName("chatItemActive")
                    else:
                        btn.setObjectName("chatItem")

                    # Left-click → open
                    sid = session.id
                    btn.clicked.connect(
                        lambda checked, s=sid: self.session_selected.emit(s))

                    # Context menu → delete
                    btn.setContextMenuPolicy(
                        QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
                    btn.customContextMenuRequested.connect(
                        lambda pos, s=sid, b=btn: self._show_ctx(pos, s, b))

                    idx = self._list_layout.count() - 1
                    self._list_layout.insertWidget(idx, btn)

        def _show_ctx(self, pos, session_id: str, btn):
            """Context menu: rename / delete."""
            menu = QtWidgets.QMenu(self)
            rename_act = menu.addAction("✏️ Переименовать")
            delete_act = menu.addAction("🗑 Удалить")
            action = menu.exec(btn.mapToGlobal(pos))
            if action == delete_act:
                self.session_deleted.emit(session_id)
            elif action == rename_act:
                self._rename_dialog(session_id)

        def _rename_dialog(self, session_id: str):
            session = history_mgr.load_session(session_id)
            if not session:
                return
            new_title, ok = QtWidgets.QInputDialog.getText(
                self, "Переименовать чат", "Название:",
                text=session.title)
            if ok and new_title.strip():
                history_mgr.rename_session(session_id, new_title.strip())
                self.refresh(self._active_session_id)

        def _on_search(self, text: str):
            self.refresh(self._active_session_id)

    # ─────────────────────────────────────────────────────────────────────
    # Block T — Title Bar
    # ─────────────────────────────────────────────────────────────────────

    class TitleBar(QtWidgets.QWidget):
        """Custom title bar with avatar, name, status, menu buttons."""

        theme_toggle = (QtCore.Signal() if hasattr(QtCore, 'Signal')
                        else QtCore.pyqtSignal())
        settings_requested = (QtCore.Signal() if hasattr(QtCore, 'Signal')
                              else QtCore.pyqtSignal())

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("titleBar")
            self.setFixedHeight(68)
            self.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            self._setup_ui()

        def _setup_ui(self):
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(18, 0, 18, 0)
            layout.setSpacing(12)

            # Title + status
            info_layout = QtWidgets.QVBoxLayout()
            info_layout.setSpacing(0)
            info_layout.setContentsMargins(0, 0, 0, 0)

            title = QtWidgets.QLabel("Lina AI")
            title.setObjectName("titleLabel")
            info_layout.addWidget(title)

            self.status_label = QtWidgets.QLabel("● В сети")
            self.status_label.setObjectName("statusDot")
            info_layout.addWidget(self.status_label)

            layout.addLayout(info_layout)
            layout.addStretch()

            # Settings button
            self._settings_btn = QtWidgets.QPushButton("⚙")
            self._settings_btn.setObjectName("titleBtn")
            self._settings_btn.setFixedSize(38, 38)
            self._settings_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self._settings_btn.setToolTip("Настройки")
            self._settings_btn.clicked.connect(self.settings_requested.emit)
            layout.addWidget(self._settings_btn)

            # Theme toggle button
            theme_btn = QtWidgets.QPushButton("🌙")
            theme_btn.setObjectName("titleBtn")
            theme_btn.setFixedSize(38, 38)
            theme_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            theme_btn.setToolTip("Сменить тему")
            theme_btn.clicked.connect(self.theme_toggle.emit)
            layout.addWidget(theme_btn)

            # Menu button
            menu_btn = QtWidgets.QPushButton("⋮")
            menu_btn.setObjectName("titleBtn")
            menu_btn.setFixedSize(38, 38)
            menu_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            menu_btn.setToolTip("Меню")
            self._menu_btn = menu_btn
            layout.addWidget(menu_btn)

        def set_status(self, text: str, color: str = "#3fb950"):
            self.status_label.setText(f"● {text}")
            self.status_label.setStyleSheet(
                f"color: {color}; font-size: 11px;")

    # ─────────────────────────────────────────────────────────────────────
    # Block C — InputBar (redesigned glass)
    # ─────────────────────────────────────────────────────────────────────

    class InputBar(QtWidgets.QWidget):
        """Messenger-style input bar: [🎤] [     text     ] [▶/■]"""

        send_requested = (QtCore.Signal(str) if hasattr(QtCore, 'Signal')
                          else QtCore.pyqtSignal(str))
        stop_requested = (QtCore.Signal() if hasattr(QtCore, 'Signal')
                          else QtCore.pyqtSignal())
        voice_requested = (QtCore.Signal() if hasattr(QtCore, 'Signal')
                           else QtCore.pyqtSignal())
        voice_stop_requested = (QtCore.Signal() if hasattr(QtCore, 'Signal')
                                else QtCore.pyqtSignal())

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("inputBar")
            self.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            self._generating = False
            self._voice_recording = False
            self._voice_available = False
            self._setup_ui()

        def _setup_ui(self):
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(14, 12, 14, 14)
            layout.setSpacing(10)

            # Mic button (toggle: 🎤 / ⏹)
            self.mic_btn = QtWidgets.QPushButton("🎤")
            self.mic_btn.setObjectName("micButton")
            self.mic_btn.setFixedSize(44, 44)
            self.mic_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.mic_btn.setToolTip("Голосовой ввод")
            self.mic_btn.clicked.connect(self._on_mic_click)
            self.mic_btn.setVisible(False)  # hidden until voice is available
            layout.addWidget(self.mic_btn)

            # Text field (rounded, auto-grow)
            self.text_edit = QtWidgets.QTextEdit()
            self.text_edit.setObjectName("inputField")
            self.text_edit.setPlaceholderText("Напиши сообщение...")
            self.text_edit.setMinimumHeight(48)
            self.text_edit.setMaximumHeight(168)
            self.text_edit.setAcceptRichText(False)
            self.text_edit.installEventFilter(self)
            self.text_edit.document().contentsChanged.connect(self._adjust_height)
            self._adjust_height()
            layout.addWidget(self.text_edit, stretch=1)

            # Send / Stop button (toggles)
            self.send_btn = QtWidgets.QPushButton("▶")
            self.send_btn.setObjectName("sendButton")
            self.send_btn.setFixedSize(44, 44)
            self.send_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.send_btn.setToolTip("Отправить (Enter)")
            self.send_btn.clicked.connect(self._on_btn_click)
            layout.addWidget(self.send_btn)

        def eventFilter(self, obj, event):
            """Enter → send, Shift+Enter → newline, Escape → stop."""
            if obj is self.text_edit and event.type() == QtCore.QEvent.Type.KeyPress:
                if event.key() in (QtCore.Qt.Key.Key_Return,
                                   QtCore.Qt.Key.Key_Enter):
                    mods = event.modifiers()
                    if mods & QtCore.Qt.KeyboardModifier.ShiftModifier:
                        return False
                    self._on_btn_click()
                    return True
                if event.key() == QtCore.Qt.Key.Key_Escape and self._generating:
                    self.stop_requested.emit()
                    return True
            return super().eventFilter(obj, event)

        def _on_btn_click(self):
            if self._generating:
                self.stop_requested.emit()
            else:
                text = self.text_edit.toPlainText().strip()
                if text:
                    self.text_edit.clear()
                    self.send_requested.emit(text)

        def _adjust_height(self):
            """Resize text_edit height to fit content (1–6 lines)."""
            doc = self.text_edit.document()
            margins = self.text_edit.contentsMargins()
            h = int(doc.size().height()) + margins.top() + margins.bottom() + 14
            h = max(48, min(h, 168))
            self.text_edit.setFixedHeight(h)

        def set_generating(self, generating: bool):
            """Toggle between Send ▶ and Stop ■ modes."""
            self._generating = generating
            if generating:
                self.send_btn.setText("■")
                self.send_btn.setToolTip("Остановить (Escape)")
                self.send_btn.setStyleSheet(
                    "QPushButton { background: #da3633; color: #fff; }")
                self.text_edit.setEnabled(False)
            else:
                self.send_btn.setText("▶")
                self.send_btn.setToolTip("Отправить (Enter)")
                self.send_btn.setStyleSheet("")  # reset to theme default
                self.text_edit.setEnabled(True)
                self.text_edit.setFocus()

        def get_text(self) -> str:
            return self.text_edit.toPlainText().strip()

        def clear(self):
            self.text_edit.clear()

        # ── Voice ──

        def set_voice_available(self, available: bool):
            """Show/hide mic button based on voice availability."""
            self._voice_available = available
            self.mic_btn.setVisible(available)

        def _on_mic_click(self):
            if self._voice_recording:
                self._voice_recording = False
                self.mic_btn.setText("🎤")
                self.mic_btn.setToolTip("Голосовой ввод")
                self.voice_stop_requested.emit()
            else:
                self._voice_recording = True
                self.mic_btn.setText("⏹")
                self.mic_btn.setToolTip("Остановить запись")
                self.voice_requested.emit()

        def set_voice_recording(self, recording: bool):
            """External control of voice indicator."""
            self._voice_recording = recording
            if recording:
                self.mic_btn.setText("⏹")
                self.mic_btn.setToolTip("Остановить запись")
            else:
                self.mic_btn.setText("🎤")
                self.mic_btn.setToolTip("Голосовой ввод")

    # ─────────────────────────────────────────────────────────────────────
    # Block B — ChatView (with timestamps, avatars, checkmarks)
    # ─────────────────────────────────────────────────────────────────────

    class ChatView(QtWidgets.QTextBrowser):
        """Displays chat messages as rendered HTML with timestamps."""

        def __init__(self, theme: ThemeColors, parent=None):
            super().__init__(parent)
            self._theme = theme
            self._messages: List = []
            self.setOpenExternalLinks(True)
            self.setReadOnly(True)
            self.setObjectName("chatView")
            self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            self.document().setDocumentMargin(0)
            # Throttle streaming updates to avoid shaking
            self._render_timer = QtCore.QTimer(self)
            self._render_timer.setSingleShot(True)
            self._render_timer.setInterval(50)  # ms — max 20 FPS
            self._render_timer.timeout.connect(self._do_render)
            self._render_pending = False

        def set_theme(self, theme: ThemeColors):
            self._theme = theme

        def _do_render(self):
            """Deferred render — called by timer to coalesce rapid updates."""
            self._render_pending = False
            html = self._build_html(self._messages)
            # Save scroll position BEFORE setHtml (which resets to 0)
            sb = self.verticalScrollBar()
            old_value = sb.value()
            old_max = sb.maximum()
            was_at_bottom = (old_value >= old_max - 20) if old_max > 0 else True
            self.setHtml(html)
            if was_at_bottom:
                QtCore.QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))
            else:
                # Restore scroll position proportionally — content may have grown
                new_max = sb.maximum()
                if old_max > 0 and new_max > 0:
                    ratio = old_value / old_max
                    restored = int(ratio * new_max)
                else:
                    restored = old_value
                QtCore.QTimer.singleShot(0, lambda: sb.setValue(restored))

        def render_messages(self, messages: list):
            """Full re-render of all messages."""
            self._messages = messages
            html = self._build_html(messages)
            self.setHtml(html)
            sb = self.verticalScrollBar()
            QtCore.QTimer.singleShot(10, lambda: sb.setValue(sb.maximum()))

        def append_message(self, msg):
            if msg not in self._messages:
                self._messages.append(msg)
            self.render_messages(self._messages)

        def update_message(self, msg):
            for i, m in enumerate(self._messages):
                if m.message_id == msg.message_id:
                    self._messages[i] = msg
                    break
            # Throttle: schedule render instead of immediate setHtml
            if not self._render_pending:
                self._render_pending = True
                self._render_timer.start()

        def show_typing(self, show: bool = True):
            """Show/hide typing indicator at bottom."""
            if show:
                html = self._build_html(self._messages)
                typing_html = (
                    '<table class="message"><tr>'
                    '<td class="message-cell">'
                    '<div class="bubble bubble-assistant">'
                    '<div class="meta">'
                    '<span class="role-tag role-assistant">LINA</span>'
                    '<span class="timestamp">сейчас</span>'
                    '</div>'
                    '<div class="typing">● ● ●</div>'
                    '</div>'
                    '</td></tr></table>'
                )
                html = html.replace('</body></html>', typing_html + '</body></html>')
                self.setHtml(html)
                sb = self.verticalScrollBar()
                QtCore.QTimer.singleShot(10, lambda: sb.setValue(sb.maximum()))
            else:
                self.render_messages(self._messages)

        def _build_html(self, messages) -> str:
            t = self._theme
            base_font_size = max(gui_config.font_size, 14)
            parts = [
                f'<html><head><style>'
                f'body {{ background:transparent; color:{t.text};'
                f'  font-family:"Inter","Noto Sans","Segoe UI",sans-serif;'
                f'  font-size:{base_font_size}px; line-height:1.6;'
                f'  margin:0; padding:16px 18px 18px 18px; }}'
                # ── message row ──
                f'table.message {{ width:100%; border-collapse:separate;'
                f'  border-spacing:0; margin:0 0 14px 0; }}'
                f'td.message-cell {{ vertical-align:top; }}'
                # ── frosted glass bubbles ──
                f'.bubble {{ display:inline-block; max-width:680px;'
                f'  padding:14px 18px 16px 18px;'
                f'  border-radius:20px; text-align:left; }}'
                f'.bubble-assistant {{ background:{t.bot_bubble};'
                f'  border:1px solid {t.bot_bubble_border};'
                f'  backdrop-filter:blur(20px);'
                f'  -webkit-backdrop-filter:blur(20px); }}'
                f'.bubble-user {{ background:{t.user_bubble};'
                f'  border:1px solid {t.user_bubble_border};'
                f'  backdrop-filter:blur(20px);'
                f'  -webkit-backdrop-filter:blur(20px); }}'
                # ── meta + role tags ──
                f'.meta {{ margin:0 0 8px 0; line-height:1.2; }}'
                f'.role-tag {{ display:inline-block; padding:3px 10px;'
                f'  border-radius:999px; font-size:10px; font-weight:700;'
                f'  letter-spacing:0.08em; text-transform:uppercase; }}'
                f'.role-assistant {{ background:rgba(108,140,255,0.18);'
                f'  color:{t.primary}; }}'
                f'.role-user {{ background:{t.secondary};'
                f'  color:#ffffff; }}'
                f'.timestamp {{ color:{t.message_meta}; font-size:11px;'
                f'  margin-left:8px; opacity:0.8; }}'
                f'.status-note {{ color:{t.warning}; font-size:11px;'
                f'  margin-left:8px; }}'
                f'.status-error {{ color:{t.error}; }}'
                # ── content ──
                f'.content {{ color:{t.text}; font-size:{base_font_size}px;'
                f'  line-height:1.65; word-wrap:break-word; }}'
                # ── system pill ──
                f'.system-row {{ text-align:center; margin:6px 0 16px 0; }}'
                f'.system-pill {{ display:inline-block;'
                f'  background:rgba(108,140,255,0.12);'
                f'  color:{t.text_secondary};'
                f'  border:1px solid rgba(108,140,255,0.18);'
                f'  border-radius:999px; padding:7px 16px; font-size:12px; }}'
                # ── typing indicator ──
                f'.typing {{ color:{t.text_secondary}; font-size:14px;'
                f'  letter-spacing:4px; }}'
                f'a {{ color:{t.primary}; text-decoration:none; }}'
                f'a:hover {{ text-decoration:underline; }}'
                f'</style></head><body>'
            ]

            for msg in messages:
                rendered = controller.render_markdown(msg.content)
                ts = datetime.fromtimestamp(msg.timestamp).strftime("%H:%M")

                if msg.role == MessageRole.USER:
                    uname = html.escape(_get_username())
                    parts.append(
                        '<table class="message"><tr>'
                        '<td class="message-cell" align="right">'
                        '<div class="bubble bubble-user">'
                        '<div class="meta">'
                        f'<span class="role-tag role-user">{uname}</span>'
                        f'<span class="timestamp">{ts}</span>'
                        '</div>'
                        f'<div class="content">{rendered}</div>'
                        '</div>'
                        '</td>'
                        '</tr></table>'
                    )

                elif msg.role == MessageRole.ASSISTANT:
                    status_html = ""
                    if msg.status == MessageStatus.PENDING:
                        status_html = '<span class="status-note">думаю</span>'
                    elif msg.status == MessageStatus.ERROR:
                        status_html = (
                            '<span class="status-note status-error">ошибка</span>'
                        )

                    parts.append(
                        '<table class="message"><tr>'
                        '<td class="message-cell">'
                        '<div class="bubble bubble-assistant">'
                        '<div class="meta">'
                        '<span class="role-tag role-assistant">LINA</span>'
                        f'<span class="timestamp">{ts}</span>'
                        f'{status_html}'
                        '</div>'
                        f'<div class="content">{rendered}</div>'
                        '</div>'
                        '</td>'
                        '</tr></table>'
                    )

                else:  # SYSTEM
                    parts.append(
                        '<div class="system-row">'
                        f'<span class="system-pill">{html.escape(msg.content)}</span>'
                        '</div>'
                    )

            parts.append('</body></html>')
            return ''.join(parts)

    # ─────────────────────────────────────────────────────────────────────
    # Block E — Confirmation UI
    # ─────────────────────────────────────────────────────────────────────

    class ConfirmationBar(QtWidgets.QWidget):
        """Inline confirmation widget shown when NEEDS_CONFIRM."""

        confirmed = (QtCore.Signal(str) if hasattr(QtCore, 'Signal')
                     else QtCore.pyqtSignal(str))
        denied = (QtCore.Signal(str) if hasattr(QtCore, 'Signal')
                  else QtCore.pyqtSignal(str))

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            self.setObjectName("confirmBar")
            self._current_esc_id: Optional[str] = None
            self._setup_ui()
            self.hide()

        def _setup_ui(self):
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(12, 6, 12, 6)

            self.label = QtWidgets.QLabel("Требуется подтверждение")
            layout.addWidget(self.label, stretch=1)

            self.confirm_btn = QtWidgets.QPushButton("✅ Подтвердить")
            self.confirm_btn.setObjectName("confirmButton")
            self.confirm_btn.clicked.connect(self._on_confirm)
            layout.addWidget(self.confirm_btn)

            self.cancel_btn = QtWidgets.QPushButton("❌ Отклонить")
            self.cancel_btn.setObjectName("cancelButton")
            self.cancel_btn.clicked.connect(self._on_cancel)
            layout.addWidget(self.cancel_btn)

        def show_confirmation(self, esc_id: str, description: str):
            self._current_esc_id = esc_id
            self.label.setText(f"⚠ {description}")
            self.show()

        def _on_confirm(self):
            if self._current_esc_id:
                self.confirmed.emit(self._current_esc_id)
            self.hide()
            self._current_esc_id = None

        def _on_cancel(self):
            if self._current_esc_id:
                self.denied.emit(self._current_esc_id)
            self.hide()
            self._current_esc_id = None

        def dismiss(self):
            self.hide()
            self._current_esc_id = None

    # ─────────────────────────────────────────────────────────────────────
    # Block F — StatusBar
    # ─────────────────────────────────────────────────────────────────────

    class LinaStatusBar(QtWidgets.QWidget):
        """Bottom bar with status / info / metrics + mic button."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            self.setFixedHeight(52)
            self.setObjectName("statusBar")

            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(16, 6, 16, 6)
            layout.setSpacing(8)

            layout.addStretch()

            # Status text (inline)
            self.mode_label = QtWidgets.QLabel("● Готова")
            layout.addWidget(self.mode_label)

            self.info_label = QtWidgets.QLabel("")
            layout.addWidget(self.info_label)

            self.metrics_label = QtWidgets.QLabel("")
            layout.addWidget(self.metrics_label)

            # Mic buttons (like reference)
            mic1 = QtWidgets.QPushButton("🎤")
            mic1.setObjectName("titleBtn")
            mic1.setFixedSize(38, 38)
            mic1.setToolTip("Голосовой ввод")
            layout.addWidget(mic1)

        def set_mode(self, mode: str):
            colors = get_theme(gui_config.theme_name)
            modes = {
                "ready": ("● Готова", colors.success),
                "generating": ("⏳ Думаю...", colors.warning),
                "confirming": ("⚠ Ожидание подтверждения", colors.warning),
                "error": ("❌ Ошибка", colors.error),
                "degraded": ("⚠ Ограниченный режим", colors.warning),
            }
            text, color = modes.get(mode, ("● Готова", colors.success))
            self.mode_label.setText(text)
            self.mode_label.setStyleSheet(
                f"color: {color}; font-size: 11px;")

        def set_info(self, text: str):
            theme = get_theme(gui_config.theme_name)
            self.info_label.setText(text)
            self.info_label.setStyleSheet(
                f"color: {theme.text_secondary}; font-size: 11px;")

        def set_metrics(self, text: str):
            theme = get_theme(gui_config.theme_name)
            self.metrics_label.setText(text)
            self.metrics_label.setStyleSheet(
                f"color: {theme.text_secondary}; font-size: 11px;")

    # ─────────────────────────────────────────────────────────────────────
    # Block A — MainWindow (with sidebar)
    # ─────────────────────────────────────────────────────────────────────

    class LinaMainWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.controller = controller
            self.settings = settings
            self.tray_controller = tray_controller
            self._current_theme_name = gui_config.theme_name
            self._workers: list = []
            self._current_worker = None
            self._current_session_id: Optional[str] = None
            self._repair_attempts = {"count": 0, "last_cmd": ""}
            self._install_workflow = None  # активный install workflow (если есть)

            self._setup_window()
            self._setup_ui()
            self._connect_controller()
            self._apply_theme(gui_config.theme_name)
            self._init_session()

        # ── Window Setup ──

        def _setup_window(self):
            self.setWindowTitle("Lina AI Assistant")
            self.setMinimumSize(800, 520)
            self.resize(
                gui_config.window_width or 1100,
                gui_config.window_height or 720,
            )
            self.setWindowIcon(self._create_icon())

            # ── Glass-morphism: translucent background ──
            self.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
            if gui_config.opacity < 1.0:
                self.setWindowOpacity(gui_config.opacity)

            # ── KWin blur hint (KDE Plasma / Wayland & X11) ──
            self._request_blur()

        def _request_blur(self):
            """Request background blur from KWin (KDE Plasma).

            Works on both X11 and Wayland via the _KDE_NET_WM_BLUR_BEHIND_REGION
            X property or org.kde.kwin.blur Wayland protocol.
            """
            try:
                import subprocess
                import struct
                # On Wayland KDE, we set a window property that KWin reads
                # For X11, we use native interface
                win_id = int(self.winId())
                if win_id:
                    # Try XCB (X11) first
                    try:
                        native = self.windowHandle()
                        if native is not None:
                            # Setting empty region = blur the whole window
                            import ctypes
                            # X11 approach via xprop
                            subprocess.Popen(
                                ["xprop", "-id", str(win_id), "-f",
                                 "_KDE_NET_WM_BLUR_BEHIND_REGION", "32c",
                                 "-set", "_KDE_NET_WM_BLUR_BEHIND_REGION", "0"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            logger.debug("KWin blur hint set via xprop")
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Could not set blur hint: {e}")

        def _create_icon(self) -> QtGui.QIcon:
            pixmap = QtGui.QPixmap(64, 64)
            pixmap.fill(QtGui.QColor("#3b82f6"))
            painter = QtGui.QPainter(pixmap)
            painter.setPen(QtGui.QColor("#ffffff"))
            font = QtGui.QFont("Inter", 28, QtGui.QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(
                pixmap.rect(),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                "L",
            )
            painter.end()
            return QtGui.QIcon(pixmap)

        # ── UI Layout ──

        def _setup_ui(self):
            central = QtWidgets.QWidget()
            central.setObjectName("centralWidget")
            self.setCentralWidget(central)

            root_layout = QtWidgets.QHBoxLayout(central)
            root_layout.setContentsMargins(20, 20, 20, 20)
            root_layout.setSpacing(16)

            # ── Left: Sidebar ──
            self.sidebar = ChatSidebar()
            self.sidebar.session_selected.connect(self._on_session_selected)
            self.sidebar.new_chat_requested.connect(self._on_new_chat)
            self.sidebar.session_deleted.connect(self._on_session_deleted)
            root_layout.addWidget(self.sidebar)

            # ── Right: Chat Area ──
            right_panel = QtWidgets.QWidget()
            right_panel.setObjectName("rightPanel")
            right_panel.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            right_layout = QtWidgets.QVBoxLayout(right_panel)
            right_layout.setContentsMargins(0, 0, 0, 0)
            right_layout.setSpacing(12)

            # Title bar
            self.title_bar = TitleBar()
            self.title_bar.theme_toggle.connect(self._toggle_theme)
            self.title_bar.settings_requested.connect(self._open_settings_dialog)
            self.title_bar._menu_btn.clicked.connect(self._show_menu_popup)
            right_layout.addWidget(self.title_bar)

            # Chat view
            theme = get_theme(self._current_theme_name)
            self.chat_view = ChatView(theme)
            self.chat_shell = QtWidgets.QWidget()
            self.chat_shell.setObjectName("chatShell")
            self.chat_shell.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            chat_shell_layout = QtWidgets.QVBoxLayout(self.chat_shell)
            chat_shell_layout.setContentsMargins(0, 0, 0, 0)
            chat_shell_layout.setSpacing(0)
            chat_shell_layout.addWidget(self.chat_view)
            self._chat_shadow = QtWidgets.QGraphicsDropShadowEffect(
                self.chat_shell)
            self._chat_shadow.setBlurRadius(60)
            self._chat_shadow.setOffset(0, 16)
            self._chat_shadow.setColor(QtGui.QColor(30, 20, 80, 90))
            self.chat_shell.setGraphicsEffect(self._chat_shadow)
            right_layout.addWidget(self.chat_shell, stretch=1)

            # Confirmation bar (hidden)
            self.confirmation_bar = ConfirmationBar()
            self.confirmation_bar.confirmed.connect(self._on_confirm)
            self.confirmation_bar.denied.connect(self._on_deny)
            right_layout.addWidget(self.confirmation_bar)

            # Command action bar (hidden) — shows install commands
            from lina.gui.terminal_widget import (
                create_command_action_bar_class,
                create_embedded_terminal_class,
            )
            CommandActionBar = create_command_action_bar_class()
            self.command_bar = CommandActionBar()
            self.command_bar.execute_requested.connect(
                self._on_execute_command)
            right_layout.addWidget(self.command_bar)

            # Input bar
            self.input_bar = InputBar()
            self.input_bar.send_requested.connect(self._on_user_send)
            right_layout.addWidget(self.input_bar)

            # Embedded terminal (hidden) — PTY for running commands
            EmbeddedTerminal = create_embedded_terminal_class()
            self.terminal = EmbeddedTerminal()
            self.terminal.command_finished.connect(
                self._on_terminal_finished)
            right_layout.addWidget(self.terminal)

            # Status bar
            self.status_bar = LinaStatusBar()
            right_layout.addWidget(self.status_bar)

            root_layout.addWidget(right_panel, stretch=1)

        # ── Glass paint ──

        def paintEvent(self, event):
            """Paint cosmic gradient background with glowing accents."""
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

            theme = get_theme(self._current_theme_name)
            rect = self.rect().adjusted(0, 0, -1, -1)
            w, h = float(rect.width()), float(rect.height())

            # ── 1. Deep cosmic gradient (top-left to bottom-right) ──
            base = QtGui.QLinearGradient(0, 0, w * 0.4, h)
            base.setColorAt(0.0, QtGui.QColor(theme.window_gradient_start))
            base.setColorAt(0.45, QtGui.QColor(theme.window_gradient_mid))
            base.setColorAt(1.0, QtGui.QColor(theme.window_gradient_end))
            painter.setBrush(QtGui.QBrush(base))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 28, 28)

            # ── 2. Large bottom-center blue/cyan glow ──
            glow1 = QtGui.QRadialGradient(w * 0.48, h * 0.88, w * 0.45)
            glow1.setColorAt(0.0, QtGui.QColor(80, 160, 255, 44))
            glow1.setColorAt(0.4, QtGui.QColor(60, 120, 240, 20))
            glow1.setColorAt(1.0, QtGui.QColor(60, 120, 240, 0))
            painter.setBrush(QtGui.QBrush(glow1))
            painter.drawRoundedRect(rect, 28, 28)

            # ── 3. Top-right purple/lavender accent ──
            glow2 = QtGui.QRadialGradient(w * 0.85, h * 0.10, w * 0.35)
            glow2.setColorAt(0.0, QtGui.QColor(160, 120, 255, 36))
            glow2.setColorAt(0.5, QtGui.QColor(120, 90, 220, 14))
            glow2.setColorAt(1.0, QtGui.QColor(120, 90, 220, 0))
            painter.setBrush(QtGui.QBrush(glow2))
            painter.drawRoundedRect(rect, 28, 28)

            # ── 4. Bottom-left warm pink accent ──
            glow3 = QtGui.QRadialGradient(w * 0.12, h * 0.92, w * 0.28)
            glow3.setColorAt(0.0, QtGui.QColor(200, 100, 220, 24))
            glow3.setColorAt(0.6, QtGui.QColor(160, 80, 180, 8))
            glow3.setColorAt(1.0, QtGui.QColor(160, 80, 180, 0))
            painter.setBrush(QtGui.QBrush(glow3))
            painter.drawRoundedRect(rect, 28, 28)

            # ── 5. Subtle window border ──
            border_pen = QtGui.QPen(
                QtGui.QColor(255, 255, 255, 18), 1)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.setPen(border_pen)
            painter.drawRoundedRect(rect, 28, 28)

            painter.end()

        def showEvent(self, event):
            """Re-apply blur hint after window is mapped (winId is valid)."""
            super().showEvent(event)
            # Defer a bit to ensure the window is fully mapped
            QtCore.QTimer.singleShot(100, self._request_blur)

        # ── Session Management ──

        def _init_session(self):
            """Load latest session or create new one."""
            sessions = history_mgr.list_sessions()
            if sessions:
                self._load_session(sessions[0].id)
            else:
                self._on_new_chat()

        def _on_new_chat(self):
            """Create a new chat session."""
            session = history_mgr.create_session()
            self._load_session(session.id)
            # Welcome
            controller.add_message(
                MessageRole.SYSTEM,
                "Добро пожаловать! Я Lina — ваш ИИ-помощник.",
            )
            self._persist_current_messages()

        def _load_session(self, session_id: str):
            """Load a chat session from history."""
            session = history_mgr.load_session(session_id)
            if not session:
                return
            self._current_session_id = session_id

            # Restore messages into controller
            controller.clear_history()
            for msg_data in session.messages:
                role = MessageRole(msg_data.get("role", "system"))
                content = msg_data.get("content", "")
                status = MessageStatus(msg_data.get("status", "complete"))
                controller.add_message(role, content, status=status)

            # Refresh views
            self.chat_view.render_messages(controller.get_messages())
            self.sidebar.refresh(session_id)

        def _on_session_selected(self, session_id: str):
            """User clicked a session in sidebar."""
            if session_id == self._current_session_id:
                return
            # Save current session first
            self._persist_current_messages()
            self._load_session(session_id)

        def _on_session_deleted(self, session_id: str):
            """Delete a chat session."""
            history_mgr.delete_session(session_id)
            if session_id == self._current_session_id:
                # Switch to another session or create new
                remaining = history_mgr.list_sessions()
                if remaining:
                    self._load_session(remaining[0].id)
                else:
                    self._on_new_chat()
            else:
                self.sidebar.refresh(self._current_session_id)

        def _persist_current_messages(self):
            """Save current controller messages to active session."""
            if not self._current_session_id:
                return
            session = history_mgr.load_session(self._current_session_id)
            if not session:
                return
            session.messages = [m.to_dict() for m in controller.get_messages()]
            # Auto-title from first user message
            for m in session.messages:
                if m.get("role") == "user":
                    content = m["content"]
                    session.title = content[:50] + ("…" if len(content) > 50 else "")
                    break
            history_mgr.save_session(session)
            self.sidebar.refresh(self._current_session_id)

        # ── Controller Wiring (Block D) ──

        def _connect_controller(self):
            controller.set_on_message_added(self._on_message_added)
            controller.set_on_message_updated(self._on_message_updated)
            controller.set_on_generation_started(self._on_generation_started)
            controller.set_on_generation_finished(self._on_generation_finished)

        def _on_message_added(self, msg):
            try:
                self.chat_view.append_message(msg)
                if (msg.role == MessageRole.ASSISTANT
                        and msg.metadata.get("needs_confirm")):
                    esc_id = msg.metadata.get("escalation_id", "")
                    if esc_id:
                        self.confirmation_bar.show_confirmation(
                            esc_id, msg.content)
                        self.status_bar.set_mode("confirming")
            except Exception as e:
                logger.error("UI update error (message_added): %s", e)

        def _on_message_updated(self, msg):
            try:
                self.chat_view.update_message(msg)
            except Exception as e:
                logger.error("UI update error (message_updated): %s", e)

        def _on_generation_started(self):
            self.input_bar.set_generating(True)
            self.status_bar.set_mode("generating")
            self.title_bar.set_status("Думаю...", "#d29922")
            self.chat_view.show_typing(True)

        def _on_generation_finished(self):
            self.input_bar.set_generating(False)
            self.status_bar.set_mode("ready")
            self.title_bar.set_status("В сети", "#3fb950")
            self.chat_view.show_typing(False)
            # Auto-save after each response
            self._persist_current_messages()

        # ── User Actions ──

        def _on_user_send(self, text: str):
            if not text or controller.is_generating():
                return

            controller._is_generating = True
            if controller._on_generation_started:
                controller._on_generation_started()

            controller.add_message(MessageRole.USER, text)
            placeholder = controller.add_message(
                MessageRole.ASSISTANT, "⏳ Думаю...",
                status=MessageStatus.PENDING,
            )
            self._current_placeholder_id = placeholder.message_id
            controller._current_stream_id = placeholder.message_id

            try:
                use_streaming = getattr(controller, '_stream_handler', None)

                if use_streaming:
                    # ── Streaming path: token-by-token ──
                    from lina.gui.workers import create_streaming_worker_class
                    StreamWorker = create_streaming_worker_class()
                    worker = StreamWorker(controller._stream_handler, text)
                    worker.token.connect(self._on_stream_token)
                    worker.finished.connect(self._on_worker_finished)
                    worker.error.connect(self._on_worker_error)
                    self.input_bar.stop_requested.connect(worker.cancel)
                    self._current_worker = worker
                else:
                    # ── Non-streaming path: bulk response ──
                    from lina.gui.workers import create_chat_worker_class
                    ChatWorker = create_chat_worker_class()
                    worker = ChatWorker(controller, text)
                    worker.finished.connect(self._on_worker_finished)
                    worker.error.connect(self._on_worker_error)
                    self._current_worker = worker

                self._workers.append(worker)
                worker.finished.connect(lambda _: self._cleanup_worker(worker))
                worker.error.connect(lambda _: self._cleanup_worker(worker))
                worker.start()
            except ImportError:
                logger.warning("QThread unavailable, running synchronously")
                response = controller._process_via_intent(text)
                self._on_worker_finished(response)
            except Exception as e:
                logger.error("Worker creation error: %s", e, exc_info=True)
                self._on_worker_error("Внутренняя ошибка при обработке запроса.")

        def _on_stream_token(self, token: str):
            """Handle a single streamed token — update placeholder live."""
            # Если первый «токен» — sentinel install workflow, не показываем
            # его пользователю. Финал обработает _on_worker_finished.
            if isinstance(token, str) and token.startswith("[LINA-INSTALL]"):
                return
            controller.stream_token(token)

        def _on_worker_finished(self, response: str):
            try:
                self._disconnect_stop_signal()
                pid = getattr(self, '_current_placeholder_id', None)

                # ── Install workflow trigger ────────────────────────
                if response and response.startswith("[LINA-INSTALL]"):
                    target = response[len("[LINA-INSTALL]"):].strip()
                    if pid is not None and target:
                        self._start_install_workflow(target, pid)
                        return  # workflow возьмёт управление, finally сработает

                if pid is not None:
                    text = response.strip() if response else ""
                    if not text:
                        text = ("⚠ Пустой ответ. "
                                "Возможно, LLM-модель не загружена.")
                    controller.update_message(
                        pid, text, MessageStatus.COMPLETE)
                    self._current_placeholder_id = None
                    controller._current_stream_id = None

                    # Check for executable commands in response
                    self._check_for_executable_commands(text)
            except Exception as e:
                logger.error("Error updating response: %s", e)
            finally:
                controller._is_generating = False
                self._current_worker = None
                if controller._on_generation_finished:
                    controller._on_generation_finished()

        def _on_worker_error(self, error_msg: str):
            try:
                self._disconnect_stop_signal()
                pid = getattr(self, '_current_placeholder_id', None)
                if pid is not None:
                    controller.update_message(
                        pid, "❌ Произошла ошибка при обработке запроса.",
                        MessageStatus.ERROR)
                    self._current_placeholder_id = None
                    controller._current_stream_id = None
            except Exception as e:
                logger.error("Error displaying worker error: %s", e)
            finally:
                controller._is_generating = False
                self._current_worker = None
                if controller._on_generation_finished:
                    controller._on_generation_finished()
                self.status_bar.set_mode("error")
                QtCore.QTimer.singleShot(
                    5000, lambda: self.status_bar.set_mode("ready"))

        # ── Embedded Terminal (install commands) ──

        def _start_install_workflow(self, target: str, placeholder_id) -> None:
            """Запустить InstallWorkflow для target и привязать его прогресс
            к placeholder-сообщению (заменяя «⏳ Думаю...»).

            Если предыдущий workflow ещё жив (например, пользователь снова
            набрал «установи X» пока шла установка) — отменяем его. Это
            гарантирует:
              • один активный subscriber на terminal.command_finished,
              • убийство зависшего PTY-процесса (sudo ждал пароль),
              • никаких параллельных диалогов подтверждения.
            """
            # Cancel any previous workflow before starting a new one.
            prev_wf = getattr(self, "_install_workflow", None)
            if prev_wf is not None:
                logger.info(
                    "Install workflow: cancelling previous workflow "
                    "before starting new one for '%s'", target,
                )
                try:
                    prev_wf.cancel()
                except Exception as e:
                    logger.debug("Previous workflow cancel failed: %s", e)
                self._install_workflow = None

            try:
                from lina.gui.install_workflow import (
                    InstallWorkflow, InstallResult,
                )
            except Exception as e:
                logger.error("install_workflow import failed: %s", e, exc_info=True)
                controller.update_message(
                    placeholder_id,
                    "⚠ Не удалось загрузить install workflow.",
                    MessageStatus.ERROR,
                )
                return

            self._current_placeholder_id = None
            controller._current_stream_id = None

            def _on_card(text: str) -> None:
                # Обновляем сообщение каждый раз когда workflow меняет карточку.
                try:
                    controller.update_message(
                        placeholder_id, text, MessageStatus.PENDING,
                    )
                except Exception as e:
                    logger.debug("install card update failed: %s", e)

            def _on_done(result) -> None:
                final_status = (
                    MessageStatus.COMPLETE if result.success
                    else MessageStatus.ERROR
                )
                final_text = (
                    result.card.render() if result.card else
                    (f"✅ Установлено: {result.target}" if result.success
                     else f"❌ Не удалось установить «{result.target}»: "
                          f"{result.reason}")
                )
                try:
                    controller.update_message(
                        placeholder_id, final_text, final_status,
                    )
                except Exception as e:
                    logger.debug("install final update failed: %s", e)
                self._install_workflow = None

            def _on_confirm(title: str, message: str) -> bool:
                # Спрашиваем пользователя через QMessageBox. Работает только
                # для удалений/потенциально опасных действий.
                # Гарантируем что окно поднимется поверх всего.
                self.raise_()
                self.activateWindow()
                box = QtWidgets.QMessageBox(self)
                box.setIcon(QtWidgets.QMessageBox.Icon.Question)
                box.setWindowTitle(title)
                box.setText(message)
                box.setStandardButtons(
                    QtWidgets.QMessageBox.StandardButton.Yes
                    | QtWidgets.QMessageBox.StandardButton.No
                )
                box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
                box.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
                logger.info("Install workflow: showing confirm dialog '%s'", title)
                reply = box.exec()
                logger.info("Install workflow: confirm reply=%s", reply)
                return reply == QtWidgets.QMessageBox.StandardButton.Yes

            def _on_password(reason: str):
                """Спросить sudo-пароль модальным диалогом.

                Возвращает строку с паролем или None если пользователь
                отменил/закрыл окно. Пароль не логируется и не хранится
                нигде кроме памяти workflow.
                """
                self.raise_()
                self.activateWindow()
                pw, ok = QtWidgets.QInputDialog.getText(
                    self,
                    "Требуется пароль sudo",
                    reason + "\n\nВведите пароль для пользователя:",
                    QtWidgets.QLineEdit.EchoMode.Password,
                )
                logger.info("Install workflow: sudo password dialog ok=%s", ok)
                if not ok or not pw:
                    return None
                return pw

            wf = InstallWorkflow(
                target=target,
                terminal=self.terminal,
                on_card_update=_on_card,
                on_done=_on_done,
                on_confirm_request=_on_confirm,
                on_password_request=_on_password,
            )
            self._install_workflow = wf  # держим ссылку чтобы не GC'нулась
            logger.info("Install workflow started for '%s'", target)
            wf.start()

        def _check_for_executable_commands(self, text: str):
            """Detect executable commands in bot response and show action bar."""
            try:
                from lina.gui.terminal_widget import extract_executable_commands
                commands = list(extract_executable_commands(text))

                try:
                    from lina.core.system_interaction import extract_commands
                    for extracted in extract_commands(text):
                        cmd = extracted.command.strip()
                        if cmd and cmd not in commands:
                            commands.append(cmd)
                except Exception as e:
                    logger.debug("Generic command extraction skipped: %s", e)

                if commands:
                    self.command_bar.show_commands(commands)
                else:
                    self.command_bar.hide()
            except Exception as e:
                logger.error("Command extraction error: %s", e)

        def _on_execute_command(self, command: str):
            """User confirmed — run command in embedded terminal."""
            try:
                # Блокируем опасные команды (`curl ... | sh`, `rm -rf /`,
                # форк-бомбы, dd if=, и т.п.). Делаем проверку через тот же
                # детектор что и в CLI-pipeline (`extract_commands`), чтобы
                # GUI и CLI имели одинаковую модель безопасности.
                from lina.core.system_interaction import _DANGEROUS_RE
                if _DANGEROUS_RE.search(command):
                    logger.warning("GUI: blocked dangerous command: %s", command)
                    self.command_bar.hide()
                    controller.add_message(
                        MessageRole.SYSTEM,
                        "⛔ Команда заблокирована как опасная: "
                        f"`{command}`\n"
                        "Чаще всего это «скачать-и-выполнить» (`curl … | sh`) "
                        "или необратимое удаление. Сейчас попрошу Lina "
                        "предложить безопасный способ.",
                    )
                    # Сразу запускаем repair-flow — так пользователь не
                    # остаётся с заблокированной командой и без альтернативы.
                    self._auto_repair_after_block(command)
                    return
                self.command_bar.hide()
                self.terminal.run_command(command)
                self.status_bar.set_mode("executing")
                self.title_bar.set_status("Выполняю…", "#d29922")
            except Exception as e:
                logger.error("Terminal execution error: %s", e)
                controller.add_message(
                    MessageRole.SYSTEM,
                    f"❌ Ошибка запуска: {e}")

        def _on_terminal_finished(self, exit_code: int, command: str, output: str = ""):
            """Terminal command completed.

            On success — quietly mark the command as done.
            On error — automatically ask the LLM for a fix attempt
            using the captured stderr/stdout, so the user does not
            have to debug it themselves.
            """
            # Если активен install_workflow — он сам обрабатывает сигнал
            # терминала и всю диагностику. Стандартный auto-repair в это
            # время МОЛЧИТ, иначе оба механизма одновременно лезут с
            # советами и дублируют сообщения.
            if self._install_workflow is not None:
                return
            try:
                if exit_code == 0:
                    controller.add_message(
                        MessageRole.SYSTEM,
                        f"✅ Команда выполнена: {command}")
                    # Сброс счётчика repair при успехе
                    self._repair_attempts["count"] = 0
                    self._repair_attempts["last_cmd"] = ""
                elif exit_code == -1:
                    controller.add_message(
                        MessageRole.SYSTEM,
                        f"⚠ Команда остановлена: {command}")
                else:
                    # Compact, useful tail of the output (stderr + last lines).
                    tail = self._format_output_tail(output)
                    controller.add_message(
                        MessageRole.SYSTEM,
                        f"❌ Ошибка (код {exit_code}): {command}\n{tail}"
                        if tail else
                        f"❌ Ошибка (код {exit_code}): {command}",
                    )
                    # Ask LLM to diagnose and propose a fix.
                    self._auto_repair_after_error(command, exit_code, tail)
            except Exception as e:
                logger.error("Terminal finish handling error: %s", e)
            finally:
                self.status_bar.set_mode("ready")
                self.title_bar.set_status("В сети", "#3fb950")

        @staticmethod
        def _format_output_tail(output: str, max_chars: int = 600) -> str:
            """Trim verbose terminal output to the last ~600 chars,
            preferring lines with 'error' / 'fail' / 'denied'."""
            if not output:
                return ""
            cleaned = output.strip()
            if not cleaned:
                return ""
            # Prefer error-marker lines.
            lines = cleaned.splitlines()
            error_lines = [
                ln for ln in lines
                if any(kw in ln.lower() for kw in
                       ("error", "fail", "denied", "not found", "ошибк",
                        "невозможно", "недоступн"))
            ]
            picked = error_lines[-12:] if error_lines else lines[-12:]
            tail = "\n".join(picked).strip()
            if len(tail) > max_chars:
                tail = tail[-max_chars:]
            return tail

        def _auto_repair_after_error(self, command: str, exit_code: int,
                                     tail: str) -> None:
            """Auto-recover from a failed terminal command.

            Стратегия:
              1. Классифицируем ошибку по выводу (`tail`).
              2. Для известных классов (сеть/прокси/DNS, permission, missing
                 file) — даём ГОТОВЫЙ человекочитаемый ответ без LLM. Это
                 быстро, надёжно и не зависит от размера контекста.
              3. Для неизвестных — обращаемся к LLM, но с КРАТКИМ промптом
                 и без истории, чтобы влезло в малый recovery-контекст.
              4. Лимит — 1 попытка на сессию для одной команды.
            """
            if controller.is_generating():
                logger.debug("Auto-repair skipped: generation in flight")
                return

            # Лимит на повторные попытки одной и той же команды.
            if (self._repair_attempts["last_cmd"] == command
                    or self._repair_attempts["count"] >= 1):
                logger.info(
                    "Auto-repair: skipping (already tried %d times)",
                    self._repair_attempts["count"],
                )
                return
            self._repair_attempts["count"] += 1
            self._repair_attempts["last_cmd"] = command

            tail_l = (tail or "").lower()

            # ── 1. Сетевая проблема — ответ без LLM ────────────────────────
            # Это самый частый кейс на твоей системе (SOCKS5/прокси).
            # LLM на маленьком контексте + сетевой tail = пустой ответ.
            # Готовый совет надёжнее.
            if any(k in tail_l for k in (
                "socks5", "rejected by the socks", "could not resolve host",
                "name or service not known", "network is unreachable",
                "no route to host", "connection refused", "connection timed out",
                "failed to retrieve", "could not download",
                "временно недоступн", "сеть недоступн", "host is unreachable",
            )):
                logger.info("Auto-repair: network error, giving canned response")
                self._show_network_repair_advice(command)
                return

            # ── 2. Permission denied — тоже без LLM ────────────────────────
            if any(k in tail_l for k in (
                "permission denied", "operation not permitted",
                "must be root", "cannot perform this operation",
                "you cannot perform",
            )) and not command.lstrip().startswith("sudo "):
                logger.info("Auto-repair: missing sudo, suggesting it")
                self._show_sudo_repair_advice(command)
                return

            # ── 3. database lock (pacman) — без LLM ────────────────────────
            if "unable to lock database" in tail_l or "could not lock database" in tail_l:
                logger.info("Auto-repair: pacman db lock, suggesting cleanup")
                controller.add_message(
                    MessageRole.ASSISTANT,
                    "База pacman заблокирована другим процессом или осталась "
                    "после прерванной транзакции.\n\n"
                    "Сначала проверь, не запущен ли pacman:\n"
                    "```bash\n"
                    "pgrep -a pacman\n"
                    "```\n"
                    "Если процесса нет — снять блокировку:\n"
                    "```bash\n"
                    "sudo rm /var/lib/pacman/db.lck\n"
                    "```",
                )
                return

            # ── 4. Остальное — короткий запрос к LLM без истории ───────────
            self._llm_repair(command, exit_code, tail)

        def _show_network_repair_advice(self, command: str) -> None:
            """Готовый совет по сетевым проблемам pacman."""
            text = (
                "🌐 Это сетевая проблема — пакет не скачивается с зеркал. "
                "Возможные причины: активный SOCKS/VPN-прокси режет соединение "
                "к зеркалам Arch, медленные/упавшие зеркала, или нет интернета.\n\n"
                "Проверь интернет:\n"
                "```bash\n"
                "ping -c2 archlinux.org\n"
                "```\n"
                "Если интернет есть, а проблема в зеркалах — обнови их "
                "(для CachyOS):\n"
                "```bash\n"
                "sudo cachyos-rate-mirrors\n"
                "```\n"
                "Затем повтори установку. Если проблема в SOCKS-прокси — "
                "временно отключи его и попробуй снова."
            )
            controller.add_message(MessageRole.ASSISTANT, text)

        def _show_sudo_repair_advice(self, command: str) -> None:
            """Готовый совет если забыли sudo."""
            fixed = "sudo " + command.lstrip()
            controller.add_message(
                MessageRole.ASSISTANT,
                "Команда требует прав root. Добавлю sudo:\n\n"
                f"```bash\n{fixed}\n```",
            )

        def _llm_repair(self, command: str, exit_code: int, tail: str) -> None:
            """Минимальный LLM-проход для редких ошибок без истории."""
            tail_short = (tail or "")[-300:]
            prompt = (
                f"[LINA-REPAIR]\n"
                f"Команда: `{command}`\n"
                f"Код: {exit_code}\n"
                f"Вывод (последние строки):\n```\n{tail_short}\n```\n"
                "Объясни причину 1 предложением и дай ОДНУ исправленную "
                "команду в ```bash блоке. Без рассуждений."
            )

            try:
                logger.info("Auto-repair: querying LLM for fix (exit=%d)", exit_code)
                controller.add_message(
                    MessageRole.SYSTEM,
                    "🔧 Пытаюсь подобрать исправление…",
                )

                use_streaming = getattr(controller, '_stream_handler', None)
                if not use_streaming:
                    handler = getattr(controller, '_request_handler', None)
                    if not handler:
                        return
                    fix_text = handler(prompt)
                    controller.add_message(MessageRole.ASSISTANT, fix_text)
                    return

                controller._is_generating = True
                if controller._on_generation_started:
                    controller._on_generation_started()
                placeholder = controller.add_message(
                    MessageRole.ASSISTANT, "⏳ Думаю...",
                    status=MessageStatus.PENDING,
                )
                self._current_placeholder_id = placeholder.message_id
                controller._current_stream_id = placeholder.message_id

                from lina.gui.workers import create_streaming_worker_class
                StreamWorker = create_streaming_worker_class()
                worker = StreamWorker(controller._stream_handler, prompt)
                worker.token.connect(self._on_stream_token)
                worker.finished.connect(self._on_worker_finished)
                worker.error.connect(self._on_worker_error)
                self.input_bar.stop_requested.connect(worker.cancel)
                self._current_worker = worker
                self._workers.append(worker)
                worker.start()
            except Exception as e:
                logger.error("Auto-repair dispatch failed: %s", e, exc_info=True)
                controller._is_generating = False

        def _auto_repair_after_block(self, command: str) -> None:
            """Send a blocked dangerous command back to the LLM with a
            request to propose a safe alternative.

            Тригерится из `_on_execute_command` когда команда отбита
            `_DANGEROUS_RE` (например `curl ... | sh`).
            """
            if controller.is_generating():
                logger.debug("Auto-repair-block skipped: generation in flight")
                return

            prompt = (
                f"[LINA-REPAIR]\n"
                f"Эта команда отклонена системой безопасности как "
                f"«скачать-и-выполнить» / необратимое удаление:\n"
                f"```bash\n{command}\n```\n"
                "Предложи БЕЗОПАСНУЮ альтернативу для текущего "
                "пакетного менеджера: пакет в репозитории, AUR, "
                "Flatpak, или скачать AppImage руками. "
                "Только ОДИН ```bash блок с альтернативой. "
                "Без рассуждений."
            )

            try:
                logger.info("Auto-repair-block: querying LLM for safe alternative")
                use_streaming = getattr(controller, '_stream_handler', None)
                if not use_streaming:
                    handler = getattr(controller, '_request_handler', None)
                    if not handler:
                        return
                    fix_text = handler(prompt)
                    controller.add_message(MessageRole.ASSISTANT, fix_text)
                    return

                controller._is_generating = True
                if controller._on_generation_started:
                    controller._on_generation_started()
                placeholder = controller.add_message(
                    MessageRole.ASSISTANT, "⏳ Думаю...",
                    status=MessageStatus.PENDING,
                )
                self._current_placeholder_id = placeholder.message_id
                controller._current_stream_id = placeholder.message_id

                from lina.gui.workers import create_streaming_worker_class
                StreamWorker = create_streaming_worker_class()
                worker = StreamWorker(controller._stream_handler, prompt)
                worker.token.connect(self._on_stream_token)
                worker.finished.connect(self._on_worker_finished)
                worker.error.connect(self._on_worker_error)
                self.input_bar.stop_requested.connect(worker.cancel)
                self._current_worker = worker
                self._workers.append(worker)
                worker.start()
            except Exception as e:
                logger.error("Auto-repair-block dispatch failed: %s", e, exc_info=True)
                controller._is_generating = False

        def _cleanup_worker(self, worker):
            try:
                if worker in self._workers:
                    self._workers.remove(worker)
            except Exception:
                pass

        def _disconnect_stop_signal(self):
            """Safely disconnect stop_requested from current worker."""
            worker = getattr(self, '_current_worker', None)
            if worker is not None:
                try:
                    self.input_bar.stop_requested.disconnect(worker.cancel)
                except (TypeError, RuntimeError):
                    pass  # already disconnected or worker deleted

        # ── Error Display (Block J) ──

        def _show_error(self, error_msg: str):
            """Display an error as a system message in the chat."""
            controller.add_message(
                MessageRole.SYSTEM,
                f"❌ {error_msg}",
                status=MessageStatus.ERROR,
            )

        # ── Confirmation (Block E) ──

        def _on_confirm(self, esc_id: str):
            controller.send_user_message(f"/confirm {esc_id}")
            self.confirmation_bar.dismiss()
            self.status_bar.set_mode("ready")

        def _on_deny(self, esc_id: str):
            controller.send_user_message(f"/deny {esc_id}")
            self.confirmation_bar.dismiss()
            self.status_bar.set_mode("ready")

        # ── Theme (Block G) ──

        def _toggle_theme(self):
            """Switch between light and dark themes (called from title bar)."""
            current = getattr(self, "_current_theme_name", "dark")
            new_name = "light" if current == "dark" else "dark"
            logger.info("GUI: theme %s → %s", current, new_name)
            self._apply_theme(new_name)

        def _apply_theme(self, theme_name: str):
            try:
                theme = get_theme(theme_name)
                stylesheet = build_stylesheet(theme)
                controller.set_markdown_palette(
                    code_bg=theme.code_bg,
                    code_fg=theme.code_fg,
                    accent=theme.primary,
                    code_border=theme.code_border,
                    inline_code_bg=theme.inline_code_bg,
                    inline_code_fg=theme.inline_code_fg,
                    muted=theme.message_meta,
                )
                self.setStyleSheet(stylesheet)
                self.chat_view.set_theme(theme)
                if hasattr(self, "_chat_shadow"):
                    alpha = 30 if theme_name == "light" else 90
                    self._chat_shadow.setColor(
                        QtGui.QColor(30, 20, 80, alpha))
                self._current_theme_name = theme_name
                gui_config.theme_name = theme_name
                self.chat_view.render_messages(controller.get_messages())
                settings.set("gui", "theme", theme_name)
                logger.info(f"Тема применена: {theme_name}")
            except Exception as e:
                logger.error("Ошибка применения темы: %s", e)

        def _open_settings_dialog(self):
            """Open settings dialog from the main window."""
            try:
                from lina.gui.settings_dialog import create_settings_dialog
                dialog = create_settings_dialog(parent=self, settings=settings)
                dialog.exec()
            except Exception as e:
                logger.error("Settings dialog error: %s", e, exc_info=True)
                self._show_error("Не удалось открыть настройки.")

        # ── Menu ──

        def _show_menu_popup(self):
            """Show popup menu from title bar ⋮ button.

            Только действия, которых нет в title bar:
            настройки, очистка чата, помощь, выход.
            Переключения темы — в title bar.
            """
            menu = QtWidgets.QMenu(self)
            settings_action = menu.addAction("⚙  Настройки")
            settings_action.triggered.connect(self._open_settings_dialog)
            menu.addSeparator()
            clear_action = menu.addAction("🗑  Очистить чат")
            clear_action.triggered.connect(self._on_clear_history)
            menu.addSeparator()
            about_action = menu.addAction("ℹ  О программе")
            about_action.triggered.connect(self._show_about)
            help_action = menu.addAction("❓  Команды")
            help_action.triggered.connect(self._show_help)
            menu.addSeparator()
            quit_action = menu.addAction("⏻  Выход")
            quit_action.triggered.connect(self._on_quit)
            menu.exec(self.title_bar._menu_btn.mapToGlobal(
                QtCore.QPoint(0, self.title_bar._menu_btn.height())))

        def _on_clear_history(self):
            count = controller.clear_history()
            self.chat_view.render_messages([])
            controller.add_message(
                MessageRole.SYSTEM,
                f"История очищена ({count} сообщений).",
            )
            self._persist_current_messages()

        def _on_quit(self):
            self._persist_current_messages()
            for w in self._workers:
                if w.isRunning():
                    w.quit()
                    w.wait(2000)
            QtWidgets.QApplication.instance().quit()

        def _show_about(self):
            QtWidgets.QMessageBox.about(
                self,
                "О программе Lina",
                "<h3>Lina AI Assistant</h3>"
                "<p>Версия 0.7.0</p>"
                "<p>Локальный ИИ-помощник для Linux.</p>"
                "<p>Все действия проходят через governance pipeline.</p>",
            )

        def _show_help(self):
            controller.send_user_message("/help")

        # ── Tray Integration (Block H) ──

        def toggle_visibility(self):
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.raise_()
                self.activateWindow()

        # ── Window Events ──

        def closeEvent(self, event):
            self._persist_current_messages()
            if (self.tray_controller
                    and self.tray_controller.is_visible()
                    and settings.gui.show_tray_icon):
                event.ignore()
                self.hide()
                self.tray_controller.notify(
                    "Lina", "Свёрнута в трей. Нажмите иконку для открытия.")
            else:
                self._on_quit()
                event.accept()

        def keyPressEvent(self, event):
            if event.key() == QtCore.Qt.Key.Key_Escape:
                if self.tray_controller and self.tray_controller.is_visible():
                    self.hide()
            else:
                super().keyPressEvent(event)

    # Create and return
    window = LinaMainWindow()
    return window
