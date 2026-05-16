# Установка Lina

## Arch Linux / Manjaro / CachyOS

### Из AUR
```bash
yay -S lina
# или
paru -S lina
```

### Из исходников
```bash
git clone https://github.com/lina-linux/lina.git
cd lina
makepkg -si
```

## Ubuntu / Debian / Linux Mint

### Из .deb пакета
```bash
wget https://github.com/lina-linux/lina/releases/latest/download/lina.deb
sudo dpkg -i lina.deb
sudo apt install -f  # установить зависимости
```

### Из исходников
```bash
git clone https://github.com/lina-linux/lina.git
cd lina
sudo apt install python3 python3-pip python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Fedora / RHEL / CentOS

### Из .rpm пакета
```bash
wget https://github.com/lina-linux/lina/releases/latest/download/lina.rpm
sudo dnf install lina.rpm
```

### Из исходников
```bash
git clone https://github.com/lina-linux/lina.git
cd lina
sudo dnf install python3 python3-pip python3-devel
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## openSUSE

```bash
git clone https://github.com/lina-linux/lina.git
cd lina
sudo zypper install python3 python3-pip python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Универсальная установка (pip)

```bash
pip install lina-linux
```

## Первый запуск

```bash
lina --first-run
```

Мастер проведёт вас через:
1. Выбор модели (Small 3B / Medium 7B / Large 13B)
2. Скачивание модели
3. Индексацию базы знаний
4. Настройку языка и интерфейса

## Systemd (автозапуск)

```bash
# Пользовательский сервис
systemctl --user enable lina
systemctl --user start lina

# Проверка статуса
systemctl --user status lina
```

## Опциональные зависимости

| Пакет | Назначение |
|-------|-----------|
| python-pyqt6 | GUI интерфейс |
| espeak-ng | Text-to-Speech |
| piper-tts | Высококачественный TTS |
| whisper.cpp | Speech-to-Text |

## Обновление

```bash
# Arch Linux
yay -Syu lina

# Ubuntu/Debian
sudo apt update && sudo apt upgrade lina

# Fedora
sudo dnf upgrade lina

# Из исходников
cd lina && git pull && pip install -e .
```

## Удаление

```bash
# Arch Linux
sudo pacman -Rns lina

# Ubuntu/Debian
sudo apt remove lina

# Fedora
sudo dnf remove lina

# pip
pip uninstall lina-linux
```
