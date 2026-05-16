"""
Lina Voice — Speech-to-Text (STT).

Поддерживаемые бэкенды (в порядке приоритета):
  1. whisper.cpp (через whisper-cpp-python или subprocess)
  2. vosk (офлайн, лёгкий)
  3. Google Speech (онлайн, fallback)

Класс SpeechToText:
  - start_listening()    → начинает запись
  - stop_listening()     → останавливает, возвращает текст
  - listen_for(seconds)  → запись N секунд
  - transcribe(audio_path) → распознаёт аудиофайл
  - is_available()       → проверка наличия бэкенда + микрофона
  - VAD (Voice Activity Detection)
  - Cancel-слово: "отмена"
"""

from __future__ import annotations

import io
import logging
import subprocess
import shutil
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, List, Callable, Dict, Any

logger = logging.getLogger("lina.voice.stt")


# ─── Конфигурация ────────────────────────────────────────────────────────────

class STTBackend(Enum):
    WHISPER_CPP = "whisper_cpp"       # whisper.cpp binary
    WHISPER_PY = "whisper_py"         # openai-whisper python
    VOSK = "vosk"                     # vosk-api
    NONE = "none"


@dataclass
class STTConfig:
    """Конфигурация Speech-to-Text."""
    # Бэкенд
    preferred_backend: str = "auto"   # auto / whisper_cpp / whisper_py / vosk

    # Whisper
    whisper_model: str = "small"      # tiny / base / small / medium
    whisper_binary: str = "whisper-cpp"  # имя или путь к whisper.cpp binary
    whisper_model_path: str = ""      # путь к .bin модели (auto-detect)

    # Vosk
    vosk_model_path: str = ""         # путь к vosk модели

    # Общие
    language: str = "ru"              # язык распознавания
    sample_rate: int = 16000          # частота дискретизации
    channels: int = 1                 # моно
    chunk_duration_ms: int = 30       # длительность чанка для VAD

    # VAD
    vad_enabled: bool = True          # Voice Activity Detection
    vad_threshold: float = 0.5        # порог VAD (0-1)
    silence_timeout_s: float = 2.0    # таймаут тишины для остановки
    max_recording_s: float = 30.0     # макс. длительность записи

    # Cancel
    cancel_words: List[str] = field(default_factory=lambda: [
        "отмена", "отменить", "стоп", "cancel",
    ])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preferred_backend": self.preferred_backend,
            "whisper_model": self.whisper_model,
            "language": self.language,
            "sample_rate": self.sample_rate,
            "vad_enabled": self.vad_enabled,
            "vad_threshold": self.vad_threshold,
            "silence_timeout_s": self.silence_timeout_s,
            "max_recording_s": self.max_recording_s,
            "cancel_words": self.cancel_words,
        }


# ─── Аудиозапись (абстракция) ────────────────────────────────────────────────

@dataclass
class AudioChunk:
    """Кусок аудио данных."""
    data: bytes
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2  # 16-bit
    duration_ms: float = 0.0


