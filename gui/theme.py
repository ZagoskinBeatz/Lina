"""
Lina GUI — Темы и стили.

Дизайн: космический glass-morphism с индиго-фиолетовым градиентом.
Тёмная, светлая тема. CSS-стили для всех виджетов.

Glass-morphism достигается через:
  - WA_TranslucentBackground на QMainWindow
  - RGBA фоны с альфа-каналом (полупрозрачные панели)
  - KWin blur hint (KDE Plasma) для размытия фона за окном
  - Градиенты и свечение на границах
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class ThemeColors:
    """Набор цветов для темы."""
    # Main backgrounds — deep indigo / cosmic
    background: str = "#0c0e1a"
    surface: str = "#161938"
    surface_hover: str = "#1e2250"
    surface_active: str = "#272b64"

    # ── Glass-morphism (RGBA with alpha) ──
    glass_bg: str = "rgba(14, 16, 34, 0.72)"
    glass_surface: str = "rgba(22, 25, 56, 0.60)"
    glass_surface_hover: str = "rgba(30, 34, 80, 0.68)"
    glass_surface_active: str = "rgba(39, 43, 100, 0.75)"
    glass_border: str = "rgba(255, 255, 255, 0.06)"
    glass_border_light: str = "rgba(255, 255, 255, 0.12)"
    glass_highlight: str = "rgba(110, 140, 255, 0.20)"
    glass_input: str = "rgba(18, 20, 42, 0.80)"
    glass_bubble_user: str = "rgba(40, 55, 120, 0.55)"
    glass_bubble_bot: str = "rgba(22, 25, 56, 0.50)"
    blur_radius: int = 28

    # Window / cosmic gradient
    window_gradient_start: str = "#0a0c1e"
    window_gradient_end: str = "#1a1040"
    window_gradient_mid: str = "#12163a"

    # Accent — bright blue / lavender
    primary: str = "#6c8cff"
    primary_hover: str = "#8aa4ff"
    secondary: str = "#a78bfa"

    # Text
    text: str = "#f0f0f8"
    text_secondary: str = "#b0b8d0"
    text_hint: str = "#7882a4"
    message_meta: str = "#a0aad0"

    # Borders
    border: str = "rgba(255, 255, 255, 0.10)"
    border_accent: str = "rgba(108, 140, 255, 0.35)"

    # Semantic
    error: str = "#ff6b7a"
    warning: str = "#f5c84c"
    success: str = "#42d98c"

    # Chat bubbles — frosted glass
    user_bubble: str = "rgba(50, 60, 130, 0.50)"
    user_bubble_border: str = "rgba(108, 140, 255, 0.30)"
    bot_bubble: str = "rgba(255, 255, 255, 0.08)"
    bot_bubble_border: str = "rgba(255, 255, 255, 0.12)"

    # Input
    input_bg: str = "rgba(18, 20, 42, 0.80)"
    input_border: str = "rgba(255, 255, 255, 0.14)"
    input_focus: str = "#6c8cff"

    # Scrollbar
    scrollbar: str = "rgba(255, 255, 255, 0.10)"
    scrollbar_hover: str = "rgba(255, 255, 255, 0.18)"

    # Avatar
    avatar_bot: str = "#6c8cff"
    avatar_user: str = "#a78bfa"

    # Code blocks
    code_bg: str = "rgba(10, 12, 28, 0.85)"
    code_border: str = "rgba(255, 255, 255, 0.08)"
    code_fg: str = "#e8ecff"
    inline_code_bg: str = "rgba(30, 34, 80, 0.50)"
    inline_code_fg: str = "#b8c8ff"

    # Mode tabs (наследие; больше не используются после удаления PURE/PIPE)
    tab_active: str = "#6c8cff"
    tab_inactive: str = "rgba(255, 255, 255, 0.08)"


DARK_THEME = ThemeColors()

LIGHT_THEME = ThemeColors(
    background="#eef1f8",
    surface="#ffffff",
    surface_hover="#f0f3ff",
    surface_active="#e2e8ff",
    glass_bg="rgba(238, 241, 248, 0.75)",
    glass_surface="rgba(255, 255, 255, 0.65)",
    glass_surface_hover="rgba(240, 243, 255, 0.72)",
    glass_surface_active="rgba(226, 232, 255, 0.78)",
    glass_border="rgba(80, 60, 160, 0.08)",
    glass_border_light="rgba(80, 60, 160, 0.12)",
    glass_highlight="rgba(108, 140, 255, 0.14)",
    glass_input="rgba(255, 255, 255, 0.80)",
    glass_bubble_user="rgba(224, 232, 255, 0.70)",
    glass_bubble_bot="rgba(248, 249, 255, 0.55)",
    blur_radius=22,
    window_gradient_start="#e8ecf8",
    window_gradient_end="#d8d0f0",
    window_gradient_mid="#e0e4f4",
    primary="#5a7cff",
    primary_hover="#4a6cf0",
    secondary="#8b6cf0",
    text="#181830",
    text_secondary="#5060a0",
    text_hint="#8090b8",
    message_meta="#7080b0",
    border="rgba(80, 60, 160, 0.12)",
    border_accent="rgba(90, 124, 255, 0.30)",
    error="#d42040",
    warning="#b07a10",
    success="#1a7f3c",
    user_bubble="rgba(210, 222, 255, 0.60)",
    user_bubble_border="rgba(90, 124, 255, 0.30)",
    bot_bubble="rgba(255, 255, 255, 0.50)",
    bot_bubble_border="rgba(80, 60, 160, 0.12)",
    input_bg="rgba(255, 255, 255, 0.80)",
    input_border="rgba(80, 60, 160, 0.16)",
    input_focus="#5a7cff",
    scrollbar="rgba(80, 60, 160, 0.12)",
    scrollbar_hover="rgba(80, 60, 160, 0.22)",
    avatar_bot="#5a7cff",
    avatar_user="#8b6cf0",
    code_bg="rgba(20, 22, 48, 0.92)",
    code_border="rgba(255, 255, 255, 0.10)",
    code_fg="#e8ecff",
    inline_code_bg="rgba(230, 234, 255, 0.50)",
    inline_code_fg="#3a4cb0",
    tab_active="#5a7cff",
    tab_inactive="rgba(0, 0, 0, 0.06)",
)


def get_theme(name: str = "dark") -> ThemeColors:
    """Возвращает тему по имени: 'dark', 'light', 'system'."""
    if name == "light":
        return LIGHT_THEME
    return DARK_THEME


def build_stylesheet(theme: ThemeColors) -> str:
    """Генерирует CSS-stylesheet для Qt из параметров темы."""
    return f"""
    /* === Lina Cosmic Glass Theme === */

    * {{
        font-family: "Inter", "IBM Plex Sans", "Noto Sans", "Segoe UI", sans-serif;
    }}

    QMainWindow {{
        background-color: transparent;
        color: {theme.text};
    }}

    QWidget {{
        background-color: transparent;
        color: {theme.text};
        font-size: 14px;
    }}

    QWidget#centralWidget,
    QWidget#rightPanel {{
        background-color: transparent;
    }}

    QScrollArea,
    QScrollArea > QWidget > QWidget {{
        background: transparent;
        border: none;
    }}

    /* ── Sidebar (frosted panel) ── */

    QWidget#sidebar {{
        background-color: {theme.glass_surface};
        border: 1px solid {theme.glass_border_light};
        border-radius: 24px;
    }}

    QLineEdit#searchBox {{
        background-color: {theme.glass_input};
        color: {theme.text};
        border: 1px solid {theme.glass_border_light};
        border-radius: 14px;
        padding: 10px 14px 10px 36px;
        font-size: 13px;
    }}

    QLineEdit#searchBox:focus {{
        border-color: {theme.primary};
    }}

    QPushButton#chatItem,
    QPushButton#chatItemActive {{
        border-radius: 16px;
        padding: 11px 14px;
        text-align: left;
        font-size: 13px;
        font-weight: 500;
    }}

    QPushButton#chatItem {{
        background-color: transparent;
        color: {theme.text_secondary};
        border: 1px solid transparent;
    }}

    QPushButton#chatItem:hover {{
        background-color: {theme.glass_surface_hover};
        color: {theme.text};
        border-color: {theme.glass_border_light};
    }}

    QPushButton#chatItemActive {{
        background-color: {theme.glass_surface_active};
        color: {theme.text};
        border: 1px solid {theme.border_accent};
    }}

    QPushButton#newChatBtn {{
        background-color: {theme.glass_surface_hover};
        color: {theme.text};
        border: 1px solid {theme.glass_border_light};
        border-radius: 16px;
        padding: 12px 14px;
        text-align: left;
        font-size: 13px;
        font-weight: 600;
    }}

    QPushButton#newChatBtn:hover {{
        background-color: {theme.glass_highlight};
        border-color: {theme.border_accent};
        color: {theme.primary};
    }}

    QLabel#sectionLabel {{
        color: {theme.text_secondary};
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.06em;
        padding: 8px 10px 4px 10px;
    }}

    /* ── Frosted panels (title, input, status, command, confirm) ── */

    QWidget#titleBar {{
        background-color: {theme.glass_surface};
        border: 1px solid {theme.glass_border_light};
        border-radius: 22px;
    }}

    QWidget#inputBar {{
        background-color: {theme.glass_surface};
        border: 1px solid {theme.glass_border_light};
        border-radius: 22px;
    }}

    QWidget#confirmBar,
    QWidget#commandBar {{
        background-color: {theme.glass_surface};
        border: 1px solid {theme.glass_border_light};
        border-radius: 18px;
    }}

    QWidget#statusBar {{
        background-color: {theme.glass_surface};
        border: 1px solid {theme.glass_border_light};
        border-radius: 16px;
    }}

    /* ── Chat shell (main content area, frosted) ── */

    QWidget#chatShell {{
        background-color: {theme.glass_bg};
        border: 1px solid {theme.glass_border_light};
        border-radius: 24px;
    }}

    /* ── Title bar elements ── */

    QLabel#titleAvatar {{
        background-color: {theme.primary};
        color: white;
        border-radius: 22px;
        font-size: 18px;
        font-weight: 700;
    }}

    QLabel#titleLabel {{
        color: {theme.text};
        font-size: 16px;
        font-weight: 700;
    }}

    QLabel#statusDot {{
        color: {theme.success};
        font-size: 11px;
    }}

    QPushButton#titleBtn,
    QPushButton#micButton {{
        background-color: {theme.glass_surface_hover};
        color: {theme.text_secondary};
        border: 1px solid {theme.glass_border_light};
        border-radius: 14px;
        padding: 0 12px;
        font-size: 13px;
        font-weight: 600;
    }}

    QPushButton#titleBtn:hover,
    QPushButton#micButton:hover {{
        background-color: {theme.glass_highlight};
        color: {theme.text};
        border-color: {theme.border_accent};
    }}

    QPushButton#titleBtn:checked {{
        background-color: {theme.primary};
        color: white;
        border-color: {theme.primary};
    }}

    /* ── Mode tab buttons ── */

    QPushButton#modeTab {{
        background-color: {theme.tab_inactive};
        color: {theme.text_secondary};
        border: 1px solid {theme.glass_border_light};
        border-radius: 16px;
        padding: 8px 18px;
        font-size: 12px;
        font-weight: 600;
    }}

    QPushButton#modeTab:hover {{
        background-color: {theme.glass_surface_hover};
        color: {theme.text};
    }}

    QPushButton#modeTabActive {{
        background-color: {theme.tab_active};
        color: white;
        border: 1px solid {theme.tab_active};
        border-radius: 16px;
        padding: 8px 18px;
        font-size: 12px;
        font-weight: 700;
    }}

    /* ── Chat view (transparent inside shell) ── */

    QTextBrowser#chatView {{
        background-color: transparent;
        color: {theme.text};
        border: none;
        padding: 0;
        selection-background-color: {theme.primary};
    }}

    /* ── Input field ── */

    QTextEdit#inputField {{
        background-color: {theme.glass_input};
        color: {theme.text};
        border: 1px solid {theme.glass_border_light};
        border-radius: 18px;
        padding: 12px 16px;
        font-size: 14px;
        selection-background-color: {theme.primary};
    }}

    QTextEdit#inputField:focus {{
        border-color: {theme.primary};
    }}

    QPushButton#sendButton {{
        background-color: {theme.primary};
        color: white;
        border: none;
        border-radius: 16px;
        font-size: 16px;
        font-weight: 700;
    }}

    QPushButton#sendButton:hover {{
        background-color: {theme.primary_hover};
    }}

    QPushButton#inputActionBtn {{
        background-color: {theme.glass_surface_hover};
        color: {theme.text_secondary};
        border: 1px solid {theme.glass_border_light};
        border-radius: 16px;
        font-size: 18px;
    }}

    QPushButton#inputActionBtn:hover {{
        color: {theme.text};
        background-color: {theme.glass_highlight};
        border-color: {theme.border_accent};
    }}

    /* ── Confirm / Cancel ── */

    QPushButton#confirmButton,
    QPushButton#cancelButton,
    QMessageBox QPushButton {{
        color: white;
        border: none;
        border-radius: 12px;
        padding: 8px 18px;
        min-width: 70px;
        font-weight: 700;
    }}

    QPushButton#confirmButton {{
        background-color: {theme.success};
    }}

    QPushButton#confirmButton:hover {{
        background-color: #36bd7a;
    }}

    QPushButton#cancelButton {{
        background-color: {theme.error};
    }}

    QPushButton#cancelButton:hover {{
        background-color: #e0506a;
    }}

    /* ── Status bar ── */

    QWidget#statusBar QLabel {{
        color: {theme.text_secondary};
    }}

    /* ── Scrollbar (thin, cosmic) ── */

    QScrollBar:vertical {{
        background: transparent;
        width: 6px;
        margin: 6px 2px 6px 0;
    }}

    QScrollBar::handle:vertical {{
        background: {theme.scrollbar};
        min-height: 36px;
        border-radius: 3px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: {theme.scrollbar_hover};
    }}

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QScrollBar:horizontal {{
        height: 0px;
    }}

    /* ── Menus ── */

    QMenu {{
        background-color: {theme.glass_surface};
        color: {theme.text};
        border: 1px solid {theme.glass_border_light};
        border-radius: 16px;
        padding: 6px;
    }}

    QMenu::item {{
        padding: 9px 22px;
        border-radius: 10px;
    }}

    QMenu::item:selected {{
        background-color: {theme.glass_surface_hover};
    }}

    QMessageBox {{
        background-color: {theme.glass_surface};
        color: {theme.text};
        border: 1px solid {theme.glass_border_light};
        border-radius: 16px;
    }}

    QToolTip {{
        background-color: {theme.glass_surface_hover};
        color: {theme.text};
        border: 1px solid {theme.glass_border_light};
        border-radius: 10px;
        padding: 6px 10px;
    }}

    QLabel {{
        color: {theme.text};
    }}

    QLineEdit,
    QPlainTextEdit {{
        background-color: {theme.glass_input};
        color: {theme.text};
        border: 1px solid {theme.glass_border_light};
        border-radius: 12px;
        padding: 8px 10px;
        selection-background-color: {theme.primary};
    }}

    QComboBox {{
        background-color: {theme.glass_input};
        color: {theme.text};
        border: 1px solid {theme.glass_border_light};
        border-radius: 12px;
        padding: 8px 14px;
    }}

    QComboBox::drop-down {{
        border: none;
    }}

    QCheckBox {{
        color: {theme.text};
        spacing: 8px;
    }}

    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 5px;
        border: 2px solid {theme.glass_border_light};
    }}

    QCheckBox::indicator:checked {{
        background-color: {theme.primary};
        border-color: {theme.primary};
    }}

    QProgressBar {{
        border: 1px solid {theme.glass_border_light};
        border-radius: 10px;
        text-align: center;
        color: {theme.text};
    }}

    QProgressBar::chunk {{
        background-color: {theme.primary};
        border-radius: 9px;
    }}

    /* ── Command bar ── */

    #commandLabel {{
        color: {theme.text};
        font-family: "JetBrains Mono", "Fira Code", monospace;
    }}

    #execButton,
    #terminalSendBtn {{
        background-color: {theme.primary};
        color: white;
        border: none;
        border-radius: 12px;
        padding: 6px 14px;
        font-weight: 700;
    }}

    #execButton:hover,
    #terminalSendBtn:hover {{
        background-color: {theme.primary_hover};
    }}

    #commandCloseBtn,
    #terminalCloseBtn {{
        background: transparent;
        color: {theme.text_secondary};
        border: none;
        border-radius: 8px;
        font-size: 14px;
    }}

    #commandCloseBtn:hover,
    #terminalCloseBtn:hover {{
        background-color: {theme.glass_surface_hover};
        color: {theme.error};
    }}

    /* ── Embedded terminal ── */

    #embeddedTerminal {{
        background-color: {theme.code_bg};
        border: 1px solid {theme.glass_border_light};
        border-radius: 20px;
    }}

    #terminalHeader {{
        background-color: {theme.glass_surface_hover};
        border-bottom: 1px solid {theme.glass_border_light};
        border-top-left-radius: 20px;
        border-top-right-radius: 20px;
    }}

    #terminalHeaderLabel {{
        color: {theme.primary};
        font-family: "JetBrains Mono", "Fira Code", monospace;
        font-size: 10pt;
    }}

    #terminalStopBtn {{
        background-color: {theme.error};
        color: white;
        border: none;
        border-radius: 10px;
        padding: 4px 12px;
        font-size: 11px;
        font-weight: 700;
    }}

    #terminalStopBtn:hover {{
        background-color: #e0506a;
    }}

    #terminalStopBtn:disabled {{
        background-color: {theme.glass_surface};
        color: {theme.text_hint};
    }}

    #terminalOutput {{
        background-color: {theme.code_bg};
        color: {theme.code_fg};
        border: none;
        border-radius: 0px;
        padding: 10px 14px;
        font-family: "JetBrains Mono", "Fira Code", monospace;
        font-size: 11pt;
        selection-background-color: rgba(108, 140, 255, 0.25);
    }}

    #terminalInputBar {{
        background-color: {theme.glass_surface_hover};
        border-top: 1px solid {theme.glass_border_light};
        border-bottom-left-radius: 20px;
        border-bottom-right-radius: 20px;
    }}

    #terminalInput {{
        background-color: {theme.glass_input};
        color: {theme.code_fg};
        border: 1px solid {theme.glass_border_light};
        border-radius: 12px;
        padding: 7px 10px;
        font-family: "JetBrains Mono", "Fira Code", monospace;
    }}

    #terminalInput:focus {{
        border-color: {theme.primary};
    }}

    #commandSelector {{
        background-color: {theme.glass_input};
        color: {theme.text};
        border: 1px solid {theme.glass_border_light};
        border-radius: 10px;
        padding: 4px 8px;
        font-family: "JetBrains Mono", "Fira Code", monospace;
        font-size: 10pt;
    }}
    """


@dataclass
class GUIConfig:
    """Конфигурация GUI."""
    theme_name: str = "dark"
    window_width: int = 960
    window_height: int = 640
    hotkey: str = "Meta+J"
    font_size: int = 13
    show_tray_icon: bool = True
    start_minimized: bool = True
    autostart: bool = False
    language: str = "ru"
    enable_notifications: bool = True
    enable_streaming: bool = True
    opacity: float = 0.97
    enable_animations: bool = True
    sidebar_width: int = 260

    def get_theme(self) -> ThemeColors:
        """Возвращает объект темы."""
        return get_theme(self.theme_name)

    def get_stylesheet(self) -> str:
        """Возвращает CSS-stylesheet для текущей темы."""
        return build_stylesheet(self.get_theme())

    def to_dict(self) -> Dict:
        """Словарь всех настроек."""
        return {
            "theme_name": self.theme_name,
            "window_width": self.window_width,
            "window_height": self.window_height,
            "hotkey": self.hotkey,
            "font_size": self.font_size,
            "show_tray_icon": self.show_tray_icon,
            "start_minimized": self.start_minimized,
            "autostart": self.autostart,
            "language": self.language,
            "enable_notifications": self.enable_notifications,
            "enable_streaming": self.enable_streaming,
            "opacity": self.opacity,
            "enable_animations": self.enable_animations,
            "sidebar_width": self.sidebar_width,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "GUIConfig":
        """Создание конфига из словаря (безопасный)."""
        valid_keys = {
            "theme_name", "window_width", "window_height", "hotkey",
            "font_size", "show_tray_icon", "start_minimized", "autostart",
            "language", "enable_notifications", "enable_streaming",
            "opacity", "enable_animations", "sidebar_width",
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)
