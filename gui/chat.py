"""
Lina GUI — Chat Controller & Markdown Parser.

Модель данных чата, Markdown→HTML парсер, ChatController.
Не зависит от Qt. GUI подписывается на колбэки.
"""

from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Any
from enum import Enum

logger = logging.getLogger("lina.gui.chat")


# ─── Модель данных ─────────────────────────────────────────────────────────────

class MessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageStatus(Enum):
    PENDING = "pending"
    STREAMING = "streaming"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class ChatMessage:
    """Одно сообщение в чате."""
    role: MessageRole
    content: str
    timestamp: float = field(default_factory=time.time)
    status: MessageStatus = MessageStatus.COMPLETE
    metadata: Dict[str, Any] = field(default_factory=dict)
    message_id: int = 0

    def to_dict(self) -> Dict:
        return {
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp,
            "status": self.status.value,
            "message_id": self.message_id,
        }


@dataclass
class CodeBlock:
    """Извлечённый блок кода."""
    code: str
    language: str = ""
    line_start: int = 0


# ─── Парсер Markdown ──────────────────────────────────────────────────────────

class MarkdownParser:
    """Лёгкий парсер Markdown → HTML для чата.

    Поддерживает:
      - ```code blocks``` с подсветкой
      - **bold**, *italic*, `inline code`
      - Списки (- и 1.)
      - Горизонтальные линии (---)
    """

    # Регулярки
    CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    INLINE_CODE_RE = re.compile(r"`([^`]+)`")
    BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
    ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
    HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    UL_ITEM_RE = re.compile(r"^[-*]\s+(.+)$", re.MULTILINE)
    OL_ITEM_RE = re.compile(r"^(\d+)\.\s+(.+)$", re.MULTILINE)
    HR_RE = re.compile(r"^---+$", re.MULTILINE)

    def __init__(
        self,
        code_bg: str = "#0b0f14",
        code_fg: str = "#f0f4ff",
        accent: str = "#4f8cff",
        code_border: str = "#202835",
        inline_code_bg: str = "#111723",
        inline_code_fg: str = "#9fc1ff",
        muted: str = "#97a3b3",
    ):
        self.code_bg = code_bg
        self.code_fg = code_fg
        self.accent = accent
        self.code_border = code_border
        self.inline_code_bg = inline_code_bg
        self.inline_code_fg = inline_code_fg
        self.muted = muted

    def set_palette(
        self,
        code_bg: str,
        code_fg: str,
        accent: str,
        code_border: str | None = None,
        inline_code_bg: str | None = None,
        inline_code_fg: str | None = None,
        muted: str | None = None,
    ) -> None:
        """Обновляет палитру markdown без пересоздания парсера."""
        self.code_bg = code_bg
        self.code_fg = code_fg
        self.accent = accent
        if code_border is not None:
            self.code_border = code_border
        if inline_code_bg is not None:
            self.inline_code_bg = inline_code_bg
        if inline_code_fg is not None:
            self.inline_code_fg = inline_code_fg
        if muted is not None:
            self.muted = muted

    def parse(self, text: str) -> str:
        """Конвертирует Markdown → HTML."""
        if not text:
            return ""

        # Защищаем code blocks от остального парсинга
        code_blocks: List[str] = []

        def stash_code(m):
            lang = m.group(1)
            code = m.group(2).rstrip()
            idx = len(code_blocks)
            label_html = ""
            if lang:
                label_html = (
                    f'<div style="color:{self.muted};font-size:10px;'
                    f'font-weight:700;letter-spacing:0.12em;'
                    f'text-transform:uppercase;margin:0 0 8px 0;">'
                    f'{self._escape(lang)}</div>'
                )
            html = (
                f'<div style="margin:10px 0 8px 0;">'
                f'<div style="background:{self.code_bg};'
                f'border:1px solid {self.code_border};border-radius:12px;'
                f'padding:12px 14px;">'
                f'{label_html}'
                f'<pre style="color:{self.code_fg};margin:0;'
                f'white-space:pre-wrap;font-family:&quot;JetBrains Mono&quot;,'
                f'&quot;Fira Code&quot;,monospace;font-size:13px;'
                f'line-height:1.6;">{self._escape(code)}</pre>'
                f'</div>'
                f'</div>'
            )
            code_blocks.append(html)
            return f"__CODE_BLOCK_{idx}__"

        result = self.CODE_BLOCK_RE.sub(stash_code, text)

        # Inline code
        result = self.INLINE_CODE_RE.sub(
            lambda m: (
                f'<code style="background:{self.inline_code_bg};'
                f'color:{self.inline_code_fg};padding:1px 6px;'
                f'border:1px solid {self.code_border};border-radius:6px;'
                f'font-family:&quot;JetBrains Mono&quot;,&quot;Fira Code&quot;,'
                f'monospace;">'
                f'{self._escape(m.group(1))}</code>'
            ),
            result,
        )

        # Bold, italic
        result = self.BOLD_RE.sub(r"<b>\1</b>", result)
        result = self.ITALIC_RE.sub(r"<i>\1</i>", result)

        # Headings
        def heading_repl(m):
            level = len(m.group(1))
            sizes = {1: "24px", 2: "20px", 3: "18px"}
            return (
                f'<div style="font-size:{sizes.get(level, "18px")};'
                f'font-weight:700;line-height:1.3;margin:12px 0 8px 0;">'
                f'{m.group(2)}</div>'
            )

        result = self.HEADING_RE.sub(heading_repl, result)

        # Horizontal rule
        result = self.HR_RE.sub(
            f'<hr style="border:none;border-top:1px solid {self.code_border};'
            f'margin:14px 0;">',
            result,
        )

        # Lists (unordered)
        result = self.UL_ITEM_RE.sub(r"  • \1", result)

        # Lists (ordered)
        result = self.OL_ITEM_RE.sub(r"  \1. \2", result)

        # Newlines → <br>
        result = result.replace("\n", "<br>")

        # Восстанавливаем code blocks
        for idx, block in enumerate(code_blocks):
            result = result.replace(f"__CODE_BLOCK_{idx}__", block)

        return result

    def extract_code_blocks(self, text: str) -> List[CodeBlock]:
        """Извлекает все блоки кода из текста."""
        blocks = []
        for m in self.CODE_BLOCK_RE.finditer(text):
            blocks.append(CodeBlock(
                code=m.group(2).strip(),
                language=m.group(1),
                line_start=text[:m.start()].count("\n"),
            ))
        return blocks

    @staticmethod
    def _escape(text: str) -> str:
        """Экранирует HTML."""
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


