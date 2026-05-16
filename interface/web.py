"""
Lina — Веб-интерфейс (REST API + простой UI).

Предоставляет HTTP API для:
  - Отправки команд
  - Получения статуса
  - Управления LLM
  - Поиска по базе знаний

Доступ по локальной сети для управления с других устройств.

Запуск:
  python -m lina.interface.web
  или через CLI: python lina.py --web

Зависимости: встроенный http.server (без Flask).
"""

import json
import logging
import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Optional, Callable

_log = logging.getLogger(__name__)

MAX_BODY_SIZE = 65536  # 64 KB

# Добавляем корень проекта в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ── HTML-шаблон (встроенный, без внешних файлов) ──

WEB_UI_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lina AI — Веб-интерфейс</title>
<style>
  :root {
    --bg: #1a1b26; --fg: #c0caf5; --accent: #7aa2f7;
    --green: #9ece6a; --red: #f7768e; --yellow: #e0af68;
    --surface: #24283b; --border: #3b4261;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    background: var(--bg); color: var(--fg);
    display: flex; flex-direction: column; height: 100vh;
  }
  header {
    background: var(--surface); padding: 12px 20px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 16px;
  }
  header h1 { font-size: 1.2em; color: var(--accent); }
  #status-bar {
    font-size: 0.8em; color: var(--yellow);
    margin-left: auto;
  }
  #output {
    flex: 1; overflow-y: auto; padding: 16px;
    font-size: 0.9em; white-space: pre-wrap; line-height: 1.6;
  }
  .msg { margin-bottom: 12px; padding: 8px 12px; border-radius: 6px; }
  .msg-user { background: var(--surface); border-left: 3px solid var(--accent); }
  .msg-lina { background: #1e2030; border-left: 3px solid var(--green); }
  .msg-error { background: #2d202a; border-left: 3px solid var(--red); }
  .msg-label { font-size: 0.75em; color: var(--accent); margin-bottom: 4px; }
  #input-area {
    display: flex; padding: 12px; background: var(--surface);
    border-top: 1px solid var(--border);
  }
  #cmd-input {
    flex: 1; padding: 10px 14px; font-size: 1em;
    background: var(--bg); color: var(--fg); border: 1px solid var(--border);
    border-radius: 6px; outline: none;
    font-family: inherit;
  }
  #cmd-input:focus { border-color: var(--accent); }
  #send-btn {
    margin-left: 8px; padding: 10px 20px; background: var(--accent);
    color: var(--bg); border: none; border-radius: 6px;
    cursor: pointer; font-weight: bold; font-family: inherit;
  }
  #send-btn:hover { opacity: 0.85; }
  .quick-btns {
    display: flex; gap: 6px; padding: 8px 16px; flex-wrap: wrap;
    background: var(--surface); border-top: 1px solid var(--border);
  }
  .qbtn {
    padding: 4px 10px; font-size: 0.75em; background: var(--bg);
    color: var(--fg); border: 1px solid var(--border);
    border-radius: 4px; cursor: pointer;
    font-family: inherit;
  }
  .qbtn:hover { border-color: var(--accent); color: var(--accent); }
</style>
</head>
<body>
<header>
  <h1>🤖 Lina AI</h1>
  <span id="status-bar">⬜ Загрузка...</span>
</header>

<div id="output"></div>

<div class="quick-btns">
  <button class="qbtn" onclick="send('/help')"> /help</button>
  <button class="qbtn" onclick="send('/статус')">📊 Статус</button>
  <button class="qbtn" onclick="send('статус системы')">💻 Система</button>
  <button class="qbtn" onclick="send('статус модели')">🤖 LLM</button>
  <button class="qbtn" onclick="send('процессы')">📈 Процессы</button>
  <button class="qbtn" onclick="send('статус базы знаний')">📚 RAG</button>
  <button class="qbtn" onclick="send('история')">📜 История</button>
  <button class="qbtn" onclick="send('макросы')">📋 Макросы</button>
</div>

<div id="input-area">
  <input id="cmd-input" type="text" placeholder="Введите команду..."
    onkeydown="if(event.key==='Enter')sendInput()" autofocus />
  <button id="send-btn" onclick="sendInput()">▶ Отправить</button>
</div>

<script>
const output = document.getElementById('output');
const input = document.getElementById('cmd-input');
const statusBar = document.getElementById('status-bar');

function addMsg(text, cls, label) {
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  if (label) {
    const lbl = document.createElement('div');
    lbl.className = 'msg-label';
    lbl.textContent = label;
    div.appendChild(lbl);
  }
  const content = document.createElement('div');
  content.textContent = text;
  div.appendChild(content);
  output.appendChild(div);
  output.scrollTop = output.scrollHeight;
}

async function send(cmd) {
  addMsg(cmd, 'msg-user', '🧑 Вы');
  try {
    const res = await fetch('/api/command', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({command: cmd})
    });
    const data = await res.json();
    if (data.response) {
      addMsg(data.response, 'msg-lina', '🤖 Lina');
    }
    if (data.error) {
      addMsg(data.error, 'msg-error', '❌ Ошибка');
    }
  } catch(e) {
    addMsg('Ошибка соединения: ' + e.message, 'msg-error', '❌');
  }
}

