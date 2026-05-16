# Безопасность Linux

## Firewall

### UFW (Uncomplicated Firewall)
```bash
# Установка и включение
sudo pacman -S ufw   # или apt install ufw
sudo ufw enable
sudo ufw status verbose

# Базовые правила
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh         # порт 22
sudo ufw allow 80/tcp      # HTTP
sudo ufw allow 443/tcp     # HTTPS
sudo ufw allow 8080/tcp    # кастомный порт

# Удаление правила
sudo ufw delete allow 8080/tcp

# Разрешить с конкретного IP
sudo ufw allow from 192.168.1.0/24 to any port 22
```

### iptables / nftables
```bash
# Просмотр правил
sudo iptables -L -n -v
sudo nft list ruleset

# Базовый набор nftables
sudo nft add table inet filter
sudo nft add chain inet filter input '{ type filter hook input priority 0; policy drop; }'
sudo nft add rule inet filter input ct state established,related accept
sudo nft add rule inet filter input iif lo accept
sudo nft add rule inet filter input tcp dport 22 accept
```

## SSH безопасность
```bash
# /etc/ssh/sshd_config
PermitRootLogin no              # запрет root входа
PasswordAuthentication no        # только ключи
MaxAuthTries 3                   # макс. попыток
Port 2222                        # нестандартный порт
AllowUsers user1 user2          # только определённые пользователи

# Генерация SSH-ключа
ssh-keygen -t ed25519 -C "user@host"
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@server
```

## Шифрование

### LUKS (шифрование диска)
```bash
# Создание зашифрованного раздела
sudo cryptsetup luksFormat /dev/sdX
sudo cryptsetup open /dev/sdX encrypted_name
sudo mkfs.ext4 /dev/mapper/encrypted_name

# Монтирование
sudo cryptsetup open /dev/sdX encrypted_name
sudo mount /dev/mapper/encrypted_name /mnt

# Проверка статуса
sudo cryptsetup status encrypted_name
```

### GPG
```bash
# Генерация ключа
gpg --full-gen-key

# Шифрование файла
gpg -c file.txt                  # симметричное (пароль)
gpg -e -r user@email file.txt   # асимметричное (ключ)

# Расшифровка
gpg -d file.txt.gpg
```

## Аудит и мониторинг
```bash
# Последние входы
last
lastb                           # неудачные попытки
who                              # текущие сессии

# Журналы безопасности
journalctl -u sshd --since "1 hour ago"

# Проверка открытых портов
ss -tulnp                        # все слушающие порты
nmap -sT localhost               # сканирование портов

# Проверка SUID-файлов
find / -perm -4000 -type f 2>/dev/null
```

## Рекомендации
1. Регулярно обновляйте систему
2. Используйте сложные пароли + 2FA
3. Минимизируйте запущенные сервисы
4. Делайте резервные копии
5. Включите firewall
6. Используйте SELinux/AppArmor

## AppArmor
```bash
# Статус
sudo aa-status

# Профили (/etc/apparmor.d/)
sudo aa-enforce /etc/apparmor.d/usr.bin.firefox    # enforce
sudo aa-complain /etc/apparmor.d/usr.bin.firefox   # только логировать
sudo aa-disable /etc/apparmor.d/usr.bin.firefox    # отключить

# Создать профиль для приложения
sudo aa-genprof /usr/bin/myapp

# Обновить профиль по логам
sudo aa-logprof
```

## SELinux (Fedora, RHEL, CentOS)
```bash
# Статус
getenforce                     # Enforcing / Permissive / Disabled
sestatus                       # подробно

# Переключение режима (до перезагрузки)
sudo setenforce Permissive
sudo setenforce Enforcing

# Постоянно: /etc/selinux/config
SELINUX=enforcing              # enforcing / permissive / disabled

# Управление контекстами
ls -Z /var/www/html/           # показать контексты файлов
sudo chcon -t httpd_sys_content_t /var/www/html/index.html
sudo restorecon -Rv /var/www/html/  # восстановить контексты по умолчанию

# Булевы переключатели
getsebool -a | grep httpd      # показать все для httpd
sudo setsebool -P httpd_enable_homedirs on

# Отладка
sudo ausearch -m avc --recent  # последние отказы
sudo audit2allow -a            # сгенерировать разрешающую политику
```

