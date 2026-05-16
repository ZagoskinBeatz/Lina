# Пользователи, группы и sudo

## Управление пользователями

```bash
# Информация о текущем пользователе
whoami                       # Имя пользователя
id                           # UID, GID, группы
groups                       # Список моих групп

# Информация о пользователе
id username
finger username              # Подробная информация (если установлен)

# Создать пользователя
sudo useradd -m -s /bin/bash username       # С домашним каталогом и bash
sudo useradd -m -G wheel,video username     # + группы

# Установить/изменить пароль
sudo passwd username

# Изменить пользователя
sudo usermod -aG wheel username             # Добавить в группу wheel (sudo)
sudo usermod -aG docker username            # Добавить в группу docker
sudo usermod -s /bin/zsh username           # Изменить оболочку
sudo usermod -l newname oldname             # Переименовать

# Удалить пользователя
sudo userdel username
sudo userdel -r username     # + удалить домашний каталог

# Список пользователей
cat /etc/passwd              # Все пользователи
getent passwd                # То же, но через NSS
awk -F: '$3 >= 1000 {print $1}' /etc/passwd  # Только обычные юзеры
```

## Управление группами

```bash
# Создать группу
sudo groupadd developers

# Добавить пользователя в группу
sudo usermod -aG developers username
# ИЛИ
sudo gpasswd -a username developers

# Удалить из группы
sudo gpasswd -d username developers

# Удалить группу
sudo groupdel developers

# Список групп
cat /etc/group
groups username              # Группы пользователя

# Применить новые группы без перезагрузки
newgrp groupname
# ИЛИ перезайти в систему
```

## sudo

### Настройка
```bash
# Редактировать sudoers (ТОЛЬКО через visudo!)
sudo visudo

# Или файл в /etc/sudoers.d/
sudo visudo -f /etc/sudoers.d/custom
```

### Типичные правила sudoers
```
# Формат: КТО ОТКУДА=(ОТ КОГО) КОМАНДЫ

# Полный доступ для группы wheel
%wheel ALL=(ALL:ALL) ALL

# Без пароля для группы wheel
%wheel ALL=(ALL:ALL) NOPASSWD: ALL

# Конкретная команда без пароля
username ALL=(ALL) NOPASSWD: /usr/bin/pacman

# Только определённые команды
username ALL=(ALL) /usr/bin/systemctl restart nginx, /usr/bin/systemctl status nginx

# Запретить команду
username ALL=(ALL) ALL, !/usr/bin/su
```

### Использование sudo
```bash
sudo command                 # Выполнить от root
sudo -u user command         # Выполнить от другого пользователя
sudo -i                     # Интерактивная root-оболочка
sudo -s                     # Root-оболочка (текущий каталог)
sudo -l                     # Показать мои sudo-права
sudo !!                     # Повторить последнюю команду через sudo
```

## PAM (Pluggable Authentication Modules)

### Конфигурация
```
/etc/pam.d/                  # Каталог конфигурации
/etc/pam.d/system-auth       # Основная аутентификация
/etc/pam.d/sudo              # Правила для sudo
/etc/pam.d/login             # Правила для входа
/etc/pam.d/sshd              # Правила для SSH
```

### Пример: ограничить число попыток входа
```
# /etc/pam.d/system-auth
auth required pam_faillock.so preauth deny=5 unlock_time=600
```

## Файлы аутентификации

| Файл | Содержимое |
|------|-----------|
| `/etc/passwd` | Список пользователей (login:x:UID:GID:comment:home:shell) |
| `/etc/shadow` | Хеши паролей (только root) |
| `/etc/group` | Список групп |
| `/etc/gshadow` | Пароли групп |
| `/etc/login.defs` | Настройки: мин/макс UID, политика паролей |

## Блокировка учётных записей

```bash
# Заблокировать пользователя
sudo usermod -L username
sudo passwd -l username

# Разблокировать
sudo usermod -U username
sudo passwd -u username

# Установить срок действия пароля
sudo chage -M 90 username   # Макс. 90 дней
sudo chage -l username      # Показать политику
```

