# -*- coding: utf-8 -*-
"""
Lina — Voice Module Tests (STT, TTS, Pipeline, Integration).

Deep Audit Round 4 / v0.9.0 milestone.
Tests: test_1769 – test_1838 (70 tests).
"""

import io
import re
import threading
import time
import unittest
import wave
from collections import deque
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch, PropertyMock


# ═══════════════════════════════════════════════════════════════════════════════
#  Block A — STT Configuration & AudioRecorder
# ═══════════════════════════════════════════════════════════════════════════════

class TestSTTConfig(unittest.TestCase):
    """STTConfig default values and serialization."""

    def test_1769_stt_config_defaults(self):
        """STTConfig has sensible defaults."""
        from lina.voice.stt import STTConfig
        cfg = STTConfig()
        self.assertEqual(cfg.preferred_backend, "auto")
        self.assertEqual(cfg.language, "ru")
        self.assertEqual(cfg.sample_rate, 16000)
        self.assertEqual(cfg.channels, 1)
        self.assertTrue(cfg.vad_enabled)

    def test_1770_stt_config_to_dict(self):
        """STTConfig.to_dict() returns all expected keys."""
        from lina.voice.stt import STTConfig
        cfg = STTConfig()
        d = cfg.to_dict()
        self.assertIn("preferred_backend", d)
        self.assertIn("language", d)
        self.assertIn("sample_rate", d)
        self.assertIn("vad_enabled", d)
        self.assertIn("cancel_words", d)
        self.assertIsInstance(d["cancel_words"], list)

    def test_1771_stt_config_cancel_words(self):
        """Default cancel words include отмена and cancel."""
        from lina.voice.stt import STTConfig
        cfg = STTConfig()
        self.assertIn("отмена", cfg.cancel_words)
        self.assertIn("cancel", cfg.cancel_words)

    def test_1772_stt_backend_enum(self):
        """STTBackend enum has expected values."""
        from lina.voice.stt import STTBackend
        self.assertEqual(STTBackend.WHISPER_CPP.value, "whisper_cpp")
        self.assertEqual(STTBackend.VOSK.value, "vosk")
        self.assertEqual(STTBackend.NONE.value, "none")

    def test_1773_stt_config_custom_values(self):
        """STTConfig accepts custom overrides."""
        from lina.voice.stt import STTConfig
        cfg = STTConfig(
            preferred_backend="vosk",
            language="en",
            sample_rate=44100,
            vad_enabled=False,
        )
        self.assertEqual(cfg.preferred_backend, "vosk")
        self.assertEqual(cfg.language, "en")
        self.assertEqual(cfg.sample_rate, 44100)
        self.assertFalse(cfg.vad_enabled)


