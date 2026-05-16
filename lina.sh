#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Lina — Universal shell launcher (bash/zsh/fish compatible).
#
# Автоматически активирует venv и запускает Lina
# с корректной изоляцией stdout.
#
# Использование:
#   ./lina.sh                    # Интерактивный REPL
#   ./lina.sh --cv               # С Computer Vision
#   ./lina.sh --quiet            # Без emoji
#   ./lina.sh --oneshot 'привет' # Одноразовый запрос
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Ищем venv Python
VENV_PYTHON=""
for candidate in \
    "$PROJECT_ROOT/.venv/bin/python" \
    "$SCRIPT_DIR/../.venv/bin/python" \
    "$SCRIPT_DIR/.venv/bin/python" \
; do
    if [ -x "$candidate" ]; then
        VENV_PYTHON="$candidate"
        break
    fi
done

# Fallback на системный Python
if [ -z "$VENV_PYTHON" ]; then
    VENV_PYTHON="$(command -v python3 || command -v python || true)"
    if [ -z "$VENV_PYTHON" ]; then
        echo "[ERR] Python не найден. Установите Python 3.10+." >&2
        exit 1
    fi
    echo "[WARN] venv не найден, используется: $VENV_PYTHON" >&2
fi

# Окружение
export PYTHONUNBUFFERED=1
export LINA_OUTPUT_MODE=TTY

# Запуск
exec "$VENV_PYTHON" "$SCRIPT_DIR/lina.py" "$@"
