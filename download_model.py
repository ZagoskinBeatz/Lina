#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для скачивания GGUF моделей для Lina.

Архитектура: Dual Model (Qwen3.5).
  • mini — Qwen3.5-0.8B-BF16 (~1.5 GB) — быстрые ответы, function-calling.
  • full — Qwen3.5-4B-Q8_0  (~4.5 GB) — сложный анализ, длинные ответы.

Особенности скачивания:
  • Скачивание возобновляется (HTTP Range), если соединение оборвалось.
  • Файл пишется во временный *.part и атомарно переименовывается
    в финальное имя только после успешного завершения.
  • Имена файлов на диске жёстко совпадают с тем, что ожидает lina/config.py.
  • Перед стартом делается HEAD-запрос: проверяем доступность и размер.
  • Несколько ретраев на сетевые сбои.

Использование:
    python download_model.py            # интерактивное меню
    python download_model.py --mini     # скачать mini
    python download_model.py --full     # скачать full
    python download_model.py --all      # скачать обе
    python download_model.py --check    # проверить локальные файлы
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Конфигурация моделей ──────────────────────────────────────────────────────

MODELS_DIR = Path(__file__).resolve().parent / "models"


@dataclass(frozen=True)
class ModelSpec:
    tier: str                # "mini" | "full"
    name: str                # человекочитаемое имя
    filename: str            # имя файла на диске (совпадает с config.py)
    subdir: str              # подпапка внутри models/
    url: str                 # прямая ссылка на GGUF
    size_gb: float           # ожидаемый размер на диске
    ram_gb: float            # ожидаемое потребление RAM
    desc: str                # назначение

    @property
    def path(self) -> Path:
        return MODELS_DIR / self.subdir / self.filename


MODELS: dict[str, ModelSpec] = {
    "mini": ModelSpec(
        tier="mini",
        name="Qwen3.5 0.8B Instruct (BF16)",
        filename="Qwen3.5-0.8B-BF16.gguf",
        subdir="mini",
        url=(
            "https://huggingface.co/bartowski/Qwen_Qwen3.5-0.8B-GGUF/"
            "resolve/main/Qwen_Qwen3.5-0.8B-bf16.gguf"
        ),
        size_gb=1.5,
        ram_gb=2.0,
        desc="Быстрые ответы, function-calling, классификация интента.",
    ),
    "full": ModelSpec(
        tier="full",
        name="Qwen3.5 4B Instruct (Q8_0)",
        filename="Qwen3.5-4B-Q8_0.gguf",
        subdir="full",
        url=(
            "https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF/"
            "resolve/main/Qwen_Qwen3.5-4B-Q8_0.gguf"
        ),
        size_gb=4.5,
        ram_gb=6.0,
        desc="Сложный анализ, длинные ответы, лучшее качество русского.",
    ),
}

USER_AGENT = "lina-downloader/1.0"
CHUNK_SIZE = 1024 * 1024            # 1 MiB
MAX_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = 3.0


# ── Утилиты сети ──────────────────────────────────────────────────────────────


