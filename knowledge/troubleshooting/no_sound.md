# Нет звука в Linux — диагностика и решение

## Быстрая диагностика

```bash
# 1. Проверить, какая звуковая система используется
pactl info | head -5          # PipeWire или PulseAudio
pipewire --version 2>/dev/null && echo "PipeWire" || echo "Не PipeWire"

# 2. Проверить устройства вывода
pactl list sinks short        # Список устройств вывода
wpctl status                  # PipeWire: полный статус

# 3. Проверить громкость
pactl get-sink-volume @DEFAULT_SINK@
pactl get-sink-mute @DEFAULT_SINK@

# 4. Проверить ALSA (нижний уровень)
aplay -l                      # Список звуковых карт
cat /proc/asound/cards        # То же

# 5. Проверить модули ядра
lsmod | grep snd
dmesg | grep -i audio
dmesg | grep -i snd
```

## PipeWire (современная система, замена PulseAudio)

### Перезапуск PipeWire
```bash
systemctl --user restart pipewire pipewire-pulse wireplumber
```

### Проверка и настройка
```bash
# Статус
systemctl --user status pipewire
systemctl --user status pipewire-pulse
systemctl --user status wireplumber

# Переключить устройство вывода
wpctl set-default <sink_id>

# Громкость
wpctl set-volume @DEFAULT_AUDIO_SINK@ 50%
wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+
wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle
```

### Установка PipeWire (если не установлен)
```bash
# Arch
sudo pacman -S pipewire pipewire-pulse pipewire-alsa wireplumber

# Ubuntu 22.04+
sudo apt install pipewire pipewire-pulse wireplumber

# Fedora (по умолчанию уже есть)
```

## PulseAudio

### Перезапуск
```bash
pulseaudio --kill
pulseaudio --start
# ИЛИ
systemctl --user restart pulseaudio
```

### Настройка
```bash
# GUI
pavucontrol                   # Графические настройки

# CLI
pactl list sinks              # Устройства вывода
pactl set-default-sink <name> # Установить по умолчанию
pactl set-sink-volume @DEFAULT_SINK@ 100%
pactl set-sink-mute @DEFAULT_SINK@ false
```

## Типичные проблемы

### Звук пропал после обновления
```bash
# Перезапустить звуковую систему
systemctl --user restart pipewire pipewire-pulse wireplumber
# ИЛИ
pulseaudio --kill && pulseaudio --start

# Переустановить пакеты
# Arch:
sudo pacman -S pipewire pipewire-pulse wireplumber
# Ubuntu:
sudo apt reinstall pipewire pipewire-pulse
```

### HDMI/DisplayPort — нет звука
```bash
# Проверить доступные устройства
pactl list sinks | grep -A5 "Name:"

# Переключить на HDMI
pactl set-default-sink alsa_output.pci-0000_01_00.1.hdmi-stereo
# ИЛИ через wpctl
wpctl set-default <hdmi_sink_id>
```

### Bluetooth наушники — нет звука
```bash
# Проверить подключение
bluetoothctl devices Connected

# Переключить профиль на A2DP (высокое качество)
pactl set-card-profile <card_name> a2dp-sink

# Если не удаётся — удалить и переподключить
bluetoothctl remove <MAC>
bluetoothctl scan on
bluetoothctl pair <MAC>
bluetoothctl connect <MAC>
```

### Треск/щелчки в звуке
```bash
# PipeWire: увеличить буфер
# ~/.config/pipewire/pipewire.conf.d/99-latency.conf
context.properties = {
    default.clock.rate = 48000
    default.clock.quantum = 1024
    default.clock.min-quantum = 512
}
systemctl --user restart pipewire

# ALSA: параметр tsched
# /etc/pulse/default.pa (PulseAudio)
load-module module-udev-detect tsched=0
```

