"""
Lina Voice — Voice Pipeline.

Полный цикл голосового взаимодействия:
    User speaks → STT → text → Lina pipeline → response → TTS → User hears

Особенности:
  - Streaming TTS: озвучивает первое предложение, пока генерируется второе
  - Прерывание голосом: "стоп", "хватит"
  - VAD: автоматическое начало записи по голосу
  - Push-to-talk режим
"""

from __future__ import annotations

import logging
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, List, Dict, Any

logger = logging.getLogger("lina.voice.pipeline")


# ─── Состояния pipeline ──────────────────────────────────────────────────────

class VoicePipelineState(Enum):
    IDLE = "idle"                   # Ожидание
    LISTENING = "listening"         # Запись голоса
    PROCESSING = "processing"       # STT распознаёт
    THINKING = "thinking"           # Lina обрабатывает запрос
    SPEAKING = "speaking"           # TTS озвучивает
    ERROR = "error"                 # Ошибка


class InteractionMode(Enum):
    PUSH_TO_TALK = "push_to_talk"   # Говорим по кнопке
    VAD = "vad"                     # Автоматическая активация по голосу
    CONTINUOUS = "continuous"       # Непрерывное прослушивание


# ─── Конфигурация ────────────────────────────────────────────────────────────

@dataclass
class VoicePipelineConfig:
    """Конфигурация голосового pipeline."""
    mode: str = "push_to_talk"       # push_to_talk / vad / continuous
    auto_listen_after_response: bool = True  # Продолжать слушать после ответа
    beep_on_start: bool = True       # Звуковой сигнал начала записи
    beep_on_end: bool = True         # Звуковой сигнал конца записи
    max_idle_timeout_s: float = 300  # Таймаут бездействия (5 мин)
    interrupt_words: List[str] = field(default_factory=lambda: [
        "стоп", "хватит", "тихо", "замолчи", "stop",
    ])
    wake_word: Optional[str] = None  # Слово активации (None = отключено)
    response_prefix: str = ""        # Префикс перед ответом ("Так,")
    listen_timeout_s: float = 15.0   # Макс. ожидание речи

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "auto_listen_after_response": self.auto_listen_after_response,
            "interrupt_words": self.interrupt_words,
            "wake_word": self.wake_word,
            "listen_timeout_s": self.listen_timeout_s,
        }


# ─── Событие pipeline ────────────────────────────────────────────────────────

@dataclass
class VoiceEvent:
    """Событие голосового pipeline."""
    event_type: str                 # state_change / text_recognized / response / error
    state: VoicePipelineState = VoicePipelineState.IDLE
    text: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "event_type": self.event_type,
            "state": self.state.value,
            "text": self.text,
            "timestamp": self.timestamp,
        }


# ─── Voice Pipeline ──────────────────────────────────────────────────────────

