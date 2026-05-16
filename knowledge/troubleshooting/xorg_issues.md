# Проблемы X11 / Xorg

## Диагностика

### Логи и статус
```bash
# Логи Xorg
cat /var/log/Xorg.0.log
cat /var/log/Xorg.0.log | grep "(EE)"   # ошибки
cat /var/log/Xorg.0.log | grep "(WW)"   # предупреждения

# Для Wayland сессий (XWayland)
journalctl --user -b | grep -i xwayland

# Текущие настройки
xrandr                               # мониторы и разрешения
xdpyinfo                            # информация о дисплее
xinput list                          # устройства ввода
xset q                              # параметры сервера

# Драйвер видео
lspci -v | grep -A 10 VGA
glxinfo | grep "OpenGL renderer"
```

### Конфигурация Xorg
```bash
# Файлы конфигурации (приоритет от высшего к низшему):
# /etc/X11/xorg.conf.d/          — пользовательские снипеты
# /usr/share/X11/xorg.conf.d/    — дистрибутивные
# /etc/X11/xorg.conf              — полный конфиг (обычно не нужен)

# Сгенерировать конфиг
sudo Xorg :1 -configure
# Создаст /root/xorg.conf.new
```

## Проблемы с разрешением экрана

### xrandr
```bash
# Показать мониторы
xrandr --listmonitors

# Установить разрешение
xrandr --output HDMI-1 --mode 1920x1080 --rate 60

# Добавить пользовательское разрешение
cvt 1920 1080 60                     # генерация modeline
xrandr --newmode "1920x1080_60" ...  # добавить mode
xrandr --addmode HDMI-1 "1920x1080_60"
xrandr --output HDMI-1 --mode "1920x1080_60"

# Мультимонитор
xrandr --output HDMI-1 --right-of eDP-1     # справа
xrandr --output HDMI-1 --left-of eDP-1      # слева
xrandr --output HDMI-1 --above eDP-1        # сверху
xrandr --output HDMI-1 --same-as eDP-1      # зеркало

# Основной монитор
xrandr --output HDMI-1 --primary

# Отключить монитор
xrandr --output HDMI-1 --off

# Повернуть
xrandr --output eDP-1 --rotate left
xrandr --output eDP-1 --rotate normal
```

### HiDPI / масштабирование
```bash
# Xorg: масштабирование через DPI
xrandr --dpi 192

# Или Xresources
# ~/.Xresources:
# Xft.dpi: 192
xrdb -merge ~/.Xresources

# GDK (GTK)
export GDK_SCALE=2
export GDK_DPI_SCALE=0.5

# QT
export QT_AUTO_SCREEN_SCALE_FACTOR=1
export QT_SCALE_FACTOR=2
```

## Проблемы с видео-драйверами

### NVIDIA
```bash
# Установка проприетарного драйвера
# Arch/CachyOS:
sudo pacman -S nvidia nvidia-utils nvidia-settings lib32-nvidia-utils

# Debian/Ubuntu:
sudo apt install nvidia-driver-560

# Проверка
nvidia-smi
nvidia-settings

# Проблемы:
# 1. Чёрный экран после установки
#    → Добавить в GRUB: nvidia-drm.modeset=1
#    → /etc/mkinitcpio.conf: MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)
#    → sudo mkinitcpio -P

# 2. Разрыв изображения (tearing)
#    → nvidia-settings: Force Full Composition Pipeline
#    → Или /etc/X11/xorg.conf.d/20-nvidia.conf:
# Section "Screen"
#     Identifier "Screen0"
#     Option "metamodes" "nvidia-auto-select +0+0 {ForceFullCompositionPipeline=On}"
# EndSection

# 3. NVIDIA + Wayland
#    Debian: не блокирует
#    Arch: убедиться что nvidia-drm.modeset=1
#    KDE: должно работать с 560+
```

