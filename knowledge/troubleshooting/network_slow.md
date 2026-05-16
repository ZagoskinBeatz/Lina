# Медленная сеть и проблемы с интернетом

## Пошаговая диагностика

### Шаг 1 — Физическое подключение
```bash
# Статус интерфейсов
ip link show
nmcli device status

# Wi-Fi сигнал
nmcli device wifi list               # уровень сигнала (dBm)
# -30 = отличный, -50 = хороший, -70 = слабый, -80 = проблема
iwconfig wlan0                       # bitrate, signal level
iw dev wlan0 link                    # подробно

# Ethernet
ethtool eth0 | grep -i "speed\|duplex\|link"
# Speed: 1000Mb/s, Duplex: Full, Link detected: yes
```

### Шаг 2 — IP и маршрутизация
```bash
# IP адрес
ip addr show
# Нет IP → проблема DHCP
# 169.254.x.x → DHCP не работает (self-assigned)

# Маршрут
ip route show
# Нет default → нет шлюза
ip route get 8.8.8.8

# Попробовать получить IP
sudo dhclient -v eth0               # или
sudo nmcli connection up "Wired"
```

### Шаг 3 — DNS
```bash
# Проверка
dig google.com
nslookup google.com
resolvectl query google.com

# Тест: работает по IP, но не по имени? = DNS проблема
ping -c 2 8.8.8.8                   # по IP
ping -c 2 google.com                # по имени

# Сменить DNS
sudo resolvectl dns eth0 1.1.1.1 8.8.8.8
# Или через nmcli
nmcli connection modify "Wired" ipv4.dns "1.1.1.1 8.8.8.8"
nmcli connection modify "Wired" ipv4.ignore-auto-dns yes
nmcli connection up "Wired"

# DNS кэш
resolvectl flush-caches
resolvectl statistics

# Ручной /etc/resolv.conf (не рекомендуется с NM)
# nameserver 1.1.1.1
# nameserver 8.8.8.8
```

### Шаг 4 — Скорость и потери
```bash
# Потери пакетов
ping -c 100 8.8.8.8 | tail -3
# 0% loss = OK, > 5% = проблема

# Трассировка (где именно теряются)
mtr -rw -c 50 google.com
# Loss% на конкретном хопе = проблема на этом участке

# Тест скорости
speedtest-cli
speedtest-cli --simple
curl -o /dev/null -s -w 'Download: %{speed_download} bytes/sec\n' http://speedtest.tele2.net/10MB.zip

# Тест между двумя машинами
# Сервер: iperf3 -s
# Клиент: iperf3 -c server_ip
```

## Типичные проблемы и решения

### Wi-Fi медленный
```bash
# Проверить канал и помехи
nmcli device wifi list | sort -k6 -rn
# Много сетей на одном канале = помехи

# Сменить канал на роутере (1, 6, 11 для 2.4GHz)
# Использовать 5GHz если поддерживается

# Управление питанием Wi-Fi (отключить энергосбережение)
sudo iw dev wlan0 set power_save off
# Постоянно:
# /etc/NetworkManager/conf.d/wifi-powersave.conf
# [connection]
# wifi.powersave = 2

# Драйвер iwlwifi (Intel) — минимальная мощность
sudo modprobe -r iwlwifi
sudo modprobe iwlwifi power_save=0 11n_disable=8

# Realtek — часто проблемный
# Проверить: lspci | grep -i net
# Установить DKMS драйвер если нужно
```

### MTU проблема
```bash
# Симптомы: маленькие запросы работают, большие нет
# (DNS OK, но web-страницы не загружаются)

# Найти оптимальный MTU
ping -c 1 -M do -s 1472 8.8.8.8    # 1472 + 28 = 1500 (стандарт)
# Если "message too long" → уменьшать на 10 пока не заработает

# Установить
sudo ip link set eth0 mtu 1400
# Постоянно через NM:
nmcli connection modify "Wired" 802-3-ethernet.mtu 1400
```

### Slow DNS
```bash
# Проверить время ответа
time dig google.com @1.1.1.1
time dig google.com @8.8.8.8
time dig google.com @$(resolvectl dns | awk '{print $NF}')

# Медленный ISP DNS (>100ms) → сменить на 1.1.1.1 или 8.8.8.8

# DNS over TLS (шифрование + часто быстрее)
# /etc/systemd/resolved.conf:
# [Resolve]
# DNS=1.1.1.1#cloudflare-dns.com 9.9.9.9#dns.quad9.net
# DNSOverTLS=yes
sudo systemctl restart systemd-resolved

# Локальный DNS кэш
# systemd-resolved кэширует автоматически
resolvectl statistics                # cache hit rate
```

### Высокий ping / задержка
```bash
# Причины:
# 1. Wi-Fi → подключиться по кабелю
# 2. Буферизация (bufferbloat) → настроить QoS
# 3. Фоновые загрузки → найти через nethogs

# Тест bufferbloat
# Запустить speedtest-cli и одновременно ping
# Если ping растёт во время загрузки → bufferbloat

# SQM/CAKE для управления очередями
sudo tc qdisc replace dev eth0 root cake bandwidth 100mbit

# Или через NetworkManager
# nmcli connection modify "Wired" tc.qdisc "root cake bandwidth 100mbit"
```

### IPv6 проблемы
```bash
# Некоторые сайты медленные или не открываются
# Причина: сломанный IPv6 — браузер пробует IPv6, timeout, fallback на IPv4

# Проверить
ping6 -c 4 ipv6.google.com

# Временно отключить IPv6
sudo sysctl -w net.ipv6.conf.all.disable_ipv6=1
sudo sysctl -w net.ipv6.conf.default.disable_ipv6=1

# Постоянно через NM
nmcli connection modify "Wired" ipv6.method disabled
nmcli connection up "Wired"
```

### VPN замедляет подключение
```bash
# Причины:
# 1. DNS leak protection redirect
# 2. MTU слишком большой для туннеля

# WireGuard — установить MTU
# В /etc/wireguard/wg0.conf:
# [Interface]
# MTU = 1380

# OpenVPN — MTU
# В .ovpn файле:
# mssfix 1400
# tun-mtu 1400

# Split tunneling — направить только нужный трафик через VPN
# WireGuard: AllowedIPs = 10.0.0.0/8 (только внутренняя сеть)
# Вместо: AllowedIPs = 0.0.0.0/0 (весь трафик)
```

## Сетевые утилиты — сводная таблица
| Утилита | Назначение |
|---------|-----------|
| ip | Интерфейсы, маршруты, адреса |
| nmcli | NetworkManager CLI |
| ss | Сокеты и соединения |
| ping / mtr | Задержка и потери |
| dig / nslookup | DNS запросы |
| speedtest-cli | Тест скорости |
| iperf3 | Тест пропускной способности |
| tcpdump | Захват пакетов |
| iftop | Трафик по соединениям |
| nethogs | Трафик по процессам |
| nmap | Сканирование портов |
| curl / wget | HTTP запросы |
| ethtool | Параметры Ethernet |
| iw / iwconfig | Параметры Wi-Fi |
| resolvectl | systemd-resolved DNS |

## Firewall — блокирует соединение?
```bash
# Проверить
sudo iptables -L -n
sudo nft list ruleset
sudo firewall-cmd --list-all

# Временно отключить (для теста)
sudo systemctl stop firewalld
# или
sudo iptables -F

# Если помогло → добавить правило
sudo firewall-cmd --add-port=PORT/tcp --permanent
sudo firewall-cmd --reload
```
