# Офисные приложения — LibreOffice, OnlyOffice

## LibreOffice

Бесплатный офисный пакет с совместимостью MS Office.

### Установка

```bash
# Arch / CachyOS
sudo pacman -S libreoffice-fresh libreoffice-fresh-ru

# Ubuntu
sudo apt install libreoffice libreoffice-l10n-ru

# Flatpak
flatpak install flathub org.libreoffice.LibreOffice
```

### Компоненты

| Программа | Аналог MS Office |
| ----------- | ----------------- |
| Writer | Word |
| Calc | Excel |
| Impress | PowerPoint |
| Draw | Visio (базовый) |
| Base | Access |
| Math | Equation Editor |

### Совместимость с MS Office

- Поддерживает .docx, .xlsx, .pptx
- Для сложных документов может быть расхождение в форматировании
- Для максимальной совместимости: сохранять в формате .ods/.odt

## OnlyOffice

Офисный пакет с лучшей совместимостью с MS Office.

```bash
# Arch (AUR)
yay -S onlyoffice-bin

# Flatpak
flatpak install flathub org.onlyoffice.desktopeditors

# Snap
sudo snap install onlyoffice-desktopeditors
```

## Дополнительные инструменты

### Okular (просмотр PDF, KDE)

```bash
sudo pacman -S okular
```

### Zathura (минималистичный PDF)

```bash
sudo pacman -S zathura zathura-pdf-mupdf
```

### Thunderbird (электронная почта)

```bash
sudo pacman -S thunderbird thunderbird-i18n-ru
```
