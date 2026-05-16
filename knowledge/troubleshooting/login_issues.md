# Проблемы со входом в систему — диагностика и решение

## Типичные симптомы

- Чёрный экран после ввода пароля
- Экран входа (SDDM/GDM/LightDM) зацикливается (login loop)
- «Incorrect password» при верном пароле
- Рабочий стол не загружается, возврат к экрану входа
- TTY работает, но GUI — нет

## Диагностика: TTY первым делом

```bash
# Переключиться в текстовую консоль
Ctrl+Alt+F2                      # Или F3, F4, F5, F6

# Войти логин/пароль
# Если удалось — проблема в GUI, не в системе

# Проверить логи
journalctl -b --priority=err     # Ошибки текущей загрузки
journalctl -b | grep -i -E "sddm|gdm|lightdm|login"
journalctl --user -b             # Логи пользовательской сессии

# Проверить дисковое пространство
df -h
# Частая причина: /home или / заполнен на 100%
```

## Login Loop (зацикливание входа)

### Причина 1: Нет места на диске

```bash
# Проверить
df -h /home /tmp /

# Если заполнен:
# Удалить большие файлы
du -sh ~/.cache/* | sort -rh | head -10
rm -rf ~/.cache/thumbnails/*
rm -rf ~/.local/share/Trash/*

# Очистить журнал
sudo journalctl --vacuum-size=200M

# Очистить кэш пакетов
sudo pacman -Sc            # Arch
sudo apt clean             # Ubuntu/Debian
```

### Причина 2: Повреждён .Xauthority / .ICEauthority

```bash
# X11: удалить файлы авторизации
rm -f ~/.Xauthority ~/.ICEauthority

# Wayland: проверить XDG_RUNTIME_DIR
ls -la /run/user/$(id -u)/
# Должен существовать и принадлежать вашему пользователю
```

### Причина 3: Неправильные права домашнего каталога

```bash
# Проверить
ls -la /home/
# Должно быть: drwx------ username username

# Исправить
sudo chown -R username:username /home/username
sudo chmod 700 /home/username
```

### Причина 4: Проблема с .bashrc / .profile / .xprofile

```bash
# Ошибка в скрипте инициализации может сломать вход
# Переименовать для проверки
mv ~/.bashrc ~/.bashrc.bak
mv ~/.profile ~/.profile.bak
mv ~/.xprofile ~/.xprofile.bak

# Попробовать войти. Если получилось — ошибка в одном из файлов.
```

### Причина 5: Несовместимый DE / WM

```bash
# Посмотреть доступные сессии
ls /usr/share/xsessions/        # X11-сессии
ls /usr/share/wayland-sessions/  # Wayland-сессии

# Попробовать другую сессию на экране входа
# SDDM/GDM обычно позволяют выбрать сессию (шестерёнка/выпадающий список)
```

## Проблемы SDDM (KDE)

### SDDM не запускается

```bash
# Статус
sudo systemctl status sddm

# Логи
journalctl -u sddm -b
cat /var/log/sddm.log 2>/dev/null

# Перезапустить
sudo systemctl restart sddm

# Переустановить
sudo pacman -S sddm sddm-kcm     # Arch
sudo apt install sddm              # Debian/Ubuntu
```

### SDDM показывает чёрный экран

```bash
# Проблема с GPU-драйвером
# Попробовать другой бэкенд
sudo nano /etc/sddm.conf.d/10-wayland.conf
```

```ini
[General]
DisplayServer=x11
# Если было wayland — переключить на x11 или наоборот
```

```bash
# Или полностью сбросить конфигурацию
sudo rm -f /etc/sddm.conf.d/*
sudo systemctl restart sddm
```

### SDDM не принимает пароль

```bash
# Проверить PAM
cat /etc/pam.d/sddm
# Должен включать system-auth или common-auth

# Проверить, не истёк ли пароль
sudo chage -l username

# Сбросить пароль (из TTY от root)
sudo passwd username
```

## Проблемы GDM (GNOME)

### GDM не запускается

```bash
sudo systemctl status gdm
journalctl -u gdm -b

# Переустановить
sudo pacman -S gdm                 # Arch
sudo apt install gdm3              # Ubuntu
sudo dnf install gdm               # Fedora
```

### GDM + NVIDIA + Wayland

```bash
# GDM по умолчанию отключает Wayland для NVIDIA
# Чтобы включить:
sudo nano /etc/gdm/custom.conf
```

```ini
[daemon]
WaylandEnable=true
```

```bash
# Убедиться что nvidia-drm.modeset=1 в параметрах ядра
cat /proc/cmdline | grep nvidia

# Добавить если нет:
sudo nano /etc/default/grub
# GRUB_CMDLINE_LINUX="nvidia-drm.modeset=1"
sudo grub-mkconfig -o /boot/grub/grub.cfg
sudo reboot
```

