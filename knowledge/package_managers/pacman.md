# Pacman — менеджер пакетов Arch Linux

## Основные операции

### Установка
```bash
sudo pacman -S <пакет>            # Установить пакет
sudo pacman -S пакет1 пакет2      # Несколько пакетов
sudo pacman -S --needed <пакет>   # Установить только если не установлен
sudo pacman -U /path/to/package.pkg.tar.zst  # Установить из файла
```

### Удаление
```bash
sudo pacman -R <пакет>            # Удалить пакет
sudo pacman -Rs <пакет>           # Удалить пакет + неиспользуемые зависимости (рекомендуется)
sudo pacman -Rns <пакет>          # + удалить конфигурационные файлы
sudo pacman -Rdd <пакет>          # Удалить без проверки зависимостей (ОПАСНО)
```

### Обновление системы
```bash
sudo pacman -Syu                  # Синхронизировать БД + обновить все пакеты
sudo pacman -Syyu                 # Принудительно обновить БД + обновить
# НИКОГДА не делайте pacman -Sy (без -u) перед установкой — partial upgrade ломает систему!
```

### Поиск
```bash
pacman -Ss <запрос>               # Поиск в репозиториях
pacman -Qs <запрос>               # Поиск среди установленных
pacman -Si <пакет>                # Информация о пакете в репозитории
pacman -Qi <пакет>                # Информация об установленном пакете
pacman -Ql <пакет>                # Список файлов пакета
pacman -Qo /path/to/file          # Какому пакету принадлежит файл
pacman -F <filename>              # Найти пакет по имени файла (нужен pacman -Fy)
```

## Управление кешем

```bash
# Кеш хранится в /var/cache/pacman/pkg/
du -sh /var/cache/pacman/pkg/     # Размер кеша

# Очистка (оставить 3 последних версии):
sudo paccache -r
# Оставить только 1 версию:
sudo paccache -rk1
# Удалить кеш неустановленных пакетов:
sudo pacman -Sc
# Удалить весь кеш (ОСТОРОЖНО):
sudo pacman -Scc
```

## Осиротевшие пакеты (orphans)

```bash
# Список сирот (зависимости, которые никому не нужны)
pacman -Qdt
# Удалить сироты
sudo pacman -Rs $(pacman -Qdtq)
# Пометить пакет как явно установленный (не сирота):
sudo pacman -D --asexplicit <пакет>
# Пометить как зависимость:
sudo pacman -D --asdeps <пакет>
```

## pacman.conf — конфигурация

```bash
# /etc/pacman.conf

# Полезные опции:
Color                            # Цветной вывод
VerbosePkgLists                  # Подробные списки при обновлении
ParallelDownloads = 5            # Параллельная загрузка
ILoveCandy                       # Пасхалка — Pac-Man прогресс-бар

# Репозитории (уже включены):
[core]
[extra]
[multilib]                       # 32-bit библиотеки (для Steam и т.д.)
# Раскомментировать [multilib] для Steam/Wine
```

## Зеркала (mirrors)

```bash
# /etc/pacman.d/mirrorlist

# Автоматический выбор быстрых зеркал:
sudo pacman -S reflector
sudo reflector --country Germany,France --age 12 --protocol https --sort rate --save /etc/pacman.d/mirrorlist
sudo pacman -Syyu

# Или с автоматическим таймером:
sudo systemctl enable --now reflector.timer
```

## AUR (Arch User Repository)

### yay (рекомендуемый AUR-хелпер)
```bash
# Установка yay:
sudo pacman -S --needed git base-devel
git clone https://aur.archlinux.org/yay-bin.git
cd yay-bin
makepkg -si

# Использование:
yay -S <aur_пакет>               # Установить из AUR
yay -Ss <запрос>                  # Поиск в AUR + репозиториях
yay -Syu                          # Обновить всё (включая AUR)
yay -Yc                           # Удалить неиспользуемые зависимости
```

### paru (альтернатива)
```bash
sudo pacman -S --needed git base-devel
git clone https://aur.archlinux.org/paru-bin.git
cd paru-bin
makepkg -si

paru -S <пакет>
paru -Syu
```

## Откат пакета (downgrade)

