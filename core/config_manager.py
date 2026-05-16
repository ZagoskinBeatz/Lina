# -*- coding: utf-8 -*-
"""
Lina Core — Config Manager (Phase 22).

Единый центр конфигурации с runtime override + persistent config.
Все параметры читаются ТОЛЬКО через ConfigManager.

Параметры:
  max_history_messages     — макс. сообщений в истории (20)
  max_rag_tokens           — макс. токенов RAG-контекста (500)
  max_tool_output_tokens   — макс. токенов вывода tool (300)
  router_confidence_threshold — порог уверенности роутера (0.5)
  llm_max_tokens_cap       — макс. cap для генерации LLM (512)
  safe_mode                — безопасный режим (False)
  debug_mode               — отладка (False)
  auto_regenerate          — авто-перегенерация при провале валидации (True)
  strict_validation        — строгая валидация (False)
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("lina.core.config_manager")


# ═══════════════════════════════════════════════════════════
#  Default Config Values
# ═══════════════════════════════════════════════════════════

@dataclass
class LinaConfig:
    """Все параметры конфигурации Lina."""
    max_history_messages: int = 20
    max_rag_tokens: int = 500
    max_tool_output_tokens: int = 300
    router_confidence_threshold: float = 0.5
    llm_max_tokens_cap: int = 512
    safe_mode: bool = False
    debug_mode: bool = False
    auto_regenerate: bool = True
    strict_validation: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __post_init__(self):
        self._validate()

    def _validate(self):
        """Валидация значений. Raises ValueError on invalid config."""
        if not (1 <= self.max_history_messages <= 100):
            raise ValueError(
                f"max_history_messages must be 1–100, got {self.max_history_messages}")
        if not (0 <= self.max_rag_tokens <= 5000):
            raise ValueError(
                f"max_rag_tokens must be 0–5000, got {self.max_rag_tokens}")
        if not (0 <= self.max_tool_output_tokens <= 2000):
            raise ValueError(
                f"max_tool_output_tokens must be 0–2000, got {self.max_tool_output_tokens}")
        if not (0.0 <= self.router_confidence_threshold <= 1.0):
            raise ValueError(
                f"router_confidence_threshold must be 0.0–1.0, got {self.router_confidence_threshold}")
        if not (32 <= self.llm_max_tokens_cap <= 4096):
            raise ValueError(
                f"llm_max_tokens_cap must be 32–4096, got {self.llm_max_tokens_cap}")


# ═══════════════════════════════════════════════════════════
#  Config Manager
# ═══════════════════════════════════════════════════════════

class ConfigManager:
    """Менеджер конфигурации (Phase 22).

    Поддерживает:
      - Дефолтные значения (LinaConfig)
      - Runtime override (set/get/reset)
      - Persistent config (load/save JSON)

    Все модули читают параметры ТОЛЬКО через ConfigManager.
    ConfigManager НИКОГДА не вызывает engine-ы.
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Args:
            config_path: Путь к persistent JSON-конфигу.
                         Если None → только runtime.
        """
        self._config = LinaConfig()
        self._overrides: Dict[str, Any] = {}
        self._config_path = Path(config_path) if config_path else None

        if self._config_path and self._config_path.exists():
            self._load()

    def get(self, key: str, default: Any = None) -> Any:
        """Получить значение параметра.

        Приоритет: override → persistent → default.
        """
        if key in self._overrides:
            return self._overrides[key]
        if hasattr(self._config, key):
            return getattr(self._config, key)
        return default

    def set(self, key: str, value: Any, persist: bool = False) -> bool:
        """Установить runtime override.

        Args:
            key: Имя параметра.
            value: Новое значение.
            persist: True → сохранить в файл.

        Returns:
            True если успешно, False если невалидно.
        """
        if not hasattr(self._config, key):
            logger.warning("CONFIG: unknown parameter '%s'", key)
            return False

        # Type check
        expected_type = type(getattr(self._config, key))
        try:
            typed_value = expected_type(value)
        except (ValueError, TypeError):
            logger.warning("CONFIG: cannot cast '%s' to %s", value, expected_type.__name__)
            return False

        # Validate
        try:
            test = LinaConfig(**{**self._config.to_dict(), key: typed_value})
        except (ValueError, TypeError) as e:
            logger.warning("CONFIG: validation failed for %s=%s: %s", key, value, e)
            return False

        self._overrides[key] = typed_value
        setattr(self._config, key, typed_value)

        if persist and self._config_path:
            self._save()

        logger.debug("CONFIG_SET: %s = %s (persist=%s)", key, typed_value, persist)
        return True

    def reset(self, key: Optional[str] = None) -> None:
        """Сброс override (одного или всех).

        Args:
            key: Конкретный параметр. None → сброс всех.
        """
        if key:
            self._overrides.pop(key, None)
            # Restore default
            default = LinaConfig()
            if hasattr(default, key):
                setattr(self._config, key, getattr(default, key))
        else:
            self._overrides.clear()
            self._config = LinaConfig()

        logger.debug("CONFIG_RESET: %s", key or "ALL")

    def get_all(self) -> Dict[str, Any]:
        """Все текущие значения (с учётом overrides)."""
        return self._config.to_dict()

    def get_overrides(self) -> Dict[str, Any]:
        """Только runtime overrides."""
        return dict(self._overrides)

    def _load(self) -> None:
        """Загрузить persistent config."""
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(self._config, k):
                    try:
                        self.set(k, v)
                    except Exception as e:
                        logger.warning("CONFIG_LOAD: skip key %s: %s", k, type(e).__name__)
            logger.debug("CONFIG_LOAD: loaded from %s", self._config_path)
        except Exception as e:
            logger.warning("CONFIG_LOAD: failed: %s", e)

    def _save(self) -> None:
        """Сохранить в persistent config."""
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._config_path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(self._config.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_path.replace(self._config_path)  # atomic on POSIX
            logger.debug("CONFIG_SAVE: saved to %s", self._config_path)
        except Exception as e:
            logger.warning("CONFIG_SAVE: failed: %s", e)

    def save(self) -> bool:
        """Публичный метод сохранения."""
        if not self._config_path:
            return False
        self._save()
        return True
