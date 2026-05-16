# Инструменты разработки в Linux

## Python

### Установка и управление версиями
```bash
# Системный Python
python3 --version
which python3

# pyenv — управление версиями Python
curl https://pyenv.run | bash
# Добавить в ~/.bashrc / ~/.config/fish/config.fish:
# export PATH="$HOME/.pyenv/bin:$PATH"
# eval "$(pyenv init -)"

pyenv install --list | grep "3\."  # доступные версии
pyenv install 3.12.4
pyenv global 3.12.4                # глобально
pyenv local 3.11.9                 # для текущего каталога
pyenv versions                     # установленные

# uv — быстрый менеджер пакетов (замена pip, venv)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv                            # создать .venv
uv pip install requests            # установить пакет
uv pip compile requirements.in -o requirements.txt
uv pip sync requirements.txt       # воспроизводимая установка
```

### Виртуальные окружения
```bash
# venv (стандартный)
python -m venv .venv
source .venv/bin/activate           # Bash
source .venv/bin/activate.fish      # Fish
deactivate

# conda / mamba
conda create -n myenv python=3.12
conda activate myenv
conda deactivate
conda env list

# pipx — изолированная установка CLI-утилит
pipx install ruff
pipx install black
pipx install httpie
```

### Инструменты разработки Python
```bash
# Линтер + форматтер
pip install ruff                    # быстрый линтер (замена flake8+isort+pyupgrade)
ruff check .                        # проверка
ruff format .                       # форматирование

# Типизация
pip install mypy
mypy src/

# Тестирование
pip install pytest pytest-cov
pytest                              # запуск тестов
pytest --cov=src --cov-report=html  # с покрытием

# Сборка пакетов
pip install build twine
python -m build                     # sdist + wheel
twine upload dist/*                 # публикация на PyPI

# pyproject.toml — современная конфигурация проекта
# [build-system]
# requires = ["hatchling"]
# build-backend = "hatchling.build"
# [project]
# name = "mypackage"
# version = "0.1.0"
# dependencies = ["requests>=2.28"]
```

## Node.js / JavaScript

### Установка
```bash
# nvm — менеджер версий
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash
nvm install --lts
nvm install 22
nvm use 22
nvm list

# fnm — быстрая альтернатива (Rust)
curl -fsSL https://fnm.vercel.app/install | bash
fnm install --lts
fnm use 22

# Системный (Arch)
sudo pacman -S nodejs npm
```

### Менеджеры пакетов
```bash
# npm
npm init -y                         # инициализация проекта
npm install express                 # зависимость
npm install -D typescript           # dev dependency
npm run build                       # скрипт из package.json
npx create-react-app myapp          # одноразовый запуск

# pnpm (быстрый, экономит место)
npm install -g pnpm
pnpm install
pnpm add express
pnpm run dev

# yarn
npm install -g yarn
yarn add express
yarn dev

# bun (ультрабыстрый runtime + менеджер пакетов)
curl -fsSL https://bun.sh/install | bash
bun init
bun install
bun run dev
bun test
```

### TypeScript
```bash
npm install -g typescript
tsc --init                          # tsconfig.json
tsc                                 # компиляция
tsc --watch                         # watch mode
npx ts-node script.ts              # запуск без компиляции
```

## Rust

### Установка
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

rustup update                       # обновить toolchain
rustc --version
cargo --version

rustup component add clippy         # линтер
rustup component add rustfmt        # форматтер
```

### Основные команды
```bash
cargo new myproject                 # новый проект
cargo build                         # сборка (debug)
cargo build --release               # сборка (release)
cargo run                           # сборка + запуск
cargo test                          # тесты
cargo clippy                        # линтер
cargo fmt                           # форматирование
cargo doc --open                    # документация
cargo add serde tokio               # добавить зависимость
cargo update                        # обновить зависимости
```

## Go

### Установка
```bash
# Arch
sudo pacman -S go

# Или с golang.org
wget https://go.dev/dl/go1.22.linux-amd64.tar.gz
sudo tar -C /usr/local -xzf go1.22.linux-amd64.tar.gz
# PATH: export PATH=$PATH:/usr/local/go/bin:$HOME/go/bin
```

### Основные команды
```bash
go mod init mymodule                # инициализация модуля
go run main.go                      # запуск
go build                            # сборка
go test ./...                       # тесты
go get github.com/pkg/errors        # добавить зависимость
go mod tidy                         # очистить зависимости
go vet ./...                        # анализ кода
gofmt -w .                          # форматирование
```

## C / C++

### Компиляция
```bash
# Установка
sudo pacman -S base-devel gcc cmake  # Arch
sudo apt install build-essential cmake  # Debian

# GCC
gcc -o program main.c               # C
g++ -o program main.cpp             # C++
gcc -Wall -Wextra -O2 -o program main.c  # с предупреждениями и оптимизацией

# CMake — сборочная система
mkdir build && cd build
cmake ..
cmake --build .
cmake --build . --config Release

# Makefile
make                                # сборка
make clean                          # очистка
make install                        # установка
```

### Отладка
```bash
# GDB
gcc -g -o program main.c           # компиляция с debug info
gdb ./program
# (gdb) break main
# (gdb) run
# (gdb) next / step / continue
# (gdb) print variable
# (gdb) backtrace
# (gdb) quit

# Valgrind — поиск утечек памяти
valgrind --leak-check=full ./program

# Address Sanitizer
gcc -fsanitize=address -g -o program main.c
./program
```

## Редакторы кода

### VS Code
```bash
# Arch
sudo pacman -S code                 # Open Source build
yay -S visual-studio-code-bin       # Official Microsoft build

# Запуск из терминала
code .                              # открыть текущий каталог
code file.py                        # открыть файл

# Полезные расширения
# Python, Pylance, Rust Analyzer, C/C++, ESLint, Prettier
# GitLens, Remote SSH, Docker, Vim
```

### Neovim (как IDE)
```bash
sudo pacman -S neovim
# Популярные конфигурации:
# LazyVim: https://www.lazyvim.org/
# NvChad: https://nvchad.com/
# AstroNvim: https://astronvim.com/

# Установка LazyVim
git clone https://github.com/LazyVim/starter ~/.config/nvim
nvim                                # автоустановка плагинов
```

## Контейнеризация и CI/CD

### Docker для разработки
```bash
# Dev-контейнер
docker run -it -v $(pwd):/app -w /app python:3.12-slim bash

# docker-compose.yml для dev
# services:
#   app:
#     build: .
#     volumes:
#       - .:/app
#     ports:
#       - "8000:8000"
#     command: python manage.py runserver 0.0.0.0:8000
```

### Git Hooks
```bash
# pre-commit (Python)
pip install pre-commit
pre-commit install

# .pre-commit-config.yaml
# repos:
#   - repo: https://github.com/astral-sh/ruff-pre-commit
#     rev: v0.5.0
#     hooks:
#       - id: ruff
#       - id: ruff-format
```

## Полезные CLI-инструменты для разработки
| Инструмент | Описание |
|------------|----------|
| httpie / curlie | Удобный HTTP-клиент |
| jq / yq | Обработка JSON / YAML |
| gh | GitHub CLI |
| lazygit | TUI для Git |
| direnv | Автозагрузка переменных окружения |
| just | Альтернатива Makefile |
| tokei | Подсчёт строк кода |
| hyperfine | Бенчмарки CLI-команд |
| watchexec | Автоперезапуск при изменениях |
| act | Локальный запуск GitHub Actions |
