# DNF — менеджер пакетов Fedora / RHEL / CentOS

## Основные операции

### Установка
```bash
sudo dnf install <пакет>          # Установить пакет
sudo dnf install пакет1 пакет2    # Несколько пакетов
sudo dnf install /path/to/file.rpm  # Из локального RPM
sudo dnf install https://url/to/file.rpm  # Из URL
sudo dnf groupinstall "Development Tools"  # Группа пакетов
```

### Удаление
```bash
sudo dnf remove <пакет>           # Удалить пакет
sudo dnf autoremove               # Удалить неиспользуемые зависимости
```

### Обновление
```bash
sudo dnf check-update              # Проверить обновления
sudo dnf upgrade                   # Обновить все пакеты
sudo dnf upgrade --refresh         # Обновить метаданные + пакеты
sudo dnf upgrade <пакет>           # Обновить конкретный пакет
sudo dnf distro-sync               # Синхронизировать с репозиторием
```

### Поиск
```bash
dnf search <запрос>                # Поиск по имени и описанию
dnf list installed                 # Установленные пакеты
dnf list available                 # Доступные пакеты
dnf info <пакет>                   # Информация о пакете
dnf repoquery -l <пакет>          # Список файлов пакета
dnf provides /path/to/file         # Какой пакет предоставляет файл
dnf provides '*/bin/htop'          # Поиск по имени файла
```

## Репозитории

```bash
# Список репозиториев
dnf repolist
dnf repolist all                   # Включая отключённые

# Включить/отключить репозиторий
sudo dnf config-manager --set-enabled <repo>
sudo dnf config-manager --set-disabled <repo>

# Добавить репозиторий (RPM Fusion — популярный):
sudo dnf install https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
```

### COPR (аналог PPA для Fedora)
```bash
sudo dnf copr enable <user>/<project>
sudo dnf install <пакет>
# Удалить COPR:
sudo dnf copr remove <user>/<project>
```

## История и откат

```bash
# История транзакций
dnf history
dnf history info <id>              # Подробности транзакции

# Откатить последнюю транзакцию
sudo dnf history undo last
# Откатить конкретную:
sudo dnf history undo <id>

# Понизить версию пакета:
sudo dnf downgrade <пакет>
```

## Группы пакетов

```bash
dnf group list                     # Все группы
dnf group info "Development Tools" # Содержимое группы
sudo dnf group install "Development Tools"
sudo dnf group remove "Development Tools"
```

## Управление кешем

```bash
# Кеш хранится в /var/cache/dnf/
du -sh /var/cache/dnf/

sudo dnf clean all                 # Очистить весь кеш
sudo dnf clean packages            # Удалить только скачанные RPM
sudo dnf clean metadata            # Удалить метаданные
sudo dnf makecache                 # Пересоздать кеш
```

## Конфигурация

```bash
# /etc/dnf/dnf.conf
[main]
gpgcheck=True
installonly_limit=3               # Сколько версий ядра хранить
clean_requirements_on_remove=True # Удалять зависимости при удалении
fastestmirror=True                # Выбирать быстрое зеркало
max_parallel_downloads=10         # Параллельная загрузка
defaultyes=True                   # По умолчанию "да"
```

## Модули (modularity)

```bash
# Доступные модули (например, разные версии Node.js, PHP)
dnf module list
dnf module info nodejs

# Включить модуль:
sudo dnf module enable nodejs:20
sudo dnf install nodejs

# Сбросить:
sudo dnf module reset nodejs
```

## Обновление дистрибутива (Fedora N → N+1)

```bash
# 1. Обновить текущую систему
sudo dnf upgrade --refresh

# 2. Установить плагин обновления системы
sudo dnf install dnf-plugin-system-upgrade

# 3. Скачать пакеты новой версии
sudo dnf system-upgrade download --releasever=41

# 4. Перезагрузиться и обновиться
sudo dnf system-upgrade reboot
# Система перезагрузится и обновится автоматически
```

## Решение проблем

### "Error: Failed to synchronize cache"
```bash
sudo dnf clean all
sudo dnf makecache
```

### "Conflicting requests"
```bash
# Зависимость не может быть разрешена
sudo dnf install --allowerasing <пакет>  # Заменить конфликтующий пакет
# ОСТОРОЖНО: может удалить нужные пакеты
```

### "GPG check FAILED"
```bash
# Импортировать ключ вручную:
sudo rpm --import https://example.com/RPM-GPG-KEY
# Или пропустить проверку (не рекомендуется):
sudo dnf install --nogpgcheck <пакет>
```

### Сломанные зависимости
```bash
sudo dnf distro-sync              # Привести всё в соответствие с репозиторием
# Или:
sudo rpm -Va                       # Проверить целостность всех пакетов
```

## rpm — низкоуровневые операции

```bash
rpm -qa                            # Все установленные пакеты
rpm -qi <пакет>                    # Информация
rpm -ql <пакет>                    # Файлы пакета
rpm -qf /path/to/file             # Кому принадлежит файл
rpm -ivh <file>.rpm                # Установить
rpm -Uvh <file>.rpm                # Обновить
rpm -e <пакет>                     # Удалить
```

## Flatpak (часто используется в Fedora)

```bash
# Fedora уже включает Flatpak
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo

flatpak search <запрос>
flatpak install flathub <app.id>
flatpak update
flatpak uninstall <app.id>
flatpak list
```

## DNF5 (Fedora 41+)

Fedora 41+ переходит на dnf5 — переписанный на C++ для скорости.

```bash
# DNF5 полностью совместим по синтаксису
dnf5 install <пакет>
dnf5 upgrade
dnf5 search <запрос>

# Новые фичи
dnf5 offline upgrade             # Офлайн-обновление (при перезагрузке)
dnf5 versionlock add <пакет>     # Блокировка версии (встроенная)
dnf5 replay /var/lib/dnf5/history/  # Воспроизведение транзакции
```

## Продвинутые операции

### Фиксация версии пакета
```bash
# Плагин versionlock
sudo dnf install python3-dnf-plugin-versionlock
sudo dnf versionlock add <пакет>
sudo dnf versionlock list
sudo dnf versionlock delete <пакет>
```

### Логи и аудит
```bash
# История операций
dnf history list --reverse | head -20
dnf history info last

# Что изменилось при конкретном обновлении
dnf history info 42

# Отменить несколько транзакций
sudo dnf history rollback 38    # Вернуться к состоянию транзакции 38
```

### Создание локального репозитория
```bash
sudo dnf install createrepo_c
mkdir -p /var/local/repo
cp *.rpm /var/local/repo/
createrepo_c /var/local/repo/

# Добавить как реп
sudo dnf config-manager --add-repo file:///var/local/repo
```

### Automatic updates
```bash
sudo dnf install dnf-automatic
# Конфигурация: /etc/dnf/automatic.conf

# Только скачивать:
sudo systemctl enable --now dnf-automatic-download.timer

# Скачивать и устанавливать:
sudo systemctl enable --now dnf-automatic-install.timer
```
