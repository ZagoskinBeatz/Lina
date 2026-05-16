# Шифрование и безопасность ключей

## GPG (GNU Privacy Guard)

### Генерация ключей

```bash
# Генерация ключевой пары (интерактивно)
gpg --full-generate-key
# Рекомендации:
# Тип: RSA and RSA (или ed25519 для новых)
# Размер: 4096 бит (для RSA)
# Срок: 2 года (можно продлить позже)

# Быстрая генерация (параметры по умолчанию)
gpg --gen-key

# Ed25519 (современный, быстрый, безопасный)
gpg --full-generate-key --expert
# → (9) ECC (sign and encrypt) → Curve 25519
```

### Управление ключами

```bash
# Список ключей
gpg --list-keys                  # Все открытые ключи
gpg --list-keys user@email.com   # Конкретный
gpg --list-secret-keys           # Закрытые ключи
gpg --fingerprint user@email.com # Отпечаток

# Экспорт
gpg --export -a user@email.com > public.asc       # Открытый ключ (ASCII)
gpg --export-secret-keys -a user@email.com > private.asc  # Закрытый (бэкап!)

# Импорт
gpg --import public.asc
gpg --import private.asc

# Импорт с сервера ключей
gpg --keyserver keyserver.ubuntu.com --recv-keys KEY_ID
gpg --keyserver keys.openpgp.org --search-keys user@email.com

# Загрузить свой ключ на сервер
gpg --keyserver keys.openpgp.org --send-keys KEY_ID

# Удаление
gpg --delete-keys KEY_ID         # Открытый ключ
gpg --delete-secret-keys KEY_ID  # Закрытый ключ

# Доверие (trust level)
gpg --edit-key user@email.com
# trust → 5 (ultimate) для своих ключей
# trust → 4 (full) для ключей проверенных людей
```

### Шифрование и подпись файлов

```bash
# Шифрование для получателя
gpg -e -r recipient@email.com file.txt
# Результат: file.txt.gpg

# Шифрование для себя
gpg -e -r your@email.com file.txt

# Шифрование с ASCII-выводом (для email)
gpg -e -a -r recipient@email.com file.txt
# Результат: file.txt.asc

# Симметричное шифрование (паролем, без ключей)
gpg -c file.txt                  # AES-256 по умолчанию
gpg -c --cipher-algo AES256 file.txt

# Дешифрование
gpg -d file.txt.gpg > file.txt
gpg -d file.txt.asc > file.txt

# Подпись файла
gpg --sign file.txt              # Подпись + сжатие → file.txt.gpg
gpg --clearsign file.txt         # Подпись в текстовом виде → file.txt.asc
gpg --detach-sign file.txt       # Отдельная подпись → file.txt.sig

# Проверка подписи
gpg --verify file.txt.sig file.txt
gpg --verify file.txt.asc
```

### Шифрование/подпись каталога

```bash
# Зашифровать каталог
tar czf - directory/ | gpg -e -r user@email.com -o directory.tar.gz.gpg

# Расшифровать
gpg -d directory.tar.gz.gpg | tar xzf -
```

### gpg-agent

```bash
# Кэширование пароля от ключа
# ~/.gnupg/gpg-agent.conf
default-cache-ttl 3600          # 1 час
max-cache-ttl 86400             # Максимум 24 часа

# Перезапустить агент
gpgconf --kill gpg-agent
gpg-agent --daemon
```

## SSH-ключи

### Генерация

```bash
# Ed25519 (рекомендуется)
ssh-keygen -t ed25519 -C "user@email.com"

# RSA 4096 (если Ed25519 не поддерживается)
ssh-keygen -t rsa -b 4096 -C "user@email.com"

# Без интерактивного ввода
ssh-keygen -t ed25519 -f ~/.ssh/mykey -N ""      # Без пароля
ssh-keygen -t ed25519 -f ~/.ssh/mykey -N "pass"   # С паролем
```