class TestAudioRecorder(unittest.TestCase):
    """AudioRecorder: recording, chunks, WAV building."""

    def test_1774_recorder_init(self):
        """AudioRecorder initializes with defaults."""
        from lina.voice.stt import AudioRecorder
        rec = AudioRecorder.__new__(AudioRecorder)
        rec.sample_rate = 16000
        rec.channels = 1
        rec._recording = False
        rec._chunks = deque(maxlen=3000)
        rec._backend = None
        rec._stream = None
        self.assertEqual(rec.sample_rate, 16000)
        self.assertFalse(rec.is_recording())

    def test_1775_recorder_add_chunk(self):
        """add_chunk() stores AudioChunks in deque."""
        from lina.voice.stt import AudioRecorder
        rec = AudioRecorder.__new__(AudioRecorder)
        rec.sample_rate = 16000
        rec.channels = 1
        rec._recording = False
        rec._chunks = deque(maxlen=3000)
        rec._backend = "test"
        rec._stream = None

        data = bytes(3200)  # 100ms of silence at 16kHz 16-bit mono
        rec.add_chunk(data)
        self.assertEqual(len(rec._chunks), 1)
        self.assertEqual(rec._chunks[0].data, data)

    def test_1776_recorder_get_duration(self):
        """get_duration_s() calculates total duration from chunks."""
        from lina.voice.stt import AudioRecorder
        rec = AudioRecorder.__new__(AudioRecorder)
        rec.sample_rate = 16000
        rec.channels = 1
        rec._recording = False
        rec._chunks = deque(maxlen=3000)
        rec._backend = "test"
        rec._stream = None

        # 1 second of audio = 32000 bytes (16kHz × 2 bytes × 1 channel)
        rec.add_chunk(bytes(32000))
        self.assertAlmostEqual(rec.get_duration_s(), 1.0, delta=0.01)

    def test_1777_recorder_build_wav(self):
        """_build_wav() produces valid WAV bytes."""
        from lina.voice.stt import AudioRecorder
        rec = AudioRecorder.__new__(AudioRecorder)
        rec.sample_rate = 16000
        rec.channels = 1
        rec._recording = False
        rec._chunks = deque(maxlen=3000)
        rec._backend = "test"
        rec._stream = None

        rec.add_chunk(bytes(3200))
        wav = rec._build_wav()
        self.assertTrue(len(wav) > 44)  # WAV header is 44 bytes
        # Verify it's valid WAV
        buf = io.BytesIO(wav)
        with wave.open(buf, "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getframerate(), 16000)
            self.assertEqual(wf.getsampwidth(), 2)

    def test_1778_recorder_empty_wav(self):
        """_build_wav() returns empty bytes if no chunks."""
        from lina.voice.stt import AudioRecorder
        rec = AudioRecorder.__new__(AudioRecorder)
        rec._chunks = deque()
        wav = rec._build_wav()
        self.assertEqual(wav, b"")

    def test_1779_recorder_start_stop(self):
        """start() and stop() toggle recording state."""
        from lina.voice.stt import AudioRecorder
        rec = AudioRecorder.__new__(AudioRecorder)
        rec.sample_rate = 16000
        rec.channels = 1
        rec._recording = False
        rec._chunks = deque(maxlen=3000)
        rec._backend = "test"
        rec._stream = None

        self.assertTrue(rec.start())
        self.assertTrue(rec.is_recording())
        wav = rec.stop()
        self.assertFalse(rec.is_recording())

    def test_1780_recorder_start_no_backend_fails(self):
        """start() returns False when no backend available."""
        from lina.voice.stt import AudioRecorder
        rec = AudioRecorder.__new__(AudioRecorder)
        rec._backend = None
        rec._recording = False
        rec._chunks = deque()
        rec._stream = None
        self.assertFalse(rec.start())

    def test_1781_recorder_record_seconds_silence(self):
        """record_seconds() generates silence when no real audio."""
        from lina.voice.stt import AudioRecorder
        rec = AudioRecorder.__new__(AudioRecorder)
        rec.sample_rate = 16000
        rec.channels = 1
        rec._recording = False
        rec._chunks = deque(maxlen=3000)
        rec._backend = "test"
        rec._stream = None

        wav = rec.record_seconds(0.5)
        self.assertTrue(len(wav) > 0)


# ═══════════════════════════════════════════════════════════════════════════════
#  Block B — SpeechToText
# ═══════════════════════════════════════════════════════════════════════════════

class TestSpeechToText(unittest.TestCase):
    """SpeechToText: backend detection, API, cancel words."""

    def _make_stt(self, backend="none"):
        """Create STT with forced backend for testing."""
        from lina.voice.stt import SpeechToText, STTConfig, STTBackend
        stt = SpeechToText.__new__(SpeechToText)
        stt.config = STTConfig()
        stt._backend = STTBackend(backend)
        stt._recorder = MagicMock()
        stt._recorder.is_available.return_value = (backend != "none")
        stt._is_listening = False
        stt._last_text = ""
        stt._on_text = None
        stt._on_partial = None
        return stt

    def test_1782_stt_is_available_none(self):
        """is_available() returns False when backend is NONE."""
        stt = self._make_stt("none")
        self.assertFalse(stt.is_available())

    def test_1783_stt_get_backend(self):
        """get_backend() returns backend name string."""
        stt = self._make_stt("whisper_cpp")
        self.assertEqual(stt.get_backend(), "whisper_cpp")

    def test_1784_stt_has_microphone(self):
        """has_microphone() delegates to recorder."""
        stt = self._make_stt("whisper_cpp")
        stt._recorder.is_available.return_value = True
        self.assertTrue(stt.has_microphone())

    def test_1785_stt_cancel_word_positive(self):
        """is_cancel_word detects cancel words."""
        stt = self._make_stt()
        self.assertTrue(stt.is_cancel_word("отмена"))
        self.assertTrue(stt.is_cancel_word("  Отменить запрос  "))
        self.assertTrue(stt.is_cancel_word("cancel"))

    def test_1786_stt_cancel_word_negative(self):
        """is_cancel_word rejects non-cancel text."""
        stt = self._make_stt()
        self.assertFalse(stt.is_cancel_word("привет"))
        self.assertFalse(stt.is_cancel_word(""))

    def test_1787_stt_start_listening_no_backend(self):
        """start_listening returns False when not available."""
        stt = self._make_stt("none")
        self.assertFalse(stt.start_listening())

    def test_1788_stt_start_listening_success(self):
        """start_listening starts recording when available."""
        stt = self._make_stt("whisper_cpp")
        stt._recorder.start.return_value = True
        self.assertTrue(stt.start_listening())
        self.assertTrue(stt.is_listening())

    def test_1789_stt_stop_listening_returns_text(self):
        """stop_listening returns transcribed text."""
        stt = self._make_stt("whisper_cpp")
        stt._is_listening = True
        stt._recorder.stop.return_value = b"fake_wav"

        with patch.object(stt, '_transcribe_wav_bytes', return_value="привет мир"):
            text = stt.stop_listening()
        self.assertEqual(text, "привет мир")
        self.assertFalse(stt.is_listening())

    def test_1790_stt_stop_not_listening(self):
        """stop_listening returns empty string when not listening."""
        stt = self._make_stt()
        stt._is_listening = False
        self.assertEqual(stt.stop_listening(), "")

    def test_1791_stt_listen_for(self):
        """listen_for records N seconds and returns text."""
        stt = self._make_stt("whisper_cpp")
        stt._recorder.record_seconds.return_value = b"fake_wav"

        with patch.object(stt, '_transcribe_wav_bytes', return_value="тестовая фраза"):
            text = stt.listen_for(5.0)
        self.assertEqual(text, "тестовая фраза")

    def test_1792_stt_listen_for_no_backend(self):
        """listen_for returns empty string when not available."""
        stt = self._make_stt("none")
        self.assertEqual(stt.listen_for(5.0), "")

    def test_1793_stt_to_dict(self):
        """to_dict() contains expected keys."""
        stt = self._make_stt("whisper_cpp")
        d = stt.to_dict()
        self.assertIn("available", d)
        self.assertIn("backend", d)
        self.assertIn("has_microphone", d)
        self.assertIn("config", d)
        self.assertTrue(d["available"])

    def test_1794_stt_get_info(self):
        """get_info() returns human-readable string."""
        stt = self._make_stt("whisper_cpp")
        info = stt.get_info()
        self.assertIn("whisper_cpp", info)
        self.assertIn("STT", info)

    def test_1795_stt_last_text(self):
        """get_last_text() returns last recognized text."""
        stt = self._make_stt()
        stt._last_text = "последний текст"
        self.assertEqual(stt.get_last_text(), "последний текст")

    def test_1796_stt_callbacks(self):
        """set_on_text / set_on_partial store callbacks."""
        stt = self._make_stt()
        cb = MagicMock()
        stt.set_on_text(cb)
        self.assertEqual(stt._on_text, cb)
        stt.set_on_partial(cb)
        self.assertEqual(stt._on_partial, cb)

    def test_1797_stt_transcribe_nonexistent_file(self):
        """transcribe() returns empty string for missing file."""
        stt = self._make_stt("whisper_cpp")
        result = stt.transcribe("/no/such/file.wav")
        self.assertEqual(result, "")


# ═══════════════════════════════════════════════════════════════════════════════
#  Block C — TTS Configuration & AudioPlayer
# ═══════════════════════════════════════════════════════════════════════════════

class TestTTSConfig(unittest.TestCase):
    """TTSConfig defaults and serialization."""

    def test_1798_tts_config_defaults(self):
        """TTSConfig has sensible defaults."""
        from lina.voice.tts import TTSConfig
        cfg = TTSConfig()
        self.assertEqual(cfg.preferred_backend, "auto")
        self.assertEqual(cfg.language, "ru")
        self.assertAlmostEqual(cfg.speed, 1.0)
        self.assertAlmostEqual(cfg.volume, 1.0)
        self.assertTrue(cfg.streaming_enabled)

    def test_1799_tts_config_to_dict(self):
        """TTSConfig.to_dict() returns expected keys."""
        from lina.voice.tts import TTSConfig
        d = TTSConfig().to_dict()
        for key in ("preferred_backend", "language", "speed", "volume", "streaming_enabled"):
            self.assertIn(key, d)

    def test_1800_tts_backend_enum(self):
        """TTSBackend enum has expected values."""
        from lina.voice.tts import TTSBackend
        self.assertEqual(TTSBackend.PIPER.value, "piper")
        self.assertEqual(TTSBackend.ESPEAK.value, "espeak")
        self.assertEqual(TTSBackend.NONE.value, "none")

    def test_1801_voice_info_dataclass(self):
        """VoiceInfo stores voice metadata."""
        from lina.voice.tts import VoiceInfo
        vi = VoiceInfo(name="ru-default", language="ru", backend="piper")
        self.assertEqual(vi.name, "ru-default")
        self.assertEqual(vi.sample_rate, 22050)


class TestAudioPlayer(unittest.TestCase):
    """AudioPlayer: backend detection and playback control."""

    def _make_player(self, backend=None):
        from lina.voice.tts import AudioPlayer
        player = AudioPlayer.__new__(AudioPlayer)
        player._backend = backend
        player._process = None
        player._playing = False
        return player

    def test_1802_player_no_backend(self):
        """AudioPlayer with no backend is unavailable."""
        player = self._make_player(None)
        self.assertFalse(player.is_available())

    def test_1803_player_with_backend(self):
        """AudioPlayer with backend is available."""
        player = self._make_player("aplay")
        self.assertTrue(player.is_available())
        self.assertEqual(player.get_backend(), "aplay")

    def test_1804_player_play_no_backend(self):
        """play() returns False without backend."""
        player = self._make_player(None)
        self.assertFalse(player.play("/tmp/test.wav"))

    def test_1805_player_stop_not_playing(self):
        """stop() returns False when not playing."""
        player = self._make_player("aplay")
        self.assertFalse(player.stop())

    def test_1806_player_build_command_aplay(self):
        """_build_command produces correct aplay command."""
        player = self._make_player("aplay")
        cmd = player._build_command("/tmp/test.wav")
        self.assertEqual(cmd, ["aplay", "/tmp/test.wav"])

    def test_1807_player_build_command_ffplay(self):
        """_build_command produces correct ffplay command with flags."""
        player = self._make_player("ffplay")
        cmd = player._build_command("/tmp/test.wav")
        self.assertIn("-nodisp", cmd)
        self.assertIn("-autoexit", cmd)

    def test_1808_player_is_playing(self):
        """is_playing() reflects playback state."""
        player = self._make_player("aplay")
        self.assertFalse(player.is_playing())
        player._playing = True
        self.assertTrue(player.is_playing())


# ═══════════════════════════════════════════════════════════════════════════════
#  Block D — TextToSpeech
# ═══════════════════════════════════════════════════════════════════════════════

class TestTextToSpeech(unittest.TestCase):
    """TextToSpeech: backend detection, speak, streaming."""

    def _make_tts(self, backend="none"):
        from lina.voice.tts import TextToSpeech, TTSConfig, TTSBackend, AudioPlayer
        tts = TextToSpeech.__new__(TextToSpeech)
        tts.config = TTSConfig()
        tts._backend = TTSBackend(backend)
        tts._player = MagicMock(spec=AudioPlayer)
        tts._player.is_available.return_value = (backend != "none")
        tts._speaking = False
        tts._stop_requested = False
        tts._temp_files = []
        tts._on_start = None
        tts._on_done = None
        tts._on_sentence = None
        return tts

    def test_1809_tts_is_available_none(self):
        """is_available() returns False for NONE backend."""
        tts = self._make_tts("none")
        self.assertFalse(tts.is_available())

    def test_1810_tts_is_available_espeak(self):
        """is_available() returns True for espeak backend."""
        tts = self._make_tts("espeak")
        self.assertTrue(tts.is_available())

    def test_1811_tts_get_backend(self):
        """get_backend() returns backend name."""
        tts = self._make_tts("piper")
        self.assertEqual(tts.get_backend(), "piper")

    def test_1812_tts_set_speed(self):
        """set_speed() clamps to 0.5-2.0."""
        tts = self._make_tts("espeak")
        tts.set_speed(3.0)
        self.assertEqual(tts.config.speed, 2.0)
        tts.set_speed(0.1)
        self.assertEqual(tts.config.speed, 0.5)
        tts.set_speed(1.5)
        self.assertEqual(tts.config.speed, 1.5)

    def test_1813_tts_set_volume(self):
        """set_volume() clamps to 0.0-1.0."""
        tts = self._make_tts("espeak")
        tts.set_volume(1.5)
        self.assertEqual(tts.config.volume, 1.0)
        tts.set_volume(-0.5)
        self.assertEqual(tts.config.volume, 0.0)

    def test_1814_tts_set_voice_espeak(self):
        """set_voice() updates espeak voice."""
        tts = self._make_tts("espeak")
        tts.set_voice("en")
        self.assertEqual(tts.config.espeak_voice, "en")

    def test_1815_tts_speak_empty(self):
        """speak() returns False for empty text."""
        tts = self._make_tts("espeak")
        self.assertFalse(tts.speak(""))

    def test_1816_tts_speak_no_backend(self):
        """speak() returns False when not available."""
        tts = self._make_tts("none")
        self.assertFalse(tts.speak("привет"))

    def test_1817_tts_stop_not_speaking(self):
        """stop() returns False when not speaking."""
        tts = self._make_tts("espeak")
        self.assertFalse(tts.stop())

    def test_1818_tts_split_sentences(self):
        """_split_sentences breaks text on . ! ? and newlines."""
        from lina.voice.tts import TextToSpeech
        sentences = TextToSpeech._split_sentences("Привет. Как дела? Хорошо!")
        self.assertGreaterEqual(len(sentences), 3)
        self.assertEqual(sentences[0], "Привет.")
        self.assertEqual(sentences[1], "Как дела?")
        self.assertEqual(sentences[2], "Хорошо!")

    def test_1819_tts_split_sentences_newline(self):
        """_split_sentences handles newlines."""
        from lina.voice.tts import TextToSpeech
        sentences = TextToSpeech._split_sentences("Строка 1\nСтрока 2\nСтрока 3")
        self.assertEqual(len(sentences), 3)

    def test_1820_tts_to_dict(self):
        """to_dict() contains expected keys."""
        tts = self._make_tts("espeak")
        d = tts.to_dict()
        self.assertIn("available", d)
        self.assertIn("backend", d)
        self.assertIn("speed", d)
        self.assertIn("voices", d)
        self.assertTrue(d["available"])

    def test_1821_tts_get_info(self):
        """get_info() returns readable string."""
        tts = self._make_tts("espeak")
        info = tts.get_info()
        self.assertIn("espeak", info)
        self.assertIn("TTS", info)

    def test_1822_tts_callbacks_stored(self):
        """set_on_start/done/sentence store callbacks."""
        tts = self._make_tts()
        cb1, cb2, cb3 = MagicMock(), MagicMock(), MagicMock()
        tts.set_on_start(cb1)
        tts.set_on_done(cb2)
        tts.set_on_sentence(cb3)
        self.assertEqual(tts._on_start, cb1)
        self.assertEqual(tts._on_done, cb2)
        self.assertEqual(tts._on_sentence, cb3)

    def test_1823_tts_get_voices_espeak(self):
        """get_available_voices returns voices for espeak."""
        tts = self._make_tts("espeak")
        voices = tts.get_available_voices()
        self.assertGreater(len(voices), 0)
        self.assertEqual(voices[0].backend, "espeak")

    def test_1824_tts_cleanup_temp_files(self):
        """_cleanup_temp_files clears the list."""
        tts = self._make_tts()
        tts._temp_files = ["/tmp/nonexistent_lina_test.wav"]
        tts._cleanup_temp_files()
        self.assertEqual(len(tts._temp_files), 0)


# ═══════════════════════════════════════════════════════════════════════════════
#  Block E — VoicePipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoicePipeline(unittest.TestCase):
    """VoicePipeline: state machine, process_single, events."""

    def _make_pipeline(self):
        from lina.voice.pipeline import VoicePipeline, VoicePipelineConfig, VoicePipelineState
        vp = VoicePipeline.__new__(VoicePipeline)
        vp.config = VoicePipelineConfig()
        vp._state = VoicePipelineState.IDLE
        vp._mode = MagicMock()
        vp._running = False
        vp._session_active = False
        vp._lock = threading.Lock()
        vp._stt = None
        vp._tts = None
        vp._request_handler = None
        vp._on_state_change = None
        vp._on_event = None
        vp._on_text_recognized = None
        vp._on_response = None
        vp._events = deque(maxlen=1000)
        vp._conversation = deque(maxlen=200)
        vp._total_interactions = 0
        vp._errors_count = 0
        vp._session_thread = None
        return vp

    def test_1825_pipeline_initial_state(self):
        """Pipeline starts in IDLE state."""
        from lina.voice.pipeline import VoicePipelineState
        vp = self._make_pipeline()
        self.assertEqual(vp.get_state(), VoicePipelineState.IDLE)
        self.assertFalse(vp.is_active())

    def test_1826_pipeline_set_state(self):
        """_set_state transitions and logs event."""
        from lina.voice.pipeline import VoicePipelineState
        vp = self._make_pipeline()
        vp._set_state(VoicePipelineState.LISTENING)
        self.assertEqual(vp.get_state(), VoicePipelineState.LISTENING)
        self.assertEqual(len(vp._events), 1)
        self.assertEqual(vp._events[0].event_type, "state_change")

    def test_1827_pipeline_is_active(self):
        """is_active() is True for non-IDLE/ERROR states."""
        from lina.voice.pipeline import VoicePipelineState
        vp = self._make_pipeline()
        vp._state = VoicePipelineState.LISTENING
        self.assertTrue(vp.is_active())
        vp._state = VoicePipelineState.ERROR
        self.assertFalse(vp.is_active())

    def test_1828_pipeline_process_single_with_text(self):
        """process_single with user_text skips STT."""
        vp = self._make_pipeline()
        vp._request_handler = lambda t: f"Ответ: {t}"
        result = vp.process_single("привет")
        self.assertTrue(result["success"])
        self.assertEqual(result["recognized_text"], "привет")
        self.assertEqual(result["response_text"], "Ответ: привет")
        self.assertIsNone(result["error"])
        self.assertGreaterEqual(result["duration_ms"], 0)

    def test_1829_pipeline_process_single_interrupt(self):
        """process_single detects interrupt word."""
        vp = self._make_pipeline()
        result = vp.process_single("стоп")
        self.assertTrue(result["was_interrupted"])
        self.assertFalse(result["success"])

    def test_1830_pipeline_process_single_no_handler(self):
        """process_single returns error when no handler."""
        vp = self._make_pipeline()
        result = vp.process_single("привет")
        self.assertFalse(result["success"])
        self.assertIsNotNone(result["error"])

    def test_1831_pipeline_conversation_history(self):
        """Successful interaction adds to conversation history."""
        vp = self._make_pipeline()
        vp._request_handler = lambda t: "ок"
        vp.process_single("тест")
        history = vp.get_conversation()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["role"], "assistant")

    def test_1832_pipeline_clear_conversation(self):
        """clear_conversation empties history."""
        vp = self._make_pipeline()
        vp._conversation.append({"role": "user", "text": "test"})
        vp.clear_conversation()
        self.assertEqual(len(vp.get_conversation()), 0)

    def test_1833_pipeline_events_logged(self):
        """process_single logs text_recognized and response events."""
        vp = self._make_pipeline()
        vp._request_handler = lambda t: "hello"
        vp.process_single("hi")
        events = vp.get_events()
        types = [e["event_type"] for e in events]
        self.assertIn("text_recognized", types)
        self.assertIn("response", types)

    def test_1834_pipeline_get_stats(self):
        """get_stats() returns expected keys."""
        vp = self._make_pipeline()
        stats = vp.get_stats()
        self.assertIn("total_interactions", stats)
        self.assertIn("errors_count", stats)
        self.assertIn("session_active", stats)
        self.assertIn("current_state", stats)

    def test_1835_pipeline_to_dict(self):
        """to_dict() returns full pipeline state."""
        vp = self._make_pipeline()
        d = vp.to_dict()
        self.assertIn("state", d)
        self.assertIn("has_stt", d)
        self.assertIn("has_tts", d)
        self.assertIn("has_handler", d)
        self.assertFalse(d["has_stt"])

    def test_1836_pipeline_set_components(self):
        """set_stt/set_tts/set_request_handler wire components."""
        vp = self._make_pipeline()
        mock_stt = MagicMock()
        mock_stt.get_backend.return_value = "whisper_cpp"
        mock_tts = MagicMock()
        mock_tts.get_backend.return_value = "piper"
        handler = lambda t: "ok"

        vp.set_stt(mock_stt)
        vp.set_tts(mock_tts)
        vp.set_request_handler(handler)

        self.assertEqual(vp._stt, mock_stt)
        self.assertEqual(vp._tts, mock_tts)
        self.assertEqual(vp._request_handler, handler)

    def test_1837_pipeline_interrupt_word_detection(self):
        """_is_interrupt_word detects configured words."""
        vp = self._make_pipeline()
        self.assertTrue(vp._is_interrupt_word("стоп"))
        self.assertTrue(vp._is_interrupt_word("ХВАТИТ"))
        self.assertTrue(vp._is_interrupt_word("stop"))
        self.assertFalse(vp._is_interrupt_word("привет"))


