# 50 основных команд Linux

## Навигация и файлы

### pwd — текущий каталог
```bash
pwd
# /home/user/Documents
```

### ls — содержимое каталога
```bash
ls                    # Список файлов
ls -la                # Подробно + скрытые
ls -lh                # Размеры в человекочитаемом формате
ls -lt                # Сортировка по дате (новые первые)
ls -lS                # Сортировка по размеру
```

### cd — смена каталога
```bash
cd /path/to/dir       # Абсолютный путь
cd ..                 # На уровень вверх
cd ~                  # Домашний каталог
cd -                  # Предыдущий каталог
```

### mkdir — создать каталог
```bash
mkdir mydir
mkdir -p path/to/nested/dir   # Создать вложенные каталоги
```

### cp — копировать
```bash
cp file1 file2                # Копировать файл
cp -r dir1 dir2               # Копировать каталог рекурсивно
cp -a source/ dest/           # Сохранить права, символические ссылки
cp -i file1 file2             # Спрашивать перед перезаписью
```

### mv — переместить / переименовать
```bash
mv old_name new_name          # Переименовать
mv file /path/to/dest/        # Переместить
mv -i file dest/              # Спрашивать перед перезаписью
```

### rm — удалить
```bash
rm file                       # Удалить файл
rm -r dir                     # Удалить каталог рекурсивно
rm -rf dir                    # Без подтверждений (ОСТОРОЖНО!)
rm -i file                    # Спрашивать перед удалением
```

### touch — создать файл / обновить время
```bash
touch newfile                 # Создать пустой файл
touch -t 202401011200 file    # Установить дату модификации
```

### ln — ссылки
```bash
ln -s /path/to/target link_name   # Символическая ссылка
ln /path/to/target link_name      # Жёсткая ссылка
```

## Просмотр файлов

### cat — показать содержимое
```bash
cat file                      # Вывести файл
cat file1 file2 > merged      # Объединить файлы
```

### less / more — постраничный просмотр
```bash
less file                     # Просмотр (q — выход, / — поиск)
```

### head / tail — начало / конец файла
```bash
head -n 20 file               # Первые 20 строк
tail -n 20 file               # Последние 20 строк
tail -f /var/log/syslog       # Следить за обновлениями файла в реальном времени
```

### wc — подсчёт строк/слов/символов
```bash
wc -l file                    # Количество строк
wc -w file                    # Количество слов
wc -c file                    # Количество байт
```

## Поиск

### find — поиск файлов
```bash
find /path -name "*.txt"                # По имени
find / -name "*.log" -size +100M        # По размеру (>100MB)
find . -mtime -7                        # Изменённые за 7 дней
find . -type f -name "*.tmp" -delete    # Найти и удалить
find . -type d -empty                   # Пустые каталоги
find . -perm 777                        # По правам
```

### grep — поиск текста
```bash
grep "pattern" file                     # Поиск в файле
grep -r "pattern" /path/                # Рекурсивный поиск
grep -i "pattern" file                  # Без учёта регистра
grep -n "pattern" file                  # С номерами строк
grep -v "pattern" file                  # Инвертировать (не содержит)
grep -c "pattern" file                  # Количество совпадений
grep -l "pattern" *.py                  # Только имена файлов
grep -E "regex|pattern" file            # Расширенные регулярки
```

### which / whereis / type — расположение команды
```bash
which python3                 # Путь к исполняемому файлу
whereis python3               # Бинарник + man + исходники
type ls                       # Тип команды (alias, builtin, file)
```

## Права доступа

### chmod — изменить права
```bash
chmod 755 file                # rwxr-xr-x
chmod 644 file                # rw-r--r--
chmod +x script.sh            # Добавить выполнение
chmod -R 755 dir/             # Рекурсивно
chmod u+w,g-w file            # Символический формат
```

### chown — изменить владельца
```bash
sudo chown user:group file
sudo chown -R user:group dir/
```

## Дисковое пространство

### df — свободное место на разделах
```bash
df -h                         # Все разделы (человекочитаемо)
df -h /home                   # Конкретный раздел
```

### du — размер файлов/каталогов
```bash
du -sh dir/                   # Общий размер каталога
du -sh *                      # Размер каждого элемента
du -sh * | sort -rh | head    # Топ-10 по размеру
```

## Процессы

### ps — список процессов
```bash
ps aux                        # Все процессы
ps aux | grep firefox         # Найти процесс
ps -ef --forest               # Дерево процессов
```

### top / htop — мониторинг в реальном времени
```bash
top                           # Стандартный монитор
htop                          # Улучшенный (нужна установка)
```

### kill — завершить процесс
```bash
kill <PID>                    # Послать SIGTERM (мягко)
kill -9 <PID>                 # SIGKILL (принудительно)
killall firefox               # По имени
pkill -f "python script"      # По шаблону команды
```

## Сеть

### ip — сетевые интерфейсы и маршруты
```bash
ip addr                       # IP-адреса
ip route                      # Таблица маршрутизации
ip link                       # Интерфейсы (UP/DOWN)
```

