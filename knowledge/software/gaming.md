# Игры на Linux — Steam, Proton, Wine, Lutris

## Steam

### Установка

```bash
# Arch / CachyOS
sudo pacman -S steam

# Ubuntu
sudo apt install steam

# Fedora (RPM Fusion)
sudo dnf install steam

# Flatpak (универсально)
flatpak install flathub com.valvesoftware.Steam
```

### Proton (запуск Windows-игр)

Proton — форк Wine от Valve, встроен в Steam.

```bash
# Включение Proton:
# Steam → Настройки → Совместимость → "Включить Steam Play для всех остальных продуктов"
# Выбрать версию Proton (рекомендуется: Proton Experimental или Proton 9)

# Для конкретной игры:
# ПКМ на игре → Свойства → Совместимость → Принудительно использовать...
```

### Проверка совместимости

- [ProtonDB](https://www.protondb.com/) — база совместимости игр
- Рейтинги: Platinum > Gold > Silver > Bronze > Borked

### Параметры запуска Steam

```bash
# В свойствах игры → Параметры запуска:
PROTON_USE_WINED3D=1 %command%           # OpenGL вместо DXVK
DXVK_HUD=fps %command%                    # Показать FPS
mangohud %command%                         # MangoHud overlay
gamemoderun %command%                      # Feral GameMode
PROTON_ENABLE_NVAPI=1 %command%           # NVIDIA DLSS
```

## Wine

Wine — запуск Windows-приложений без эмуляции.

```bash
# Установка
sudo pacman -S wine wine-mono wine-gecko   # Arch
sudo apt install wine64                     # Ubuntu

# Запуск .exe
wine program.exe

# Настройка
winecfg                  # Конфигурация (версия Windows, графика)
winetricks               # Установка компонентов (vcrun, dotnet, dxvk)
```

## Lutris

Lutris — менеджер игр с готовыми скриптами установки.

```bash
# Установка
sudo pacman -S lutris     # Arch
sudo apt install lutris    # Ubuntu
flatpak install flathub net.lutris.Lutris

# Использование
# Lutris → Поиск игры → Автоматический скрипт установки
# Поддерживает: GOG, Epic Games Store, Ubisoft Connect, Battle.net
```

## Производительность

### MangoHud (оверлей FPS/CPU/GPU)

```bash
sudo pacman -S mangohud lib32-mangohud

# Запуск
mangohud <игра>
# Или в Steam: mangohud %command%
```

### Feral GameMode

```bash
sudo pacman -S gamemode lib32-gamemode

# Запуск
gamemoderun <игра>
# В Steam: gamemoderun %command%
```

### Драйверы для игр

```bash
# NVIDIA
sudo pacman -S nvidia nvidia-utils lib32-nvidia-utils
# AMD (mesa — по умолчанию)
sudo pacman -S mesa lib32-mesa vulkan-radeon lib32-vulkan-radeon
# Vulkan
vulkaninfo | head -5           # Проверить поддержку Vulkan
```

## Частые проблемы

### Игра не запускается через Proton

1. Проверить ProtonDB для советов
2. Попробовать другую версию Proton
3. Удалить prefix: `~/.steam/steam/steamapps/compatdata/<appid>/`
4. Проверить зависимости: `winetricks vcrun2019 dotnet48`

### Низкий FPS

```bash
# Проверить GPU драйвер
glxinfo | grep "OpenGL renderer"
# Проверить Vulkan
vulkaninfo | grep "deviceName"
# Включить DXVK (встроен в Proton)
# Проверить GameMode
gamemoded -s
```
