# Сетевые команды и утилиты

## curl — работа с HTTP

### Базовые запросы
```bash
# GET-запрос
curl https://example.com
curl -s https://example.com          # Тихий режим (без прогресса)
curl -v https://example.com          # Подробный вывод (заголовки)
curl -I https://example.com          # Только заголовки (HEAD)
curl -L https://example.com          # Следовать редиректам

# Скачивание
curl -O https://example.com/file.tar.gz           # Сохранить с оригинальным именем
curl -o myfile.tar.gz https://example.com/file     # Указать имя
curl -C - -O https://example.com/large_file.iso    # Продолжить прерванную загрузку

# POST-запрос
curl -X POST https://api.example.com/data \
  -H "Content-Type: application/json" \
  -d '{"key": "value"}'

# Отправка формы
curl -X POST https://example.com/form \
  -F "file=@photo.jpg" \
  -F "name=test"

# Загрузка файла
curl -T file.txt ftp://example.com/uploads/
```

### Аутентификация
```bash
curl -u username:password https://example.com       # Basic auth
curl -H "Authorization: Bearer TOKEN" https://api.example.com  # Bearer token
curl --negotiate -u : https://example.com           # Kerberos
curl -b cookies.txt https://example.com             # Куки из файла
curl -c cookies.txt https://example.com             # Сохранить куки
```

### Отладка и тайминг
```bash
# Показать время
curl -w "\n  time_connect: %{time_connect}\n  time_starttransfer: %{time_starttransfer}\n  time_total: %{time_total}\n  speed: %{speed_download}\n" -o /dev/null -s https://example.com

# Игнорировать ошибки SSL
curl -k https://self-signed.example.com

# Указать CA-сертификат
curl --cacert /path/to/ca.crt https://example.com

# Клиентский сертификат
curl --cert client.crt --key client.key https://example.com

# Использовать прокси
curl -x socks5://127.0.0.1:1080 https://example.com
curl -x http://proxy:8080 https://example.com

# DNS-over-HTTPS
curl --doh-url https://dns.google/dns-query https://example.com
```

## wget — скачивание

```bash
# Базовое скачивание
wget https://example.com/file.tar.gz
wget -O output.tar.gz https://example.com/file      # Указать имя
wget -c https://example.com/large.iso               # Продолжить прерванное

# Рекурсивное скачивание (зеркало сайта)
wget -r -l 2 https://example.com/docs/              # Глубина 2
wget -m -p -k https://example.com                   # Полное зеркало
wget -r --no-parent https://example.com/dir/         # Не покидать директорию

# Скачать список URL
wget -i urls.txt

# Фоновое скачивание
wget -b https://example.com/large.iso
tail -f wget-log                                     # Следить за прогрессом

# Ограничение скорости
wget --limit-rate=1m https://example.com/large.iso   # 1 МБ/с

# Скачать только определённые типы файлов
wget -r -A "*.pdf" https://example.com/docs/

# User-Agent
wget --user-agent="Mozilla/5.0" https://example.com
```

## nc (netcat) — сетевой «швейцарский нож»

```bash
# Проверить порт
nc -zv host 80                   # TCP
nc -zuv host 53                  # UDP

# Сканирование диапазона портов
nc -zv host 20-100 2>&1 | grep succeeded

# Простой сервер/клиент
nc -l 9999                       # Слушать порт 9999
nc host 9999                     # Подключиться

# Передача файла
# На принимающей стороне:
nc -l 9999 > received_file
# На отправляющей:
nc host 9999 < file_to_send

# Простой HTTP-запрос
echo -e "GET / HTTP/1.1\r\nHost: example.com\r\n\r\n" | nc example.com 80

# Chat между двумя машинами
# Машина 1: nc -l 1234
# Машина 2: nc machine1 1234
```

## nmap — сканирование сети

