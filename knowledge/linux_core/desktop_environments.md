# Рабочие окружения и оконные менеджеры

## Сравнение рабочих окружений (DE)

| DE | RAM | Toolkit | Композитор | Описание |
|-----|-----|---------|-----------|----------|
| KDE Plasma | ~500 МБ | Qt 6 | KWin | Полноценное, настраиваемое, красивое |
| GNOME | ~600 МБ | GTK 4 | Mutter | Минималистичное, сенсорный ввод |
| Xfce | ~300 МБ | GTK 3 | Xfwm4 | Лёгкое, классическое |
| Cinnamon | ~450 МБ | GTK 3 | Muffin | Классическое (fork GNOME 3) |
| MATE | ~350 МБ | GTK 3 | Marco | Продолжение GNOME 2 |
| LXQt | ~200 МБ | Qt 5 | Openbox/KWin | Очень лёгкое |
| Budgie | ~500 МБ | GTK 3 | Mutter | Современное, простое |
| Deepin DE | ~600 МБ | Qt 5 | KWin | Красивое, китайская разработка |
| Cosmic | ~400 МБ | Iced/Smithay | Smithay | Новое DE от System76 (Rust) |

## KDE Plasma

### Установка
```bash
# Arch
sudo pacman -S plasma-meta kde-applications-meta sddm
sudo systemctl enable sddm

# Минимальная установка:
sudo pacman -S plasma-desktop sddm dolphin konsole kate

# Ubuntu
sudo apt install kde-plasma-desktop

# Fedora
sudo dnf install @kde-desktop-environment
```

### Настройка
```bash
# Файлы конфигурации: ~/.config/
# Ключевые:
~/.config/kwinrc                 # Композитор (эффекты, тайлинг)
~/.config/kdeglobals             # Глобальные настройки
~/.config/plasmarc               # Тема Plasma
~/.config/plasmashellrc          # Панель и виджеты
~/.config/kscreenlockerrc        # Экран блокировки

# Командная строка для настроек
kwriteconfig5 --file ~/.config/kdeglobals --group KDE --key SingleClick false
# Или через kcmshell5:
kcmshell5 desktoptheme           # Тема Plasma
kcmshell5 kcm_colors             # Цветовая схема
kcmshell5 kcm_fonts              # Шрифты
```

### KWin тайлинг (Plasma 6)
```bash
# Plasma 6 включает встроенный тайлинг
# Настройки → Управление окнами → Тайлинг
# Meta+T — режим тайлинга

# Пользовательские скрипты KWin
# ~/.local/share/kwin/scripts/

# Bismuth (сторонний тайлинг для Plasma 5)
# https://github.com/Bismuth-Forge/bismuth
```

## GNOME

### Установка
```bash
# Arch
sudo pacman -S gnome gnome-extra gdm
sudo systemctl enable gdm

# Ubuntu (уже установлен)
# Fedora (уже установлен)
```

### Настройка через gsettings
```bash
# Тёмная тема
gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark'

# Кнопки окна (закрыть, свернуть, развернуть)
gsettings set org.gnome.desktop.wm.preferences button-layout ':minimize,maximize,close'

# Шрифт
gsettings set org.gnome.desktop.interface font-name 'Noto Sans 11'

# Масштабирование (HiDPI)
gsettings set org.gnome.desktop.interface scaling-factor 2

# Горячие клавиши
gsettings set org.gnome.desktop.wm.keybindings switch-applications "['<Super>Tab']"

# Трекпад (tap-to-click)
gsettings set org.gnome.desktop.peripherals.touchpad tap-to-click true
```

### Расширения GNOME
```bash
# Установка менеджера расширений
sudo pacman -S gnome-shell-extensions gnome-browser-connector

# Популярные расширения (extensions.gnome.org):
# - Dash to Dock — док-панель
# - AppIndicator — индикаторы в трее
# - Pop Shell — тайлинг (от System76)
# - Blur my Shell — размытие
# - GSConnect — интеграция с Android

# CLI управление:
gnome-extensions list
gnome-extensions enable <extension-uuid>
gnome-extensions disable <extension-uuid>
```

## Xfce

### Установка
```bash
# Arch
sudo pacman -S xfce4 xfce4-goodies lightdm lightdm-gtk-greeter
sudo systemctl enable lightdm

# Ubuntu
sudo apt install xfce4 xfce4-goodies
```

### Настройка
```bash
# Файлы: ~/.config/xfce4/
# Основной конфиг: ~/.config/xfce4/xfconf/xfce-perchannel-xml/

# Командная строка:
xfconf-query -c xsettings -p /Net/ThemeName -s "Adwaita-dark"
xfconf-query -c xfwm4 -p /general/theme -s "Default"

# Панели:
xfce4-panel --preferences

# Менеджер настроек:
xfce4-settings-manager
```

## Оконные менеджеры (WM)

WM — это только управление окнами, без DE. Для продвинутых пользователей.

### Тайлинговые WM

| WM | Язык | Протокол | Конфигурация |
|----|------|----------|-------------|
| i3 | C | X11 | Текстовый файл |
| Sway | C | Wayland | Совместим с i3 config |
| Hyprland | C++ | Wayland | Текстовый + анимации |
| Awesome | C/Lua | X11 | Lua |
| bspwm | C | X11 | shell-скрипты |
| dwm | C | X11 | Исходный код (patches) |
| River | Zig | Wayland | Текстовый |
| Qtile | Python | X11/Wayland | Python |

