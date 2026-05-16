#!/usr/bin/env python3
"""
Скрипт для скачивания GGUF моделей для Lina.

Phase 20.1 — Single Heavy Model.
Скачивает полную модель (Qwen2.5 7B Instruct, ~4.7 GB).

Использование:
    python download_model.py            # Скачать полную модель
    python download_model.py --full     # Скачать полную модель
"""

import sys
import argparse
import urllib.request
from pathlib import Path

# ── Конфигурация моделей ──

MODELS_DIR = Path(__file__).parent / "models"

MODELS = {
    "mini": {
        "name": "Phi-3 Mini 3.8B Instruct (Q4_K_M)",
        "size": "~2.2 GB",
        "ram": "~3 GB",
        "url": (
            "https://huggingface.co/bartowski/Phi-3.1-mini-4k-instruct-GGUF/"
            "resolve/main/Phi-3.1-mini-4k-instruct-Q4_K_M.gguf"
        ),
        "path": MODELS_DIR / "mini" / "mini.gguf",
        "desc": "Быстрые ответы, классификация, лёгкие задачи",
    },
    "full": {
        "name": "Qwen2.5 7B Instruct (Q4_K_M)",
        "size": "~4.7 GB",
        "ram": "~6.0 GB",
        "url": (
            "https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/"
            "resolve/main/Qwen2.5-7B-Instruct-Q4_K_M.gguf"
        ),
        "path": MODELS_DIR / "full" / "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        "desc": "Сильнее на русском, лучше держит инструкции и длинные ответы",
    },
}


def download_with_progress(url: str, dest: str):
    """Скачивает файл с прогресс-баром."""
    print(f"📥 Скачивание: {url}")
    print(f"📁 Сохранение: {dest}")

    def reporthook(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            percent = min(100, downloaded * 100 / total_size)
            mb_down = downloaded / 1024 / 1024
            mb_total = total_size / 1024 / 1024
            bar_len = 40
            filled = int(bar_len * percent / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            sys.stdout.write(
                f"\r  [{bar}] {percent:.1f}%  {mb_down:.1f}/{mb_total:.1f} MB"
            )
            sys.stdout.flush()

    try:
        urllib.request.urlretrieve(url, dest, reporthook=reporthook)
        print(f"\n✅ Модель скачана: {dest}")
        return True
    except Exception as e:
        print(f"\n❌ Ошибка скачивания: {e}")
        print(
            "\nМожно скачать вручную:\n"
            f"  wget {url} -O {dest}\n"
            "или\n"
            f"  curl -L {url} -o {dest}"
        )
        return False


def download_model(tier: str = "full") -> bool:
    """Скачивает модель указанного типа."""
    info = MODELS[tier]
    model_path = info["path"]
    label = "🟢 Mini" if tier == "mini" else "🔵 Full"

    print(f"\n{label}: {info['name']}")
    print(f"  Размер: {info['size']}")
    print(f"  RAM:    {info['ram']}")
    print(f"  Назначение: {info['desc']}")
    print()

    if model_path.exists():
        size_mb = model_path.stat().st_size / 1024 / 1024
        print(f"✅ Модель уже существует: {model_path} ({size_mb:.0f} MB)")
        answer = input("Перескачать? (y/n): ").strip().lower()
        if answer != "y":
            print("  Пропущено.")
            return True

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return download_with_progress(info["url"], str(model_path))


def show_menu():
    """Показывает интерактивное меню."""
    print("=" * 55)
    print("  Lina — Скачивание моделей LLM")
    print("=" * 55)
    print()

    for tier, info in MODELS.items():
        icon = "🟢" if tier == "mini" else "🔵"
        tag = tier.upper()
        exists = "✅" if info["path"].exists() else "❌"
        print(f"  {icon} {tag}:  {info['name']}")
        print(f"        Размер: {info['size']},  RAM: {info['ram']}")
        print(f"        Файл:   {exists} {info['path']}")
        print()

    print("Выберите действие:")
    print("  1) Скачать mini  модель  (быстрая, ~2.2 GB)")
    print("  2) Скачать full  модель  (мощная,  ~4.7 GB)")
    print("  3) Скачать обе модели")
    print("  q) Выход")
    print()

    choice = input("Ваш выбор: ").strip().lower()

    if choice == "1":
        download_model("mini")
    elif choice == "2":
        download_model("full")
    elif choice == "3":
        download_model("mini")
        download_model("full")
    elif choice in ("q", "й"):
        print("Отмена.")
    else:
        print("Неверный выбор.")


def main():
    parser = argparse.ArgumentParser(
        description="Скачивание GGUF моделей для Lina"
    )
    parser.add_argument("--mini", action="store_true", help="Скачать mini модель (Phi-3, ~2.2 GB)")
    parser.add_argument("--full", action="store_true", help="Скачать full модель (Qwen2.5 7B, ~4.7 GB)")
    parser.add_argument("--all", action="store_true", help="Скачать обе модели")
    args = parser.parse_args()

    if args.all:
        download_model("mini")
        download_model("full")
    elif args.mini:
        download_model("mini")
    elif args.full:
        download_model("full")
    else:
        show_menu()


if __name__ == "__main__":
    main()
