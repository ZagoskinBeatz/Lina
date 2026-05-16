"""
Lina — Менеджер аудиосистемы.

Диагностика и базовая настройка звука через PipeWire / PulseAudio / ALSA.
Все мутирующие операции генерируют команды, а не выполняют их.

Функциональность:
  - Определение аудиосервера (PipeWire/PulseAudio)
  - Список sink/source устройств
  - Текущая громкость и mute-статус
  - Диагностика "нет звука"
  - Генерация команд для настройки
"""

import subprocess
import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum

logger = logging.getLogger("lina.system.audio_manager")


# ─── Модели данных ─────────────────────────────────────────────────────────


class AudioServer(Enum):
    PIPEWIRE = "pipewire"
    PULSEAUDIO = "pulseaudio"
    ALSA_ONLY = "alsa"
    UNKNOWN = "unknown"


@dataclass
class AudioSink:
    """Устройство вывода звука."""
    index: int = 0
    name: str = ""
    description: str = ""
    driver: str = ""
    state: str = ""  # RUNNING, IDLE, SUSPENDED
    volume_percent: int = 0
    muted: bool = False
    is_default: bool = False


@dataclass
class AudioSource:
    """Устройство ввода звука (микрофон)."""
    index: int = 0
    name: str = ""
    description: str = ""
    driver: str = ""
    state: str = ""
    volume_percent: int = 0
    muted: bool = False
    is_default: bool = False


@dataclass
class AudioDiagResult:
    """Результат диагностики аудио."""
    ok: bool = True
    server: AudioServer = AudioServer.UNKNOWN
    server_running: bool = False
    sinks_found: int = 0
    default_sink: Optional[str] = None
    is_muted: bool = False
    volume_percent: int = 0
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


# ─── Утилиты ──────────────────────────────────────────────────────────────────


