# Игры на Linux — Steam, Proton, Vulkan

## Обзор
Linux стал полноценной игровой платформой благодаря Steam Deck, Proton и Vulkan.
Большинство Windows-игр запускаются через Proton (обёртка над Wine + DXVK).

## Необходимые компоненты

### Vulkan (обязателен!)
```bash
# Arch / CachyOS
sudo pacman -S vulkan-icd-loader lib32-vulkan-icd-loader
# Для NVIDIA:
sudo pacman -S nvidia-utils lib32-nvidia-utils
# Для AMD:
sudo pacman -S vulkan-radeon lib32-vulkan-radeon
# Для Intel:
sudo pacman -S vulkan-intel lib32-vulkan-intel

# Проверка
vulkaninfo --summary
```

### 32-битные библиотеки (multilib)
```bash
# /etc/pacman.conf — раскомментировать:
# [multilib]
# Include = /etc/pacman.d/mirrorlist
sudo pacman -Syu
sudo pacman -S lib32-mesa lib32-glibc
```

## Steam
```bash
# Установка
sudo pacman -S steam            # Arch
flatpak install flathub com.valvesoftware.Steam  # Flatpak

# Включить Proton для всех игр:
# Steam → Settings → Steam Play → Enable Steam Play for all titles
# Выбрать последнюю версию Proton
```

## Proton / GE-Proton
GE-Proton — кастомная версия с дополнительными патчами.

```bash
# Установка через ProtonUp-Qt
flatpak install flathub net.davidotek.pupgui2
protonup-qt  # GUI для управления версиями

# Ручная установка
mkdir -p ~/.steam/root/compatibilitytools.d/
cd ~/.steam/root/compatibilitytools.d/
wget https://github.com/GloriousEggroll/proton-ge-custom/releases/download/GE-Proton9-1/GE-Proton9-1.tar.gz
tar xzf GE-Proton9-1.tar.gz
```

## DXVK / VKD3D
- **DXVK** — трансляция DirectX 9/10/11 → Vulkan
- **VKD3D-proton** — трансляция DirectX 12 → Vulkan
- Оба встроены в Proton

## Gamescope
```bash
sudo pacman -S gamescope
# Запуск игры в Gamescope (изолированный композитор):
gamescope -w 1920 -h 1080 -f -- steam
# Параметры запуска в Steam:
# gamescope -f -- %command%
```

## MangoHud (overlay с FPS)
```bash
sudo pacman -S mangohud lib32-mangohud
# Параметры запуска в Steam:
# mangohud %command%
# или: MANGOHUD=1 %command%
```

## gamemode (оптимизация производительности)
```bash
sudo pacman -S gamemode lib32-gamemode
# Параметры запуска:
# gamemoderun %command%
```

