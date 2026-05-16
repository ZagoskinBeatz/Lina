# Безопасность Linux — Файрвол, UFW, nftables, hardening

## Файрвол

### UFW (Uncomplicated Firewall)

UFW — простой интерфейс над iptables/nftables. Рекомендуется для десктопов.

```bash
# Установка
sudo pacman -S ufw           # Arch
sudo apt install ufw          # Ubuntu

# Включение
sudo ufw enable
sudo systemctl enable ufw

# Базовые правила
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh            # или: sudo ufw allow 22/tcp
sudo ufw allow 80/tcp         # HTTP
sudo ufw allow 443/tcp        # HTTPS

# Удаление правила
sudo ufw delete allow 80/tcp

# Статус
sudo ufw status verbose
sudo ufw status numbered       # с номерами
```

### nftables (современный)

Замена iptables. Используется по умолчанию в Fedora, Debian 11+.

```bash
# Просмотр правил
sudo nft list ruleset

# Простой файрвол
sudo nft add table inet filter
sudo nft add chain inet filter input { type filter hook input priority 0 \; policy drop \; }
sudo nft add rule inet filter input ct state established,related accept
sudo nft add rule inet filter input iif lo accept
sudo nft add rule inet filter input tcp dport 22 accept
```

### firewalld (Fedora, RHEL)

```bash
# Статус
sudo firewall-cmd --state
sudo firewall-cmd --list-all

# Открыть порт
sudo firewall-cmd --permanent --add-port=8080/tcp
sudo firewall-cmd --reload

# Разрешить сервис
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --reload
```

## Укрепление системы (Hardening)

### Базовые шаги

1. **Обновления**: Всегда обновляйте систему
2. **Минимум пакетов**: Устанавливайте только нужное
3. **Сильные пароли**: Минимум 12 символов
4. **SSH**: Отключить root-доступ, использовать ключи
5. **Файрвол**: Включить, закрыть ненужные порты

### SSH hardening

Файл: `/etc/ssh/sshd_config`

```text
PermitRootLogin no
PasswordAuthentication no        # Только ключи
PubkeyAuthentication yes
MaxAuthTries 3
AllowUsers myuser
Protocol 2
```

### Fail2ban

```bash
# Установка
sudo pacman -S fail2ban
sudo systemctl enable --now fail2ban

# Конфигурация /etc/fail2ban/jail.local
[sshd]
enabled = true
port = ssh
maxretry = 3
bantime = 3600
```

### Аудит безопасности

```bash
# Lynis — аудит безопасности
sudo pacman -S lynis
sudo lynis audit system

# Проверка открытых портов
sudo ss -tlnp
sudo nmap -sT localhost

# Проверка SUID файлов
find / -perm -4000 -type f 2>/dev/null

# Проверка прав /etc/shadow
ls -la /etc/shadow              # Должен быть -rw------- root
```

## AppArmor / SELinux

### AppArmor (Ubuntu, openSUSE)

```bash
# Статус
sudo aa-status

# Перевести профиль в enforce/complain
sudo aa-enforce /etc/apparmor.d/<profile>
sudo aa-complain /etc/apparmor.d/<profile>
```

### SELinux (Fedora, RHEL)

```bash
# Статус
getenforce                     # Enforcing / Permissive / Disabled
sestatus

# Временно отключить
sudo setenforce 0              # Permissive (логирует, не блокирует)

# Посмотреть отказы
sudo ausearch -m avc -ts recent
```

## Шифрование

### LUKS (шифрование дисков)

```bash
# Шифрование раздела
sudo cryptsetup luksFormat /dev/sdX
sudo cryptsetup open /dev/sdX encrypted
sudo mkfs.ext4 /dev/mapper/encrypted
sudo mount /dev/mapper/encrypted /mnt

# Автомонтирование через /etc/crypttab и /etc/fstab
```

### GPG (шифрование файлов)

```bash
# Создать ключ
gpg --full-gen-key

# Шифрование / дешифрование
gpg -e -r user@email.com file.txt     # → file.txt.gpg
gpg -d file.txt.gpg > file.txt        # дешифровка
```
