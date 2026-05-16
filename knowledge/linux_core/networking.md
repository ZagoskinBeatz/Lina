# Сеть в Linux

## Интерфейсы и IP

```bash
# Показать интерфейсы и IP
ip addr                      # Все интерфейсы
ip addr show wlan0           # Конкретный
ip link                      # Состояние интерфейсов
ip link set eth0 up          # Включить интерфейс
ip link set eth0 down        # Отключить

# Добавить IP (временно)
sudo ip addr add 192.168.1.100/24 dev eth0

# Маршруты
ip route                     # Таблица маршрутов
ip route add default via 192.168.1.1 dev eth0
```

## NetworkManager

```bash
# Статус
nmcli general status
nmcli device status
nmcli connection show

# WiFi
nmcli device wifi list                    # Показать сети
nmcli device wifi connect "SSID" password "pass"  # Подключиться
nmcli connection up "MyWifi"              # Активировать
nmcli connection down "MyWifi"            # Отключить
nmcli device wifi rescan                  # Пересканировать

# Ethernet
nmcli connection modify eth0 ipv4.addresses 192.168.1.100/24
nmcli connection modify eth0 ipv4.gateway 192.168.1.1
nmcli connection modify eth0 ipv4.dns "8.8.8.8 8.8.4.4"
nmcli connection modify eth0 ipv4.method manual
nmcli connection up eth0

# Создать профиль
nmcli connection add type wifi con-name "MyWifi" ssid "SSID" \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "password"

# Интерактивный TUI
nmtui
```

## DNS

```bash
# Проверить резолвинг
nslookup google.com
dig google.com
host google.com
resolvectl status            # systemd-resolved

# Настройка DNS
# Через NetworkManager:
nmcli connection modify eth0 ipv4.dns "1.1.1.1 8.8.8.8"

# Через resolv.conf (может перезаписываться):
cat /etc/resolv.conf

# Через systemd-resolved:
sudo systemctl restart systemd-resolved

# Очистить DNS-кэш
sudo resolvectl flush-caches
```

## Диагностика сети

```bash
# Проверка связи
ping -c 4 google.com        # 4 пакета
ping -c 4 8.8.8.8           # По IP (если DNS не работает)

# Трассировка маршрута
traceroute google.com
tracepath google.com
mtr google.com               # Интерактивная трассировка

# Порты и соединения
ss -tlnp                     # Слушающие TCP-порты
ss -ulnp                     # Слушающие UDP-порты
ss -s                        # Статистика
ss -tnp | grep :80           # Кто слушает порт 80

# Скачивание
curl -O https://example.com/file
wget https://example.com/file
curl -I https://example.com  # Только заголовки

# SSH
ssh user@host
ssh -p 2222 user@host        # Нестандартный порт
ssh -i ~/.ssh/key user@host  # С указанием ключа
scp file.txt user@host:/path/  # Копирование
rsync -avz src/ user@host:dst/ # Синхронизация
```

## Firewall

### iptables / nftables
```bash
# Показать правила
sudo iptables -L -n -v
sudo nft list ruleset

# Разрешить SSH
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT

# Заблокировать IP
sudo iptables -A INPUT -s 10.0.0.1 -j DROP
```

### firewalld (Fedora, CentOS)
```bash
sudo firewall-cmd --state
sudo firewall-cmd --list-all
sudo firewall-cmd --add-service=http --permanent
sudo firewall-cmd --add-port=8080/tcp --permanent
sudo firewall-cmd --reload
```

### ufw (Ubuntu)
```bash
sudo ufw status
sudo ufw enable
sudo ufw allow ssh
sudo ufw allow 80/tcp
sudo ufw deny from 10.0.0.1
```

## WiFi проблемы

### WiFi не видит сети
```bash
# Проверить интерфейс
ip link show wlan0
rfkill list                  # Проверить программную/аппаратную блокировку
sudo rfkill unblock wifi     # Разблокировать

# Проверить драйвер
lspci -k | grep -A3 -i network   # PCI WiFi
lsusb | grep -i wireless         # USB WiFi
dmesg | grep -i wifi              # Ошибки ядра

# Перезапустить NetworkManager
sudo systemctl restart NetworkManager
```

### WiFi отключается / нестабилен
```bash
# Отключить энергосбережение WiFi
sudo iwconfig wlan0 power off

# Постоянно (через NetworkManager)
# /etc/NetworkManager/conf.d/wifi-powersave-off.conf
[connection]
wifi.powersave = 2

# Обновить firmware
# Arch: sudo pacman -S linux-firmware
# Ubuntu: sudo apt install linux-firmware
```