# ─── Контроллер чата (без Qt-зависимости) ─────────────────────────────────────

class ChatController:
    """Управляет логикой чата: историей, отправкой, streaming.

    Не зависит от Qt. Qt-виджет подписывается на колбэки.
    """

    def __init__(self, max_history: int = 500):
        self._messages: List[ChatMessage] = []
        self._max_history = max_history
        self._next_id = 1
        self._is_generating = False
        self._current_stream_id: Optional[int] = None
        self._parser = MarkdownParser()

        # Колбэки для UI
        self._on_message_added: Optional[Callable[[ChatMessage], None]] = None
        self._on_message_updated: Optional[Callable[[ChatMessage], None]] = None
        self._on_generation_started: Optional[Callable[[], None]] = None
        self._on_generation_finished: Optional[Callable[[], None]] = None

        # Обработчик запросов (подключается извне)
        self._request_handler: Optional[Callable[[str], str]] = None
        self._stream_handler: Optional[Callable] = None

        # TTS response callback (вызывается при завершении ответа)
        self._on_tts_response: Optional[Callable[[str], None]] = None

        logger.info("ChatController создан")

    # ── Колбэки UI ──

    def set_on_message_added(self, cb: Callable) -> None:
        self._on_message_added = cb

    def set_on_message_updated(self, cb: Callable) -> None:
        self._on_message_updated = cb

    def set_on_generation_started(self, cb: Callable) -> None:
        self._on_generation_started = cb

    def set_on_generation_finished(self, cb: Callable) -> None:
        self._on_generation_finished = cb

    def set_on_tts_response(self, cb: Callable[[str], None]) -> None:
        """Устанавливает колбэк озвучки ответа (TTS)."""
        self._on_tts_response = cb

    def set_request_handler(self, handler: Callable[[str], str]) -> None:
        """Устанавливает обработчик запросов (pipeline)."""
        self._request_handler = handler

    def set_stream_handler(self, handler: Callable) -> None:
        """Устанавливает streaming-обработчик (yields tokens)."""
        self._stream_handler = handler

    # ── Управление сообщениями ──

    def add_message(self, role: MessageRole, content: str,
                    status: MessageStatus = MessageStatus.COMPLETE,
                    **metadata) -> ChatMessage:
        """Добавляет сообщение в историю."""
        msg = ChatMessage(
            role=role,
            content=content,
            status=status,
            metadata=metadata,
            message_id=self._next_id,
        )
        self._next_id += 1
        self._messages.append(msg)

        # Обрезаем историю
        if len(self._messages) > self._max_history:
            self._messages = self._messages[-self._max_history:]

        if self._on_message_added:
            self._on_message_added(msg)

        return msg

    def update_message(self, message_id: int, content: str,
                       status: Optional[MessageStatus] = None) -> Optional[ChatMessage]:
        """Обновляет содержимое сообщения (для streaming)."""
        for msg in reversed(self._messages):
            if msg.message_id == message_id:
                msg.content = content
                if status:
                    msg.status = status
                    # Обновить timestamp при завершении генерации,
                    # чтобы показывать время ответа, а не начала обработки
                    if status == MessageStatus.COMPLETE:
                        msg.timestamp = time.time()
                if self._on_message_updated:
                    self._on_message_updated(msg)
                return msg
        return None

    def get_messages(self) -> List[ChatMessage]:
        """Возвращает историю сообщений."""
        return list(self._messages)

    def get_message(self, message_id: int) -> Optional[ChatMessage]:
        """Находит сообщение по ID."""
        for msg in self._messages:
            if msg.message_id == message_id:
                return msg
        return None

    def get_last_message(self) -> Optional[ChatMessage]:
        """Последнее сообщение."""
        return self._messages[-1] if self._messages else None

    def clear_history(self) -> int:
        """Очищает историю. Возвращает кол-во удалённых."""
        count = len(self._messages)
        self._messages.clear()
        self._next_id = 1
        logger.info("История очищена: %d сообщений", count)
        return count

    def message_count(self) -> int:
        """Количество сообщений в истории."""
        return len(self._messages)

    # ── Отправка запросов ──

    def send_user_message(self, text: str) -> Optional[ChatMessage]:
        """Обрабатывает пользовательский ввод через Intent Bridge.

        Phase 1: UI НЕ выполняет — UI генерирует Intent.
        Phase 3: /confirm and /deny commands intercepted before Intent routing.

        1. Проверяет /confirm и /deny команды
        2. Добавляет user message
        3. Создаёт Intent через IntentBridge
        4. Обрабатывает IntentResult (success/denied/needs_confirm)
        5. Добавляет assistant response

        Returns:
            ChatMessage с ответом (или None если нет handler)
        """
        text = text.strip()
        if not text:
            return None

        # ── Phase 3: Intercept /confirm and /deny commands ──
        confirm_response = self._handle_confirm_deny(text)
        if confirm_response is not None:
            self.add_message(MessageRole.USER, text)
            return self.add_message(
                MessageRole.ASSISTANT, confirm_response,
                status=MessageStatus.COMPLETE,
            )

        # Добавляем сообщение пользователя
        self.add_message(MessageRole.USER, text)

        # Генерация ответа
        self._is_generating = True
        if self._on_generation_started:
            self._on_generation_started()

        try:
            # Создаём placeholder для streaming
            placeholder = self.add_message(
                MessageRole.ASSISTANT, "⏳ Думаю...",
                status=MessageStatus.PENDING,
            )
            self._current_stream_id = placeholder.message_id

            # ── Phase 1: Intent Bridge ──
            response = self._process_via_intent(text)

            # Обновляем сообщение
            self.update_message(
                placeholder.message_id,
                response,
                status=MessageStatus.COMPLETE,
            )

            # v0.9.0: TTS — озвучиваем ответ если есть callback
            if self._on_tts_response and response:
                try:
                    self._on_tts_response(response)
                except Exception as e:
                    logger.warning("TTS response callback error: %s", e)

            return placeholder

        except Exception as e:
            logger.error("Ошибка генерации: %s", e)
            return self.add_message(
                MessageRole.SYSTEM,
                "❌ Произошла внутренняя ошибка. Попробуйте ещё раз.",
                status=MessageStatus.ERROR,
            )
        finally:
            self._is_generating = False
            self._current_stream_id = None
            if self._on_generation_finished:
                self._on_generation_finished()

    # ── Phase 3: /confirm and /deny command parsing ──────────────

    def _handle_confirm_deny(self, text: str) -> Optional[str]:
        """
        Parse /confirm <esc_id> and /deny <esc_id> commands.

        Returns:
            Response text if command was handled, None otherwise.
        """
        import re
        match = re.match(
            r"^/(confirm|deny)\s+([a-zA-Z0-9_-]+)\s*$", text.strip())
        if not match:
            return None

        action = match.group(1)  # "confirm" or "deny"
        esc_id = match.group(2)
        approved = (action == "confirm")

        try:
            from lina.governance.confirmation import get_confirmation_handler
            handler = get_confirmation_handler()
            resolved = handler.resolve(esc_id, approved)
            if approved:
                if resolved:
                    return "✅ Подтверждено и выполнено."
                return "⚠ Подтверждение не удалось (ID не найден или истёк)."
            else:
                return "🚫 Операция отклонена."
        except ImportError:
            logger.warning("ConfirmationHandler not available")
            return "⚠ Обработчик подтверждений недоступен."
        except Exception as e:
            logger.error("Confirm/deny error: %s", e)
            return "❌ Произошла внутренняя ошибка при обработке команды."

    def _process_via_intent(self, text: str) -> str:
        """Route ALL user text through LLM pipeline.

        Execution order:
          0. /help → instant help (meta-command, not a question)
          1. IntentBridge → governance pipeline (access/policy/execution)
          2. Everything → LLM pipeline_handler (auto-loads model)

        ALL requests go through LLM. No fast-path bypass.
        Phase 4: ResponseFormatter for human-friendly output.
        """
        try:
            from lina.core.response_ux import get_response_formatter
            fmt = get_response_formatter()

            # 0. Help command (meta, not a real query)
            if fmt.is_help_command(text):
                return fmt.format_help()

            # ── 1. IntentBridge → governance ───────────────────────
            from lina.intent.bridge import get_intent_bridge
            from lina.intent.types import IntentStatus

            bridge = get_intent_bridge()
            result = bridge.from_text(
                text,
                source="ui",
                pipeline_handler=self._request_handler,
            )

            # NEEDS_CONFIRM — show confirm/deny instructions
            if result.status == IntentStatus.NEEDS_CONFIRM:
                return fmt.format_result(result)

            # CHAT_RESPONSE — use response_text from bridge (already processed)
            if result.status == IntentStatus.CHAT_RESPONSE:
                if result.response_text:
                    return result.response_text
                if self._request_handler:
                    return self._request_handler(text)
                return "⏳ Загружаю LLM-модель..."

            # NOT_FOUND — always go through LLM
            if result.status == IntentStatus.NOT_FOUND:
                if result.response_text:
                    return result.response_text
                if self._request_handler:
                    return self._request_handler(text)
                return (fmt.format_result(result)
                        or "⏳ Загружаю LLM-модель...")

            # All other statuses — format through UX layer
            domain = getattr(result, 'metadata', {}).get('domain', '')
            action = getattr(result, 'metadata', {}).get('action', '')
            return fmt.format_result(result, domain=domain, action=action)

        except ImportError:
            # Phase 3: NO bypass — governance unavailable = error
            logger.error("IntentBridge не доступен — governance routing failed")
            try:
                from lina.core.response_ux import get_response_formatter
                return get_response_formatter().format_degradation("governance")
            except ImportError:
                return "⚠ Governance pipeline недоступен. Действия заблокированы."
        except Exception as e:
            logger.error("IntentBridge error: %s", e)
            return "❌ Произошла внутренняя ошибка."

    # ── Fast-path: direct execution without LLM ─────────────────
    # NOTE: _try_direct() is DEPRECATED — preprocessor now runs in app.py
    # handlers (_handler/_stream_handler) which call _get_system_context().
    # Kept for backward compatibility; will be removed in v0.8.x.

    _preprocessor = None  # class-level lazy singleton

    def _try_direct(self, text: str) -> Optional[str]:
        """DEPRECATED: Use app.py _handler/_stream_handler preprocessor path.

        Try QueryPreprocessor for instant responses (no LLM needed).
        """
        import warnings
        warnings.warn(
            "ChatController._try_direct() is deprecated. "
            "Preprocessor now runs in app.py handlers.",
            DeprecationWarning, stacklevel=2,
        )
        try:
            if ChatController._preprocessor is None:
                from lina.core.system_interaction import QueryPreprocessor
                ChatController._preprocessor = QueryPreprocessor()
            return ChatController._preprocessor.try_direct_answer(text)
        except ImportError:
            logger.debug("QueryPreprocessor not available")
            return None
        except Exception as e:
            logger.warning("QueryPreprocessor error: %s", e)
            return None

    def stream_token(self, token: str) -> None:
        """Добавляет токен к текущему streaming-сообщению."""
        if self._current_stream_id is None:
            return
        for msg in reversed(self._messages):
            if msg.message_id == self._current_stream_id:
                if msg.status == MessageStatus.PENDING:
                    msg.content = token
                    msg.status = MessageStatus.STREAMING
                else:
                    msg.content += token
                if self._on_message_updated:
                    self._on_message_updated(msg)
                break

    def stop_generation(self) -> bool:
        """Прерывает текущую генерацию."""
        if not self._is_generating:
            return False
        self._is_generating = False
        if self._current_stream_id:
            msg = self.get_message(self._current_stream_id)
            if msg:
                self.update_message(
                    self._current_stream_id,
                    msg.content + "\n\n⚠ *Прервано*",
                    status=MessageStatus.COMPLETE,
                )
        logger.info("Генерация прервана")
        return True

    def is_generating(self) -> bool:
        """Идёт ли генерация ответа."""
        return self._is_generating

    # ── Markdown ──

    def render_markdown(self, text: str) -> str:
        """Конвертирует Markdown → HTML."""
        return self._parser.parse(text)

    def set_markdown_palette(
        self,
        code_bg: str,
        code_fg: str,
        accent: str,
        code_border: str | None = None,
        inline_code_bg: str | None = None,
        inline_code_fg: str | None = None,
        muted: str | None = None,
    ) -> None:
        """Обновляет цветовую схему markdown-парсера."""
        self._parser.set_palette(
            code_bg=code_bg,
            code_fg=code_fg,
            accent=accent,
            code_border=code_border,
            inline_code_bg=inline_code_bg,
            inline_code_fg=inline_code_fg,
            muted=muted,
        )

    def extract_code_blocks(self, text: str) -> List[CodeBlock]:
        """Извлекает блоки кода из текста."""
        return self._parser.extract_code_blocks(text)

    # ── Сериализация ──

    def to_dict(self) -> Dict:
        """Состояние чата."""
        return {
            "messages": [m.to_dict() for m in self._messages],
            "message_count": len(self._messages),
            "is_generating": self._is_generating,
            "has_handler": self._request_handler is not None,
        }

    def export_history(self) -> List[Dict]:
        """Экспорт истории для LLM context."""
        return [
            {"role": m.role.value, "content": m.content}
            for m in self._messages
            if m.status == MessageStatus.COMPLETE
            and m.role in (MessageRole.USER, MessageRole.ASSISTANT)
        ]


