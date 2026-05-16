# Резервное копирование (Backup)

## Стратегии бэкапа

### Правило 3-2-1
- **3** копии данных (оригинал + 2 бэкапа)
- **2** разных типа носителей (SSD + внешний HDD)
- **1** копия вне помещения (облако или удалённый сервер)

### Типы бэкапов
| Тип | Описание | Время | Размер |
|-----|----------|-------|--------|
| Полный | Все файлы | Долго | Большой |
| Инкрементальный | Только изменения с последнего бэкапа | Быстро | Маленький |
| Дифференциальный | Изменения с последнего полного | Среднее | Средний |

## rsync — синхронизация файлов

### Базовое использование
```bash
# Локальная копия
rsync -avh /home/user/ /backup/home/
# -a = archive (рекурсивно, права, владелец, timestamps)
# -v = verbose
# -h = human readable

# С удалением лишних файлов на приёмнике
rsync -avh --delete /home/user/ /backup/home/

# На удалённый сервер
rsync -avhz /home/user/ user@server:/backup/home/
# -z = сжатие при передаче

# С исключениями
rsync -avh --exclude='.cache' --exclude='node_modules' --exclude='.venv' /home/user/ /backup/home/

# Файл исключений
rsync -avh --exclude-from='exclude.txt' /home/user/ /backup/home/

# Из файла исключений:
# .cache
# **/node_modules
# **/__pycache__
# *.pyc
# .venv
# .local/share/Trash

# Проверка (dry-run)
rsync -avhn --delete /home/user/ /backup/home/
# -n = dry-run (только показать что будет сделано)

# С прогрессом
rsync -avh --progress /home/user/ /backup/home/
rsync -avh --info=progress2 /home/user/ /backup/home/  # общий прогресс
```

### Автоматический бэкап (cron)
```bash
# crontab -e
0 2 * * * rsync -avh --delete /home/user/ /backup/home/ >> /var/log/backup.log 2>&1

# Или systemd timer
# /etc/systemd/system/backup.timer
# [Timer]
# OnCalendar=daily
# Persistent=true
```

## Borg Backup — дедупликация + шифрование

### Установка
```bash
sudo pacman -S borg                 # Arch
sudo apt install borgbackup         # Debian/Ubuntu
sudo dnf install borgbackup         # Fedora
```

### Использование
```bash
# Инициализация репозитория
borg init --encryption=repokey /backup/borg

# Инициализация на удалённом сервере
borg init --encryption=repokey ssh://user@server/backup/borg

# Создание бэкапа
borg create --progress --stats \
  /backup/borg::home-{now:%Y-%m-%d} \
  /home/user \
  --exclude '/home/user/.cache' \
  --exclude '/home/user/.local/share/Trash'

# Список архивов
borg list /backup/borg

# Информация об архиве
borg info /backup/borg::home-2026-03-05

# Просмотр содержимого
borg list /backup/borg::home-2026-03-05

# Восстановление
cd /
borg extract /backup/borg::home-2026-03-05

# Восстановить конкретный файл
borg extract /backup/borg::home-2026-03-05 home/user/documents/important.txt

# Монтирование как FUSE
borg mount /backup/borg::home-2026-03-05 /mnt/backup
ls /mnt/backup
borg umount /mnt/backup

# Ротация (удаление старых бэкапов)
borg prune --keep-daily=7 --keep-weekly=4 --keep-monthly=6 /backup/borg

# Компактификация (освобождение места после prune)
borg compact /backup/borg
```

### Скрипт автоматического бэкапа Borg
```bash
#!/bin/bash
set -euo pipefail
REPO="/backup/borg"
export BORG_PASSPHRASE="secret"  # или файл, или keyfile

borg create --stats --progress \
  "$REPO"::"{hostname}-{now:%Y-%m-%d_%H:%M}" \
  /home /etc \
  --exclude '*.cache' \
  --exclude '*/.venv' \
  --exclude '*/node_modules'

borg prune --keep-daily=7 --keep-weekly=4 --keep-monthly=12 "$REPO"
borg compact "$REPO"
```

## Restic — быстрый инкрементальный бэкап

### Установка
```bash
sudo pacman -S restic               # Arch
sudo apt install restic              # Debian
```

### Использование
```bash
# Инициализация
restic init --repo /backup/restic
restic -r sftp:user@server:/backup/restic init  # удалённый
restic -r s3:s3.amazonaws.com/bucket init       # S3

# Бэкап
restic -r /backup/restic backup /home/user \
  --exclude='.cache' --exclude='node_modules'

# Список снапшотов
restic -r /backup/restic snapshots

# Восстановление
restic -r /backup/restic restore latest --target /restore/

# Монтирование
restic -r /backup/restic mount /mnt/restic

# Ротация
restic -r /backup/restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune

# Проверка целостности
restic -r /backup/restic check
```

## Timeshift — снапшоты системы

### Для BTRFS (рекомендуется)
```bash
sudo pacman -S timeshift
sudo timeshift --create --comments "Before update"
sudo timeshift --list
sudo timeshift --restore

# GUI:
timeshift-gtk

# Автоматические снапшоты
# GUI: Schedule → включить daily/weekly/monthly
```

### Для ext4 (через rsync)
```bash
# Timeshift использует rsync для ext4
sudo timeshift --create --snapshot-device /dev/sdb1
```

## Snapper — снапшоты BTRFS (openSUSE, Arch)
```bash
sudo pacman -S snapper snap-pac

# Конфигурация для корня
sudo snapper -c root create-config /

# Создать снапшот вручную
sudo snapper -c root create -d "Before system update"

# Список снапшотов
sudo snapper -c root list

# Сравнить два снапшота
sudo snapper -c root diff 1..2

# Откатить изменения
sudo snapper -c root undochange 1..2

# Автоматические снапшоты при pacman (snap-pac)
# Автоматически создаёт pre/post снапшоты при -S/-R/-U

# Настроить ротацию
sudo snapper -c root set-config "NUMBER_LIMIT=10"
sudo snapper -c root set-config "TIMELINE_CREATE=yes"
sudo snapper -c root set-config "TIMELINE_LIMIT_DAILY=7"
sudo snapper -c root set-config "TIMELINE_LIMIT_WEEKLY=4"
```

## Облачный бэкап

### rclone — «rsync для облака»
```bash
sudo pacman -S rclone
rclone config                       # интерактивная настройка

# Поддерживаемые провайдеры:
# Google Drive, Dropbox, OneDrive, S3, Backblaze B2, Yandex Disk, Mega и 40+

# Синхронизация
rclone sync /home/user/documents remote:backup/documents
rclone sync /home/user/documents gdrive:backup/documents

# Копирование (не удаляет лишнее)
rclone copy /home/user/documents remote:backup/

# Монтирование как FS
rclone mount remote:/ /mnt/cloud --vfs-cache-mode full &

# Шифрование
rclone config  # создать crypt remote поверх обычного

# Проверка
rclone check /home/user/documents remote:backup/documents
```

## Сравнение инструментов
| Инструмент | Дедупликация | Шифрование | Облако | Снапшоты | Скорость |
|-----------|-------------|-----------|--------|----------|----------|
| rsync | Нет | Нет | SSH | Нет | Быстрый |
| Borg | Да | Да | SSH | Да | Средний |
| Restic | Да | Да | S3/SFTP | Да | Быстрый |
| Timeshift | Нет | Нет | Нет | BTRFS/rsync | Быстрый |
| Snapper | Нет | Нет | Нет | BTRFS | Мгновенный |
| rclone | Нет | Опционально | 40+ | Нет | Зависит |
