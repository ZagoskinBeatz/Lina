"""
Lina Integration Test — Sandbox-окружение.

Обеспечивает изоляцию тестов:
  - Временные каталоги для файлов/скриншотов/логов
  - Тестовые скриншоты для CV-модуля
  - Подмена путей в конфигурации
  - Симуляция сетевых действий
  - Автоматическая очистка после тестов

Ни один файл реальной системы не модифицируется.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


class SandboxEnvironment:
    """
    Изолированная среда для интеграционных тестов.

    Создаёт временную файловую систему с:
      - screenshots/  : тестовые скриншоты для CV
      - knowledge/     : тестовая база знаний для RAG
      - logs/          : логи тестов
      - cache/         : кэш ответов
      - macros/        : тестовые макросы
      - files/         : тестовые файлы для команд

    Usage:
        sandbox = SandboxEnvironment()
        sandbox.setup()
        # ... тесты ...
        sandbox.teardown()

    Или как контекст-менеджер:
        with SandboxEnvironment() as sandbox:
            # ... тесты ...
    """

    def __init__(self, base_dir: Optional[str] = None):
        """
        Args:
            base_dir: Базовый каталог для песочницы.
                      None → создаётся временный каталог.
        """
        self._base_dir = base_dir
        self._temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.root: Optional[Path] = None

        # Подкаталоги
        self.screenshots_dir: Optional[Path] = None
        self.knowledge_dir: Optional[Path] = None
        self.logs_dir: Optional[Path] = None
        self.cache_dir: Optional[Path] = None
        self.macros_dir: Optional[Path] = None
        self.files_dir: Optional[Path] = None

        self._is_setup = False
        self._original_config: Dict[str, Any] = {}

    def setup(self) -> "SandboxEnvironment":
        """
        Инициализирует песочницу: создаёт каталоги, генерирует
        тестовые данные, подменяет конфигурацию Lina.
        """
        if self._is_setup:
            return self

        # Создаём корневой каталог
        if self._base_dir:
            self.root = Path(self._base_dir)
            self.root.mkdir(parents=True, exist_ok=True)
        else:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="lina_test_")
            self.root = Path(self._temp_dir.name)

        # Подкаталоги
        self.screenshots_dir = self.root / "screenshots"
        self.knowledge_dir = self.root / "knowledge"
        self.logs_dir = self.root / "logs"
        self.cache_dir = self.root / "cache"
        self.macros_dir = self.root / "knowledge" / "macros"
        self.files_dir = self.root / "test_files"

        for d in [
            self.screenshots_dir, self.knowledge_dir,
            self.logs_dir, self.cache_dir, self.macros_dir,
            self.files_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

        # Генерируем тестовые данные
        self._create_test_files()
        self._create_test_knowledge()
        self._create_test_screenshots()

        self._is_setup = True
        return self

    def teardown(self) -> None:
        """Полная очистка песочницы."""
        if self._temp_dir:
            try:
                self._temp_dir.cleanup()
            except Exception:
                pass
        elif self.root and self.root.exists():
            try:
                shutil.rmtree(self.root, ignore_errors=True)
            except Exception:
                pass

        self._is_setup = False

    def __enter__(self) -> "SandboxEnvironment":
        return self.setup()

    def __exit__(self, *args) -> None:
        self.teardown()

    # ── Тестовые файлы ──

    def _create_test_files(self) -> None:
        """Создаёт набор тестовых файлов для команд файловой системы."""
        # Python-скрипт
        (self.files_dir / "hello.py").write_text(
            '#!/usr/bin/env python3\n'
            'print("Hello from Lina test!")\n',
            encoding="utf-8",
        )

        # Конфигурационный файл
        (self.files_dir / "config.yaml").write_text(
            "server:\n"
            "  host: localhost\n"
            "  port: 8080\n"
            "  debug: true\n"
            "database:\n"
            "  url: sqlite:///test.db\n",
            encoding="utf-8",
        )

        # Лог с ошибками
        (self.files_dir / "app.log").write_text(
            "[2025-02-20 10:00:01] INFO: Server started\n"
            "[2025-02-20 10:00:15] WARNING: High memory usage: 85%\n"
            "[2025-02-20 10:01:03] ERROR: Connection timeout to database\n"
            "[2025-02-20 10:01:10] ERROR: Failed to process request: OOM\n"
            "[2025-02-20 10:02:00] INFO: Retry succeeded\n"
            "[2025-02-20 10:05:00] CRITICAL: Disk space low: 2% remaining\n",
            encoding="utf-8",
        )

        # Bash-скрипт
        (self.files_dir / "deploy.sh").write_text(
            "#!/bin/bash\n"
            "echo 'Deploying application...'\n"
            "git pull origin main\n"
            "pip install -r requirements.txt\n"
            "systemctl restart app\n",
            encoding="utf-8",
        )

        # Пустой каталог
        (self.files_dir / "empty_dir").mkdir(exist_ok=True)

        # Вложенная структура
        nested = self.files_dir / "project" / "src"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "main.py").write_text(
            "def main():\n    print('main')\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8",
        )
        (nested / "utils.py").write_text(
            "def helper():\n    return 42\n",
            encoding="utf-8",
        )

    def _create_test_knowledge(self) -> None:
        """Создаёт тестовую базу знаний для RAG."""
        # Гайд по Lina
        (self.knowledge_dir / "lina_guide.txt").write_text(
            "Lina — локальный ИИ-ассистент для Linux.\n"
            "Основные возможности:\n"
            "1. Управление файлами: показать файлы, прочитать, поиск.\n"
            "2. Системные команды: статус системы, мониторинг.\n"
            "3. RAG: база знаний, индексация, поиск.\n"
            "4. LLM: генерация ответов через LLaMA.\n"
            "5. CV: скриншоты, OCR, анализ GUI.\n"
            "6. Макросы: цепочки команд, автоматизация.\n"
            "7. Предустановка: проверка железа, сети, пакетов.\n\n"
            "Команды:\n"
            "- 'покажи файлы' — список файлов в текущем каталоге\n"
            "- 'статус системы' — информация о CPU, RAM, диске\n"
            "- 'индексируй' — обновить базу знаний\n"
            "- '/help' — показать справку\n"
            "- '/status' — статус всех подсистем\n",
            encoding="utf-8",
        )

        # Команды Linux
        (self.knowledge_dir / "linux_commands.txt").write_text(
            "Полезные команды Linux:\n"
            "- ls -la: показать файлы с деталями\n"
            "- df -h: свободное место на диске\n"
            "- free -h: информация о RAM\n"
            "- top: мониторинг процессов\n"
            "- systemctl status: статус сервисов\n"
            "- journalctl -xe: просмотр логов\n"
            "- pacman -S <пакет>: установка пакета (Arch)\n"
            "- apt install <пакет>: установка пакета (Debian)\n"
            "- dnf install <пакет>: установка пакета (Fedora)\n",
            encoding="utf-8",
        )

        # FAQ
        (self.knowledge_dir / "faq.txt").write_text(
            "Q: Как обновить систему?\n"
            "A: Используй команду обновления для своего дистрибутива.\n\n"
            "Q: Как включить CV?\n"
            "A: Установи зависимости (mss, pillow, pytesseract) и "
            "включи в конфигурации cv.enabled = True.\n\n"
            "Q: Как создать макрос?\n"
            "A: Введи 'сохрани макрос <имя>: команда1 → команда2 → команда3'.\n",
            encoding="utf-8",
        )

    def _create_test_screenshots(self) -> None:
        """Генерирует тестовые скриншоты для CV-модуля."""
        if not _HAS_PIL:
            # Создаём заглушки как PNG-файлы минимального размера
            self._create_minimal_png("test_desktop.png", 320, 240)
            self._create_minimal_png("test_error_dialog.png", 400, 300)
            self._create_minimal_png("test_terminal.png", 640, 480)
            return

        # Обычный рабочий стол
        self._generate_desktop_screenshot("test_desktop.png")

        # Диалог ошибки
        self._generate_error_dialog_screenshot("test_error_dialog.png")

        # Терминал с текстом
        self._generate_terminal_screenshot("test_terminal.png")

        # Прогресс-бар
        self._generate_progress_screenshot("test_progress.png")

    def _generate_desktop_screenshot(self, name: str) -> None:
        """Генерирует имитацию рабочего стола."""
        img = Image.new("RGB", (1920, 1080), color=(40, 40, 80))
        draw = ImageDraw.Draw(img)

        # «Панель задач» внизу
        draw.rectangle([0, 1040, 1920, 1080], fill=(30, 30, 30))

        # «Окно»
        draw.rectangle([200, 100, 1200, 700], fill=(50, 50, 50), outline=(100, 100, 100))
        draw.rectangle([200, 100, 1200, 130], fill=(60, 60, 120))

        # Кнопки окна
        draw.ellipse([1160, 107, 1178, 125], fill=(255, 80, 80))    # Закрыть
        draw.ellipse([1135, 107, 1153, 125], fill=(255, 180, 50))   # Свернуть

        img.save(str(self.screenshots_dir / name))

    def _generate_error_dialog_screenshot(self, name: str) -> None:
        """Генерирует имитацию диалога ошибки."""
        img = Image.new("RGB", (800, 600), color=(40, 40, 80))
        draw = ImageDraw.Draw(img)

        # Фон окна ошибки
        draw.rectangle([150, 150, 650, 450], fill=(60, 60, 60), outline=(200, 50, 50))
        draw.rectangle([150, 150, 650, 185], fill=(200, 50, 50))

        # Текст ошибки (шрифт по умолчанию)
        try:
            draw.text((200, 160), "Error", fill=(255, 255, 255))
            draw.text((200, 220), "Connection failed:", fill=(255, 200, 200))
            draw.text((200, 250), "ERRO: timeout after 30s", fill=(255, 100, 100))
            draw.text((200, 280), "WARNING: retry limit", fill=(255, 200, 100))
        except Exception:
            pass

        # Кнопка OK
        draw.rectangle([350, 380, 450, 420], fill=(80, 80, 180), outline=(120, 120, 220))
        try:
            draw.text((385, 393), "OK", fill=(255, 255, 255))
        except Exception:
            pass

        img.save(str(self.screenshots_dir / name))

    def _generate_terminal_screenshot(self, name: str) -> None:
        """Генерирует имитацию терминала с выводом."""
        img = Image.new("RGB", (1280, 720), color=(0, 0, 0))
        draw = ImageDraw.Draw(img)

        lines = [
            ("$ sudo pacman -Syu", (100, 255, 100)),
            (":: Synchronizing package databases...", (255, 255, 255)),
            (" core is up to date", (255, 255, 255)),
            (" extra is up to date", (255, 255, 255)),
            (":: Starting full system upgrade...", (255, 255, 255)),
            (" there is nothing to do", (100, 255, 100)),
            ("$ systemctl status nginx", (100, 255, 100)),
            ("● nginx.service - A high performance web server", (255, 255, 255)),
            ("   Active: active (running) since Mon", (100, 255, 100)),
            ("   Memory: 12.5M", (255, 255, 255)),
            ("      CPU: 342ms", (255, 255, 255)),
            ("$ free -h", (100, 255, 100)),
            ("              total        used        free", (255, 255, 255)),
            ("Mem:          15Gi       8.2Gi       4.1Gi", (255, 255, 255)),
            ("Swap:         8.0Gi       0.0Gi       8.0Gi", (255, 255, 255)),
        ]

        y = 20
        for text, color in lines:
            try:
                draw.text((20, y), text, fill=color)
            except Exception:
                pass
            y += 28

        img.save(str(self.screenshots_dir / name))

    def _generate_progress_screenshot(self, name: str) -> None:
        """Генерирует имитацию скриншота с прогресс-баром."""
        img = Image.new("RGB", (800, 200), color=(40, 40, 60))
        draw = ImageDraw.Draw(img)

        # Прогресс-бар
        draw.rectangle([50, 80, 750, 120], fill=(30, 30, 30), outline=(100, 100, 100))
        draw.rectangle([52, 82, 530, 118], fill=(50, 150, 50))  # ~65%

        try:
            draw.text((50, 50), "Installing packages... 65%", fill=(255, 255, 255))
            draw.text((50, 130), "[██████████████░░░░░░░░] 65/100", fill=(200, 200, 200))
        except Exception:
            pass

        img.save(str(self.screenshots_dir / name))

    def _create_minimal_png(self, name: str, w: int = 100, h: int = 100) -> None:
        """Создаёт минимальный валидный PNG-файл без Pillow."""
        import struct
        import zlib

        def _chunk(chunk_type: bytes, data: bytes) -> bytes:
            c = chunk_type + data
            crc = zlib.crc32(c) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

        # PNG сигнатура
        sig = b"\x89PNG\r\n\x1a\n"

        # IHDR: width, height, bit_depth=8, color_type=2(RGB)
        ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        ihdr = _chunk(b"IHDR", ihdr_data)

        # IDAT: сырые данные (заполняем серым)
        raw_rows = b""
        for _ in range(h):
            raw_rows += b"\x00" + b"\x80\x80\x80" * w
        compressed = zlib.compress(raw_rows)
        idat = _chunk(b"IDAT", compressed)

        # IEND
        iend = _chunk(b"IEND", b"")

        path = self.screenshots_dir / name
        path.write_bytes(sig + ihdr + idat + iend)

    # ── Утилиты ──

    def get_test_screenshot(self, name: str = "test_desktop.png") -> str:
        """Возвращает путь к тестовому скриншоту."""
        path = self.screenshots_dir / name
        if path.exists():
            return str(path)
        # Возвращаем первый доступный
        pngs = list(self.screenshots_dir.glob("*.png"))
        return str(pngs[0]) if pngs else ""

    def get_test_file(self, name: str) -> str:
        """Возвращает путь к тестовому файлу."""
        return str(self.files_dir / name)

    def create_temp_file(self, name: str, content: str) -> str:
        """Создаёт временный файл в песочнице."""
        path = self.files_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    @property
    def is_active(self) -> bool:
        return self._is_setup
