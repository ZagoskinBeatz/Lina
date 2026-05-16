"""
Lina — Системная диагностика.

Сбор диагностической информации о Linux-системе:
  - Общая сводка (CPU, RAM, диски, uptime)
  - Ошибки из journalctl / dmesg
  - Неудачные systemd-сервисы
  - Использование дисков
  - Давление памяти (memory pressure)
  - Нагрузка CPU
  - Статус сети
  - Статус GPU
  - Статус аудио
  - Лог загрузки

Все операции — read-only через subprocess.
Кэширование на 30 сек для частых запросов.
"""

import subprocess
import time
import re
from typing import Dict, List, Optional, Tuple
from functools import lru_cache


# ─── Кэш с TTL ────────────────────────────────────────────────────────────────

_cache: Dict[str, Tuple[float, object]] = {}
_CACHE_TTL = 30.0  # секунд
_MAX_CACHE_ENTRIES = 50
_SINCE_RE = re.compile(r"^\d{1,4}[smhd]\Z")


def _cached(key: str, fn, ttl: float = _CACHE_TTL):
    """Простой кэш с TTL."""
    now = time.time()
    if key in _cache:
        ts, val = _cache[key]
        if now - ts < ttl:
            return val
    if len(_cache) >= _MAX_CACHE_ENTRIES:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest]
    val = fn()
    _cache[key] = (now, val)
    return val


def clear_cache():
    """Очищает весь кэш диагностики."""
    _cache.clear()


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def _run(cmd: str, timeout: int = 10) -> str:
    """Выполняет команду и возвращает stdout. При ошибке — пустая строка."""
    try:
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True,
            timeout=timeout,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _run_lines(cmd: str, timeout: int = 10) -> List[str]:
    """Выполняет команду и возвращает список строк."""
    out = _run(cmd, timeout)
    return [line for line in out.split("\n") if line.strip()] if out else []


def _parse_size(size_str: str) -> str:
    """Оставляет размер как есть (уже human-readable из -h)."""
    return size_str.strip()


# ─── Диагностические функции ──────────────────────────────────────────────────

