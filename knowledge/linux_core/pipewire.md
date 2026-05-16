# PipeWire — аудиосистема Linux

## Обзор
PipeWire — современная мультимедийная подсистема, заменяет PulseAudio и JACK.
Обеспечивает низкую задержку, поддержку Bluetooth-аудио и видео (screen sharing на Wayland).

## Архитектура
```
Приложения (PulseAudio API / JACK API / native PipeWire API)
        |
    PipeWire (сервер)
        |
   +----+----+
   |         |
 ALSA     Bluetooth
 (ядро)   (BlueZ)
```

## Основные компоненты
- **pipewire** — ядро системы
- **pipewire-pulse** — замена PulseAudio (совместимость)
- **wireplumber** — менеджер сессий (policy manager)
- **pipewire-jack** — совместимость с JACK

## Установка
```bash
# Arch Linux / CachyOS
sudo pacman -S pipewire pipewire-pulse pipewire-jack wireplumber

# Ubuntu/Debian (22.04+)
sudo apt install pipewire pipewire-pulse wireplumber

# Fedora (по умолчанию с F34)
sudo dnf install pipewire pipewire-pulseaudio wireplumber
```

## Полезные команды
```bash
# Статус PipeWire
systemctl --user status pipewire pipewire-pulse wireplumber

# Перезапуск
systemctl --user restart pipewire pipewire-pulse wireplumber

# Список аудио-устройств
wpctl status               # WirePlumber
pactl list sinks short     # PulseAudio-совместимый

# Громкость
wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.5     # 50%
wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+     # +5%
wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle    # вкл/выкл звук

# Переключить sink (выход)
wpctl set-default <id_sink>

# Информация о потоках
pw-top           # мониторинг потоков в реальном времени
pw-dump          # полный дамп графа

# Bluetooth
wpctl status | grep -i bluetooth
```

## Конфигурация
Файлы конфигурации:
- `/usr/share/pipewire/pipewire.conf` — дефолт (НЕ редактировать)
- `~/.config/pipewire/pipewire.conf.d/` — пользовательские переопределения
- `/etc/pipewire/pipewire.conf.d/` — системные переопределения

Пример: увеличение буфера для предотвращения треска:
```bash
mkdir -p ~/.config/pipewire/pipewire.conf.d/
cat > ~/.config/pipewire/pipewire.conf.d/99-fix-crackling.conf << 'EOF'
context.properties = {
    default.clock.quantum     = 1024
    default.clock.min-quantum = 512
}
EOF
systemctl --user restart pipewire
```

## Bluetooth Audio
```bash
# Проверка кодеков
pactl list cards | grep -A20 bluez

# Доступные кодеки: SBC, AAC, aptX, aptX-HD, LDAC
# Переключить кодек
wpctl set-profile <card_id> <profile_id>
```

## Частые проблемы
1. **Нет звука** — `wpctl status`, проверьте default sink, unmute
2. **Треск/заикание** — увеличьте quantum (см. выше)
3. **Bluetooth не подключается** — `systemctl --user restart wireplumber`
4. **Конфликт с PulseAudio** — `sudo pacman -R pulseaudio` (заменяется pipewire-pulse)

## JACK-совместимость (для продакшн-аудио)
```bash
# PipeWire заменяет JACK — DAW и аудио-приложения работают нативно

# Установить JACK-совместимый слой
sudo pacman -S pipewire-jack      # Arch
sudo apt install pipewire-jack    # Debian

# Запуск JACK-приложений (Ardour, Carla, Guitarix)
pw-jack ardour                    # обёртка (обычно не нужна)
# Большинство JACK-приложений работают автоматически

# Проверить JACK-совместимость
jack_lsp                          # список JACK-портов
jack_connect                      # соединить порты

# Настройка низкой задержки для DAW
# ~/.config/pipewire/pipewire.conf.d/99-lowlatency.conf
context.properties = {
    default.clock.rate          = 48000
    default.clock.quantum       = 64       # ~1.3ms при 48kHz
    default.clock.min-quantum   = 32
    default.clock.max-quantum   = 1024
}
```

