# -*- coding: utf-8 -*-
"""
Lina Inference — Умная настройка потоков (Thread Tuner).

Автоподбор количества CPU-потоков для llama-cpp:
  1. Определяет физические / логические ядра
  2. Учитывает текущую нагрузку (load average)
  3. Ограничение — не больше 8 потоков (diminishing returns)

Phase 10 — AI Runtime v2.
"""

import os
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger("lina.inference.threading")


# ═══════════════════════════════════════════════════════════
#  Константы
# ═══════════════════════════════════════════════════════════

# Максимум потоков для llama-cpp (diminishing returns после ~8)
MAX_THREADS = 8

# Минимум потоков
MIN_THREADS = 2

# Высокая нагрузка — снижаем потоки
HIGH_LOAD_THRESHOLD = 0.75  # load / cpu_count


# ═══════════════════════════════════════════════════════════
#  Результат настройки
# ═══════════════════════════════════════════════════════════

@dataclass
class ThreadConfig:
    """Результат подбора потоков.

    Attributes:
        n_threads: Рекомендуемое количество потоков.
        cpu_count: Количество логических ядер.
        physical_cores: Количество физических ядер (0 = неизвестно).
        load_avg: Средняя нагрузка (1 мин).
        strategy: Описание стратегии.
        tier: Уровень модели.
    """
    n_threads: int = 4
    cpu_count: int = 4
    physical_cores: int = 0
    load_avg: float = 0.0
    strategy: str = "default"
    tier: str = "full"

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "n_threads": self.n_threads,
            "cpu_count": self.cpu_count,
            "physical_cores": self.physical_cores,
            "load_avg": round(self.load_avg, 2),
            "strategy": self.strategy,
            "tier": self.tier,
        }


# ═══════════════════════════════════════════════════════════
#  Утилиты
# ═══════════════════════════════════════════════════════════

def _get_physical_cores() -> int:
    """Определяет количество физических ядер CPU.

    Returns:
        Количество физических ядер (0 = не удалось определить).
    """
    try:
        # Linux: /sys/devices/system/cpu/cpu*/topology/core_id
        import pathlib
        cores = set()
        cpu_path = pathlib.Path("/sys/devices/system/cpu")
        for cpu_dir in cpu_path.glob("cpu[0-9]*"):
            core_id_file = cpu_dir / "topology" / "core_id"
            if core_id_file.exists():
                cores.add(core_id_file.read_text().strip())
        if cores:
            return len(cores)
    except Exception:
        pass

    # Fallback: lscpu
    try:
        import subprocess
        result = subprocess.run(
            ["lscpu"], capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.splitlines():
            if "Core(s) per socket" in line:
                return int(line.split(":")[-1].strip())
    except Exception:
        pass

    return 0


def _get_load_avg() -> float:
    """Возвращает load average (1 мин).

    Returns:
        Load average (0.0 при ошибке).
    """
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return 0.0


# ═══════════════════════════════════════════════════════════
#  Публичный API
# ═══════════════════════════════════════════════════════════

def optimize_threads(
    tier: str = "full",
    force_threads: Optional[int] = None,
) -> ThreadConfig:
    """Определяет оптимальное количество потоков для inference.

    Стратегия:
      1. force_threads → используем как есть
      2. Базовый расчёт: physical_cores или cpu_count
      3. Ограничение MAX_THREADS=8
      4. Коррекция по нагрузке (если load > 75%)

    Args:
        tier: Тип модели.
        force_threads: Принудительное значение.

    Returns:
        ThreadConfig с оптимальными параметрами.
    """
    cpu_count = os.cpu_count() or 4
    physical = _get_physical_cores()
    load = _get_load_avg()

    config = ThreadConfig(
        cpu_count=cpu_count,
        physical_cores=physical,
        load_avg=load,
        tier=tier,
    )

    # 1. Принудительное значение
    if force_threads is not None and force_threads > 0:
        config.n_threads = min(force_threads, cpu_count)
        config.strategy = f"force={force_threads}"
        logger.info("Thread config: force=%d", config.n_threads)
        return config

    # 2. Базовый расчёт
    # Используем физические ядра (без HT) если известны
    base = physical if physical > 0 else cpu_count

    # llama-cpp лучше работает с физическими ядрами
    # HyperThreading не даёт прироста для GGML
    optimal = min(base, MAX_THREADS)

    # 3. Коррекция по нагрузке
    load_ratio = load / cpu_count if cpu_count > 0 else 0
    if load_ratio > HIGH_LOAD_THRESHOLD:
        # Система нагружена — снижаем потоки
        reduction = max(1, int(optimal * 0.3))
        optimal -= reduction
        config.strategy = (
            f"load_adjusted: base={base}, "
            f"load={load:.1f}/{cpu_count} ({load_ratio:.0%}), "
            f"-{reduction} threads"
        )
    else:
        config.strategy = (
            f"optimal: base={base}"
            + (f" (physical)" if physical > 0 else "")
            + f", load={load:.1f}/{cpu_count}"
        )

    # 5. Ограничения
    config.n_threads = max(MIN_THREADS, optimal)

    logger.info(
        "Thread config: tier=%s, threads=%d (%s)",
        tier, config.n_threads, config.strategy,
    )

    return config


def format_thread_config(cfg: ThreadConfig) -> str:
    """Форматирует конфигурацию потоков для CLI.

    Args:
        cfg: Конфигурация потоков.

    Returns:
        Человекочитаемая строка.
    """
    lines = [
        f"Threads: {cfg.n_threads}",
        f"  CPU count: {cfg.cpu_count} logical"
        + (f", {cfg.physical_cores} physical" if cfg.physical_cores else ""),
        f"  Load avg: {cfg.load_avg:.1f}",
        f"  Tier: {cfg.tier}",
        f"  Strategy: {cfg.strategy}",
    ]
    return "\n".join(lines)