## Проблемы LightDM

```bash
# Статус
sudo systemctl status lightdm
journalctl -u lightdm -b

# Конфигурация
cat /etc/lightdm/lightdm.conf

# Проверить greeter
ls /usr/share/xgreeters/
# lightdm-gtk-greeter, lightdm-slick-greeter и т.д.

# Сменить greeter
sudo nano /etc/lightdm/lightdm.conf
```

```ini
[Seat:*]
greeter-session=lightdm-gtk-greeter
```

## Пароль не принимается

### Capslock / раскладка

```bash
# Проверить раскладку на экране входа
# Обычно можно переключить по индикатору
# В SDDM: Layout в углу экрана

# Проверить из TTY
localectl status
# Если раскладка неожиданная:
sudo localectl set-x11-keymap us,ru "" "" grp:alt_shift_toggle
```

### Пароль истёк

```bash
# Проверить
sudo chage -l username

# Если "Password expires: ..." прошёл:
sudo chage -M -1 username   # Убрать срок действия
# Или установить новый пароль:
sudo passwd username
```

### Заблокирован faillock

```bash
# Слишком много неудачных попыток → блокировка
sudo faillock --user username
# Если locked:
sudo faillock --user username --reset
```

### /etc/nologin существует

```bash
# Этот файл запрещает вход для обычных пользователей
sudo rm /etc/nologin
```

## Рабочий стол KDE Plasma не загружается

```bash
# Из TTY:
# Проверить логи Plasma
journalctl --user -u plasma-plasmashell -b
cat ~/.local/share/sddm/xorg-session.log 2>/dev/null

# Сбросить конфигурацию Plasma (осторожно — сбросит настройки)
mv ~/.config/plasma-org.kde.plasma.desktop-appletsrc{,.bak}
mv ~/.config/plasmashellrc{,.bak}
mv ~/.config/kwinrc{,.bak}

# Или полный сброс (последнее средство):
rm -rf ~/.config/plasma*
rm -rf ~/.config/kwin*
rm -rf ~/.local/share/kscreen/

# Перезайти
```

## GNOME не загружается

```bash
# Логи
journalctl --user -u gnome-shell -b

# Сбросить расширения (частая причина)
gsettings set org.gnome.shell enabled-extensions "[]"

# Или из TTY / безопасной сессии:
dconf reset -f /org/gnome/shell/extensions/

# Сбросить конфигурацию GNOME полностью:
dconf reset -f /org/gnome/

# Пересоздать кэш иконок/шрифтов
gtk-update-icon-cache -f -t /usr/share/icons/hicolor/
fc-cache -fv
```

## Xorg проблемы при входе

```bash
# Логи X11
cat /var/log/Xorg.0.log | grep -i "EE\|WW" | head -30
cat ~/.local/share/xorg/Xorg.0.log | grep -i "EE\|WW" 2>/dev/null

# Типичные ошибки:
# (EE) Failed to load module "nvidia" → нет драйвера NVIDIA
# (EE) No screens found → GPU не определён

# Временное решение — запуск без X-конфига
sudo mv /etc/X11/xorg.conf /etc/X11/xorg.conf.bak
sudo systemctl restart sddm
```

## Wayland проблемы при входе

```bash
# Проверить поддержку
echo $XDG_SESSION_TYPE   # wayland или x11

# Логи Wayland (зависит от композитора)
# KWin:
journalctl --user -u plasma-kwin_wayland -b

# Переключиться на X11 (если Wayland не работает)
# В файле сессии DM выбрать "Plasma (X11)" вместо "Plasma (Wayland)"

# Принудительно X11 для SDDM:
# /etc/sddm.conf.d/10-x11.conf
```

```ini
[General]
DisplayServer=x11
```

## Автологин (настройка)

### SDDM
```ini
# /etc/sddm.conf.d/autologin.conf
[Autologin]
User=username
Session=plasma
# Session=gnome / xfce / i3 и т.д.
```

### GDM
```ini
# /etc/gdm/custom.conf
[daemon]
AutomaticLoginEnable=true
AutomaticLogin=username
```

### LightDM
```ini
# /etc/lightdm/lightdm.conf
[Seat:*]
autologin-user=username
autologin-session=plasma
```

## Алгоритм действий при проблемах со входом

```
1. Ctrl+Alt+F2 → войти в TTY
2. journalctl -b --priority=err        → ошибки загрузки
3. df -h                               → место на диске
4. ls -la /home/username               → права каталога
5. systemctl status sddm/gdm/lightdm  → DM работает?
6. rm ~/.Xauthority ~/.ICEauthority    → сброс авторизации
7. Попробовать другую сессию (X11/Wayland)
8. Проверить .bashrc / .profile        → ошибки в скриптах
9. Проверить GPU-драйвер               → nomodeset в GRUB
10. Переустановить DM и DE
```
