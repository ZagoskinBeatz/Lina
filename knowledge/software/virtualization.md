# Виртуализация — KVM/QEMU, VirtualBox, Docker

## KVM/QEMU (рекомендуется)

### Проверка поддержки

```bash
# Проверить виртуализацию в CPU
egrep -c '(vmx|svm)' /proc/cpuinfo
# > 0 = поддерживается

# Проверить модули
lsmod | grep kvm
# kvm_intel или kvm_amd
```

### Установка

```bash
# Arch / CachyOS
sudo pacman -S qemu-full virt-manager dnsmasq bridge-utils libvirt

# Ubuntu
sudo apt install qemu-kvm libvirt-daemon-system virt-manager bridge-utils

# Fedora
sudo dnf install @virtualization
```

### Настройка

```bash
# Добавить пользователя в группу libvirt
sudo usermod -aG libvirt $USER

# Запустить сервис
sudo systemctl enable --now libvirtd

# Открыть virt-manager (GUI)
virt-manager
```

### Командная строка (virsh)

```bash
# Список VM
virsh list --all

# Запуск / остановка
virsh start <vm>
virsh shutdown <vm>
virsh destroy <vm>          # принудительное выключение

# Создание снимка
virsh snapshot-create-as <vm> --name "до_обновления"
virsh snapshot-revert <vm> "до_обновления"
```

### Проброс GPU (GPU Passthrough)

```bash
# 1. Включить IOMMU
# В /etc/default/grub:
GRUB_CMDLINE_LINUX="intel_iommu=on iommu=pt"  # Intel
GRUB_CMDLINE_LINUX="amd_iommu=on iommu=pt"    # AMD

# 2. Обновить GRUB
sudo grub-mkconfig -o /boot/grub/grub.cfg

# 3. Привязать GPU к vfio-pci
# В /etc/modprobe.d/vfio.conf:
options vfio-pci ids=10de:xxxx,10de:yyyy

# 4. Обновить initramfs
sudo mkinitcpio -P
```

## VirtualBox

### Установка VirtualBox

```bash
# Arch
sudo pacman -S virtualbox virtualbox-host-modules-arch
sudo modprobe vboxdrv
sudo usermod -aG vboxusers $USER

# Ubuntu
sudo apt install virtualbox virtualbox-ext-pack
```

## Docker

### Установка Docker

```bash
# Arch
sudo pacman -S docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker $USER

# Ubuntu
sudo apt install docker.io docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

### Основные команды

```bash
# Запуск контейнера
docker run -it ubuntu bash
docker run -d -p 8080:80 nginx

# Управление
docker ps                   # запущенные
docker ps -a                # все
docker stop <id>
docker rm <id>
docker images               # образы
docker rmi <image>          # удалить образ

# Docker Compose
docker compose up -d        # запустить сервисы
docker compose down         # остановить
docker compose logs -f      # логи
```

## Podman (rootless Docker)

```bash
# Arch
sudo pacman -S podman

# Совместим с Docker CLI
podman run -it ubuntu bash
podman ps
podman compose up -d
```
