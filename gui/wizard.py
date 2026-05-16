# -*- coding: utf-8 -*-
"""
Lina GUI — Мастер первого запуска (QWizard).

Графическая обёртка над installer.first_run.FirstRunWizard.
Проводит пользователя через 8 шагов первоначальной настройки.
"""

import logging
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger("lina.gui.wizard")

# ── Qt-импорт ────────────────────────────────────────────────────────────────
try:
    from PyQt6.QtWidgets import (
        QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
        QLabel, QRadioButton, QButtonGroup, QProgressBar,
        QCheckBox, QComboBox, QGroupBox, QTextBrowser,
        QSizePolicy, QSpacerItem, QPushButton,
    )
    from PyQt6.QtCore import Qt, QThread, pyqtSignal
    from PyQt6.QtGui import QFont, QPixmap
except ImportError:
    try:
        from PySide6.QtWidgets import (
            QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
            QLabel, QRadioButton, QButtonGroup, QProgressBar,
            QCheckBox, QComboBox, QGroupBox, QTextBrowser,
            QSizePolicy, QSpacerItem, QPushButton,
        )
        from PySide6.QtCore import Qt, QThread, Signal as pyqtSignal
        from PySide6.QtGui import QFont, QPixmap
    except ImportError:
        QWizard = None  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
#  Вспомогательный поток
# ═══════════════════════════════════════════════════════════════════════════════

if QWizard is not None:

    class _IndexWorker(QThread):
        """Фоновый поток для индексации знаний."""
        progress = pyqtSignal(str, float)
        finished_ok = pyqtSignal(dict)

        def __init__(self, wizard_backend):
            super().__init__()
            self._backend = wizard_backend

        def run(self):
            self._backend.set_on_progress(
                lambda msg, pct: self.progress.emit(msg, pct))
            result = self._backend.index_knowledge()
            self.finished_ok.emit(result)


