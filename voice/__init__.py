"""
Lina Voice — голосовой ввод/вывод.

Модули:
  - stt.py       — Speech-to-Text (whisper.cpp)
  - tts.py       — Text-to-Speech (piper / espeak-ng)
  - pipeline.py  — голосовой pipeline (STT → Lina → TTS)
"""

from typing import Optional


def is_voice_available() -> bool:
    """Проверяет, доступен ли хотя бы один компонент голоса."""
    try:
        from lina.voice.stt import SpeechToText
        from lina.voice.tts import TextToSpeech
        stt = SpeechToText()
        tts = TextToSpeech()
        return stt.is_available() or tts.is_available()
    except Exception:
        return False


def get_voice_status() -> dict:
    """Статус голосовых компонентов."""
    result = {"stt_available": False, "tts_available": False,
              "stt_backend": None, "tts_backend": None}
    try:
        from lina.voice.stt import SpeechToText
        stt = SpeechToText()
        result["stt_available"] = stt.is_available()
        result["stt_backend"] = stt.get_backend()
    except Exception:
        pass
    try:
        from lina.voice.tts import TextToSpeech
        tts = TextToSpeech()
        result["tts_available"] = tts.is_available()
        result["tts_backend"] = tts.get_backend()
    except Exception:
        pass
    return result