def _open(url: str, *, range_start: int = 0, timeout: int = 30):
    """Открывает URL с правильными заголовками. Поддерживает Range для resume."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if range_start > 0:
        req.add_header("Range", f"bytes={range_start}-")
    return urllib.request.urlopen(req, timeout=timeout)


def _remote_size(url: str) -> Optional[int]:
    """HEAD-запрос: размер удалённого файла в байтах. None — если не удалось."""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
    except Exception:
        return None


def _format_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024:.0f} KB"


def _draw_progress(downloaded: int, total: int, started: float) -> None:
    bar_len = 36
    if total > 0:
        percent = min(100.0, downloaded * 100.0 / total)
        filled = int(bar_len * percent / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        head = f"[{bar}] {percent:5.1f}%"
    else:
        head = "[downloading...]"

    elapsed = max(0.001, time.monotonic() - started)
    speed = downloaded / elapsed
    eta = ""
    if total > 0 and speed > 0:
        remaining = max(0, total - downloaded)
        eta_s = int(remaining / speed)
        eta = f"  ETA {eta_s // 60:02d}:{eta_s % 60:02d}"

    line = (
        f"\r  {head}  "
        f"{_format_bytes(downloaded)}/{_format_bytes(total) if total else '?'}  "
        f"{_format_bytes(int(speed))}/s{eta}"
    )
    sys.stdout.write(line + " " * 4)
    sys.stdout.flush()


# ── Скачивание ────────────────────────────────────────────────────────────────


def _download_attempt(url: str, dest: Path, *, expected_size: Optional[int]) -> bool:
    """Один проход скачивания (с поддержкой resume через .part)."""
    part = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)

    resume_from = part.stat().st_size if part.exists() else 0
    if expected_size and resume_from >= expected_size:
        # .part уже полный — просто переименуем
        part.replace(dest)
        return True

    started = time.monotonic()
    mode = "ab" if resume_from > 0 else "wb"
    if resume_from > 0:
        print(f"  ↻ Возобновляем с {_format_bytes(resume_from)}")

    try:
        with _open(url, range_start=resume_from) as resp:
            # Если сервер не поддержал Range — начинаем сначала
            if resume_from > 0 and resp.status == 200:
                resume_from = 0
                mode = "wb"

            content_length = resp.headers.get("Content-Length")
            if content_length is not None:
                content_length = int(content_length)
                total = resume_from + content_length
            else:
                total = expected_size or 0

            downloaded = resume_from
            with open(part, mode) as fout:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    fout.write(chunk)
                    downloaded += len(chunk)
                    _draw_progress(downloaded, total, started)
        sys.stdout.write("\n")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        sys.stdout.write("\n")
        print(f"  ⚠ Сетевая ошибка: {exc}")
        return False

    # Проверка размера
    actual = part.stat().st_size
    if expected_size and actual != expected_size:
        print(
            f"  ⚠ Размер не совпал: получено {actual} B, ожидалось {expected_size} B"
        )
        return False

    part.replace(dest)
    return True


def download_with_resume(url: str, dest: Path) -> bool:
    """Скачивает url → dest с ретраями, прогрессом и атомарным rename."""
    expected = _remote_size(url)
    if expected:
        print(f"  📦 Размер: {_format_bytes(expected)}")
    else:
        print("  ⚠ Размер сервер не сообщил — продолжаю без проверки.")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1:
            print(f"  ↻ Попытка {attempt}/{MAX_ATTEMPTS} через {RETRY_BACKOFF_SECONDS}s...")
            time.sleep(RETRY_BACKOFF_SECONDS)
        if _download_attempt(url, dest, expected_size=expected):
            return True

    print("\n❌ Не удалось скачать после нескольких попыток.")
    print("Можно скачать вручную:")
    print(f"  curl -L -o '{dest}' '{url}'")
    return False


# ── Действия ──────────────────────────────────────────────────────────────────


def _exists_ok(spec: ModelSpec) -> bool:
    """Файл есть и его размер похож на правду (>= 90% от заявленного)."""
    if not spec.path.exists():
        return False
    actual_gb = spec.path.stat().st_size / 1024 ** 3
    return actual_gb >= spec.size_gb * 0.9


def download_model(tier: str, *, force: bool = False) -> bool:
    spec = MODELS[tier]
    icon = "🟢" if tier == "mini" else "🔵"

    print(f"\n{icon} {spec.name}")
    print(f"  Файл:    {spec.path}")
    print(f"  Размер:  ~{spec.size_gb:.1f} GB")
    print(f"  RAM:     ~{spec.ram_gb:.1f} GB")
    print(f"  Назначение: {spec.desc}")

    if _exists_ok(spec) and not force:
        size_mb = spec.path.stat().st_size / 1024 ** 2
        print(f"\n✅ Уже скачано: {spec.path} ({size_mb:.0f} MB)")
        try:
            answer = input("Перескачать? (y/N): ").strip().lower()
        except EOFError:
            answer = "n"
        if answer != "y":
            return True
        spec.path.unlink(missing_ok=True)

    ok = download_with_resume(spec.url, spec.path)
    if ok:
        print(f"✅ Готово: {spec.path}")
    return ok


def check_local() -> int:
    """Печатает состояние локальных файлов. Возвращает количество отсутствующих."""
    print("\n📁 Локальные модели:")
    missing = 0
    for tier, spec in MODELS.items():
        icon = "🟢" if tier == "mini" else "🔵"
        if _exists_ok(spec):
            size_mb = spec.path.stat().st_size / 1024 ** 2
            print(f"  {icon} {tier:>4}  ✅  {spec.path}  ({size_mb:.0f} MB)")
        elif spec.path.exists():
            size_mb = spec.path.stat().st_size / 1024 ** 2
            print(
                f"  {icon} {tier:>4}  ⚠  {spec.path}  "
                f"({size_mb:.0f} MB, ожидалось ~{spec.size_gb * 1024:.0f} MB)"
            )
            missing += 1
        else:
            print(f"  {icon} {tier:>4}  ❌  {spec.path}  (не скачано)")
            missing += 1
    print()
    return missing


# ── CLI ───────────────────────────────────────────────────────────────────────


def show_menu() -> None:
    print("=" * 60)
    print("  Lina — Скачивание GGUF моделей (Qwen3.5)")
    print("=" * 60)
    check_local()

    print("Выберите действие:")
    print("  1) Скачать mini  (Qwen3.5-0.8B-BF16, ~1.5 GB)")
    print("  2) Скачать full  (Qwen3.5-4B-Q8_0,  ~4.5 GB)")
    print("  3) Скачать обе модели")
    print("  q) Выход")
    print()

    try:
        choice = input("Ваш выбор: ").strip().lower()
    except EOFError:
        return

    if choice == "1":
        download_model("mini")
    elif choice == "2":
        download_model("full")
    elif choice == "3":
        if download_model("mini"):
            download_model("full")
    elif choice in ("q", "й", "exit", "quit"):
        print("Отмена.")
    else:
        print("Неверный выбор.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Скачивание GGUF моделей для Lina (Qwen3.5)"
    )
    parser.add_argument("--mini", action="store_true",
                        help="Скачать mini (Qwen3.5-0.8B-BF16)")
    parser.add_argument("--full", action="store_true",
                        help="Скачать full (Qwen3.5-4B-Q8_0)")
    parser.add_argument("--all", action="store_true",
                        help="Скачать обе модели")
    parser.add_argument("--check", action="store_true",
                        help="Проверить, что уже скачано")
    parser.add_argument("--force", action="store_true",
                        help="Перескачать даже если файл уже есть")
    args = parser.parse_args()

    if args.check:
        return 1 if check_local() else 0

    if args.all:
        ok = download_model("mini", force=args.force)
        ok = download_model("full", force=args.force) and ok
        return 0 if ok else 1

    if args.mini:
        return 0 if download_model("mini", force=args.force) else 1

    if args.full:
        return 0 if download_model("full", force=args.force) else 1

    show_menu()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n⏹ Прервано пользователем.")
        sys.exit(130)
