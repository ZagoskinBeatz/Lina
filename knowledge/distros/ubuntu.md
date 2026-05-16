# Ubuntu — Руководство

## Обзор

Ubuntu — самый популярный Linux-дистрибутив для десктопа, основан на Debian. Выходит каждые 6 месяцев, LTS-версии — каждые 2 года (поддержка 5 лет).

## Пакетный менеджер apt

### Основные команды

```bash
# Обновить список пакетов
sudo apt update

# Обновить систему
sudo apt upgrade              # безопасное обновление
sudo apt full-upgrade         # с удалением устаревших
sudo apt dist-upgrade         # переход на новую версию

# Установка / удаление
sudo apt install <пакет>
sudo apt remove <пакет>       # без конфигов
sudo apt purge <пакет>        # с конфигами
sudo apt autoremove            # неиспользуемые зависимости

# Поиск и информация
apt search <запрос>
apt show <пакет>
apt list --installed           # установленные пакеты
dpkg -L <пакет>               # файлы пакета
dpkg -S /путь/к/файлу         # чей файл
```

### PPA (Personal Package Archives)

```bash
# Добавить PPA
sudo add-apt-repository ppa:developer/repo
sudo apt update
sudo apt install <пакет>

# Удалить PPA
sudo add-apt-repository --remove ppa:developer/repo
```

## Snap пакеты

Ubuntu использует Snap для многих приложений.

```bash
# Управление snap
snap list                      # установленные
snap find <запрос>             # поиск
sudo snap install <пакет>     # установка
sudo snap remove <пакет>      # удаление
sudo snap refresh              # обновить все
```

## Обновление между версиями

```bash
# Обновление до новой LTS
sudo do-release-upgrade

# Обновление до любой новой версии
sudo do-release-upgrade -d

# Проверить текущую версию
lsb_release -a
cat /etc/os-release
```

## Настройка

### Файрвол (UFW)

```bash
sudo ufw enable
sudo ufw allow ssh
sudo ufw allow 80/tcp
sudo ufw status verbose
```

### Установка проприетарных драйверов

```bash
sudo ubuntu-drivers autoinstall
# или через GUI: "Дополнительные драйверы"
```

## Частые проблемы

### dpkg lock

```bash
# Ошибка: "Could not get lock /var/lib/dpkg/lock"
sudo rm /var/lib/dpkg/lock-frontend
sudo rm /var/lib/dpkg/lock
sudo dpkg --configure -a
```

### Broken packages

```bash
sudo apt --fix-broken install
sudo dpkg --configure -a
sudo apt clean
sudo apt update
```