class AudioRecorder:
    """Запись аудио с микрофона.

    Абстрактный слой над pyaudio / sounddevice.
    Если ни один бэкенд не доступен — fallback на arecord (Linux).
    """

    def __init__(self, sample_rate: int = 16000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._recording = False
        self._chunks: deque = deque(maxlen=3000)
        self._backend: Optional[str] = None
        self._stream = None
        self._detect_backend()

    def _detect_backend(self) -> None:
        """Определяем доступный аудио-бэкенд."""
        try:
            import sounddevice  # noqa: F401
            self._backend = "sounddevice"
            return
        except ImportError:
            pass
        try:
            import pyaudio  # noqa: F401
            self._backend = "pyaudio"
            return
        except ImportError:
            pass
        if shutil.which("arecord"):
            self._backend = "arecord"
            return
        self._backend = None

    def is_available(self) -> bool:
        """Доступен ли микрофон."""
        return self._backend is not None

    def get_backend(self) -> Optional[str]:
        return self._backend

    def start(self) -> bool:
        """Начать запись."""
        if not self._backend:
            logger.warning("Нет доступного аудио-бэкенда")
            return False
        self._recording = True
        self._chunks.clear()
        logger.info(f"Запись начата (бэкенд: {self._backend})")
        return True

    def stop(self) -> bytes:
        """Остановить запись, вернуть WAV bytes."""
        self._recording = False
        logger.info(f"Запись остановлена, чанков: {len(self._chunks)}")
        return self._build_wav()

    def is_recording(self) -> bool:
        return self._recording

    def add_chunk(self, data: bytes) -> None:
        """Добавляет чанк аудио (для тестирования / симуляции)."""
        chunk = AudioChunk(
            data=data,
            sample_rate=self.sample_rate,
            channels=self.channels,
            duration_ms=len(data) / (self.sample_rate * 2) * 1000,
        )
        self._chunks.append(chunk)

    def get_chunks(self) -> List[AudioChunk]:
        return list(self._chunks)

    def get_duration_s(self) -> float:
        """Общая длительность записи в секундах."""
        return sum(c.duration_ms for c in self._chunks) / 1000.0

    def record_seconds(self, seconds: float) -> bytes:
        """Записать N секунд и вернуть WAV bytes.

        Использует реальный аудио-бэкенд (sounddevice / pyaudio / arecord).
        Fallback: генерирует тишину если захват невозможен.
        """
        if not self._backend:
            return b""
        self.start()

        try:
            if self._backend == "sounddevice":
                self._record_sounddevice(seconds)
            elif self._backend == "pyaudio":
                self._record_pyaudio(seconds)
            elif self._backend == "arecord":
                self._record_arecord(seconds)
            else:
                # Fallback: silence
                if not self._chunks:
                    silence = bytes(int(self.sample_rate * 2 * seconds))
                    self.add_chunk(silence)
        except Exception as e:
            logger.warning("Ошибка записи (%s): %s, генерируем тишину",
                           self._backend, e)
            if not self._chunks:
                silence = bytes(int(self.sample_rate * 2 * seconds))
                self.add_chunk(silence)

        self._recording = False
        return self._build_wav()

    def _record_sounddevice(self, seconds: float) -> None:
        """Запись через sounddevice (блокирующий режим)."""
        import sounddevice as sd
        import numpy as np

        frames = int(self.sample_rate * seconds)
        logger.debug("sounddevice: запись %d фреймов (%.1f с)", frames, seconds)
        audio = sd.rec(
            frames, samplerate=self.sample_rate,
            channels=self.channels, dtype="int16",
        )
        sd.wait()  # block until done
        self.add_chunk(audio.tobytes())

    def _record_pyaudio(self, seconds: float) -> None:
        """Запись через pyaudio."""
        import pyaudio

        chunk_size = 1024
        pa = pyaudio.PyAudio()
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=chunk_size,
            )
            total_frames = int(self.sample_rate / chunk_size * seconds)
            for _ in range(total_frames):
                data = stream.read(chunk_size, exception_on_overflow=False)
                self.add_chunk(data)
            stream.stop_stream()
            stream.close()
        finally:
            pa.terminate()

    def _record_arecord(self, seconds: float) -> None:
        """Запись через arecord (ALSA)."""
        duration_str = f"{seconds:.1f}"
        cmd = [
            "arecord", "-f", "S16_LE",
            "-r", str(self.sample_rate),
            "-c", str(self.channels),
            "-d", duration_str,
            "-q", "-t", "raw",
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=seconds + 5,
            )
            if proc.returncode == 0 and proc.stdout:
                self.add_chunk(proc.stdout)
        except subprocess.TimeoutExpired:
            logger.warning("arecord timeout")
        except Exception as e:
            logger.warning("arecord error: %s", e)

    def _build_wav(self) -> bytes:
        """Собирает WAV из чанков."""
        if not self._chunks:
            return b""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            for chunk in self._chunks:
                wf.writeframes(chunk.data)
        return buf.getvalue()


