# KDE Plasma — настройка и управление

## Обзор
KDE Plasma — один из самых популярных рабочих столов Linux. Гибкий, настраиваемый,
поддерживает Wayland и X11.

## Основные компоненты
- **Plasmashell** — рабочий стол, панель, виджеты
- **KWin** — оконный менеджер и композитор
- **KRunner** — быстрый поиск (Alt+F2)
- **System Settings** — центр настройки
- **Dolphin** — файловый менеджер
- **Konsole** — терминал
- **KDE Connect** — связь с телефоном

## Полезные команды
```bash
# Перезапуск Plasma
kquitapp5 plasmashell && kstart5 plasmashell

# Перезапуск KWin
kwin_x11 --replace &    # X11
kwin_wayland --replace & # Wayland

# Очистка кэша
rm -rf ~/.cache/plasmashell*
rm -rf ~/.cache/kwin*

# Сброс панели
mv ~/.config/plasma-org.kde.plasma.desktop-appletsrc ~/.config/plasma-org.kde.plasma.desktop-appletsrc.bak
```

## Настройка через терминал
```bash
# Тема
lookandfeeltool --list           # список тем
lookandfeeltool --apply Breeze   # применить тему

# Обои
qdbus org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript '
  var Desktops = desktops();
  for (i=0;i<Desktops.length;i++) {
    d = Desktops[i]; d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = Array("Wallpaper", "org.kde.image", "General");
    d.writeConfig("Image", "file:///path/to/wallpaper.jpg")
  }'
```

## Горячие клавиши по умолчанию
| Комбинация | Действие |
|---|---|
| Meta | Открыть меню приложений |
| Alt+F2 | KRunner |
| Meta+E | Dolphin |
| Meta+L | Блокировка экрана |
| Ctrl+Alt+T | Терминал (если настроено) |
| Meta+Tab | Переключение окон |

## Часто встречающиеся проблемы
1. **Панель исчезла** — `kquitapp5 plasmashell && kstart5 plasmashell`
2. **Высокий CPU plasmashell** — удалите проблемный виджет, очистите кэш
3. **Wayland баги** — откатитесь на X11: выберите "Plasma X11" при входе
4. **Шрифты размытые** — System Settings → Fonts → Force DPI: 96

## Конфигурационные файлы
```
~/.config/
├── kdeglobals              # Глобальные настройки (тема, цвета, шрифты)
├── kwinrc                  # Настройки KWin (эффекты, тайлинг)
├── kglobalshortcutsrc      # Глобальные горячие клавиши
├── khotkeysrc              # Кастомные горячие клавиши
├── plasmarc                # Настройки Plasma (тема, виджеты)
├── plasma-org.kde.plasma.desktop-appletsrc  # Панель и виджеты
├── dolphinrc               # Настройки Dolphin
├── konsolerc               # Настройки Konsole
├── kscreenlockerrc         # Экран блокировки
└── kwinrulesrc             # Правила для окон
```

## Управление KWin через терминал
```bash
# Список эффектов
qdbus org.kde.KWin /Effects org.kde.KWin.Effects.loadedEffects

# Включить/выключить эффект
qdbus org.kde.KWin /Effects org.kde.KWin.Effects.loadEffect wobblywindows
qdbus org.kde.KWin /Effects org.kde.KWin.Effects.unloadEffect wobblywindows

# Тайлинг (Plasma 5.27+/6)
# System Settings → Window Management → Window Tiling

# Правила для окон
# System Settings → Window Management → Window Rules
# Или kwriteconfig5:
kwriteconfig5 --file kwinrc --group Windows --key FocusPolicy ClickToFocus
qdbus org.kde.KWin /KWin reconfigure
```

## KDE Connect — связь с телефоном
```bash
# Установка
sudo pacman -S kdeconnect    # Arch
sudo apt install kdeconnect  # Debian

# Firewall — открыть порты
sudo ufw allow 1714:1764/udp
sudo ufw allow 1714:1764/tcp

# CLI
kdeconnect-cli -l            # список устройств
kdeconnect-cli -d <id> --ping
kdeconnect-cli -d <id> --share /path/to/file
kdeconnect-cli -d <id> --ring   # найти телефон

# Возможности:
# - Общий буфер обмена
# - Передача файлов
# - Уведомления с телефона на ПК
# - Управление мышкой/клавиатурой
# - Управление медиа
# - SMS с ПК
# - Запуск команд
```

## Plasma Vaults (шифрованные папки)
```bash
# Создание через GUI: System Tray → Vaults → Create New Vault
# Бэкенды: CryFS (рекомендуется), EncFS, gocryptfs

# CLI
plasma-vault create --name "Private" --backend cryfs --mountpoint ~/Vaults/Private
```

## Активити (Activities)
```bash
# Активити — виртуальные рабочие пространства со своими обоями, виджетами, приложениями
# Создание: Meta → Activities → Create Activity
# Переключение: Meta+Tab (если настроено)

# CLI
qdbus org.kde.ActivityManager /ActivityManager/Activities ListActivities
qdbus org.kde.ActivityManager /ActivityManager/Activities AddActivity "Work"
```

## SDDM — менеджер входа
```bash
# Настройка (/etc/sddm.conf.d/kde_settings.conf)
[Theme]
Current=breeze

[Autologin]
User=username
Session=plasma

# Тестирование темы
sddm-greeter --test-mode --theme /usr/share/sddm/themes/breeze
```

## Полезные Plasma-виджеты
| Виджет | Описание |
|--------|----------|
| System Monitor | Мониторинг CPU, RAM, сети, дисков |
| Weather | Погода (OpenWeatherMap) |
| Notes | Заметки на рабочем столе |
| Timer | Таймер и секундомер |
| Color Picker | Пипетка для цветов |
| Clipboard | История буфера обмена |
| KSysGuard | Системный монитор |
| Night Color | Ночной режим (снижение синего) |

## Оптимизация производительности
```bash
# Отключить анимации
kwriteconfig5 --file kwinrc --group Compositing --key AnimationSpeed 0

# Отключить compositing (для слабых GPU)
kwriteconfig5 --file kwinrc --group Compositing --key Enabled false

# Уменьшить эффекты
# System Settings → Workspace Behavior → Desktop Effects
# Отключить: Blur, Translucency, Wobbly Windows

# Мониторинг ресурсов
qdbus org.kde.KWin /KWin supportInformation  # диагностика KWin
```

## Plasma 6 (Qt6) — отличия от Plasma 5
| Plasma 5 | Plasma 6 |
|----------|----------|
| Qt5 / kf5 | Qt6 / kf6 |
| kquitapp5, kstart5 | kquitapp6, kstart6 |
| kwriteconfig5 | kwriteconfig6 |
| lookandfeeltool | plasma-apply-lookandfeel |
| X11 по умолчанию | Wayland по умолчанию |
| KStatusNotifierItem | Переработанный System Tray |
| Плоские настройки | Унифицированный System Settings |
