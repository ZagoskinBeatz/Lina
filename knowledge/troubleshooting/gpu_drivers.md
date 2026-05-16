# Проблемы с GPU и видеодрайверами

## Определение видеокарты

```bash
lspci -k | grep -A3 -i vga
lspci -nn | grep -i vga
# Пример: NVIDIA Corporation GA106M [GeForce RTX 3060] [10de:2560]
# Пример: AMD/ATI Navi 14 [Radeon RX 5500] [1002:7340]
# Пример: Intel UHD Graphics 630 [8086:3e92]

# Текущий драйвер
lspci -k | grep -A2 "VGA\|3D"
# Kernel driver in use: nvidia / amdgpu / i915 / nouveau

# Информация через DRI
glxinfo | grep "OpenGL renderer"
```

## NVIDIA

### Установка проприетарного драйвера

#### Arch Linux
```bash
# Определить поколение карты
# GeForce 900+/10xx/16xx/20xx/30xx/40xx:
sudo pacman -S nvidia nvidia-utils nvidia-settings
# Для DKMS (если ядро не стандартное):
sudo pacman -S nvidia-dkms nvidia-utils

# После установки
sudo mkinitcpio -P     # Пересобрать initramfs
sudo reboot
```

#### Ubuntu / Debian
```bash
# Автоматически:
sudo ubuntu-drivers autoinstall

# Или выбрать конкретную версию:
ubuntu-drivers devices
sudo apt install nvidia-driver-550

sudo reboot
```

#### Fedora
```bash
# Включить RPM Fusion
sudo dnf install https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm

sudo dnf install akmod-nvidia xorg-x11-drv-nvidia-cuda
sudo reboot
```

### Проверка работы
```bash
nvidia-smi                  # Статус GPU, память, температура
nvidia-settings             # GUI настроек

# OpenGL
glxinfo | grep -i "opengl renderer"
# Должно показать NVIDIA, а не llvmpipe/nouveau

# Vulkan
vulkaninfo --summary 2>/dev/null | head -20
```

### NVIDIA + Wayland
```bash
# Для GDM (GNOME):
# В /etc/gdm/custom.conf (или /etc/gdm3/custom.conf):
# Раскомментировать: WaylandEnable=true
# Убедиться nvidia-drm.modeset=1

# Параметр ядра:
sudo nano /etc/default/grub
# GRUB_CMDLINE_LINUX="nvidia-drm.modeset=1"
sudo grub-mkconfig -o /boot/grub/grub.cfg
sudo reboot
```

### Гибридная графика (NVIDIA Optimus)

```bash
# Проверить наличие двух GPU
lspci | grep -i vga

# PRIME (рекомендуется для новых драйверов)
# Запуск на NVIDIA:
__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia glxinfo | grep vendor
__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia steam

# Или через prime-run (Arch):
prime-run glxinfo | grep "OpenGL renderer"

# Переключение через envycontrol (Arch AUR / pip):
pip install envycontrol
sudo envycontrol -s nvidia     # Только NVIDIA
sudo envycontrol -s integrated # Только Intel/AMD
sudo envycontrol -s hybrid     # Гибрид (по умолчанию)
sudo reboot
```

## AMD (Radeon)

### Драйвер amdgpu (открытый, рекомендуется)
```bash
# Работает из коробки на большинстве дистрибутивов
# GCN 1.0+ (HD 7000+/ R5/R7/R9 200+ / RX 400+)

# Проверить
lspci -k | grep -A2 VGA
# Kernel driver in use: amdgpu

# Если используется radeon вместо amdgpu (старые карты):
# Параметр ядра для принудительного amdgpu:
# amdgpu.si_support=1 radeon.si_support=0 (Southern Islands)
# amdgpu.cik_support=1 radeon.cik_support=0 (Sea Islands)
```

### Vulkan для AMD
```bash
# Arch
sudo pacman -S vulkan-radeon lib32-vulkan-radeon mesa lib32-mesa

# Ubuntu
sudo apt install mesa-vulkan-drivers libvulkan1

# Fedora
sudo dnf install mesa-vulkan-drivers vulkan-loader
```

### Мониторинг AMD GPU
```bash
# Встроенная утилита
cat /sys/class/drm/card0/device/gpu_busy_percent
cat /sys/class/drm/card0/device/hwmon/hwmon*/temp1_input

# radeontop
sudo pacman -S radeontop    # Arch
radeontop
```

## Intel

### Установка (обычно работает из коробки)
```bash
# Arch
sudo pacman -S mesa lib32-mesa intel-media-driver vulkan-intel

# Ubuntu
sudo apt install mesa-utils intel-media-va-driver
```

### Проверка
```bash
glxinfo | grep "OpenGL renderer"
vainfo                          # VA-API (аппаратное декодирование видео)
```

## Типичные проблемы

### Чёрный экран после установки драйвера
```bash
# 1. Загрузиться в TTY: Ctrl+Alt+F2 (или F3-F6)
# Если не работает → загрузиться в recovery/single mode

# 2. Проверить логи
journalctl -b -1 | grep -i -E "nvidia|amdgpu|drm|error"
cat /var/log/Xorg.0.log | grep -i -E "EE|error" 2>/dev/null

# 3. Удалить проблемный драйвер
# Arch (NVIDIA):
sudo pacman -R nvidia nvidia-utils
# Ubuntu:
sudo apt remove --purge nvidia-*
sudo apt install xserver-xorg-video-nouveau

sudo reboot
```