```bash
# Сканирование хоста
nmap 192.168.1.1                 # Базовое сканирование
nmap -sV 192.168.1.1             # Определить версии сервисов
nmap -O 192.168.1.1              # Определить ОС
nmap -A 192.168.1.1              # Всё: ОС + версии + скрипты + traceroute

# Сканирование сети
nmap 192.168.1.0/24              # Вся подсеть
nmap -sn 192.168.1.0/24          # Только ping (кто в сети)
nmap -sn 192.168.1.1-50          # Диапазон IP

# Типы сканирования
nmap -sT host                    # TCP connect (обычный)
nmap -sS host                    # TCP SYN (полуоткрытый, sudo)
nmap -sU host                    # UDP-сканирование
nmap -sA host                    # ACK-сканирование (обнаружение firewall)

# Конкретные порты
nmap -p 22,80,443 host           # Конкретные порты
nmap -p 1-1000 host              # Диапазон
nmap -p- host                    # Все 65535 портов
nmap --top-ports 100 host        # Топ-100 популярных портов

# Скрипты
nmap --script=vuln host          # Проверка уязвимостей
nmap --script=http-enum host     # Перечисление HTTP-ресурсов
nmap --script=ssl-heartbleed host  # Проверка Heartbleed

# Скрытное сканирование
nmap -sS -T2 --randomize-hosts 192.168.1.0/24  # Медленный, со случайным порядком
nmap -D RND:5 host               # Decoy (фальшивые IP-источники)

# Вывод
nmap -oN scan.txt host           # Обычный формат
nmap -oX scan.xml host           # XML
nmap -oG scan.gnmap host         # Grepable формат
```

## tcpdump — перехват трафика

```bash
# Базовый захват
sudo tcpdump -i eth0             # Все пакеты на интерфейсе
sudo tcpdump -i any              # На всех интерфейсах

# Фильтры
sudo tcpdump -i eth0 port 80     # Только порт 80
sudo tcpdump -i eth0 host 192.168.1.1  # Только хост
sudo tcpdump -i eth0 src 10.0.0.1     # Только от IP
sudo tcpdump -i eth0 dst 10.0.0.1     # Только к IP
sudo tcpdump -i eth0 tcp          # Только TCP
sudo tcpdump -i eth0 udp port 53  # DNS-трафик

# Комбинация фильтров
sudo tcpdump -i eth0 'host 192.168.1.1 and port 443'
sudo tcpdump -i eth0 'not port 22'        # Исключить SSH
sudo tcpdump -i eth0 'src net 192.168.1.0/24 and dst port 80'

# Формат вывода
sudo tcpdump -i eth0 -n          # Без DNS-резолвинга (быстрее)
sudo tcpdump -i eth0 -nn         # Без резолвинга портов
sudo tcpdump -i eth0 -X          # Содержимое в HEX + ASCII
sudo tcpdump -i eth0 -A          # Содержимое в ASCII
sudo tcpdump -i eth0 -c 100      # Захватить только 100 пакетов

# Запись в файл
sudo tcpdump -i eth0 -w capture.pcap
# Чтение из файла
sudo tcpdump -r capture.pcap
# Открыть в Wireshark:
wireshark capture.pcap
```

## iperf3 — тестирование пропускной способности

```bash
# Сервер
iperf3 -s                        # Запустить сервер (порт 5201)
iperf3 -s -p 9999                # На другом порту

# Клиент
iperf3 -c server_ip              # TCP-тест
iperf3 -c server_ip -u -b 100M  # UDP-тест, 100 Мбит/с
iperf3 -c server_ip -P 4        # 4 параллельных потока
iperf3 -c server_ip -R           # Обратное направление (download)
iperf3 -c server_ip -t 60        # Тест 60 секунд (по умолчанию 10)
iperf3 -c server_ip -i 1         # Отчёт каждую секунду
```

## ss — сокеты и соединения

