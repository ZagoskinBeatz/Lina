"""
Lina — Расширенная информация об оборудовании.

Обёртка над preinstall/hardware.py (HardwareScanner) + дополнительные диагностики:
  - Температурные датчики
  - SMART-статус дисков
  - USB-устройства
  - PCI-устройства
  - Информация о дисплеях
  - Сводка совместимости

Все операции — read-only.
"""

import subprocess
import re
import os
from pathlib import Path
from typing import Dict, List, Optional

try:
    from lina.preinstall.hardware import HardwareScanner
except ImportError:
    HardwareScanner = None  # type: ignore


def _run(cmd: str, timeout: int = 10) -> str:
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
    out = _run(cmd, timeout)
    return [l for l in out.split("\n") if l.strip()] if out else []


def _read_sys(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except (IOError, PermissionError, FileNotFoundError):
        return ""


class HardwareInfo:
    """
    Расширенная HW-инфо для runtime-режима Lina.

    Делегирует базовые запросы к HardwareScanner,
    добавляет sensor/SMART/USB/PCI/display.
    """

    def __init__(self):
        self._scanner = HardwareScanner() if HardwareScanner else None
        self._cache: Dict[str, object] = {}

    # ── Делегирование к HardwareScanner ──

    def get_cpu_info(self) -> Dict:
        """CPU: модель, ядра, частоты, архитектура, кэш."""
        if self._scanner:
            return self._scanner.get_cpu_info()
        return self._fallback_cpu()

    def get_gpu_info(self) -> List[Dict]:
        """GPU: модель(и), драйвер, VRAM."""
        if self._scanner:
            return self._scanner.get_gpu_info()
        return self._fallback_gpu()

    def get_ram_info(self) -> Dict:
        """RAM: total, available, used, swap."""
        if self._scanner:
            return self._scanner.get_ram_info()
        return self._fallback_ram()

    def get_disk_info(self) -> List[Dict]:
        """Диск-и: name, size, type, model, mountpoint."""
        if self._scanner:
            return self._scanner.get_disk_info()
        return []

    def get_boot_mode(self) -> Dict:
        """UEFI/BIOS/SecureBoot."""
        if self._scanner:
            return self._scanner.get_boot_mode()
        return {"mode": "unknown"}

    def get_battery_info(self) -> Optional[Dict]:
        """Батарея (если ноутбук)."""
        if self._scanner:
            return self._scanner.get_battery_info()
        return self._fallback_battery()

    # ── Дополнительные методы ──

    def get_sensors(self) -> Dict:
        """
        Температурные датчики (sensors / hwmon).

        Returns:
            {sensors: [{name, adapter, values: {key: {current, high, crit}}}], available: bool}
        """
        info: Dict = {"sensors": [], "available": False}

        # sensors (lm-sensors)
        out = _run("sensors 2>/dev/null")
        if out:
            info["available"] = True
            current_sensor: Dict = {}
            for line in out.split("\n"):
                if not line.strip():
                    if current_sensor:
                        info["sensors"].append(current_sensor)
                        current_sensor = {}
                    continue

                if ":" not in line and line.strip():
                    # Новый адаптер/чип
                    if current_sensor:
                        info["sensors"].append(current_sensor)
                    current_sensor = {"name": line.strip(), "values": {}}
                    continue

                if line.startswith("Adapter:"):
                    if current_sensor:
                        current_sensor["adapter"] = line.split(":", 1)[1].strip()
                    continue

                # Парсим значение
                match = re.match(r'^(.+?):\s+\+?([\d.]+)°C', line)
                if match and current_sensor:
                    key = match.group(1).strip()
                    val: Dict = {"current": float(match.group(2))}
                    high_match = re.search(r'high\s*=\s*\+?([\d.]+)', line)
                    crit_match = re.search(r'crit\s*=\s*\+?([\d.]+)', line)
                    if high_match:
                        val["high"] = float(high_match.group(1))
                    if crit_match:
                        val["crit"] = float(crit_match.group(1))
                    current_sensor.setdefault("values", {})[key] = val

            if current_sensor:
                info["sensors"].append(current_sensor)
        else:
            # Фолбэк: /sys/class/thermal
            thermals = _run_lines("find /sys/class/thermal -name 'temp' -o -name 'type' 2>/dev/null")
            zones = sorted(set(str(Path(t).parent) for t in thermals if "thermal_zone" in t))
            for zone in zones:
                temp_raw = _read_sys(f"{zone}/temp")
                zone_type = _read_sys(f"{zone}/type")
                if temp_raw.isdigit():
                    temp_c = int(temp_raw) / 1000.0
                    info["sensors"].append({
                        "name": zone_type or Path(zone).name,
                        "values": {"temp": {"current": temp_c}},
                    })
                    info["available"] = True

        return info

    def get_smart_status(self) -> List[Dict]:
        """
        SMART-статус дисков.

        Returns:
            [{device, model, health, temperature, power_on_hours, reallocated_sectors}, ...]
        """
        disks = _run_lines("lsblk -d -n -o NAME,TYPE 2>/dev/null")
        results = []

        for line in disks:
            parts = line.split()
            if len(parts) < 2 or parts[1] != "disk":
                continue
            dev = f"/dev/{parts[0]}"

            smart = _run(f"smartctl -i -H -A {dev} 2>/dev/null", timeout=15)
            if not smart:
                continue

            info: Dict = {
                "device": dev,
                "model": "",
                "health": "unknown",
                "temperature": None,
                "power_on_hours": None,
                "reallocated_sectors": None,
            }

            # Model
            model_match = re.search(r'(?:Device Model|Model Number):\s+(.+)', smart)
            if model_match:
                info["model"] = model_match.group(1).strip()

            # Health
            if "PASSED" in smart:
                info["health"] = "PASSED"
            elif "FAILED" in smart:
                info["health"] = "FAILED"

            # Temperature
            temp_match = re.search(r'Temperature.*?(\d+)', smart)
            if temp_match:
                info["temperature"] = int(temp_match.group(1))

            # Power-On Hours
            poh_match = re.search(r'Power_On_Hours.*?\s(\d+)\s*$', smart, re.MULTILINE)
            if poh_match:
                info["power_on_hours"] = int(poh_match.group(1))

            # Reallocated Sectors
            rs_match = re.search(r'Reallocated_Sector_Ct.*?\s(\d+)\s*$', smart, re.MULTILINE)
            if rs_match:
                info["reallocated_sectors"] = int(rs_match.group(1))

            results.append(info)

        return results

    def get_usb_devices(self) -> List[Dict]:
        """
        USB-устройства.

        Returns:
            [{bus, device, id, name}, ...]
        """
        lines = _run_lines("lsusb 2>/dev/null")
        devices = []
        for line in lines:
            match = re.match(
                r'Bus\s+(\d+)\s+Device\s+(\d+):\s+ID\s+(\S+)\s+(.*)', line
            )
            if match:
                devices.append({
                    "bus": match.group(1),
                    "device": match.group(2),
                    "id": match.group(3),
                    "name": match.group(4).strip(),
                })
        return devices

    def get_pci_devices(self, category: Optional[str] = None) -> List[Dict]:
        """
        PCI-устройства.

        Args:
            category: Фильтр (VGA, Network, Audio, USB, Storage, Bridge).

        Returns:
            [{slot, class_name, device, driver}, ...]
        """
        lines = _run_lines("lspci -k 2>/dev/null")
        devices = []
        current: Dict = {}
        for line in lines:
            if line and not line.startswith("\t"):
                if current:
                    devices.append(current)
                match = re.match(r'(\S+)\s+(.+?):\s+(.*)', line)
                if match:
                    current = {
                        "slot": match.group(1),
                        "class_name": match.group(2),
                        "device": match.group(3),
                        "driver": "",
                    }
                else:
                    current = {"slot": "", "class_name": "", "device": line, "driver": ""}
            elif "Kernel driver" in line and current:
                drv = line.split(":", 1)[-1].strip()
                current["driver"] = drv

        if current:
            devices.append(current)

        if category:
            cat = category.lower()
            devices = [d for d in devices if cat in d["class_name"].lower()]

        return devices

    def get_display_info(self) -> List[Dict]:
        """
        Подключённые дисплеи (xrandr / drm).

        Returns:
            [{name, connected, resolution, refresh, primary}, ...]
        """
        displays = []

        # Пробуем xrandr
        xr = _run("xrandr --current 2>/dev/null")
        if xr:
            for line in xr.split("\n"):
                conn_match = re.match(r'^(\S+)\s+(connected|disconnected)', line)
                if conn_match:
                    d: Dict = {
                        "name": conn_match.group(1),
                        "connected": conn_match.group(2) == "connected",
                        "resolution": "",
                        "refresh": "",
                        "primary": "primary" in line,
                    }
                    res_match = re.search(r'(\d+x\d+)\+', line)
                    if res_match:
                        d["resolution"] = res_match.group(1)
                    displays.append(d)
            # Найти refresh для текущего разрешения
            for line in xr.split("\n"):
                # строка вида "   1920x1080     60.00*+  59.94  ..."
                if "*" in line:
                    ref_match = re.search(r'(\d+\.\d+)\*', line)
                    if ref_match and displays:
                        for d in displays:
                            if d["connected"] and not d["refresh"]:
                                d["refresh"] = ref_match.group(1) + " Hz"
                                break
        else:
            # fallback: /sys/class/drm
            drm_path = Path("/sys/class/drm")
            if drm_path.exists():
                for card_dir in sorted(drm_path.iterdir()):
                    status_file = card_dir / "status"
                    if status_file.exists():
                        status = _read_sys(str(status_file))
                        modes_file = card_dir / "modes"
                        mode = ""
                        if modes_file.exists():
                            mode = _read_sys(str(modes_file)).split("\n")[0] if _read_sys(str(modes_file)) else ""
                        displays.append({
                            "name": card_dir.name,
                            "connected": status == "connected",
                            "resolution": mode,
                            "refresh": "",
                            "primary": False,
                        })

        return [d for d in displays if d["connected"]]

    def get_full_summary(self) -> Dict:
        """
        Полная сводка оборудования.

        Returns:
            {cpu, gpu, ram, disks, boot_mode, battery, sensors, usb_count, pci_gpu, displays}
        """
        cpu = self.get_cpu_info()
        gpu = self.get_gpu_info()
        ram = self.get_ram_info()
        disks = self.get_disk_info()
        boot = self.get_boot_mode()
        battery = self.get_battery_info()
        sensors = self.get_sensors()
        usb = self.get_usb_devices()
        displays = self.get_display_info()

        return {
            "cpu": cpu,
            "gpu": gpu,
            "ram": ram,
            "disks": disks,
            "boot_mode": boot,
            "battery": battery,
            "sensors": sensors,
            "usb_count": len(usb),
            "displays": displays,
        }

    def format_summary(self) -> str:
        """Красивый текстовый отчёт."""
        cpu = self.get_cpu_info()
        gpu = self.get_gpu_info()
        ram = self.get_ram_info()
        disks = self.get_disk_info()
        boot = self.get_boot_mode()
        battery = self.get_battery_info()
        sensors = self.get_sensors()

        lines = ["═══ Оборудование ═══", ""]

        # CPU
        model = cpu.get("model", "N/A")
        cores = cpu.get("cores", "?")
        threads = cpu.get("threads", "?")
        lines.append(f"CPU: {model} ({cores}C/{threads}T)")

        # GPU
        for g in gpu:
            # HardwareScanner uses 'name', fallback uses 'model'
            model = g.get('model') or g.get('name', 'N/A')
            # Clean lspci raw output: strip PCI address and bracket codes
            model = re.sub(r'^[\da-f:.]+\s+', '', model, flags=re.I)
            model = re.sub(r'\s*\[[^\]]*\]\s*', ' ', model)  # [0300], [AMD/ATI], [1002:15e7]
            model = re.sub(r'\s*\([^)]*\)\s*$', '', model)   # (rev c1)
            model = re.sub(r'VGA compatible controller\s*:?\s*', '', model, flags=re.I)
            model = re.sub(r'3D controller\s*:?\s*', '', model, flags=re.I)
            model = re.sub(r'Display controller\s*:?\s*', '', model, flags=re.I)
            model = re.sub(r',?\s*Inc\.?\s*', ' ', model)
            model = re.sub(r'\s{2,}', ' ', model).strip()
            driver = g.get('driver') or ''
            if not driver:
                # Extract just the driver name from driver_suggestion
                suggestion = g.get('driver_suggestion', '')
                if suggestion:
                    # "amdgpu (рекомендуется, встроен в ядро)" → "amdgpu"
                    driver = suggestion.split('(')[0].split(',')[0].strip()
            if driver:
                lines.append(f"GPU: {model} [{driver}]")
            else:
                lines.append(f"GPU: {model}")

        # RAM
        # HardwareScanner returns total_mb (int), fallback returns total (str)
        total_mb = ram.get('total_mb')
        if total_mb and isinstance(total_mb, (int, float)) and total_mb > 0:
            gb = total_mb / 1024
            total = f"{gb:.1f} ГБ"
        else:
            total = ram.get('total', '?')
        lines.append(f"RAM: {total}")

        # Disks
        for d in disks:
            lines.append(f"Disk: {d.get('name', '?')} — {d.get('size', '?')} ({d.get('type', '?')})")

        # Boot
        boot_mode = boot.get("mode", "unknown") if boot else "unknown"
        lines.append(f"Boot: {boot_mode}")

        # Battery
        if battery:
            pct = battery.get("capacity", "?")
            state = battery.get("status", "?")
            lines.append(f"Battery: {pct}% ({state})")

        # Sensors
        if sensors.get("available"):
            temps = []
            for s in sensors.get("sensors", []):
                for k, v in s.get("values", {}).items():
                    if isinstance(v, dict) and "current" in v:
                        temps.append(f"{s.get('name', '?')}: {v['current']}°C")
                        break
            if temps:
                lines.append(f"Temps: {', '.join(temps[:3])}")

        return "\n".join(lines)

    # ── Fallback-методы (если HardwareScanner недоступен) ──

    def _fallback_cpu(self) -> Dict:
        info: Dict = {"model": "", "cores": 0, "threads": 0, "arch": ""}
        out = _run("lscpu 2>/dev/null")
        for line in out.split("\n"):
            if "Model name" in line or "Имя модели" in line:
                info["model"] = line.split(":", 1)[-1].strip()
            elif line.startswith("CPU(s):") or line.startswith("Процессор(ов):"):
                val = line.split(":", 1)[-1].strip()
                info["threads"] = int(val) if val.isdigit() else 0
            elif "Core(s) per socket" in line or "Ядер на сокет" in line:
                val = line.split(":", 1)[-1].strip()
                info["cores"] = int(val) if val.isdigit() else 0
            elif "Architecture" in line or "Архитектура" in line:
                info["arch"] = line.split(":", 1)[-1].strip()
        return info

    def _fallback_gpu(self) -> List[Dict]:
        lines = _run_lines("lspci 2>/dev/null | grep -iE 'VGA|3D|Display'")
        gpus = []
        for line in lines:
            match = re.search(r':\s+(.*)', line)
            if match:
                gpus.append({"model": match.group(1).strip(), "driver": ""})
        return gpus

    def _fallback_ram(self) -> Dict:
        info: Dict = {"total": "", "available": "", "used": ""}
        mem = _run("free -h 2>/dev/null | grep Mem")
        if mem:
            parts = mem.split()
            if len(parts) >= 4:
                info["total"] = parts[1]
                info["used"] = parts[2]
                info["available"] = parts[3] if len(parts) > 3 else ""
        return info

    def _fallback_battery(self) -> Optional[Dict]:
        bat = Path("/sys/class/power_supply/BAT0")
        if not bat.exists():
            return None
        cap = _read_sys(str(bat / "capacity"))
        status = _read_sys(str(bat / "status"))
        return {
            "capacity": int(cap) if cap.isdigit() else 0,
            "status": status or "Unknown",
        }