### Разрешение экрана неправильное
```bash
# Посмотреть доступные режимы
xrandr                          # X11
wlr-randr                      # Wayland (wlroots)

# Установить разрешение
xrandr --output HDMI-1 --mode 1920x1080 --rate 60

# Если нужного нет — добавить вручную
cvt 1920 1080 60                # Генерировать modeline
xrandr --newmode "1920x1080_60" ...  # Из вывода cvt
xrandr --addmode HDMI-1 "1920x1080_60"
xrandr --output HDMI-1 --mode "1920x1080_60"
```

### Screen tearing (разрывы изображения)
```bash
# NVIDIA — включить ForceFullCompositionPipeline
nvidia-settings → X Server Display Configuration → Advanced → Force Full Composition Pipeline

# AMD / Intel — picom (X11):
# ~/.config/picom.conf
backend = "glx";
vsync = true;

# Или через ядро:
# i915 (Intel): i915.enable_psr=0
# amdgpu: amdgpu.dc=1
```

### Высокая температура GPU
```bash
# NVIDIA
nvidia-smi -q -d TEMPERATURE
# Установить лимит мощности:
sudo nvidia-smi -pl 200         # 200W

# AMD — через sysfs
cat /sys/class/drm/card0/device/hwmon/hwmon*/temp1_input
# Делим на 1000 → градусы Цельсия

# Вентиляторы
# NVIDIA:
nvidia-settings -a "[gpu:0]/GPUFanControlState=1"
nvidia-settings -a "[fan:0]/GPUTargetFanSpeed=70"
```

## Аппаратное ускорение видео (VA-API / VDPAU)

Позволяет декодировать видео на GPU (снижает нагрузку на CPU).

### Установка
```bash
# Intel (VA-API)
sudo pacman -S intel-media-driver libva-utils   # Arch (Gen 8+)
sudo pacman -S libva-intel-driver libva-utils   # Arch (Gen 5-9)

# AMD (VA-API)
sudo pacman -S libva-mesa-driver libva-utils    # Arch
sudo apt install mesa-va-drivers                # Ubuntu

# NVIDIA (NVDEC/NVENC через VA-API)
sudo pacman -S libva-nvidia-driver              # Arch (545+)

# VDPAU (альтернатива VA-API для NVIDIA)
sudo pacman -S nvidia-utils                     # Уже содержит VDPAU
```

### Проверка
```bash
vainfo                          # VA-API профили
vdpauinfo                      # VDPAU профили

# Использование в Firefox
# about:config → media.ffmpeg.vaapi.enabled = true
# Для Wayland: MOZ_ENABLE_WAYLAND=1

# Использование в mpv
mpv --hwdec=vaapi video.mp4
mpv --hwdec=auto video.mp4     # Автовыбор

# Использование в Chromium/Chrome
# Запуск с флагами:
chromium --enable-features=VaapiVideoDecodeLinuxGL,VaapiVideoEncoder
```

## Multi-GPU и переключение

### PRIME Render Offload (NVIDIA + Intel/AMD)

```bash
# Запуск конкретного приложения на дискретном GPU
__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia application
# Или:
prime-run application            # Arch (nvidia-prime)

# Для Vulkan:
__NV_PRIME_RENDER_OFFLOAD=1 __VK_LAYER_NV_optimus=NVIDIA_only application
```

### switcherooctl (Mesa/GNOME)

```bash
# Для AMD + Intel (оба open-source драйвера)
switcherooctl list               # Показать GPU
switcherooctl launch -g 1 application  # Запуск на GPU #1

# В GNOME: ПКМ по приложению → "Launch using Discrete Graphics Card"
```

### envycontrol (режимы GPU)

```bash
pip install envycontrol

sudo envycontrol -s integrated   # Только встроенный GPU (макс. автономность)
sudo envycontrol -s hybrid       # Гибрид (дискретный по запросу)
sudo envycontrol -s nvidia       # Только NVIDIA (макс. производительность)
sudo envycontrol --query         # Текущий режим
sudo reboot                      # Перезагрузка обязательна
```

## Vulkan

```bash
# Установка
# Arch (NVIDIA):
sudo pacman -S vulkan-icd-loader lib32-vulkan-icd-loader
# Arch (AMD):
sudo pacman -S vulkan-radeon lib32-vulkan-radeon
# Arch (Intel):
sudo pacman -S vulkan-intel lib32-vulkan-intel

# Проверка
vulkaninfo --summary
vkcube                          # Тестовый куб

# Выбор GPU для Vulkan-приложений
VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json application
```

## Матрица рекомендаций

| GPU | Драйвер | Wayland | Рекомендация |
|-----|---------|---------|-------------|
| NVIDIA (новый, 900+) | nvidia (проприетарный) | С nvidia-drm.modeset=1 | Рекомендуется |
| NVIDIA (старый, 700-) | nvidia-470xx / nouveau | X11 лучше | nvidia-470xx-dkms |
| AMD (GCN 1.0+) | amdgpu (открытый) | Полная поддержка | Работает из коробки |
| Intel (Gen 8+) | i915 (открытый) | Полная поддержка | Работает из коробки |
| AMD + NVIDIA | amdgpu + nvidia | PRIME | envycontrol/switcheroo |
| Intel + NVIDIA | i915 + nvidia | PRIME | envycontrol/prime-run |
