# Конфликты пакетов и сломанные зависимости

## Arch Linux / CachyOS (pacman)

### Конфликт файлов
```bash
# Ошибка: "X exists in filesystem (owned by Y)"
# Решение 1: Определить причину
pacman -Qo /path/to/conflicting/file

# Решение 2: Перезаписать (если уверены)
sudo pacman -S package --overwrite '/path/to/file'
sudo pacman -S package --overwrite '*'  # все файлы

# Решение 3: Удалить конфликтующий пакет
sudo pacman -R conflicting-package
sudo pacman -S desired-package
```

### Сломанные зависимости
```bash
# Проверка целостности
sudo pacman -Dk                     # проверить зависимости
sudo pacman -Qk                     # проверить файлы пакетов
sudo pacman -Qkk                    # подробная проверка (checksums)

# Найти пакеты с проблемами
sudo pacman -Dk 2>&1 | grep "missing"

# Переустановить все пакеты
sudo pacman -S $(pacman -Qnq)       # все из репозиториев
# ⚠ Долго, но надёжно

# Переустановить один пакет
sudo pacman -S --needed package
```

### Частичное обновление
```bash
# ⚠ Arch НЕ поддерживает partial upgrades!
# Всегда: sudo pacman -Syu (полное обновление)

# Если уже сломано:
# 1. Загрузиться с Live USB
# 2. arch-chroot /mnt
# 3. pacman -Syu
```

### Ключи GPG
```bash
# Ошибка: "invalid or corrupted package (PGP signature)"
sudo pacman-key --init
sudo pacman-key --populate archlinux cachyos
sudo pacman-key --refresh-keys       # обновить (долго)

# Конкретный ключ
sudo pacman-key --recv-keys KEY_ID
sudo pacman-key --lsign-key KEY_ID

# Полный сброс
sudo rm -rf /etc/pacman.d/gnupg
sudo pacman-key --init
sudo pacman-key --populate
```

### pacnew / pacsave
```bash
# pacnew — новая версия конфига
# pacsave — бэкап при удалении

# Найти
find /etc -name "*.pacnew" -o -name "*.pacsave" 2>/dev/null
pacdiff                              # интерактивное управление

# Или вручную:
sudo diff /etc/X.conf /etc/X.conf.pacnew
sudo mv /etc/X.conf.pacnew /etc/X.conf  # принять новый
```

### Откат пакета (downgrade)
```bash
# Из кэша
ls /var/cache/pacman/pkg/ | grep package
sudo pacman -U /var/cache/pacman/pkg/package-OLD_VERSION.pkg.tar.zst

# С помощью downgrade
sudo pacman -S downgrade
sudo downgrade package               # интерактивный выбор версии

# Заблокировать обновление (IgnorePkg)
# /etc/pacman.conf:
# IgnorePkg = package1 package2
```

### Зеркала
```bash
# Ошибка скачивания / 404
sudo pacman -Syyu                    # обновить БД + принудительно

# Обновить список зеркал
sudo reflector --country Russia,Germany --age 12 --protocol https --sort rate --save /etc/pacman.d/mirrorlist

# CachyOS зеркала
sudo cachyos-rate-mirrors
```

## Debian / Ubuntu (apt)

### Сломанные зависимости
```bash
# Автоисправление
sudo apt --fix-broken install
sudo dpkg --configure -a             # незавершённые установки

# Принудительная переустановка
sudo apt install --reinstall package

# Пересоздание кэша
sudo rm -rf /var/lib/apt/lists/*
sudo apt update

# dpkg проблемы
sudo dpkg --remove --force-remove-reinstreq package
sudo apt --fix-broken install
```

### Held packages (удержанные)
```bash
# Проверить
apt-mark showhold
dpkg --get-selections | grep hold

# Снять удержание
sudo apt-mark unhold package

# Установить удержание
sudo apt-mark hold package
```

### PPA конфликты
```bash
# Список PPA
grep -r "^deb " /etc/apt/sources.list.d/

# Удалить PPA
sudo add-apt-repository --remove ppa:name/ppa
sudo apt update

# ppa-purge — откатить пакеты PPA до official
sudo apt install ppa-purge
sudo ppa-purge ppa:name/ppa
```

### Apt pinning — приоритеты
```bash
# /etc/apt/preferences.d/pin.pref
# Package: firefox
# Pin: release a=focal
# Pin-Priority: 1001

# Проверить
apt-cache policy package
```

## Fedora (dnf)

### Сломанные зависимости
```bash
# Автоисправление
sudo dnf distro-sync
sudo dnf check                       # проверка целостности

# Переустановка
sudo dnf reinstall package

# Удалить дубликаты
sudo dnf remove --duplicates

# Очистить метаданные
sudo dnf clean all
sudo dnf makecache
```

### Откат транзакций
```bash
# История
sudo dnf history
sudo dnf history info 15             # детали

# Откат
sudo dnf history undo 15             # отменить транзакцию
sudo dnf history rollback 10         # откатить до ID 10
```

### RPM Fusion конфликты
```bash
# Проверить
rpm -qa | grep rpmfusion

# Переустановить из Fusion
sudo dnf swap mesa-va-drivers mesa-va-drivers-freeworld
sudo dnf groupupdate multimedia
```

## Общие стратегии

### Принудительное удаление
```bash
# Arch
sudo pacman -Rdd package             # без проверки зависимостей

# Debian
sudo dpkg --force-depends -r package
sudo apt --fix-broken install

# Fedora
sudo rpm -e --nodeps package
sudo dnf distro-sync
```

### Кэш пакетов — очистка
```bash
# Arch
sudo paccache -r                     # оставить 3 последних версии
sudo paccache -rk1                   # оставить 1 версию
sudo pacman -Scc                     # удалить всё

# Debian
sudo apt clean                       # весь кэш
sudo apt autoclean                   # только устаревшие

# Fedora
sudo dnf clean all
```

### Осиротевшие пакеты
```bash
# Arch
pacman -Qtdq                         # список
sudo pacman -Rns $(pacman -Qtdq)     # удалить

# Debian
sudo apt autoremove

# Fedora
sudo dnf autoremove
```

### Аудит установленных пакетов
```bash
# Arch — явно установленные
pacman -Qeq                          # без версий
pacman -Qet                          # которые ничего не зависит от них

# Сохранить список для восстановления
pacman -Qeq > ~/packages.txt
# Восстановить:
sudo pacman -S --needed - < ~/packages.txt

# Debian
dpkg --get-selections > ~/packages.txt
# Восстановить:
sudo dpkg --set-selections < ~/packages.txt
sudo apt dselect-upgrade

# Fedora
dnf repoquery --userinstalled > ~/packages.txt
```

## Диагностика — общий алгоритм
1. **Определить ошибку**: прочитать сообщение pacman/apt/dnf полностью
2. **Проверить зависимости**: `pacman -Dk` / `apt --fix-broken install`
3. **Обновить БД**: `pacman -Syy` / `apt update`
4. **Очистить кэш**: `pacman -Scc` / `apt clean`
5. **Ключи GPG**: обновить кольцо ключей
6. **Откатить**: downgrade пакет до предыдущей версии
7. **Принудительно**: `--overwrite` / `--force-depends` (с осторожностью)
8. **Chroot**: загрузиться с Live USB если система не грузится