```bash
# Слушающие порты
ss -tlnp                         # TCP с PID
ss -ulnp                         # UDP с PID

# Все соединения
ss -tnp                          # TCP established + PID
ss -s                            # Сводная статистика

# Фильтры
ss -tn state established         # Только ESTABLISHED
ss -tn state time-wait           # Только TIME-WAIT
ss -tn 'sport == :80'            # Исходящий порт 80
ss -tn 'dport == :443'           # Назначение порт 443
ss -tn 'dst 10.0.0.0/8'         # Все соединения к 10.x.x.x

# Сравнение с netstat
# ss -tlnp  ≈  netstat -tlnp    # ss быстрее и показывает больше
```

## dig / nslookup — DNS-запросы

```bash
# dig — расширенный DNS-запрос
dig google.com                   # A-запись
dig google.com MX                # MX-записи (почта)
dig google.com NS                # NS-записи
dig google.com AAAA              # IPv6
dig google.com ANY               # Все записи
dig google.com +short            # Краткий ответ
dig @8.8.8.8 google.com         # Через конкретный DNS-сервер
dig -x 8.8.8.8                  # Обратный DNS (PTR)

# Трассировка DNS
dig google.com +trace            # Полный путь от корня

# nslookup (проще)
nslookup google.com
nslookup google.com 8.8.8.8     # Через сервер
nslookup -type=MX google.com    # MX-записи

# host (самый простой)
host google.com
host -t MX google.com
```

## ip — сетевые настройки (iproute2)

```bash
# Адреса
ip addr show                     # Все интерфейсы
ip addr add 192.168.1.100/24 dev eth0  # Добавить IP
ip addr del 192.168.1.100/24 dev eth0  # Удалить IP

# Интерфейсы
ip link show                     # Состояние
ip link set eth0 up              # Включить
ip link set eth0 down            # Выключить
ip link set eth0 mtu 9000        # Установить MTU (jumbo frames)
ip link set eth0 promisc on      # Promiscuous mode

# Маршрутизация
ip route show                    # Таблица маршрутов
ip route add 10.0.0.0/8 via 192.168.1.1 dev eth0  # Добавить маршрут
ip route del 10.0.0.0/8         # Удалить маршрут
ip route add default via 192.168.1.1  # Шлюз по умолчанию
ip route get 8.8.8.8            # Как маршрутизируется пакет

# Соседи (ARP)
ip neigh show                    # ARP-таблица
ip neigh flush dev eth0          # Очистить ARP-кэш

# Правила маршрутизации (policy routing)
ip rule list
ip rule add from 192.168.1.0/24 table 100
ip route add default via 10.0.0.1 table 100
```

## Таблица сравнения утилит

| Задача | Классическая | Современная |
|--------|-------------|-------------|
| Адреса/маршруты | ifconfig, route | ip addr, ip route |
| Сокеты | netstat | ss |
| DNS | nslookup | dig, host |
| Перехват | tcpdump | tcpdump, tshark |
| Скорость | iperf | iperf3 |
| Скачивание | wget | curl, aria2c |
| Сканирование | nmap | nmap, masscan |
| Порты | netcat | ncat (nmap), socat |
| Мониторинг | iftop | bmon, nethogs, bandwhich |
| ARP | arp | ip neigh |

## Дополнительные утилиты

```bash
# mtr — интерактивный traceroute + ping
mtr google.com
mtr -r -c 10 google.com         # Отчёт (10 пакетов)

# aria2c — многопоточное скачивание
aria2c -x 16 https://example.com/large.iso  # 16 соединений

# socat — продвинутый netcat
socat TCP-LISTEN:8080,fork TCP:target:80    # Проброс порта
socat - TCP:host:80                         # Подключение

# nethogs — трафик по процессам
sudo nethogs eth0

# bmon — мониторинг полосы пропускания
bmon

# bandwhich — кто потребляет трафик
sudo bandwhich

# whois
whois example.com

# tracepath (не требует root, в отличие от traceroute)
tracepath google.com
```
