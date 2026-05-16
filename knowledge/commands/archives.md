# Работа с архивами в Linux

## tar — основной архиватор
```bash
# Создание
tar -czf archive.tar.gz dir/         # gzip
tar -cjf archive.tar.bz2 dir/        # bzip2
tar -cJf archive.tar.xz dir/         # xz (лучшее сжатие)
tar --zstd -cf archive.tar.zst dir/  # zstd (быстрое)

# Распаковка
tar -xzf archive.tar.gz              # gzip
tar -xjf archive.tar.bz2             # bzip2
tar -xJf archive.tar.xz              # xz
tar --zstd -xf archive.tar.zst       # zstd
tar -xf archive.tar.*                # автоопределение

# В конкретную директорию
tar -xf archive.tar.gz -C /target/dir/

# Просмотр содержимого
tar -tf archive.tar.gz
tar -tvf archive.tar.gz              # подробно (как ls -l)

# Добавить файл в архив
tar -rf archive.tar newfile.txt       # только для .tar (без сжатия)

# Исключения
tar -czf archive.tar.gz dir/ --exclude='*.log' --exclude='.git'
tar -czf archive.tar.gz dir/ --exclude-from=exclude.txt

# Сохранить права и владельца
tar -czpf archive.tar.gz dir/        # -p = preserve permissions

# С прогрессом (через pv)
tar -cf - dir/ | pv | gzip > archive.tar.gz

# Разделить на части
tar -czf - large_dir/ | split -b 4G - archive.tar.gz.part_
# Собрать обратно
cat archive.tar.gz.part_* | tar -xzf -
```

## zip / unzip
```bash
# Создание
zip archive.zip file1 file2
zip -r archive.zip dir/               # рекурсивно
zip -e archive.zip file               # с паролем
zip -9 archive.zip file               # максимальное сжатие
zip -r archive.zip dir/ -x "*.git*" "*.cache*"  # с исключениями

# Распаковка
unzip archive.zip
unzip archive.zip -d /target/
unzip -l archive.zip                  # список файлов
unzip -o archive.zip                  # overwrite без вопросов
unzip archive.zip "path/to/file"      # только один файл

# Тестирование целостности
unzip -t archive.zip
```

## 7z — максимальное сжатие
```bash
# Установка
sudo pacman -S p7zip

# Создание
7z a archive.7z dir/
7z a -mx=9 archive.7z dir/            # максимальное сжатие
7z a -p archive.7z dir/               # с паролем
7z a -v100m archive.7z dir/           # разделить по 100 MB

# Распаковка
7z x archive.7z                       # с сохранением структуры
7z e archive.7z                       # все файлы в текущий каталог

# Список
7z l archive.7z

# Тестирование
7z t archive.7z
```

## Утилиты сжатия
```bash
# gzip
gzip file           # file → file.gz (оригинал удаляется)
gunzip file.gz      # file.gz → file
gzip -k file        # сохранить оригинал
gzip -9 file        # максимальное сжатие
pigz file           # параллельный gzip (быстрее на многоядерных)

# zstd (быстрое современное сжатие)
zstd file           # file → file.zst
zstd -d file.zst    # распаковка
zstd -19 file       # максимальное сжатие
zstd -T0 file       # использовать все ядра
zstd --long file    # оптимизация для больших файлов

# xz (максимальное сжатие, но медленнее)
xz file
xz -d file.xz
xz -9e file         # экстремальное сжатие
xz -T0 file         # многопоточный

# bzip2
bzip2 file
bunzip2 file.bz2
pbzip2 file         # параллельный bzip2

# lz4 (самый быстрый, меньше сжатие)
lz4 file
lz4 -d file.lz4
```

## Сравнение алгоритмов сжатия
| Алгоритм | Сжатие | Скорость сжатия | Скорость распаковки |
|----------|--------|----------------|-------------------|
| lz4 | Низкое | Очень быстрая | Очень быстрая |
| zstd | Хорошее | Быстрая | Быстрая |
| gzip | Хорошее | Средняя | Быстрая |
| bzip2 | Высокое | Медленная | Средняя |
| xz | Очень высокое | Очень медленная | Средняя |
| 7z (LZMA2) | Максимальное | Очень медленная | Средняя |

## Примеры часто используемых операций
```bash
# Бэкап home с zstd
tar --zstd -cf ~/backup_$(date +%Y%m%d).tar.zst \
  --exclude='.cache' --exclude='.local/share/Trash' \
  --exclude='node_modules' --exclude='.venv' ~/

# Извлечь один файл из архива
tar -xf archive.tar.gz path/to/specific/file

# Сравнить содержимое архива с диском
tar -df archive.tar.gz

# Подсчитать файлы в архиве
tar -tzf archive.tar.gz | wc -l

# Бэкап с ssh
tar -czf - /home/user/ | ssh user@server 'cat > /backup/home.tar.gz'

# Распаковать .rpm / .deb
rpm2cpio package.rpm | cpio -idmv
ar x package.deb && tar -xf data.tar.*
```
