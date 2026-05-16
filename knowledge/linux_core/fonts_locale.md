# Шрифты, локализация и интернационализация

## Локаль (Locale)

### Проверка и настройка
```bash
# Текущая локаль
locale
locale -a                           # доступные локали

# Установить локаль (Arch)
sudo nano /etc/locale.gen
# Раскомментировать:
# en_US.UTF-8 UTF-8
# ru_RU.UTF-8 UTF-8
sudo locale-gen

# Установить системную локаль
sudo localectl set-locale LANG=ru_RU.UTF-8
# Или /etc/locale.conf:
# LANG=ru_RU.UTF-8
# LC_TIME=ru_RU.UTF-8
# LC_NUMERIC=en_US.UTF-8

# Ubuntu
sudo dpkg-reconfigure locales

# Переменные локали
# LANG          — основная (fallback)
# LC_MESSAGES   — язык сообщений
# LC_TIME       — формат даты/времени
# LC_NUMERIC    — формат чисел
# LC_MONETARY   — формат валюты
# LC_COLLATE    — сортировка
# LC_CTYPE      — классификация символов
# LC_ALL        — переопределяет всё (не рекомендуется в конфиге)
```

### Раскладка клавиатуры
```bash
# Системная (console)
sudo localectl set-keymap us
sudo localectl set-x11-keymap us,ru "" "" grp:alt_shift_toggle

# Проверить
localectl status

# X11
setxkbmap -layout us,ru -option grp:alt_shift_toggle

# Wayland (KDE Plasma)
# Системные параметры → Клавиатура → Раскладки

# fcitx5 / ibus — ввод CJK (Китайский, Японский, Корейский)
sudo pacman -S fcitx5 fcitx5-gtk fcitx5-qt fcitx5-configtool
# Переменные:
# GTK_IM_MODULE=fcitx
# QT_IM_MODULE=fcitx
# XMODIFIERS=@im=fcitx
```

## Шрифты

### Установка шрифтов
```bash
# Системные шрифты (для всех пользователей)
sudo cp font.ttf /usr/share/fonts/TTF/
sudo fc-cache -fv

# Пользовательские шрифты
mkdir -p ~/.local/share/fonts
cp font.ttf ~/.local/share/fonts/
fc-cache -fv

# Из пакетов (Arch)
sudo pacman -S noto-fonts noto-fonts-cjk noto-fonts-emoji  # Google Noto
sudo pacman -S ttf-liberation                               # Liberation (метрики MS)
sudo pacman -S ttf-dejavu                                   # DejaVu
sudo pacman -S ttf-fira-code                                # Fira Code (ligatures)
sudo pacman -S ttf-jetbrains-mono                           # JetBrains Mono
sudo pacman -S ttf-cascadia-code                            # Cascadia Code
sudo pacman -S inter-font                                   # Inter (UI)

# AUR — Microsoft шрифты
yay -S ttf-ms-win11-auto                                    # Windows шрифты

# Nerd Fonts (для терминала, с иконками)
yay -S ttf-nerd-fonts-symbols
yay -S ttf-jetbrains-mono-nerd
yay -S ttf-firacode-nerd
```

### Управление шрифтами
```bash
# Список установленных
fc-list
fc-list | grep -i "mono"           # моноширинные
fc-list :lang=ru                   # с поддержкой русского

# Информация о шрифте
fc-match monospace                 # какой шрифт используется для monospace
fc-match sans-serif
fc-match serif

# Обновить кэш
fc-cache -fv
```

