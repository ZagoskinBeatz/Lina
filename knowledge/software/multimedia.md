# Мультимедиа в Linux — VLC, OBS, GIMP, Kdenlive

## Видеоплееры

### VLC

```bash
# Установка
sudo pacman -S vlc             # Arch
sudo apt install vlc            # Ubuntu
flatpak install flathub org.videolan.VLC

# Командная строка
vlc video.mp4
cvlc video.mp4                  # без GUI
```

### mpv (минималистичный)

```bash
sudo pacman -S mpv
mpv video.mp4
mpv --vo=gpu video.mp4          # GPU ускорение
```

## Запись экрана — OBS Studio

```bash
# Установка
sudo pacman -S obs-studio
flatpak install flathub com.obsproject.Studio

# Wayland: используйте PipeWire для захвата экрана
# OBS → Источники → Захват экрана (PipeWire)

# Плагины
# obs-vkcapture — для захвата Vulkan-игр
sudo pacman -S obs-vkcapture lib32-obs-vkcapture
```

## Графические редакторы

### GIMP

```bash
sudo pacman -S gimp
# Аналог Photoshop, поддерживает слои, фильтры, скрипты
```

### Krita (цифровая живопись)

```bash
sudo pacman -S krita
# Оптимизирован для графических планшетов
```

### Inkscape (вектор)

```bash
sudo pacman -S inkscape
# SVG-редактор, аналог Illustrator
```

## Видеоредакторы

### Kdenlive

```bash
sudo pacman -S kdenlive
# Полнофункциональный видеоредактор (KDE)
# Поддерживает: нарезку, эффекты, переходы, титры, аудио
```

### Shotcut

```bash
sudo pacman -S shotcut
flatpak install flathub org.shotcut.Shotcut
```

## Аудио

### Audacity

```bash
sudo pacman -S audacity
# Редактор аудио: запись, обрезка, эффекты, шумоподавление
```

### LMMS (создание музыки)

```bash
sudo pacman -S lmms
# DAW — аналог FL Studio, встроенные синтезаторы
```

## Кодеки и мультимедиа

```bash
# Arch — все кодеки
sudo pacman -S gst-plugins-good gst-plugins-bad gst-plugins-ugly \
  gst-libav ffmpeg

# Ubuntu
sudo apt install ubuntu-restricted-extras

# Fedora (RPM Fusion)
sudo dnf install gstreamer1-plugins-{bad-*,good-*,base} \
  gstreamer1-plugin-openh264 gstreamer1-libav
```
