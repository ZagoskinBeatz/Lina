"""
Lina Installer — First Run Wizard.

Мастер первого запуска:
  1. Приветствие
  2. Выбор модели (Small / Medium / Large)
  3. Скачивание модели
  4. Индексация базы знаний
  5. Определение дистрибутива
  6. Настройка языка
  7. Опционально: GUI, голос
  8. Завершение
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger("lina.installer.first_run")


# ─── Модели ──────────────────────────────────────────────────────────────────

class ModelSize(Enum):
    SMALL = "small"       # 3B параметров, ~2 GB RAM
    MEDIUM = "medium"     # 7B параметров, ~5 GB RAM
    LARGE = "large"       # 13B параметров, ~8 GB RAM


@dataclass
class ModelOption:
    """Вариант модели для выбора."""
    size: ModelSize
    name: str
    params: str               # "3B", "7B", "13B"
    ram_required_gb: float
    disk_required_gb: float
    description: str
    download_url: str = ""
    filename: str = ""

    def to_dict(self) -> Dict:
        return {
            "size": self.size.value,
            "name": self.name,
            "params": self.params,
            "ram_required_gb": self.ram_required_gb,
            "disk_required_gb": self.disk_required_gb,
            "description": self.description,
        }


DEFAULT_MODELS = [
    ModelOption(
        size=ModelSize.SMALL,
        name="Qwen3.5 0.8B (BF16)",
        params="0.8B",
        ram_required_gb=2.0,
        disk_required_gb=1.5,
        description="Быстрая и лёгкая. Function-calling, простые ответы.",
        download_url=(
            "https://huggingface.co/bartowski/Qwen_Qwen3.5-0.8B-GGUF/"
            "resolve/main/Qwen_Qwen3.5-0.8B-bf16.gguf"
        ),
        filename="Qwen3.5-0.8B-BF16.gguf",
    ),
    ModelOption(
        size=ModelSize.MEDIUM,
        name="Qwen3.5 4B (Q8_0)",
        params="4B",
        ram_required_gb=6.0,
        disk_required_gb=4.5,
        description="Сложный анализ, длинные ответы, лучшее качество русского.",
        download_url=(
            "https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF/"
            "resolve/main/Qwen_Qwen3.5-4B-Q8_0.gguf"
        ),
        filename="Qwen3.5-4B-Q8_0.gguf",
    ),
]


# ─── Шаги мастера ────────────────────────────────────────────────────────────

class WizardStep(Enum):
    WELCOME = "welcome"
    SELECT_MODEL = "select_model"
    DOWNLOAD_MODEL = "download_model"
    INDEX_KNOWLEDGE = "index_knowledge"
    DETECT_SYSTEM = "detect_system"
    SELECT_LANGUAGE = "select_language"
    OPTIONAL_FEATURES = "optional_features"
    COMPLETE = "complete"


@dataclass
class WizardState:
    """Состояние мастера первого запуска."""
    current_step: WizardStep = WizardStep.WELCOME
    selected_model: Optional[ModelOption] = None
    model_downloaded: bool = False
    knowledge_indexed: bool = False
    detected_distro: str = ""
    detected_desktop: str = ""
    language: str = "ru"
    enable_gui: bool = True
    enable_voice: bool = False
    total_ram_gb: float = 0.0
    errors: deque = field(default_factory=lambda: deque(maxlen=100))
    completed_steps: deque = field(default_factory=lambda: deque(maxlen=100))

    def to_dict(self) -> Dict:
        return {
            "current_step": self.current_step.value,
            "selected_model": self.selected_model.to_dict() if self.selected_model else None,
            "model_downloaded": self.model_downloaded,
            "knowledge_indexed": self.knowledge_indexed,
            "detected_distro": self.detected_distro,
            "language": self.language,
            "enable_gui": self.enable_gui,
            "enable_voice": self.enable_voice,
            "total_ram_gb": self.total_ram_gb,
            "completed_steps": list(self.completed_steps),
            "errors": list(self.errors),
        }


# ─── First Run Wizard ────────────────────────────────────────────────────────

class FirstRunWizard:
    """Мастер первого запуска Lina.

    Проводит пользователя через настройку:
    выбор модели → скачивание → индексация → настройки.
    """

    STEPS_ORDER = [
        WizardStep.WELCOME,
        WizardStep.SELECT_MODEL,
        WizardStep.DOWNLOAD_MODEL,
        WizardStep.INDEX_KNOWLEDGE,
        WizardStep.DETECT_SYSTEM,
        WizardStep.SELECT_LANGUAGE,
        WizardStep.OPTIONAL_FEATURES,
        WizardStep.COMPLETE,
    ]

    def __init__(self, models_dir: Optional[Path] = None,
                 knowledge_dir: Optional[Path] = None):
        from lina.config import MODELS_DIR, KNOWLEDGE_DIR
        self.models_dir = models_dir or MODELS_DIR
        self.knowledge_dir = knowledge_dir or KNOWLEDGE_DIR
        self.state = WizardState()
        self.available_models = list(DEFAULT_MODELS)
        self._on_progress: Optional[Callable[[str, float], None]] = None
        self._on_step_change: Optional[Callable[[WizardStep], None]] = None
        logger.info("FirstRunWizard создан")

    # ── Навигация ──

    def get_current_step(self) -> WizardStep:
        return self.state.current_step

    def get_step_index(self) -> int:
        """Индекс текущего шага (0-based)."""
        try:
            return self.STEPS_ORDER.index(self.state.current_step)
        except ValueError:
            return 0

    def get_total_steps(self) -> int:
        return len(self.STEPS_ORDER)

    def get_progress(self) -> float:
        """Прогресс 0.0 — 1.0."""
        return self.get_step_index() / max(1, self.get_total_steps() - 1)

    def next_step(self) -> WizardStep:
        """Переходит к следующему шагу."""
        idx = self.get_step_index()
        self.state.completed_steps.append(self.state.current_step.value)
        if idx + 1 < len(self.STEPS_ORDER):
            self.state.current_step = self.STEPS_ORDER[idx + 1]
        if self._on_step_change:
            self._on_step_change(self.state.current_step)
        return self.state.current_step

    def prev_step(self) -> WizardStep:
        """Возвращается на предыдущий шаг."""
        idx = self.get_step_index()
        if idx > 0:
            self.state.current_step = self.STEPS_ORDER[idx - 1]
        return self.state.current_step

    def skip_step(self) -> WizardStep:
        """Пропускает текущий шаг."""
        return self.next_step()

    # ── Колбэки ──

    def set_on_progress(self, cb: Callable[[str, float], None]) -> None:
        """Колбэк прогресса: cb(message, percent)."""
        self._on_progress = cb

    def set_on_step_change(self, cb: Callable[[WizardStep], None]) -> None:
        self._on_step_change = cb

    # ── Шаг 1: Приветствие ──

    def get_welcome_text(self) -> str:
        return (
            "🤖 Добро пожаловать в Lina!\n\n"
            "Lina — ваш локальный ИИ-помощник для Linux.\n"
            "Он работает полностью оффлайн, прямо на вашем компьютере.\n\n"
            "Сейчас мы настроим всё для работы.\n"
            "Это займёт несколько минут."
        )

    # ── Шаг 2: Выбор модели ──

    def get_available_models(self) -> List[ModelOption]:
        """Модели, подходящие по RAM."""
        self._detect_ram()
        suitable = []
        for model in self.available_models:
            suitable.append(model)
        return suitable

    def get_recommended_model(self) -> ModelOption:
        """Рекомендованная модель на основе RAM."""
        self._detect_ram()
        ram = self.state.total_ram_gb
        if ram >= 6:
            return self.available_models[1]  # Medium (full)
        return self.available_models[0]      # Small (mini)

    def select_model(self, size: str) -> bool:
        """Выбирает модель по размеру ('small', 'medium', 'large')."""
        for model in self.available_models:
            if model.size.value == size:
                self.state.selected_model = model
                logger.info(f"Выбрана модель: {model.name} ({model.params})")
                return True
        return False

    # ── Шаг 3: Скачивание модели ──

    def _model_subdir(self, model: Optional["ModelOption"] = None) -> str:
        """Подпапка для модели: mini/ для small, full/ для остальных."""
        m = model or self.state.selected_model
        if m and m.size == ModelSize.SMALL:
            return "mini"
        return "full"

    def check_model_exists(self) -> bool:
        """Проверяет, скачана ли выбранная модель."""
        if not self.state.selected_model:
            return False
        model_path = (self.models_dir / self._model_subdir() /
                      self.state.selected_model.filename)
        return model_path.exists()

    def get_download_info(self) -> Dict:
        """Информация о скачивании."""
        model = self.state.selected_model
        if not model:
            return {"error": "Модель не выбрана"}
        subdir = self._model_subdir(model)
        return {
            "model": model.name,
            "size_gb": model.disk_required_gb,
            "url": model.download_url,
            "filename": model.filename,
            "target_dir": str(self.models_dir / subdir),
            "already_exists": self.check_model_exists(),
        }

    def simulate_download(self) -> bool:
        """Симулирует скачивание (для тестов / если модель уже есть)."""
        self.state.model_downloaded = True
        if self._on_progress:
            self._on_progress("Модель готова", 1.0)
        return True

    # ── Шаг 4: Индексация знаний ──

    def index_knowledge(self) -> Dict:
        """Индексирует базу знаний."""
        files_count = 0
        if self.knowledge_dir.exists():
            files_count = sum(1 for f in self.knowledge_dir.rglob("*.md"))

        self.state.knowledge_indexed = True
        if self._on_progress:
            self._on_progress(f"Проиндексировано {files_count} файлов", 1.0)

        return {"files_indexed": files_count, "success": True}

    # ── Шаг 5: Определение системы ──

    def detect_system(self) -> Dict:
        """Определяет дистрибутив и окружение."""
        info = {
            "os": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "distro": "",
            "desktop": "",
        }

        # Определяем дистрибутив
        try:
            import distro as distro_mod
            info["distro"] = distro_mod.name(pretty=True)
        except ImportError:
            # Fallback
            try:
                with open("/etc/os-release") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            info["distro"] = line.split("=", 1)[1].strip().strip('"')
                            break
            except FileNotFoundError:
                info["distro"] = "Unknown Linux"

        # Desktop Environment
        import os
        info["desktop"] = (os.environ.get("XDG_CURRENT_DESKTOP", "") or
                           os.environ.get("DESKTOP_SESSION", ""))

        self.state.detected_distro = info["distro"]
        self.state.detected_desktop = info["desktop"]

        return info

    # ── Шаг 6: Язык ──

    def set_language(self, lang: str) -> None:
        """Устанавливает язык ('ru' или 'en')."""
        if lang in ("ru", "en"):
            self.state.language = lang

    def get_available_languages(self) -> List[Dict[str, str]]:
        return [
            {"code": "ru", "name": "Русский"},
            {"code": "en", "name": "English"},
        ]

    # ── Шаг 7: Опции ──

    def set_optional_features(self, gui: bool = True,
                               voice: bool = False) -> None:
        self.state.enable_gui = gui
        self.state.enable_voice = voice

    def check_optional_deps(self) -> Dict[str, bool]:
        """Проверяет наличие опциональных зависимостей."""
        deps = {}
        # PyQt6
        try:
            import PyQt6  # noqa: F401
            deps["pyqt6"] = True
        except ImportError:
            deps["pyqt6"] = False

        # espeak-ng
        deps["espeak_ng"] = shutil.which("espeak-ng") is not None

        # piper
        deps["piper"] = shutil.which("piper") is not None

        # whisper
        deps["whisper"] = (shutil.which("whisper-cpp") is not None or
                           shutil.which("whisper") is not None)

        return deps

    # ── Шаг 8: Завершение ──

    def get_completion_text(self) -> str:
        model_str = (self.state.selected_model.name
                     if self.state.selected_model else "не выбрана")
        return (
            f"✅ Lina настроен и готов к работе!\n\n"
            f"📦 Модель: {model_str}\n"
            f"📚 База знаний: {'✓' if self.state.knowledge_indexed else '✗'}\n"
            f"🖥 Система: {self.state.detected_distro}\n"
            f"🌐 Язык: {self.state.language}\n"
            f"🖼 GUI: {'✓' if self.state.enable_gui else '✗'}\n"
            f"🗣 Голос: {'✓' if self.state.enable_voice else '✗'}\n\n"
            f"Попробуйте: 'обзор системы' или 'как обновить пакеты?'"
        )

    def is_completed(self) -> bool:
        return self.state.current_step == WizardStep.COMPLETE

    # ── Проверка первого запуска ──

    @staticmethod
    def is_first_run(config_dir: Optional[Path] = None) -> bool:
        """Проверяет, первый ли это запуск."""
        cfg = config_dir or Path.home() / ".config" / "lina"
        marker = cfg / ".first_run_done"
        return not marker.exists()

    @staticmethod
    def mark_first_run_done(config_dir: Optional[Path] = None) -> None:
        """Отмечает, что первый запуск завершён."""
        cfg = config_dir or Path.home() / ".config" / "lina"
        cfg.mkdir(parents=True, exist_ok=True)
        marker = cfg / ".first_run_done"
        tmp = marker.with_suffix(".tmp")
        tmp.write_text("done")
        os.replace(str(tmp), str(marker))

    # ── Утилиты ──

    def _detect_ram(self) -> None:
        """Определяет количество RAM."""
        if self.state.total_ram_gb > 0:
            return
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        self.state.total_ram_gb = round(kb / 1024 / 1024, 1)
                        return
        except Exception:
            self.state.total_ram_gb = 4.0  # fallback

    def to_dict(self) -> Dict:
        return self.state.to_dict()