## WirePlumber — менеджер сессий
```bash
# WirePlumber управляет маршрутизацией и политиками аудио
systemctl --user status wireplumber

# Конфигурация через Lua-скрипты
# /usr/share/wireplumber/main.lua.d/    ← дефолт
# ~/.config/wireplumber/main.lua.d/     ← пользовательская

# Пример: установить устройство по умолчанию
mkdir -p ~/.config/wireplumber/main.lua.d/
cat > ~/.config/wireplumber/main.lua.d/51-default-device.lua << 'EOF'
rule = {
  matches = {
    { { "node.name", "equals", "alsa_output.pci-0000_00_1f.3.analog-stereo" } },
  },
  apply_properties = {
    ["priority.session"] = 2000,
  },
}
table.insert(default_access.rules, rule)
EOF

# Перезапуск
systemctl --user restart wireplumber
```

## Профили (Bluetooth A2DP / HSP/HFP)
```bash
# A2DP — высокое качество (только воспроизведение)
# HSP/HFP — с микрофоном (низкое качество)

# Переключение профилей
pactl list cards short             # найти карту bluetooth
pactl set-card-profile <card_id> a2dp-sink          # A2DP
pactl set-card-profile <card_id> headset-head-unit  # HSP/HFP

# Автоматическое переключение (WirePlumber)
# По умолчанию WirePlumber автоматически переключает профиль
# когда приложение запрашивает микрофон

# Поддерживаемые кодеки (pipewire-pulse 0.3.40+)
# SBC, SBC-XQ, AAC, aptX, aptX-HD, LDAC, LC3, LC3plus
# Выбор кодека: автоматически (лучший доступный)
```

## Screen Share на Wayland (через PipeWire)
```bash
# PipeWire обеспечивает screen sharing для Wayland
# Нужен xdg-desktop-portal

sudo pacman -S xdg-desktop-portal-kde      # KDE
sudo pacman -S xdg-desktop-portal-gnome    # GNOME
sudo pacman -S xdg-desktop-portal-wlr      # wlroots (Sway, Hyprland)

# Проверить работу portal
systemctl --user status xdg-desktop-portal
systemctl --user status xdg-desktop-portal-kde

# Для Chromium/Chrome:
# chrome://flags → Preferred Ozone platform → Wayland
# chrome://flags → WebRTC PipeWire support → Enabled

# Для Firefox:
# about:config → media.webrtc.camera.allow-pipewire = true
```

## Мониторинг и отладка
```bash
# pw-top — мониторинг потоков в реальном времени
pw-top

# pw-dump — полный дамп графа (JSON)
pw-dump | jq '.[] | select(.type == "PipeWire:Interface:Node")'

# pw-cli — управление через CLI
pw-cli ls Node                   # список нод
pw-cli info <id>                 # информация о ноде

# pw-dot — визуализация графа
pw-dot | dot -Tsvg > pipewire-graph.svg

# Логи PipeWire
PIPEWIRE_DEBUG=3 pipewire        # подробные логи (уровень 0-5)
journalctl --user -u pipewire -f

# SPA профиль (для отладки)
spa-monitor alsa/monitor
```

## Миграция с PulseAudio на PipeWire
```bash
# 1. Удалить PulseAudio
sudo pacman -R pulseaudio pulseaudio-bluetooth  # Arch
sudo apt remove pulseaudio                       # Debian

# 2. Установить PipeWire
sudo pacman -S pipewire pipewire-pulse pipewire-jack wireplumber

# 3. Включить
systemctl --user enable --now pipewire pipewire-pulse wireplumber

# 4. Проверить
pactl info | grep "Server Name"
# Server Name: PulseAudio (on PipeWire 0.3.xx)

# Обратная совместимость:
# - pactl, pavucontrol, pamixer — работают
# - ~/.config/pulse/* — НЕ используется (PipeWire свои конфиги)
```