### Микрофон не работает
```bash
# Проверить устройства ввода
pactl list sources short
wpctl status                  # Секция "Audio → Sources"

# Проверить громкость входа
pactl set-source-volume @DEFAULT_SOURCE@ 100%
pactl set-source-mute @DEFAULT_SOURCE@ false

# Проверить разрешения
ls -la /dev/snd/
# Пользователь должен быть в группе audio
sudo usermod -aG audio $USER
```

## Диагностика ALSA
```bash
# Проверить ALSA-устройства
aplay -l                         # список устройств воспроизведения
arecord -l                       # список устройств записи
cat /proc/asound/cards           # звуковые карты ядра
cat /proc/asound/modules         # загруженные модули

# Тест ALSA напрямую (минуя PipeWire/PulseAudio)
speaker-test -D hw:0,0 -c 2     # тест на конкретном устройстве
aplay /usr/share/sounds/alsa/Front_Left.wav

# ALSA mixer
alsamixer                        # TUI mixer
# F6 → выбрать карту
# M → unmute
# Стрелки → громкость

# Сохранить настройки ALSA
sudo alsactl store
sudo alsactl restore             # восстановить
```

## Проблемы с конкретными картами

### Realtek (ALC-серия)
```bash
# Подобрать правильную модель для HDA
# /etc/modprobe.d/alsa.conf
options snd-hda-intel model=generic
# Или для конкретной модели:
# options snd-hda-intel model=dell-headset-multi
# options snd-hda-intel model=asus-mode4

# Посмотреть доступные модели
modinfo snd-hda-intel | grep model

# Перезагрузить модуль
sudo modprobe -r snd-hda-intel
sudo modprobe snd-hda-intel
```

### USB-звуковые карты
```bash
# Проверить USB-аудио
lsusb | grep -i audio
dmesg | grep -i "usb.*audio"

# Если USB-карта не определяется:
sudo modprobe snd-usb-audio

# Задать как устройство по умолчанию
# wpctl:
wpctl status                     # найти ID USB-устройства
wpctl set-default <id>

# Или pactl:
pactl set-default-sink <sink_name>
```

### HDMI/DisplayPort аудио
```bash
# Проверить HDMI-выход
pactl list sinks | grep -A5 hdmi
wpctl status | grep -i hdmi

# Переключить на HDMI
pactl set-default-sink alsa_output.pci-0000_01_00.1.hdmi-stereo
# или
wpctl set-default <hdmi_sink_id>

# NVIDIA: HDMI аудио использует nvidia-драйвер
# Проверить: aplay -l | grep NVIDIA
# AMD: HDMI аудио через amdgpu
# Intel: HDMI через snd_hda_intel
```

## Звук в контейнерах и Flatpak
```bash
# Flatpak аудио через PipeWire portal
# Проверить разрешения:
flatpak info --show-permissions <app_id>
# Должно быть: --socket=pulseaudio или --socket=pipewire

# Исправить с Flatseal:
flatpak install flathub com.github.tchx84.Flatseal
# Включить: PulseAudio socket

# Docker аудио:
docker run --device /dev/snd -e PULSE_SERVER=unix:/run/user/1000/pulse/native \
  -v /run/user/1000/pulse/native:/run/user/1000/pulse/native \
  <image>
```

## Полная переустановка аудио-стека
```bash
# Ядерный вариант: полный сброс аудио

# Arch / CachyOS:
sudo pacman -Rns pulseaudio pulseaudio-bluetooth 2>/dev/null
sudo pacman -S pipewire pipewire-pulse pipewire-jack wireplumber
rm -rf ~/.config/pulse/ ~/.config/pipewire/
systemctl --user enable --now pipewire pipewire-pulse wireplumber

# Debian / Ubuntu:
sudo apt purge pulseaudio
sudo apt install pipewire pipewire-pulse wireplumber
systemctl --user enable --now pipewire pipewire-pulse wireplumber

# После переустановки:
systemctl --user restart pipewire pipewire-pulse wireplumber
wpctl status                     # проверить
```