```bash
# Из кеша:
sudo pacman -U /var/cache/pacman/pkg/<пакет>-<старая_версия>.pkg.tar.zst

# Или утилита downgrade:
yay -S downgrade
sudo downgrade <пакет>

# Заблокировать обновление пакета:
# /etc/pacman.conf
IgnorePkg = <пакет>
# Или через командную строку:
sudo pacman -Syu --ignore=<пакет>
```

## Решение проблем

### "failed to commit transaction (conflicting files)"
```bash
# Файл уже существует и принадлежит другому пакету или был создан вручную
# Проверить кому принадлежит файл:
pacman -Qo /path/to/conflicting/file
# Если ничей — можно перезаписать:
sudo pacman -S --overwrite '/path/to/file' <пакет>
```

### "unable to lock database"
```bash
# Другой pacman уже работает, или предыдущий завершился аварийно
sudo rm /var/lib/pacman/db.lck
```

### "invalid or corrupted package (PGP signature)"
```bash
# Обновить ключи:
sudo pacman-key --init
sudo pacman-key --populate archlinux
sudo pacman -Sy archlinux-keyring
sudo pacman -Syu
```

### Сломанная система после обновления
```bash
# Загрузиться с Live USB
sudo mount /dev/sda2 /mnt
sudo arch-chroot /mnt
# Откатить проблемные пакеты из кеша:
sudo pacman -U /var/cache/pacman/pkg/<пакет>-<старая_версия>.pkg.tar.zst
# Или пересобрать initramfs:
mkinitcpio -P
```

## Hooks (хуки pacman)

Хуки выполняются автоматически при установке/обновлении/удалении пакетов.

```bash
# Системные хуки: /usr/share/libalpm/hooks/
# Пользовательские: /etc/pacman.d/hooks/

# Пример: автоочистка кэша после обновления
# /etc/pacman.d/hooks/clean_cache.hook
```

```ini
[Trigger]
Operation = Upgrade
Type = Package
Target = *

[Action]
Description = Очистка старых пакетов из кэша
When = PostTransaction
Exec = /usr/bin/paccache -rk3
```

```bash
# Пример: обновить базу GRUB после обновления ядра
# /etc/pacman.d/hooks/grub-update.hook
```

```ini
[Trigger]
Type = Package
Operation = Upgrade
Target = linux
Target = linux-lts

[Action]
Description = Обновление GRUB конфигурации
When = PostTransaction
Exec = /usr/bin/grub-mkconfig -o /boot/grub/grub.cfg
```

## Сравнение флагов

| Операция | Флаг | Описание |
|----------|------|----------|
| Установка | `-S` | Sync (установить) |
| Обновление | `-Syu` | Sync + refresh + upgrade |
| Удаление | `-R` | Remove |
| Удаление + зависимости | `-Rs` | Remove + recursive |
| Удаление + конфиги | `-Rns` | Remove + nosave |
| Поиск (репо) | `-Ss` | Search (sync) |
| Поиск (установленные) | `-Qs` | Query search |
| Инфо (репо) | `-Si` | Sync info |
| Инфо (установленный) | `-Qi` | Query info |
| Файлы пакета | `-Ql` | Query list |
| Владелец файла | `-Qo` | Query owns |
| Сироты | `-Qdt` | Query deps + unrequired |
| Явно установленные | `-Qe` | Query explicit |

## Продвинутые операции

```bash
# Список всех явно установленных пакетов (для бэкапа)
pacman -Qeq > pkglist.txt
# Восстановить на новой системе:
sudo pacman -S --needed - < pkglist.txt

# Список AUR-пакетов
pacman -Qmq                     # foreign (не из репозиториев)

# Проверить целостность пакетов
pacman -Qk                       # Проверить все файлы
pacman -Qkk <пакет>              # Подробная проверка (контрольные суммы)

# Статистика
pacman -Qi | grep "Installed Size" | awk -F: '{sum+=$2} END {print sum/1024 " GiB"}'

# Список пакетов по размеру
expac '%m %n' -Q | sort -rn | head -20  # Самые большие пакеты (нужен expac)

# Дерево зависимостей
pactree <пакет>                  # Зависимости (кого требует)
pactree -r <пакет>               # Обратные зависимости (кто требует)
```
