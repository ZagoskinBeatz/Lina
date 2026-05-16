# Браузеры в Linux — Firefox, Chromium, настройка

## Firefox

### Установка

```bash
# Arch / CachyOS
sudo pacman -S firefox firefox-i18n-ru

# Ubuntu (snap по умолчанию, deb-версия)
sudo snap install firefox
# Или deb через Mozilla PPA:
sudo add-apt-repository ppa:mozillateam/ppa
sudo apt install firefox

# Flatpak
flatpak install flathub org.mozilla.firefox
```

### Полезные настройки (about:config)

```text
# Плавная прокрутка
general.smoothScroll=true

# Аппаратное ускорение
gfx.webrender.all=true
media.ffmpeg.vaapi.enabled=true     # VA-API для видео

# Приватность
privacy.trackingprotection.enabled=true
dom.security.https_only_mode=true

# Производительность
browser.cache.disk.capacity=512000
network.http.max-connections=256
```

### Wayland

```bash
# Запуск Firefox на Wayland
MOZ_ENABLE_WAYLAND=1 firefox

# Или в /etc/environment:
MOZ_ENABLE_WAYLAND=1
```

## Chromium / Chrome

### Установка Chromium / Chrome

```bash
# Arch (open-source Chromium)
sudo pacman -S chromium

# Google Chrome (AUR)
yay -S google-chrome

# Ubuntu
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
sudo apt install google-chrome-stable
```

### Флаги (chrome://flags)

```text
# Wayland
--ozone-platform-hint=auto

# GPU ускорение
--enable-gpu-rasterization
--enable-zero-copy
--enable-features=VaapiVideoDecoder
```

## Tor Browser

```bash
# Arch
sudo pacman -S torbrowser-launcher
torbrowser-launcher

# Flatpak
flatpak install flathub com.github.nickvergessen.TorBrowser
```

## Brave Browser

```bash
# Arch (AUR)
yay -S brave-bin

# Flatpak
flatpak install flathub com.brave.Browser
```

## Общие проблемы

### Видео тормозит (нет аппаратного декодирования)

```bash
# Firefox
about:config → media.ffmpeg.vaapi.enabled=true
# Проверить VA-API
vainfo

# Chromium
chrome://flags → #enable-accelerated-video-decode
```

### Шрифты выглядят плохо

```bash
sudo pacman -S noto-fonts noto-fonts-cjk noto-fonts-emoji ttf-liberation
# Настроить в ~/.config/fontconfig/fonts.conf
```
