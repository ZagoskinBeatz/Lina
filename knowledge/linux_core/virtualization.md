# Виртуализация в Linux

## KVM / QEMU / libvirt

### Проверка поддержки
```bash
# Проверка аппаратной виртуализации
egrep -c '(vmx|svm)' /proc/cpuinfo    # >0 = поддерживается
lsmod | grep kvm                       # kvm_intel или kvm_amd

# Если модули не загружены
sudo modprobe kvm_intel   # Intel
sudo modprobe kvm_amd     # AMD
```

### Установка
```bash
# Arch / CachyOS
sudo pacman -S qemu-full virt-manager libvirt dnsmasq edk2-ovmf
sudo systemctl enable --now libvirtd
sudo usermod -aG libvirt $USER

# Ubuntu/Debian
sudo apt install qemu-kvm libvirt-daemon-system virt-manager
sudo adduser $USER libvirt

# Fedora
sudo dnf install @virtualization
sudo systemctl enable --now libvirtd
```

### Основные команды
```bash
# Через virsh (CLI)
virsh list --all              # все VM
virsh start <vm>              # запуск
virsh shutdown <vm>           # выключение
virsh destroy <vm>            # принудительное выключение
virsh snapshot-create-as <vm> <name>  # создание снимка
virsh snapshot-revert <vm> <name>     # откат к снимку

# Через QEMU напрямую
qemu-system-x86_64 -enable-kvm -m 4G -cpu host -smp 4 \
  -drive file=disk.qcow2,format=qcow2 \
  -cdrom install.iso -boot d
```

### virt-manager
Графический менеджер виртуальных машин:
```bash
virt-manager   # запуск GUI
```

## Docker / Podman

### Docker
```bash
# Установка
sudo pacman -S docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker $USER

# Основные команды
docker pull <образ>
docker run -it <образ> bash
docker ps                    # запущенные контейнеры
docker ps -a                 # все контейнеры
docker stop <id>
docker rm <id>
docker images                # образы
docker rmi <образ>           # удалить образ
docker-compose up -d         # запуск из docker-compose.yml
```

### Podman (rootless альтернатива Docker)
```bash
# Установка
sudo pacman -S podman podman-compose

# Те же команды что и Docker
podman pull <образ>
podman run -it <образ> bash
podman-compose up -d
```

## VirtualBox
```bash
# Установка
sudo pacman -S virtualbox virtualbox-host-modules-arch
# Для других ядер:
sudo pacman -S virtualbox-host-dkms

sudo modprobe vboxdrv
sudo usermod -aG vboxusers $USER
```

## Советы по оптимизации VM
1. Включите VirtIO для дисков и сети (значительно быстрее)
2. Используйте формат qcow2 с предварительным выделением для производительности
3. Передайте CPU host для максимальной совместимости
4. Для GPU passthrough: IOMMU (VT-d/AMD-Vi) + VFIO

## GPU Passthrough (VFIO)
```bash
# Требования:
# - Два GPU (встроенный + дискретный)
# - IOMMU (VT-d для Intel, AMD-Vi для AMD)
# - Второй GPU для гостевой системы

# 1. Включить IOMMU в GRUB
# /etc/default/grub
GRUB_CMDLINE_LINUX="intel_iommu=on iommu=pt"    # Intel
GRUB_CMDLINE_LINUX="amd_iommu=on iommu=pt"      # AMD
sudo grub-mkconfig -o /boot/grub/grub.cfg

# 2. Проверить группы IOMMU
for d in /sys/kernel/iommu_groups/*/devices/*; do
    n=$(basename $d)
    echo "Group $(basename $(dirname $(dirname $d))): $n $(lspci -nns $n)"
done

# 3. Привязать GPU к vfio-pci
# /etc/modprobe.d/vfio.conf
options vfio-pci ids=10de:xxxx,10de:yyyy   # ID GPU и аудио

# /etc/mkinitcpio.conf
MODULES=(vfio_pci vfio vfio_iommu_type1)

sudo mkinitcpio -P

# 4. Создать VM с GPU
virt-install --name=win11 \
  --ram=16384 --vcpus=8 \
  --disk path=/var/lib/libvirt/images/win11.qcow2,size=100 \
  --cdrom=/path/to/win11.iso \
  --os-variant=win11 \
  --hostdev=<pci_address>

# 5. Looking Glass — зеркало гостевого дисплея на хосте
# https://looking-glass.io/ (почти нулевая задержка)
```