## Проверка совместимости
- [ProtonDB](https://www.protondb.com/) — рейтинги совместимости
- Platinum/Gold — работает отлично
- Silver — мелкие проблемы
- Borked — не работает

## Советы по оптимизации
1. Используйте проприетарный драйвер NVIDIA
2. Включите Vulkan pre-caching в Steam
3. Используйте GE-Proton для проблемных игр
4. Для тиринга: включите VSync или используйте Gamescope
5. Для античита: EAC и BattlEye поддерживаются через Proton

## Частые проблемы
- **"Missing runtime"** — `steam --reset`
- **Чёрный экран** — переключите версию Proton
- **Низкий FPS** — проверьте `glxinfo | grep renderer` (должен быть GPU, не llvmpipe)
- **Контроллер не работает** — Steam Input или `sudo pacman -S game-devices-udev`

## Wine — запуск Windows-приложений
```bash
# Установка
sudo pacman -S wine wine-mono wine-gecko winetricks  # Arch
sudo apt install wine64 winetricks                     # Debian

# Запуск приложения
wine setup.exe
wine program.exe

# Настройка prefix (изолированное окружение)
WINEPREFIX=~/.wine-game winecfg
WINEPREFIX=~/.wine-game wine game.exe

# Winetricks — установка библиотек
winetricks d3dx9 vcrun2019 dotnet48

# Bottles / Lutris — графические менеджеры
flatpak install flathub com.usebottles.bottles
flatpak install flathub net.lutris.Lutris
```

## Lutris — универсальный гейм-менеджер
```bash
# Установка
sudo pacman -S lutris        # Arch
flatpak install flathub net.lutris.Lutris

# Особенности:
# - Каталог установочных скриптов от сообщества
# - Поддержка: Wine, DOSBox, RetroArch, ScummVM
# - Авто-настройка runtime для каждой игры
# - Интеграция с GOG, Epic Games Store, Humble Bundle
```

## RetroArch — ретро-игры
```bash
# Установка
sudo pacman -S retroarch retroarch-assets-xmb
flatpak install flathub org.libretro.RetroArch

# Ядра (cores) для эмуляции:
# - bsnes (SNES), mupen64plus (N64), PPSSPP (PSP)
# - Dolphin (GameCube/Wii), melonDS (NDS), mGBA (GBA)
# - DuckStation (PS1), PCSX2 (PS2)
```

## Мониторинг производительности
```bash
# MangoHud — оверлей FPS/CPU/GPU
mangohud glxgears                   # тест
mangohud %command%                  # в Steam Launch Options

# MangoHud конфигурация (~/.config/MangoHud/MangoHud.conf)
fps_limit=60
cpu_temp
gpu_temp
ram
vram
frame_timing

# Gamescope — микрокомпозитор
gamescope -w 1920 -h 1080 -f -- %command%
# -w/-h разрешение, -f фулскрин, -r лимит FPS
# Полезно для HDR, VRR/FreeSync, масштабирование (FSR)

# CoreCtrl — управление GPU (AMD)
sudo pacman -S corectrl
# Профиль по игре, управление частотами и вентилятором
```

## Настройка NVIDIA для игр
```bash
# Установка проприетарного драйвера
sudo pacman -S nvidia nvidia-utils lib32-nvidia-utils nvidia-settings

# Переменные окружения для производительности
__GL_THREADED_OPTIMIZATIONS=1
__GL_SHADER_DISK_CACHE=1
__GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1

# Для Wayland + NVIDIA
GBM_BACKEND=nvidia-drm
__GLX_VENDOR_LIBRARY_NAME=nvidia

# nvidia-settings
nvidia-settings                      # GUI настройки
nvidia-smi                           # мониторинг GPU
```

## Настройка AMD для игр
```bash
# Открытый драйвер (AMDGPU) — встроен в ядро
sudo pacman -S mesa lib32-mesa vulkan-radeon lib32-vulkan-radeon

# Переменные окружения
RADV_PERFTEST=gpl              # Graphics Pipeline Library (быстрее шейдеры)
AMD_VULKAN_ICD=RADV            # использовать RADV (Mesa)

# Мониторинг
cat /sys/class/drm/card0/device/gpu_busy_percent
radeontop                       # top для GPU
```

## Игровой режим (gamemode)
```bash
sudo pacman -S gamemode lib32-gamemode

# В Steam Launch Options:
gamemoderun %command%

# Или комбинация:
gamemoderun mangohud %command%

# Что делает gamemode:
# - Устанавливает governor CPU в performance
# - Увеличивает приоритет процесса (nice)
# - Отключает экранохранитель
# - Оптимизирует io-scheduler
```

## Proton / Steam Play — совместимость
| Версия Proton | Особенности |
|---------------|-------------|
| Proton Stable | Официальная от Valve, стабильная |
| Proton Experimental | Свежие фиксы, менее стабильная |
| GE-Proton | Фанатская сборка с дополнительными патчами |
| Proton-tkg | Кастомная компиляция |

```bash
# Установка GE-Proton
# Скачайте с https://github.com/GloriousEggroll/proton-ge-custom/releases
# Распакуйте в ~/.steam/root/compatibilitytools.d/

# Через protonup-qt
flatpak install flathub net.davidotek.pupgui2
```

## Античит (EAC / BattlEye)
```bash
# EasyAntiCheat — поддержка через Proton
# Разработчик должен включить поддержку Linux
# Проверить статус: https://areweanticheatyet.com

# BattlEye — аналогично, зависит от разработчика
# Работают: Elden Ring, Destiny 2 (частично), Apex Legends
# Не работают: Fortnite, PUBG (нативный Linux отключён)
```
