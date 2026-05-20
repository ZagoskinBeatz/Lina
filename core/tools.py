"""
Lina — Tool Definitions для function-calling.

Определения всех инструментов, которые мини-модель может вызывать.
Каждый инструмент — это функция с описанием, параметрами и реализацией.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("lina.core.tools")

# Активные таймеры (для отмены)
_active_timers: Dict[str, threading.Event] = {}
MAX_CONCURRENT_TIMERS = 10
_timer_counter_lock = threading.Lock()
_timer_counter = 0

# Validate numeric input for brightness/volume (digits with optional leading +/-)
_NUMERIC_VALUE_RE = re.compile(r"^[+-]?\d{1,3}$")


# ═══════════════════════════════════════════════════════════════════════════════
#  Tool Result
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolResult:
    """Результат выполнения инструмента."""
    success: bool = True
    output: str = ""
    error: str = ""
    needs_full_llm: bool = False  # Если True — передать в тяжёлую модель


# ═══════════════════════════════════════════════════════════════════════════════
#  Tool Registry — реестр всех доступных инструментов
# ═══════════════════════════════════════════════════════════════════════════════

class ToolRegistry:
    """
    Реестр инструментов для function-calling.

    Хранит описания инструментов (для промпта) и их реализации.
    """

    def __init__(self):
        self._tools: Dict[str, dict] = {}
        self._handlers: Dict[str, Callable] = {}
        self._register_builtins()

    def register(self, name: str, description: str,
                 parameters: Dict[str, Any], handler: Callable):
        """Регистрирует инструмент."""
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
        }
        self._handlers[name] = handler

    def get_tools_prompt(self) -> str:
        """Генерирует описание всех инструментов для системного промпта."""
        lines = ["Доступные функции (tools):"]
        for name, info in self._tools.items():
            params_desc = []
            for pname, pinfo in info["parameters"].items():
                req = " (обязательный)" if pinfo.get("required") else ""
                params_desc.append(f"    - {pname}: {pinfo['type']} — {pinfo['description']}{req}")
            params_str = "\n".join(params_desc) if params_desc else "    (без параметров)"
            lines.append(f"\n• {name}: {info['description']}\n  Параметры:\n{params_str}")
        return "\n".join(lines)

    def execute(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        """Выполняет инструмент по имени."""
        handler = self._handlers.get(name)
        if not handler:
            return ToolResult(success=False, error=f"Неизвестный инструмент: {name}")
        try:
            return handler(**arguments)
        except Exception as e:
            logger.error("Tool %s failed: %s", name, e)
            return ToolResult(success=False, error=str(e))

    @property
    def tool_names(self) -> List[str]:
        return list(self._tools.keys())

    # ── Регистрация встроенных инструментов ──

    def _register_builtins(self):
        """Регистрирует все встроенные инструменты."""

        # ─── Ответ пользователю (для приветствий, вопросов и т.д.) ───
        self.register(
            name="respond",
            description="Отправить текстовый ответ пользователю. Используй для приветствий, ответов на вопросы, разговоров.",
            parameters={
                "message": {"type": "string", "description": "Текст ответа", "required": True},
            },
            handler=self._tool_respond,
        )

        # ─── Дата / Время (быстрый ответ без LLM) ───
        self.register(
            name="get_datetime",
            description="Узнать текущую дату и/или время. Используй когда спрашивают 'который час', 'сколько времени', 'какая дата', 'какое число'.",
            parameters={
                "query": {"type": "string", "description": "Запрос: 'время', 'дата', или пусто для обоих", "required": False},
            },
            handler=self._tool_datetime,
        )

        # ─── Яркость ───
        self.register(
            name="set_brightness",
            description="Установить яркость экрана. Можно задать абсолютное значение (%) или изменить относительно (+10%, -10%).",
            parameters={
                "value": {"type": "string", "description": "Значение: '50%', '+10%', '-10%', '100%'", "required": True},
            },
            handler=self._tool_brightness,
        )

        # ─── Громкость ───
        self.register(
            name="set_volume",
            description="Установить громкость звука. Можно задать абсолютное значение (%) или изменить (+10%, -10%). Можно также mute/unmute.",
            parameters={
                "value": {"type": "string", "description": "Значение: '50%', '+10%', '-10%', 'mute', 'unmute'", "required": True},
            },
            handler=self._tool_volume,
        )

        # ─── Запуск приложений (универсальный поиск + верификация PID) ───
        self.register(
            name="open_app",
            description="Открыть (запустить) любое приложение. Ищет везде: .desktop, Flatpak, Snap, AppImage, PATH. Проверяет запуск. Если не установлено — подскажет как установить.",
            parameters={
                "app_name": {"type": "string", "description": "Название приложения на любом языке (хром, firefox, терминал, steam, happ, wireguard и т.д.)", "required": True},
            },
            handler=self._tool_open_app,
        )

        # ─── Выполнение shell-команды ───
        self.register(
            name="run_shell",
            description="Выполнить shell-команду в терминале. Используй для системных запросов: информация о системе, процессы, диски, сеть, пакеты и т.д.",
            parameters={
                "command": {"type": "string", "description": "Shell-команда для выполнения", "required": True},
            },
            handler=self._tool_shell,
        )

        # ─── Системная информация ───
        self.register(
            name="system_info",
            description="Получить информацию о системе: ОС, ядро, CPU, RAM, диски, сеть, процессы. Укажи категорию.",
            parameters={
                "category": {"type": "string", "description": "Категория: 'os', 'cpu', 'ram', 'disk', 'network', 'processes', 'all'", "required": True},
            },
            handler=self._tool_system_info,
        )

        # ─── Передать тяжёлой модели ───
        self.register(
            name="ask_full_llm",
            description="Передать вопрос тяжёлой LLM-модели для сложных задач: программирование, анализ, создание текстов, объяснения, код.",
            parameters={
                "query": {"type": "string", "description": "Вопрос для тяжёлой модели", "required": True},
            },
            handler=self._tool_full_llm,
        )

        # ─── Скриншот ───
        self.register(
            name="screenshot",
            description="Сделать снимок экрана. Можно снять весь экран, активное окно или выбрать область.",
            parameters={
                "mode": {"type": "string", "description": "Режим: 'full' (весь экран), 'window' (активное окно), 'region' (выбрать область). По умолчанию 'full'.", "required": False},
            },
            handler=self._tool_screenshot,
        )

        # ─── Управление медиа ───
        self.register(
            name="media_control",
            description="Управление воспроизведением музыки/видео: play, pause, next, previous, stop, toggle (пауза/играть).",
            parameters={
                "action": {"type": "string", "description": "Действие: 'play', 'pause', 'toggle', 'next', 'previous', 'stop', 'status'", "required": True},
            },
            handler=self._tool_media_control,
        )

        # ─── Таймер ───
        self.register(
            name="set_timer",
            description="Установить таймер с уведомлением. Через N секунд/минут покажет уведомление.",
            parameters={
                "seconds": {"type": "integer", "description": "Время в секундах (60 = 1 минута, 300 = 5 минут)", "required": True},
                "message": {"type": "string", "description": "Текст уведомления (например: 'Время вышло!')", "required": False},
            },
            handler=self._tool_timer,
        )

        # ─── Отменить таймер ───
        self.register(
            name="cancel_timer",
            description="Отменить активный таймер по его ID.",
            parameters={
                "timer_id": {"type": "string", "description": "ID таймера (из list_timers)", "required": True},
            },
            handler=self._tool_cancel_timer,
        )

        # ─── Список таймеров ───
        self.register(
            name="list_timers",
            description="Показать все активные таймеры.",
            parameters={},
            handler=self._tool_list_timers,
        )

        # ─── Управление питанием ───
        self.register(
            name="power_control",
            description="Управление питанием: заблокировать экран, спящий режим, перезагрузка, выключение.",
            parameters={
                "action": {"type": "string", "description": "Действие: 'lock', 'sleep', 'reboot', 'shutdown'", "required": True},
            },
            handler=self._tool_power_control,
        )

        # ─── WiFi ───
        self.register(
            name="toggle_wifi",
            description="Включить или выключить WiFi.",
            parameters={
                "state": {"type": "string", "description": "Состояние: 'on', 'off', 'status'", "required": True},
            },
            handler=self._tool_wifi,
        )

        # ─── Bluetooth ───
        self.register(
            name="toggle_bluetooth",
            description="Включить или выключить Bluetooth.",
            parameters={
                "state": {"type": "string", "description": "Состояние: 'on', 'off', 'status'", "required": True},
            },
            handler=self._tool_bluetooth,
        )

        # ─── Ночной режим (синий фильтр) ───
        self.register(
            name="night_mode",
            description="Ночной режим (тёплый свет, фильтр синего). Включить, выключить или задать температуру.",
            parameters={
                "state": {"type": "string", "description": "Состояние: 'on', 'off', 'toggle', 'status'. Для определённой температуры: '3500' (тёплый), '4500' (нейтральный).", "required": True},
            },
            handler=self._tool_night_mode,
        )

        # ─── Буфер обмена ───
        self.register(
            name="clipboard",
            description="Работа с буфером обмена: прочитать или записать текст.",
            parameters={
                "action": {"type": "string", "description": "Действие: 'get' (прочитать), 'set' (записать)", "required": True},
                "text": {"type": "string", "description": "Текст для записи в буфер (при action='set')", "required": False},
            },
            handler=self._tool_clipboard,
        )

        # ─── Уведомление ───
        self.register(
            name="send_notification",
            description="Отправить уведомление на рабочий стол (всплывающее сообщение).",
            parameters={
                "title": {"type": "string", "description": "Заголовок уведомления", "required": True},
                "message": {"type": "string", "description": "Текст уведомления", "required": False},
            },
            handler=self._tool_notification,
        )

        # ─── Убить процесс ───
        self.register(
            name="kill_process",
            description="Завершить (убить) процесс по имени. Используй для закрытия зависших программ.",
            parameters={
                "name": {"type": "string", "description": "Имя процесса (firefox, chrome, steam и т.д.)", "required": True},
                "force": {"type": "boolean", "description": "Принудительно (SIGKILL). По умолчанию False (SIGTERM).", "required": False},
            },
            handler=self._tool_kill_process,
        )

        # ─── Открыть URL ───
        self.register(
            name="open_url",
            description="Открыть веб-сайт (URL) в браузере.",
            parameters={
                "url": {"type": "string", "description": "URL сайта (например: https://youtube.com)", "required": True},
            },
            handler=self._tool_open_url,
        )

        # ─── Поиск файлов ───
        self.register(
            name="find_file",
            description="Найти файл или папку на компьютере по имени или части имени.",
            parameters={
                "pattern": {"type": "string", "description": "Имя или часть имени файла (например: '*.pdf', 'report', 'фото.jpg')", "required": True},
                "directory": {"type": "string", "description": "Где искать (по умолчанию домашняя папка ~)", "required": False},
            },
            handler=self._tool_find_file,
        )

        # ─── Погода ───
        self.register(
            name="weather",
            description="Узнать текущую погоду в городе.",
            parameters={
                "city": {"type": "string", "description": "Город (например: 'Москва', 'Moscow', 'Краснодар')", "required": False},
            },
            handler=self._tool_weather,
        )

        # ─── Веб-поиск ───
        self.register(
            name="web_search",
            description="Поиск информации в интернете через DuckDuckGo.",
            parameters={
                "query": {"type": "string", "description": "Поисковый запрос", "required": True},
            },
            handler=self._tool_web_search,
        )

        # ─── Найти сайт и открыть в браузере ───
        # Это «комплекс» из web_search + open_url: модель вызывает один раз,
        # инструмент находит топ-релевантную ссылку и открывает её в браузере.
        # Для запросов вроде «открой страницу Steam на Arch Wiki».
        self.register(
            name="find_and_open_site",
            description=(
                "Найти сайт/страницу через веб-поиск и открыть её в браузере. "
                "Используй когда пользователь просит «открой страницу X», "
                "«найди и открой Y», «зайди на сайт Z», и точный URL "
                "неизвестен."
            ),
            parameters={
                "query": {
                    "type": "string",
                    "description": (
                        "Что искать. Например: 'GIMP официальный сайт', "
                        "'Anthropic Claude Code GitHub releases', "
                        "'Arch Wiki Steam'."
                    ),
                    "required": True,
                },
            },
            handler=self._tool_find_and_open_site,
        )

        # ─── Установка приложений ───
        self.register(
            name="install_app",
            description="Подсказать как установить приложение, которое не найдено в системе. Ищет в pacman/apt/dnf/flatpak/snap и формирует инструкцию.",
            parameters={
                "app_name": {"type": "string", "description": "Название приложения (например: 'telegram', 'obs', 'gimp')", "required": True},
            },
            handler=self._tool_install_app,
        )

        # ─── Интерактивная консоль ───
        self.register(
            name="run_in_console",
            description="Открыть терминал и выполнить команду в нём (пользователь видит процесс).",
            parameters={
                "command": {"type": "string", "description": "Команда для выполнения (например: 'sudo apt update', 'ip addr')", "required": True},
            },
            handler=self._tool_run_in_console,
        )

        # ═══════════════════════════════════════════════════════════
        #  PROBLEM TERMINATOR — диагностика и починка
        # ═══════════════════════════════════════════════════════════

        # ─── Диагностика проблемы ───
        self.register(
            name="diagnose_problem",
            description="Полная диагностика проблемы: сканирование системы, анализ логов, классификация ошибки, подбор решения. Используй когда что-то не работает (интернет, звук, видео, bluetooth и т.д.).",
            parameters={
                "problem": {"type": "string", "description": "Описание проблемы (например: 'не работает звук', 'интернет пропал', 'wifi не подключается')", "required": True},
            },
            handler=self._tool_diagnose_problem,
        )

        # ─── Здоровье системы ───
        self.register(
            name="system_health",
            description="Быстрая проверка здоровья системы: CPU, RAM, диск, температуры, сеть, сервисы, обновления. Покажет проблемные области.",
            parameters={},
            handler=self._tool_system_health,
        )

        # ─── Починка проблемы ───
        self.register(
            name="fix_problem",
            description="Попытаться автоматически починить обнаруженную проблему. Сначала диагностирует, потом предлагает или выполняет fix. Режимы: safe (только отчёт), assist (предложить и подтвердить), auto (автоматически для LOW/MEDIUM).",
            parameters={
                "problem": {"type": "string", "description": "Описание проблемы", "required": True},
                "mode": {"type": "string", "description": "Режим: 'safe' (отчёт), 'assist' (подтверждение), 'auto' (автоматически). По умолчанию 'safe'.", "required": False},
            },
            handler=self._tool_fix_problem,
        )

        # ─── Обзор управления системой ───
        self.register(
            name="system_overview",
            description="Полный обзор всех подсистем Linux: сеть, аудио, GPU, bluetooth, сервисы, питание, DNS, ядро.",
            parameters={},
            handler=self._tool_system_overview,
        )

        # ═══════════════════════════════════════════════════════════
        #  SYSTEM OVERLORD — расширенная автономная защита
        # ═══════════════════════════════════════════════════════════

        # ─── Предиктивный отчёт ───
        self.register(
            name="predictive_report",
            description="Предиктивный анализ: тренды CPU/RAM/диска/температур, прогноз проблем, аномалии. Показывает что СКОРО сломается.",
            parameters={},
            handler=self._tool_predictive_report,
        )

        # ─── Проверка дрифта ───
        self.register(
            name="drift_check",
            description="Проверка дрифта конфигурации: изменились ли ядро, драйверы, DNS, GPU, аудио, сеть с момента baseline. Обнаружит неожиданные изменения системы.",
            parameters={},
            handler=self._tool_drift_check,
        )

        # ─── Оценка риска команды ───
        self.register(
            name="risk_assess",
            description="Оценить уровень риска команды или плана действий перед выполнением. Показывает факторы: деструктивность, привилегии, зависимости.",
            parameters={
                "command": {"type": "string", "description": "Команда для оценки", "required": True},
            },
            handler=self._tool_risk_assess,
        )

        # ─── Целостность системы ───
        self.register(
            name="integrity_check",
            description="Проверка целостности модулей Lina и конфигов: обнаружение изменений, пропавших файлов, потенциального вмешательства.",
            parameters={},
            handler=self._tool_integrity_check,
        )

        # ─── Статус самовосстановления ───
        self.register(
            name="healer_status",
            description="Статус системы самовосстановления OVERLORD: текущий режим (NORMAL/DEGRADED/SAFE), blocked recipes, failure streak.",
            parameters={},
            handler=self._tool_healer_status,
        )

        # ─── Веб-разведка для решения проблемы ───
        self.register(
            name="web_solution",
            description="Найти решение проблемы в интернете (ArchWiki, StackOverflow и т.д.), извлечь безопасные команды, проверить совместимость с системой.",
            parameters={
                "error": {"type": "string", "description": "Текст ошибки или описание проблемы", "required": True},
                "category": {"type": "string", "description": "Категория (network, audio, gpu и т.д.)", "required": False},
            },
            handler=self._tool_web_solution,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    #  Реализации инструментов
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _tool_datetime(query: str = "") -> ToolResult:
        """Быстрый ответ на вопросы о дате/времени без LLM."""
        from datetime import datetime
        # Russian day/month names (avoid process-global locale.setlocale)
        _DAYS_RU = {
            0: "понедельник", 1: "вторник", 2: "среда",
            3: "четверг", 4: "пятница", 5: "суббота", 6: "воскресенье",
        }
        _MONTHS_RU = {
            1: "января", 2: "февраля", 3: "марта", 4: "апреля",
            5: "мая", 6: "июня", 7: "июля", 8: "августа",
            9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
        }
        now = datetime.now()
        day_name = _DAYS_RU[now.weekday()]
        month_name = _MONTHS_RU[now.month]
        q = query.lower().strip()
        if any(w in q for w in ("час", "время", "time")):
            return ToolResult(output=f"🕐 Сейчас {now.strftime('%H:%M:%S')}")
        if any(w in q for w in ("дат", "числ", "date", "день")):
            return ToolResult(
                output=f"📅 Сегодня {day_name}, {now.day} {month_name} {now.year} г."
            )
        return ToolResult(
            output=f"🕐 {now.strftime('%H:%M:%S')} | 📅 {now.strftime('%d.%m.%Y')}, {day_name}"
        )

    @staticmethod
    def _tool_respond(message: str) -> ToolResult:
        return ToolResult(output=message)

    @staticmethod
    def _tool_brightness(value: str) -> ToolResult:
        if not shutil.which("brightnessctl"):
            return ToolResult(success=False, error="brightnessctl не установлен")
        val = value.strip().rstrip("%")
        if not _NUMERIC_VALUE_RE.match(val):
            return ToolResult(success=False, error=f"Неверное значение яркости: {value}")
        # brightnessctl uses N%+ / N%- for relative, N% for absolute
        if val.startswith("+"):
            arg = f"{val[1:]}%+"
        elif val.startswith("-"):
            arg = f"{val[1:]}%-"
        else:
            # v0.8.0: clamp absolute brightness to 0-100
            num = int(val)
            if num < 0 or num > 100:
                return ToolResult(success=False, error=f"Яркость должна быть 0-100, получено: {num}")
            arg = f"{num}%"
        try:
            r = subprocess.run(
                ["brightnessctl", "set", arg],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                return ToolResult(output=f"Яркость установлена: {value}")
            return ToolResult(success=False, error=r.stderr.strip())
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _tool_volume(value: str) -> ToolResult:
        if not shutil.which("pactl"):
            return ToolResult(success=False, error="pactl не установлен")
        val = value.strip().lower()
        if val == "mute":
            cmd = ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"]
        elif val == "unmute":
            cmd = ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"]
        else:
            val = val.rstrip("%")
            if not _NUMERIC_VALUE_RE.match(val):
                return ToolResult(success=False, error=f"Неверное значение громкости: {value}")
            cmd = ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{val}%"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return ToolResult(output=f"Громкость: {value}")
            return ToolResult(success=False, error=r.stderr.strip())
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    # ─── Известные сайты для fallback open_app → open_url ───
    _SITE_MAP = {
        # AI / Chat
        "дипсик": "https://chat.deepseek.com",
        "deepseek": "https://chat.deepseek.com",
        "chatgpt": "https://chat.openai.com",
        "чатгпт": "https://chat.openai.com",
        "claude": "https://claude.ai",
        "клод": "https://claude.ai",
        "copilot": "https://copilot.microsoft.com",
        "gemini": "https://gemini.google.com",
        "джемини": "https://gemini.google.com",
        # Dev
        "github": "https://github.com",
        "гитхаб": "https://github.com",
        "gitlab": "https://gitlab.com",
        "stackoverflow": "https://stackoverflow.com",
        # Social
        "youtube": "https://youtube.com",
        "ютуб": "https://youtube.com",
        "вк": "https://vk.com",
        "vk": "https://vk.com",
        "телеграм": "https://web.telegram.org",
        "telegram": "https://web.telegram.org",
        "вотсап": "https://web.whatsapp.com",
        "whatsapp": "https://web.whatsapp.com",
        "твич": "https://twitch.tv",
        "twitch": "https://twitch.tv",
        "редит": "https://reddit.com",
        "reddit": "https://reddit.com",
        "инстаграм": "https://instagram.com",
        "instagram": "https://instagram.com",
        "тикток": "https://tiktok.com",
        "tiktok": "https://tiktok.com",
        "twitter": "https://x.com",
        "твиттер": "https://x.com",
        "x": "https://x.com",
        "одноклассники": "https://ok.ru",
        "ok": "https://ok.ru",
        "discord": "https://discord.com",
        "дискорд": "https://discord.com",
        # Music / Media
        "spotify": "https://open.spotify.com",
        "спотифай": "https://open.spotify.com",
        "яндекс музыка": "https://music.yandex.ru",
        "netflix": "https://netflix.com",
        "нетфликс": "https://netflix.com",
        "кинопоиск": "https://kinopoisk.ru",
        "kinopoisk": "https://kinopoisk.ru",
        # Services
        "gmail": "https://mail.google.com",
        "гмейл": "https://mail.google.com",
        "яндекс почта": "https://mail.yandex.ru",
        "google drive": "https://drive.google.com",
        "гугл диск": "https://drive.google.com",
        "notion": "https://notion.so",
        "figma": "https://figma.com",
        "canva": "https://canva.com",
        "google": "https://google.com",
        "гугл": "https://google.com",
        "яндекс": "https://ya.ru",
        "yandex": "https://ya.ru",
        "wildberries": "https://wildberries.ru",
        "вайлдберриз": "https://wildberries.ru",
        "озон": "https://ozon.ru",
        "ozon": "https://ozon.ru",
        "авито": "https://avito.ru",
        "avito": "https://avito.ru",
        "aliexpress": "https://aliexpress.com",
        "алиэкспресс": "https://aliexpress.com",
        "amazon": "https://amazon.com",
        "амазон": "https://amazon.com",
    }

    def _tool_open_app(self, app_name: str) -> ToolResult:
        """Запуск приложения — универсальный поиск с верификацией.

        Порядок:
          1. site_map — точные/частичные совпадения известных сайтов
             (яндекс музыка, ютуб, gmail, …). Это нужно ДО резолвера,
             иначе для запроса «открой яндекс музыку в браузере»
             резолвер по слову «браузер» запустит Firefox без URL.
          2. ApplicationResolver — поиск установленного desktop-приложения.
          3. site_map с нормализацией падежей (страховка).
        """
        # ── 1. site_map — приоритет известным сайтам ──────────────
        site_result = self._try_open_known_site(app_name)
        if site_result is not None:
            return site_result

        # ── 2. Локальный desktop-резолвер ─────────────────────────
        try:
            from lina.core.application_resolver import get_resolver
            resolver = get_resolver()
            result = resolver.launch(app_name)
            if result.success:
                return ToolResult(output=result.message)
        except Exception as e:
            logger.error("ApplicationResolver error: %s", e)

        # ── 3. Final fallback: повторный site_map с нормализацией ─
        return self._open_app_web_fallback(app_name)

    @staticmethod
    def _normalize_for_site_map(text: str) -> str:
        """Простая нормализация русских падежей для lookup в _SITE_MAP.

        «яндекс музыку» → «яндекс музык»  (чтобы совпало с «яндекс музыка»
        через partial-match внутри _open_app_web_fallback).

        Не идеально, но решает основные случаи: -у/-ю/-е/-ой/-и в конце.
        """
        text = (text or "").strip().lower()
        if not text:
            return ""
        # Снимаем хвост «в браузере / через хром …»
        text = re.sub(
            r"\s+(?:в|через)\s+(?:браузере?|хроме?|firefox|chrome|"
            r"opera|edge|safari)\.?$",
            "", text, flags=re.IGNORECASE,
        ).strip()
        return text

    def _try_open_known_site(self, app_name: str):
        """Lookup app_name в _SITE_MAP с учётом склонений и хвоста.

        Возвращает ToolResult если совпало, иначе None — caller продолжит
        обычным путём (через ApplicationResolver).
        """
        key = self._normalize_for_site_map(app_name)
        if not key:
            return None

        # 1. Точное совпадение
        if key in self._SITE_MAP:
            url = self._SITE_MAP[key]
            logger.info("open_app site (exact): %s → %s", key, url)
            return self._tool_open_url(url)

        # 2. Частичное совпадение — нужно чтобы «яндекс музыку» совпало
        # с «яндекс музыка». Берём только базу слов (3+ символа) и
        # сравниваем с алиасами по началу.
        key_words = [w for w in key.split() if len(w) >= 3]
        for alias, url in self._SITE_MAP.items():
            alias_words = alias.split()
            # Все слова алиаса должны быть префиксами слов в key
            # (или наоборот) — это покрывает падежи.
            if not alias_words or not key_words:
                continue
            ok = True
            for aw in alias_words:
                base = aw[:max(3, len(aw) - 2)]  # «музыка» → «музык»
                if not any(kw.startswith(base) or aw.startswith(kw[:max(3, len(kw) - 2)])
                           for kw in key_words):
                    ok = False
                    break
            if ok:
                logger.info("open_app site (fuzzy): %s → %s (alias=%s)",
                            key, url, alias)
                return self._tool_open_url(url)

        return None

    def _open_app_web_fallback(self, app_name: str) -> ToolResult:
        """Fallback: открываем соответствующий сайт в браузере."""
        key = app_name.strip().lower()

        # 1. Точное совпадение в site_map
        if key in self._SITE_MAP:
            url = self._SITE_MAP[key]
            logger.info("open_app fallback (exact): %s → %s", key, url)
            return self._tool_open_url(url)

        # 2. Частичное совпадение (spotify → open.spotify.com)
        for alias, url in self._SITE_MAP.items():
            if alias in key or key in alias:
                logger.info("open_app fallback (partial): %s → %s", key, url)
                return self._tool_open_url(url)

        # 3. Unknown app — no URL guessing (security: prevents open-redirect)
        return ToolResult(
            success=False,
            error=f"Приложение «{app_name}» не найдено ни локально, ни в известных сайтах."
        )

    @staticmethod
    def _tool_install_app(app_name: str) -> ToolResult:
        """Подсказка по установке приложения через ApplicationResolver."""
        app_name = app_name.strip()
        if not app_name:
            return ToolResult(success=False, error="Не указано название приложения")
        try:
            from lina.core.application_resolver import get_resolver
            resolver = get_resolver()
            suggestions = resolver.suggest_installation(app_name)
            if not suggestions:
                return ToolResult(
                    success=False,
                    error=f"Не удалось найти способ установки «{app_name}». "
                          f"Попробуй использовать web_search для поиска в интернете."
                )
            # Проверяем, есть ли реальные результаты (не только web-ссылки)
            real_suggestions = [s for s in suggestions if s.method != "web"]
            web_suggestions = [s for s in suggestions if s.method == "web"]

            lines = []
            if real_suggestions:
                lines.append(f"📦 Варианты установки «{app_name}»:\n")
                for i, s in enumerate(real_suggestions, 1):
                    method = s.method.upper() if hasattr(s, 'method') else "?"
                    cmd = s.command if hasattr(s, 'command') else str(s)
                    note = f" — {s.note}" if hasattr(s, 'note') and s.note else ""
                    src_tag = ""
                    if hasattr(s, 'source') and s.source:
                        src_tag = f" ({s.source})"
                    if cmd:
                        lines.append(f"  {i}. [{method}] {cmd}{src_tag}{note}")
                    elif hasattr(s, 'url') and s.url:
                        lines.append(f"  {i}. [{method}] 🌐 {s.url}{note}")
            else:
                lines.append(
                    f"⚠️ Приложение «{app_name}» не найдено в системных "
                    f"репозиториях (pacman, AUR, flatpak, snap).\n"
                )

            # Показываем web-результаты
            for s in web_suggestions:
                note = s.note if hasattr(s, 'note') and s.note else ""
                url = s.url if hasattr(s, 'url') and s.url else ""
                if note:
                    lines.append(f"\n🌐 Результат веб-поиска:\n  {note}")
                if url:
                    lines.append(f"  🔗 {url}")

            return ToolResult(output="\n".join(lines))
        except Exception as e:
            logger.error("install_app error: %s", e)
            return ToolResult(success=False, error=f"Ошибка поиска установки: {e}")

    @staticmethod
    def _tool_shell(command: str) -> ToolResult:
        """Выполнение shell-команды с проверкой безопасности."""
        # v0.8.0: normalize whitespace before security checks to prevent bypass
        normalized = re.sub(r'\s+', ' ', command.strip())
        # Блокируем опасные
        dangerous = re.compile(
            r"rm\s+(-rf?|--recursive).*(/|~|\*)|"
            r"mkfs\.|dd\s+if=|:>\s*/|>\s*/dev/sd|"
            r"chmod\s+-R\s+777\s+/|"
            r"shutdown|reboot|poweroff|halt|init\s+0",
            re.IGNORECASE,
        )
        if dangerous.search(normalized):
            logger.warning("Blocked dangerous command: %s", command[:80])
            return ToolResult(success=False, error="⛔ Команда заблокирована: обнаружен опасный паттерн")
        # Блокируем injection-паттерны: $(...), `...`, base64 -d|bash, eval, python/perl -c
        injection = re.compile(
            r"\$\(|`[^`]+`|"                             # subshell injection
            r"base64\s+(-d|--decode)\s*\|\s*(ba)?sh|"    # encoded payload
            r"\beval\s+|"                                 # eval trick
            r"(python[23]?|perl|ruby)\s+-[ce]\s|"        # script injection
            r"\bcurl\s+.*\|\s*(ba)?sh|"                  # curl|bash
            r"\bwget\s+.*\|\s*(ba)?sh|"                  # wget|bash
            r"\$'\\'",                                    # ANSI-C quoting bypass
            re.IGNORECASE,
        )
        if injection.search(normalized):
            logger.warning("Blocked injection pattern: %s", command[:80])
            return ToolResult(success=False, error="⛔ Команда заблокирована: обнаружен injection-паттерн")
        try:
            r = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=30, env={**os.environ, "LANG": "C.UTF-8"},
            )
            out = r.stdout.strip()
            err = r.stderr.strip()
            if r.returncode == 0:
                return ToolResult(output=out or "(выполнено)")
            return ToolResult(success=False, output=out, error=err)
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="⏰ Таймаут (30с)")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _tool_system_info(category: str) -> ToolResult:
        """Получение системной информации по категории."""
        commands = {
            "os": "cat /etc/os-release | head -5 && uname -r",
            "cpu": "lscpu | head -15",
            "ram": "free -h",
            "disk": "df -h / && echo '---' && lsblk -o NAME,SIZE,TYPE,MOUNTPOINT 2>/dev/null | head -10",
            "network": "ip -brief addr 2>/dev/null && echo '---' && ss -tlnp 2>/dev/null | head -10",
            "processes": "ps aux --sort=-%cpu | head -12",
            "all": "echo '=== OS ===' && cat /etc/os-release | head -3 && echo '=== Kernel ===' && uname -r && echo '=== CPU ===' && grep 'model name' /proc/cpuinfo | head -1 && echo '=== RAM ===' && free -h | head -2 && echo '=== Disk ===' && df -h / | tail -1",
        }
        cat = category.lower().strip()
        if cat not in commands:
            cat = "all"
        cmd = commands[cat]
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            return ToolResult(output=r.stdout.strip())
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _tool_full_llm(query: str) -> ToolResult:
        """Маркер для передачи в тяжёлую модель."""
        return ToolResult(needs_full_llm=True, output=query)

    # ═══════════════════════════════════════════════════════════════════════════
    #  НОВЫЕ инструменты
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _tool_screenshot(mode: str = "full") -> ToolResult:
        """Скриншот через spectacle (KDE)."""
        if not shutil.which("spectacle"):
            return ToolResult(success=False, error="spectacle не установлен")
        mode = mode.lower().strip()
        mode_map = {
            "full": ["-f", "-b"],          # fullscreen, background
            "window": ["-a", "-b"],        # active window
            "region": ["-r"],              # interactive region
        }
        args = mode_map.get(mode, mode_map["full"])
        try:
            subprocess.Popen(
                ["spectacle"] + args,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            labels = {"full": "всего экрана", "window": "активного окна", "region": "выбранной области"}
            return ToolResult(output=f"📸 Скриншот {labels.get(mode, 'экрана')} сделан")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _tool_media_control(action: str) -> ToolResult:
        """Управление медиа через playerctl."""
        if not shutil.which("playerctl"):
            return ToolResult(success=False, error="playerctl не установлен")
        action = action.lower().strip()
        action_map = {
            "play": "play", "pause": "pause", "toggle": "play-pause",
            "play-pause": "play-pause", "next": "next", "previous": "previous",
            "prev": "previous", "stop": "stop", "status": "status",
        }
        pctl_action = action_map.get(action)
        if not pctl_action:
            return ToolResult(success=False, error=f"Неизвестное действие: {action}")
        try:
            if pctl_action == "status":
                r = subprocess.run(
                    ["playerctl", "metadata", "--format",
                     "{{ status }}: {{ artist }} — {{ title }}"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout.strip():
                    return ToolResult(output=f"🎵 {r.stdout.strip()}")
                return ToolResult(output="🎵 Ничего не играет")
            r = subprocess.run(
                ["playerctl", pctl_action],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                labels = {
                    "play": "▶️ Воспроизведение",
                    "pause": "⏸️ Пауза",
                    "play-pause": "⏯️ Пауза/воспроизведение",
                    "next": "⏭️ Следующий трек",
                    "previous": "⏮️ Предыдущий трек",
                    "stop": "⏹️ Остановлено",
                }
                return ToolResult(output=labels.get(pctl_action, "Готово"))
            return ToolResult(success=False, error=r.stderr.strip() or "Нет активного плеера")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _tool_timer(seconds: int, message: str = "⏰ Время вышло!") -> ToolResult:
        """Таймер с уведомлением."""
        global _timer_counter
        seconds = int(seconds)
        if seconds <= 0:
            return ToolResult(success=False, error="Время должно быть > 0")
        if seconds > 86400:
            return ToolResult(success=False, error="Максимум 24 часа")
        with _timer_counter_lock:
            if len(_active_timers) >= MAX_CONCURRENT_TIMERS:
                return ToolResult(
                    success=False,
                    error=f"Достигнут лимит: {MAX_CONCURRENT_TIMERS} таймеров. "
                          "Отмените один перед созданием нового."
                )
            _timer_counter += 1
            timer_id = f"timer_{_timer_counter}"
            cancel_event = threading.Event()
            _active_timers[timer_id] = cancel_event

        def _timer_thread():
            # Ждём с возможностью отмены
            if cancel_event.wait(timeout=seconds):
                # Отменено
                _active_timers.pop(timer_id, None)
                return
            _active_timers.pop(timer_id, None)
            try:
                subprocess.run(
                    ["notify-send", "-u", "critical", "-t", "10000",
                     "⏰ Lina Timer", str(message)],
                    timeout=5,
                )
            except Exception as e:
                logger.warning("Timer %s notification failed: %s", timer_id, e)

        t = threading.Thread(target=_timer_thread, daemon=True)
        t.start()

        if seconds >= 3600:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            desc = f"{h}ч {m}мин" if m else f"{h}ч"
        elif seconds >= 60:
            m = seconds // 60
            s = seconds % 60
            desc = f"{m}мин {s}сек" if s else f"{m}мин"
        else:
            desc = f"{seconds}сек"

        return ToolResult(output=f"⏰ Таймер на {desc} установлен (ID: {timer_id}). Сообщение: {message}")

    @staticmethod
    def _tool_cancel_timer(timer_id: str) -> ToolResult:
        """Отменить активный таймер."""
        timer_id = str(timer_id).strip()
        event = _active_timers.get(timer_id)
        if event is None:
            return ToolResult(success=False, error=f"Таймер '{timer_id}' не найден")
        event.set()
        _active_timers.pop(timer_id, None)
        return ToolResult(output=f"✅ Таймер {timer_id} отменён")

    @staticmethod
    def _tool_list_timers() -> ToolResult:
        """Показать все активные таймеры."""
        if not _active_timers:
            return ToolResult(output="Нет активных таймеров")
        lines = [f"⏰ Активных таймеров: {len(_active_timers)}"]
        for tid in sorted(_active_timers.keys()):
            lines.append(f"  • {tid}")
        return ToolResult(output="\n".join(lines))

    @staticmethod
    def _tool_power_control(action: str, confirm: bool = False) -> ToolResult:
        """Управление питанием."""
        action = action.lower().strip()
        commands = {
            "lock": ("loginctl lock-session", "🔒 Экран заблокирован"),
            "sleep": ("systemctl suspend", "😴 Переход в спящий режим"),
            "reboot": ("systemctl reboot", "🔄 Перезагрузка..."),
            "shutdown": ("systemctl poweroff", "⏻ Выключение..."),
        }
        if action not in commands:
            return ToolResult(success=False, error=f"Неизвестное действие: {action}. Доступно: lock, sleep, reboot, shutdown")
        # Destructive actions require explicit confirmation
        _DESTRUCTIVE_ACTIONS = {"reboot", "shutdown"}
        if action in _DESTRUCTIVE_ACTIONS and not confirm:
            return ToolResult(
                success=False,
                error=f"Действие '{action}' требует подтверждения (confirm=True). Вы уверены?",
                needs_full_llm=True,
            )
        cmd, label = commands[action]
        try:
            r = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return ToolResult(output=label)
            return ToolResult(success=False, error=r.stderr.strip())
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _tool_wifi(state: str) -> ToolResult:
        """WiFi через nmcli."""
        if not shutil.which("nmcli"):
            return ToolResult(success=False, error="nmcli не установлен")
        state = state.lower().strip()
        if state == "status":
            try:
                r = subprocess.run(
                    ["nmcli", "-t", "-f", "WIFI", "general"],
                    capture_output=True, text=True, timeout=5,
                )
                wifi_state = r.stdout.strip()
                # Получаем текущее подключение
                r2 = subprocess.run(
                    ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
                    capture_output=True, text=True, timeout=5,
                )
                # Filter wireless connections from output
                active_lines = [l for l in r2.stdout.strip().splitlines() if "wireless" in l]
                network = active_lines[0].split(":")[0] if active_lines else "не подключён"
                enabled = "включён" if "enabled" in wifi_state else "выключен"
                return ToolResult(output=f"📶 WiFi {enabled}, сеть: {network}")
            except Exception as e:
                return ToolResult(success=False, error=str(e))
        elif state in ("on", "off"):
            try:
                r = subprocess.run(
                    ["nmcli", "radio", "wifi", state],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    label = "включён" if state == "on" else "выключен"
                    return ToolResult(output=f"📶 WiFi {label}")
                return ToolResult(success=False, error=r.stderr.strip())
            except Exception as e:
                return ToolResult(success=False, error=str(e))
        return ToolResult(success=False, error=f"Неизвестное значение: {state}. Используй: on, off, status")

    @staticmethod
    def _tool_bluetooth(state: str) -> ToolResult:
        """Bluetooth через bluetoothctl."""
        if not shutil.which("bluetoothctl"):
            return ToolResult(success=False, error="bluetoothctl не установлен")
        state = state.lower().strip()
        if state == "status":
            try:
                r = subprocess.run(
                    ["bluetoothctl", "show"],
                    capture_output=True, text=True, timeout=5,
                )
                powered = "включён" if "Powered: yes" in r.stdout else "выключен"
                return ToolResult(output=f"🔵 Bluetooth {powered}")
            except Exception as e:
                return ToolResult(success=False, error=str(e))
        elif state in ("on", "off"):
            try:
                r = subprocess.run(
                    ["bluetoothctl", "power", state],
                    capture_output=True, text=True, timeout=10,
                )
                label = "включён" if state == "on" else "выключен"
                return ToolResult(output=f"🔵 Bluetooth {label}")
            except Exception as e:
                return ToolResult(success=False, error=str(e))
        return ToolResult(success=False, error=f"Неизвестное значение: {state}. Используй: on, off, status")

    @staticmethod
    def _tool_night_mode(state: str) -> ToolResult:
        """Ночной режим (KDE NightLight через DBus)."""
        _dbus_svc = "org.kde.KWin"
        _dbus_obj = "/org/kde/KWin/NightLight"
        _dbus_iface = "org.kde.KWin.NightLight"
        state = state.lower().strip()

        if state == "status":
            try:
                r = subprocess.run(
                    ["busctl", "--user", "get-property", _dbus_svc, _dbus_obj, _dbus_iface, "enabled"],
                    capture_output=True, text=True, timeout=5,
                )
                r2 = subprocess.run(
                    ["busctl", "--user", "get-property", _dbus_svc, _dbus_obj, _dbus_iface, "currentTemperature"],
                    capture_output=True, text=True, timeout=5,
                )
                enabled = "включён" if "true" in r.stdout else "выключен"
                temp = r2.stdout.strip().split()[-1] if r2.stdout.strip() else "?"
                return ToolResult(output=f"🌙 Ночной режим {enabled}, температура: {temp}K")
            except Exception as e:
                return ToolResult(success=False, error=str(e))

        # Числовое значение — предпросмотр температуры
        if state.isdigit():
            temp = int(state)
            if temp < 1000 or temp > 6500:
                return ToolResult(success=False, error="Температура должна быть 1000-6500K")
            try:
                subprocess.run(
                    ["qdbus6", "org.kde.KWin", "/org/kde/KWin/NightLight",
                     "org.kde.KWin.NightLight.preview", str(temp)],
                    capture_output=True, text=True, timeout=5,
                )
                return ToolResult(output=f"🌙 Температура экрана: {temp}K")
            except Exception as e:
                return ToolResult(success=False, error=str(e))

        if state in ("on", "off", "toggle"):
            # KDE NightLight управляется через kwriteconfig + DBus
            try:
                if state == "toggle":
                    r = subprocess.run(
                        ["busctl", "--user", "get-property", _dbus_svc, _dbus_obj, _dbus_iface, "enabled"],
                        capture_output=True, text=True, timeout=5,
                    )
                    currently_on = "true" in r.stdout
                    state = "off" if currently_on else "on"

                enabled_val = "true" if state == "on" else "false"
                # Используем kwriteconfig6 для изменения настройки
                subprocess.run(
                    ["kwriteconfig6", "--file", "kwinrc", "--group", "NightColor",
                     "--key", "Active", enabled_val],
                    capture_output=True, text=True, timeout=5,
                )
                # Перезагружаем конфиг KWin
                subprocess.run(
                    ["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"],
                    capture_output=True, text=True, timeout=5,
                )
                label = "включён 🌙" if state == "on" else "выключен ☀️"
                return ToolResult(output=f"Ночной режим {label}")
            except Exception as e:
                return ToolResult(success=False, error=str(e))

        return ToolResult(success=False, error=f"Неизвестное значение: {state}. Используй: on, off, toggle, status, или число 1000-6500")

    @staticmethod
    def _tool_clipboard(action: str, text: str = "") -> ToolResult:
        """Буфер обмена через wl-copy/wl-paste (Wayland)."""
        action = action.lower().strip()
        if action == "get":
            if not shutil.which("wl-paste"):
                return ToolResult(success=False, error="wl-paste не установлен")
            try:
                r = subprocess.run(
                    ["wl-paste"], capture_output=True, text=True, timeout=5,
                )
                content = r.stdout.strip()
                if not content:
                    return ToolResult(output="📋 Буфер обмена пуст")
                # Ограничиваем длину
                if len(content) > 500:
                    content = content[:500] + "..."
                return ToolResult(output=f"📋 Содержимое буфера:\n{content}")
            except Exception as e:
                return ToolResult(success=False, error=str(e))
        elif action == "set":
            if not text:
                return ToolResult(success=False, error="Нет текста для записи в буфер")
            if not shutil.which("wl-copy"):
                return ToolResult(success=False, error="wl-copy не установлен")
            try:
                r = subprocess.run(
                    ["wl-copy", text], capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    return ToolResult(output="📋 Текст скопирован в буфер обмена")
                return ToolResult(success=False, error=r.stderr.strip())
            except Exception as e:
                return ToolResult(success=False, error=str(e))
        return ToolResult(success=False, error=f"Неизвестное действие: {action}. Используй: get, set")

    @staticmethod
    def _tool_notification(title: str, message: str = "") -> ToolResult:
        """Уведомление через notify-send."""
        if not shutil.which("notify-send"):
            return ToolResult(success=False, error="notify-send не установлен")
        try:
            cmd = ["notify-send", "-t", "5000", title]
            if message:
                cmd.append(message)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return ToolResult(output=f"🔔 Уведомление отправлено: {title}")
            return ToolResult(success=False, error=r.stderr.strip())
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _tool_kill_process(name: str, force: bool = False) -> ToolResult:
        """Убить процесс по имени."""
        name = name.strip()
        if not name:
            return ToolResult(success=False, error="Укажи имя процесса")
        # Reject regex-like patterns — name must be a simple process name
        if re.search(r'[.*+?\[\]|^$(){}]', name):
            return ToolResult(success=False, error="Имя процесса содержит недопустимые символы")
        # Проверяем что процесс существует (exact match)
        try:
            r = subprocess.run(
                ["pgrep", "-x", "-c", name],
                capture_output=True, text=True, timeout=5,
            )
            count = int(r.stdout.strip()) if r.stdout.strip() else 0
            if count == 0:
                return ToolResult(output=f"Процесс '{name}' не найден (уже не запущен)")
        except Exception:
            pass
        signal = "-9" if force else "-15"
        try:
            r = subprocess.run(
                ["pkill", signal, "-x", name],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return ToolResult(output=f"💀 Процесс '{name}' завершён")
            return ToolResult(success=False, error=f"Не удалось завершить '{name}': {r.stderr.strip()}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _tool_open_url(url: str) -> ToolResult:
        """Открыть URL в браузере."""
        url = url.strip()
        # Block dangerous URL schemes (case-insensitive)
        _BLOCKED_SCHEMES = ("file://", "ssh://", "smb://", "ftp://", "data:", "javascript:")
        url_lower = url.lower()
        if any(url_lower.startswith(s) for s in _BLOCKED_SCHEMES):
            return ToolResult(success=False, error="Запрещённая схема URL")
        # Добавляем https:// если нет протокола
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            subprocess.Popen(
                ["xdg-open", url],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return ToolResult(output=f"🌐 Открываю: {url}")
        except Exception as e:
            logger.error("open_url failed: %s", e)
            return ToolResult(success=False, error="Не удалось открыть URL")

    @staticmethod
    def _tool_find_file(pattern: str, directory: str = "") -> ToolResult:
        """Поиск файлов через find."""
        home = os.path.expanduser("~")
        directory = directory.strip() or home
        # Restrict to home directory — prevent scanning /etc, /root, /proc etc.
        real_dir = os.path.realpath(directory)
        if not real_dir.startswith(home):
            return ToolResult(
                success=False,
                error=f"Поиск разрешён только в домашней директории ({home})",
            )
        pattern = pattern.strip()
        if not pattern:
            return ToolResult(success=False, error="Укажи имя файла для поиска")
        # Добавляем wildcard если нет
        if "*" not in pattern and "?" not in pattern:
            pattern = f"*{pattern}*"
        try:
            r = subprocess.run(
                ["find", "-P", real_dir, "-maxdepth", "5", "-iname", pattern,
                 "-not", "-path", "*/.*", "-not", "-path", "*/node_modules/*"],
                capture_output=True, text=True, timeout=15,
            )
            files = r.stdout.strip().split("\n")
            files = [f for f in files if f]  # убираем пустые
            if not files:
                return ToolResult(output=f"🔍 Файлы по '{pattern}' не найдены в {directory}")
            # Ограничиваем количество
            total = len(files)
            files = files[:20]
            result = "\n".join(files)
            if total > 20:
                result += f"\n... и ещё {total - 20} файлов"
            return ToolResult(output=f"🔍 Найдено {total} файлов:\n{result}")
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="⏰ Поиск занял слишком много времени (>15с)")
        except Exception as e:
            logger.error("find_file failed: %s", e)
            return ToolResult(success=False, error="Не удалось выполнить поиск")

    @staticmethod
    def _tool_weather(city: str = "") -> ToolResult:
        """Погода через WebSearchEngine (production-grade)."""
        try:
            from lina.core.web_search_engine import get_web_search_engine
            engine = get_web_search_engine()
            query = f"погода {city.strip()}" if city.strip() else "погода"
            resp = engine.search(query)
            if resp.success and resp.summary:
                return ToolResult(output=resp.summary)
            return ToolResult(success=False, error=resp.error or "Не удалось получить погоду.")
        except Exception as e:
            # Fallback: прямой wttr.in
            from urllib.parse import quote
            safe_city = quote(city.strip(), safe='')
            try:
                from lina.utils.http import http_get
                wttr_url = f"https://wttr.in/{safe_city}?format=%l:+%C+%t+(%f)+💧%h+💨%w&lang=ru"
                body = http_get(wttr_url, timeout=20)
                if body and "Unknown" not in body:
                    return ToolResult(output=f"🌤️ {body.strip()}")
            except Exception:
                pass
            return ToolResult(success=False, error=f"Ошибка погоды: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    #  Веб-поиск
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _tool_web_search(query: str) -> ToolResult:
        """Поиск в интернете — production-grade с retry + fallback."""
        query = query.strip()
        if not query:
            return ToolResult(success=False, error="Пустой запрос")
        try:
            from lina.core.web_search_engine import get_web_search_engine
            engine = get_web_search_engine()
            resp = engine.search(query)
            if resp.success and resp.summary:
                return ToolResult(output=resp.summary)
            elif resp.error:
                return ToolResult(success=False, error=resp.error)
            else:
                return ToolResult(success=False, error="Не удалось выполнить поиск в интернете")
        except Exception as e:
            return ToolResult(success=False, error=f"Ошибка веб-поиска: {e}")

    @staticmethod
    def _tool_find_and_open_site(query: str) -> ToolResult:
        """Find the most relevant URL via web search and open it in browser.

        Логика:
          1. Выполнить веб-поиск.
          2. Из результатов выбрать топ по relevance.
          3. Отфильтровать «мусорные» площадки (агрегаторы, видео-сниппеты).
          4. Открыть лучшую ссылку через `open_url` (xdg-open в браузере).
          5. Вернуть какой URL открыт.
        """
        query = query.strip()
        if not query:
            return ToolResult(success=False, error="Пустой запрос")

        try:
            from lina.core.web_search_engine import get_web_search_engine
            engine = get_web_search_engine()
            resp = engine.search(query)
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Не удалось выполнить поиск: {e}",
            )

        if not resp.success or not resp.results:
            return ToolResult(
                success=False,
                error=(resp.error or
                       "Поиск ничего не нашёл — попробуй уточнить запрос."),
            )

        # Сортируем по relevance (на всякий случай — обычно уже отсортировано).
        ranked = sorted(
            resp.results,
            key=lambda r: getattr(r, "relevance", 0.0),
            reverse=True,
        )

        # Лёгкий чёрный список: видео-агрегаторы и нерелевантные домены.
        # Видео сами по себе валидны, но «открой страницу X» обычно
        # означает официальный сайт / документацию / GitHub, а не YouTube.
        _BLACKLIST_DOMAINS = (
            "google.com/search", "duckduckgo.com", "ecosia.org",
            "search.brave.com",
        )

        best = None
        for r in ranked:
            url = getattr(r, "url", "") or ""
            if not url or not url.startswith(("http://", "https://")):
                continue
            url_lower = url.lower()
            if any(d in url_lower for d in _BLACKLIST_DOMAINS):
                continue
            best = r
            break

        if best is None:
            return ToolResult(
                success=False,
                error="В результатах нет подходящих ссылок — попробуй другой запрос.",
            )

        # Открываем через тот же метод что и обычный open_url —
        # его блок-лист схем (file://, javascript:, data:) сработает.
        result = ToolRegistry._tool_open_url(best.url)
        if not result.success:
            return result

        title = (getattr(best, "title", "") or "").strip()
        label = f"«{title}»" if title else best.url
        return ToolResult(
            output=f"🌐 Нашла и открываю: {label}\n{best.url}",
        )

    # ═══════════════════════════════════════════════════════════════════════════
    #  Интерактивная консоль
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _tool_run_in_console(command: str) -> ToolResult:
        """Открывает терминал (konsole) и выполняет команду в нём."""
        command = command.strip()
        if not command:
            return ToolResult(success=False, error="Пустая команда")
        # Apply same safety checks as _tool_shell
        _CONSOLE_DANGEROUS = re.compile(
            r"rm\s+(-[rf]+\s+)?/|dd\s+if=|mkfs|:>|>\s*/dev/|chmod\s+777\s+/|"
            r"curl.*\|\s*(ba)?sh|wget.*\|\s*(ba)?sh|python\s+-c|bash\s+-c|eval\s|"
            r"find.*-delete|shred|wipefs|truncate",
            re.IGNORECASE,
        )
        if _CONSOLE_DANGEROUS.search(command):
            return ToolResult(
                success=False,
                error="Команда заблокирована: обнаружен опасный паттерн",
            )

        # Определяем доступный терминал
        terminal = None
        for term in ("konsole", "alacritty", "kitty", "gnome-terminal", "xterm"):
            if shutil.which(term):
                terminal = term
                break
        if not terminal:
            return ToolResult(success=False, error="Не найден терминал (konsole/alacritty/kitty)")

        try:
            # v0.8.0: don't shlex.quote entire command — it's passed to bash -c
            # which expects a single string argument, not a quoted blob
            safe_cmd = command.replace("'", "'\\''")  # escape single quotes for shell embedding
            if terminal == "konsole":
                subprocess.Popen(
                    ["konsole", "--hold", "-e", "bash", "-c",
                     f"{safe_cmd}; echo \"\"; echo \"━━━ Команда завершена ━━━\"; read"],
                    start_new_session=True,
                )
            elif terminal == "alacritty":
                subprocess.Popen(
                    ["alacritty", "-e", "bash", "-c",
                     f'{safe_cmd}; echo ""; echo "━━━ Команда завершена ━━━"; read'],
                    start_new_session=True,
                )
            elif terminal == "kitty":
                subprocess.Popen(
                    ["kitty", "--hold", "bash", "-c", safe_cmd],
                    start_new_session=True,
                )
            else:
                subprocess.Popen(
                    [terminal, "-e", "bash", "-c",
                     f'{safe_cmd}; echo ""; echo "━━━ Команда завершена ━━━"; read'],
                    start_new_session=True,
                )
            return ToolResult(output=f"🖥️ Открыл {terminal} с командой: {command}")
        except Exception as e:
            return ToolResult(success=False, error=f"Не удалось открыть терминал: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    #  PROBLEM TERMINATOR — реализации
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _tool_diagnose_problem(problem: str) -> ToolResult:
        """Полная диагностика: scan → logs → classify → memory → рекомендации."""
        problem = problem.strip()
        if not problem:
            return ToolResult(success=False, error="Опишите проблему")
        try:
            from lina.diagnostics.scanner import get_scanner
            from lina.diagnostics.log_engine import get_log_engine
            from lina.diagnostics.classifier import get_classifier
            from lina.diagnostics.memory import get_memory

            lines = [f"🔍 Диагностика: «{problem}»\n"]

            # 1. Scan
            scanner = get_scanner()
            state = scanner.scan()
            summary = state.format_summary()
            lines.append("═══ Состояние системы ═══")
            lines.append(summary)

            # 2. Classify
            classifier = get_classifier()
            log_engine = get_log_engine()
            log_report = log_engine.analyze_for_problem(problem)
            diagnoses = classifier.classify(problem, state, log_report)

            if diagnoses:
                lines.append("\n═══ Диагнозы ═══")
                for i, d in enumerate(diagnoses[:3], 1):
                    lines.append(f"  {i}. [{d.category.value}] {d.root_cause} (уверенность: {d.confidence:.0%})")
                    lines.append(f"     Риск: {d.risk.value}")
                    if d.suggested_actions:
                        for act in d.suggested_actions[:3]:
                            lines.append(f"     → {act}")
                    if d.search_query:
                        lines.append(f"     🔎 Поисковый запрос: {d.search_query}")

            # 3. Memory — прошлый опыт
            memory = get_memory()
            if diagnoses:
                rec = memory.get_recommendation(diagnoses[0].category.value)
                if rec:
                    lines.append(f"\n═══ Из памяти ═══\n  {rec}")

            # 4. Log highlights
            if log_report and log_report.root_causes:
                lines.append("\n═══ Корневые причины (логи) ═══")
                for rc in log_report.root_causes[:5]:
                    lines.append(f"  ⚠️ {rc}")

            return ToolResult(output="\n".join(lines))

        except Exception as e:
            logger.error("diagnose_problem error: %s", e)
            return ToolResult(success=False, error=f"Ошибка диагностики: {e}")

    @staticmethod
    def _tool_system_health() -> ToolResult:
        """Быстрая проверка здоровья системы."""
        try:
            from lina.diagnostics.scanner import get_scanner
            scanner = get_scanner()
            state = scanner.scan()
            summary = state.format_summary()
            return ToolResult(output=f"🏥 Здоровье системы\n\n{summary}")
        except Exception as e:
            logger.error("system_health error: %s", e)
            return ToolResult(success=False, error=f"Ошибка сканирования: {e}")

    @staticmethod
    def _tool_fix_problem(problem: str, mode: str = "safe") -> ToolResult:
        """Починка с полным пайплайном PROBLEM TERMINATOR."""
        problem = problem.strip()
        if not problem:
            return ToolResult(success=False, error="Опишите проблему")
        mode = mode.strip().lower()

        try:
            from lina.diagnostics.scanner import get_scanner
            from lina.diagnostics.log_engine import get_log_engine
            from lina.diagnostics.classifier import get_classifier
            from lina.diagnostics.autofix import get_autofix, FixMode
            from lina.diagnostics.memory import get_memory

            # 1. Diagnose
            scanner = get_scanner()
            state = scanner.scan()
            log_engine = get_log_engine()
            log_report = log_engine.analyze_for_problem(problem)
            classifier = get_classifier()
            diagnoses = classifier.classify(problem, state, log_report)

            if not diagnoses:
                return ToolResult(output=f"🤔 Не удалось классифицировать проблему «{problem}». Попробуйте описать подробнее.")

            diag = diagnoses[0]
            lines = [f"🔧 Починка: «{problem}»"]
            lines.append(f"   Категория: {diag.category.value}")
            lines.append(f"   Причина: {diag.root_cause}")
            lines.append(f"   Риск: {diag.risk.value}\n")

            # 2. Create fix plan
            fix_mode_map = {"safe": FixMode.SAFE, "assist": FixMode.ASSIST, "auto": FixMode.AUTONOMOUS}
            fix_mode = fix_mode_map.get(mode, FixMode.SAFE)
            autofix = get_autofix()
            autofix.mode = fix_mode

            plan = autofix.create_plan(diag)

            if not plan or not plan.actions:
                lines.append("⚠️ Нет автоматических действий для этой категории проблем.")
                if diag.suggested_actions:
                    lines.append("\nРекомендации:")
                    for act in diag.suggested_actions:
                        lines.append(f"  → {act}")
                return ToolResult(output="\n".join(lines))

            # 3. Show plan
            lines.append(f"═══ План починки ({fix_mode.value}) ═══")
            for i, action in enumerate(plan.actions, 1):
                lines.append(f"  {i}. [{action.risk.value}] {action.command}")
                if action.dry_run_cmd:
                    lines.append(f"     dry-run: {action.dry_run_cmd}")

            # 4. Execute if not SAFE
            if fix_mode == FixMode.SAFE:
                lines.append("\n📋 Режим SAFE — только отчёт, ничего не выполнено.")
                lines.append("   Для починки используйте mode='assist' или mode='auto'.")
            else:
                result = autofix.execute_plan(plan)
                lines.append(f"\n═══ Результат ═══")
                lines.append(f"   Статус: {result.status.value}")
                if result.output:
                    lines.append(f"   Вывод: {result.output[:300]}")
                if result.error:
                    lines.append(f"   Ошибка: {result.error[:200]}")

                # 5. Record to memory
                memory = get_memory()
                memory.record_fix(
                    category=diag.category.value,
                    problem=problem,
                    diagnosis=diag.root_cause,
                    actions_taken=[a.command for a in plan.actions],
                    outcome=result.status.value,
                    verified=result.status.value == "success",
                )

            return ToolResult(output="\n".join(lines))

        except Exception as e:
            logger.error("fix_problem error: %s", e)
            return ToolResult(success=False, error=f"Ошибка починки: {e}")

    @staticmethod
    def _tool_system_overview() -> ToolResult:
        """Полный обзор всех подсистем через FullSystemControlLayer."""
        try:
            from lina.diagnostics.control import get_control
            ctrl = get_control()
            overview = ctrl.full_overview()
            return ToolResult(output=overview)
        except Exception as e:
            logger.error("system_overview error: %s", e)
            return ToolResult(success=False, error=f"Ошибка обзора: {e}")

    # ═══════════════════════════════════════════════════════════════
    #  SYSTEM OVERLORD — реализации инструментов
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _tool_predictive_report() -> ToolResult:
        """Предиктивный анализ с трендами и прогнозами."""
        try:
            from lina.diagnostics.predictor import get_predictor
            predictor = get_predictor()
            predictor.tick()

            lines = ["═══ Предиктивный отчёт OVERLORD ═══", ""]

            # Current metrics
            for name, series in predictor._metrics.items():
                if series.samples:
                    last = series.samples[-1]
                    avg = series.moving_average()
                    trend = series.trend_direction()
                    slope = series.trend_slope()
                    trend_icon = {"rising": "📈", "falling": "📉", "stable": "➡️"}.get(trend, "?")
                    lines.append(f"  {trend_icon} {name}: {last.value:.1f} (avg={avg:.1f}, slope={slope:+.3f})")

            # Alerts
            alerts = predictor.analyze()
            if alerts:
                lines.append("")
                lines.append("  ⚠️ Alerts:")
                for a in alerts:
                    lines.append(f"    [{a.level.value}] {a.subsystem}/{a.metric}: "
                                 f"{a.current_value:.1f} → {a.predicted_value:.1f} "
                                 f"(порог={a.threshold:.1f})")
            else:
                lines.append("")
                lines.append("  ✅ Нет предупреждений")

            return ToolResult(output="\n".join(lines))
        except Exception as e:
            logger.error("predictive_report error: %s", e)
            return ToolResult(success=False, error=f"Ошибка предиктивного отчёта: {e}")

    @staticmethod
    def _tool_drift_check() -> ToolResult:
        """Проверка дрифта конфигурации."""
        try:
            from lina.diagnostics.drift import get_drift_detector
            detector = get_drift_detector()
            events = detector.check()

            lines = ["═══ Drift Check OVERLORD ═══", ""]
            if not events:
                lines.append("  ✅ Дрифт не обнаружен — система стабильна")
            else:
                for e in events:
                    icon = {"critical": "🔴", "warning": "⚠️", "info": "ℹ️"}.get(e.severity, "?")
                    lines.append(f"  {icon} [{e.severity}] {e.component}.{e.field}")
                    lines.append(f"    {e.old_value} → {e.new_value}")
                    if e.requires_healthcheck:
                        lines.append(f"    ↳ Требуется health-check")

            return ToolResult(output="\n".join(lines))
        except Exception as e:
            logger.error("drift_check error: %s", e)
            return ToolResult(success=False, error=f"Ошибка drift check: {e}")

    @staticmethod
    def _tool_risk_assess(command: str) -> ToolResult:
        """Оценка риска команды."""
        try:
            from lina.diagnostics.risk_engine import get_risk_engine
            engine = get_risk_engine()
            assessment = engine.assess_command(command)

            verdict_icons = {
                "negligible": "🟢", "low": "🟢", "medium": "🟡",
                "high": "🟠", "critical": "🔴",
            }
            icon = verdict_icons.get(assessment.verdict.value, "?")

            lines = [
                f"═══ Risk Assessment ═══",
                f"  Команда: {command}",
                f"  Вердикт: {icon} {assessment.verdict.value.upper()} ({assessment.total_risk:.2f})",
                "",
                "  Факторы:",
            ]
            for factor, score in assessment.factors.items():
                bar = "█" * int(score * 10)
                lines.append(f"    {factor:25s} {score:.2f} {bar}")

            lines.append("")
            lines.append(f"  Допустимые режимы: {', '.join(assessment.allowed_modes)}")

            return ToolResult(output="\n".join(lines))
        except Exception as e:
            logger.error("risk_assess error: %s", e)
            return ToolResult(success=False, error=f"Ошибка оценки риска: {e}")

    @staticmethod
    def _tool_integrity_check() -> ToolResult:
        """Проверка целостности модулей."""
        try:
            from lina.diagnostics.integrity import get_integrity_guard
            guard = get_integrity_guard()
            report = guard.full_check()
            return ToolResult(output=guard.format_report(report))
        except Exception as e:
            logger.error("integrity_check error: %s", e)
            return ToolResult(success=False, error=f"Ошибка проверки целостности: {e}")

    @staticmethod
    def _tool_healer_status() -> ToolResult:
        """Статус самовосстановления."""
        try:
            from lina.diagnostics.self_healer import get_self_healer
            healer = get_self_healer()
            return ToolResult(output=healer.format_report())
        except Exception as e:
            logger.error("healer_status error: %s", e)
            return ToolResult(success=False, error=f"Ошибка статуса healer: {e}")

    @staticmethod
    def _tool_web_solution(error: str, category: str = "") -> ToolResult:
        """Веб-разведка для решения проблемы."""
        try:
            from lina.diagnostics.web_intel import get_web_intel_sandbox
            sandbox = get_web_intel_sandbox()
            result = sandbox.search_solution(error, category=category)
            return ToolResult(output=sandbox.format_report(result))
        except Exception as e:
            logger.error("web_solution error: %s", e)
            return ToolResult(success=False, error=f"Ошибка веб-разведки: {e}")
