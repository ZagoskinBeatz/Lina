# APT — менеджер пакетов Debian / Ubuntu

## Основные операции

### Установка
```bash
sudo apt install <пакет>          # Установить пакет
sudo apt install пакет1 пакет2    # Несколько пакетов
sudo apt install ./<file>.deb     # Установить из .deb файла
sudo dpkg -i <file>.deb           # Альтернатива (без зависимостей)
sudo apt install -f               # Доустановить зависимости после dpkg
sudo apt install --no-install-recommends <пакет>  # Без рекомендуемых пакетов
```

### Удаление
```bash
sudo apt remove <пакет>           # Удалить пакет (конфиги остаются)
sudo apt purge <пакет>            # Удалить пакет + конфигурацию
sudo apt autoremove               # Удалить неиспользуемые зависимости
sudo apt autoremove --purge       # + удалить их конфиги
```

### Обновление
```bash
sudo apt update                   # Обновить список пакетов (БД репозиториев)
sudo apt upgrade                  # Обновить установленные пакеты (безопасно)
sudo apt full-upgrade             # Обновить с удалением/добавлением зависимостей
sudo apt dist-upgrade             # То же что full-upgrade

# Стандартная процедура обновления:
sudo apt update && sudo apt upgrade -y
```

### Поиск
```bash
apt search <запрос>               # Поиск по имени и описанию
apt list --installed              # Все установленные пакеты
apt list --upgradable             # Доступные обновления
apt show <пакет>                  # Подробная информация
apt-cache depends <пакет>         # Зависимости пакета
apt-cache rdepends <пакет>        # Обратные зависимости
dpkg -L <пакет>                   # Список файлов пакета
dpkg -S /path/to/file             # Какому пакету принадлежит файл
apt-file search <filename>        # Поиск файла по всем пакетам (нужен apt-file)
```

## Репозитории (sources)

### Файлы конфигурации
```bash
# Deb822 формат (Ubuntu 24.04+):
/etc/apt/sources.list.d/ubuntu.sources

# Классический формат:
/etc/apt/sources.list
/etc/apt/sources.list.d/*.list
```

### Добавление PPA (Ubuntu)
```bash
sudo add-apt-repository ppa:<user>/<ppa-name>
sudo apt update
sudo apt install <пакет>

# Удалить PPA:
sudo add-apt-repository --remove ppa:<user>/<ppa-name>
```

### Добавление стороннего репозитория
```bash
# 1. Добавить GPG ключ
curl -fsSL https://example.com/key.gpg | sudo gpg --dearmor -o /etc/apt/keyrings/example.gpg

# 2. Добавить репозиторий
echo "deb [signed-by=/etc/apt/keyrings/example.gpg] https://example.com/repo stable main" | \
  sudo tee /etc/apt/sources.list.d/example.list

# 3. Обновить и установить
sudo apt update
sudo apt install <пакет>
```

## Управление кешем

```bash
# Кеш .deb файлов
du -sh /var/cache/apt/archives/

# Очистить скачанные .deb
sudo apt clean                    # Удалить все .deb из кеша
sudo apt autoclean                # Удалить только устаревшие .deb
```

## Фиксация версии пакета

```bash
# Заблокировать обновление:
sudo apt-mark hold <пакет>
# Разблокировать:
sudo apt-mark unhold <пакет>
# Список заблокированных:
apt-mark showhold
```

## dpkg — низкоуровневые операции

```bash
dpkg --list                       # Все установленные пакеты
dpkg --list | grep <запрос>       # Поиск
dpkg -i <file>.deb                # Установить .deb
dpkg -r <пакет>                   # Удалить
dpkg --configure -a               # Довести незавершённые установки
dpkg -l | grep "^rc"             # Пакеты с оставшимися конфигами
dpkg --purge $(dpkg -l | grep "^rc" | awk '{print $2}')  # Удалить их конфиги
```

## Решение проблем

### "Could not get lock /var/lib/dpkg/lock"
```bash
# Другой apt/dpkg уже работает
# Подождать завершения или:
sudo kill $(pgrep -f apt)
sudo rm /var/lib/dpkg/lock
sudo rm /var/lib/dpkg/lock-frontend
sudo rm /var/cache/apt/archives/lock
sudo dpkg --configure -a
```

### "Unmet dependencies"
```bash
sudo apt --fix-broken install
# Если не помогло:
sudo dpkg --configure -a
sudo apt update
sudo apt upgrade
```

### "Hash Sum mismatch"
```bash
sudo rm -rf /var/lib/apt/lists/*
sudo apt clean
sudo apt update
```

### "The following packages have been kept back"
```bash
# Пакеты требуют новых зависимостей:
sudo apt full-upgrade
# Или установить конкретный:
sudo apt install <held_package>
```

### Откат пакета
```bash
# Посмотреть доступные версии:
apt-cache policy <пакет>
# Установить конкретную версию:
sudo apt install <пакет>=<версия>
# Пример:
sudo apt install firefox=125.0.1+build1-0ubuntu1
```

## Snap (Ubuntu)

```bash
snap find <запрос>                # Поиск
sudo snap install <пакет>         # Установить
sudo snap remove <пакет>          # Удалить
snap list                         # Установленные snap-пакеты
sudo snap refresh                 # Обновить все snap-пакеты

# Удалить snapd полностью (Ubuntu):
sudo snap remove --purge snap-store
sudo snap remove --purge $(snap list | awk 'NR>1 {print $1}')
sudo apt remove --purge snapd
sudo apt-mark hold snapd
```

## Flatpak (альтернатива snap)

```bash
sudo apt install flatpak
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo

flatpak search <запрос>
flatpak install flathub <app.id>
flatpak run <app.id>
flatpak update
flatpak uninstall <app.id>
flatpak list                      # Установленные
```

## Продвинутые операции APT

### Приоритеты пакетов (pinning)
```bash
# /etc/apt/preferences.d/priority.pref
Package: firefox
Pin: release a=focal
Pin-Priority: 1001

# Приоритеты:
# 1001+  — устанавливается даже с понижением версии
# 500    — нормальный (по умолчанию)
# 100    — устанавливается только если нет другой версии
# -1     — никогда не устанавливать
```

### Автоматические обновления безопасности
```bash
sudo apt install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades

# Конфигурация: /etc/apt/apt.conf.d/50unattended-upgrades
# Проверить статус:
sudo unattended-upgrade --dry-run
```

### Логи apt
```bash
# История всех операций
less /var/log/apt/history.log
# Подробные логи (скачивание, ошибки)
less /var/log/apt/term.log

# Что изменилось за последний день:
grep "$(date +%Y-%m-%d)" /var/log/apt/history.log
```

### Восстановление сломанной системы
```bash
# Довести незавершённые операции
sudo dpkg --configure -a
sudo apt install -f

# Пересоздать кэш
sudo apt clean
sudo rm -rf /var/lib/apt/lists/*
sudo apt update

# Восстановить пакет до состояния репозитория
sudo apt install --reinstall <пакет>
```

### Управление ядрами
```bash
# Список установленных ядер
dpkg --list | grep linux-image

# Удалить старые ядра (осторожно — оставьте текущее!)
uname -r                          # Текущее ядро
sudo apt autoremove --purge       # Автоудаление старых ядер
```
