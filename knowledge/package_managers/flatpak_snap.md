# Flatpak и Snap — универсальные пакетные системы

## Flatpak

### Установка
```bash
# Arch / CachyOS
sudo pacman -S flatpak

# Ubuntu (обычно предустановлен)
sudo apt install flatpak

# Добавить Flathub
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
```

### Основные команды
```bash
# Поиск
flatpak search <name>

# Установка
flatpak install flathub <app-id>
flatpak install flathub com.spotify.Client

# Запуск
flatpak run <app-id>

# Список установленных
flatpak list
flatpak list --app              # только приложения

# Обновление
flatpak update                  # обновить все
flatpak update <app-id>        # обновить конкретное

# Удаление
flatpak uninstall <app-id>
flatpak uninstall --unused      # удалить неиспользуемые рантаймы

# Информация
flatpak info <app-id>

# Разрешения
flatpak override --user <app-id> --filesystem=/path  # дать доступ к папке
flatpak override --user --show <app-id>              # показать переопределения
```

### Flatseal (управление разрешениями)
```bash
flatpak install flathub com.github.tchx84.Flatseal
# GUI для управления разрешениями Flatpak-приложений
```

## Snap (Ubuntu)

### Основные команды
```bash
# Поиск
snap find <name>

# Установка
sudo snap install <name>
sudo snap install --classic <name>  # с полным доступом

# Список
snap list

# Обновление
sudo snap refresh

# Удаление
sudo snap remove <name>

# Информация
snap info <name>
```

### Отключение Snap (если не нужен)
```bash
# Ubuntu
sudo snap remove --purge firefox
sudo snap remove --purge snap-store
sudo apt remove --purge snapd
sudo apt-mark hold snapd           # предотвратить переустановку
```

## AppImage
```bash
# Запуск AppImage
chmod +x app.AppImage
./app.AppImage

# Интеграция с системой
# Установите AppImageLauncher для автоматической интеграции
sudo pacman -S appimagelauncher    # AUR
```

## Сравнение
| Критерий | Flatpak | Snap | AppImage |
|---|---|---|---|
| Sandbox | ✅ (Bubblewrap) | ✅ | ❌ |
| Централизованный репо | Flathub | Snap Store | Нет |
| Размер | Средний | Большой | Средний |
| Автообновление | ✅ | ✅ | ❌ |
| Запуск без установки | ❌ | ❌ | ✅ |

## Flatpak — продвинутое использование

### Управление разрешениями (Flatseal)
```bash
# Установка Flatseal — GUI для управления разрешениями
flatpak install flathub com.github.tchx84.Flatseal

# CLI — просмотр разрешений
flatpak info --show-permissions <app_id>

# CLI — переопределение разрешений
flatpak override --user <app_id> --filesystem=home
flatpak override --user <app_id> --socket=wayland
flatpak override --user <app_id> --env=MOZ_ENABLE_WAYLAND=1
flatpak override --user <app_id> --no-talk-name=org.freedesktop.Notifications

# Отменить переопределение
flatpak override --user --reset <app_id>
```

### Flatpak runtime и SDK
```bash
# Runtime — общие библиотеки для приложений
flatpak list --runtime

# Удалить неиспользуемые runtime
flatpak uninstall --unused

# Пересобрать приложение (для разработчиков)
flatpak-builder build-dir manifest.yml --force-clean
flatpak-builder --install --user build-dir manifest.yml
```

### Flatpak downgrade
```bash
# Показать историю коммитов
flatpak remote-info --log flathub <app_id>

# Откатить на конкретный коммит
sudo flatpak update --commit=<hash> <app_id>

# Заблокировать обновление
flatpak mask <app_id>
flatpak mask --remove <app_id>   # разблокировать
```

### Flatpak и темы
```bash
# GTK-тема для Flatpak-приложений
flatpak install flathub org.gtk.Gtk3theme.Adwaita-dark
# Или для KDE:
flatpak install flathub org.gtk.Gtk3theme.Breeze

# Иконки
flatpak override --user --filesystem=~/.icons
flatpak override --user --filesystem=~/.local/share/icons

# Шрифты
flatpak override --user --filesystem=~/.local/share/fonts
```

### Sideload (установка без репозитория)
```bash
# Установить из файла .flatpakref
flatpak install --from /path/to/app.flatpakref

# Установить из bundle (.flatpak)
flatpak install /path/to/app.flatpak

# Экспортировать в bundle
flatpak build-bundle /var/lib/flatpak/repo app.flatpak <app_id>
```

## Snap — продвинутое использование

### Snap channels (каналы)
```bash
# Каналы: stable, candidate, beta, edge
snap install <package> --channel=beta
snap refresh <package> --channel=stable

# Переключить канал
snap switch <package> --channel=edge
snap refresh <package>
```

### Snap confinement
```bash
# Уровни изоляции:
# strict — полная песочница (по умолчанию)
# classic — полный доступ к системе (IDE, компиляторы)
# devmode — только для разработки

snap install code --classic        # VS Code
snap install lxd                    # strict
```

### Snap interfaces (разрешения)
```bash
# Список интерفейсов
snap connections <package>

# Подключить интерфейс
snap connect <package>:<plug> :<slot>

# Отключить
snap disconnect <package>:<plug>

# Пример: дать доступ к домашнему каталогу
snap connect myapp:home :home

# Список всех доступных интерфейсов
snap interface --all
```

### Полное отключение Snap (Ubuntu)
```bash
# Удалить все snap-пакеты
snap list                           # список
sudo snap remove --purge <package>  # для каждого

# Удалить snapd
sudo apt remove --purge snapd
sudo apt-mark hold snapd            # предотвратить переустановку

# Заблокировать в apt
echo 'Package: snapd
Pin: release a=*
Pin-Priority: -10' | sudo tee /etc/apt/preferences.d/nosnap.pref

# Замена snap-пакетов на Flatpak или deb:
# Firefox: sudo add-apt-repository ppa:mozillateam/ppa
# Chromium: flatpak install flathub org.chromium.Chromium
```

## AppImage — продвинутое использование
```bash
# Извлечь содержимое AppImage
./app.AppImage --appimage-extract
# Создаётся каталог squashfs-root/

# Обновить (если поддержка встроена)
./app.AppImage --appimage-update

# Создать .desktop файл вручную
cat > ~/.local/share/applications/myapp.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=My App
Exec=/path/to/app.AppImage
Icon=/path/to/icon.png
Categories=Utility;
EOF

# AppImageLauncher — автоматическая интеграция
sudo pacman -S appimagelauncher    # AUR
# При запуске AppImage предложит "Integrate and run"
```

## Nix — ещё один универсальный менеджер
```bash
# Установка Nix (single-user)
sh <(curl -L https://nixos.org/nix/install) --no-daemon

# Установить пакет
nix-env -iA nixpkgs.firefox

# Поиск
nix search nixpkgs firefox

# Удалить
nix-env -e firefox

# Garbage collection
nix-collect-garbage -d

# Преимущества:
# - Атомарные обновления и откаты
# - Воспроизводимые сборки
# - Может работать рядом с системным менеджером
```
