# Контейнеры и контейнеризация

## Docker

### Основные команды
```bash
# Образы
docker pull ubuntu:24.04
docker images
docker image ls
docker image rm ubuntu:24.04
docker image prune                   # удалить неиспользуемые

# Контейнеры
docker run -it ubuntu:24.04 bash     # интерактивный
docker run -d --name web -p 8080:80 nginx  # фоновый с именем
docker ps                            # работающие
docker ps -a                         # все
docker stop web
docker start web
docker restart web
docker rm web
docker rm -f web                     # принудительно

# Логи и мониторинг
docker logs web
docker logs -f web                   # follow
docker logs --tail 100 web
docker stats                         # ресурсы
docker top web                       # процессы в контейнере
docker inspect web                   # полная информация

# Вход в контейнер
docker exec -it web bash
docker exec -it web sh               # если нет bash
docker attach web                    # к главному процессу

# Копирование файлов
docker cp file.txt web:/path/
docker cp web:/path/file.txt .
```

### Dockerfile
```dockerfile
# Многоэтапная сборка (Multi-stage)
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /root/.local/lib /root/.local/lib
COPY --from=builder /root/.local/bin /root/.local/bin
ENV PATH=/root/.local/bin:$PATH
COPY . .
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD curl -f http://localhost:8000/health || exit 1
CMD ["python", "app.py"]
```

### .dockerignore
```
.git
.venv
__pycache__
*.pyc
node_modules
.env
docker-compose*.yml
Dockerfile
README.md
```

### Docker Compose
```yaml
# docker-compose.yml
services:
  web:
    build: .
    ports:
      - "8080:8000"
    volumes:
      - ./app:/app
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/mydb
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  db:
    image: postgres:16-alpine
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass
      POSTGRES_DB: mydb
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U user -d mydb"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  pgdata:
  redis_data:

networks:
  default:
    driver: bridge
```

```bash
# Управление compose
docker compose up -d                 # запуск
docker compose down                  # остановка
docker compose down -v               # + удалить тома
docker compose logs -f web           # логи сервиса
docker compose exec web bash         # вход в сервис
docker compose pull                  # обновить образы
docker compose build --no-cache      # пересобрать
```

### Сети Docker
```bash
docker network ls
docker network create mynet
docker network connect mynet web
docker network disconnect mynet web
docker network inspect mynet

# Типы сетей
# bridge — по умолчанию, изолированная
# host — разделяет сеть хоста
# none — без сети
# overlay — для Docker Swarm
# macvlan — отдельный MAC-адрес
```

### Volumes — постоянное хранилище
```bash
docker volume ls
docker volume create mydata
docker volume inspect mydata
docker volume rm mydata
docker volume prune                  # удалить неиспользуемые

# Использование
docker run -v mydata:/data nginx     # именованный том
docker run -v /host/path:/container/path nginx  # bind mount
docker run -v /host/path:/container/path:ro nginx  # read-only
docker run --tmpfs /tmp nginx        # tmpfs (в памяти)
```

### Оптимизация Docker
```bash
# Очистка
docker system prune -af              # всё неиспользуемое
docker system df                     # использование диска

# Ограничение ресурсов
docker run -m 512m --cpus=1.5 --name limited nginx

# Кэширование слоёв
# В Dockerfile располагать редко меняющиеся команды выше
# COPY requirements.txt . + RUN pip install ← перед COPY . .
```

## Podman — rootless альтернатива Docker

### Основное
```bash
# Совместим с Docker CLI
podman pull nginx
podman run -d --name web -p 8080:80 nginx
podman ps
podman stop web
podman rm web

# Rootless — без root
podman run --rootless nginx

# Pods (группы контейнеров)
podman pod create --name mypod -p 8080:80
podman run -d --pod mypod nginx
podman run -d --pod mypod redis
podman pod list
podman pod stop mypod
```

### Podman Compose
```bash
pip install podman-compose
podman-compose up -d
podman-compose down
```

### Systemd-интеграция
```bash
# Автогенерация unit-файла
podman generate systemd --new --name web > ~/.config/systemd/user/container-web.service
systemctl --user enable --now container-web.service
loginctl enable-linger $USER        # запуск без логина
```

## Distrobox — Linux дистрибутивы в контейнере
```bash
# Установка
sudo pacman -S distrobox

# Создание контейнеров
distrobox create --name ubuntu -i ubuntu:24.04
distrobox create --name fedora -i fedora:40
distrobox create --name arch -i archlinux

# Вход
distrobox enter ubuntu
distrobox enter fedora

# Экспорт приложений (появятся в меню)
distrobox enter ubuntu -- distrobox-export --app firefox
distrobox enter ubuntu -- distrobox-export --bin /usr/bin/code --export-path ~/.local/bin

# Список
distrobox list
distrobox stop ubuntu
distrobox rm ubuntu
```

## LXC / LXD — системные контейнеры
```bash
# Инициализация LXD
sudo lxd init

# Создание контейнера
lxc launch ubuntu:24.04 mycontainer
lxc launch images:archlinux myarch

# Управление
lxc list
lxc exec mycontainer -- bash
lxc stop mycontainer
lxc delete mycontainer
lxc info mycontainer

# Снапшоты
lxc snapshot mycontainer snap1
lxc restore mycontainer snap1
lxc copy mycontainer/snap1 newcontainer

# Ресурсы
lxc config set mycontainer limits.memory 2GB
lxc config set mycontainer limits.cpu 2

# Проброс устройств
lxc config device add mycontainer myport proxy listen=tcp:0.0.0.0:8080 connect=tcp:127.0.0.1:80
```

## Vagrant — автоматизация VM
```ruby
# Vagrantfile
Vagrant.configure("2") do |config|
  config.vm.box = "generic/ubuntu2404"
  config.vm.network "private_network", ip: "192.168.56.10"
  config.vm.network "forwarded_port", guest: 80, host: 8080
  config.vm.synced_folder "./data", "/vagrant_data"

  config.vm.provider "libvirt" do |v|
    v.memory = 2048
    v.cpus = 2
  end

  config.vm.provision "shell", inline: <<-SHELL
    apt-get update
    apt-get install -y nginx
  SHELL
end
```

```bash
vagrant up
vagrant ssh
vagrant halt
vagrant destroy
vagrant snapshot save clean
vagrant snapshot restore clean
```

## Сравнение технологий
| Технология | Изоляция | Rootless | Скорость | GUI | Use case |
|-----------|----------|---------|---------|------|----------|
| Docker | Процесс | Возможно | Мгновенно | Нет | Микросервисы |
| Podman | Процесс | Да | Мгновенно | Нет | Безопасные контейнеры |
| Distrobox | Процесс | Да | Мгновенно | Да | Другие дистрибутивы |
| LXC/LXD | Система | Нет | Секунды | Да | Полные ОС |
| Vagrant | VM | Нет | Минуты | Да | Воспроизводимые среды |
| Flatpak | Sandbox | Да | — | Да | Десктоп приложения |
