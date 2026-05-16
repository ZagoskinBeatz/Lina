#!/usr/bin/env fish
# ─────────────────────────────────────────────────────────────────────
# Lina — Fish shell launcher (безопасный запуск).
#
# Решает проблему: fish интерпретирует emoji (🟢, ⚠) в stdout
# Python-программы как команды, если вывод проходит через eval/source.
#
# Этот скрипт:
#   1. Активирует Python venv (если найден)
#   2. Выставляет PYTHONUNBUFFERED=1 для корректного flush
#   3. Запускает lina.py с корректной изоляцией stdout
#   4. Все аргументы пробрасываются как есть
#
# Использование:
#   ./lina.fish                    # Интерактивный REPL
#   ./lina.fish --cv               # С Computer Vision
#   ./lina.fish --quiet            # Без emoji (fish-safe)
#   ./lina.fish --oneshot 'привет' # Одноразовый запрос
#
# Установка:
#   chmod +x lina.fish
#   # Опционально — создать alias:
#   alias lina='~/Документы/AI/lina/lina.fish'
#   funcsave lina
# ─────────────────────────────────────────────────────────────────────

# Определяем директорию скрипта
set -l SCRIPT_DIR (dirname (status filename))
set -l PROJECT_ROOT (dirname "$SCRIPT_DIR")

# Ищем venv
set -l VENV_PYTHON ""
if test -f "$PROJECT_ROOT/.venv/bin/python"
    set VENV_PYTHON "$PROJECT_ROOT/.venv/bin/python"
else if test -f "$SCRIPT_DIR/../.venv/bin/python"
    set VENV_PYTHON "$SCRIPT_DIR/../.venv/bin/python"
else if test -f "$SCRIPT_DIR/.venv/bin/python"
    set VENV_PYTHON "$SCRIPT_DIR/.venv/bin/python"
end

# Если venv не найден — используем системный Python
if test -z "$VENV_PYTHON"
    set VENV_PYTHON (command -v python3; or command -v python)
    if test -z "$VENV_PYTHON"
        echo "[ERR] Python не найден. Установите Python 3.10+."
        exit 1
    end
    echo "[WARN] venv не найден, используется системный Python: $VENV_PYTHON"
    echo "[WARN] llama-cpp-python может быть недоступен."
    echo "[WARN] Создайте venv: python3 -m venv $PROJECT_ROOT/.venv"
end

# Окружение
set -x PYTHONUNBUFFERED 1
set -x LINA_OUTPUT_MODE TTY

# Запуск Lina — ПРЯМОЕ ВЫПОЛНЕНИЕ (без eval/source!)
# Именно это предотвращает интерпретацию stdout как команд fish.
exec $VENV_PYTHON "$SCRIPT_DIR/lina.py" $argv