## Docker подробно
```bash
# Установка
sudo pacman -S docker docker-compose       # Arch
sudo apt install docker.io docker-compose   # Debian

sudo systemctl enable --now docker
sudo usermod -aG docker $USER               # без sudo (re-login)

# Основные команды
docker run -it ubuntu bash                   # запустить контейнер
docker run -d -p 8080:80 nginx              # фоновый режим + маппинг порта
docker ps                                    # список запущенных
docker ps -a                                 # все контейнеры
docker stop <id>                             # остановить
docker rm <id>                               # удалить контейнер
docker images                                # список образов
docker rmi <image>                           # удалить образ
docker logs <id>                             # логи
docker exec -it <id> bash                    # зайти в контейнер

# Docker Compose
docker compose up -d                         # запустить compose-стек
docker compose down                          # остановить
docker compose logs -f                       # логи
docker compose ps                            # статус

# Очистка ресурсов
docker system prune                          # неиспользуемые ресурсы
docker system prune -a --volumes             # всё неиспользуемое + volumes
```

### Dockerfile пример
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "app.py"]
```

## Podman (rootless Docker)
```bash
# Podman — drop-in замена Docker без демона
sudo pacman -S podman podman-compose

# Те же команды, что и Docker
podman run -it ubuntu bash
podman-compose up -d

# Преимущества:
# - Rootless по умолчанию (безопаснее)
# - Daemonless (нет фонового процесса)
# - Совместим с Docker CLI и Dockerfile
# - Поддержка pods (группы контейнеров)

# Pods (как Kubernetes pods)
podman pod create --name mypod -p 8080:80
podman run -d --pod mypod nginx
podman run -d --pod mypod redis
podman pod ps
```

## LXC/LXD — системные контейнеры
```bash
# LXD — менеджер системных контейнеров
sudo pacman -S lxd
sudo lxd init                                # инициализация

lxc launch ubuntu:22.04 mycontainer          # создать контейнер
lxc exec mycontainer -- bash                 # зайти
lxc list                                     # список
lxc stop mycontainer                         # остановить
lxc delete mycontainer                       # удалить

# Преимущество: полная ОС внутри контейнера (systemd, apt и т.д.)
```

## Vagrant — управление VM для разработки
```bash
# Установка
sudo pacman -S vagrant

# Быстрый старт
vagrant init hashicorp/bionic64
vagrant up                                   # создать и запустить VM
vagrant ssh                                  # подключиться
vagrant halt                                 # выключить
vagrant destroy                              # удалить

# Vagrantfile
Vagrant.configure("2") do |config|
  config.vm.box = "archlinux/archlinux"
  config.vm.network "forwarded_port", guest: 80, host: 8080
  config.vm.provider "libvirt" do |lv|
    lv.memory = 4096
    lv.cpus = 4
  end
end
```

## distrobox — дистрибутивы внутри контейнеров
```bash
# Запуск любого дистрибутива на любом хосте
sudo pacman -S distrobox

# Создать Ubuntu-контейнер на Arch
distrobox create --name ubuntu --image ubuntu:24.04
distrobox enter ubuntu                       # зайти
# Внутри: apt install, запуск GUI-приложений (интеграция с хостом!)

# Экспорт приложения в хост
distrobox-export --app firefox               # .desktop ярлык
distrobox-export --bin /usr/bin/code --export-path ~/.local/bin/
```