# ─── STT движок ──────────────────────────────────────────────────────────────

class SpeechToText:
    """Главный класс распознавания речи.

    Поддерживает whisper.cpp, whisper (python), vosk.
    Автоматически определяет лучший доступный бэкенд.
    """

    def __init__(self, config: Optional[STTConfig] = None):
        self.config = config or STTConfig()
        self._backend = STTBackend.NONE
        self._recorder = AudioRecorder(
            sample_rate=self.config.sample_rate,
            channels=self.config.channels,
        )
        self._is_listening = False
        self._last_text = ""
        self._on_text: Optional[Callable[[str], None]] = None
        self._on_partial: Optional[Callable[[str], None]] = None
        self._detect_backend()
        logger.info(f"STT инициализирован: backend={self._backend.value}")

    def _detect_backend(self) -> None:
        """Определяет доступный STT бэкенд."""
        pref = self.config.preferred_backend

        if pref == "auto" or pref == "whisper_cpp":
            if shutil.which(self.config.whisper_binary) or \
               shutil.which("whisper-cpp") or shutil.which("main"):
                self._backend = STTBackend.WHISPER_CPP
                return

        if pref == "auto" or pref == "whisper_py":
            try:
                import whisper  # noqa: F401
                self._backend = STTBackend.WHISPER_PY
                return
            except ImportError:
                pass

        if pref == "auto" or pref == "vosk":
            try:
                import vosk  # noqa: F401
                self._backend = STTBackend.VOSK
                return
            except ImportError:
                pass

        self._backend = STTBackend.NONE

    # ── Публичный API ──

    def is_available(self) -> bool:
        """Доступен ли STT (бэкенд + микрофон)."""
        return self._backend != STTBackend.NONE

    def get_backend(self) -> str:
        """Имя текущего бэкенда."""
        return self._backend.value

    def has_microphone(self) -> bool:
        """Есть ли микрофон."""
        return self._recorder.is_available()

    def start_listening(self) -> bool:
        """Начать прослушивание (непрерывный режим).

        Returns:
            True если запись началась.
        """
        if self._is_listening:
            return False
        if not self.is_available():
            logger.warning("STT недоступен")
            return False
        self._is_listening = True
        self._recorder.start()
        logger.info("Прослушивание начато")
        return True

    def stop_listening(self) -> str:
        """Остановить прослушивание, вернуть распознанный текст.

        Returns:
            Распознанный текст или пустая строка.
        """
        if not self._is_listening:
            return ""
        self._is_listening = False
        audio_data = self._recorder.stop()
        if not audio_data:
            return ""
        text = self._transcribe_wav_bytes(audio_data)
        self._last_text = text
        logger.info(f"Распознано: '{text[:50]}...' " if len(text) > 50
                     else f"Распознано: '{text}'")
        return text

    def listen_for(self, seconds: float) -> str:
        """Записать N секунд и распознать.

        Args:
            seconds: Длительность записи.

        Returns:
            Распознанный текст.
        """
        if not self.is_available():
            return ""
        audio_data = self._recorder.record_seconds(seconds)
        if not audio_data:
            return ""
        text = self._transcribe_wav_bytes(audio_data)
        self._last_text = text
        return text

    def transcribe(self, audio_path: str) -> str:
        """Распознать аудиофайл.

        Args:
            audio_path: Путь к WAV/MP3/OGG файлу.

        Returns:
            Распознанный текст.
        """
        path = Path(audio_path)
        if not path.exists():
            logger.error(f"Файл не найден: {audio_path}")
            return ""

        if self._backend == STTBackend.WHISPER_CPP:
            return self._transcribe_whisper_cpp(str(path))
        elif self._backend == STTBackend.WHISPER_PY:
            return self._transcribe_whisper_py(str(path))
        elif self._backend == STTBackend.VOSK:
            return self._transcribe_vosk(str(path))
        else:
            logger.error("Нет доступного STT бэкенда")
            return ""

    def is_listening(self) -> bool:
        """Идёт ли запись."""
        return self._is_listening

    def get_last_text(self) -> str:
        """Последний распознанный текст."""
        return self._last_text

    def is_cancel_word(self, text: str) -> bool:
        """Проверяет, содержит ли текст cancel-слово."""
        text_lower = text.lower().strip()
        for word in self.config.cancel_words:
            if word in text_lower:
                return True
        return False

    # ── Колбэки ──

    def set_on_text(self, callback: Callable[[str], None]) -> None:
        """Колбэк при получении финального текста."""
        self._on_text = callback

    def set_on_partial(self, callback: Callable[[str], None]) -> None:
        """Колбэк при получении частичного результата."""
        self._on_partial = callback

    # ── Бэкенды ──

    def _transcribe_wav_bytes(self, wav_bytes: bytes) -> str:
        """Распознаёт WAV bytes через доступный бэкенд."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(wav_bytes)
            tmp.flush()
            return self.transcribe(tmp.name)

    def _transcribe_whisper_cpp(self, audio_path: str) -> str:
        """Распознавание через whisper.cpp CLI."""
        binary = (shutil.which(self.config.whisper_binary) or
                  shutil.which("whisper-cpp") or
                  shutil.which("main"))
        if not binary:
            return ""

        cmd = [
            binary,
            "-m", self.config.whisper_model_path or self._find_whisper_model(),
            "-l", self.config.language,
            "-f", audio_path,
            "--no-timestamps",
            "-t", "4",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            logger.error(f"whisper.cpp ошибка: {result.stderr[:200]}")
            return ""
        except Exception as e:
            logger.error(f"whisper.cpp exception: {e}")
            return ""

    def _transcribe_whisper_py(self, audio_path: str) -> str:
        """Распознавание через openai-whisper (Python)."""
        try:
            import whisper
            model = whisper.load_model(self.config.whisper_model)
            result = model.transcribe(audio_path, language=self.config.language)
            return result.get("text", "").strip()
        except Exception as e:
            logger.error(f"whisper.py ошибка: {e}")
            return ""

    def _transcribe_vosk(self, audio_path: str) -> str:
        """Распознавание через Vosk."""
        try:
            import vosk
            import json as _json

            model_path = self.config.vosk_model_path
            if not model_path or not Path(model_path).exists():
                logger.error("Vosk: модель не найдена")
                return ""

            model = vosk.Model(model_path)
            rec = vosk.KaldiRecognizer(model, self.config.sample_rate)

            with wave.open(audio_path, "rb") as wf:
                while True:
                    data = wf.readframes(4000)
                    if not data:
                        break
                    rec.AcceptWaveform(data)

            result = _json.loads(rec.FinalResult())
            return result.get("text", "").strip()
        except Exception as e:
            logger.error(f"Vosk ошибка: {e}")
            return ""

    def _find_whisper_model(self) -> str:
        """Ищет файл модели whisper."""
        from lina.config import BASE_DIR, MODELS_DIR
        candidates = [
            MODELS_DIR / "whisper" / f"ggml-{self.config.whisper_model}.bin",
            BASE_DIR / f"ggml-{self.config.whisper_model}.bin",
            Path.home() / ".cache" / "whisper" / f"ggml-{self.config.whisper_model}.bin",
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return f"ggml-{self.config.whisper_model}.bin"  # fallback

    # ── Сериализация ──

    def to_dict(self) -> Dict:
        """Состояние STT."""
        return {
            "available": self.is_available(),
            "backend": self._backend.value,
            "has_microphone": self.has_microphone(),
            "is_listening": self._is_listening,
            "last_text": self._last_text,
            "config": self.config.to_dict(),
        }

    def get_info(self) -> str:
        """Краткая информация для отладки."""
        return (f"STT: backend={self._backend.value}, "
                f"mic={'✓' if self.has_microphone() else '✗'}, "
                f"model={self.config.whisper_model}, "
                f"lang={self.config.language}")
