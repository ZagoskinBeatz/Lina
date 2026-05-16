# Shell — командная оболочка Linux

## Обзор
Shell — интерфейс между пользователем и ядром. Интерпретирует команды, управляет
процессами, поддерживает скрипты и автоматизацию.

## Типы оболочек
| Shell | Описание | Конфиг |
|-------|----------|--------|
| Bash | Стандартный в большинстве дистрибутивов | ~/.bashrc |
| Zsh | Расширенный, популярный | ~/.zshrc |
| Fish | Дружелюбный, современный | ~/.config/fish/config.fish |
| Dash | Минимальный, быстрый (POSIX) | — |
| Nushell | Структурированные данные | ~/.config/nushell/config.nu |

```bash
# Текущая оболочка
echo $SHELL
echo $0

# Доступные оболочки
cat /etc/shells

# Сменить оболочку
chsh -s /usr/bin/fish
chsh -s /usr/bin/zsh
# Вступит в силу при следующем входе
```

## Bash — основы

### Переменные
```bash
# Объявление
NAME="Lina"
VERSION=1.0
ARRAY=(one two three)

# Использование
echo $NAME
echo ${NAME}
echo ${NAME:-default}              # значение по умолчанию
echo ${#NAME}                       # длина строки
echo ${NAME:0:3}                    # подстрока (первые 3 символа)
echo ${NAME/Lin/Мин}               # замена

# Экспорт (для дочерних процессов)
export PATH="$HOME/.local/bin:$PATH"

# Массивы
echo ${ARRAY[0]}                    # первый элемент
echo ${ARRAY[@]}                    # все элементы
echo ${#ARRAY[@]}                   # количество элементов

# Ассоциативные массивы
declare -A MAP
MAP[key1]="value1"
MAP[key2]="value2"
echo ${MAP[key1]}
```

### Условные конструкции
```bash
# if / elif / else
if [ -f "$FILE" ]; then
    echo "Файл существует"
elif [ -d "$DIR" ]; then
    echo "Это каталог"
else
    echo "Не найдено"
fi

# Операторы сравнения (строки)
[ "$a" = "$b" ]                     # равны
[ "$a" != "$b" ]                    # не равны
[ -z "$a" ]                         # пустая строка
[ -n "$a" ]                         # непустая строка

# Операторы сравнения (числа)
[ "$a" -eq "$b" ]                   # равно
[ "$a" -ne "$b" ]                   # не равно
[ "$a" -gt "$b" ]                   # больше
[ "$a" -lt "$b" ]                   # меньше
[ "$a" -ge "$b" ]                   # больше или равно
[ "$a" -le "$b" ]                   # меньше или равно

# Файловые тесты
[ -f "$path" ]                      # обычный файл
[ -d "$path" ]                      # каталог
[ -e "$path" ]                      # существует
[ -r "$path" ]                      # читаемый
[ -w "$path" ]                      # записываемый
[ -x "$path" ]                      # исполняемый
[ -s "$path" ]                      # размер > 0
[ -L "$path" ]                      # символическая ссылка

# [[ ]] — расширенные тесты (bash-specific)
[[ "$string" =~ ^[0-9]+$ ]]        # regex
[[ "$a" > "$b" ]]                   # лексикографическое сравнение
```

### Циклы
```bash
# for
for i in 1 2 3 4 5; do
    echo "$i"
done

for file in *.txt; do
    echo "Processing $file"
done

for ((i=0; i<10; i++)); do
    echo "$i"
done

# while
while read -r line; do
    echo "$line"
done < file.txt

# until
count=0
until [ $count -ge 5 ]; do
    echo "$count"
    ((count++))
done
```

### Функции
```bash
greet() {
    local name="${1:-World}"
    echo "Hello, $name!"
    return 0
}

greet "Lina"
echo "Exit code: $?"

# С аргументами
backup() {
    local src="$1"
    local dst="${2:-/tmp/backup}"
    cp -r "$src" "$dst"
    echo "Backed up $src → $dst"
}
```

### Перенаправление и каналы
```bash
# Перенаправление вывода
command > file.txt                  # stdout в файл (перезапись)
command >> file.txt                 # stdout в файл (дополнение)
command 2> errors.txt               # stderr в файл
command &> all.txt                  # stdout + stderr в файл
command 2>&1                        # stderr → stdout

# Канал (pipe)
cat file.txt | grep "pattern" | sort | uniq -c | sort -rn

# Process substitution
diff <(ls dir1) <(ls dir2)

# Here document
cat << 'EOF'
Многострочный
текст
EOF

# Here string
grep "pattern" <<< "$variable"
```

## Fish Shell

