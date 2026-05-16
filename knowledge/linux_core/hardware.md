# Аппаратное обеспечение — диагностика и управление

## Информация о системе

### Общая информация
```bash
# Полная сводка
neofetch                            # или fastfetch
inxi -Fxz                           # детальная инфо (sudo pacman -S inxi)
hostnamectl                          # имя хоста, ОС, ядро
uname -a                             # ядро

# DMI / BIOS
sudo dmidecode --type system         # информация о системе
sudo dmidecode --type bios           # BIOS версия
sudo dmidecode --type memory         # RAM модули
sudo dmidecode --type processor      # CPU
```

### CPU
```bash
lscpu                                # архитектура, ядра, потоки, кэши
cat /proc/cpuinfo                    # подробно
nproc                                # количество ядер

# Частоты
cpupower frequency-info
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq
watch -n 1 'cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq'

# Губернатор
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
sudo cpupower frequency-set -g performance
sudo cpupower frequency-set -g powersave
sudo cpupower frequency-set -g schedutil  # автоматический

# Температура
sensors                              # lm-sensors
sudo pacman -S lm_sensors && sudo sensors-detect
watch -n 1 sensors
```

### Память (RAM)
```bash
free -h                              # общая
sudo dmidecode --type memory | grep -E "Size|Type|Speed|Manufacturer"
lshw -short -class memory            # подробно

# Характеристики модулей
sudo dmidecode --type 17
# Показывает: DDR4/DDR5, частоту, производителя, размер каждого модуля
```

### GPU
```bash
lspci | grep -i vga                  # видеокарты
lspci -v -s $(lspci | grep VGA | cut -d' ' -f1)  # подробно

# NVIDIA
nvidia-smi                           # статус, память, температура
nvidia-smi -q                        # полная инфо

# AMD
radeontop                            # мониторинг
cat /sys/class/drm/card0/device/gpu_busy_percent

# Intel
sudo intel_gpu_top                   # intel-gpu-tools

# OpenGL/Vulkan
glxinfo | grep "OpenGL"
vulkaninfo --summary
```

### Диски
```bash
lsblk -f                            # разделы, FS, UUID
sudo hdparm -I /dev/sda             # HDD/SSD параметры
sudo smartctl -a /dev/sda           # SMART
sudo nvme list                       # NVMe диски
sudo nvme smart-log /dev/nvme0n1    # NVMe SMART

# Тест скорости
sudo hdparm -Tt /dev/sda
fio --name=test --size=1G --rw=randread --bs=4k --runtime=10  # подробный
```

### Сеть
```bash
lspci | grep -i net                  # сетевые адаптеры
lsusb | grep -i net                  # USB сетевые
ethtool eth0                         # скорость, duplex
iw dev wlan0 info                    # Wi-Fi деталь
iwconfig wlan0                       # Wi-Fi сигнал
```

### USB
```bash
lsusb                                # список USB-устройств
lsusb -v                             # подробно
lsusb -t                             # дерево
usb-devices                          # альтернативное представление

# Мониторинг подключений
dmesg -w | grep -i usb               # в реальном времени
udevadm monitor                      # события udev
```

### PCI
```bash
lspci                                # все PCI устройства
lspci -v                             # подробно
lspci -k                             # с драйверами
lspci -nn                            # с ID (для поиска драйверов)
```

## Управление питанием (ноутбуки)

### TLP — оптимизация батареи
```bash
sudo pacman -S tlp tlp-rdw
sudo systemctl enable --now tlp
sudo systemctl enable --now NetworkManager-dispatcher

# Статус
sudo tlp-stat -s
sudo tlp-stat -b                     # батарея
sudo tlp-stat -t                     # температура

# Ручное переключение
sudo tlp bat                         # режим батареи
sudo tlp ac                          # режим от сети

# Конфигурация: /etc/tlp.conf
# CPU_SCALING_GOVERNOR_ON_AC=performance
# CPU_SCALING_GOVERNOR_ON_BAT=powersave
# USB_AUTOSUSPEND=1
# WIFI_PWR_ON_BAT=on
```

### Батарея
```bash
upower -i /org/freedesktop/UPower/devices/battery_BAT0
cat /sys/class/power_supply/BAT0/capacity       # процент
cat /sys/class/power_supply/BAT0/status         # Charging/Discharging
cat /sys/class/power_supply/BAT0/cycle_count    # циклы
acpi -b                              # кратко

# Лимит заряда (ThinkPad)
echo 80 | sudo tee /sys/class/power_supply/BAT0/charge_control_end_threshold
# TLP: START_CHARGE_THRESH_BAT0=75, STOP_CHARGE_THRESH_BAT0=80
```

### Подсветка экрана
```bash
# brightnessctl
sudo pacman -S brightnessctl
brightnessctl get
brightnessctl max
brightnessctl set 50%
brightnessctl set +10%
brightnessctl set 10%-

# Через sysfs
cat /sys/class/backlight/*/brightness
echo 100 | sudo tee /sys/class/backlight/intel_backlight/brightness
```

## Bluetooth

### Настройка
```bash
sudo pacman -S bluez bluez-utils
sudo systemctl enable --now bluetooth

# bluetoothctl
bluetoothctl
# [bluetooth] power on
# [bluetooth] agent on
# [bluetooth] default-agent
# [bluetooth] scan on
# [bluetooth] pair XX:XX:XX:XX:XX:XX
# [bluetooth] connect XX:XX:XX:XX:XX:XX
# [bluetooth] trust XX:XX:XX:XX:XX:XX
```

## Принтеры (CUPS)

### Настройка
```bash
sudo pacman -S cups cups-pdf
sudo systemctl enable --now cups

# Веб-интерфейс
# http://localhost:631

# Команды
lpstat -p -d                         # список принтеров
lp file.pdf                          # печать
lp -d printer_name file.pdf          # на конкретный принтер
lpq                                  # очередь печати
cancel -a                            # отменить все задания

# Сетевой принтер
# Автообнаружение через Avahi:
sudo pacman -S avahi nss-mdns
sudo systemctl enable --now avahi-daemon
# Добавить в /etc/nsswitch.conf:
# hosts: ... mdns_minimal [NOTFOUND=return] ...

# HP принтеры
sudo pacman -S hplip
hp-setup                             # интерактивная настройка
```

## Звуковые устройства
```bash
# PipeWire (современный)
wpctl status                         # статус
wpctl set-default <id>               # устройство по умолчанию
wpctl set-volume @DEFAULT_AUDIO_SINK@ 50%  # громкость

# PulseAudio (legacy)
pactl list sinks short               # выходные устройства
pactl set-sink-volume @DEFAULT_SINK@ 50%

# ALSA (низкий уровень)
aplay -l                             # устройства воспроизведения
arecord -l                           # устройства записи
alsamixer                            # микшер
```

## Таблица полезных утилит
| Утилита | Назначение |
|---------|-----------|
| lspci | PCI устройства |
| lsusb | USB устройства |
| lsblk | Блочные устройства |
| lscpu | Информация о CPU |
| sensors | Температура |
| upower | Батарея и питание |
| brightnessctl | Подсветка |
| bluetoothctl | Bluetooth |
| smartctl | SMART диска |
| dmidecode | DMI/BIOS таблицы |
| hwinfo | Полная инфо (openSUSE) |
| inxi | Сводка системы |
