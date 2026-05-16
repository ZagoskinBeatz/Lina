"""
Lina — Модуль мониторинга системных ресурсов.

Отслеживание CPU, RAM, процессов для контроля нагрузки.
Мониторинг LLM-процессов и предотвращение перегрузки.
"""

import os
import time
import threading
from typing import Optional

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


class SystemMonitor:
    """
    Мониторинг системных ресурсов.

    Использует psutil для получения данных о CPU, RAM, процессах.
    Если psutil недоступен, предоставляет базовую информацию через /proc.

    Расширения:
    - Мониторинг LLM процессов (llama, python)
    - Предотвращение перегрузки (overload guard)
    - Фоновый watchdog
    """

    def __init__(self):
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_running = False
        self._overload_callback = None
        self._llm_pid: Optional[int] = None

    def get_system_info(self) -> dict:
        """Возвращает общую информацию о системе."""
        if PSUTIL_AVAILABLE:
            return self._get_info_psutil()
        return self._get_info_proc()

    def get_cpu_usage(self) -> float:
        """Возвращает текущую загрузку CPU в процентах."""
        if PSUTIL_AVAILABLE:
            return psutil.cpu_percent(interval=0.5)
        return self._read_cpu_proc()

    def get_memory_usage(self) -> dict:
        """Возвращает информацию об использовании RAM."""
        if PSUTIL_AVAILABLE:
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            return {
                "total_mb": round(mem.total / 1024 / 1024),
                "used_mb": round(mem.used / 1024 / 1024),
                "available_mb": round(mem.available / 1024 / 1024),
                "percent": mem.percent,
                "swap_total_mb": round(swap.total / 1024 / 1024),
                "swap_used_mb": round(swap.used / 1024 / 1024),
                "swap_percent": swap.percent,
            }
        return self._read_memory_proc()

    def get_top_processes(self, n: int = 5) -> list:
        """Возвращает топ-N процессов по потреблению RAM."""
        if not PSUTIL_AVAILABLE:
            return [{"info": "psutil не установлен, информация о процессах недоступна"}]

        processes = []
        for proc in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
            try:
                info = proc.info
                mem = info.get("memory_info")
                processes.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "memory_mb": round(mem.rss / 1024 / 1024, 1) if mem else 0,
                    "cpu_percent": info.get("cpu_percent", 0),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Сортируем по RAM (убывающая)
        processes.sort(key=lambda p: p["memory_mb"], reverse=True)
        return processes[:n]

    def check_resources_ok(self, max_ram_mb: int = 0, max_cpu: int = 0) -> dict:
        """
        Проверяет, достаточно ли свободных ресурсов.

        Args:
            max_ram_mb: Сколько RAM потребуется (MB). 0 = не проверять.
            max_cpu: Допустимая загрузка CPU (%). 0 = не проверять.

        Returns:
            dict с результатом проверки.
        """
        result = {"ok": True, "warnings": []}

        mem = self.get_memory_usage()
        cpu = self.get_cpu_usage()

        if max_ram_mb and mem.get("available_mb", 0) < max_ram_mb:
            result["ok"] = False
            result["warnings"].append(
                f"Недостаточно RAM: доступно {mem['available_mb']} MB, "
                f"требуется {max_ram_mb} MB"
            )

        if max_cpu and cpu > max_cpu:
            result["warnings"].append(
                f"Высокая загрузка CPU: {cpu}% (лимит: {max_cpu}%)"
            )

        result["memory"] = mem
        result["cpu_percent"] = cpu
        return result

    def format_status(self) -> str:
        """Форматирует текущий статус системы в читаемую строку."""
        mem = self.get_memory_usage()
        cpu = self.get_cpu_usage()

        lines = [
            "╔══════════════════════════════════════╗",
            "║       Состояние системы              ║",
            "╠══════════════════════════════════════╣",
            f"║  CPU:  {cpu:5.1f}%                        ║",
            f"║  RAM:  {mem.get('used_mb', '?')} / {mem.get('total_mb', '?')} MB "
            f"({mem.get('percent', '?')}%)    ║",
            f"║  Свободно: {mem.get('available_mb', '?')} MB               ║",
            f"║  Swap: {mem.get('swap_used_mb', '?')} / {mem.get('swap_total_mb', '?')} MB "
            f"({mem.get('swap_percent', '?')}%)    ║",
            "╚══════════════════════════════════════╝",
        ]
        return "\n".join(lines)

    # ── LLM-мониторинг ──

    def find_llm_processes(self) -> list:
        """Находит процессы LLM (llama, python с llama)."""
        if not PSUTIL_AVAILABLE:
            return []

        llm_procs = []
        keywords = ["llama", "gguf", "llm", "transformers"]

        for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "cpu_percent"]):
            try:
                info = proc.info
                name = (info.get("name") or "").lower()
                cmdline = " ".join(info.get("cmdline") or []).lower()

                is_llm = any(kw in name or kw in cmdline for kw in keywords)
                if is_llm:
                    mem = info.get("memory_info")
                    llm_procs.append({
                        "pid": info["pid"],
                        "name": info["name"],
                        "memory_mb": round(mem.rss / 1024 / 1024, 1) if mem else 0,
                        "cpu_percent": info.get("cpu_percent", 0),
                        "cmdline": " ".join(info.get("cmdline") or [])[:120],
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return llm_procs

    def set_llm_pid(self, pid: int) -> None:
        """Регистрирует PID текущего LLM-процесса."""
        self._llm_pid = pid

    def get_llm_memory_mb(self) -> float:
        """Получает потребление RAM LLM-процессом."""
        if not PSUTIL_AVAILABLE or not self._llm_pid:
            return 0.0
        try:
            proc = psutil.Process(self._llm_pid)
            return round(proc.memory_info().rss / 1024 / 1024, 1)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self._llm_pid = None
            return 0.0

    def is_overloaded(self, ram_threshold_pct: float = 90.0, cpu_threshold: float = 95.0) -> tuple:
        """
        Проверяет, перегружена ли система.

        Returns:
            (overloaded: bool, reasons: list[str])
        """
        reasons = []
        mem = self.get_memory_usage()
        cpu = self.get_cpu_usage()

        if mem.get("percent", 0) > ram_threshold_pct:
            reasons.append(f"RAM: {mem['percent']}% > {ram_threshold_pct}%")

        if cpu > cpu_threshold:
            reasons.append(f"CPU: {cpu}% > {cpu_threshold}%")

        if mem.get("swap_percent", 0) > 80:
            reasons.append(f"Swap: {mem['swap_percent']}% > 80%")

        return bool(reasons), reasons

    # ── Watchdog ──

    def start_watchdog(
        self,
        interval: int = 30,
        on_overload=None,
    ) -> None:
        """
        Запускает фоновый watchdog для мониторинга.

        Args:
            interval: Интервал проверки (сек).
            on_overload: Callback при перегрузке (принимает список причин).
        """
        if self._watchdog_running:
            return

        self._overload_callback = on_overload
        self._watchdog_running = True

        def _watch():
            while self._watchdog_running:
                try:
                    overloaded, reasons = self.is_overloaded()
                    if overloaded and self._overload_callback:
                        self._overload_callback(reasons)
                except Exception as e:
                    logger.error("Watchdog error: %s", e, exc_info=True)
                time.sleep(interval)

        self._watchdog_thread = threading.Thread(target=_watch, daemon=True, name="lina-watchdog")
        self._watchdog_thread.start()

    def stop_watchdog(self) -> None:
        """Останавливает watchdog."""
        self._watchdog_running = False
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=max(getattr(self, '_watchdog_interval', 5), 2) + 2)
            self._watchdog_thread = None

    def format_extended_status(self) -> str:
        """Расширенный статус с LLM-процессами."""
        base = self.format_status()

        llm_procs = self.find_llm_processes()
        if llm_procs:
            lines = [base, "", "  LLM процессы:"]
            for p in llm_procs:
                lines.append(f"    PID {p['pid']}: {p['name']} — {p['memory_mb']} MB RAM")
        else:
            lines = [base, "", "  LLM процессы: не обнаружены"]

        overloaded, reasons = self.is_overloaded()
        if overloaded:
            lines.append(f"  ⚠ ПЕРЕГРУЗКА: {', '.join(reasons)}")

        return "\n".join(lines)

    # ── Fallback через /proc (если psutil недоступен) ──

    def _get_info_psutil(self) -> dict:
        """Информация через psutil."""
        return {
            "cpu_count": psutil.cpu_count(),
            "cpu_freq": psutil.cpu_freq()._asdict() if psutil.cpu_freq() else {},
            "memory": self.get_memory_usage(),
            "cpu_percent": self.get_cpu_usage(),
            "boot_time": psutil.boot_time(),
        }

    def _get_info_proc(self) -> dict:
        """Базовая информация через /proc для Linux."""
        info = {
            "cpu_count": os.cpu_count(),
            "memory": self.get_memory_usage(),
        }
        return info

    def _read_cpu_proc(self) -> float:
        """Чтение загрузки CPU из /proc/loadavg."""
        try:
            with open("/proc/loadavg", "r") as f:
                load = float(f.read().split()[0])
                cpu_count = os.cpu_count() or 1
                return min(100.0, (load / cpu_count) * 100)
        except (FileNotFoundError, ValueError):
            return 0.0

    def _read_memory_proc(self) -> dict:
        """Чтение информации о памяти из /proc/meminfo."""
        mem = {}
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split()
                    key = parts[0].rstrip(":")
                    value_kb = int(parts[1])
                    if key == "MemTotal":
                        mem["total_mb"] = value_kb // 1024
                    elif key == "MemAvailable":
                        mem["available_mb"] = value_kb // 1024
                    elif key == "SwapTotal":
                        mem["swap_total_mb"] = value_kb // 1024
                    elif key == "SwapFree":
                        mem["swap_used_mb"] = (
                            mem.get("swap_total_mb", 0) - value_kb // 1024
                        )

            total = mem.get("total_mb", 1)
            avail = mem.get("available_mb", 0)
            mem["used_mb"] = total - avail
            mem["percent"] = round((mem["used_mb"] / total) * 100, 1) if total else 0
            mem["swap_percent"] = (
                round((mem.get("swap_used_mb", 0) / mem.get("swap_total_mb", 1)) * 100, 1)
                if mem.get("swap_total_mb", 0) > 0
                else 0
            )
        except (FileNotFoundError, ValueError, KeyError):
            mem = {
                "total_mb": 0, "used_mb": 0, "available_mb": 0, "percent": 0,
                "swap_total_mb": 0, "swap_used_mb": 0, "swap_percent": 0,
            }
        return mem
