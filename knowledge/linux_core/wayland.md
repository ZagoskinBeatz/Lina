# Wayland — дисплейный протокол

## Обзор
Wayland — современный протокол для взаимодействия композитора (оконного менеджера)
с приложениями. Замена X11 с акцентом на безопасность, простоту и производительность.

## Wayland vs X11
| Критерий | Wayland | X11 |
|---|---|---|
| Безопасность | Каждое окно изолировано | Любое окно может читать любое |
| Tearing | Нет (по дизайну) | Возможен |
| Задержка ввода | Ниже | Выше |
| Screen recording | Только через порталы | Любое приложение |
| Совместимость | 95%+ через XWayland | 100% |

## Проверка сессии
```bash
echo $XDG_SESSION_TYPE    # wayland или x11
echo $WAYLAND_DISPLAY     # wayland-0 если Wayland

# loginctl
loginctl show-session $(loginctl | grep $USER | awk '{print $1}') -p Type
```

## Композиторы
- **KWin** — KDE Plasma (Wayland по умолчанию с Plasma 6)
- **Mutter** — GNOME
- **Sway** — тайловый (совместимый с i3)
- **Hyprland** — тайловый с анимациями
- **Wayfire** — плагинный (как Compiz)
- **river** — минималистичный тайловый
- **niri** — скроллинговый

## XWayland (совместимость)
```bash
# Проверить, работает ли приложение нативно или через XWayland
xprop    # курсор станет крестиком — кликните на окно
# Если открывается — XWayland. Если нет — нативный Wayland.

# Или через xlsclients
xlsclients    # список X11-клиентов (= XWayland приложений)
```

## Утилиты для Wayland
```bash
# Скриншоты
grim                              # полный экран
grim -g "$(slurp)"               # область (slurp для выделения)
grim -g "$(slurp)" - | wl-copy   # копировать в буфер

# Буфер обмена
wl-copy "текст"                   # копировать
wl-paste                          # вставить

# Мониторы
wlr-randr                         # для wlroots-композиторов
# KDE: используйте System Settings → Display

# Запись экрана
wf-recorder                        # для wlroots
wf-recorder -g "$(slurp)"        # область
```

## Переменные окружения для Wayland
```bash
# В ~/.config/environment.d/wayland.conf или /etc/environment:
MOZ_ENABLE_WAYLAND=1              # Firefox нативный Wayland
QT_QPA_PLATFORM=wayland           # Qt приложения → Wayland
GDK_BACKEND=wayland               # GTK → Wayland
SDL_VIDEODRIVER=wayland            # SDL → Wayland
ELECTRON_OZONE_PLATFORM_HINT=auto # Electron (Chrome, VS Code) → Wayland
```

## Частые проблемы
1. **Electron-приложения размыты** → Добавьте флаг `--ozone-platform=wayland`
2. **Screen sharing не работает** → Установите `xdg-desktop-portal-kde` или `xdg-desktop-portal-gnome`
3. **Игра не запускается** → `SDL_VIDEODRIVER=x11 game` (fallback на XWayland)
4. **Приложение падает** → Запустите с `QT_QPA_PLATFORM=xcb` или `GDK_BACKEND=x11`

## Подробнее о XWayland
```bash
# XWayland — слой совместимости для X11-приложений
# Запускается автоматически для X11-приложений

# Проверить, какие окна используют XWayland
xlsclients                        # X11 клиенты
# В KDE: окно → правый клик → More → Special → XWayland/Wayland

# Принудительно запустить через XWayland
QT_QPA_PLATFORM=xcb firefox
GDK_BACKEND=x11 gimp

# Принудительно через нативный Wayland
QT_QPA_PLATFORM=wayland libreoffice
GDK_BACKEND=wayland nautilus
```

## Безопасность Wayland vs X11
| Аспект | X11 | Wayland |
|--------|-----|---------|
| Кейлоггинг | Легко (любое приложение) | Защищено |
| Скриншот чужих окон | Возможно | Только через portal |
| Захват ввода | Глобальный | Изолированный |
| Буфер обмена | Глобальный доступ | Ограниченный |
| Screen recording | Свободный | Через PipeWire portal |

## Скриншоты на Wayland
```bash
# Spectacle (KDE)
spectacle                        # GUI
spectacle -r                     # выделить область
spectacle -f                     # полный экран
spectacle -a                     # активное окно

# grim + slurp (wlroots-совместимые)
grim screenshot.png              # полный экран
grim -g "$(slurp)" area.png     # выделить область

# Flameshot на Wayland
# Требует: Grim backend
```

## Запись экрана на Wayland
```bash
# OBS Studio (рекомендуется)
# Использовать PipeWire capture source

# wf-recorder (для wlroots)
wf-recorder -f output.mp4
wf-recorder -g "$(slurp)" -f area.mp4    # область
wf-recorder -a                            # с аудио (PipeWire)

# Kooha (GNOME)
flatpak install flathub io.github.seadve.Kooha
```

## Clipboard (буфер обмена) на Wayland
```bash
# wl-clipboard — аналог xclip/xsel
sudo pacman -S wl-clipboard

echo "текст" | wl-copy           # скопировать
wl-paste                         # вставить
wl-paste --list-types            # доступные типы
wl-copy < file.png               # скопировать файл
wl-paste > output.txt            # вставить в файл

# Менеджер истории буфера
# KDE: Klipper (встроен)
# GNOME: GPaste
# wlroots: clipman, cliphist
cliphist list | wofi -d | cliphist decode | wl-copy
```

## HDR (High Dynamic Range) на Wayland
```bash
# HDR поддерживается начиная с KDE Plasma 6 и Gamescope
# Требования:
# - Монитор с HDR
# - GPU: AMD (AMDGPU) или Intel (дискретная)
# - NVIDIA: ограниченная поддержка

# KDE Plasma 6:
# System Settings → Display → HDR → Enable

# Gamescope (для игр):
gamescope --hdr-enabled -- %command%
```

## Тайлинг на Wayland
```bash
# Нативные тайлинговые композиторы:
# - Sway (аналог i3, wlroots)
# - Hyprland (анимации, wlroots)
# - river, dwl, niri

# Sway конфигурация (~/.config/sway/config)
# Наследует синтаксис i3:
bindsym $mod+Return exec alacritty
bindsym $mod+d exec wofi --show drun
bindsym $mod+1 workspace number 1

# Hyprland конфигурация (~/.config/hypr/hyprland.conf)
exec-once = waybar & hyprpaper & dunst
bind = $mainMod, Return, exec, kitty
bind = $mainMod, Q, killactive

# KDE Plasma 5.27+/6: встроенный тайлинг
# System Settings → Window Management → Window Tiling
# Или: Bismuth/Polonium kwin-скрипты
```

## Wayland и NVIDIA
```bash
# Настройка для NVIDIA (545+)
# /etc/modprobe.d/nvidia.conf
options nvidia_drm modeset=1
options nvidia_drm fbdev=1         # для Plasma 6

# Переменные окружения
GBM_BACKEND=nvidia-drm
__GLX_VENDOR_LIBRARY_NAME=nvidia
WLR_NO_HARDWARE_CURSORS=1         # для wlroots (если курсор мигает)

# GRUB: добавить в /etc/default/grub
GRUB_CMDLINE_LINUX="nvidia_drm.modeset=1"
sudo grub-mkconfig -o /boot/grub/grub.cfg
```