# ═══════════════════════════════════════════════════════════════════════════════
#  Страницы мастера
# ═══════════════════════════════════════════════════════════════════════════════

    class WelcomePage(QWizardPage):
        """Шаг 1 — Приветствие."""

        def __init__(self, backend, parent=None):
            super().__init__(parent)
            self._backend = backend
            self.setTitle("Добро пожаловать")
            self.setSubTitle("Мастер первого запуска Lina")

            layout = QVBoxLayout(self)
            text = QLabel(backend.get_welcome_text())
            text.setWordWrap(True)
            text.setStyleSheet("font-size: 14px; padding: 12px;")
            layout.addWidget(text)
            layout.addStretch()

    class ModelPage(QWizardPage):
        """Шаг 2 — Выбор модели."""

        def __init__(self, backend, parent=None):
            super().__init__(parent)
            self._backend = backend
            self.setTitle("Выбор модели")
            self.setSubTitle("Рекомендуем модель на основе вашей RAM")

            layout = QVBoxLayout(self)
            self._group = QButtonGroup(self)
            self._buttons: Dict[str, QRadioButton] = {}

            for model in backend.get_available_models():
                label = (f"{model.name} ({model.params}) — "
                         f"RAM: {model.ram_required_gb} GB, "
                         f"Диск: {model.disk_required_gb} GB")
                rb = QRadioButton(label)
                rb.setProperty("model_size", model.size.value)
                self._group.addButton(rb)
                self._buttons[model.size.value] = rb
                layout.addWidget(rb)

                desc = QLabel(f"  {model.description}")
                desc.setStyleSheet("color: gray; margin-left: 24px;")
                layout.addWidget(desc)

            # Предвыбрать рекомендованную
            rec = backend.get_recommended_model()
            if rec.size.value in self._buttons:
                self._buttons[rec.size.value].setChecked(True)

            ram_label = QLabel(
                f"Обнаружено RAM: {backend.state.total_ram_gb:.1f} GB")
            ram_label.setStyleSheet("margin-top: 12px; font-weight: bold;")
            layout.addWidget(ram_label)
            layout.addStretch()

        def validatePage(self):
            btn = self._group.checkedButton()
            if btn:
                size_val = btn.property("model_size")
                self._backend.select_model(size_val)
                return True
            return False

    class DownloadPage(QWizardPage):
        """Шаг 3 — Скачивание модели (симуляция / проверка)."""

        def __init__(self, backend, parent=None):
            super().__init__(parent)
            self._backend = backend
            self.setTitle("Скачивание модели")
            self.setSubTitle("Проверяем наличие модели...")

            layout = QVBoxLayout(self)
            self._status = QLabel("Проверка...")
            self._status.setWordWrap(True)
            layout.addWidget(self._status)

            self._progress = QProgressBar()
            self._progress.setRange(0, 100)
            layout.addWidget(self._progress)
            layout.addStretch()

        def initializePage(self):
            info = self._backend.get_download_info()
            if info.get("already_exists"):
                self._status.setText(
                    f"✅ Модель {info.get('model', '')} уже загружена.")
                self._progress.setValue(100)
                self._backend.simulate_download()
            elif info.get("error"):
                self._status.setText(f"⚠ {info['error']}")
            else:
                self._status.setText(
                    f"Модель: {info.get('model', '')}\n"
                    f"Размер: {info.get('size_gb', 0):.1f} GB\n\n"
                    f"Скачайте вручную или используйте:\n"
                    f"  lina --download-model")
                self._progress.setValue(0)
                self._backend.simulate_download()
                self._progress.setValue(100)

    class IndexPage(QWizardPage):
        """Шаг 4 — Индексация базы знаний."""

        def __init__(self, backend, parent=None):
            super().__init__(parent)
            self._backend = backend
            self._complete = False
            self.setTitle("Индексация знаний")
            self.setSubTitle("Подготовка базы знаний...")

            layout = QVBoxLayout(self)
            self._status = QLabel("Ожидание...")
            layout.addWidget(self._status)

            self._progress = QProgressBar()
            self._progress.setRange(0, 100)
            layout.addWidget(self._progress)
            layout.addStretch()

        def initializePage(self):
            self._worker = _IndexWorker(self._backend)
            self._worker.progress.connect(self._on_progress)
            self._worker.finished_ok.connect(self._on_done)
            self._worker.start()

        def _on_progress(self, msg: str, pct: float):
            self._status.setText(msg)
            self._progress.setValue(int(pct * 100))

        def _on_done(self, result: dict):
            n = result.get("files_indexed", 0)
            self._status.setText(
                f"✅ Проиндексировано файлов: {n}")
            self._progress.setValue(100)
            self._complete = True
            self.completeChanged.emit()

        def isComplete(self):
            return self._complete

    class SystemPage(QWizardPage):
        """Шаг 5 — Определение системы."""

        def __init__(self, backend, parent=None):
            super().__init__(parent)
            self._backend = backend
            self.setTitle("Ваша система")
            self.setSubTitle("Определяем конфигурацию")

            layout = QVBoxLayout(self)
            self._info = QTextBrowser()
            self._info.setOpenExternalLinks(False)
            layout.addWidget(self._info)

        def initializePage(self):
            info = self._backend.detect_system()
            lines = [
                f"<b>ОС:</b> {info.get('os', '')} {info.get('release', '')}",
                f"<b>Дистрибутив:</b> {info.get('distro', 'N/A')}",
                f"<b>Рабочий стол:</b> {info.get('desktop', 'N/A')}",
                f"<b>Архитектура:</b> {info.get('machine', '')}",
                f"<b>Python:</b> {info.get('python', '')}",
                f"<b>RAM:</b> {self._backend.state.total_ram_gb:.1f} GB",
            ]
            self._info.setHtml("<br/>".join(lines))

    class LanguagePage(QWizardPage):
        """Шаг 6 — Выбор языка."""

        def __init__(self, backend, parent=None):
            super().__init__(parent)
            self._backend = backend
            self.setTitle("Язык")
            self.setSubTitle("Выберите основной язык")

            layout = QVBoxLayout(self)
            self._combo = QComboBox()
            for lang in backend.get_available_languages():
                self._combo.addItem(lang["name"], lang["code"])
            # Предвыбор по state
            idx = self._combo.findData(backend.state.language)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
            layout.addWidget(QLabel("Язык интерфейса:"))
            layout.addWidget(self._combo)
            layout.addStretch()

        def validatePage(self):
            code = self._combo.currentData()
            if code:
                self._backend.set_language(code)
            return True

    class FeaturesPage(QWizardPage):
        """Шаг 7 — Опциональные функции."""

        def __init__(self, backend, parent=None):
            super().__init__(parent)
            self._backend = backend
            self.setTitle("Дополнительные функции")
            self.setSubTitle("Включите нужные модули")

            layout = QVBoxLayout(self)

            self._cb_gui = QCheckBox("Графический интерфейс (GUI)")
            self._cb_gui.setChecked(backend.state.enable_gui)
            layout.addWidget(self._cb_gui)

            self._cb_voice = QCheckBox("Голосовой ввод/вывод (STT + TTS)")
            self._cb_voice.setChecked(backend.state.enable_voice)
            layout.addWidget(self._cb_voice)

            # Зависимости
            deps = backend.check_optional_deps()
            grp = QGroupBox("Статус зависимостей")
            glayout = QVBoxLayout(grp)
            dep_names = {
                "pyqt6": "PyQt6 (GUI)",
                "espeak_ng": "espeak-ng (TTS)",
                "piper": "piper (TTS качество)",
                "whisper": "whisper-cpp (STT)",
            }
            for key, label in dep_names.items():
                ok = deps.get(key, False)
                icon = "✅" if ok else "❌"
                glayout.addWidget(QLabel(f"{icon} {label}"))
            layout.addWidget(grp)
            layout.addStretch()

        def validatePage(self):
            self._backend.set_optional_features(
                gui=self._cb_gui.isChecked(),
                voice=self._cb_voice.isChecked(),
            )
            return True

    class CompletePage(QWizardPage):
        """Шаг 8 — Завершение."""

        def __init__(self, backend, parent=None):
            super().__init__(parent)
            self._backend = backend
            self.setTitle("Готово!")
            self.setSubTitle("Lina настроена и готова к работе")

            layout = QVBoxLayout(self)
            self._text = QLabel()
            self._text.setWordWrap(True)
            self._text.setStyleSheet("font-size: 14px; padding: 12px;")
            layout.addWidget(self._text)
            layout.addStretch()

        def initializePage(self):
            # Переходим бэкенд на шаг COMPLETE
            from lina.installer.first_run import WizardStep
            self._backend.state.current_step = WizardStep.COMPLETE
            self._text.setText(self._backend.get_completion_text())