## Fail2ban (защита от brute-force)
```bash
# Установка
sudo pacman -S fail2ban        # Arch
sudo apt install fail2ban      # Debian/Ubuntu

# Конфигурация (/etc/fail2ban/jail.local)
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 3

[sshd]
enabled = true
port = ssh
logpath = %(sshd_log)s

# Управление
sudo systemctl enable --now fail2ban
sudo fail2ban-client status           # общий статус
sudo fail2ban-client status sshd      # статус jail
sudo fail2ban-client set sshd unbanip <IP>  # разбанить IP
```

## Управление пользователями и паролями
```bash
# Политика паролей (/etc/security/pwquality.conf)
minlen = 12
minclass = 3          # минимум 3 класса символов
maxrepeat = 3         # макс. повторяющихся символов
reject_username       # пароль не должен содержать имя пользователя

# Срок действия пароля
sudo chage -M 90 user    # макс. 90 дней
sudo chage -l user       # показать политику
sudo passwd -l user      # заблокировать аккаунт
sudo passwd -u user      # разблокировать

# Sudo безопасность (/etc/sudoers через visudo)
Defaults    passwd_timeout=1
Defaults    timestamp_timeout=5
Defaults    logfile="/var/log/sudo.log"
user ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart nginx
```

## PAM (Pluggable Authentication Modules)
```bash
# Конфигурация: /etc/pam.d/
# Двухфакторная аутентификация с Google Authenticator
sudo pacman -S libpam-google-authenticator
google-authenticator                     # настройка для пользователя
# Добавить в /etc/pam.d/sshd:
# auth required pam_google_authenticator.so

# Ограничение su
# /etc/pam.d/su:
# auth required pam_wheel.so             # только группа wheel
```

## Проверка безопасности системы
```bash
# Сканирование руткитов
sudo rkhunter --check
sudo chkrootkit

# Аудит системы Lynis
sudo lynis audit system

# Проверка уязвимостей ядра
cat /proc/cmdline          # параметры ядра
# Рекомендованные параметры ядра:
# slab_nomerge init_on_alloc=1 init_on_free=1
# page_alloc.shuffle=1 randomize_kstack_offset=on

# Hardening sysctl (/etc/sysctl.d/99-hardening.conf)
kernel.randomize_va_space = 2
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
kernel.perf_event_paranoid = 3
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv6.conf.all.accept_redirects = 0

# Применить
sudo sysctl --system
```

## Шифрование файлов и каталогов
```bash
# age — простое шифрование (замена GPG для файлов)
age-keygen -o key.txt              # генерация ключа
age -R key.txt.pub file.tar > file.tar.age  # шифрование
age -d -i key.txt file.tar.age > file.tar   # дешифрование

# fscrypt — шифрование каталогов (ext4, f2fs)
sudo fscrypt setup
fscrypt encrypt ~/Private

# VeraCrypt — шифрованные контейнеры
veracrypt -c                       # создать контейнер
veracrypt /path/to/container /mnt  # монтировать
veracrypt -d /mnt                  # размонтировать
```

## Безопасность сети
```bash
# Проверка открытых портов
ss -tulnp
nmap -sV localhost

# DNS over TLS (systemd-resolved)
# /etc/systemd/resolved.conf
[Resolve]
DNS=1.1.1.1#cloudflare-dns.com 9.9.9.9#dns.quad9.net
DNSOverTLS=yes

# WireGuard VPN
sudo wg-quick up wg0
sudo wg-quick down wg0
sudo wg show
```
