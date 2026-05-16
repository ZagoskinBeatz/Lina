# WiFi не работает — диагностика и решение

## Быстрая диагностика

```bash
# 1. Определить WiFi-адаптер
lspci -k | grep -A3 -i network    # PCI/PCIe адаптер
lsusb | grep -i wireless          # USB адаптер
iw dev                             # Беспроводные интерфейсы

# 2. Проверить rfkill (блокировка)
rfkill list
# Если blocked: yes →
sudo rfkill unblock wifi

# 3. Проверить интерфейс
ip link show                       # wlan0 / wlp3s0 — UP или DOWN?
sudo ip link set wlan0 up          # Включить интерфейс

# 4. Проверить NetworkManager
systemctl status NetworkManager
nmcli general status
nmcli device status
nmcli device wifi list             # Видны ли сети?

# 5. Проверить ошибки ядра
dmesg | grep -i -E "wifi|wlan|firmware|iwl|ath|rtl|brcm"
journalctl -b | grep -i -E "wifi|firmware|NetworkManager"
```

## WiFi не видит сети

### Причина 1: Нет драйвера
```bash
# Определить чип
lspci -nn | grep -i network
# Пример: Intel Wi-Fi 6 AX200 [8086:2723]
# Пример: Qualcomm Atheros QCA6174 [168c:003e]
# Пример: Broadcom BCM4360 [14e4:43a0]
# Пример: Realtek RTL8822CE [10ec:c822]

# Intel: обычно работает из коробки
sudo pacman -S linux-firmware      # Arch
sudo apt install linux-firmware    # Ubuntu

# Broadcom:
sudo pacman -S broadcom-wl-dkms   # Arch
sudo apt install bcmwl-kernel-source  # Ubuntu

# Realtek (часто проблемные):
# Arch AUR:
yay -S rtw89-dkms-git             # Новые Realtek
yay -S rtl8821ce-dkms-git         # RTL8821CE

# Перезагрузить модуль
sudo modprobe -r <module_name>
sudo modprobe <module_name>
```

### Причина 2: Программная блокировка
```bash
rfkill list
# Если Soft blocked: yes
sudo rfkill unblock wifi
# Если Hard blocked: yes → аппаратный переключатель на ноутбуке (Fn+F2 и т.п.)
```

### Причина 3: NetworkManager не запущен
```bash
sudo systemctl enable --now NetworkManager
```

### Причина 4: Конфликт с wpa_supplicant / iwd
```bash
# Если используется NetworkManager — отключить iwd
sudo systemctl disable --now iwd
sudo systemctl restart NetworkManager

# Если хотите iwd вместо wpa_supplicant:
# /etc/NetworkManager/conf.d/wifi-backend.conf
[device]
wifi.backend=iwd
```

## WiFi подключается, но нет интернета

```bash
# 1. Проверить IP-адрес
ip addr show wlan0
# Если нет IP → проблема DHCP

# 2. Проверить DNS
ping -c 2 8.8.8.8            # Если работает → проблема DNS
ping -c 2 google.com         # Если не работает → проблема DNS

# Временно установить DNS
echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf

# Через NetworkManager
nmcli connection modify "MyWifi" ipv4.dns "8.8.8.8 1.1.1.1"
nmcli connection up "MyWifi"

# 3. Проверить маршрут
ip route
# Должен быть default via <gateway>

# 4. Проверить firewall
sudo iptables -L -n | head
sudo nft list ruleset 2>/dev/null | head
```

## WiFi отключается / нестабильный

### Энергосбережение
```bash
# Проверить
iwconfig wlan0 | grep -i power

# Отключить временно
sudo iwconfig wlan0 power off

# Отключить постоянно через NetworkManager
# /etc/NetworkManager/conf.d/wifi-powersave-off.conf
[connection]
wifi.powersave = 2

sudo systemctl restart NetworkManager
```

### Роуминг / переподключение
```bash
# Увеличить стабильность
nmcli connection modify "MyWifi" 802-11-wireless.band bg  # Только 2.4 ГГц
nmcli connection modify "MyWifi" 802-11-wireless.bssid AA:BB:CC:DD:EE:FF  # Фиксировать точку
```

