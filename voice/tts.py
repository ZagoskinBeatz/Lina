"""
Lina Voice — Text-to-Speech (TTS).

Поддерживаемые бэкенды (в порядке приоритета):
  1. piper-tts    (офлайн, хорошее качество, русский)
  2. espeak-ng    (офлайн, везде есть, качество ниже)
  3. edge-tts     (онлайн, Microsoft, высокое качество)

Класс TextToSpeech:
  - speak(text)          → озвучить текст
  - speak_async(text)    → озвучить без блокировки
  - stop()               → прервать озвучку
  - set_voice(name)      → выбрать голос
  - set_speed(rate)      → скорость (0.5-2.0)
  - is_available()       → проверка
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, List, Callable, Dict, Any

logger = logging.getLogger("lina.voice.tts")


# ─── Конфигурация ────────────────────────────────────────────────────────────

class TTSBackend(Enum):
    PIPER = "piper"
    ESPEAK = "espeak"
    EDGE_TTS = "edge_tts"
    NONE = "none"


@dataclass
class VoiceInfo:
    """Информация о голосе."""
    name: str
    language: str
    backend: str
    description: str = ""
    sample_rate: int = 22050


@dataclass
class TTSConfig:
    """Конфигурация Text-to-Speech."""
    # Бэкенд
    preferred_backend: str = "auto"  # auto / piper / espeak / edge_tts

    # Piper
    piper_binary: str = "piper"          # имя или путь к piper binary
    piper_model: str = ""                # путь к .onnx модели
    piper_config: str = ""               # путь к .json конфигу модели

    # espeak-ng
    espeak_binary: str = "espeak-ng"
    espeak_voice: str = "ru"

    # edge-tts
    edge_voice: str = "ru-RU-DmitryNeural"

    # Общие
    language: str = "ru"
    speed: float = 1.0          # 0.5 — 2.0
    volume: float = 1.0         # 0.0 — 1.0
    pitch: float = 1.0          # 0.5 — 2.0
    output_format: str = "wav"  # wav / mp3 / ogg

    # Проигрывание
    player_binary: str = "auto"  # auto / aplay / paplay / ffplay

    # Streaming
    streaming_enabled: bool = True  # Озвучка по предложениям
    sentence_pause_ms: int = 200    # Пауза между предложениями

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preferred_backend": self.preferred_backend,
            "language": self.language,
            "speed": self.speed,
            "volume": self.volume,
            "pitch": self.pitch,
            "streaming_enabled": self.streaming_enabled,
        }


# ─── Аудио-проигрыватель ─────────────────────────────────────────────────────

class AudioPlayer:
    """Проигрывание аудио через системный плеер.

    Поддерживает: paplay (PulseAudio), aplay (ALSA),
    pw-play (PipeWire), ffplay (FFmpeg).
    """

    def __init__(self, preferred: str = "auto"):
        self._backend: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None
        self._playing = False
        self._detect_backend(preferred)

    def _detect_backend(self, preferred: str) -> None:
        """Определяем плеер."""
        if preferred != "auto" and shutil.which(preferred):
            self._backend = preferred
            return

        for player in ["pw-play", "paplay", "aplay", "ffplay"]:
            if shutil.which(player):
                self._backend = player
                return
        self._backend = None

    def is_available(self) -> bool:
        return self._backend is not None

    def get_backend(self) -> Optional[str]:
        return self._backend

    def play(self, audio_path: str) -> bool:
        """Проигрывает аудиофайл (блокирующий)."""
        if not self._backend:
            logger.warning("Нет аудио-плеера")
            return False

        cmd = self._build_command(audio_path)
        try:
            self._playing = True
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._process.wait()
            self._playing = False
            return self._process.returncode == 0
        except Exception as e:
            logger.error(f"Ошибка воспроизведения: {e}")
            self._playing = False
            return False

    def play_async(self, audio_path: str, on_done: Optional[Callable] = None) -> bool:
        """Проигрывает аудиофайл в фоне."""
        if not self._backend:
            return False

        def _play():
            self.play(audio_path)
            if on_done:
                on_done()

        thread = threading.Thread(target=_play, daemon=True)
        thread.start()
        return True

    def stop(self) -> bool:
        """Прерывает воспроизведение."""
        if self._process and self._playing:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                self._process.kill()
            self._playing = False
            logger.info("Воспроизведение прервано")
            return True
        return False

    def is_playing(self) -> bool:
        return self._playing

    def _build_command(self, audio_path: str) -> List[str]:
        """Строит команду для проигрывания."""
        if self._backend in ("pw-play", "paplay", "aplay"):
            return [self._backend, audio_path]
        elif self._backend == "ffplay":
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                    audio_path]
        return [self._backend, audio_path]


# ─── TTS движок ──────────────────────────────────────────────────────────────

class TextToSpeech:
    """Главный класс синтеза речи.

    Автоматически определяет лучший бэкенд.
    Поддерживает streaming (озвучка по предложениям).
    """

    def __init__(self, config: Optional[TTSConfig] = None):
        self.config = config or TTSConfig()
        self._backend = TTSBackend.NONE
        self._player = AudioPlayer(self.config.player_binary)
        self._speaking = False
        self._stop_requested = False
        self._temp_files: list[str] = []  # track generated files for cleanup
        self._on_start: Optional[Callable[[str], None]] = None
        self._on_done: Optional[Callable[[], None]] = None
        self._on_sentence: Optional[Callable[[str, int], None]] = None
        self._detect_backend()
        logger.info(f"TTS инициализирован: backend={self._backend.value}")

    def _detect_backend(self) -> None:
        """Определяет доступный TTS бэкенд."""
        pref = self.config.preferred_backend

        if pref == "auto" or pref == "piper":
            if shutil.which(self.config.piper_binary) or shutil.which("piper"):
                self._backend = TTSBackend.PIPER
                return

        if pref == "auto" or pref == "espeak":
            if shutil.which(self.config.espeak_binary) or shutil.which("espeak-ng") \
               or shutil.which("espeak"):
                self._backend = TTSBackend.ESPEAK
                return

        if pref == "auto" or pref == "edge_tts":
            try:
                import edge_tts  # noqa: F401
                self._backend = TTSBackend.EDGE_TTS
                return
            except ImportError:
                pass

        self._backend = TTSBackend.NONE

    # ── Публичный API ──

    def is_available(self) -> bool:
        """Доступен ли TTS."""
        return self._backend != TTSBackend.NONE

    def get_backend(self) -> str:
        """Имя текущего бэкенда."""
        return self._backend.value

    def get_available_voices(self) -> List[VoiceInfo]:
        """Список доступных голосов."""
        voices: List[VoiceInfo] = []

        if self._backend == TTSBackend.PIPER:
            voices.append(VoiceInfo(
                name="ru-default", language="ru", backend="piper",
                description="Piper TTS русский голос",
            ))
        elif self._backend == TTSBackend.ESPEAK:
            for lang in ["ru", "en"]:
                voices.append(VoiceInfo(
                    name=lang, language=lang, backend="espeak",
                    description=f"espeak-ng {lang}",
                ))
        elif self._backend == TTSBackend.EDGE_TTS:
            for v in ["ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural"]:
                voices.append(VoiceInfo(
                    name=v, language="ru", backend="edge_tts",
                    description=f"Microsoft Edge TTS: {v}",
                ))

        return voices

    def speak(self, text: str) -> bool:
        """Озвучивает текст (блокирующий).

        Args:
            text: Текст для озвучки.

        Returns:
            True если озвучка завершена.
        """
        if not text or not self.is_available():
            return False

        self._speaking = True
        self._stop_requested = False

        if self._on_start:
            self._on_start(text)

        try:
            if self.config.streaming_enabled:
                return self._speak_streaming(text)
            else:
                return self._speak_full(text)
        finally:
            self._speaking = False
            # v0.8.0: cleanup temp files to prevent resource leak
            self._cleanup_temp_files()
            if self._on_done:
                self._on_done()

    def _cleanup_temp_files(self) -> None:
        """Remove accumulated temp audio files."""
        for path in self._temp_files:
            try:
                if Path(path).exists():
                    Path(path).unlink()
            except OSError:
                pass
        self._temp_files.clear()

    def speak_async(self, text: str,
                    on_done: Optional[Callable] = None) -> bool:
        """Озвучивает текст в фоновом потоке."""
        if not text or not self.is_available():
            return False

        def _worker():
            self.speak(text)
            if on_done:
                on_done()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return True

    def stop(self) -> bool:
        """Прерывает текущую озвучку."""
        if not self._speaking:
            return False
        self._stop_requested = True
        self._player.stop()
        self._speaking = False
        logger.info("Озвучка прервана")
        return True

    def is_speaking(self) -> bool:
        """Идёт ли озвучка."""
        return self._speaking

    def set_voice(self, name: str) -> None:
        """Устанавливает голос."""
        if self._backend == TTSBackend.ESPEAK:
            self.config.espeak_voice = name
        elif self._backend == TTSBackend.EDGE_TTS:
            self.config.edge_voice = name
        logger.info(f"Голос установлен: {name}")

    def set_speed(self, rate: float) -> None:
        """Устанавливает скорость (0.5 — 2.0)."""
        self.config.speed = max(0.5, min(2.0, rate))
        logger.debug(f"Скорость: {self.config.speed}")

    def set_volume(self, volume: float) -> None:
        """Устанавливает громкость (0.0 — 1.0)."""
        self.config.volume = max(0.0, min(1.0, volume))

    # ── Колбэки ──

    def set_on_start(self, cb: Callable[[str], None]) -> None:
        """Колбэк при начале озвучки."""
        self._on_start = cb

    def set_on_done(self, cb: Callable[[], None]) -> None:
        """Колбэк при завершении озвучки."""
        self._on_done = cb

    def set_on_sentence(self, cb: Callable[[str, int], None]) -> None:
        """Колбэк при озвучке каждого предложения (text, index)."""
        self._on_sentence = cb

    # ── Внутренние методы ──

    def _speak_full(self, text: str) -> bool:
        """Озвучка всего текста целиком."""
        audio_path = self._synthesize(text)
        if not audio_path:
            return False
        return self._player.play(audio_path)

    def _speak_streaming(self, text: str) -> bool:
        """Озвучка по предложениям."""
        sentences = self._split_sentences(text)
        for i, sentence in enumerate(sentences):
            if self._stop_requested:
                break
            if not sentence.strip():
                continue
            if self._on_sentence:
                self._on_sentence(sentence, i)
            audio_path = self._synthesize(sentence)
            if audio_path:
                self._player.play(audio_path)
        return not self._stop_requested

    def _synthesize(self, text: str) -> Optional[str]:
        """Синтезирует текст в аудиофайл.

        Returns:
            Путь к аудиофайлу или None.
        """
        if self._backend == TTSBackend.PIPER:
            return self._synth_piper(text)
        elif self._backend == TTSBackend.ESPEAK:
            return self._synth_espeak(text)
        elif self._backend == TTSBackend.EDGE_TTS:
            return self._synth_edge(text)
        return None

    def _synth_piper(self, text: str) -> Optional[str]:
        """Синтез через piper-tts."""
        binary = shutil.which(self.config.piper_binary) or shutil.which("piper")
        if not binary:
            return None

        outfile = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        outpath = outfile.name
        outfile.close()
        self._temp_files.append(outpath)
        cmd = [binary]
        if self.config.piper_model:
            cmd.extend(["--model", self.config.piper_model])
        if self.config.piper_config:
            cmd.extend(["--config", self.config.piper_config])
        cmd.extend(["--output_file", outpath])

        try:
            proc = subprocess.run(
                cmd, input=text, capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0 and Path(outpath).exists():
                return outpath
            logger.error(f"piper ошибка: {proc.stderr[:200]}")
            return None
        except Exception as e:
            logger.error(f"piper exception: {e}")
            return None

    def _synth_espeak(self, text: str) -> Optional[str]:
        """Синтез через espeak-ng."""
        binary = (shutil.which(self.config.espeak_binary) or
                  shutil.which("espeak-ng") or shutil.which("espeak"))
        if not binary:
            return None

        outfile = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        outpath = outfile.name
        outfile.close()
        self._temp_files.append(outpath)
        speed_wpm = int(175 * self.config.speed)

        cmd = [
            binary,
            "-v", self.config.espeak_voice,
            "-s", str(speed_wpm),
            "-w", outpath,
            text,
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=15)
            if proc.returncode == 0 and Path(outpath).exists():
                return outpath
            return None
        except Exception as e:
            logger.error(f"espeak ошибка: {e}")
            return None

    def _synth_edge(self, text: str) -> Optional[str]:
        """Синтез через edge-tts (async → sync wrapper)."""
        try:
            import asyncio
            import edge_tts

            outfile = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            outpath = outfile.name
            outfile.close()
            self._temp_files.append(outpath)

            async def _gen():
                comm = edge_tts.Communicate(
                    text,
                    self.config.edge_voice,
                    rate=f"{int((self.config.speed - 1) * 100):+d}%",
                )
                await comm.save(outpath)

            # Запускаем async в sync контексте
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # Уже в async контексте
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(lambda: asyncio.run(_gen())).result(timeout=30)
            else:
                asyncio.run(_gen())

            if Path(outpath).exists():
                return outpath
            return None
        except Exception as e:
            logger.error(f"edge-tts ошибка: {e}")
            return None

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """Разбивает текст на предложения."""
        import re
        # Разделяем по . ! ? и \n
        sentences = re.split(r'(?<=[.!?])\s+|\n+', text)
        return [s.strip() for s in sentences if s.strip()]

    # ── Сериализация ──

    def to_dict(self) -> Dict:
        """Состояние TTS."""
        return {
            "available": self.is_available(),
            "backend": self._backend.value,
            "speaking": self._speaking,
            "speed": self.config.speed,
            "volume": self.config.volume,
            "language": self.config.language,
            "voices": [{"name": v.name, "lang": v.language}
                       for v in self.get_available_voices()],
            "config": self.config.to_dict(),
        }

    def get_info(self) -> str:
        """Краткая информация для отладки."""
        return (f"TTS: backend={self._backend.value}, "
                f"speed={self.config.speed}, "
                f"lang={self.config.language}")