# ═══════════════════════════════════════════════════════════════════════════════
#  Главный QWizard
# ═══════════════════════════════════════════════════════════════════════════════

    class FirstRunQWizard(QWizard):
        """GUI-обёртка над FirstRunWizard бэкендом.

        Использование:
            from lina.gui.wizard import FirstRunQWizard
            wiz = FirstRunQWizard()
            if wiz.exec():
                # пользователь завершил настройку
        """

        def __init__(self, backend=None, parent=None):
            super().__init__(parent)
            from lina.installer.first_run import FirstRunWizard
            self._backend = backend or FirstRunWizard()

            self.setWindowTitle("Lina — Первый запуск")
            self.setMinimumSize(640, 480)
            self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

            # Добавляем страницы в порядке шагов
            self.addPage(WelcomePage(self._backend))       # 0
            self.addPage(ModelPage(self._backend))         # 1
            self.addPage(DownloadPage(self._backend))      # 2
            self.addPage(IndexPage(self._backend))         # 3
            self.addPage(SystemPage(self._backend))        # 4
            self.addPage(LanguagePage(self._backend))      # 5
            self.addPage(FeaturesPage(self._backend))      # 6
            self.addPage(CompletePage(self._backend))      # 7

            self.setButtonText(QWizard.WizardButton.NextButton, "Далее ›")
            self.setButtonText(QWizard.WizardButton.BackButton, "‹ Назад")
            self.setButtonText(QWizard.WizardButton.FinishButton, "Готово")
            self.setButtonText(QWizard.WizardButton.CancelButton, "Пропустить")

            logger.info("FirstRunQWizard создан")

        def accept(self):
            """Пользователь нажал Готово — отмечаем первый запуск."""
            self._backend.mark_first_run_done()
            logger.info("Первый запуск завершён, маркер создан")
            super().accept()

        def get_backend(self):
            """Возвращает бэкенд-объект FirstRunWizard."""
            return self._backend

        def get_state_dict(self) -> dict:
            """Текущее состояние мастера как dict."""
            return self._backend.to_dict()

else:
    # Qt недоступен — заглушки
    class FirstRunQWizard:  # type: ignore
        """Заглушка: Qt не установлен."""
        def __init__(self, *a, **kw):
            raise ImportError("PyQt6/PySide6 не установлен")

    WelcomePage = ModelPage = DownloadPage = IndexPage = None  # type: ignore
    SystemPage = LanguagePage = FeaturesPage = CompletePage = None  # type: ignore