## WiFi на ноутбуке после установки Linux

### Общий алгоритм
1. Подключить Ethernet (временно) для загрузки драйверов
2. Определить адаптер: `lspci -nn | grep -i net`
3. Найти нужный пакет/драйвер
4. Установить и перезагрузить
5. `nmcli device wifi list` — проверить

### Если нет Ethernet
```bash
# Подключить телефон через USB-tethering
# Android: Настройки → Сеть → Точка доступа → USB-модем
# Интерфейс появится как usb0 или enp0s*

# Или скачать .deb/.pkg.tar.zst на другом компьютере и установить вручную
```

## iwd — альтернатива wpa_supplicant

iwd (iNet Wireless Daemon) от Intel — современная замена wpa_supplicant.
Работает быстрее, потребляет меньше ресурсов, встроенный DHCP-клиент.

```bash
# Установка
sudo pacman -S iwd               # Arch
sudo apt install iwd              # Ubuntu

# Запуск
sudo systemctl enable --now iwd

# Интерактивный режим
iwctl
[iwd]# device list                # Показать адаптеры
[iwd]# station wlan0 scan         # Сканировать
[iwd]# station wlan0 get-networks # Список сетей
[iwd]# station wlan0 connect "SSID"  # Подключиться
[iwd]# station wlan0 disconnect   # Отключиться
[iwd]# known-networks list        # Сохранённые сети
[iwd]# known-networks "SSID" forget  # Забыть сеть

# Конфигурация: /etc/iwd/main.conf
[General]
EnableNetworkConfiguration=true    # Встроенный DHCP

[Network]
NameResolvingService=systemd       # DNS через systemd-resolved
```

### iwd + NetworkManager
```bash
# Использовать iwd как бэкенд для NetworkManager
# /etc/NetworkManager/conf.d/wifi-backend.conf
[device]
wifi.backend=iwd

sudo systemctl restart NetworkManager
```

## Диагностика скорости WiFi

```bash
# Текущее подключение
iw dev wlan0 link
# → Показывает SSID, частоту, битрейт, уровень сигнала

# Уровень сигнала
iw dev wlan0 station dump | grep signal
# -30 до -50 dBm = отличный
# -50 до -60 dBm = хороший
# -60 до -70 dBm = нормальный
# -70 до -80 dBm = слабый
# ниже -80 dBm = очень слабый

# Тест скорости
speedtest-cli                     # pip install speedtest-cli
# Или через iperf3 на роутере/сервере в локальной сети

# Мониторинг трафика WiFi
wavemon                           # Интерактивный мониторинг (pacman -S wavemon)
```

## Точка доступа (Hotspot)

```bash
# Через NetworkManager
nmcli device wifi hotspot ifname wlan0 ssid "MyHotspot" password "password123"

# Отключить
nmcli connection down Hotspot

# Через hostapd (продвинутый)
sudo pacman -S hostapd dnsmasq
```

## Таблица чипов WiFi и драйверов

| Производитель | Чип | Модуль ядра | Примечания |
|--------------|------|------------|-----------|
| Intel | AX200/AX210 | iwlwifi | Из коробки |
| Intel | AC 7260/8265 | iwlwifi | Из коробки |
| Qualcomm | QCA6174/QCA9377 | ath10k | Из коробки (firmware) |
| Realtek | RTL8822CE | rtw88_8822ce | Из коробки (ядро 5.9+) |
| Realtek | RTL8821CE | rtw88_8821ce | Из коробки (ядро 6.2+) |
| Realtek | RTL8812AU | rtl8812au (AUR) | USB, нужен из AUR |
| Broadcom | BCM4360 | wl (broadcom-wl-dkms) | Проприетарный |
| Broadcom | BCM43142 | b43 | linux-firmware |
| MediaTek | MT7921 | mt7921e | Из коробки (ядро 5.12+) |
| MediaTek | MT7922 | mt7921e | Из коробки (ядро 6.0+) |
