# Zypper — пакетный менеджер openSUSE

## Базовые операции
```bash
# Обновление базы
sudo zypper refresh

# Обновление системы
sudo zypper update            # обновить пакеты
sudo zypper dist-upgrade      # обновление дистрибутива (Tumbleweed)

# Установка
sudo zypper install <пакет>
sudo zypper in <пакет>        # сокращение

# Удаление
sudo zypper remove <пакет>
sudo zypper rm <пакет>

# Поиск
zypper search <запрос>
zypper se <запрос>

# Информация
zypper info <пакет>

# Список установленных
zypper packages --installed-only
```

## Управление репозиториями
```bash
# Список
zypper repos
zypper lr -d               # подробно

# Добавление
sudo zypper addrepo <url> <alias>
sudo zypper ar <url> <alias>

# Удаление
sudo zypper removerepo <alias>

# Включить/выключить
sudo zypper modifyrepo --enable <alias>
sudo zypper modifyrepo --disable <alias>
```

## Patterns (группы пакетов)
```bash
zypper patterns                  # доступные паттерны
sudo zypper install -t pattern kde_plasma  # установить окружение
```

## Откат
```bash
# История транзакций
zypper history

# Снимки (в связке с snapper)
sudo snapper list
sudo snapper rollback <номер>
```

## Locks (блокировка пакетов)
```bash
# Заблокировать пакет от обновления
sudo zypper addlock <package>

# Список блокировок
sudo zypper locks

# Удалить блокировку
sudo zypper removelock <package>
```

## Управление репозиториями — подробно
```bash
# Добавить репозиторий
sudo zypper addrepo <URL> <alias>
sudo zypper addrepo --refresh <URL> <alias>   # с автообновлением

# Добавить OBS-репозиторий
sudo zypper addrepo https://download.opensuse.org/repositories/<project>/<distro>/<project>.repo

# Приоритеты репозиториев (меньше = выше приоритет)
sudo zypper modifyrepo --priority 90 <alias>

# Список репозиториев с приоритетами
zypper repos -p

# Обновить метаданные
sudo zypper refresh
sudo zypper refresh -f            # принудительно
```

## Работа с файлами пакетов
```bash
# Установить локальный RPM
sudo zypper install /path/to/package.rpm

# Какому пакету принадлежит файл
zypper search --provides <file>
rpm -qf /usr/bin/python3

# Список файлов пакета
rpm -ql <package>

# Зависимости пакета
zypper info --requires <package>
zypper info --recommends <package>
```

## Транзакции и откат
```bash
# Показать историю транзакций
zypper history
# Или через snapper:
sudo snapper list

# Откат к предыдущему снимку (Btrfs + Snapper)
sudo snapper rollback <number>

# Повторить установку конкретной версии
sudo zypper install --oldpackage <package>=<version>
```

## Миграция дистрибутива (dup)
```bash
# Обновление до новой версии openSUSE
sudo zypper refresh
sudo zypper dup                   # distribution upgrade

# Leap → Tumbleweed миграция
# 1. Заменить репозитории на Tumbleweed
# 2. sudo zypper dup --allow-vendor-change
```

## openSUSE специфика
```bash
# YaST — графический менеджер системы
yast2                             # GUI
yast                              # TUI (ncurses)
yast2 sw_single                   # менеджер пакетов
yast2 firewall                    # firewall
yast2 network                     # настройка сети

# 1-Click Install (для openSUSE)
# На software.opensuse.org — кнопка "Direct Install"
# Автоматически добавляет реопозиторий и устанавливает пакет

# SUSEConnect (для SLES)
sudo SUSEConnect -r <registration_code>
sudo SUSEConnect -p <product>/<version>/<arch>
```

## Сравнение с другими менеджерами
| Операция | zypper | pacman | apt | dnf |
|----------|--------|--------|-----|-----|
| Установить | `zypper in` | `pacman -S` | `apt install` | `dnf install` |
| Удалить | `zypper rm` | `pacman -R` | `apt remove` | `dnf remove` |
| Обновить всё | `zypper up` | `pacman -Syu` | `apt upgrade` | `dnf upgrade` |
| Поиск | `zypper se` | `pacman -Ss` | `apt search` | `dnf search` |
| Инфо | `zypper info` | `pacman -Si` | `apt show` | `dnf info` |
| Владелец файла | `zypper se --provides` | `pacman -Qo` | `dpkg -S` | `dnf provides` |
| Очистка кэша | `zypper clean` | `paccache -r` | `apt clean` | `dnf clean all` |