### Управление ключами

```bash
# Копировать ключ на сервер
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@server
# Вручную:
cat ~/.ssh/id_ed25519.pub | ssh user@server "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"

# Права доступа (ОБЯЗАТЕЛЬНО)
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
chmod 644 ~/.ssh/id_ed25519.pub
chmod 600 ~/.ssh/authorized_keys
chmod 644 ~/.ssh/config

# Список ключей в агенте
ssh-add -l

# Добавить ключ в агент
ssh-add ~/.ssh/id_ed25519

# Удалить все ключи из агента
ssh-add -D
```

### SSH Config (упрощение подключений)

```bash
# ~/.ssh/config
Host myserver
    HostName 192.168.1.100
    User admin
    Port 2222
    IdentityFile ~/.ssh/id_ed25519
    ForwardAgent yes

Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/github_key

Host *.internal.company.com
    User admin
    ProxyJump bastion.company.com
    IdentityFile ~/.ssh/work_key

# Использование:
ssh myserver                     # Вместо ssh -p 2222 admin@192.168.1.100
```

### SSH Tunneling (проброс портов)

```bash
# Локальный проброс (доступ к удалённому сервису через локальный порт)
ssh -L 8080:localhost:80 user@server
# Теперь http://localhost:8080 → порт 80 на сервере

# Удалённый проброс (доступ к локальному сервису с сервера)
ssh -R 9090:localhost:3000 user@server
# Теперь server:9090 → localhost:3000

# SOCKS прокси
ssh -D 1080 user@server
# Настроить браузер на SOCKS5: 127.0.0.1:1080

# Динамический проброс через jump-хост
ssh -J bastion@jump.example.com user@internal.server
```

### Безопасность SSH-сервера

```bash
# /etc/ssh/sshd_config
PermitRootLogin no                    # Запретить вход root
PasswordAuthentication no             # Только ключи
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
MaxAuthTries 3                        # Макс. попыток
Port 2222                             # Нестандартный порт
AllowUsers admin deploy               # Только эти пользователи
Protocol 2                            # Только SSH2
X11Forwarding no                      # Отключить X11
PrintMotd yes

# Применить:
sudo systemctl restart sshd

# Проверить конфигурацию:
sudo sshd -t
```

## age — современное шифрование

age — простая альтернатива GPG: меньше настроек, проще в использовании.

```bash
# Установка
sudo pacman -S age                   # Arch
sudo apt install age                 # Debian/Ubuntu

# Генерация ключа
age-keygen -o key.txt
# => Public key: age1...
# Файл key.txt содержит закрытый ключ

# Шифрование
age -r age1ql3z... file.txt > file.txt.age
# Для нескольких получателей:
age -r age1abc... -r age1def... file.txt > file.txt.age

# Шифрование паролем (без ключей)
age -p file.txt > file.txt.age

# Шифрование SSH-ключом
age -R ~/.ssh/id_ed25519.pub file.txt > file.txt.age

# Дешифрование
age -d -i key.txt file.txt.age > file.txt
age -d -i ~/.ssh/id_ed25519 file.txt.age > file.txt

# Шифрование каталога
tar czf - directory/ | age -r age1... > directory.tar.gz.age
age -d -i key.txt directory.tar.gz.age | tar xzf -
```

## LUKS — шифрование дисков (расширенно)

### Создание зашифрованного раздела

```bash
# Создать шифрованный раздел
sudo cryptsetup luksFormat /dev/sda2
# Подтвердить YES (заглавными)
# Ввести пароль

# Открыть
sudo cryptsetup open /dev/sda2 cryptdata

# Создать файловую систему
sudo mkfs.ext4 /dev/mapper/cryptdata

# Монтировать
sudo mount /dev/mapper/cryptdata /mnt/data
```

### Управление ключевыми слотами