### i3 / Sway (конфигурация)
```bash
# Конфиг: ~/.config/i3/config (или ~/.config/sway/config)

# Модификатор
set $mod Mod4        # Super (Win) клавиша

# Запуск программ
bindsym $mod+Return exec alacritty
bindsym $mod+d exec dmenu_run
bindsym $mod+d exec wofi --show drun   # Sway (Wayland)

# Управление окнами
bindsym $mod+h focus left
bindsym $mod+j focus down
bindsym $mod+k focus up
bindsym $mod+l focus right
bindsym $mod+Shift+h move left
bindsym $mod+q kill

# Рабочие столы
bindsym $mod+1 workspace number 1
bindsym $mod+Shift+1 move container to workspace number 1

# Разделение
bindsym $mod+b splith   # Горизонтально
bindsym $mod+v splitv   # Вертикально

# Полноэкранный
bindsym $mod+f fullscreen toggle

# Плавающий режим
bindsym $mod+Shift+space floating toggle

# Статусная строка
bar {
    status_command i3status
    # Или: status_command waybar (для Sway)
}
```

### Hyprland (конфигурация)
```bash
# ~/.config/hypr/hyprland.conf

# Монитор
monitor=,preferred,auto,1

# Программы при запуске
exec-once = waybar
exec-once = hyprpaper
exec-once = dunst

# Горячие клавиши
bind = SUPER, Return, exec, kitty
bind = SUPER, Q, killactive
bind = SUPER, D, exec, wofi --show drun
bind = SUPER, F, fullscreen
bind = SUPER SHIFT, Space, togglefloating

# Анимации
animations {
    enabled = yes
    bezier = myBezier, 0.05, 0.9, 0.1, 1.05
    animation = windows, 1, 7, myBezier
    animation = fade, 1, 7, default
    animation = workspaces, 1, 6, default
}

# Оформление
general {
    gaps_in = 5
    gaps_out = 10
    border_size = 2
    col.active_border = rgba(33ccffee)
}

decoration {
    rounding = 10
    blur {
        enabled = true
        size = 3
    }
}
```

## Display Managers (менеджеры дисплея)

| DM | DE | Особенности |
|----|-----|------------|
| SDDM | KDE | Qt, темизация QML |
| GDM | GNOME | GTK, тяжёлый |
| LightDM | Любой | Лёгкий, множество greeters |
| ly | Любой | TUI (текстовый), минимальный |
| greetd | Любой | Модульный, Wayland |

### Переключение DM
```bash
# Отключить текущий
sudo systemctl disable gdm

# Включить новый
sudo systemctl enable sddm
sudo reboot

# Или запуск без DM (из TTY):
# X11:
startx
# Добавить в ~/.xinitrc:
exec startplasma-x11
exec i3

# Wayland:
# Для Sway:
sway
# Для Hyprland:
Hyprland
```

## Темы и внешний вид

### GTK-темы
```bash
# Пользовательские: ~/.themes/ или ~/.local/share/themes/
# Системные: /usr/share/themes/

# Установить тему через CLI
# GTK 3:
gsettings set org.gnome.desktop.interface gtk-theme "Adwaita-dark"
# GTK 2:
echo 'gtk-theme-name="Adwaita-dark"' >> ~/.gtkrc-2.0

# Для Qt-приложений в GTK-окружении:
# Установить qt5ct / qt6ct
sudo pacman -S qt5ct qt6ct
# Установить переменную:
export QT_QPA_PLATFORMTHEME=qt5ct
```

### Иконки
```bash
# ~/.local/share/icons/
# /usr/share/icons/

# Популярные:
sudo pacman -S papirus-icon-theme
gsettings set org.gnome.desktop.interface icon-theme "Papirus-Dark"
```

### Курсоры
```bash
# ~/.local/share/icons/ (или ~/.icons/)
sudo pacman -S bibata-cursor-theme

# KDE: Настройки → Курсоры
# GNOME:
gsettings set org.gnome.desktop.interface cursor-theme "Bibata-Modern-Classic"
gsettings set org.gnome.desktop.interface cursor-size 24
```

## Wayland vs X11

| Аспект | X11 | Wayland |
|--------|-----|---------|
| Архитектура | Сервер-клиент | Композитор = сервер |
| Безопасность | Низкая (клиенты видят друг друга) | Высокая (изоляция) |
| Скриншоты | Любая программа | Через портал (xdg-desktop-portal) |
| Запись экрана | Простая | Через PipeWire + портал |
| Тиринг | Частый | Редкий |
| HiDPI | Плохая поддержка | Хорошая (per-monitor) |
| Совместимость | Всё работает | XWayland для старого ПО |
| Дробное масштабирование | Плохое | Хорошее (Plasma 6, GNOME 46+) |

```bash
# Проверить текущий протокол
echo $XDG_SESSION_TYPE           # x11 или wayland

# Запуск X11-приложения в Wayland
# XWayland обычно работает автоматически
# Проверить, использует ли окно XWayland:
xprop                            # Если работает → окно через XWayland
```