class VoicePipeline:
    """Голосовой pipeline: STT → обработка → TTS.

    Связывает все голосовые компоненты в единый цикл.
    STT и TTS можно подключить через set_stt() / set_tts().
    Обработчик запросов подключается через set_request_handler().
    """

    def __init__(self, config: Optional[VoicePipelineConfig] = None):
        self.config = config or VoicePipelineConfig()
        self._state = VoicePipelineState.IDLE
        self._mode = InteractionMode(self.config.mode)
        self._running = False
        self._session_active = False
        self._lock = threading.Lock()  # guards mutable state

        # Компоненты (подключаются извне или автоматически)
        self._stt = None
        self._tts = None
        self._request_handler: Optional[Callable[[str], str]] = None

        # Колбэки
        self._on_state_change: Optional[Callable[[VoicePipelineState], None]] = None
        self._on_event: Optional[Callable[[VoiceEvent], None]] = None
        self._on_text_recognized: Optional[Callable[[str], None]] = None
        self._on_response: Optional[Callable[[str], None]] = None

        # История
        self._events: deque = deque(maxlen=1000)
        self._conversation: deque = deque(maxlen=200)
        self._total_interactions = 0
        self._errors_count = 0
        self._session_thread: Optional[threading.Thread] = None

        logger.info(f"VoicePipeline создан: mode={self._mode.value}")

    # ── Настройка компонентов ──

    def set_stt(self, stt) -> None:
        """Устанавливает STT движок."""
        self._stt = stt
        logger.info(f"STT подключён: {getattr(stt, 'get_backend', lambda: 'unknown')()}")

    def set_tts(self, tts) -> None:
        """Устанавливает TTS движок."""
        self._tts = tts
        logger.info(f"TTS подключён: {getattr(tts, 'get_backend', lambda: 'unknown')()}")

    def set_request_handler(self, handler: Callable[[str], str]) -> None:
        """Устанавливает обработчик запросов (main_pipeline)."""
        self._request_handler = handler

    # ── Колбэки ──

    def set_on_state_change(self, cb: Callable[[VoicePipelineState], None]) -> None:
        self._on_state_change = cb

    def set_on_event(self, cb: Callable[[VoiceEvent], None]) -> None:
        self._on_event = cb

    def set_on_text_recognized(self, cb: Callable[[str], None]) -> None:
        self._on_text_recognized = cb

    def set_on_response(self, cb: Callable[[str], None]) -> None:
        self._on_response = cb

    # ── Управление состоянием ──

    def _set_state(self, new_state: VoicePipelineState) -> None:
        """Меняет состояние с уведомлением."""
        with self._lock:
            old = self._state
            self._state = new_state
        event = VoiceEvent(
            event_type="state_change",
            state=new_state,
            metadata={"previous": old.value},
        )
        self._events.append(event)
        if self._on_state_change:
            self._on_state_change(new_state)
        if self._on_event:
            self._on_event(event)
        logger.debug(f"Состояние: {old.value} → {new_state.value}")

    def get_state(self) -> VoicePipelineState:
        """Текущее состояние."""
        return self._state

    def is_active(self) -> bool:
        """Активен ли pipeline (не IDLE и не ERROR)."""
        return self._state not in (VoicePipelineState.IDLE,
                                    VoicePipelineState.ERROR)

    # ── Одиночное взаимодействие ──

    def process_single(self, user_text: Optional[str] = None) -> Dict[str, Any]:
        """Одно полное взаимодействие: [запись →] STT → обработка → TTS.

        Args:
            user_text: Готовый текст (пропускает STT). Если None → записывает.

        Returns:
            Dict: recognized_text, response_text, success, error, duration_ms
        """
        start = time.time()
        result: Dict[str, Any] = {
            "recognized_text": "",
            "response_text": "",
            "success": False,
            "error": None,
            "duration_ms": 0,
            "was_interrupted": False,
            "was_cancelled": False,
        }

        try:
            # 1. STT (если текст не передан)
            if user_text is None:
                self._set_state(VoicePipelineState.LISTENING)
                text = self._do_listen()
                if not text:
                    result["error"] = "Не удалось распознать речь"
                    self._set_state(VoicePipelineState.IDLE)
                    return result
            else:
                text = user_text

            result["recognized_text"] = text

            # Проверяем cancel/interrupt
            if self._stt and hasattr(self._stt, 'is_cancel_word') and \
               self._stt.is_cancel_word(text):
                result["was_cancelled"] = True
                self._set_state(VoicePipelineState.IDLE)
                return result

            if self._is_interrupt_word(text):
                result["was_interrupted"] = True
                if self._tts:
                    self._tts.stop()
                self._set_state(VoicePipelineState.IDLE)
                return result

            # Уведомляем о распознанном тексте
            event = VoiceEvent(
                event_type="text_recognized",
                state=VoicePipelineState.PROCESSING,
                text=text,
            )
            self._events.append(event)
            if self._on_text_recognized:
                self._on_text_recognized(text)
            if self._on_event:
                self._on_event(event)

            # 2. Обработка запроса
            self._set_state(VoicePipelineState.THINKING)
            response = self._do_process(text)
            if not response:
                result["error"] = "Не удалось получить ответ"
                self._set_state(VoicePipelineState.ERROR)
                self._errors_count += 1
                return result

            result["response_text"] = response

            # Уведомляем об ответе
            resp_event = VoiceEvent(
                event_type="response",
                state=VoicePipelineState.SPEAKING,
                text=response,
            )
            self._events.append(resp_event)
            if self._on_response:
                self._on_response(response)
            if self._on_event:
                self._on_event(resp_event)

            # 3. TTS
            self._set_state(VoicePipelineState.SPEAKING)
            self._do_speak(response)

            # Сохраняем в историю
            self._conversation.append({"role": "user", "text": text})
            self._conversation.append({"role": "assistant", "text": response})
            self._total_interactions += 1

            result["success"] = True
            self._set_state(VoicePipelineState.IDLE)

        except Exception as e:
            logger.error("Pipeline error: %s", e, exc_info=True)
            result["error"] = "Внутренняя ошибка голосового конвейера."
            self._errors_count += 1
            self._set_state(VoicePipelineState.ERROR)

        finally:
            result["duration_ms"] = int((time.time() - start) * 1000)

        return result

    # ── Циклический режим ──

    def start_session(self) -> None:
        """Запускает непрерывную сессию голосового взаимодействия.

        В цикле: слушает → обрабатывает → озвучивает → опять слушает.
        Останавливается через stop_session().
        """
        with self._lock:
            self._session_active = True
            self._running = True
        logger.info("Голосовая сессия начата")

    def stop_session(self) -> None:
        """Останавливает сессию."""
        with self._lock:
            self._session_active = False
            self._running = False
        # Дождаться завершения фонового потока
        t = self._session_thread
        if t is not None and t.is_alive():
            t.join(timeout=5.0)
        self._session_thread = None
        if self._tts:
            self._tts.stop()
        self._set_state(VoicePipelineState.IDLE)
        logger.info("Голосовая сессия остановлена")

    def is_session_active(self) -> bool:
        """Активна ли сессия."""
        return self._session_active

    def run_session_loop(self) -> None:
        """Основной цикл сессии (блокирующий).

        Вызывается в отдельном потоке.
        """
        while self._session_active:
            result = self.process_single()
            if result.get("was_cancelled"):
                logger.info("Сессия отменена пользователем")
                break
            if result.get("error") and not self.config.auto_listen_after_response:
                break
            if not self.config.auto_listen_after_response:
                break

        self.stop_session()

    def start_session_async(self) -> None:
        """Запускает сессию в фоне."""
        self.start_session()
        self._session_thread = threading.Thread(
            target=self.run_session_loop, daemon=True, name="voice-session",
        )
        self._session_thread.start()

    # ── Внутренние методы ──

    def _do_listen(self) -> str:
        """Записывает и распознаёт голос через STT."""
        if not self._stt:
            logger.warning("STT не подключён")
            return ""
        try:
            if hasattr(self._stt, 'listen_for'):
                return self._stt.listen_for(self.config.listen_timeout_s)
            return ""
        except Exception as e:
            logger.error(f"Ошибка STT: {e}")
            return ""

    def _do_process(self, text: str) -> str:
        """Обрабатывает текст через pipeline."""
        if not self._request_handler:
            logger.warning("Request handler не подключён")
            return ""
        try:
            prefix = self.config.response_prefix
            response = self._request_handler(text)
            if prefix:
                return f"{prefix} {response}"
            return response
        except Exception as e:
            logger.error(f"Ошибка обработки: {e}")
            return ""

    def _do_speak(self, text: str) -> None:
        """Озвучивает текст через TTS."""
        if not self._tts:
            logger.debug("TTS не подключён, пропускаем озвучку")
            return
        try:
            if hasattr(self._tts, 'speak'):
                self._tts.speak(text)
        except Exception as e:
            logger.error(f"Ошибка TTS: {e}")

    def _is_interrupt_word(self, text: str) -> bool:
        """Проверяет, является ли текст командой прерывания."""
        text_lower = text.lower().strip()
        for word in self.config.interrupt_words:
            if word in text_lower:
                return True
        return False

    # ── История и статистика ──

    def get_conversation(self) -> List[Dict[str, str]]:
        """История разговора."""
        return list(self._conversation)

    def clear_conversation(self) -> None:
        """Очищает историю."""
        self._conversation.clear()

    def get_events(self, limit: int = 50) -> List[Dict]:
        """Последние события."""
        return [e.to_dict() for e in list(self._events)[-limit:]]

    def get_stats(self) -> Dict[str, Any]:
        """Статистика сессии."""
        return {
            "total_interactions": self._total_interactions,
            "errors_count": self._errors_count,
            "conversation_length": len(self._conversation),
            "events_count": len(self._events),
            "session_active": self._session_active,
            "current_state": self._state.value,
        }

    # ── Сериализация ──

    def to_dict(self) -> Dict:
        """Полное состояние pipeline."""
        return {
            "state": self._state.value,
            "mode": self._mode.value,
            "session_active": self._session_active,
            "has_stt": self._stt is not None,
            "has_tts": self._tts is not None,
            "has_handler": self._request_handler is not None,
            "stats": self.get_stats(),
            "config": self.config.to_dict(),
        }


# ─── Фабрика ─────────────────────────────────────────────────────────────────

def create_voice_pipeline(
    request_handler: Optional[Callable[[str], str]] = None,
    config: Optional[VoicePipelineConfig] = None,
) -> VoicePipeline:
    """Создаёт VoicePipeline с автоматическим подключением STT/TTS.

    Args:
        request_handler: Функция обработки текста (main_pipeline)
        config: Конфигурация pipeline

    Returns:
        Настроенный VoicePipeline
    """
    pipeline = VoicePipeline(config)

    # Подключаем STT
    try:
        from lina.voice.stt import SpeechToText
        stt = SpeechToText()
        if stt.is_available():
            pipeline.set_stt(stt)
    except Exception as e:
        logger.warning(f"STT недоступен: {e}")

    # Подключаем TTS
    try:
        from lina.voice.tts import TextToSpeech
        tts = TextToSpeech()
        if tts.is_available():
            pipeline.set_tts(tts)
    except Exception as e:
        logger.warning(f"TTS недоступен: {e}")

    # Подключаем handler
    if request_handler:
        pipeline.set_request_handler(request_handler)

    return pipeline
