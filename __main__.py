"""
Lina — ИИ-ассистент для Linux.

Позволяет запускать Lina через: python -m lina [args]

Примеры:
    python -m lina                    # Интерактивный REPL
    python -m lina --gui              # Qt десктопный GUI
    python -m lina --daemon           # Фоновый режим (systemd)
    python -m lina --first-run        # Мастер первого запуска
    python -m lina --oneshot 'привет' # Одноразовый запрос
"""

from __future__ import annotations

import sys


def main():
    from lina.core.cli import main as cli_main
    sys.exit(cli_main())


if __name__ == "__main__":
    main()
