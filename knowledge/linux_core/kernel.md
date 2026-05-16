# Ядро Linux — управление и настройка

## Обзор
Ядро Linux — сердце операционной системы. Управляет оборудованием, процессами,
памятью, файловыми системами, сетью и безопасностью.

## Информация о ядре
```bash
# Текущая версия
uname -r                          # 6.7.5-cachyos
uname -a                          # полная информация

# Подробная информация
cat /proc/version
hostnamectl | grep Kernel

# Параметры ядра при загрузке
cat /proc/cmdline

# Конфигурация ядра
zcat /proc/config.gz              # если включено
cat /boot/config-$(uname -r)      # на диске
```

## Установка ядра

### Arch Linux / CachyOS
```bash
# Стандартные ядра
sudo pacman -S linux linux-headers              # стандартное
sudo pacman -S linux-lts linux-lts-headers      # LTS
sudo pacman -S linux-zen linux-zen-headers      # для десктопа
sudo pacman -S linux-hardened linux-hardened-headers  # безопасность

# CachyOS ядра
sudo pacman -S linux-cachyos linux-cachyos-headers
# BORE scheduler, оптимизации для десктопа

# После установки нового ядра:
sudo grub-mkconfig -o /boot/grub/grub.cfg
# или для systemd-boot:
# Entries обновляются автоматически
```

### Debian / Ubuntu
```bash
# Список доступных ядер
apt search linux-image | grep -E "^linux-image-[0-9]"

# Установить
sudo apt install linux-image-6.8.0-45-generic linux-headers-6.8.0-45-generic

# Список установленных ядер
dpkg --list | grep linux-image

# Удалить старое ядро
sudo apt remove linux-image-6.5.0-old
sudo apt autoremove
```

### Fedora
```bash
# Установить
sudo dnf install kernel kernel-devel

# Список установленных
rpm -qa | grep kernel

# Fedora хранит 3 последних ядра по умолчанию
# /etc/dnf/dnf.conf
installonly_limit=3
```

## Модули ядра
```bash
# Список загруженных модулей
lsmod
lsmod | grep nvidia

# Информация о модуле
modinfo nvidia
modinfo snd-hda-intel

# Загрузить модуль
sudo modprobe <module>
sudo modprobe nvidia
sudo modprobe -v btusb              # verbose

# Выгрузить модуль
sudo modprobe -r <module>
sudo rmmod <module>

# Параметры модуля
sudo modprobe snd-hda-intel model=generic
# Или постоянно: /etc/modprobe.d/
echo "options snd-hda-intel model=generic" | sudo tee /etc/modprobe.d/alsa.conf

# Чёрный список модулей
echo "blacklist nouveau" | sudo tee /etc/modprobe.d/blacklist-nouveau.conf
# Применить:
sudo mkinitcpio -P                  # Arch
sudo update-initramfs -u            # Debian
```

## DKMS — Dynamic Kernel Module Support
```bash
# DKMS автоматически пересобирает модули при обновлении ядра

# Статус
dkms status

# Добавить модуль
sudo dkms add -m <module> -v <version>
sudo dkms build -m <module> -v <version>
sudo dkms install -m <module> -v <version>

# Автоматическая сборка:
# DKMS-пакеты (nvidia-dkms, virtualbox-host-dkms)
# автоматически пересобираются при установке нового ядра
```