```bash
# LUKS имеет 8 слотов для разных паролей/ключей (0-7)
# Информация о слотах
sudo cryptsetup luksDump /dev/sda2

# Добавить второй пароль
sudo cryptsetup luksAddKey /dev/sda2

# Удалить слот
sudo cryptsetup luksKillSlot /dev/sda2 1  # Удалить слот 1

# Сменить пароль
sudo cryptsetup luksChangeKey /dev/sda2

# Использовать ключевой файл вместо пароля
dd if=/dev/urandom of=/root/luks.key bs=4096 count=1
chmod 600 /root/luks.key
sudo cryptsetup luksAddKey /dev/sda2 /root/luks.key

# Открыть с ключевым файлом
sudo cryptsetup open /dev/sda2 cryptdata --key-file /root/luks.key
```

### Автомонтирование зашифрованных разделов

```bash
# /etc/crypttab — таблица шифрованных устройств
# <name>  <device>              <password/keyfile>  <options>
cryptdata  UUID=abc123-def456   /root/luks.key      luks
# Или с паролем при загрузке:
cryptdata  UUID=abc123-def456   none                luks

# /etc/fstab — монтирование
/dev/mapper/cryptdata  /data  ext4  defaults  0  2
```

### Бэкап заголовка LUKS

```bash
# ВАЖНО: без заголовка данные невосстановимы!
sudo cryptsetup luksHeaderBackup /dev/sda2 --header-backup-file /safe/luks-header.bak

# Восстановить заголовок
sudo cryptsetup luksHeaderRestore /dev/sda2 --header-backup-file /safe/luks-header.bak
```

## Хеширование и проверка целостности

```bash
# SHA-256 (рекомендуется)
sha256sum file.iso
sha256sum -c checksums.sha256       # Проверить по файлу

# SHA-512
sha512sum file.iso

# MD5 (устарел для безопасности, но используется для проверки)
md5sum file.iso

# b2sum (BLAKE2, быстрый)
b2sum file.iso

# Проверка скачанного ISO
echo "expected_hash  file.iso" | sha256sum -c -

# Хеш всех файлов в каталоге
find . -type f -exec sha256sum {} + > checksums.sha256
```

## Пароли и секреты

### pass — менеджер паролей CLI

```bash
# Инициализация (привязка к GPG-ключу)
pass init GPG_KEY_ID

# Добавить пароль
pass insert email/gmail
pass generate -n 20 web/github    # Сгенерировать 20 символов

# Получить пароль
pass email/gmail                  # Показать
pass -c email/gmail               # Скопировать (очистится через 45 сек)

# Структура
pass ls                           # Дерево паролей
# Хранилище: ~/.password-store/ (зашифрованные GPG-файлы)

# Git-синхронизация
pass git init
pass git remote add origin git@github.com:user/pass-store.git
pass git push -u origin master
```

### Генерация безопасных паролей

```bash
# openssl
openssl rand -base64 32         # 32 байта → base64

# /dev/urandom
head -c 32 /dev/urandom | base64

# pwgen (нужно установить)
pwgen -s 20 1                   # Безопасный, 20 символов

# Python
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

## Таблица алгоритмов

| Алгоритм | Тип | Размер ключа | Использование |
|----------|-----|-------------|---------------|
| AES-256 | Симметричный | 256 бит | Шифрование данных, LUKS |
| ChaCha20 | Симметричный | 256 бит | WireGuard, TLS |
| RSA | Асимметричный | 2048-4096 бит | SSH, GPG (совместимость) |
| Ed25519 | Асимметричный | 256 бит | SSH, GPG (рекомендуется) |
| X25519 | Обмен ключами | 256 бит | age, WireGuard, TLS |
| SHA-256 | Хеш | 256 бит | Проверка целостности |
| BLAKE2 | Хеш | до 512 бит | Быстрый хеш |
| bcrypt | Хеш пароля | — | Хранение паролей |
| Argon2 | Хеш пароля | — | Современное хранение паролей |
