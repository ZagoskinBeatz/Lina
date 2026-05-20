"""
Lina — MiniLLM Engine с Function-Calling.

Быстрая мини-модель (Qwen2.5-1.5B) для:
  - Понимание intent пользователя  
  - Вызов функций (brightness, volume, apps, shell, system_info)
  - Быстрые ответы (приветствия, простые вопросы)
  - Эскалация на тяжёлую модель для сложных задач

Принцип: ИИ ПОНИМАЕТ запрос, а не сопоставляет с регулярками.
"""

import gc
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lina.config import config, ModelProfile, MODELS_DIR
from lina.core.tools import ToolRegistry, ToolResult
from lina.core.output import get_printer

logger = logging.getLogger("lina.llm.mini")


def _safe_tool_error(error: str | None) -> str:
    """Strip stack traces from tool errors — return first line only."""
    if not error:
        return "Неизвестная ошибка"
    return error.split("\n")[0][:100]


# Путь к мини-модели (синхронизирован с lina/config.py)
MINI_MODEL_PATH = config.llm.mini.model_path

# Профиль мини-модели
MINI_PROFILE = ModelProfile(
    model_path=MINI_MODEL_PATH,
    n_ctx=4096,         # Промпт ~1800 токенов + ответ 200 = запас
    n_threads=4,
    n_gpu_layers=0,
    temperature=0.1,    # Низкая температура — точные ответы
    max_tokens=200,     # Короткие ответы (JSON)
    top_p=0.9,
    repeat_penalty=1.1,
    estimated_ram_mb=1500,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Системный промпт для function-calling
# ═══════════════════════════════════════════════════════════════════════════════

def _build_system_prompt(tools: ToolRegistry, system_context: str = "") -> str:
    """Строит системный промпт с описанием инструментов."""

    # Компактное описание инструментов (вместо verbose get_tools_prompt)
    tools_compact = """ФУНКЦИИ:
• respond(message) — текстовый ответ (ТОЛЬКО приветствия/реплики)
• set_brightness(value) — яркость экрана: "+10%", "-10%", "50%", "100%"
• set_volume(value) — громкость звука: "+10%", "-10%", "30%", "mute", "unmute"
• open_app(app_name) — запустить приложение: "браузер", "терминал", "проводник"
• run_shell(command) — shell-команда
• system_info(category) — системная инфо: "os","cpu","ram","disk","network","processes"
• ask_full_llm(query) — передать вопрос тяжёлой модели (объяснения, код, знания)
• screenshot(mode) — скриншот: "full", "window", "region"
• media_control(action) — медиа: "play","pause","toggle","next","previous","stop","status"
• set_timer(seconds, message) — таймер: seconds=число секунд, message=текст уведомления
• power_control(action) — питание: "lock","sleep","reboot","shutdown"
• toggle_wifi(state) — WiFi: "on","off","status"
• toggle_bluetooth(state) — Bluetooth: "on","off","status"
• night_mode(state) — ночной режим: "on","off","toggle","status" или температура "3500"
• clipboard(action, text) — буфер: action="get"/"set", text=текст при set
• send_notification(title, message) — уведомление на рабочем столе
• kill_process(name, force) — убить процесс по имени, force=true для SIGKILL
• open_url(url) — открыть КОНКРЕТНЫЙ URL в браузере: "https://youtube.com"
• find_and_open_site(query) — найти сайт через веб-поиск и открыть лучшую ссылку. Используй когда URL неизвестен: "GIMP официальный сайт", "Claude Code GitHub", "Arch Wiki Steam"
• find_file(pattern, directory) — поиск файлов: pattern="*.pdf", directory="~/Документы"
• weather(city) — погода: city="Moscow" (опционально)
• web_search(query) — поиск в интернете: query="установка Max linux"
• run_in_console(command) — открыть терминал и выполнить команду: "sudo apt update" """

    return f"""Ты — Lina, быстрый ИИ-ассистент для Linux (KDE Plasma).

ЗАДАЧА: Определи намерение пользователя и вызови ОДНУ функцию.

ПРАВИЛА:
1. ВСЕГДА отвечай ОДНИМ JSON: {{"tool": "...", "args": {{...}}}}
2. Ничего кроме JSON. Без текста до/после.
3. ГРОМЧЕ/погромче/добавь звук → set_volume("+10%"). ТИШЕ/потише/убавь → set_volume("-10%").
4. ЯРЧЕ/поярче/подсвети → set_brightness("+10%"). ТЕМНЕЕ/потемнее/притуши → set_brightness("-10%").
5. "Громкость 30%" — АБСОЛЮТНОЕ, БЕЗ знака + или -. Число без % = тоже %: "громкость 100" = "100%".
6. Вопросы/объяснения/знания/код → ask_full_llm. Ты маршрутизатор, НЕ энциклопедия!
7. respond — ТОЛЬКО приветствия ("привет","пока","спасибо").
8. Если сомневаешься → ask_full_llm.
9. "старшая модель"/"большая модель"/"тяжёлая модель" → ask_full_llm.
10. "Закрой/убей (приложение)" = kill_process, НЕ power_control! power_control ТОЛЬКО для: lock/sleep/shutdown/reboot.
11. дипсик/deepseek/ChatGPT/гитхаб — это САЙТЫ → open_url, НЕ open_app!
12. "найди/загугли/поищи в инете/интернете" = web_search. "Найди файл" = find_file.
13. "открой консоль и пропиши/введи/выполни" = run_in_console (открывает окно терминала).

ВАЖНО: set_brightness ≠ set_volume! Яркость = ЭКРАН (светлее/темнее/ярче/подсвети). Громкость = ЗВУК (громче/тише/звук/волюмэ).
ВАЖНО: Слово "звук/громкость/volume" = set_volume. Слово "яркость/экран/подсвети" = set_brightness. НЕ ПУТАЙ!
ВАЖНО: Число без % = %: "громкость 100" означает value="100%", "яркость 50" означает value="50%".

{tools_compact}

{system_context}

ПРИМЕРЫ (формат):
Яркость 50% → {{"tool":"set_brightness","args":{{"value":"50%"}}}}
Поярче → {{"tool":"set_brightness","args":{{"value":"+10%"}}}}
Темнее → {{"tool":"set_brightness","args":{{"value":"-10%"}}}}
Громкость 30% → {{"tool":"set_volume","args":{{"value":"30%"}}}}
Погромче → {{"tool":"set_volume","args":{{"value":"+10%"}}}}
Потише → {{"tool":"set_volume","args":{{"value":"-10%"}}}}
Замьють → {{"tool":"set_volume","args":{{"value":"mute"}}}}
Привет! → {{"tool":"respond","args":{{"message":"Привет! Чем помочь?"}}}}
Паузу → {{"tool":"media_control","args":{{"action":"pause"}}}}
Следующий трек → {{"tool":"media_control","args":{{"action":"next"}}}}
Что играет? → {{"tool":"media_control","args":{{"action":"status"}}}}
Ночной режим → {{"tool":"night_mode","args":{{"state":"on"}}}}
Глаза устали → {{"tool":"night_mode","args":{{"state":"on"}}}}
Скриншот → {{"tool":"screenshot","args":{{"mode":"full"}}}}
Таймер 5 минут → {{"tool":"set_timer","args":{{"seconds":300,"message":"Время вышло!"}}}}
Заблокируй экран → {{"tool":"power_control","args":{{"action":"lock"}}}}
Выключи компьютер → {{"tool":"power_control","args":{{"action":"shutdown"}}}}
Выключи вайфай → {{"tool":"toggle_wifi","args":{{"state":"off"}}}}
Включи блютуз → {{"tool":"toggle_bluetooth","args":{{"state":"on"}}}}
Буфер обмена → {{"tool":"clipboard","args":{{"action":"get"}}}}
Убей firefox → {{"tool":"kill_process","args":{{"name":"firefox"}}}}
Закрой телеграм → {{"tool":"kill_process","args":{{"name":"telegram"}}}}
Открой youtube → {{"tool":"open_url","args":{{"url":"https://youtube.com"}}}}
Открой дипсик → {{"tool":"open_url","args":{{"url":"https://chat.deepseek.com"}}}}
Открой ВК → {{"tool":"open_url","args":{{"url":"https://vk.com"}}}}
Открой страницу установки GIMP → {{"tool":"find_and_open_site","args":{{"query":"GIMP установка официальный сайт"}}}}
Найди и открой Arch Wiki по Steam → {{"tool":"find_and_open_site","args":{{"query":"Arch Wiki Steam"}}}}
Открой github проекта Claude Code → {{"tool":"find_and_open_site","args":{{"query":"Claude Code Anthropic GitHub"}}}}
Найди файл report.pdf → {{"tool":"find_file","args":{{"pattern":"report.pdf"}}}}
Какая погода? → {{"tool":"weather","args":{{}}}}
Погода в Перми → {{"tool":"weather","args":{{"city":"Perm"}}}}
Открой браузер → {{"tool":"open_app","args":{{"app_name":"браузер"}}}}
Сколько оперативки? → {{"tool":"system_info","args":{{"category":"ram"}}}}
Загугли рецепт борща → {{"tool":"web_search","args":{{"query":"рецепт борща"}}}}
Найди в инете как установить Max → {{"tool":"web_search","args":{{"query":"установка Max linux"}}}}
Открой консоль и пропиши ip addr → {{"tool":"run_in_console","args":{{"command":"ip addr"}}}}
Выполни в терминале sudo apt update → {{"tool":"run_in_console","args":{{"command":"sudo apt update"}}}}
Что такое IPTV → {{"tool":"ask_full_llm","args":{{"query":"Что такое IPTV"}}}}
Напиши код на Python → {{"tool":"ask_full_llm","args":{{"query":"Напиши код на Python"}}}}
Старшая модель → {{"tool":"ask_full_llm","args":{{"query":"Запусти старшую модель"}}}}"""


# ═══════════════════════════════════════════════════════════════════════════════
#  Парсер ответов мини-модели
# ═══════════════════════════════════════════════════════════════════════════════

def parse_tool_call(raw_response: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Извлекает вызов функции из ответа LLM.
    
    Поддерживает:
      - Чистый JSON: {"tool": "...", "args": {...}}
      - JSON в тексте: bla bla {"tool": "...", "args": {...}} bla
      - JSON в code-блоке: ```json {...} ```
    """
    text = raw_response.strip()
    
    # Убираем code-блоки
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)
    text = text.strip()

    # Находим JSON-объект
    # Ищем первый { ... } с "tool"
    patterns = [
        # Полный JSON
        r'\{[^{}]*"tool"\s*:\s*"[^"]+"\s*,\s*"args"\s*:\s*\{[^{}]*\}[^{}]*\}',
        # Вложенный — для случаев вроде {"tool": "x", "args": {"a": {"b": "c"}}}
        r'\{[^{}]*"tool"\s*:\s*"[^"]+"\s*,\s*"args"\s*:\s*\{[^}]*\{[^}]*\}[^}]*\}[^}]*\}',
    ]
    
    for pattern in patterns:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group())
                if "tool" in obj and "args" in obj:
                    return obj["tool"], obj.get("args", {})
            except json.JSONDecodeError:
                continue

    # Fallback: попробуем парсить весь текст как JSON
    try:
        # Находим первый { и последний }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            obj = json.loads(text[start:end + 1])
            if "tool" in obj:
                return obj["tool"], obj.get("args", {})
    except json.JSONDecodeError:
        pass

    # Fallback: парсим function-call стиль: tool_name("arg") или tool_name(key=value)
    # Модель иногда пишет set_brightness("+10%") вместо JSON
    fn_match = re.search(
        r'(set_brightness|set_volume|screenshot|media_control|set_timer|'
        r'power_control|toggle_wifi|toggle_bluetooth|night_mode|clipboard|'
        r'send_notification|kill_process|open_url|find_file|weather|'
        r'open_app|run_shell|system_info|ask_full_llm|respond)'
        r'\s*\(\s*["\']?([^)]*?)["\']?\s*\)',
        text, re.IGNORECASE,
    )
    if fn_match:
        tool_name = fn_match.group(1)
        arg_raw = fn_match.group(2).strip().strip("'\"")
        # Определяем имя аргумента по инструменту
        arg_name_map = {
            "set_brightness": "value", "set_volume": "value",
            "screenshot": "mode", "media_control": "action",
            "power_control": "action", "toggle_wifi": "state",
            "toggle_bluetooth": "state", "night_mode": "state",
            "kill_process": "name", "open_url": "url",
            "find_and_open_site": "query",
            "find_file": "pattern", "weather": "city",
            "open_app": "app_name", "run_shell": "command",
            "system_info": "category", "ask_full_llm": "query",
            "respond": "message",
        }
        arg_name = arg_name_map.get(tool_name, "value")
        return tool_name, {arg_name: arg_raw}

    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  MiniLLM Engine
# ═══════════════════════════════════════════════════════════════════════════════

class MiniLLMEngine:
    """
    Быстрый LLM-движок на мини-модели с function-calling.
    
    Загружается один раз при старте, держится в памяти (~1.2GB).
    Скорость ответа: <2с (CPU).
    """

    def __init__(self, system_context: str = ""):
        self._model = None
        self._tools = ToolRegistry()
        self._system_context = system_context
        self._system_prompt = _build_system_prompt(self._tools, system_context)
        self._loaded = False
        self._real_n_ctx: int = MINI_PROFILE.n_ctx
        # Контекст последнего действия — для "ещё"/"повтори"
        self._last_action: Optional[Tuple[str, Dict[str, Any]]] = None
        self._last_user_input: str = ""
    
    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self) -> bool:
        """Загрузить мини-модель в память."""
        if self._loaded:
            return True

        if not Path(MINI_MODEL_PATH).exists():
            logger.warning("Mini model not found: %s", MINI_MODEL_PATH)
            return False

        try:
            from llama_cpp import Llama
        except ImportError:
            logger.error("llama-cpp-python not installed")
            return False

        try:
            printer = get_printer()
            printer.print("⚡ Загрузка мини-модели...")
            start = time.time()

            self._model = Llama(
                model_path=MINI_MODEL_PATH,
                n_ctx=MINI_PROFILE.n_ctx,
                n_threads=MINI_PROFILE.n_threads,
                n_gpu_layers=MINI_PROFILE.n_gpu_layers,
                verbose=False,
            )

            # Реальный n_ctx
            if hasattr(self._model, 'n_ctx'):
                self._real_n_ctx = self._model.n_ctx()

            elapsed = time.time() - start
            self._loaded = True

            # Прогрев пропущен — первый реальный запрос прогреет модель сам
            total = time.time() - start
            printer.print(f"⚡ Мини-модель готова ({total:.1f}с)")
            return True

        except Exception as e:
            logger.error("Failed to load mini model: %s", e)
            return False

    def unload(self):
        """Выгрузить мини-модель."""
        if self._model:
            del self._model
            self._model = None
            self._loaded = False
            gc.collect()

    def _quick_brightness_volume(self, lower: str):
        """Быстрый фильтр: однозначные ключевые слова яркости/громкости.
        
        Только для слов, которые на 100% однозначны (без числовых значений).
        Возвращает (tool_name, args) или None.
        """
        # Яркость: поярче, притуши, подсвети — однозначны
        bright_up = ("поярче", "ярче", "подсвети", "не видно", "ничего не видно", "темно, ничего")
        bright_down = ("потемнее", "темнее", "притуши")
        if any(w in lower for w in bright_up):
            return ("set_brightness", {"value": "+10%"})
        if any(w in lower for w in bright_down):
            return ("set_brightness", {"value": "-10%"})
        # Громкость: погромче, потише — однозначны
        vol_up = ("погромче", "громче", "прибавь звук", "добавь громкост", "накинь волюм")
        vol_down = ("потише", "тише", "убавь звук", "убавь громкост")
        if any(w in lower for w in vol_up):
            return ("set_volume", {"value": "+10%"})
        if any(w in lower for w in vol_down):
            return ("set_volume", {"value": "-10%"})
        return None

    def process(self, user_input: str) -> Tuple[str, bool]:
        """
        Обработать запрос пользователя.
        
        Returns:
            (response_text, needs_full_llm)
            - response_text: ответ для пользователя
            - needs_full_llm: True если нужно передать в тяжёлую модель
        """
        if not self._loaded:
            if not self.load():
                return ("", True)  # Фолбэк на тяжёлую

        # Обработка "ещё"/"повтори" — повторить последнее действие
        lower = user_input.lower().strip().rstrip("!.")
        repeat_words = ("ещё", "еще", "ещё раз", "еще раз", "повтори", "repeat",
                        "ещё добавь", "еще добавь", "ещё убавь", "еще убавь")
        if lower in repeat_words and self._last_action:
            tool_name, args = self._last_action
            logger.info("Repeat last action: %s(%s)", tool_name, args)
            result = self._tools.execute(tool_name, args)
            if result.success:
                return (result.output, False)
            logger.warning("Repeat tool %s failed: %s", tool_name, result.error)
            return (f"❌ {_safe_tool_error(result.error)}", False)

        # Быстрый фильтр: однозначные яркость/громкость слова
        # (защита от стохастических ошибок малой модели)
        quick = self._quick_brightness_volume(lower)
        if quick:
            tool_name, args = quick
            logger.info("Quick filter: %s(%s)", tool_name, args)
            result = self._tools.execute(tool_name, args)
            if tool_name not in ("respond", "ask_full_llm"):
                self._last_action = (tool_name, args)
            self._last_user_input = user_input
            if not result.success:
                logger.warning("Quick tool %s failed: %s", tool_name, result.error)
            return (result.output if result.success else f"❌ {_safe_tool_error(result.error)}", False)

        # Быстрый фильтр: "закрой/убей X" → kill_process
        m_kill = re.match(r"(?:закрой|убей|убить|завершить?|kill)\s+(.+)", lower)
        if m_kill:
            app = m_kill.group(1).strip()
            # Маппинг русских названий → имя процесса
            _kill_map = {
                "хром": "chrome", "хромиум": "chromium", "гугл хром": "chrome",
                "фаерфокс": "firefox", "лису": "firefox",
                "телеграм": "telegram", "телегу": "telegram", "тг": "telegram",
            }
            proc = _kill_map.get(app, app)
            if any(c in proc for c in ';&|`$(){}[]<>!\n\r\t\\'):
                return ("❌ Недопустимое имя процесса", False)
            tool_name, args = "kill_process", {"name": proc}
            logger.info("Quick kill: %s(%s)", tool_name, args)
            result = self._tools.execute(tool_name, args)
            self._last_action = (tool_name, args)
            self._last_user_input = user_input
            if not result.success:
                logger.warning("Kill tool failed: %s", result.error)
            return (result.output if result.success else f"❌ {_safe_tool_error(result.error)}", False)

        # Строим промпт в формате Qwen chat с контекстом
        prompt = self._build_prompt(user_input)
        
        start = time.time()
        
        try:
            response = self._model(
                prompt,
                max_tokens=MINI_PROFILE.max_tokens,
                temperature=MINI_PROFILE.temperature,
                top_p=MINI_PROFILE.top_p,
                repeat_penalty=MINI_PROFILE.repeat_penalty,
                stop=["<|im_end|>", "<|endoftext|>", "\n\nПользователь:", "\n\nUser:"],
            )
            
            raw = response["choices"][0]["text"].strip()
            elapsed = time.time() - start
            logger.info("Mini LLM response (%0.1fs): %s", elapsed, raw[:200])
            
            # Парсим вызов функции
            tool_call = parse_tool_call(raw)
            
            if tool_call:
                tool_name, args = tool_call
                logger.info("Tool call: %s(%s)", tool_name, args)
                
                # Выполняем инструмент
                result = self._tools.execute(tool_name, args)
                
                # NOTE: open_app web fallback now built into ToolRegistry._tool_open_app
                
                if result.needs_full_llm:
                    # Эскалация на тяжёлую модель
                    return (result.output or user_input, True)
                
                # Сохраняем последнее действие (кроме respond и ask_full_llm)
                if tool_name not in ("respond", "ask_full_llm"):
                    self._last_action = (tool_name, args)
                self._last_user_input = user_input
                
                if result.success:
                    return (result.output, False)
                else:
                    logger.warning("Tool %s failed: %s", tool_name, result.error)
                    return (f"❌ {_safe_tool_error(result.error)}", False)
            else:
                # Модель не вернула JSON.
                # Код / длинный текст / сложный ответ → эскалация.
                # Короткий ответ (приветствие) → вернуть.
                word_count = len(raw.split())
                has_code = '```' in raw or 'def ' in raw or 'import ' in raw or 'class ' in raw
                if has_code or word_count > 20:
                    logger.info("Mini returned code/long text — escalating to full LLM")
                    return (user_input, True)
                else:
                    logger.info("Mini returned short text (%d words) — treating as respond", word_count)
                    return (raw, False)
                
        except Exception as e:
            logger.error("Mini LLM error: %s", e)
            return (user_input, True)

    def _build_prompt(self, user_input: str) -> str:
        """Строит промпт в формате Qwen/ChatML с контекстом."""
        # Контекст последнего действия — чтобы модель понимала "ещё"
        context_hint = ""
        if self._last_action:
            tool_name, args = self._last_action
            context_hint = f"\n[Контекст: последнее действие было {tool_name}({json.dumps(args, ensure_ascii=False)})]"

        # Qwen2.5 использует ChatML формат
        return (
            f"<|im_start|>system\n{self._system_prompt}{context_hint}<|im_end|>\n"
            f"<|im_start|>user\n{user_input}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
