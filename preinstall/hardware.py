"""
Lina — Сканер оборудования для предустановочного режима.

Обнаруживает и анализирует:
  - CPU (модель, ядра, частоты)
  - GPU (видеокарты, драйверы)
  - RAM (объём, модули)
  - Диски и разделы (lsblk, blkid, fdisk)
  - Режим загрузки (UEFI / BIOS / Secure Boot)
  - Батарея (для ноутбуков)

Все данные собираются через subprocess (lscpu, lspci, lsblk и т.д.)
без сторонних зависимостей.
"""

import subprocess
import re
import os
from pathlib import Path
from typing import Dict, List, Optional


class HardwareScanner:
    """
    Сканер оборудования для Live-USB окружения.

    Собирает полную информацию о системе перед установкой Linux.
    Все операции read-only, безопасны для Live-среды.
    """

    def __init__(self):
        self._cache: Dict[str, object] = {}

    # ── Утилиты ──

    def _run(self, cmd: str, timeout: int = 10) -> str:
        """Выполняет shell-команду и возвращает stdout."""
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True,
                text=True, timeout=timeout,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, Exception):
            return ""

    def _read_file(self, path: str) -> str:
        """Безопасное чтение системного файла."""
        try:
            return Path(path).read_text().strip()
        except (IOError, PermissionError):
            return ""

    # ── CPU ──

    def get_cpu_info(self) -> Dict:
        """
        Информация о процессоре.

        Returns:
            dict с полями: model, cores, threads, freq_mhz, arch, flags
        """
        if "cpu" in self._cache:
            return self._cache["cpu"]

        info = {
            "model": "Неизвестно",
            "cores": 0,
            "threads": 0,
            "freq_mhz": "",
            "arch": "",
            "flags": [],
        }

        output = self._run("lscpu")
        if output:
            for line in output.splitlines():
                line = line.strip()
                if "Model name" in line or "Имя модели" in line:
                    info["model"] = line.split(":", 1)[-1].strip()
                elif line.startswith("CPU(s):") or line.startswith("Процессор(ы):"):
                    try:
                        info["threads"] = int(line.split(":", 1)[-1].strip())
                    except ValueError:
                        pass
                elif "Thread(s) per core" in line or "Поток(ов)" in line:
                    try:
                        tpc = int(line.split(":", 1)[-1].strip())
                    except ValueError:
                        tpc = 1
                elif "Core(s) per socket" in line or "Ядер на сокет" in line:
                    try:
                        info["cores"] = int(line.split(":", 1)[-1].strip())
                    except ValueError:
                        pass
                elif "CPU max MHz" in line or "Макс. частота" in line:
                    info["freq_mhz"] = line.split(":", 1)[-1].strip()
                elif "Architecture" in line or "Архитектура" in line:
                    info["arch"] = line.split(":", 1)[-1].strip()
                elif "Flags" in line or "Флаги" in line:
                    info["flags"] = line.split(":", 1)[-1].strip().split()

        self._cache["cpu"] = info
        return info

    # ── GPU ──

    def get_gpu_info(self) -> List[Dict]:
        """
        Информация о видеокартах.

        Returns:
            Список dict с полями: name, vendor, driver_suggestion
        """
        if "gpu" in self._cache:
            return self._cache["gpu"]

        gpus = []
        output = self._run("lspci -nn 2>/dev/null | grep -iE 'vga|3d|display'")

        if output:
            for line in output.splitlines():
                gpu = {"name": line.strip(), "vendor": "", "driver_suggestion": ""}

                name_lower = line.lower()
                if "nvidia" in name_lower:
                    gpu["vendor"] = "NVIDIA"
                    gpu["driver_suggestion"] = "nvidia-driver (проприетарный) или nouveau (открытый)"
                elif "amd" in name_lower or "radeon" in name_lower:
                    gpu["vendor"] = "AMD"
                    gpu["driver_suggestion"] = "amdgpu (рекомендуется, встроен в ядро)"
                elif "intel" in name_lower:
                    gpu["vendor"] = "Intel"
                    gpu["driver_suggestion"] = "i915 (встроен в ядро, работает из коробки)"
                else:
                    gpu["driver_suggestion"] = "Используйте vesa/fbdev как fallback"

                gpus.append(gpu)

        self._cache["gpu"] = gpus
        return gpus

    # ── RAM ──

    def get_ram_info(self) -> Dict:
        """
        Информация об оперативной памяти.

        Returns:
            dict: total_mb, available_mb, swap_total_mb, swap_free_mb
        """
        if "ram" in self._cache:
            return self._cache["ram"]

        info = {
            "total_mb": 0,
            "available_mb": 0,
            "swap_total_mb": 0,
            "swap_free_mb": 0,
        }

        meminfo = self._read_file("/proc/meminfo")
        if meminfo:
            for line in meminfo.splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(re.findall(r'\d+', line)[0])
                    info["total_mb"] = kb // 1024
                elif line.startswith("MemAvailable:"):
                    kb = int(re.findall(r'\d+', line)[0])
                    info["available_mb"] = kb // 1024
                elif line.startswith("SwapTotal:"):
                    kb = int(re.findall(r'\d+', line)[0])
                    info["swap_total_mb"] = kb // 1024
                elif line.startswith("SwapFree:"):
                    kb = int(re.findall(r'\d+', line)[0])
                    info["swap_free_mb"] = kb // 1024

        self._cache["ram"] = info
        return info

    # ── Диски и разделы ──

    def get_disk_info(self) -> List[Dict]:
        """
        Информация о дисках и разделах (lsblk).

        Returns:
            Список dict: name, size, type, mountpoint, fstype, model
        """
        if "disks" in self._cache:
            return self._cache["disks"]

        disks = []
        output = self._run(
            "lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL -n -p 2>/dev/null"
        )

        if output:
            for line in output.splitlines():
                parts = line.split(None, 5)
                if len(parts) >= 3:
                    disk = {
                        "name": parts[0].strip(),
                        "size": parts[1] if len(parts) > 1 else "",
                        "type": parts[2] if len(parts) > 2 else "",
                        "mountpoint": parts[3] if len(parts) > 3 else "",
                        "fstype": parts[4] if len(parts) > 4 else "",
                        "model": parts[5].strip() if len(parts) > 5 else "",
                    }
                    disks.append(disk)

        self._cache["disks"] = disks
        return disks

    def get_partition_table(self) -> str:
        """
        Подробная таблица разделов (fdisk -l).

        Returns:
            Текстовый вывод fdisk для всех дисков.
        """
        # fdisk требует root, пробуем через sudo
        output = self._run("sudo fdisk -l 2>/dev/null || fdisk -l 2>/dev/null")
        return output or "Не удалось получить таблицу разделов (нужен root)."

    def get_block_ids(self) -> List[Dict]:
        """
        UUID и метки разделов (blkid).

        Returns:
            Список dict: device, uuid, type, label
        """
        output = self._run("blkid 2>/dev/null || sudo blkid 2>/dev/null")
        blocks = []

        if output:
            for line in output.splitlines():
                block = {"device": "", "uuid": "", "type": "", "label": ""}
                parts = line.split(":", 1)
                if len(parts) == 2:
                    block["device"] = parts[0].strip()
                    attrs = parts[1]
                    uuid_m = re.search(r'UUID="([^"]+)"', attrs)
                    type_m = re.search(r'TYPE="([^"]+)"', attrs)
                    label_m = re.search(r'LABEL="([^"]+)"', attrs)
                    if uuid_m:
                        block["uuid"] = uuid_m.group(1)
                    if type_m:
                        block["type"] = type_m.group(1)
                    if label_m:
                        block["label"] = label_m.group(1)
                    blocks.append(block)

        return blocks

    # ── Режим загрузки ──

    def get_boot_mode(self) -> Dict:
        """
        Определяет режим загрузки: UEFI или BIOS.

        Returns:
            dict: mode (UEFI/BIOS), secure_boot (bool/None), efi_vars (bool)
        """
        if "boot" in self._cache:
            return self._cache["boot"]

        info = {
            "mode": "BIOS",
            "secure_boot": None,
            "efi_vars": False,
        }

        # Проверяем UEFI
        if Path("/sys/firmware/efi").exists():
            info["mode"] = "UEFI"
            info["efi_vars"] = Path("/sys/firmware/efi/efivars").exists()

            # Secure Boot
            sb = self._run("mokutil --sb-state 2>/dev/null")
            if "enabled" in sb.lower():
                info["secure_boot"] = True
            elif "disabled" in sb.lower():
                info["secure_boot"] = False

        self._cache["boot"] = info
        return info

    # ── Батарея ──

    def get_battery_info(self) -> Optional[Dict]:
        """
        Информация о батарее (для ноутбуков).

        Returns:
            dict или None: status, capacity_percent, technology
        """
        bat_path = Path("/sys/class/power_supply/BAT0")
        if not bat_path.exists():
            bat_path = Path("/sys/class/power_supply/BAT1")
        if not bat_path.exists():
            return None

        info = {}
        for attr in ("status", "capacity", "technology"):
            val = self._read_file(str(bat_path / attr))
            if val:
                info[attr] = val

        if "capacity" in info:
            info["capacity_percent"] = f"{info.pop('capacity')}%"

        return info

    # ── Сводка ──

    def system_overview(self) -> str:
        """
        Полная сводка оборудования.

        Returns:
            Форматированная строка с информацией о системе.
        """
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║      🖥  Обзор системы (предустановка)          ║")
        lines.append("╠══════════════════════════════════════════════════╣")

        # CPU
        cpu = self.get_cpu_info()
        lines.append(f"║  🧠 CPU: {cpu['model']}")
        lines.append(f"║     Ядра: {cpu['cores']} | Потоки: {cpu['threads']} | Арх: {cpu['arch']}")
        if cpu["freq_mhz"]:
            lines.append(f"║     Макс. частота: {cpu['freq_mhz']} MHz")

        # RAM
        ram = self.get_ram_info()
        lines.append(f"║  💾 RAM: {ram['total_mb']} MB (свободно: {ram['available_mb']} MB)")
        if ram["swap_total_mb"]:
            lines.append(f"║     Swap: {ram['swap_total_mb']} MB (свободно: {ram['swap_free_mb']} MB)")

        # GPU
        gpus = self.get_gpu_info()
        if gpus:
            for i, gpu in enumerate(gpus):
                lines.append(f"║  🎮 GPU #{i+1}: {gpu['vendor']} — {gpu['name'][:50]}")
                lines.append(f"║     Драйвер: {gpu['driver_suggestion']}")
        else:
            lines.append("║  🎮 GPU: не обнаружена")

        # Загрузка
        boot = self.get_boot_mode()
        sb = ""
        if boot["secure_boot"] is True:
            sb = " (Secure Boot: ВКЛ)"
        elif boot["secure_boot"] is False:
            sb = " (Secure Boot: ВЫКЛ)"
        lines.append(f"║  ⚡ Режим загрузки: {boot['mode']}{sb}")

        # Диски
        disks = self.get_disk_info()
        disk_devs = [d for d in disks if d["type"] == "disk"]
        lines.append(f"║  💿 Диски: {len(disk_devs)} устройств")
        for d in disk_devs:
            model = f" ({d['model']})" if d["model"] else ""
            lines.append(f"║     {d['name']} — {d['size']}{model}")

        # Батарея
        bat = self.get_battery_info()
        if bat:
            status = bat.get("status", "?")
            cap = bat.get("capacity_percent", "?")
            lines.append(f"║  🔋 Батарея: {status} ({cap})")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    def partition_assist(self) -> str:
        """
        Анализ разделов с рекомендациями для установки.

        Returns:
            Форматированная строка с анализом и рекомендациями.
        """
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║      💿 Анализ разделов — Помощник установки    ║")
        lines.append("╠══════════════════════════════════════════════════╣")

        disks = self.get_disk_info()
        boot = self.get_boot_mode()
        ram = self.get_ram_info()

        # Группируем по дискам
        disk_devs = [d for d in disks if d["type"] == "disk"]
        parts = [d for d in disks if d["type"] == "part"]

        for disk in disk_devs:
            model = f" ({disk['model']})" if disk["model"] else ""
            lines.append(f"║")
            lines.append(f"║  📀 {disk['name']} — {disk['size']}{model}")

            disk_parts = [p for p in parts if p["name"].startswith(disk["name"])]
            if disk_parts:
                for p in disk_parts:
                    mp = p["mountpoint"] if p["mountpoint"] else "не смонтирован"
                    fs = p["fstype"] if p["fstype"] else "нет ФС"
                    lines.append(f"║    ├── {p['name']} [{p['size']}] {fs} → {mp}")
            else:
                lines.append("║    └── Нет разделов (чистый диск)")

        # Рекомендации
        lines.append("║")
        lines.append("║  📋 Рекомендации:")

        # EFI раздел
        if boot["mode"] == "UEFI":
            efi_exists = any(
                p.get("fstype") in ("vfat", "fat32")
                and ("/boot/efi" in p.get("mountpoint", "") or "EFI" in p.get("name", "").upper())
                for p in parts
            )
            if efi_exists:
                lines.append("║    ✅ EFI раздел обнаружен")
            else:
                lines.append("║    ⚠ Создайте EFI раздел (512 MB, FAT32, /boot/efi)")

        # Swap
        ram_mb = ram["total_mb"]
        swap_exists = any(p.get("fstype") == "swap" for p in parts)
        if swap_exists:
            lines.append("║    ✅ Swap раздел обнаружен")
        else:
            if ram_mb <= 4096:
                swap_rec = "4 GB"
            elif ram_mb <= 8192:
                swap_rec = "4-8 GB"
            else:
                swap_rec = "2-4 GB (или swapfile)"
            lines.append(f"║    💡 Рекомендуемый swap: {swap_rec} (RAM: {ram_mb} MB)")

        # Корневой раздел
        lines.append("║    💡 Корневой раздел (/): минимум 20 GB, рекомендуется 50+ GB")
        lines.append("║    💡 Файловая система: ext4 (надёжность) или btrfs (снимки)")

        # Отдельный /home
        lines.append("║    💡 Отдельный /home: удобно для переустановок")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    def pre_install_check(self) -> str:
        """
        Проверка готовности к установке Linux.

        Проверяет: место на диске, RAM, режим загрузки, интернет.

        Returns:
            Форматированная строка с результатами проверки.
        """
        lines = []
        lines.append("╔══════════════════════════════════════════════════╗")
        lines.append("║      ✅ Проверка готовности к установке          ║")
        lines.append("╠══════════════════════════════════════════════════╣")

        warnings = []
        ok_count = 0
        total_checks = 0

        # 1. RAM
        total_checks += 1
        ram = self.get_ram_info()
        if ram["total_mb"] >= 2048:
            lines.append(f"║  ✅ RAM: {ram['total_mb']} MB (минимум 2 GB)")
            ok_count += 1
        elif ram["total_mb"] >= 1024:
            lines.append(f"║  ⚠ RAM: {ram['total_mb']} MB (рекомендуется 2+ GB)")
            warnings.append("Мало RAM — рассмотрите лёгкий DE (XFCE, LXQt)")
        else:
            lines.append(f"║  ❌ RAM: {ram['total_mb']} MB (недостаточно!)")
            warnings.append("Критически мало RAM!")

        # 2. CPU
        total_checks += 1
        cpu = self.get_cpu_info()
        if cpu["cores"] >= 2:
            lines.append(f"║  ✅ CPU: {cpu['cores']} ядер — достаточно")
            ok_count += 1
        else:
            lines.append(f"║  ⚠ CPU: {cpu['cores']} ядро — минимум")
            warnings.append("Одноядерный CPU — установка будет медленной")

        # 3. Диски
        total_checks += 1
        disks = self.get_disk_info()
        disk_devs = [d for d in disks if d["type"] == "disk"]
        if disk_devs:
            lines.append(f"║  ✅ Дисков: {len(disk_devs)}")
            ok_count += 1
        else:
            lines.append("║  ❌ Диски не обнаружены!")
            warnings.append("Нет доступных дисков для установки")

        # 4. Режим загрузки
        total_checks += 1
        boot = self.get_boot_mode()
        lines.append(f"║  ℹ  Режим: {boot['mode']}")
        ok_count += 1
        if boot["secure_boot"] is True:
            warnings.append("Secure Boot включен — некоторые драйверы могут не работать")

        # 5. Интернет (ping)
        total_checks += 1
        ping_ok = self._run("ping -c 1 -W 3 8.8.8.8 2>/dev/null")
        if "1 received" in ping_ok or "1 packets transmitted" in ping_ok:
            lines.append("║  ✅ Интернет-подключение: есть")
            ok_count += 1
        else:
            lines.append("║  ⚠ Интернет-подключение: нет")
            warnings.append("Нет интернета — установка пакетов будет ограничена")

        # 6. Свободное место (оценка)
        total_checks += 1
        free_space = self._run("df -BG / 2>/dev/null | tail -1")
        if free_space:
            parts_df = free_space.split()
            if len(parts_df) >= 4:
                avail = parts_df[3].replace("G", "")
                try:
                    avail_gb = int(avail)
                    if avail_gb >= 20:
                        lines.append(f"║  ✅ Свободно на Live: ~{avail_gb} GB")
                        ok_count += 1
                    else:
                        lines.append(f"║  ⚠ Свободно на Live: ~{avail_gb} GB")
                except ValueError:
                    ok_count += 1
            else:
                ok_count += 1
        else:
            ok_count += 1

        # Итог
        lines.append("║")
        lines.append(f"║  📊 Готовность: {ok_count}/{total_checks} проверок пройдено")

        if warnings:
            lines.append("║")
            lines.append("║  ⚠ Предупреждения:")
            for w in warnings:
                lines.append(f"║    • {w}")
        else:
            lines.append("║  🎉 Система готова к установке!")

        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    def clear_cache(self) -> None:
        """Очищает кэш сканирования."""
        self._cache.clear()
