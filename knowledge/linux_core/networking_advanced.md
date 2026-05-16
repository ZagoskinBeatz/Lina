# Сетевое администрирование Linux

## NetworkManager

### Управление через nmcli
```bash
# Статус
nmcli general status
nmcli device status
nmcli connection show
nmcli connection show --active

# Wi-Fi
nmcli device wifi list
nmcli device wifi connect "SSID" password "пароль"
nmcli device wifi connect "SSID" password "пароль" hidden yes
nmcli connection modify "SSID" wifi-sec.key-mgmt wpa-psk

# Создание подключения
nmcli connection add type ethernet con-name "Проводное" ifname eth0
nmcli connection add type wifi ssid "MyWiFi" con-name "Wi-Fi" \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "password"

# Статический IP
nmcli connection modify "Проводное" \
  ipv4.method manual \
  ipv4.addresses 192.168.1.100/24 \
  ipv4.gateway 192.168.1.1 \
  ipv4.dns "8.8.8.8,8.8.4.4"

# DHCP обратно
nmcli connection modify "Проводное" ipv4.method auto

# Активация / деактивация
nmcli connection up "Проводное"
nmcli connection down "Проводное"
nmcli device disconnect eth0

# DNS
nmcli connection modify "Проводное" ipv4.dns "1.1.1.1 9.9.9.9"
nmcli connection modify "Проводное" ipv4.ignore-auto-dns yes
```

### Файлы конфигурации
```
/etc/NetworkManager/NetworkManager.conf       # основной конфиг
/etc/NetworkManager/conf.d/                   # дополнительные конфиги
/etc/NetworkManager/system-connections/        # сохранённые подключения
```

## systemd-networkd (альтернатива NM)
```ini
# /etc/systemd/network/20-wired.network
[Match]
Name=en*

[Network]
DHCP=yes
DNS=1.1.1.1
DNS=9.9.9.9

[DHCPv4]
RouteMetric=100
```

```bash
sudo systemctl enable --now systemd-networkd
sudo systemctl enable --now systemd-resolved
sudo ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
```

## Диагностика сети

### Пошаговая проверка
```bash
# 1. Интерфейсы
ip addr show
ip link show

# 2. Маршрутизация
ip route show
ip route get 8.8.8.8

# 3. DNS
resolvectl status
cat /etc/resolv.conf
dig google.com
nslookup google.com
host google.com

# 4. Ping
ping -c 4 8.8.8.8                  # по IP (без DNS)
ping -c 4 google.com               # через DNS
ping6 -c 4 ::1                     # IPv6

# 5. Трассировка
traceroute google.com
mtr google.com                     # интерактивная трассировка (лучше)

# 6. Порты
ss -tulnp                          # слушающие порты
ss -tn                             # активные соединения
nmap -sT localhost                  # сканирование портов
```

### Расширенная диагностика
```bash
# tcpdump — захват пакетов
sudo tcpdump -i eth0 -n port 80    # HTTP трафик
sudo tcpdump -i any -w capture.pcap # сохранить в файл

# curl — тестирование HTTP
curl -I https://example.com        # только заголовки
curl -v https://example.com        # подробный вывод
curl -o /dev/null -s -w '%{time_total}\n' https://example.com  # время ответа

# wget — загрузка
wget -c https://example.com/file   # с возможностью продолжения
wget --mirror --convert-links site # зеркало сайта

# iperf3 — тест пропускной способности
iperf3 -s                          # сервер
iperf3 -c server_ip                # клиент

# ethtool — параметры интерфейса
ethtool eth0
ethtool -s eth0 speed 1000 duplex full
```

## Firewall

### firewalld (Fedora, CentOS, openSUSE)
```bash
# Статус
sudo firewall-cmd --state
sudo firewall-cmd --list-all
sudo firewall-cmd --list-all-zones

# Сервисы
sudo firewall-cmd --add-service=http --permanent
sudo firewall-cmd --add-service=https --permanent
sudo firewall-cmd --remove-service=ssh --permanent

# Порты
sudo firewall-cmd --add-port=8080/tcp --permanent
sudo firewall-cmd --add-port=5000-5010/tcp --permanent

# Применить
sudo firewall-cmd --reload

# Rich rules (сложные правила)
sudo firewall-cmd --add-rich-rule='rule family=ipv4 source address=192.168.1.0/24 service name=ssh accept' --permanent

# Зоны
sudo firewall-cmd --get-active-zones
sudo firewall-cmd --zone=trusted --add-source=192.168.1.0/24 --permanent
```

### iptables / nftables
```bash
# iptables (legacy)
sudo iptables -L -n -v             # показать правила
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
sudo iptables -A INPUT -j DROP
sudo iptables-save > /etc/iptables.rules
sudo iptables-restore < /etc/iptables.rules

# nftables (современная замена)
sudo nft list ruleset
sudo nft add table inet filter
sudo nft add chain inet filter input '{ type filter hook input priority 0; policy drop; }'
sudo nft add rule inet filter input tcp dport 22 accept
sudo nft add rule inet filter input ct state established,related accept
```