### ping — проверка соединения
```bash
ping -c 4 google.com          # 4 пакета
ping -c 4 8.8.8.8             # Без DNS
```

### ss — сетевые соединения
```bash
ss -tulnp                     # Слушающие порты с процессами
ss -s                         # Статистика
```

### curl / wget — загрузка
```bash
curl -O https://example.com/file     # Скачать файл
curl -I https://example.com          # Только заголовки
wget https://example.com/file        # Скачать файл
wget -c https://example.com/file     # Продолжить загрузку
```

### ssh — удалённое подключение
```bash
ssh user@host                 # Подключиться
ssh -p 2222 user@host         # Нестандартный порт
ssh -i ~/.ssh/key user@host   # С ключом
scp file user@host:/path/     # Копировать файл на сервер
scp user@host:/path/file .    # Копировать с сервера
```

## Текстовая обработка

### sed — потоковый редактор
```bash
sed 's/old/new/g' file               # Заменить (вывод в stdout)
sed -i 's/old/new/g' file            # Заменить в файле
sed -n '10,20p' file                 # Строки 10-20
sed '/pattern/d' file                # Удалить строки с pattern
```

### awk — обработка полей
```bash
awk '{print $1}' file                # Первое поле
awk -F: '{print $1, $3}' /etc/passwd # Разделитель :, поля 1 и 3
awk '$3 > 1000' file                 # Фильтр по 3-му полю
```

### sort / uniq — сортировка и уникальность
```bash
sort file                            # Сортировать
sort -n file                         # Числовая сортировка
sort -r file                         # Обратная
sort file | uniq                     # Уникальные строки
sort file | uniq -c | sort -rn       # Частота (самые частые первые)
```

### cut — вырезать поля
```bash
cut -d: -f1,3 /etc/passwd           # Поля 1,3 с разделителем :
cut -c1-10 file                      # Символы 1-10
```

## Архивация и сжатие

### tar
```bash
tar czf archive.tar.gz dir/          # Создать .tar.gz
tar xzf archive.tar.gz               # Распаковать .tar.gz
tar cjf archive.tar.bz2 dir/         # Создать .tar.bz2
tar xjf archive.tar.bz2              # Распаковать .tar.bz2
tar tf archive.tar.gz                 # Просмотреть содержимое
```

### zip/unzip
```bash
zip -r archive.zip dir/              # Создать .zip
unzip archive.zip                     # Распаковать
unzip -l archive.zip                  # Просмотреть содержимое
```

## Системная информация

### uname — информация о системе
```bash
uname -a                     # Всё (ядро, архитектура, версия)
uname -r                     # Версия ядра
```

### free — оперативная память
```bash
free -h                      # RAM и swap (человекочитаемо)
```

### lsblk — блочные устройства
```bash
lsblk                        # Диски и разделы
lsblk -f                     # + файловые системы и UUID
```

### systemctl — управление сервисами
```bash
systemctl status <service>    # Статус
systemctl start <service>     # Запустить
systemctl stop <service>      # Остановить
systemctl restart <service>   # Перезапустить
systemctl enable <service>    # Автозапуск
systemctl disable <service>   # Отключить автозапуск
systemctl list-units --failed # Сбойные сервисы
```

### journalctl — логи systemd
```bash
journalctl -b                 # Логи текущей загрузки
journalctl -b -1              # Логи предыдущей загрузки
journalctl -u <service>       # Логи конкретного сервиса
journalctl -f                 # Следить в реальном времени
journalctl --since "1 hour ago"
```

## Перенаправление и конвейеры

```bash
command > file                # Перенаправить stdout в файл (перезаписать)
command >> file               # Добавить в конец файла
command 2> errors.log         # Перенаправить stderr
command &> all.log            # stderr + stdout в файл
command1 | command2           # Конвейер (pipe)
command1 | tee file           # В файл И на экран
```

## Прочее

### alias — сокращения команд
```bash
alias ll='ls -la'
alias update='sudo apt update && sudo apt upgrade -y'
# Добавить в ~/.bashrc или ~/.zshrc для постоянного эффекта
```

### history — история команд
```bash
history                       # Все команды
history | grep "pattern"      # Поиск в истории
!123                          # Выполнить команду #123
!!                            # Повторить последнюю команду
sudo !!                       # Последняя команда с sudo
```

### xargs — передача аргументов
```bash
find . -name "*.tmp" | xargs rm              # Удалить найденные
cat urls.txt | xargs -I {} curl -O {}        # Скачать каждый URL
echo "a b c" | xargs -n1                     # По одному аргументу
```

### watch — периодическое выполнение
```bash
watch -n 2 'df -h'            # Каждые 2 секунды
watch -d 'free -h'            # Подсвечивать изменения
```

### screen / tmux — терминальный мультиплексор
```bash
# tmux (рекомендуется):
tmux                          # Новая сессия
tmux new -s name              # Именованная сессия
tmux ls                       # Список сессий
tmux attach -t name           # Подключиться
# Ctrl+B, D — отключиться (сессия продолжает работать)
# Ctrl+B, % — разделить вертикально
# Ctrl+B, " — разделить горизонтально
```
