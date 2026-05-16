# Поиск файлов и текста в Linux

## find — поиск файлов
```bash
# По имени
find / -name "*.conf"                 # точное имя
find / -iname "*.conf"                # без учёта регистра
find ~ -name "*.py" -type f           # только файлы

# По типу
find /var -type f                     # файлы
find /var -type d                     # директории
find /var -type l                     # ссылки

# По размеру
find / -size +100M                    # больше 100MB
find / -size -1k                      # меньше 1KB
find /tmp -size +10M -type f          # большие файлы в /tmp

# По времени
find /var/log -mtime -1               # изменённые за сутки
find ~ -mmin -30                      # изменённые за 30 минут
find / -atime +365                    # не использованные год

# По правам
find / -perm -4000 -type f            # SUID файлы
find /tmp -perm -777                  # полный доступ

# Действия
find /tmp -name "*.tmp" -delete               # удалить
find . -name "*.log" -exec gzip {} \;        # сжать
find . -name "*.py" -exec grep -l "TODO" {} \;  # поиск в файлах

# Ограничение глубины
find / -maxdepth 3 -name "*.conf"
```

## locate / mlocate / plocate
```bash
# Быстрый поиск по базе данных
sudo updatedb                     # обновить базу
locate filename
plocate filename                  # быстрее на современных системах
```

## grep — поиск текста
```bash
# Базовый
grep "pattern" file
grep -r "pattern" /path/         # рекурсивно
grep -i "pattern" file           # без учёта регистра
grep -n "pattern" file           # с номерами строк
grep -l "pattern" *.py           # только имена файлов
grep -c "pattern" file           # количество совпадений

# Инвертированный
grep -v "exclude" file           # строки БЕЗ pattern

# Regex
grep -E "pattern1|pattern2" file  # расширенный regex
grep -P "\d{3}-\d{4}" file       # Perl regex

# Контекст
grep -B 3 "error" log            # 3 строки до
grep -A 3 "error" log            # 3 строки после
grep -C 3 "error" log            # 3 строки до и после
```

## ripgrep (rg) — быстрый grep
```bash
sudo pacman -S ripgrep

rg "pattern"                      # рекурсивно от текущей директории
rg -i "pattern"                   # без регистра
rg -t py "import"                 # только Python-файлы
rg --hidden "pattern"             # включая скрытые файлы
rg -l "pattern"                   # только имена файлов
rg "pattern" -g '*.py'            # glob-фильтр
rg -w "word"                      # только целые слова
rg --json "pattern"               # JSON-вывод (для парсинга)
rg -c "pattern"                   # количество совпадений на файл
rg -U "multi\nline"               # мультистрочный поиск
rg "pattern" --stats              # статистика поиска
rg -S "Pattern"                   # smart case (auto регистр)

# Замена (вывод, без записи)
rg "old_text" -r "new_text"
# Замена с записью:
rg -l "old_text" | xargs sed -i 's/old_text/new_text/g'

# Исключение каталогов
rg "pattern" --glob '!node_modules' --glob '!.git'
rg "pattern" --iglob '*.py'      # Case-insensitive glob

# .ripgreprc — конфигурация
# ~/.config/ripgreprc
# --smart-case
# --hidden
# --glob=!.git
# RIPGREP_CONFIG_PATH=~/.config/ripgreprc
```

## fd — быстрый find
```bash
sudo pacman -S fd

fd "*.py"                         # поиск по имени
fd -t f "config"                  # только файлы
fd -t d "src"                     # только директории
fd -e py                          # по расширению
fd -s "Pattern"                   # чувствительный к регистру
fd --hidden "pattern"             # включая скрытые
fd --no-ignore "pattern"          # включая .gitignore'd

# Исключение
fd -E node_modules -E .git "*.js"

# Выполнение команды над результатами
fd -e py -x wc -l {}             # Строки в каждом .py
fd -e py -X wc -l                # Все результаты одной командой
fd -e log --changed-within 1h     # Изменённые за последний час
fd -e tmp --changed-before 7d -x rm {}  # Удалить старые temp

# Каталогизация
fd -t d --max-depth 2             # Директории до глубины 2
fd -t f -S +100m                  # Файлы больше 100 МБ
fd -t l                           # Только символические ссылки

# Цветной вывод + tree
fd --color always | head -50
```

## fzf — нечёткий поиск
```bash
sudo pacman -S fzf

# Интерактивный поиск файлов
fzf
fzf --preview 'cat {}'           # С предпросмотром
fzf --preview 'bat --color=always {}' --preview-window=right:60%

# Поиск в истории команд
history | fzf

# Комбинация с другими командами
vim $(fzf)                        # открыть найденный файл
cd $(fd -t d | fzf)              # перейти в директорию
cat $(fzf --multi)               # Выбрать несколько файлов (Tab)

# Интеграция с другими инструментами
rg --line-number --no-heading "TODO" | fzf --delimiter=: --preview 'bat --color=always --highlight-line {2} {1}'

# Kill процесс интерактивно
ps aux | fzf | awk '{print $2}' | xargs kill -9

# Git checkout интерактивно
git branch | fzf | xargs git checkout

# Переменные окружения
printenv | fzf

# Интеграция с shell
# Fish:
fzf --fish | source              # Ctrl+R — история, Ctrl+T — файлы, Alt+C — cd

# Bash:
eval "$(fzf --bash)"

# Zsh:
source <(fzf --zsh)
```

## ag (The Silver Searcher)
```bash
sudo pacman -S the_silver_searcher

ag "pattern"                      # Быстрый поиск (автоматически .gitignore)
ag -l "pattern"                   # Только файлы
ag -w "word"                      # Целые слова
ag --python "pattern"             # Только .py файлы
ag -G "\.conf$" "pattern"        # По имени файла (regex)
```

## Сравнение инструментов поиска

| Инструмент | Скорость | .gitignore | Regex | Замена | Установлен |
|-----------|----------|-----------|-------|--------|-----------|
| grep | Средняя | Нет | Да (ERE/BRE/PCRE) | Нет | Да (везде) |
| ripgrep (rg) | Очень быстрая | Да | Да (PCRE2) | Только вывод | Нет |
| ag | Быстрая | Да | Да | Нет | Нет |
| find | Средняя | Нет | Частично | Нет | Да (везде) |
| fd | Быстрая | Да | Да | Нет | Нет |
| fzf | — | — | Нечёткий | Нет | Нет |
| locate | Мгновенная | Нет | Да | Нет | Обычно да |

## Полезные комбинации

```bash
# Найти все файлы с TODO и показать контекст
rg "TODO|FIXME|HACK|XXX" --glob '*.py' -C 2

# Найти дубликаты файлов (по хешу)
find . -type f -exec md5sum {} + | sort | uniq -D -w32

# Найти самые большие файлы
find / -type f -exec du -h {} + 2>/dev/null | sort -rh | head -20

# Найти недавно изменённые конфиги
find /etc -name "*.conf" -mtime -7 -type f

# Поиск в сжатых файлах
zgrep "pattern" /var/log/syslog.*.gz

# Поиск в бинарных файлах
strings binary_file | grep "pattern"
```