def _run(cmd: str, timeout: int = 5) -> str:
    """Выполняет команду, возвращает stdout."""
    try:
        r = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True,
            timeout=timeout,
        )
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _run_rc(cmd: str, timeout: int = 5) -> tuple:
    """Возвращает (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return -1, "", str(e)


# ─── Определение аудиосервера ──────────────────────────────────────────────


class AudioManager:
    """Управление и диагностика аудиосистемы Linux.

    Read операции выполняются напрямую.
    Мутирующие операции генерируют команды для показа пользователю.
    """

    def __init__(self):
        self._server: Optional[AudioServer] = None

    # ── Определение сервера ──

    def detect_server(self) -> AudioServer:
        """Определяет активный аудиосервер."""
        if self._server is not None:
            return self._server

        # PipeWire?
        rc, out, _ = _run_rc("pgrep -x pipewire")
        if rc == 0:
            # PipeWire может работать как замена PulseAudio
            rc2, out2, _ = _run_rc("pactl info 2>/dev/null | grep 'Server Name'")
            if "PipeWire" in out2:
                self._server = AudioServer.PIPEWIRE
                return self._server
            self._server = AudioServer.PIPEWIRE
            return self._server

        # PulseAudio?
        rc, out, _ = _run_rc("pgrep -x pulseaudio")
        if rc == 0:
            self._server = AudioServer.PULSEAUDIO
            return self._server

        # Только ALSA?
        rc, out, _ = _run_rc("aplay -l 2>/dev/null")
        if rc == 0 and out:
            self._server = AudioServer.ALSA_ONLY
            return self._server

        self._server = AudioServer.UNKNOWN
        return self._server

    # ── Sinks (устройства вывода) ──

    def list_sinks(self) -> List[AudioSink]:
        """Список устройств вывода звука."""
        sinks: List[AudioSink] = []

        # Получаем default sink
        default_name = _run("pactl get-default-sink 2>/dev/null")

        out = _run("pactl list sinks short 2>/dev/null")
        if not out:
            return sinks

        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                sink = AudioSink(
                    index=int(parts[0]) if parts[0].isdigit() else 0,
                    name=parts[1],
                    driver=parts[2] if len(parts) > 2 else "",
                    state=parts[4] if len(parts) > 4 else "",
                    is_default=(parts[1] == default_name),
                )
                sinks.append(sink)

        # Получаем громкость для каждого sink
        for sink in sinks:
            vol_out = _run(
                f"pactl get-sink-volume {sink.index} 2>/dev/null"
            )
            m = re.search(r'(\d+)%', vol_out)
            if m:
                sink.volume_percent = int(m.group(1))

            mute_out = _run(
                f"pactl get-sink-mute {sink.index} 2>/dev/null"
            )
            sink.muted = "yes" in mute_out.lower()

        return sinks

    def list_sources(self) -> List[AudioSource]:
        """Список устройств ввода (микрофоны)."""
        sources: List[AudioSource] = []

        default_name = _run("pactl get-default-source 2>/dev/null")

        out = _run("pactl list sources short 2>/dev/null")
        if not out:
            return sources

        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                # Пропускаем monitor-устройства (loopback)
                if ".monitor" in parts[1]:
                    continue
                source = AudioSource(
                    index=int(parts[0]) if parts[0].isdigit() else 0,
                    name=parts[1],
                    driver=parts[2] if len(parts) > 2 else "",
                    state=parts[4] if len(parts) > 4 else "",
                    is_default=(parts[1] == default_name),
                )
                sources.append(source)

        return sources

    # ── Громкость (read-only) ──

    def get_default_sink(self) -> Optional[AudioSink]:
        """Текущее устройство вывода по умолчанию."""
        sinks = self.list_sinks()
        for s in sinks:
            if s.is_default:
                return s
        return sinks[0] if sinks else None

    def get_volume(self) -> int:
        """Текущая громкость (%) устройства по умолчанию."""
        sink = self.get_default_sink()
        return sink.volume_percent if sink else 0

    def is_muted(self) -> bool:
        """Замьючен ли звук."""
        sink = self.get_default_sink()
        return sink.muted if sink else False

    # ── Команды (генерация, не выполнение) ──

    def set_volume_cmd(self, percent: int) -> str:
        """Генерирует команду установки громкости."""
        percent = max(0, min(percent, 150))
        return f"pactl set-sink-volume @DEFAULT_SINK@ {percent}%"

    def volume_up_cmd(self, step: int = 10) -> str:
        """Генерирует команду увеличения громкости."""
        return f"pactl set-sink-volume @DEFAULT_SINK@ +{step}%"

    def volume_down_cmd(self, step: int = 10) -> str:
        """Генерирует команду уменьшения громкости."""
        return f"pactl set-sink-volume @DEFAULT_SINK@ -{step}%"

    def mute_toggle_cmd(self) -> str:
        """Генерирует команду переключения mute."""
        return "pactl set-sink-mute @DEFAULT_SINK@ toggle"

    def set_default_sink_cmd(self, sink_name: str) -> str:
        """Генерирует команду смены устройства вывода."""
        return f"pactl set-default-sink {sink_name}"

    # ── Диагностика "нет звука" ──

    def diagnose_no_sound(self) -> AudioDiagResult:
        """Пошаговая диагностика отсутствия звука.

        Проверяет:
          1. Аудиосервер запущен?
          2. Есть ли sink-устройства?
          3. Не замьючено ли?
          4. Правильное устройство по умолчанию?
          5. Работают ALSA-модули?
          6. Конфликт PipeWire/PulseAudio?
        """
        result = AudioDiagResult()
        result.server = self.detect_server()

        # 1. Аудиосервер запущен?
        if result.server == AudioServer.UNKNOWN:
            result.ok = False
            result.server_running = False
            result.issues.append("Аудиосервер не обнаружен (ни PipeWire, ни PulseAudio)")
            result.suggestions.append("Установите PipeWire: sudo pacman -S pipewire pipewire-pulse wireplumber")
            return result

        result.server_running = True

        # 2. Есть ли sinks?
        sinks = self.list_sinks()
        result.sinks_found = len(sinks)
        if not sinks:
            result.ok = False
            result.issues.append("Нет доступных устройств вывода звука (sinks)")
            result.suggestions.append("Перезапустите аудиосервер: systemctl --user restart pipewire wireplumber")
            result.suggestions.append("Проверьте ALSA: aplay -l")
            return result

        # 3. Замьючено?
        default = self.get_default_sink()
        if default:
            result.default_sink = default.description or default.name
            result.volume_percent = default.volume_percent
            result.is_muted = default.muted

            if default.muted:
                result.ok = False
                result.issues.append("Звук замьючен (mute)")
                result.suggestions.append(f"Снять mute: pactl set-sink-mute @DEFAULT_SINK@ 0")

            if default.volume_percent == 0:
                result.ok = False
                result.issues.append("Громкость на 0%")
                result.suggestions.append("Увеличить громкость: pactl set-sink-volume @DEFAULT_SINK@ 50%")

        # 4. Несколько sinks — возможно, выбран неправильный
        if len(sinks) > 1:
            sink_names = [s.description or s.name for s in sinks]
            result.issues.append(
                f"Найдено {len(sinks)} устройств вывода: {', '.join(sink_names)}. "
                "Возможно, выбрано неправильное."
            )
            for s in sinks:
                if not s.is_default:
                    result.suggestions.append(
                        f"Сменить на '{s.description or s.name}': "
                        f"pactl set-default-sink {s.name}"
                    )

        # 5. ALSA-модули
        rc, alsa_out, _ = _run_rc("aplay -l 2>/dev/null")
        if rc != 0 or not alsa_out:
            result.ok = False
            result.issues.append("ALSA не обнаруживает звуковых карт")
            result.suggestions.append("Проверьте модули ядра: lsmod | grep snd")
            result.suggestions.append("Загрузите модуль: sudo modprobe snd-hda-intel")

        # 6. Конфликт PipeWire/PulseAudio
        pw_rc, _, _ = _run_rc("pgrep -x pipewire")
        pa_rc, _, _ = _run_rc("pgrep -x pulseaudio")
        if pw_rc == 0 and pa_rc == 0:
            result.ok = False
            result.issues.append("Одновременно запущены PipeWire и PulseAudio — конфликт!")
            result.suggestions.append("Остановите PulseAudio: systemctl --user stop pulseaudio.service pulseaudio.socket")
            result.suggestions.append("Маскируйте: systemctl --user mask pulseaudio.service pulseaudio.socket")

        if not result.issues:
            result.ok = True
            result.issues.append("Проблем не обнаружено — звук должен работать")

        return result

    # ── Форматирование ──

    def format_status(self) -> str:
        """Человекочитаемый статус аудиосистемы."""
        server = self.detect_server()
        lines = [f"Аудиосервер: {server.value}"]

        sinks = self.list_sinks()
        if sinks:
            lines.append(f"Устройства вывода: {len(sinks)}")
            for s in sinks:
                marker = " (по умолчанию)" if s.is_default else ""
                mute = " [MUTE]" if s.muted else ""
                lines.append(
                    f"  {s.index}: {s.description or s.name} "
                    f"— {s.volume_percent}%{mute}{marker}"
                )
        else:
            lines.append("Устройства вывода: нет")

        sources = self.list_sources()
        if sources:
            lines.append(f"Микрофоны: {len(sources)}")
            for s in sources:
                marker = " (по умолчанию)" if s.is_default else ""
                lines.append(f"  {s.index}: {s.description or s.name}{marker}")

        return "\n".join(lines)

    def format_diagnosis(self) -> str:
        """Форматирует полную диагностику для пользователя."""
        diag = self.diagnose_no_sound()
        lines = ["═══ Диагностика аудио ═══"]
        lines.append(f"Сервер: {diag.server.value} ({'работает' if diag.server_running else 'не запущен'})")
        lines.append(f"Устройств вывода: {diag.sinks_found}")

        if diag.default_sink:
            mute_str = " [MUTE]" if diag.is_muted else ""
            lines.append(f"По умолчанию: {diag.default_sink} ({diag.volume_percent}%{mute_str})")

        if diag.issues:
            lines.append("\nПроблемы:")
            for issue in diag.issues:
                lines.append(f"  ✗ {issue}")

        if diag.suggestions:
            lines.append("\nРекомендации:")
            for sug in diag.suggestions:
                lines.append(f"  → {sug}")

        status = "✅ OK" if diag.ok else "❌ Проблемы обнаружены"
        lines.append(f"\nИтог: {status}")
        return "\n".join(lines)