## Типичные проблемы

### «Пользователь не в sudoers»
```bash
# Проблема: username is not in the sudoers file
# Решение: добавить в группу wheel (Arch/Fedora) или sudo (Debian/Ubuntu)
su -                         # Войти как root
usermod -aG wheel username   # Arch, Fedora
usermod -aG sudo username    # Debian, Ubuntu
```

### «Permission denied» при доступе к USB/дискам
```bash
# Добавить в группу storage/plugdev
sudo usermod -aG storage username    # Arch
sudo usermod -aG plugdev username    # Debian
```

### Проблемы с правами после chown -R
```bash
# Восстановить стандартные права
find /home/user -type d -exec chmod 755 {} \;
find /home/user -type f -exec chmod 644 {} \;
chmod 700 /home/user/.ssh
chmod 600 /home/user/.ssh/*
```

## polkit (PolicyKit)

polkit — система авторизации для выполнения привилегированных операций
без предоставления полного root-доступа (через GUI-диалоги).

### Конфигурация
```bash
# Правила: /etc/polkit-1/rules.d/
# Доступные действия:
pkaction                         # Все доступные действия
pkaction --verbose | grep -A5 "org.freedesktop.systemd1.manage-units"
```

### Пример: разрешить группе управлять NetworkManager
```javascript
// /etc/polkit-1/rules.d/10-network.rules
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.NetworkManager.settings.modify.system" &&
        subject.isInGroup("network")) {
        return polkit.Result.YES;
    }
});
```

### Пример: разрешить перезагрузку без пароля
```javascript
// /etc/polkit-1/rules.d/20-reboot.rules
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.login1.reboot" &&
        subject.isInGroup("users")) {
        return polkit.Result.YES;
    }
});
```

## Системные пользователи и группы

### Важные системные группы

| Группа | Назначение |
|--------|-----------|
| wheel | Право на sudo (Arch, Fedora) |
| sudo | Право на sudo (Debian, Ubuntu) |
| video | Доступ к видеоустройствам |
| audio | Доступ к аудиоустройствам |
| input | Доступ к устройствам ввода |
| storage | Доступ к съёмным дискам |
| plugdev | Доступ к USB и подключаемым устройствам |
| docker | Запуск Docker без sudo |
| libvirt | Управление виртуальными машинами |
| kvm | Доступ к KVM |
| lp | Доступ к принтерам |
| scanner | Доступ к сканерам |
| network | Управление сетью |
| dialout | Доступ к COM-портам (Arduino и т.п.) |

### Создание системного пользователя (для сервиса)
```bash
# Системный пользователь (без логина, без домашнего каталога)
sudo useradd -r -s /usr/bin/nologin -M myservice

# Или с каталогом (для хранения данных)
sudo useradd -r -s /usr/bin/nologin -d /var/lib/myservice -m myservice
```

## Аудит пользователей

```bash
# Последние входы в систему
last                             # Журнал входов
last -f /var/log/wtmp            # Подробный журнал
lastlog                          # Последний вход каждого пользователя
faillog -a                       # Неудачные попытки входа

# Кто сейчас в системе
who                              # Текущие сессии
w                                # + что делают (процессы)
users                            # Список имён

# Текущие процессы пользователя
ps -u username

# История действий (если auditd настроен)
sudo ausearch -ua 1000           # Действия по UID
```

## Миграция пользователя

```bash
# Копировать пользователя на другую машину
# 1. Экспортировать запись из /etc/passwd, /etc/shadow, /etc/group
grep "^username:" /etc/passwd >> /tmp/user_export
grep "^username:" /etc/shadow >> /tmp/shadow_export

# 2. Скопировать файлы
sudo rsync -avz /home/username/ user@newhost:/home/username/

# 3. Импортировать записи на новой машине
sudo cat /tmp/user_export >> /etc/passwd
sudo cat /tmp/shadow_export >> /etc/shadow
sudo chown -R username:username /home/username
```