### Настройка шрифтов (fontconfig)
```xml
<!-- ~/.config/fontconfig/fonts.conf -->
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <!-- Антиалиасинг -->
  <match target="font">
    <edit name="antialias" mode="assign"><bool>true</bool></edit>
    <edit name="hinting" mode="assign"><bool>true</bool></edit>
    <edit name="hintstyle" mode="assign"><const>hintslight</const></edit>
    <edit name="rgba" mode="assign"><const>rgb</const></edit>
    <edit name="lcdfilter" mode="assign"><const>lcddefault</const></edit>
  </match>

  <!-- Предпочтительные шрифты -->
  <alias>
    <family>sans-serif</family>
    <prefer><family>Inter</family></prefer>
  </alias>
  <alias>
    <family>serif</family>
    <prefer><family>Noto Serif</family></prefer>
  </alias>
  <alias>
    <family>monospace</family>
    <prefer><family>JetBrains Mono</family></prefer>
  </alias>

  <!-- Emoji -->
  <alias>
    <family>emoji</family>
    <prefer><family>Noto Color Emoji</family></prefer>
  </alias>
</fontconfig>
```

### Шрифты в Flatpak
```bash
# Flatpak приложения видят только шрифты из стандартных путей
# Системные: /usr/share/fonts
# Пользовательские: ~/.local/share/fonts
# Если шрифт не виден:
flatpak override --user --filesystem=~/.local/share/fonts
```

## Часовой пояс и время

### Настройка
```bash
# Текущее время
timedatectl

# Установить часовой пояс
sudo timedatectl set-timezone Europe/Moscow
timedatectl list-timezones | grep Moscow

# NTP синхронизация
sudo timedatectl set-ntp true

# Ручная установка времени (если NTP не работает)
sudo timedatectl set-time "2026-03-05 12:00:00"

# Показать все часовые пояса
timedatectl list-timezones

# Проблема с Dual-boot Windows:
# Windows использует localtime, Linux — UTC
# Вариант 1: Linux использует localtime (не рекомендуется)
sudo timedatectl set-local-rtc 1
# Вариант 2: Windows использует UTC (рекомендуется)
# В Windows: reg add HKLM\SYSTEM\CurrentControlSet\Control\TimeZoneInformation /v RealTimeIsUniversal /t REG_DWORD /d 1
```

## Кодировки

### Работа с кодировками
```bash
# Определить кодировку файла
file -i document.txt
chardet document.txt                # pip install chardet

# Конвертация кодировок
iconv -f cp1251 -t utf-8 input.txt > output.txt
iconv -f windows-1251 -t utf-8 input.txt -o output.txt

# Рекурсивно конвертировать все файлы
find . -name "*.txt" -exec bash -c 'iconv -f cp1251 -t utf-8 "$1" > "$1.tmp" && mv "$1.tmp" "$1"' _ {} \;

# Перевод строк Windows → Linux
dos2unix file.txt
sed -i 's/\r$//' file.txt

# Linux → Windows
unix2dos file.txt
```

### UTF-8 проблемы
```bash
# Проверить что терминал поддерживает UTF-8
echo $LANG                          # должно быть *.UTF-8
echo "Привет мир 🐧"               # тест кириллицы и emoji

# Файл с BOM (Byte Order Mark)
file document.txt                   # упомянет "BOM" если есть
# Удалить BOM:
sed -i '1s/^\xEF\xBB\xBF//' document.txt
```

## Интернационализация приложений

### GTK / Qt
```bash
# GTK — переменные
export LANGUAGE=ru_RU:ru:en
export LANG=ru_RU.UTF-8

# Qt
export QT_QPA_PLATFORMTHEME=kde    # использовать KDE тему
export QT_STYLE_OVERRIDE=Breeze

# Пакеты локализации
sudo pacman -S kde-l10n-ru          # KDE на русском
sudo pacman -S firefox-i18n-ru      # Firefox на русском
sudo pacman -S libreoffice-fresh-ru # LibreOffice на русском
```

### Словари и проверка правописания
```bash
# hunspell — проверка орфографии
sudo pacman -S hunspell hunspell-ru hunspell-en_us

# aspell
sudo pacman -S aspell aspell-ru aspell-en

# Проверка
echo "Привет мер" | hunspell -d ru_RU
```