### Основные отличия от Bash
```fish
# Переменные
set NAME "Lina"
set -x PATH $HOME/.local/bin $PATH  # экспорт

# Условия
if test -f $FILE
    echo "Файл существует"
end

# Циклы
for file in *.txt
    echo $file
end

# Функции
function greet
    echo "Hello, $argv[1]"
end

# Сохранить функцию
funcsave greet
```

### Настройка Fish
```fish
# ~/.config/fish/config.fish
set -gx EDITOR nvim
set -gx LANG ru_RU.UTF-8

# Alias (через abbreviation)
abbr -a ll 'ls -la'
abbr -a gs 'git status'
abbr -a dc 'docker compose'

# Плагины (Fisher)
curl -sL https://raw.githubusercontent.com/jorgebucaran/fisher/main/functions/fisher.fish | source
fisher install jorgebucaran/fisher
fisher install PatrickF1/fzf.fish
fisher install jethrokuan/z
```

## Zsh

### Oh My Zsh
```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"

# ~/.zshrc
ZSH_THEME="powerlevel10k/powerlevel10k"
plugins=(git zsh-autosuggestions zsh-syntax-highlighting fzf docker)
```

### Powerlevel10k
```bash
git clone --depth=1 https://github.com/romkatv/powerlevel10k.git ${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/themes/powerlevel10k
p10k configure                     # интерактивная настройка
```

## Полезные CLI-инструменты
| Инструмент | Замена для | Описание |
|------------|-----------|----------|
| eza / exa | ls | Цветной ls с иконками |
| bat | cat | Подсветка синтаксиса |
| ripgrep (rg) | grep | Быстрый поиск |
| fd | find | Простой поиск файлов |
| fzf | — | Fuzzy finder |
| delta | diff | Красивый diff |
| dust / duf | du / df | Визуализация диска |
| bottom (btm) | top | TUI-мониторинг |
| zoxide | cd | Умная навигация |
| tldr | man | Краткие примеры |
| ncdu | du | Интерактивный размер каталогов |
| procs | ps | Улучшенный ps |
| sd | sed | Простая замена текста |

```bash
# Установка (Arch)
sudo pacman -S eza bat ripgrep fd fzf git-delta dust bottom zoxide tldr ncdu procs sd
```

## Скрипты — best practices
```bash
#!/usr/bin/env bash
set -euo pipefail                   # strict mode

# e — выход при ошибке
# u — ошибка при использовании неинициализированной переменной
# o pipefail — ошибка pipe если любая часть fail

# Получение каталога скрипта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Временные файлы с автоочисткой
TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT

# Логирование
log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# Проверка зависимостей
for cmd in jq curl grep; do
    command -v "$cmd" >/dev/null || die "$cmd не найден"
done

# Обработка аргументов
while getopts "hvo:" opt; do
    case $opt in
        h) usage; exit 0 ;;
        v) VERBOSE=1 ;;
        o) OUTPUT="$OPTARG" ;;
        *) usage; exit 1 ;;
    esac
done
shift $((OPTIND - 1))
```

## Текстовые утилиты
```bash
# sed — потоковый редактор
sed 's/old/new/g' file.txt          # замена
sed -i 's/old/new/g' file.txt       # in-place
sed -n '10,20p' file.txt            # строки 10-20
sed '/pattern/d' file.txt           # удалить строки

# awk — обработка полей
awk '{print $1, $3}' file.txt       # 1 и 3 поле
awk -F: '{print $1}' /etc/passwd    # разделитель :
awk '$3 > 100' data.txt             # фильтрация
awk '{sum+=$1} END{print sum}'      # сумма

# cut — вырезать поля
cut -d: -f1,3 /etc/passwd

# tr — замена символов
echo "HELLO" | tr 'A-Z' 'a-z'      # lowercase
echo "hello world" | tr -s ' '      # сжать пробелы
cat file | tr -d '\r'               # удалить CR (Windows → Linux)

# sort + uniq
sort file.txt | uniq -c | sort -rn  # частотный анализ

# jq — обработка JSON
curl -s url | jq '.data[] | .name'
echo '{"a":1}' | jq '.a'
cat data.json | jq -r '.items[].title'
```

## Tmux / Screen — терминальный мультиплексор
```bash
# Tmux
tmux                                # новая сессия
tmux new -s work                    # именованная сессия
tmux attach -t work                 # подключиться
tmux ls                             # список сессий

# Горячие клавиши tmux (prefix: Ctrl+B)
# Ctrl+B c — новое окно
# Ctrl+B n/p — следующее/предыдущее окно
# Ctrl+B % — вертикальное разделение
# Ctrl+B " — горизонтальное разделение
# Ctrl+B d — отключиться (сессия живёт)
# Ctrl+B [ — режим прокрутки
```