### UFW (Ubuntu)
```bash
sudo ufw enable
sudo ufw status verbose
sudo ufw allow 22/tcp
sudo ufw allow from 192.168.1.0/24 to any port 22
sudo ufw deny 8080
sudo ufw delete allow 22/tcp
sudo ufw reset
```

## VPN

### WireGuard
```bash
# Установка
sudo pacman -S wireguard-tools

# Генерация ключей
wg genkey | tee privatekey | wg pubkey > publickey

# /etc/wireguard/wg0.conf
# [Interface]
# PrivateKey = <приватный ключ>
# Address = 10.0.0.1/24
# ListenPort = 51820
#
# [Peer]
# PublicKey = <публичный ключ пира>
# AllowedIPs = 10.0.0.2/32
# Endpoint = peer_ip:51820

# Управление
sudo wg-quick up wg0
sudo wg-quick down wg0
sudo wg show
sudo systemctl enable wg-quick@wg0
```

### OpenVPN
```bash
sudo pacman -S openvpn
sudo openvpn --config client.ovpn # запуск клиента

# Через NetworkManager
sudo pacman -S networkmanager-openvpn
nmcli connection import type openvpn file client.ovpn
nmcli connection up client
```

## Мосты и VLAN

### Bridge (мост)
```bash
# Через nmcli
nmcli connection add type bridge con-name br0 ifname br0
nmcli connection add type bridge-slave con-name br0-port1 ifname eth0 master br0
nmcli connection modify br0 ipv4.method auto
nmcli connection up br0

# Через ip
sudo ip link add br0 type bridge
sudo ip link set eth0 master br0
sudo ip link set br0 up
```

### VLAN
```bash
# Через nmcli
nmcli connection add type vlan con-name vlan100 dev eth0 id 100
nmcli connection modify vlan100 ipv4.method manual ipv4.addresses 192.168.100.1/24

# Через ip
sudo ip link add link eth0 name eth0.100 type vlan id 100
sudo ip addr add 192.168.100.1/24 dev eth0.100
sudo ip link set eth0.100 up
```

## SSH

### Настройка клиента
```bash
# ~/.ssh/config
# Host server1
#     HostName 192.168.1.100
#     User admin
#     Port 2222
#     IdentityFile ~/.ssh/id_ed25519

# Генерация ключей
ssh-keygen -t ed25519 -C "user@host"
ssh-copy-id user@server             # копирование на сервер

# SSH-агент
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519

# Туннели
ssh -L 8080:localhost:80 user@server   # local port forward
ssh -R 8080:localhost:80 user@server   # remote port forward
ssh -D 1080 user@server                # SOCKS-прокси

# SCP / rsync
scp file.txt user@server:/path/
rsync -avz --progress dir/ user@server:/path/
```

### Безопасность SSH сервера
```bash
# /etc/ssh/sshd_config
# Port 2222
# PermitRootLogin no
# PasswordAuthentication no
# PubkeyAuthentication yes
# MaxAuthTries 3
# AllowUsers admin deployer

sudo systemctl restart sshd
```

## DNS

### systemd-resolved
```bash
resolvectl status
resolvectl query example.com
resolvectl statistics
resolvectl flush-caches

# DNS over TLS
# /etc/systemd/resolved.conf
# [Resolve]
# DNS=1.1.1.1#cloudflare-dns.com 9.9.9.9#dns.quad9.net
# DNSOverTLS=yes
# DNSSEC=yes
```

### Файл /etc/hosts
```bash
# Блокировка рекламы / вредоносных сайтов
sudo tee -a /etc/hosts << 'EOF'
0.0.0.0 ads.example.com
0.0.0.0 tracker.example.com
EOF

# Локальные псевдонимы
# 192.168.1.100 myserver.local
```

## Мониторинг сети
```bash
# Пропускная способность
iftop -i eth0                       # по соединениям
nload eth0                          # график нагрузки
vnstat -d                           # статистика по дням
vnstat -m                           # по месяцам
bmon                                # TUI монитор

# Сетевые соединения
ss -s                               # сводка
watch -n 1 'ss -tn | wc -l'        # количество соединений
nethogs                             # трафик по процессам
```

## Таблица портов
| Порт | Протокол | Описание |
|------|----------|----------|
| 22 | SSH | Удалённый доступ |
| 53 | DNS | Разрешение имён |
| 80 | HTTP | Веб (незашифрованный) |
| 443 | HTTPS | Веб (TLS) |
| 631 | CUPS | Печать |
| 3389 | RDP | Удалённый рабочий стол |
| 5353 | mDNS | Avahi / Bonjour |
| 51820 | WireGuard | VPN |
