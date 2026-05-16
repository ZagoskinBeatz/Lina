# Git — система контроля версий

## Базовые команды
```bash
# Инициализация
git init                          # новый репозиторий
git clone <url>                   # клонирование
git clone --depth 1 <url>         # shallow clone (только последний коммит)
git clone --recurse-submodules <url>  # с подмодулями

# Состояние
git status                        # текущее состояние
git status -s                     # краткий формат
git log --oneline -20             # последние 20 коммитов
git log --graph --oneline --all   # граф веток
git log --stat                    # с перечнем изменённых файлов
git log -p                        # с diff каждого коммита
git log --author="Name"           # по автору
git log --since="2 weeks ago"     # за последние 2 недели
git log -- path/to/file           # история файла
git diff                          # изменения (unstaged)
git diff --staged                 # изменения (staged)
git diff branch1..branch2         # разница между ветками
git diff --stat                   # сводка изменений
git blame file.py                 # кто какую строку написал
git show <hash>                   # показать коммит
git shortlog -sn                  # авторы по количеству коммитов
```

## Работа с изменениями
```bash
# Добавление
git add .                         # все файлы
git add -p                        # интерактивно (по частям)
git add -u                        # только отслеживаемые (без новых)

# Коммит
git commit -m "описание"
git commit -am "описание"         # add + commit (только tracked)
git commit --amend                # исправить последний коммит
git commit --amend --no-edit      # исправить без изменения сообщения
git commit --allow-empty -m "trigger CI"  # пустой коммит

# Откат
git checkout -- <file>            # откатить файл
git restore <file>                # новый синтаксис (Git 2.23+)
git restore --staged <file>       # убрать из staging
git reset HEAD <file>             # убрать из staging (старый)
git reset --soft HEAD~1           # отменить коммит (сохранить изменения staged)
git reset --mixed HEAD~1          # отменить коммит (сохранить unstaged)
git reset --hard HEAD~1           # отменить коммит (удалить изменения!)
git revert <hash>                 # создать обратный коммит
git clean -fd                     # удалить неотслеживаемые файлы и каталоги
git clean -fdn                    # dry-run (показать что будет удалено)
```

## Ветки
```bash
git branch                        # список веток
git branch -a                     # все (включая remote)
git branch -v                     # с последним коммитом
git branch <name>                 # создать ветку
git checkout <name>               # переключиться
git switch <name>                 # переключиться (новый синтаксис)
git checkout -b <name>            # создать + переключиться
git switch -c <name>              # создать + переключиться (новый)
git merge <branch>                # слияние
git merge --no-ff <branch>        # без fast-forward (всегда merge commit)
git rebase <branch>               # перебазирование
git branch -d <name>              # удалить (merged)
git branch -D <name>              # удалить (force)
git branch -m old-name new-name   # переименовать

# Конфликты слияния
git merge <branch>                # при конфликте:
# 1. Редактировать файлы (<<<<<<< HEAD ... >>>>>>> branch)
# 2. git add <resolved_file>
# 3. git merge --continue
git merge --abort                 # отменить merge
```

## Удалённые репозитории
```bash
git remote -v                     # список remote
git remote add origin <url>       # добавить remote
git remote set-url origin <url>   # изменить URL
git remote rename origin upstream # переименовать
git push -u origin main           # отправить + привязать
git push                          # отправить (после привязки)
git push --tags                   # отправить теги
git pull                          # получить + merge
git pull --rebase                 # получить + rebase
git fetch                         # только получить
git fetch --prune                 # удалить несуществующие remote ветки
git push --force-with-lease       # безопасный force push
git push origin --delete <branch> # удалить remote ветку
```

## Stash (временное хранение)
```bash
git stash                         # спрятать изменения
git stash -u                      # включая untracked файлы
git stash push -m "описание"      # с сообщением
git stash pop                     # достать и удалить
git stash apply                   # достать но не удалять
git stash list                    # список
git stash show -p stash@{0}       # показать содержимое
git stash drop                    # удалить последний stash
git stash clear                   # удалить все stash
```

## Теги
```bash
git tag v1.0.0                    # lightweight tag
git tag -a v1.0.0 -m "Release"   # annotated tag
git tag -l "v1.*"                 # список тегов
git push origin v1.0.0            # отправить тег
git push origin --tags            # отправить все теги
git tag -d v1.0.0                 # удалить локально
git push origin :refs/tags/v1.0.0 # удалить с remote
```

## Интерактивный rebase
```bash
git rebase -i HEAD~5              # последние 5 коммитов
# pick   — оставить
# reword — изменить сообщение
# edit   — остановиться для правки
# squash — объединить с предыдущим (оставить сообщение)
# fixup  — объединить (без сообщения)
# drop   — удалить коммит

git rebase --abort                # отмена rebase
git rebase --continue             # продолжить после правок
```

## Cherry-pick — перенос коммитов
```bash
git cherry-pick <hash>            # перенести один коммит
git cherry-pick hash1..hash3      # диапазон
git cherry-pick --no-commit <hash>  # без автокоммита
```

## Git Worktree — несколько рабочих деревьев
```bash
git worktree add ../feature-branch feature  # создать worktree
git worktree list                 # список
git worktree remove ../feature-branch  # удалить
```

## Submodules
```bash
git submodule add <url> path/     # добавить подмодуль
git submodule update --init --recursive  # инициализировать
git submodule update --remote     # обновить до последнего
```

## Полезные настройки
```bash
git config --global user.name "Имя"
git config --global user.email "email@example.com"
git config --global init.defaultBranch main
git config --global pull.rebase true
git config --global push.autoSetupRemote true
git config --global core.autocrlf input
git config --global merge.conflictstyle diff3
git config --global rerere.enabled true  # запоминать разрешения конфликтов
git config --global diff.algorithm histogram

# Алиасы
git config --global alias.st "status -s"
git config --global alias.lg "log --oneline --graph --all"
git config --global alias.co "checkout"
git config --global alias.br "branch"
git config --global alias.unstage "restore --staged"
```

## .gitignore
```
# Стандартные шаблоны
*.pyc
__pycache__/
.venv/
node_modules/
.env
*.log
.DS_Store
*.swp
*.swo
dist/
build/
*.egg-info/
.mypy_cache/
.ruff_cache/
```

## Полезные команды
```bash
# Найти коммит, который сломал тест (bisect)
git bisect start
git bisect bad                    # текущий — плохой
git bisect good v1.0.0            # этот — хороший
# Git будет переключаться, вы тестируете и отмечаете:
git bisect good / git bisect bad
git bisect reset                  # по окончании

# Рефлог — история всех перемещений HEAD
git reflog
git checkout HEAD@{5}             # вернуться к состоянию

# Архив
git archive --format=tar.gz HEAD > project.tar.gz

# Подсчёт строк кода по автору
git log --author="Name" --pretty=tformat: --numstat | awk '{s+=$1} END {print s}'
```