# ═══════════════════════════════════════════════════════════════════════════════
#  Block F — Voice Module Integration & Factory
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoiceIntegration(unittest.TestCase):
    """Cross-module voice integration."""

    def test_1838_create_voice_pipeline_factory(self):
        """create_voice_pipeline() returns configured pipeline."""
        from lina.voice.pipeline import create_voice_pipeline
        pipeline = create_voice_pipeline()
        self.assertIsNotNone(pipeline)
        self.assertEqual(pipeline.get_state().value, "idle")

    def test_1839_voice_init_is_available(self):
        """voice.__init__.is_voice_available is callable."""
        from lina.voice import is_voice_available
        result = is_voice_available()
        self.assertIsInstance(result, bool)

    def test_1840_voice_init_get_status(self):
        """voice.__init__.get_voice_status returns dict with keys."""
        from lina.voice import get_voice_status
        status = get_voice_status()
        self.assertIn("stt_available", status)
        self.assertIn("tts_available", status)
        self.assertIn("stt_backend", status)
        self.assertIn("tts_backend", status)

    def test_1841_pipeline_config_defaults(self):
        """VoicePipelineConfig has expected defaults."""
        from lina.voice.pipeline import VoicePipelineConfig
        cfg = VoicePipelineConfig()
        self.assertEqual(cfg.mode, "push_to_talk")
        self.assertTrue(cfg.auto_listen_after_response)
        self.assertIn("стоп", cfg.interrupt_words)
        self.assertIsNone(cfg.wake_word)

    def test_1842_pipeline_config_to_dict(self):
        """VoicePipelineConfig.to_dict() serializes correctly."""
        from lina.voice.pipeline import VoicePipelineConfig
        d = VoicePipelineConfig().to_dict()
        self.assertIn("mode", d)
        self.assertIn("interrupt_words", d)

    def test_1843_voice_event_to_dict(self):
        """VoiceEvent.to_dict() returns expected keys."""
        from lina.voice.pipeline import VoiceEvent, VoicePipelineState
        event = VoiceEvent(
            event_type="test",
            state=VoicePipelineState.IDLE,
            text="hello",
        )
        d = event.to_dict()
        self.assertEqual(d["event_type"], "test")
        self.assertEqual(d["state"], "idle")
        self.assertEqual(d["text"], "hello")
        self.assertIn("timestamp", d)

    def test_1844_pipeline_tts_called_on_success(self):
        """process_single calls TTS speak on successful response."""
        vp = self._make_pipeline_full()
        mock_tts = MagicMock()
        mock_tts.speak = MagicMock()
        vp.set_tts(mock_tts)
        vp._request_handler = lambda t: "Ответ TTS"

        vp.process_single("тест")
        mock_tts.speak.assert_called_once_with("Ответ TTS")

    def test_1845_pipeline_process_cancel_word(self):
        """process_single detects cancel word via STT."""
        vp = self._make_pipeline_full()
        mock_stt = MagicMock()
        mock_stt.is_cancel_word = MagicMock(return_value=True)
        vp.set_stt(mock_stt)
        result = vp.process_single("отмена")
        self.assertTrue(result["was_cancelled"])

    def test_1846_pipeline_callbacks_called(self):
        """on_text_recognized and on_response callbacks are called."""
        vp = self._make_pipeline_full()
        vp._request_handler = lambda t: "resp"
        text_cb = MagicMock()
        resp_cb = MagicMock()
        vp.set_on_text_recognized(text_cb)
        vp.set_on_response(resp_cb)

        vp.process_single("hello")
        text_cb.assert_called_once_with("hello")
        resp_cb.assert_called_once_with("resp")

    def test_1847_pipeline_error_increments_count(self):
        """Error in processing increments _errors_count."""
        vp = self._make_pipeline_full()
        vp._request_handler = None  # will cause error
        vp.process_single("fail")
        self.assertGreater(vp._errors_count, 0)

    def test_1848_pipeline_state_change_callback(self):
        """State change callback fires on transitions."""
        from lina.voice.pipeline import VoicePipelineState
        vp = self._make_pipeline_full()
        vp._request_handler = lambda t: "ok"
        states = []
        vp.set_on_state_change(lambda s: states.append(s))
        vp.process_single("test")
        # Should have at least: THINKING → SPEAKING → IDLE
        state_vals = [s.value for s in states]
        self.assertIn("thinking", state_vals)
        self.assertIn("idle", state_vals)

    def _make_pipeline_full(self):
        """Helper: pipeline with all internal state."""
        from lina.voice.pipeline import VoicePipeline, VoicePipelineConfig, VoicePipelineState
        vp = VoicePipeline.__new__(VoicePipeline)
        vp.config = VoicePipelineConfig()
        vp._state = VoicePipelineState.IDLE
        vp._mode = MagicMock()
        vp._running = False
        vp._session_active = False
        vp._lock = threading.Lock()
        vp._stt = None
        vp._tts = None
        vp._request_handler = None
        vp._on_state_change = None
        vp._on_event = None
        vp._on_text_recognized = None
        vp._on_response = None
        vp._events = deque(maxlen=1000)
        vp._conversation = deque(maxlen=200)
        vp._total_interactions = 0
        vp._errors_count = 0
        vp._session_thread = None
        return vp


# ═══════════════════════════════════════════════════════════════════════════════
#  Block G — GUI ↔ Voice Wiring
# ═══════════════════════════════════════════════════════════════════════════════

class TestGUIVoiceWiring(unittest.TestCase):
    """Voice wiring in GUI app.py."""

    def test_1849_app_has_tts_response_hook(self):
        """app.py ChatController should have TTS response callback."""
        import inspect
        from lina.gui import chat
        src = inspect.getsource(chat.ChatController)
        self.assertIn("_on_tts_response", src,
                       "ChatController should have TTS callback")

    def test_1850_chat_controller_tts_callback_stored(self):
        """set_on_tts_response stores callback."""
        from lina.gui.chat import ChatController
        ctrl = ChatController()
        cb = MagicMock()
        ctrl.set_on_tts_response(cb)
        self.assertEqual(ctrl._on_tts_response, cb)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