def get_system_summary() -> Dict:
    """
    Общая сводка о системе: CPU, RAM, swap, диски, uptime, ядро.

    Returns:
        {hostname, kernel, uptime, cpu_model, cpu_cores, load_avg,
         ram_total, ram_used, ram_free, swap_total, swap_used,
         disk_root_total, disk_root_used, disk_root_free, disk_root_pct}
    """
    def _collect():
        info = {}
        # Hostname
        info["hostname"] = _run("hostname") or "unknown"
        # Kernel
        info["kernel"] = _run("uname -r") or "unknown"
        # Uptime
        uptime_raw = _run("cat /proc/uptime")
        if uptime_raw:
            secs = float(uptime_raw.split()[0])
            days = int(secs // 86400)
            hours = int((secs % 86400) // 3600)
            mins = int((secs % 3600) // 60)
            info["uptime"] = f"{days}d {hours}h {mins}m"
            info["uptime_seconds"] = int(secs)
        else:
            info["uptime"] = "unknown"
            info["uptime_seconds"] = 0

        # CPU
        cpu_model = _run("grep -m1 'model name' /proc/cpuinfo | cut -d: -f2")
        info["cpu_model"] = cpu_model.strip() if cpu_model else "unknown"
        cores = _run("nproc")
        info["cpu_cores"] = int(cores) if cores.isdigit() else 0

        # Load average
        loadavg = _run("cat /proc/loadavg")
        if loadavg:
            parts = loadavg.split()
            info["load_avg"] = f"{parts[0]} {parts[1]} {parts[2]}"
        else:
            info["load_avg"] = "unknown"

        # RAM
        meminfo = _run("cat /proc/meminfo")
        if meminfo:
            mem = {}
            for line in meminfo.split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    # Значение в kB
                    nums = re.findall(r'\d+', v)
                    if nums:
                        mem[k.strip()] = int(nums[0])

            total_kb = mem.get("MemTotal", 0)
            free_kb = mem.get("MemFree", 0)
            avail_kb = mem.get("MemAvailable", free_kb)
            buffers_kb = mem.get("Buffers", 0)
            cached_kb = mem.get("Cached", 0)
            used_kb = total_kb - avail_kb

            info["ram_total"] = f"{total_kb // 1024} MB"
            info["ram_used"] = f"{used_kb // 1024} MB"
            info["ram_free"] = f"{avail_kb // 1024} MB"
            info["ram_pct"] = round(used_kb / total_kb * 100, 1) if total_kb else 0

            swap_total = mem.get("SwapTotal", 0)
            swap_free = mem.get("SwapFree", 0)
            swap_used = swap_total - swap_free
            info["swap_total"] = f"{swap_total // 1024} MB"
            info["swap_used"] = f"{swap_used // 1024} MB"
        else:
            for k in ("ram_total", "ram_used", "ram_free", "swap_total", "swap_used"):
                info[k] = "unknown"
            info["ram_pct"] = 0

        # Диск (корень)
        df_out = _run("df -h / | tail -1")
        if df_out:
            parts = df_out.split()
            if len(parts) >= 5:
                info["disk_root_total"] = parts[1]
                info["disk_root_used"] = parts[2]
                info["disk_root_free"] = parts[3]
                info["disk_root_pct"] = parts[4]
        else:
            for k in ("disk_root_total", "disk_root_used", "disk_root_free", "disk_root_pct"):
                info[k] = "unknown"

        return info

    return _cached("system_summary", _collect)


def get_journal_errors(since: str = "1h", limit: int = 30) -> List[Dict]:
    """
    Последние ошибки из journalctl.

    Args:
        since: Период ("1h", "30min", "1d").
        limit: Макс. количество записей.

    Returns:
        [{timestamp, unit, message, priority}, ...]
    """
    if not _SINCE_RE.match(since):
        since = "1h"
    limit = max(1, min(int(limit), 200))

    def _collect():
        lines = _run_lines(
            f"journalctl --no-pager -p err --since='-{since}' -n {limit} "
            f"--output=short-iso 2>/dev/null"
        )
        errors = []
        for line in lines:
            # Формат: 2025-01-15T10:30:00+0300 hostname unit[pid]: message
            match = re.match(
                r'^(\S+)\s+\S+\s+(\S+?)(?:\[\d+\])?:\s+(.+)$', line
            )
            if match:
                errors.append({
                    "timestamp": match.group(1),
                    "unit": match.group(2),
                    "message": match.group(3),
                    "priority": "error",
                })
            elif line.strip():
                errors.append({
                    "timestamp": "",
                    "unit": "",
                    "message": line.strip(),
                    "priority": "error",
                })
        return errors

    return _cached(f"journal_errors_{since}", _collect)


def get_dmesg_errors(limit: int = 20) -> List[Dict]:
    """
    Ошибки ядра из dmesg.

    Returns:
        [{timestamp, message, level}, ...]
    """
    limit = max(1, min(int(limit), 200))

    def _collect():
        lines = _run_lines(f"dmesg --level=err,warn -T 2>/dev/null | tail -n {limit}")
        errors = []
        for line in lines:
            match = re.match(r'^\[(.+?)\]\s+(.+)$', line)
            if match:
                errors.append({
                    "timestamp": match.group(1).strip(),
                    "message": match.group(2).strip(),
                    "level": "error",
                })
            elif line.strip():
                errors.append({
                    "timestamp": "",
                    "message": line.strip(),
                    "level": "error",
                })
        return errors

    return _cached("dmesg_errors", _collect)


def get_failed_services() -> List[Dict]:
    """
    Список неудачных systemd-сервисов.

    Returns:
        [{name, load_state, active_state, sub_state, description}, ...]
    """
    def _collect():
        lines = _run_lines(
            "systemctl --no-pager --no-legend list-units --state=failed 2>/dev/null"
        )
        services = []
        for line in lines:
            parts = line.split(None, 4)
            if len(parts) >= 5:
                services.append({
                    "name": parts[0].strip(),
                    "load_state": parts[1],
                    "active_state": parts[2],
                    "sub_state": parts[3],
                    "description": parts[4],
                })
            elif len(parts) >= 1:
                services.append({
                    "name": parts[0].strip(),
                    "load_state": "",
                    "active_state": "failed",
                    "sub_state": "",
                    "description": "",
                })
        return services

    return _cached("failed_services", _collect)


def get_disk_usage() -> List[Dict]:
    """
    Использование дисков (df -h).

    Returns:
        [{filesystem, size, used, available, use_pct, mountpoint}, ...]
    """
    def _collect():
        lines = _run_lines("df -h -x tmpfs -x devtmpfs -x squashfs 2>/dev/null")
        disks = []
        for line in lines[1:]:  # Пропускаем заголовок
            parts = line.split(None, 5)
            if len(parts) >= 6:
                disks.append({
                    "filesystem": parts[0],
                    "size": parts[1],
                    "used": parts[2],
                    "available": parts[3],
                    "use_pct": parts[4],
                    "mountpoint": parts[5],
                })
        return disks

    return _cached("disk_usage", _collect)


def get_memory_pressure() -> Dict:
    """
    Анализ давления памяти.

    Returns:
        {ram_pct, swap_pct, oom_recent, pressure_level, top_consumers}
    """
    def _collect():
        info = {}

        # RAM
        meminfo = _run("cat /proc/meminfo")
        mem = {}
        for line in (meminfo or "").split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                nums = re.findall(r'\d+', v)
                if nums:
                    mem[k.strip()] = int(nums[0])

        total = mem.get("MemTotal", 1)
        avail = mem.get("MemAvailable", total)
        used = total - avail
        info["ram_pct"] = round(used / total * 100, 1) if total else 0

        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        swap_used = swap_total - swap_free
        info["swap_pct"] = round(swap_used / swap_total * 100, 1) if swap_total else 0

        # OOM killer
        oom_lines = _run_lines("dmesg -T 2>/dev/null | grep -i 'oom\\|out of memory' | tail -5")
        info["oom_recent"] = len(oom_lines)
        info["oom_messages"] = oom_lines[:3]

        # Pressure level
        if info["ram_pct"] > 90:
            info["pressure_level"] = "critical"
        elif info["ram_pct"] > 75:
            info["pressure_level"] = "high"
        elif info["ram_pct"] > 50:
            info["pressure_level"] = "moderate"
        else:
            info["pressure_level"] = "low"

        # Top consumers
        ps_lines = _run_lines("ps aux --sort=-%mem | head -6")
        info["top_consumers"] = []
        for line in ps_lines[1:]:  # skip header
            parts = line.split(None, 10)
            if len(parts) >= 11:
                info["top_consumers"].append({
                    "user": parts[0],
                    "pid": parts[1],
                    "mem_pct": parts[3],
                    "command": parts[10][:80],
                })

        return info

    return _cached("memory_pressure", _collect)


def get_cpu_load_analysis() -> Dict:
    """
    Анализ нагрузки CPU.

    Returns:
        {cores, load_1m, load_5m, load_15m, load_status,
         top_processes, governor, temperature}
    """
    def _collect():
        info = {}
        cores = _run("nproc")
        info["cores"] = int(cores) if cores.isdigit() else 1

        loadavg = _run("cat /proc/loadavg")
        if loadavg:
            parts = loadavg.split()
            info["load_1m"] = float(parts[0])
            info["load_5m"] = float(parts[1])
            info["load_15m"] = float(parts[2])
        else:
            info["load_1m"] = info["load_5m"] = info["load_15m"] = 0.0

        # Load status
        ratio = info["load_1m"] / info["cores"] if info["cores"] else 0
        if ratio > 2.0:
            info["load_status"] = "critical"
        elif ratio > 1.0:
            info["load_status"] = "high"
        elif ratio > 0.7:
            info["load_status"] = "moderate"
        else:
            info["load_status"] = "normal"

        # Top CPU processes
        ps_lines = _run_lines("ps aux --sort=-%cpu | head -6")
        info["top_processes"] = []
        for line in ps_lines[1:]:
            parts = line.split(None, 10)
            if len(parts) >= 11:
                info["top_processes"].append({
                    "user": parts[0],
                    "pid": parts[1],
                    "cpu_pct": parts[2],
                    "command": parts[10][:80],
                })

        # CPU governor
        info["governor"] = _run(
            "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null"
        ) or "unknown"

        # Temperature
        temp = _run(
            "cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null"
        )
        if temp and temp.isdigit():
            info["temperature"] = f"{int(temp) // 1000}°C"
        else:
            info["temperature"] = "unknown"

        return info

    return _cached("cpu_load", _collect)


def get_network_status() -> Dict:
    """
    Статус сети: интерфейсы, IP, DNS, connectivity.

    Returns:
        {interfaces, default_gateway, dns_servers, internet_ok, ping_ms}
    """
    def _collect():
        info = {}

        # Интерфейсы
        iface_lines = _run_lines("ip -brief addr 2>/dev/null")
        info["interfaces"] = []
        for line in iface_lines:
            parts = line.split(None, 2)
            if len(parts) >= 2:
                info["interfaces"].append({
                    "name": parts[0],
                    "state": parts[1],
                    "addresses": parts[2].strip() if len(parts) > 2 else "",
                })

        # Default gateway
        gw = _run("ip route | grep default | head -1")
        if gw:
            match = re.search(r'via\s+(\S+)', gw)
            info["default_gateway"] = match.group(1) if match else "none"
        else:
            info["default_gateway"] = "none"

        # DNS
        dns = _run_lines("grep '^nameserver' /etc/resolv.conf 2>/dev/null")
        info["dns_servers"] = [
            line.split()[1] for line in dns if len(line.split()) >= 2
        ]

        # Ping test
        ping = _run("ping -c 1 -W 3 8.8.8.8 2>/dev/null")
        if "time=" in ping:
            match = re.search(r'time=(\S+)', ping)
            info["internet_ok"] = True
            info["ping_ms"] = match.group(1) if match else "0"
        else:
            info["internet_ok"] = False
            info["ping_ms"] = "timeout"

        return info

    return _cached("network_status", _collect)


def get_gpu_status() -> Dict:
    """
    Статус GPU: модель, драйвер, температура, VRAM.

    Returns:
        {model, driver, temperature, vram_used, vram_total, vendor}
    """
    def _collect():
        info = {"model": "unknown", "driver": "unknown", "vendor": "unknown",
                "temperature": "unknown", "vram_used": "", "vram_total": ""}

        # lspci
        gpu_line = _run("lspci -nn 2>/dev/null | grep -i 'vga\\|3d\\|display'")
        if gpu_line:
            info["model"] = gpu_line.split(":", 2)[-1].strip() if ":" in gpu_line else gpu_line

        # Драйвер
        driver = _run("lspci -k 2>/dev/null | grep -A2 -i 'vga\\|3d' | grep 'Kernel driver'")
        if driver:
            info["driver"] = driver.split(":")[-1].strip()

        # Vendor
        model_lower = info["model"].lower()
        if "nvidia" in model_lower:
            info["vendor"] = "nvidia"
        elif "amd" in model_lower or "radeon" in model_lower:
            info["vendor"] = "amd"
        elif "intel" in model_lower:
            info["vendor"] = "intel"

        # NVIDIA-specific
        if info["vendor"] == "nvidia":
            smi = _run("nvidia-smi --query-gpu=temperature.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null")
            if smi:
                parts = smi.split(",")
                if len(parts) >= 3:
                    info["temperature"] = f"{parts[0].strip()}°C"
                    info["vram_used"] = f"{parts[1].strip()} MB"
                    info["vram_total"] = f"{parts[2].strip()} MB"

        # AMD — hwmon
        elif info["vendor"] == "amd":
            temp = _run("cat /sys/class/drm/card0/device/hwmon/hwmon*/temp1_input 2>/dev/null")
            if temp and temp.isdigit():
                info["temperature"] = f"{int(temp) // 1000}°C"

        return info

    return _cached("gpu_status", _collect)


def get_boot_log(limit: int = 30) -> Dict:
    """
    Информация о загрузке.

    Returns:
        {boot_time, errors, warnings, kernel_version, systemd_version}
    """
    limit = max(1, min(int(limit), 200))

    def _collect():
        info = {}

        # Время загрузки
        blame = _run("systemd-analyze 2>/dev/null | head -1")
        info["boot_time"] = blame if blame else "unknown"

        # Ошибки загрузки
        errors = _run_lines(
            f"journalctl -b --no-pager -p err --output=short 2>/dev/null | tail -n {limit}"
        )
        info["errors"] = errors[:limit]
        info["error_count"] = len(errors)

        # Предупреждения
        warnings = _run_lines(
            "journalctl -b --no-pager -p warning --output=short 2>/dev/null | tail -n 10"
        )
        info["warning_count"] = len(warnings)

        # Версии
        info["kernel_version"] = _run("uname -r") or "unknown"
        systemd_ver = _run("systemctl --version 2>/dev/null | head -1")
        info["systemd_version"] = systemd_ver if systemd_ver else "unknown"

        return info

    return _cached("boot_log", _collect)


def get_audio_status() -> Dict:
    """
    Статус аудиосистемы.

    Returns:
        {server, running, sinks, sources, default_sink, muted, volume}
    """
    def _collect():
        info = {"server": "unknown", "running": False, "sinks": [],
                "sources": [], "default_sink": "", "muted": False, "volume": ""}

        # PipeWire?
        pw = _run("pw-cli info 0 2>/dev/null")
        if pw:
            info["server"] = "pipewire"
            info["running"] = True
        else:
            # PulseAudio?
            pa = _run("pactl info 2>/dev/null | grep 'Server Name'")
            if pa:
                info["server"] = "pulseaudio"
                info["running"] = True

        if not info["running"]:
            return info

        # Sinks (выходные устройства)
        sink_lines = _run_lines("pactl list short sinks 2>/dev/null")
        for line in sink_lines:
            parts = line.split("\t")
            if len(parts) >= 2:
                info["sinks"].append({
                    "id": parts[0],
                    "name": parts[1],
                    "state": parts[-1] if len(parts) > 2 else "",
                })

        # Sources (входные)
        source_lines = _run_lines("pactl list short sources 2>/dev/null")
        for line in source_lines:
            parts = line.split("\t")
            if len(parts) >= 2:
                # Фильтруем monitor-устройства
                if ".monitor" not in parts[1]:
                    info["sources"].append({
                        "id": parts[0],
                        "name": parts[1],
                    })

        # Default sink
        default = _run("pactl get-default-sink 2>/dev/null")
        info["default_sink"] = default if default else ""

        # Volume & mute
        vol_info = _run("pactl get-sink-volume @DEFAULT_SINK@ 2>/dev/null")
        if vol_info:
            match = re.search(r'(\d+)%', vol_info)
            info["volume"] = match.group(0) if match else ""

        mute_info = _run("pactl get-sink-mute @DEFAULT_SINK@ 2>/dev/null")
        info["muted"] = "yes" in mute_info.lower() if mute_info else False

        return info

    return _cached("audio_status", _collect)


def get_bluetooth_status() -> Dict:
    """
    Статус Bluetooth.

    Returns:
        {powered, adapter, devices, connected_devices}
    """
    def _collect():
        info = {"powered": False, "adapter": "", "devices": [],
                "connected_devices": []}

        # Проверяем bluetoothctl
        show = _run("bluetoothctl show 2>/dev/null")
        if not show:
            return info

        if "Powered: yes" in show:
            info["powered"] = True

        match = re.search(r'Name:\s+(.+)', show)
        if match:
            info["adapter"] = match.group(1).strip()

        # Подключённые устройства
        devices = _run_lines("bluetoothctl devices 2>/dev/null")
        for line in devices:
            match = re.match(r'Device\s+(\S+)\s+(.+)', line)
            if match:
                mac = match.group(1)
                name = match.group(2).strip()
                # Проверяем подключение
                dev_info = _run(f"bluetoothctl info {mac} 2>/dev/null")
                connected = "Connected: yes" in dev_info
                dev = {"mac": mac, "name": name, "connected": connected}
                info["devices"].append(dev)
                if connected:
                    info["connected_devices"].append(dev)

        return info

    return _cached("bluetooth_status", _collect)


def get_full_diagnostic() -> Dict:
    """
    Полная системная диагностика. Собирает все данные.

    Returns:
        Словарь со всеми секциями диагностики.
    """
    clear_cache()  # Свежие данные

    return {
        "system": get_system_summary(),
        "cpu": get_cpu_load_analysis(),
        "memory": get_memory_pressure(),
        "disks": get_disk_usage(),
        "network": get_network_status(),
        "gpu": get_gpu_status(),
        "audio": get_audio_status(),
        "bluetooth": get_bluetooth_status(),
        "boot": get_boot_log(),
        "failed_services": get_failed_services(),
        "journal_errors": get_journal_errors(),
        "dmesg_errors": get_dmesg_errors(),
    }


def format_summary(diag: Optional[Dict] = None) -> str:
    """
    Форматирует сводку диагностики в текстовый отчёт.

    Args:
        diag: Результат get_full_diagnostic(). Если None — собирает заново.

    Returns:
        Многострочный текст с отчётом.
    """
    if diag is None:
        diag = get_full_diagnostic()

    lines = ["═══ Системная диагностика ═══", ""]

    # System
    s = diag.get("system", {})
    lines.append(f"🖥  Хост: {s.get('hostname', '?')}  |  Ядро: {s.get('kernel', '?')}")
    lines.append(f"⏱  Uptime: {s.get('uptime', '?')}  |  Load: {s.get('load_avg', '?')}")
    lines.append(f"🔧 CPU: {s.get('cpu_model', '?')} ({s.get('cpu_cores', '?')} ядер)")
    lines.append(f"💾 RAM: {s.get('ram_used', '?')} / {s.get('ram_total', '?')} ({s.get('ram_pct', 0):.0f}%)")
    lines.append(f"💿 Swap: {s.get('swap_used', '?')} / {s.get('swap_total', '?')}")
    lines.append(f"📁 Диск /: {s.get('disk_root_used', '?')} / {s.get('disk_root_total', '?')} ({s.get('disk_root_pct', '?')})")
    lines.append("")

    # CPU
    cpu = diag.get("cpu", {})
    lines.append(f"⚡ CPU Load: {cpu.get('load_status', '?')} (1m={cpu.get('load_1m', 0):.1f}, 5m={cpu.get('load_5m', 0):.1f})")
    lines.append(f"   Governor: {cpu.get('governor', '?')}  Temp: {cpu.get('temperature', '?')}")

    # Memory
    mem = diag.get("memory", {})
    lines.append(f"🧠 Память: {mem.get('pressure_level', '?')} ({mem.get('ram_pct', 0):.0f}% RAM, {mem.get('swap_pct', 0):.0f}% Swap)")
    if mem.get("oom_recent", 0):
        lines.append(f"   ⚠ OOM events: {mem['oom_recent']}")

    # Network
    net = diag.get("network", {})
    inet = "✅" if net.get("internet_ok") else "❌"
    lines.append(f"🌐 Интернет: {inet}  Ping: {net.get('ping_ms', '?')} ms")
    lines.append(f"   Gateway: {net.get('default_gateway', '?')}  DNS: {', '.join(net.get('dns_servers', []))}")

    # GPU
    gpu = diag.get("gpu", {})
    lines.append(f"🎮 GPU: {gpu.get('model', '?')[:60]}")
    lines.append(f"   Driver: {gpu.get('driver', '?')}  Temp: {gpu.get('temperature', '?')}")

    # Audio
    audio = diag.get("audio", {})
    lines.append(f"🔊 Аудио: {audio.get('server', '?')} ({'работает' if audio.get('running') else 'не работает'})")
    if audio.get("muted"):
        lines.append("   ⚠ Звук замьючен!")

    # Failed services
    failed = diag.get("failed_services", [])
    if failed:
        lines.append(f"\n⚠ Сбойные сервисы ({len(failed)}):")
        for svc in failed[:5]:
            lines.append(f"   ✗ {svc['name']}")

    # Errors
    jerr = diag.get("journal_errors", [])
    if jerr:
        lines.append(f"\n🔴 Ошибки journalctl ({len(jerr)}):")
        for err in jerr[:5]:
            lines.append(f"   {err.get('unit', '')}: {err.get('message', '')[:70]}")

    return "\n".join(lines)