## Sysctl — параметры ядра в runtime
```bash
# Просмотр всех параметров
sysctl -a
sysctl -a | wc -l                   # ~1500+ параметров

# Просмотр конкретного
sysctl net.ipv4.ip_forward
cat /proc/sys/net/ipv4/ip_forward

# Установить временно
sudo sysctl net.ipv4.ip_forward=1

# Установить постоянно
echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-custom.conf
sudo sysctl --system                # применить все файлы

# Полезные параметры:
# Память
vm.swappiness=10                    # агрессивность swap
vm.vfs_cache_pressure=50            # кэш dentry
vm.dirty_ratio=10                   # % RAM для dirty pages
vm.dirty_background_ratio=5         # фоновая запись

# Сеть
net.core.somaxconn=65535            # очередь соединений
net.ipv4.tcp_fastopen=3             # TCP Fast Open
net.ipv4.tcp_congestion_control=bbr # BBR congestion control

# Безопасность
kernel.randomize_va_space=2         # ASLR
kernel.kptr_restrict=2              # скрыть адреса ядра
kernel.dmesg_restrict=1             # dmesg требует root
kernel.kexec_load_disabled=1        # запрет kexec
```

## Initramfs
```bash
# Initramfs — начальный RAM-диск для ранней загрузки

# Пересборка (Arch / CachyOS):
sudo mkinitcpio -P                  # все ядра
sudo mkinitcpio -p linux            # конкретное ядро

# Пересборка (Debian / Ubuntu):
sudo update-initramfs -u            # текущее ядро
sudo update-initramfs -u -k all     # все ядра

# Пересборка (Fedora):
sudo dracut --force

# Конфигурация (Arch): /etc/mkinitcpio.conf
MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)
HOOKS=(base udev autodetect modconf kms keyboard keymap consolefont block filesystems fsck)

# Добавление модуля для раннего старта:
# Добавьте в MODULES=()
# Например для NVIDIA: MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)
```

## Компиляция своего ядра
```bash
# 1. Скачать исходники
wget https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.8.tar.xz
tar xf linux-6.8.tar.xz
cd linux-6.8

# 2. Конфигурация
make menuconfig                     # TUI конфигуратор
# Или скопировать текущую конфигурацию:
zcat /proc/config.gz > .config
make olddefconfig                   # обновить под новое ядро

# 3. Компиляция
make -j$(nproc)                     # компиляция ядра
make modules                        # модули

# 4. Установка
sudo make modules_install           # модули в /lib/modules/
sudo make install                   # ядро в /boot/

# 5. Обновить загрузчик
sudo grub-mkconfig -o /boot/grub/grub.cfg
```

## Диагностика ядра
```bash
# Системные сообщения ядра
dmesg
dmesg -T                           # с human-readable временем
dmesg -l err                       # только ошибки
dmesg -w                           # follow (мониторинг)
dmesg | grep -i "error\|warn\|fail"

# Информация о оборудовании
lspci -v                           # PCI-устройства
lsusb -v                           # USB-устройства
lscpu                              # CPU
lsmem                              # память
lsblk                              # блочные устройства

# Прерывания
cat /proc/interrupts

# Загрузка CPU
cat /proc/loadavg
```

## Kernel panic и отладка
```bash
# Сохранение kernel panic логов:
# /etc/sysctl.d/99-panic.conf
kernel.panic = 10                   # перезагрузка через 10 секунд

# Kdump — сохранение дампа памяти при panic
sudo pacman -S kexec-tools
# Настройка: /etc/default/grub
# GRUB_CMDLINE_LINUX="crashkernel=256M"

# Magic SysRq (аварийные команды):
# Alt+Print+R → E → I → S → U → B (REISUB)
# R — вернуть клавиатуру из raw mode
# E — SIGTERM всем процессам
# I — SIGKILL всем процессам
# S — синхронизировать файловые системы
# U — перемонтировать read-only
# B — перезагрузка

# Включить SysRq
echo 1 | sudo tee /proc/sys/kernel/sysrq
```

## Частые проблемы
1. **Модуль не загружается** — `dmesg | tail`, проверить `modinfo <module>`
2. **Kernel panic at boot** — загрузиться с прошлым ядром, проверить initramfs
3. **Нет поддержки оборудования** — установить правильные headers и DKMS-модуль
4. **Ядра занимают много места** — удалить старые: `sudo pacman -R linux-old`
5. **Ошибка после обновления** — загрузить LTS ядро, откатить через `pacman -U`