# ─── Qt Chat Window Builder ──────────────────────────────────────────────────

def create_chat_window(controller: ChatController,
                       width: int = 420, height: int = 620,
                       stylesheet: str = ""):
    """Создаёт Qt-окно чата из контроллера.

    Args:
        controller: ChatController
        width, height: размеры окна
        stylesheet: CSS-stylesheet

    Returns:
        QWidget — окно чата
    """
    from lina.gui import get_qt_modules
    QtWidgets, QtCore, QtGui = get_qt_modules()

    class ChatWindow(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.controller = controller
            self.setWindowTitle("Lina AI")
            self.setMinimumSize(360, 480)
            self.resize(width, height)
            if stylesheet:
                self.setStyleSheet(stylesheet)
            self._setup_ui()
            self._connect_signals()

        def _setup_ui(self):
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(4)

            # Header
            header = QtWidgets.QLabel("🤖 Lina AI Assistant")
            header.setStyleSheet("font-size:16px;font-weight:bold;padding:8px;")
            layout.addWidget(header)

            # Chat area
            self.chat_area = QtWidgets.QTextBrowser()
            self.chat_area.setOpenExternalLinks(True)
            self.chat_area.setReadOnly(True)
            layout.addWidget(self.chat_area, stretch=1)

            # Status label
            self.status_label = QtWidgets.QLabel("")
            self.status_label.setStyleSheet("color:#a6adc8;font-size:11px;padding:2px;")
            layout.addWidget(self.status_label)

            # Input area
            input_layout = QtWidgets.QHBoxLayout()

            self.input_field = QtWidgets.QLineEdit()
            self.input_field.setPlaceholderText("Задайте вопрос...")
            self.input_field.setMinimumHeight(36)
            self.input_field.setMaximumHeight(120)
            input_layout.addWidget(self.input_field, stretch=1)

            self.send_btn = QtWidgets.QPushButton("→")
            self.send_btn.setFixedSize(36, 36)
            input_layout.addWidget(self.send_btn)

            layout.addLayout(input_layout)

        def _connect_signals(self):
            self.send_btn.clicked.connect(self._on_send)
            self.input_field.returnPressed.connect(self._on_send)
            self.controller.set_on_message_added(self._on_message)
            self.controller.set_on_message_updated(self._on_message)
            self.controller.set_on_generation_started(
                lambda: self.status_label.setText("⏳ Думаю...")
            )
            self.controller.set_on_generation_finished(
                lambda: self.status_label.setText("")
            )

        def _on_send(self):
            text = self.input_field.text().strip()
            if text:
                self.input_field.clear()
                self.controller.send_user_message(text)

        def _on_message(self, msg):
            self._refresh_chat()

        def _refresh_chat(self):
            html_parts = []
            for msg in self.controller.get_messages():
                if msg.role == MessageRole.USER:
                    safe = MarkdownParser._escape(msg.content)
                    html_parts.append(
                        f'<div style="text-align:right;margin:4px;">'
                        f'<b>Вы:</b> {safe}</div>'
                    )
                elif msg.role == MessageRole.ASSISTANT:
                    rendered = self.controller.render_markdown(msg.content)
                    html_parts.append(
                        f'<div style="margin:4px;">'
                        f'<b>Lina:</b> {rendered}</div>'
                    )
                else:
                    safe = MarkdownParser._escape(msg.content)
                    html_parts.append(
                        f'<div style="color:#fab387;margin:4px;">'
                        f'{safe}</div>'
                    )
            self.chat_area.setHtml("".join(html_parts))
            # Scroll to bottom
            sb = self.chat_area.verticalScrollBar()
            sb.setValue(sb.maximum())

        def keyPressEvent(self, event):
            if event.key() == QtCore.Qt.Key.Key_Escape:
                self.hide()
            else:
                super().keyPressEvent(event)

    return ChatWindow()
