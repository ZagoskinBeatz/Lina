"""
Lina — Интернационализация (X3: i18n).

Мультиязычная поддержка:
  - Русский (основной)
  - Английский (второй)
  - Шаблон для добавления языков
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger("lina.core.i18n")


# ─── Языковые пакеты ─────────────────────────────────────────────────────────

TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "ru": {
        # Общие
        "app.name": "Lina",
        "app.description": "Локальный ИИ-помощник для Linux",
        "app.version": "Версия",

        # Интерфейс
        "ui.send": "Отправить",
        "ui.cancel": "Отмена",
        "ui.close": "Закрыть",
        "ui.settings": "Настройки",
        "ui.help": "Помощь",
        "ui.about": "О программе",
        "ui.quit": "Выход",
        "ui.back": "Назад",
        "ui.next": "Далее",
        "ui.skip": "Пропустить",
        "ui.done": "Готово",
        "ui.error": "Ошибка",
        "ui.warning": "Предупреждение",
        "ui.info": "Информация",
        "ui.yes": "Да",
        "ui.no": "Нет",
        "ui.ok": "ОК",
        "ui.save": "Сохранить",
        "ui.reset": "Сбросить",
        "ui.search": "Поиск",
        "ui.loading": "Загрузка...",
        "ui.processing": "Обработка...",

        # Chat
        "chat.placeholder": "Введите сообщение...",
        "chat.thinking": "Думаю...",
        "chat.empty": "Начните диалог!",
        "chat.error": "Ошибка при обработке запроса",
        "chat.welcome": "Привет! Я Lina, ваш Linux-помощник. Чем могу помочь?",
        "chat.unclear": "Извините, я не понял вопрос. Попробуйте переформулировать.",
        "chat.noanswer": "К сожалению, у меня нет информации по этому вопросу.",

        # Tray
        "tray.show": "Показать Lina",
        "tray.hide": "Скрыть",
        "tray.status.ready": "Готов",
        "tray.status.busy": "Занят",
        "tray.status.error": "Ошибка",

        # Settings
        "settings.title": "Настройки Lina",
        "settings.general": "Общие",
        "settings.model": "Модель",
        "settings.voice": "Голос",
        "settings.appearance": "Внешний вид",
        "settings.language": "Язык",
        "settings.theme": "Тема",
        "settings.theme.dark": "Тёмная",
        "settings.theme.light": "Светлая",
        "settings.saved": "Настройки сохранены",

        # Voice
        "voice.listening": "Слушаю...",
        "voice.speaking": "Говорю...",
        "voice.error": "Ошибка распознавания",
        "voice.not_available": "Голос недоступен",

        # Diagnostics
        "diag.running": "Диагностика...",
        "diag.complete": "Диагностика завершена",
        "diag.problem_found": "Проблема найдена",
        "diag.no_problems": "Проблем не обнаружено",

        # Wizard
        "wizard.welcome": "Добро пожаловать в Lina!",
        "wizard.select_model": "Выбор модели",
        "wizard.downloading": "Скачивание модели...",
        "wizard.indexing": "Индексация знаний...",
        "wizard.complete": "Настройка завершена!",

        # Errors
        "error.model_not_found": "Модель не найдена",
        "error.no_memory": "Недостаточно памяти",
        "error.permission": "Нет прав доступа",
        "error.network": "Нет подключения к сети",
        "error.unknown": "Неизвестная ошибка",

        # System
        "system.update_available": "Доступно обновление",
        "system.up_to_date": "Система актуальна",
        "system.offline": "Оффлайн-режим",
    },

    "en": {
        # General
        "app.name": "Lina",
        "app.description": "Local AI assistant for Linux",
        "app.version": "Version",

        # UI
        "ui.send": "Send",
        "ui.cancel": "Cancel",
        "ui.close": "Close",
        "ui.settings": "Settings",
        "ui.help": "Help",
        "ui.about": "About",
        "ui.quit": "Quit",
        "ui.back": "Back",
        "ui.next": "Next",
        "ui.skip": "Skip",
        "ui.done": "Done",
        "ui.error": "Error",
        "ui.warning": "Warning",
        "ui.info": "Information",
        "ui.yes": "Yes",
        "ui.no": "No",
        "ui.ok": "OK",
        "ui.save": "Save",
        "ui.reset": "Reset",
        "ui.search": "Search",
        "ui.loading": "Loading...",
        "ui.processing": "Processing...",

        # Chat
        "chat.placeholder": "Type a message...",
        "chat.thinking": "Thinking...",
        "chat.empty": "Start a conversation!",
        "chat.error": "Error processing request",
        "chat.welcome": "Hi! I'm Lina, your Linux assistant. How can I help?",
        "chat.unclear": "Sorry, I didn't understand. Please rephrase.",
        "chat.noanswer": "Unfortunately, I don't have information on this topic.",

        # Tray
        "tray.show": "Show Lina",
        "tray.hide": "Hide",
        "tray.status.ready": "Ready",
        "tray.status.busy": "Busy",
        "tray.status.error": "Error",

        # Settings
        "settings.title": "Lina Settings",
        "settings.general": "General",
        "settings.model": "Model",
        "settings.voice": "Voice",
        "settings.appearance": "Appearance",
        "settings.language": "Language",
        "settings.theme": "Theme",
        "settings.theme.dark": "Dark",
        "settings.theme.light": "Light",
        "settings.saved": "Settings saved",

        # Voice
        "voice.listening": "Listening...",
        "voice.speaking": "Speaking...",
        "voice.error": "Recognition error",
        "voice.not_available": "Voice not available",

        # Diagnostics
        "diag.running": "Diagnosing...",
        "diag.complete": "Diagnosis complete",
        "diag.problem_found": "Problem found",
        "diag.no_problems": "No problems found",

        # Wizard
        "wizard.welcome": "Welcome to Lina!",
        "wizard.select_model": "Select model",
        "wizard.downloading": "Downloading model...",
        "wizard.indexing": "Indexing knowledge...",
        "wizard.complete": "Setup complete!",

        # Errors
        "error.model_not_found": "Model not found",
        "error.no_memory": "Not enough memory",
        "error.permission": "Permission denied",
        "error.network": "No network connection",
        "error.unknown": "Unknown error",

        # System
        "system.update_available": "Update available",
        "system.up_to_date": "System is up to date",
        "system.offline": "Offline mode",
    },
}


# ─── I18n Engine ──────────────────────────────────────────────────────────────

class I18n:
    """Интернационализация Lina.

    Использование:
        i18n = I18n("ru")
        print(i18n.t("chat.welcome"))
        # → "Привет! Я Lina, ваш Linux-помощник. Чем могу помочь?"
    """

    SUPPORTED_LANGUAGES = ["ru", "en"]
    DEFAULT_LANGUAGE = "ru"

    def __init__(self, language: str = "ru"):
        self._language = language if language in self.SUPPORTED_LANGUAGES else "ru"
        self._overrides: Dict[str, str] = {}
        logger.info("I18n: язык=%s", self._language)

    @property
    def language(self) -> str:
        return self._language

    @language.setter
    def language(self, lang: str) -> None:
        if lang in self.SUPPORTED_LANGUAGES:
            self._language = lang
            logger.info("I18n: переключён на %s", lang)

    def t(self, key: str, **kwargs) -> str:
        """Переводит ключ.

        Args:
            key: Ключ перевода (e.g. "chat.welcome")
            **kwargs: Подстановки в строку

        Returns:
            Переведённая строка, или ключ если не найден.
        """
        # Сначала проверяем override
        if key in self._overrides:
            text = self._overrides[key]
        else:
            lang_dict = TRANSLATIONS.get(self._language, {})
            text = lang_dict.get(key)
            if text is None:
                # Fallback на русский
                text = TRANSLATIONS.get("ru", {}).get(key, key)

        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, IndexError):
                pass

        return text

    def add_override(self, key: str, value: str) -> None:
        """Добавляет пользовательский перевод."""
        self._overrides[key] = value

    def remove_override(self, key: str) -> None:
        """Удаляет пользовательский перевод."""
        self._overrides.pop(key, None)

    def has_key(self, key: str) -> bool:
        """Проверяет, есть ли перевод для ключа."""
        if key in self._overrides:
            return True
        return key in TRANSLATIONS.get(self._language, {})

    def get_all_keys(self) -> list:
        """Все доступные ключи для текущего языка."""
        keys = set(TRANSLATIONS.get(self._language, {}).keys())
        keys.update(self._overrides.keys())
        return sorted(keys)

    def get_keys_by_prefix(self, prefix: str) -> Dict[str, str]:
        """Все переводы с указанным префиксом."""
        result = {}
        for key in self.get_all_keys():
            if key.startswith(prefix):
                result[key] = self.t(key)
        return result

    def get_missing_keys(self) -> list:
        """Ключи, отсутствующие в текущем языке (есть в ru)."""
        ru_keys = set(TRANSLATIONS.get("ru", {}).keys())
        current_keys = set(TRANSLATIONS.get(self._language, {}).keys())
        return sorted(ru_keys - current_keys)

    def get_supported_languages(self) -> list:
        return list(self.SUPPORTED_LANGUAGES)

    def load_language_pack(self, lang: str,
                            translations: Dict[str, str]) -> None:
        """Загружает пользовательский языковой пакет."""
        _MAX_LANGUAGES = 20
        # Validate lang: alphanumeric, max 10 chars
        if not lang or len(lang) > 10 or not lang.replace("_", "").replace("-", "").isalnum():
            logger.warning("I18n: invalid language code: %s", lang[:20])
            return
        if lang not in TRANSLATIONS:
            if len(TRANSLATIONS) >= _MAX_LANGUAGES:
                logger.warning("I18n: language limit reached (%d)", _MAX_LANGUAGES)
                return
            TRANSLATIONS[lang] = {}
            if lang not in self.SUPPORTED_LANGUAGES:
                self.SUPPORTED_LANGUAGES.append(lang)
        TRANSLATIONS[lang].update(translations)
        logger.info("Загружен языковой пакет: %s (%d ключей)",
                     lang, len(translations))

    def to_dict(self) -> Dict:
        return {
            "language": self._language,
            "supported": self.SUPPORTED_LANGUAGES,
            "total_keys": len(self.get_all_keys()),
            "overrides": len(self._overrides),
        }


# ─── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[I18n] = None


def get_i18n(language: str = "ru") -> I18n:
    """Получить глобальный экземпляр I18n."""
    global _instance
    if _instance is None:
        _instance = I18n(language)
    return _instance


def reset_i18n() -> None:
    """Сбросить глобальный экземпляр I18n."""
    global _instance
    _instance = None