### Нет WiFi после установки
```bash
# Определить адаптер
lspci | grep -i network
lsusb | grep -i wireless

# Установить драйвер
# Broadcom:
sudo pacman -S broadcom-wl-dkms   # Arch
sudo apt install bcmwl-kernel-source  # Ubuntu

# Intel:
sudo pacman -S linux-firmware      # Обычно уже есть

# Realtek:
# Часто нужен AUR-пакет или dkms-модуль
```

## VPN

```bash
# WireGuard
sudo pacman -S wireguard-tools
sudo wg-quick up wg0
sudo wg-quick down wg0
sudo wg show

# OpenVPN
sudo openvpn --config client.ovpn

# Через NetworkManager
nmcli connection import type wireguard file wg0.conf
nmcli connection up wg0
```

## Сетевые мосты и VLAN

### Мост (Bridge)
```bash
# Создать мост (для виртуальных машин)
sudo nmcli connection add type bridge ifname br0
sudo nmcli connection add type bridge-slave ifname enp0s3 master br0
sudo nmcli connection up br0

# Через ip
sudo ip link add name br0 type bridge
sudo ip link set enp0s3 master br0
sudo ip link set br0 up
```

### VLAN
```bash
# Создать VLAN 100 на интерфейсе eth0
sudo ip link add link eth0 name eth0.100 type vlan id 100
sudo ip addr add 192.168.100.1/24 dev eth0.100
sudo ip link set eth0.100 up

# Через NetworkManager
nmcli connection add type vlan ifname eth0.100 dev eth0 id 100 \
  ipv4.addresses 192.168.100.1/24 ipv4.method manual
```

## Bonding / Teaming (агрегация каналов)

```bash
# Создать bond (объединение двух интерфейсов)
sudo nmcli connection add type bond ifname bond0 bond.options "mode=active-backup,miimon=100"
sudo nmcli connection add type ethernet ifname enp0s3 master bond0
sudo nmcli connection add type ethernet ifname enp0s4 master bond0
sudo nmcli connection up bond0

# Режимы bonding:
# mode=0 (balance-rr)     — Round-robin (балансировка)
# mode=1 (active-backup)  — Резервирование
# mode=2 (balance-xor)    — XOR-балансировка
# mode=4 (802.3ad)        — LACP (агрегация IEEE)
# mode=6 (balance-alb)    — Адаптивная балансировка
```

## Проксирование

```bash
# Системные переменные прокси
export http_proxy="http://proxy:8080"
export https_proxy="http://proxy:8080"
export no_proxy="localhost,127.0.0.1,.local"

# Для pacman
# /etc/pacman.conf → XferCommand = /usr/bin/curl -x http://proxy:8080 ...

# SOCKS-прокси через SSH
ssh -D 1080 user@server
# Использовать: SOCKS5 127.0.0.1:1080

# tsocks / proxychains (запуск любой программы через прокси)
sudo pacman -S proxychains-ng
# /etc/proxychains.conf:
# socks5 127.0.0.1 1080
proxychains firefox
```

## Сетевая безопасность

### fail2ban
```bash
sudo pacman -S fail2ban
sudo systemctl enable --now fail2ban

# Конфигурация: /etc/fail2ban/jail.local
# [sshd]
# enabled = true
# maxretry = 5
# bantime = 3600
# findtime = 600

# Статус
sudo fail2ban-client status
sudo fail2ban-client status sshd
sudo fail2ban-client unban <IP>    # Разбанить
```

### Сканирование портов своей машины
```bash
# Что слушает снаружи
sudo ss -tlnp
# Или:
sudo nmap -sT localhost
# Закрыть ненужные порты через firewall
```

## Полезные сетевые файлы

| Файл | Назначение |
|------|-----------|
| `/etc/hostname` | Имя хоста |
| `/etc/hosts` | Локальный DNS |
| `/etc/resolv.conf` | DNS-серверы |
| `/etc/nsswitch.conf` | Порядок разрешения имён |
| `/etc/NetworkManager/` | Конфигурация NetworkManager |
| `/etc/systemd/network/` | Конфигурация systemd-networkd |
| `/etc/wpa_supplicant/` | WiFi через wpa_supplicant |
| `/etc/iwd/` | WiFi через iwd |
| `/etc/wireguard/` | WireGuard VPN |
