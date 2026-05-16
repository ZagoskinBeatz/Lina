"""
Lina GUI — Settings Window.

Окно настроек:
  - Модель: путь, n_ctx, RAM-лимит
  - GUI: тема, горячая клавиша, автозапуск
  - Pipeline: safe_mode, streaming
  - Голос: вкл./откл. STT/TTS (будущее)

Настройки хранятся в ~/.config/lina/settings.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List, Callable, Tuple

logger = logging.getLogger("lina.gui.settings")

# ── Путь к файлу настроек ──
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "lina"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "settings.json"


# ─── Модель настроек ──────────────────────────────────────────────────────────

@dataclass
class SettingsSection:
    """Базовый класс для секции настроек."""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "SettingsSection":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in valid})


@dataclass
class ModelSettings(SettingsSection):
    """Настройки LLM-модели."""
    model_path: str = ""
    n_ctx: int = 4096
    n_threads: int = 4
    n_gpu_layers: int = 0
    max_ram_mb: int = 6144
    temperature: float = 0.7
    max_tokens: int = 512


@dataclass
class GUISettings(SettingsSection):
    """Настройки графического интерфейса."""
    theme: str = "dark"                  # dark / light / system
    hotkey: str = "Meta+J"
    window_width: int = 420
    window_height: int = 620
    font_size: int = 13
    opacity: float = 0.95
    show_tray_icon: bool = True
    start_minimized: bool = False
    autostart: bool = False
    enable_animations: bool = True
    language: str = "ru"


@dataclass
class PipelineSettings(SettingsSection):
    """Настройки Pipeline."""
    safe_mode: bool = False
    pure_model_mode: bool = False
    pure_model_tier: str = "full"
    enable_rag: bool = True
    enable_tools: bool = True
    enable_web: bool = True
    enable_cv: bool = False
    enable_streaming: bool = True
    enable_notifications: bool = True


@dataclass
class VoiceSettings(SettingsSection):
    """Настройки голосового ввода/вывода."""
    stt_enabled: bool = False
    tts_enabled: bool = False
    stt_model: str = "whisper-small"
    tts_engine: str = "piper"
    tts_voice: str = "ru-default"
    tts_speed: float = 1.0
    tts_volume: float = 1.0
    voice_language: str = "ru"
    push_to_talk_key: str = "Ctrl+Space"
    vad_enabled: bool = True


# ─── Контроллер настроек ──────────────────────────────────────────────────────

class SettingsController:
    """Управляет загрузкой, сохранением и уведомлением об изменениях."""

    SECTIONS = {
        "model": ModelSettings,
        "gui": GUISettings,
        "pipeline": PipelineSettings,
        "voice": VoiceSettings,
    }

    def __init__(self, config_file: Optional[Path] = None):
        self.config_file = config_file or DEFAULT_CONFIG_FILE
        self.model = ModelSettings()
        self.gui = GUISettings()
        self.pipeline = PipelineSettings()
        self.voice = VoiceSettings()
        self._change_listeners: List[Callable[[str, str, Any, Any], None]] = []
        self._dirty = False
        logger.info(f"SettingsController: файл={self.config_file}")

    # ── Загрузка / Сохранение ──

    def load(self) -> bool:
        """Загружает настройки из JSON. Возвращает True если файл существовал."""
        if not self.config_file.exists():
            logger.info("Файл настроек не найден, используются дефолтные")
            return False

        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
            for section_name, cls in self.SECTIONS.items():
                if section_name in data and isinstance(data[section_name], dict):
                    setattr(self, section_name, cls.from_dict(data[section_name]))
            self._dirty = False
            logger.info("Настройки загружены")
            return True
        except Exception as e:
            logger.error(f"Ошибка загрузки настроек: {e}")
            return False

    def save(self) -> bool:
        """Сохраняет настройки в JSON."""
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for section_name in self.SECTIONS:
                section = getattr(self, section_name)
                data[section_name] = section.to_dict()
            self.config_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self._dirty = False
            logger.info("Настройки сохранены")
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек: {e}")
            return False

    def reset_to_defaults(self) -> None:
        """Сбрасывает все настройки к дефолтным."""
        self.model = ModelSettings()
        self.gui = GUISettings()
        self.pipeline = PipelineSettings()
        self.voice = VoiceSettings()
        self._dirty = True
        logger.info("Настройки сброшены к дефолтным")

    # ── Getters / Setters ──

    def get(self, section: str, key: str) -> Any:
        """Получает значение: get('gui', 'theme') → 'dark'."""
        sec = getattr(self, section, None)
        if sec is None:
            raise KeyError(f"Секция не найдена: {section}")
        if not hasattr(sec, key):
            raise KeyError(f"Ключ не найден: {section}.{key}")
        return getattr(sec, key)

    def set(self, section: str, key: str, value: Any) -> None:
        """Устанавливает значение с уведомлением слушателей."""
        sec = getattr(self, section, None)
        if sec is None:
            raise KeyError(f"Секция не найдена: {section}")
        if not hasattr(sec, key):
            raise KeyError(f"Ключ не найден: {section}.{key}")

        old_val = getattr(sec, key)
        if old_val == value:
            return  # без изменений

        setattr(sec, key, value)
        self._dirty = True
        logger.debug(f"Изменено: {section}.{key} = {value!r} (было {old_val!r})")

        # Уведомляем слушателей
        for listener in self._change_listeners:
            try:
                listener(section, key, old_val, value)
            except Exception as e:
                logger.error(f"Ошибка в listener: {e}")

    def is_dirty(self) -> bool:
        """Есть ли несохранённые изменения."""
        return self._dirty

    # ── Слушатели ──

    def add_change_listener(self, cb: Callable[[str, str, Any, Any], None]) -> None:
        """Добавляет слушателя изменений: cb(section, key, old, new)."""
        self._change_listeners.append(cb)

    def remove_change_listener(self, cb: Callable) -> None:
        """Удаляет слушателя."""
        if cb in self._change_listeners:
            self._change_listeners.remove(cb)

    # ── Полный дамп ──

    def to_dict(self) -> Dict[str, Dict]:
        """Полный дамп всех настроек."""
        return {
            name: getattr(self, name).to_dict()
            for name in self.SECTIONS
        }

    def get_section_keys(self, section: str) -> List[Tuple[str, Any, type]]:
        """Возвращает список (key, value, type) для секции.

        Полезно для динамической генерации GUI формы.
        """
        sec = getattr(self, section, None)
        if sec is None:
            return []
        result = []
        for f_name, f_obj in sec.__dataclass_fields__.items():
            val = getattr(sec, f_name)
            result.append((f_name, val, type(val)))
        return result

    def validate(self) -> List[str]:
        """Проверяет корректность настроек. Возвращает список ошибок."""
        errors = []

        if self.model.n_ctx < 256:
            errors.append("model.n_ctx: минимум 256 токенов")
        if self.model.n_ctx > 32768:
            errors.append("model.n_ctx: максимум 32768 токенов")
        if self.model.n_threads < 1:
            errors.append("model.n_threads: минимум 1")
        if self.model.n_threads > 64:
            errors.append("model.n_threads: максимум 64")
        if self.model.max_ram_mb < 512:
            errors.append("model.max_ram_mb: минимум 512 MB")
        if not (0.0 <= self.model.temperature <= 2.0):
            errors.append("model.temperature: 0.0 — 2.0")
        if not (1 <= self.model.max_tokens <= 8192):
            errors.append("model.max_tokens: 1 — 8192")

        if self.gui.font_size < 8 or self.gui.font_size > 32:
            errors.append("gui.font_size: 8 — 32")
        if self.gui.theme not in ("dark", "light", "system"):
            errors.append("gui.theme: dark / light / system")
        if not (0.3 <= self.gui.opacity <= 1.0):
            errors.append("gui.opacity: 0.3 — 1.0")
        if self.gui.language not in ("ru", "en"):
            errors.append("gui.language: ru / en")

        if self.pipeline.pure_model_tier not in ("full", "mini"):
            errors.append("pipeline.pure_model_tier: full / mini")

        if not (0.5 <= self.voice.tts_speed <= 2.0):
            errors.append("voice.tts_speed: 0.5 — 2.0")

        return errors

    def get_summary(self) -> str:
        """Краткое описание текущих настроек (для тултипа/лога)."""
        lines = [
            f"Модель: n_ctx={self.model.n_ctx}, потоки={self.model.n_threads}, "
            f"RAM≤{self.model.max_ram_mb}MB",
            f"GUI: тема={self.gui.theme}, горячая={self.gui.hotkey}, "
            f"автозапуск={'да' if self.gui.autostart else 'нет'}",
            f"Pipeline: pure={'✓' if self.pipeline.pure_model_mode else '✗'}"
            f"/{self.pipeline.pure_model_tier}, "
            f"safe={self.pipeline.safe_mode}, "
            f"RAG={'✓' if self.pipeline.enable_rag else '✗'}, "
            f"tools={'✓' if self.pipeline.enable_tools else '✗'}",
            f"Голос: STT={'✓' if self.voice.stt_enabled else '✗'}, "
            f"TTS={'✓' if self.voice.tts_enabled else '✗'}",
        ]
        return "\n".join(lines)


# ─── Singleton ──

_settings_instance: Optional[SettingsController] = None


def get_settings(config_file: Optional[Path] = None) -> SettingsController:
    """Возвращает singleton SettingsController."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = SettingsController(config_file)
        _settings_instance.load()
    return _settings_instance


def reset_settings() -> None:
    """Сбрасывает singleton (для тестов)."""
    global _settings_instance
    _settings_instance = None
