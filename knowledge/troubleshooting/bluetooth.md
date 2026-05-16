# Bluetooth в Linux

## Обзор
Bluetooth управляется стеком BlueZ + контроллер через bluetooth.service.
Для аудио используется PipeWire (или PulseAudio).

## Основные компоненты
- **BlueZ** — стек Bluetooth в ядре и userspace
- **bluetoothctl** — CLI для управления
- **bluetooth.service** — systemd-сервис

## Установка
```bash
# Arch / CachyOS
sudo pacman -S bluez bluez-utils

# Ubuntu/Debian
sudo apt install bluez bluetooth

# Fedora
sudo dnf install bluez bluez-tools

# Включение сервиса
sudo systemctl enable --now bluetooth
```

## bluetoothctl — основные команды
```bash
bluetoothctl                     # интерактивный режим
  power on                       # включить адаптер
  scan on                        # поиск устройств
  devices                        # список найденных
  pair XX:XX:XX:XX:XX:XX         # сопряжение
  trust XX:XX:XX:XX:XX:XX        # доверять устройству
  connect XX:XX:XX:XX:XX:XX      # подключение
  disconnect XX:XX:XX:XX:XX:XX   # отключение
  remove XX:XX:XX:XX:XX:XX       # удалить устройство
  info XX:XX:XX:XX:XX:XX         # информация
  paired-devices                 # список сопряжённых
```

## Автоподключение
```bash
bluetoothctl trust XX:XX:XX:XX:XX:XX
# Для автоматического подключения при загрузке:
# /etc/bluetooth/main.conf
# [Policy]
# AutoEnable=true
```

## Диагностика
```bash
# Статус сервиса
systemctl status bluetooth

# Проверка адаптера
hciconfig -a
rfkill list bluetooth

# Если заблокирован
rfkill unblock bluetooth

# Логи
journalctl -u bluetooth -f

# Проверка модуля ядра
lsmod | grep bluetooth
modinfo bluetooth
```

## Bluetooth Audio
```bash
# PipeWire автоматически поддерживает Bluetooth
# Проверка профиля
pactl list cards | grep -A5 bluez

# Переключение профиль HSP/HFP (с микрофоном) и A2DP (высокое качество)
pactl set-card-profile <card> a2dp-sink
```

## Частые проблемы
1. **Адаптер не виден** — `rfkill unblock bluetooth && sudo systemctl restart bluetooth`
2. **Устройство не подключается** — удалите и заново: `remove`, `scan on`, `pair`, `trust`, `connect`
3. **Плохой звук** — переключите с HSP на A2DP: `pactl set-card-profile`
4. **Отваливается Bluetooth** — проверьте USB-адаптер: `dmesg | grep -i bluetooth`
5. **Xbox контроллер** — `sudo pacman -S xpadneo-dkms` для лучшей поддержки

## Подробная диагностика

### Шаг 1: Проверить адаптер
```bash
# Виден ли адаптер?
hciconfig                        # список адаптеров
hciconfig hci0 up               # включить адаптер

# Или через bluetoothctl
bluetoothctl show                # информация об адаптере
bluetoothctl power on            # включить

# Проверить USB-адаптер
lsusb | grep -i bluetooth
dmesg | grep -i bluetooth | tail -20

# Проверить модуль ядра
lsmod | grep btusb
sudo modprobe btusb              # загрузить модуль
```

### Шаг 2: Проверить rfkill
```bash
rfkill list
# Если bluetooth заблокирован:
rfkill unblock bluetooth

# Проверить аппаратный переключатель на ноутбуке
# Fn+F-клавиша может блокировать Bluetooth
```

### Шаг 3: Перезапуск стека
```bash
sudo systemctl restart bluetooth
# Если не помогло — полный сброс
sudo rmmod btusb && sudo modprobe btusb
sudo systemctl restart bluetooth
bluetoothctl power on
```

### Шаг 4: Логи
```bash
# Системные логи bluetooth
journalctl -u bluetooth -b --no-pager | tail -30
dmesg | grep -i "bluetooth\|btusb\|firmware" | tail -20

# Подробная отладка
sudo btmon &                     # мониторинг HCI-трафика
# или
sudo hcidump -X                  # дамп пакетов
```

## Проблемы с конкретными устройствами

### Наушники не подключаются
```bash
# Удалить и заново подключить
bluetoothctl
> remove <MAC>
> scan on
# Найти устройство, скопировать MAC
> pair <MAC>
> trust <MAC>
> connect <MAC>

# Если PIN-код нужен (старые устройства):
> agent on
> default-agent
> pair <MAC>
# Ввести PIN (обычно 0000 или 1234)
```

### Bluetooth мышь/клавиатура
```bash
# Задержка ввода
# Уменьшить connection interval:
# /etc/bluetooth/main.conf
[LE]
MinConnectionInterval=6
MaxConnectionInterval=9
ConnectionLatency=0

# Сохранение сопряжения после перезагрузки
bluetoothctl trust <MAC>

# Auto-connect при загрузке
# /etc/bluetooth/main.conf
[Policy]
AutoEnable=true

sudo systemctl restart bluetooth
```

### Bluetooth-колонка
```bash
# Если подключается, но нет звука:
# 1. Проверить профиль
pactl list cards short | grep bluez
pactl set-card-profile <card> a2dp-sink

# 2. Установить как default sink
pactl set-default-sink <sink_name>
# или через wpctl:
wpctl set-default <id>

# 3. Проверить громкость
wpctl set-volume <id> 1.0        # 100%
```

## Dual-boot Bluetooth (Linux + Windows)
```bash
# Проблема: Bluetooth-устройства нужно переподключать при смене ОС
# Решение: скопировать ключи сопряжения из Windows в Linux

# 1. В Windows: извлечь ключи из реестра
# HKLM\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys\
# Используйте PSTool или chntpw

# 2. В Linux: записать ключ
sudo nano /var/lib/bluetooth/<adapter_mac>/<device_mac>/info
# [LinkKey]
# Key=<hex_key_from_windows>
# Type=4

sudo systemctl restart bluetooth
```

## Bluetooth Low Energy (BLE)
```bash
# Сканирование BLE-устройств
bluetoothctl
> menu scan
> transport le
> back
> scan on

# Подключение BLE
> connect <MAC>

# GATT-сервисы
> menu gatt
> list-attributes <MAC>
> select-attribute <uuid>
> read
> write <hex_value>
```