### AMD
```bash
# Открытый драйвер AMDGPU (встроен в ядро)
# Установить Vulkan:
sudo pacman -S vulkan-radeon lib32-vulkan-radeon  # RADV
sudo pacman -S mesa lib32-mesa                    # OpenGL

# AMD PRO (проприетарный)
# Обычно не нужен для десктопа

# Проверка
vulkaninfo | grep GPU
glxinfo | grep "OpenGL renderer"
radeontop                            # мониторинг GPU

# Переменные окружения
export AMD_VULKAN_ICD=RADV           # использовать RADV
export RADV_PERFTEST=aco             # ACO compiler (по умолчанию)
```

### Intel
```bash
sudo pacman -S mesa lib32-mesa vulkan-intel lib32-vulkan-intel

# Проблемы с тирингом:
# /etc/X11/xorg.conf.d/20-intel.conf
# Section "Device"
#     Identifier "Intel Graphics"
#     Driver "modesetting"
#     Option "TearFree" "true"
# EndSection
```

## Устройства ввода

### Тачпад
```bash
# Список устройств
xinput list
xinput list-props "TouchPad"

# Настройки тачпада
xinput set-prop "TouchPad" "libinput Tapping Enabled" 1
xinput set-prop "TouchPad" "libinput Natural Scrolling Enabled" 1
xinput set-prop "TouchPad" "libinput Accel Speed" 0.3

# Постоянно: /etc/X11/xorg.conf.d/30-touchpad.conf
# Section "InputClass"
#     Identifier "touchpad"
#     MatchIsTouchpad "on"
#     Driver "libinput"
#     Option "Tapping" "on"
#     Option "NaturalScrolling" "true"
#     Option "AccelSpeed" "0.3"
# EndSection
```

### Клавиатура
```bash
# Раскладка
setxkbmap -layout us,ru -option grp:alt_shift_toggle

# Постоянно: /etc/X11/xorg.conf.d/00-keyboard.conf
# Section "InputClass"
#     Identifier "keyboard"
#     MatchIsKeyboard "on"
#     Option "XkbLayout" "us,ru"
#     Option "XkbOptions" "grp:alt_shift_toggle"
# EndSection

# Скорость повтора
xset r rate 200 30                   # delay 200ms, repeat 30/s
```

## Compositing

### picom (X11 compositor)
```bash
picom --experimental-backends --backend glx --vsync
# Настройки: ~/.config/picom.conf

# Отключить композитор (для игр)
killall picom
# В KDE: Alt+Shift+F12
```

## Screen tearing

### Решения по драйверам:
```bash
# NVIDIA: ForceFullCompositionPipeline (см. выше)

# Intel: modesetting + TearFree

# AMD:
# /etc/X11/xorg.conf.d/20-amdgpu.conf
# Section "Device"
#     Identifier "AMD"
#     Driver "amdgpu"
#     Option "TearFree" "true"
# EndSection

# Или использовать Wayland — тиринг отсутствует по дизайну
```

## Troubleshooting

### Xorg не запускается
```bash
# Проверить логи
cat /var/log/Xorg.0.log | grep "(EE)"
journalctl -b | grep -i "x11\|xorg\|display"

# Частые причины:
# 1. Неправильный драйвер → удалить /etc/X11/xorg.conf
# 2. Модуль не загружен → modprobe nvidia/amdgpu/i915
# 3. Конфликт драйверов → удалить nouveau при NVIDIA
# 4. Нет прав → проверить группу video: usermod -aG video user

# Переключиться на TTY
Ctrl+Alt+F2                          # TTY2
startx                               # запустить Xorg из консоли

# Обратно на графику
Ctrl+Alt+F1                          # или F7
```

### Мерцание экрана
```bash
# EDID проблемы
xrandr --output HDMI-1 --set "Broadcast RGB" "Full"

# Panel Self Refresh (PSR) — Intel
# GRUB: i915.enable_psr=0

# NVIDIA: может помочь
# nvidia-drm.fbdev=1
```

### Пустой экран после сна
```bash
# NVIDIA
# Добавить в GRUB: nvidia.NVreg_PreserveVideoMemoryAllocations=1
# sudo systemctl enable nvidia-resume nvidia-suspend nvidia-hibernate

# Общее:
# Попробовать переключить TTY: Ctrl+Alt+F2, затем Ctrl+Alt+F1
```