function sendInput() {
  const cmd = input.value.trim();
  if (!cmd) return;
  send(cmd);
  input.value = '';
}

async function updateStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    statusBar.textContent = data.status_bar || '⬜';
  } catch(e) {
    statusBar.textContent = '❌ Нет связи';
  }
}

setInterval(updateStatus, 5000);
updateStatus();
addMsg('Lina веб-интерфейс подключен. Введите команду или нажмите кнопку.', 'msg-lina', '🤖 Lina');
</script>
</body>
</html>"""


class LinaWebHandler(BaseHTTPRequestHandler):
    """HTTP-обработчик для Lina API."""

    # Ссылки на Commander (устанавливаются при запуске сервера)
    commander = None
    notifier = None

    def log_message(self, format, *args):
        """Тихое логирование (не засоряем консоль)."""
        pass

    def _send_json(self, data: dict, status: int = 200) -> None:
        """Отправляет JSON-ответ."""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "http://localhost:8585")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        """Отправляет HTML-ответ."""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            self._send_html(WEB_UI_HTML)

        elif parsed.path == "/api/status":
            self._handle_status()

        elif parsed.path == "/api/health":
            self._send_json({"status": "ok", "timestamp": time.time()})

        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/command":
            self._handle_command()
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "http://localhost:8585")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _handle_command(self) -> None:
        """Обрабатывает POST /api/command."""
        if not self.commander:
            self._send_json({"error": "Commander not initialized"}, 500)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY_SIZE:
                self._send_json({"error": "Request too large"}, 413)
                return
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body)
            command = data.get("command", "").strip()

            if not command:
                self._send_json({"error": "Empty command"}, 400)
                return

            # Блокируем /exit через веб
            if command.lower() in ("/выход", "/exit", "/quit"):
                self._send_json({"response": "⛔ Выход через веб-интерфейс отключён."})
                return

            response = self.commander.process(command)

            if response == "__EXIT__":
                self._send_json({"response": "⛔ Выход через веб-интерфейс отключён."})
                return

            self._send_json({"response": response, "timestamp": time.time()})

        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, 400)
        except Exception as e:
            _log.error("Web API command error", exc_info=True)
            self._send_json({"error": "Внутренняя ошибка сервера."}, 500)

    def _handle_status(self) -> None:
        """Обрабатывает GET /api/status."""
        try:
            from lina.interface.notify import StatusWidget

            llm_tier = None
            rag_chunks = 0
            cpu_pct = 0.0
            ram_pct = 0.0

            if self.commander:
                if self.commander.llm.is_loaded:
                    llm_tier = self.commander.llm.active_tier

                try:
                    stats = self.commander.indexer.get_stats()
                    rag_chunks = stats.get("total_chunks", 0)
                except Exception:
                    pass

                try:
                    mem = self.commander.monitor.get_memory_usage()
                    cpu_pct = self.commander.monitor.get_cpu_usage()
                    ram_pct = mem.get("percent", 0)
                except Exception:
                    pass

            status_bar = StatusWidget.render(
                llm_tier=llm_tier,
                rag_chunks=rag_chunks,
                cpu_percent=cpu_pct,
                ram_percent=ram_pct,
                web_active=True,
            )

            self._send_json({
                "status_bar": status_bar,
                "llm_tier": llm_tier,
                "rag_chunks": rag_chunks,
                "cpu_percent": cpu_pct,
                "ram_percent": ram_pct,
            })
        except Exception as e:
            _log.error("Web API status error", exc_info=True)
            self._send_json({"error": "Внутренняя ошибка сервера."}, 500)


class LinaWebServer:
    """
    Веб-сервер Lina.

    Запускается в отдельном потоке, предоставляет REST API
    и простой веб-интерфейс.
    """

    def __init__(
        self,
        commander=None,
        host: str = "127.0.0.1",
        port: int = 8585,
    ):
        self.host = host
        self.port = port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Устанавливаем commander в обработчик
        LinaWebHandler.commander = commander

    def start(self) -> bool:
        """Запускает веб-сервер в фоновом потоке."""
        if self._running:
            return True

        try:
            self._server = HTTPServer((self.host, self.port), LinaWebHandler)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
            )
            self._thread.start()
            self._running = True
            return True
        except OSError as e:
            print(f"❌ Не удалось запустить веб-сервер: {e}")
            return False

    def stop(self) -> None:
        """Останавливает веб-сервер."""
        if self._server:
            self._server.shutdown()
            self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def main():
    """Запуск веб-сервера как самостоятельного процесса."""
    from lina.shell.commander import Commander

    commander = Commander()
    server = LinaWebServer(commander=commander, port=8585)

    print(f"🌐 Lina веб-интерфейс запущен: http://localhost:8585")
    print(f"   API: http://localhost:8585/api/command")
    print(f"   Статус: http://localhost:8585/api/status")
    print(f"   Нажмите Ctrl+C для остановки.")

    if server.start():
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Веб-сервер остановлен.")
            server.stop()
    else:
        print("❌ Не удалось запустить.")


if __name__ == "__main__":
    main()
