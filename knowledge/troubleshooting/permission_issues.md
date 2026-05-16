# Проблемы с правами доступа

## Permission denied — анализ

### Базовая диагностика
```bash
# Проверить права
ls -la /path/to/file
stat /path/to/file
namei -l /path/to/file              # права вдоль всего пути

# Кто я?
id                                   # uid, gid, groups
whoami
groups

# SELinux / AppArmor
getenforce                           # SELinux
aa-status                            # AppArmor
ls -Z /path/to/file                  # SELinux контексты
```

### Стандартные разрешения UNIX
```
r (4) — чтение
w (2) — запись
x (1) — выполнение (для каталогов — вход)

Формат: rwxrwxrwx = владелец|группа|остальные

Примеры:
755 = rwxr-xr-x   — исполняемые файлы, каталоги
644 = rw-r--r--   — обычные файлы
600 = rw-------   — приватные файлы
700 = rwx------   — приватные каталоги
777 = rwxrwxrwx   — ⚠ всем всё (небезопасно!)
```

```bash
# Изменение прав
chmod 755 file
chmod u+x script.sh                  # +execute для владельца
chmod g+w file                       # +write для группы
chmod o-rwx file                     # убрать всё для остальных
chmod -R 755 directory/              # рекурсивно

# Изменение владельца
chown user:group file
chown -R user:group directory/
chown user file                      # только владелец
chgrp group file                     # только группа
```

### Специальные биты
```bash
# SUID (4xxx) — выполняется от имени владельца
chmod u+s /usr/bin/prog
chmod 4755 /usr/bin/prog
# Пример: /usr/bin/passwd (запись в /etc/shadow)

# SGID (2xxx) — выполняется от имени группы
chmod g+s directory/
chmod 2755 directory/
# Файлы в каталоге наследуют группу каталога

# Sticky bit (1xxx) — удалять может только владелец
chmod +t /tmp
chmod 1777 /tmp
# Пример: /tmp — все могут писать, удалять только своё

# Найти SUID/SGID файлы (аудит безопасности)
find / -perm -4000 -type f 2>/dev/null  # SUID
find / -perm -2000 -type f 2>/dev/null  # SGID
```

## ACL — расширенные списки доступа
```bash
# Проверить
getfacl /path/to/file

# Установить
setfacl -m u:username:rwx /path/to/file      # пользователю
setfacl -m g:groupname:rx /path/to/file       # группе
setfacl -m o::r /path/to/file                 # остальным

# ACL по умолчанию (для новых файлов в каталоге)
setfacl -d -m u:username:rwx /path/to/dir/
setfacl -d -m g:developers:rwx /path/to/dir/

# Рекурсивно
setfacl -R -m u:username:rwx /path/to/dir/

# Удалить ACL
setfacl -x u:username /path/to/file
setfacl -b /path/to/file                     # удалить все ACL

# Копировать ACL
getfacl dir1 | setfacl --set-file=- dir2
```

## Capabilities — гранулярные привилегии
```bash
# Capabilities заменяют SUID для конкретных привилегий
# Вместо SUID root:

# Привязка к портам < 1024
sudo setcap cap_net_bind_service=+ep /usr/bin/program

# Работа с сетью (raw sockets для ping)
sudo setcap cap_net_raw=+ep /usr/bin/ping

# Проверить capabilities
getcap /usr/bin/ping
getcap -r / 2>/dev/null              # все файлы с capabilities

# Удалить
sudo setcap -r /usr/bin/program

# Часто используемые
# cap_net_bind_service — привязка к привилегированным портам
# cap_net_raw — raw sockets
# cap_dac_override — игнорировать проверку прав на файлы
# cap_sys_admin — почти как root
# cap_sys_ptrace — отладка чужих процессов
```

## Типичные проблемы и решения

### «Permission denied» при запуске скрипта
```bash
# Проверить права на выполнение
ls -la script.sh
# Решение:
chmod +x script.sh
# Или:
bash script.sh                       # запустить через интерпретатор
```

### Нет доступа к USB/внешнему диску
```bash
# Проверить
lsblk
udisksctl mount -b /dev/sdb1        # через udisks (без sudo)

# Группа для доступа к дискам
sudo usermod -aG disk $USER
sudo usermod -aG storage $USER      # Arch

# Правила udev для автомонтирования
# /etc/udev/rules.d/99-usb.rules
```

### Нет доступа к /dev/ttyUSB0 (Arduino, модемы)
```bash
ls -la /dev/ttyUSB0
# crw-rw---- 1 root uucp ...

sudo usermod -aG uucp $USER         # Arch
sudo usermod -aG dialout $USER      # Debian/Ubuntu
# Перелогиниться!
```

### Проблемы с Docker без root
```bash
# Добавить в группу docker
sudo usermod -aG docker $USER
newgrp docker                        # без перелогинивания

# Или использовать Podman (rootless по умолчанию)
```

### Flatpak не имеет доступа к файлам
```bash
# Через Flatseal или CLI
flatpak override --user --filesystem=home com.app.Name
flatpak override --user --filesystem=/media com.app.Name
flatpak override --user --filesystem=xdg-download com.app.Name

# Проверить текущие разрешения
flatpak info --show-permissions com.app.Name
```

### Файлы с immutable-флагом
```bash
# Проверить
lsattr /path/to/file
# ----i--------e-- = immutable

# Снять immutable
sudo chattr -i /path/to/file

# Установить
sudo chattr +i /path/to/file        # защита от изменений

# Append only
sudo chattr +a /path/to/file        # только дополнение (для логов)
```

### Проблемы SELinux
```bash
# Статус
getenforce                           # Enforcing, Permissive, Disabled

# Проверить контексты
ls -Z /path/to/file
ps -Z

# Восстановить контекст
sudo restorecon -Rv /path/

# Временно разрешить
sudo setenforce 0                    # Permissive (логирует, не блокирует)

# Проверить аудит
sudo ausearch -m avc -ts recent
sudo audit2why < /var/log/audit/audit.log

# Создать разрешающий модуль
sudo audit2allow -a -M mypolicy
sudo semodule -i mypolicy.pp
```

### Проблемы AppArmor
```bash
# Статус
sudo aa-status

# Перевести в complain mode
sudo aa-complain /usr/bin/program

# Вернуть enforce
sudo aa-enforce /usr/bin/program

# Логи
sudo dmesg | grep apparmor
journalctl | grep apparmor
```

## Polkit — привилегии для сервисов
```bash
# Polkit управляет доступом к системным сервисам
# (монтирование, NetworkManager, systemctl)

# Правила: /etc/polkit-1/rules.d/
# Пример: разрешить группе admin монтирование без пароля

# /etc/polkit-1/rules.d/10-mount.rules
# polkit.addRule(function(action, subject) {
#     if (action.id == "org.freedesktop.udisks2.filesystem-mount" &&
#         subject.isInGroup("admin")) {
#         return polkit.Result.YES;
#     }
# });
```

## Umask — права по умолчанию
```bash
# Текущий umask
umask                                # 0022

# Как работает: 0666 - umask = права файлов, 0777 - umask = права каталогов
# umask 0022: файлы = 644, каталоги = 755
# umask 0077: файлы = 600, каталоги = 700

# Установить
umask 077                            # приватный режим

# Постоянно: добавить в ~/.bashrc или ~/.profile
```
