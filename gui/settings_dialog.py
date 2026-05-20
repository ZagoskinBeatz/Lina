"""
Lina GUI — Settings Dialog.

Полноценное окно настроек в glass-morphism стиле.
Секции: Модель, Интерфейс, Pipeline, Голос.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lina.gui.settings_dialog")


def create_settings_dialog(parent=None, settings=None):
    """Create and return a SettingsDialog instance.

    Args:
        parent: Parent QWidget.
        settings: SettingsController instance.

    Returns:
        SettingsDialog widget.
    """
    from lina.gui import get_qt_modules
    QtWidgets, QtCore, QtGui = get_qt_modules()

    if settings is None:
        from lina.gui.settings import get_settings
        settings = get_settings()

    from lina.gui.theme import DARK_THEME as T

    class SettingsDialog(QtWidgets.QDialog):
        """Glass-morphism settings dialog."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Настройки Lina")
            self.setMinimumSize(520, 560)
            self.resize(560, 640)
            self.setModal(True)
            self._build_ui()
            self._load_values()

        # ── Build UI ──

        def _build_ui(self):
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # Title bar
            title_bar = QtWidgets.QWidget()
            title_bar.setFixedHeight(48)
            title_bar.setStyleSheet(f"""
                background: {T.glass_surface};
                border-bottom: 1px solid {T.glass_border};
            """)
            tb_layout = QtWidgets.QHBoxLayout(title_bar)
            tb_layout.setContentsMargins(16, 0, 16, 0)
            title_lbl = QtWidgets.QLabel("⚙ Настройки")
            title_lbl.setStyleSheet(f"""
                font-size: 16px; font-weight: bold;
                color: {T.text}; background: transparent;
            """)
            tb_layout.addWidget(title_lbl)
            tb_layout.addStretch()
            layout.addWidget(title_bar)

            # Tab widget
            self._tabs = QtWidgets.QTabWidget()
            self._tabs.setStyleSheet(self._tab_style())
            self._tabs.addTab(self._create_model_tab(), "🤖 Модель")
            self._tabs.addTab(self._create_gui_tab(), "🎨 Интерфейс")
            self._tabs.addTab(self._create_pipeline_tab(), "⚙ Pipeline")
            self._tabs.addTab(self._create_voice_tab(), "🎤 Голос")
            layout.addWidget(self._tabs, 1)

            # Bottom buttons
            btn_bar = QtWidgets.QWidget()
            btn_bar.setFixedHeight(56)
            btn_bar.setStyleSheet(f"""
                background: {T.glass_surface};
                border-top: 1px solid {T.glass_border};
            """)
            bb_layout = QtWidgets.QHBoxLayout(btn_bar)
            bb_layout.setContentsMargins(16, 8, 16, 8)

            btn_reset = QtWidgets.QPushButton("Сбросить")
            btn_reset.setStyleSheet(self._btn_style(T.error))
            btn_reset.clicked.connect(self._on_reset)

            bb_layout.addWidget(btn_reset)
            bb_layout.addStretch()

            btn_cancel = QtWidgets.QPushButton("Отмена")
            btn_cancel.setStyleSheet(self._btn_style(T.text_secondary))
            btn_cancel.clicked.connect(self.reject)

            btn_save = QtWidgets.QPushButton("💾 Сохранить")
            btn_save.setStyleSheet(self._btn_style(T.primary))
            btn_save.clicked.connect(self._on_save)

            bb_layout.addWidget(btn_cancel)
            bb_layout.addWidget(btn_save)
            layout.addWidget(btn_bar)

            # Dialog style
            self.setStyleSheet(f"""
                QDialog {{
                    background: {T.background};
                    color: {T.text};
                }}
            """)

        # ── Model Tab ──

        def _create_model_tab(self) -> QtWidgets.QWidget:
            tab = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(tab)
            form.setContentsMargins(20, 20, 20, 20)
            form.setSpacing(12)
            self._apply_form_style(form, tab)

            # Model path
            path_row = QtWidgets.QHBoxLayout()
            self._model_path = QtWidgets.QLineEdit()
            self._model_path.setPlaceholderText("models/full/Qwen3.5-4B-Q8_0.gguf")
            btn_browse = QtWidgets.QPushButton("📁")
            btn_browse.setFixedWidth(36)
            btn_browse.clicked.connect(self._browse_model)
            path_row.addWidget(self._model_path, 1)
            path_row.addWidget(btn_browse)
            form.addRow("Путь к модели:", path_row)

            # Context length
            self._n_ctx = QtWidgets.QSpinBox()
            self._n_ctx.setRange(256, 32768)
            self._n_ctx.setSingleStep(256)
            self._n_ctx.setSuffix(" токенов")
            form.addRow("Контекст (n_ctx):", self._n_ctx)

            # Threads
            self._n_threads = QtWidgets.QSpinBox()
            self._n_threads.setRange(1, 64)
            form.addRow("Потоки CPU:", self._n_threads)

            # GPU layers
            self._n_gpu = QtWidgets.QSpinBox()
            self._n_gpu.setRange(0, 999)
            self._n_gpu.setSpecialValueText("CPU only")
            form.addRow("GPU слои:", self._n_gpu)

            # Max RAM
            self._max_ram = QtWidgets.QSpinBox()
            self._max_ram.setRange(512, 65536)
            self._max_ram.setSingleStep(512)
            self._max_ram.setSuffix(" MB")
            form.addRow("Лимит RAM:", self._max_ram)

            # Temperature
            self._temp = QtWidgets.QDoubleSpinBox()
            self._temp.setRange(0.0, 2.0)
            self._temp.setSingleStep(0.1)
            self._temp.setDecimals(2)
            form.addRow("Температура:", self._temp)

            # Max tokens
            self._max_tokens = QtWidgets.QSpinBox()
            self._max_tokens.setRange(1, 8192)
            self._max_tokens.setSingleStep(64)
            self._max_tokens.setSuffix(" токенов")
            form.addRow("Макс. ответ:", self._max_tokens)

            return tab

        # ── GUI Tab ──

        def _create_gui_tab(self) -> QtWidgets.QWidget:
            tab = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(tab)
            form.setContentsMargins(20, 20, 20, 20)
            form.setSpacing(12)
            self._apply_form_style(form, tab)

            # Theme
            self._theme = QtWidgets.QComboBox()
            self._theme.addItems(["dark", "light", "system"])
            form.addRow("Тема:", self._theme)

            # Language
            self._language = QtWidgets.QComboBox()
            self._language.addItems(["ru", "en"])
            form.addRow("Язык:", self._language)

            # Hotkey
            self._hotkey = QtWidgets.QLineEdit()
            self._hotkey.setPlaceholderText("Meta+J")
            form.addRow("Горячая клавиша:", self._hotkey)

            # Font size
            self._font_size = QtWidgets.QSpinBox()
            self._font_size.setRange(8, 32)
            self._font_size.setSuffix(" px")
            form.addRow("Размер шрифта:", self._font_size)

            # Opacity
            self._opacity = QtWidgets.QDoubleSpinBox()
            self._opacity.setRange(0.3, 1.0)
            self._opacity.setSingleStep(0.05)
            self._opacity.setDecimals(2)
            form.addRow("Прозрачность:", self._opacity)

            # Checkboxes
            self._tray_icon = QtWidgets.QCheckBox("Показывать в трее")
            form.addRow("", self._tray_icon)

            self._start_min = QtWidgets.QCheckBox("Запускать свёрнуто")
            form.addRow("", self._start_min)

            self._autostart = QtWidgets.QCheckBox("Автозапуск при входе")
            form.addRow("", self._autostart)

            self._animations = QtWidgets.QCheckBox("Анимации")
            form.addRow("", self._animations)

            return tab

        # ── Pipeline Tab ──

        def _create_pipeline_tab(self) -> QtWidgets.QWidget:
            tab = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(tab)
            form.setContentsMargins(20, 20, 20, 20)
            form.setSpacing(12)
            self._apply_form_style(form, tab)

            self._safe_mode = QtWidgets.QCheckBox("Безопасный режим (только чтение)")
            form.addRow("", self._safe_mode)

            self._enable_rag = QtWidgets.QCheckBox("Поиск по базе знаний (RAG)")
            form.addRow("", self._enable_rag)

            self._enable_tools = QtWidgets.QCheckBox("Инструменты (выполнение команд)")
            form.addRow("", self._enable_tools)

            self._enable_cv = QtWidgets.QCheckBox("Компьютерное зрение (CV)")
            form.addRow("", self._enable_cv)

            self._enable_streaming = QtWidgets.QCheckBox("Стриминг ответов")
            form.addRow("", self._enable_streaming)

            self._enable_notif = QtWidgets.QCheckBox("Системные уведомления")
            form.addRow("", self._enable_notif)

            return tab

        # ── Voice Tab ──

        def _create_voice_tab(self) -> QtWidgets.QWidget:
            tab = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(tab)
            form.setContentsMargins(20, 20, 20, 20)
            form.setSpacing(12)
            self._apply_form_style(form, tab)

            self._stt_enabled = QtWidgets.QCheckBox("Голосовой ввод (STT)")
            form.addRow("", self._stt_enabled)

            self._stt_model = QtWidgets.QComboBox()
            self._stt_model.addItems([
                "whisper-tiny", "whisper-base", "whisper-small", "whisper-medium",
            ])
            form.addRow("STT модель:", self._stt_model)

            self._tts_enabled = QtWidgets.QCheckBox("Озвучка ответов (TTS)")
            form.addRow("", self._tts_enabled)

            self._tts_engine = QtWidgets.QComboBox()
            self._tts_engine.addItems(["piper", "espeak-ng", "edge-tts"])
            form.addRow("TTS движок:", self._tts_engine)

            self._tts_speed = QtWidgets.QDoubleSpinBox()
            self._tts_speed.setRange(0.5, 2.0)
            self._tts_speed.setSingleStep(0.1)
            self._tts_speed.setDecimals(1)
            self._tts_speed.setSuffix("x")
            form.addRow("Скорость речи:", self._tts_speed)

            self._tts_volume = QtWidgets.QDoubleSpinBox()
            self._tts_volume.setRange(0.0, 1.0)
            self._tts_volume.setSingleStep(0.1)
            self._tts_volume.setDecimals(1)
            self._tts_volume.setValue(1.0)
            form.addRow("Громкость TTS:", self._tts_volume)

            self._voice_lang = QtWidgets.QComboBox()
            self._voice_lang.addItems(["ru", "en", "de", "fr", "es"])
            form.addRow("Язык голоса:", self._voice_lang)

            self._ptt_key = QtWidgets.QLineEdit()
            self._ptt_key.setPlaceholderText("Ctrl+Space")
            form.addRow("Push-to-Talk:", self._ptt_key)

            self._vad = QtWidgets.QCheckBox("Автодетекция голоса (VAD)")
            form.addRow("", self._vad)

            return tab

        # ── Load / Save ──

        def _load_values(self):
            """Load values from settings controller into widgets."""
            s = settings

            # Model
            self._model_path.setText(s.model.model_path)
            self._n_ctx.setValue(s.model.n_ctx)
            self._n_threads.setValue(s.model.n_threads)
            self._n_gpu.setValue(s.model.n_gpu_layers)
            self._max_ram.setValue(s.model.max_ram_mb)
            self._temp.setValue(s.model.temperature)
            self._max_tokens.setValue(s.model.max_tokens)

            # GUI
            idx = self._theme.findText(s.gui.theme)
            if idx >= 0:
                self._theme.setCurrentIndex(idx)
            idx = self._language.findText(s.gui.language)
            if idx >= 0:
                self._language.setCurrentIndex(idx)
            self._hotkey.setText(s.gui.hotkey)
            self._font_size.setValue(s.gui.font_size)
            self._opacity.setValue(s.gui.opacity)
            self._tray_icon.setChecked(s.gui.show_tray_icon)
            self._start_min.setChecked(s.gui.start_minimized)
            self._autostart.setChecked(s.gui.autostart)
            self._animations.setChecked(s.gui.enable_animations)

            # Pipeline
            self._safe_mode.setChecked(s.pipeline.safe_mode)
            self._enable_rag.setChecked(s.pipeline.enable_rag)
            self._enable_tools.setChecked(s.pipeline.enable_tools)
            self._enable_cv.setChecked(s.pipeline.enable_cv)
            self._enable_streaming.setChecked(s.pipeline.enable_streaming)
            self._enable_notif.setChecked(s.pipeline.enable_notifications)

            # Voice
            self._stt_enabled.setChecked(s.voice.stt_enabled)
            idx = self._stt_model.findText(s.voice.stt_model)
            if idx >= 0:
                self._stt_model.setCurrentIndex(idx)
            self._tts_enabled.setChecked(s.voice.tts_enabled)
            idx = self._tts_engine.findText(s.voice.tts_engine)
            if idx >= 0:
                self._tts_engine.setCurrentIndex(idx)
            self._tts_speed.setValue(s.voice.tts_speed)
            self._tts_volume.setValue(s.voice.tts_volume)
            idx = self._voice_lang.findText(s.voice.voice_language)
            if idx >= 0:
                self._voice_lang.setCurrentIndex(idx)
            self._ptt_key.setText(s.voice.push_to_talk_key)
            self._vad.setChecked(s.voice.vad_enabled)

        def _save_values(self):
            """Write widget values back to settings controller."""
            s = settings

            s.set("model", "model_path", self._model_path.text())
            s.set("model", "n_ctx", self._n_ctx.value())
            s.set("model", "n_threads", self._n_threads.value())
            s.set("model", "n_gpu_layers", self._n_gpu.value())
            s.set("model", "max_ram_mb", self._max_ram.value())
            s.set("model", "temperature", self._temp.value())
            s.set("model", "max_tokens", self._max_tokens.value())

            s.set("gui", "theme", self._theme.currentText())
            s.set("gui", "language", self._language.currentText())
            s.set("gui", "hotkey", self._hotkey.text())
            s.set("gui", "font_size", self._font_size.value())
            s.set("gui", "opacity", self._opacity.value())
            s.set("gui", "show_tray_icon", self._tray_icon.isChecked())
            s.set("gui", "start_minimized", self._start_min.isChecked())
            s.set("gui", "autostart", self._autostart.isChecked())
            s.set("gui", "enable_animations", self._animations.isChecked())

            s.set("pipeline", "safe_mode", self._safe_mode.isChecked())
            s.set("pipeline", "enable_rag", self._enable_rag.isChecked())
            s.set("pipeline", "enable_tools", self._enable_tools.isChecked())
            s.set("pipeline", "enable_cv", self._enable_cv.isChecked())
            s.set("pipeline", "enable_streaming", self._enable_streaming.isChecked())
            s.set("pipeline", "enable_notifications", self._enable_notif.isChecked())

            s.set("voice", "stt_enabled", self._stt_enabled.isChecked())
            s.set("voice", "stt_model", self._stt_model.currentText())
            s.set("voice", "tts_enabled", self._tts_enabled.isChecked())
            s.set("voice", "tts_engine", self._tts_engine.currentText())
            s.set("voice", "tts_speed", self._tts_speed.value())
            s.set("voice", "tts_volume", self._tts_volume.value())
            s.set("voice", "voice_language", self._voice_lang.currentText())
            s.set("voice", "push_to_talk_key", self._ptt_key.text())
            s.set("voice", "vad_enabled", self._vad.isChecked())

            # Handle autostart toggle
            if self._autostart.isChecked():
                try:
                    from lina.installer.desktop import install_autostart
                    install_autostart(enabled=True)
                except Exception:
                    pass
            else:
                try:
                    from lina.installer.desktop import uninstall_autostart
                    uninstall_autostart()
                except Exception:
                    pass

        def _on_save(self):
            self._save_values()
            errors = settings.validate()
            if errors:
                QtWidgets.QMessageBox.warning(
                    self, "Ошибки", "\n".join(errors)
                )
                return
            settings.save()
            self.accept()

        def _on_reset(self):
            reply = QtWidgets.QMessageBox.question(
                self, "Сброс",
                "Сбросить все настройки к значениям по умолчанию?",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
            )
            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                settings.reset_to_defaults()
                self._load_values()

        def _browse_model(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Выберите GGUF модель",
                str(Path.home()),
                "GGUF Models (*.gguf);;All Files (*)",
            )
            if path:
                self._model_path.setText(path)

        # ── Styles ──

        def _apply_form_style(self, form, tab):
            tab.setStyleSheet(f"""
                QWidget {{
                    background: {T.background};
                    color: {T.text};
                }}
                QLabel {{
                    color: {T.text_secondary};
                    font-size: 13px;
                    background: transparent;
                }}
                QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
                    background: {T.glass_input};
                    color: {T.text};
                    border: 1px solid {T.glass_border_light};
                    border-radius: 6px;
                    padding: 6px 10px;
                    font-size: 13px;
                    min-height: 28px;
                }}
                QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
                    border-color: {T.primary};
                }}
                QCheckBox {{
                    color: {T.text};
                    font-size: 13px;
                    spacing: 8px;
                    background: transparent;
                }}
                QCheckBox::indicator {{
                    width: 18px; height: 18px;
                    border-radius: 4px;
                    border: 1px solid {T.glass_border_light};
                    background: {T.glass_input};
                }}
                QCheckBox::indicator:checked {{
                    background: {T.primary};
                    border-color: {T.primary};
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 24px;
                }}
                QComboBox QAbstractItemView {{
                    background: {T.surface};
                    color: {T.text};
                    border: 1px solid {T.border};
                    selection-background-color: {T.primary};
                }}
            """)

        def _tab_style(self) -> str:
            return f"""
                QTabWidget::pane {{
                    border: none;
                    background: {T.background};
                }}
                QTabBar::tab {{
                    background: {T.glass_surface};
                    color: {T.text_secondary};
                    padding: 8px 16px;
                    margin-right: 2px;
                    border-top-left-radius: 6px;
                    border-top-right-radius: 6px;
                    font-size: 13px;
                }}
                QTabBar::tab:selected {{
                    background: {T.background};
                    color: {T.text};
                    border-bottom: 2px solid {T.primary};
                }}
                QTabBar::tab:hover {{
                    background: {T.glass_surface_hover};
                    color: {T.text};
                }}
            """

        @staticmethod
        def _btn_style(color: str) -> str:
            return f"""
                QPushButton {{
                    background: transparent;
                    color: {color};
                    border: 1px solid {color};
                    border-radius: 6px;
                    padding: 8px 20px;
                    font-size: 13px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background: {color};
                    color: #ffffff;
                }}
            """

    return SettingsDialog(parent)
