# -*- coding: utf-8 -*-
"""
Lina Inference — Backend detection (Vulkan / CPU).

Определяет доступность GPU-ускорения через Vulkan:
  1. Проверяет наличие Vulkan runtime (libvulkan.so)
  2. Проверяет llama-cpp-python скомпилирован с LLAMA_VULKAN
  3. Проверяет наличие GPU (VkPhysicalDevice)
  4. Fallback на CPU если Vulkan недоступен

Поддерживает iGPU (AMD APU / Intel UHD / etc.).

Phase 10 — AI Runtime v2.
"""

import os
import logging
import subprocess
import shutil
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger("lina.inference.backend")


# ═══════════════════════════════════════════════════════════
#  Перечисления
# ═══════════════════════════════════════════════════════════

class BackendType(str, Enum):
    """Тип вычислительного бэкенда."""
    VULKAN = "vulkan"       # GPU через Vulkan API
    CPU = "cpu"             # Только CPU (fallback)


class GPUVendor(str, Enum):
    """Производитель GPU."""
    AMD = "amd"
    INTEL = "intel"
    NVIDIA = "nvidia"
    UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════
#  Данные о GPU
# ═══════════════════════════════════════════════════════════

@dataclass
class GPUInfo:
    """Информация об обнаруженном GPU.

    Attributes:
        available: GPU обнаружен и доступен.
        vendor: Производитель GPU.
        name: Название GPU (из vulkaninfo / lspci).
        vram_mb: Видеопамять в МБ (0 = неизвестно, iGPU = shared).
        vulkan_supported: Vulkan runtime присутствует.
        llama_vulkan: llama-cpp скомпилирован с Vulkan.
        recommended_layers: Рекомендованное кол-во слоёв на GPU.
    """
    available: bool = False
    vendor: GPUVendor = GPUVendor.UNKNOWN
    name: str = "Unknown"
    vram_mb: int = 0
    vulkan_supported: bool = False
    llama_vulkan: bool = False
    recommended_layers: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "available": self.available,
            "vendor": self.vendor.value,
            "name": self.name,
            "vram_mb": self.vram_mb,
            "vulkan_supported": self.vulkan_supported,
            "llama_vulkan": self.llama_vulkan,
            "recommended_layers": self.recommended_layers,
        }


# ═══════════════════════════════════════════════════════════
#  Backend конфигурация
# ═══════════════════════════════════════════════════════════

@dataclass
class BackendConfig:
    """Конфигурация бэкенда для llama-cpp.

    Attributes:
        backend: Выбранный бэкенд (vulkan / cpu).
        n_gpu_layers: Количество слоёв модели на GPU.
        n_threads: Количество CPU-потоков.
        gpu_info: Информация о GPU.
        reason: Причина выбора данного бэкенда.
    """
    backend: BackendType = BackendType.CPU
    n_gpu_layers: int = 0
    n_threads: int = 4
    gpu_info: GPUInfo = None
    reason: str = "default"

    def __post_init__(self):
        if self.gpu_info is None:
            self.gpu_info = GPUInfo()

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        return {
            "backend": self.backend.value,
            "n_gpu_layers": self.n_gpu_layers,
            "n_threads": self.n_threads,
            "gpu_info": self.gpu_info.to_dict(),
            "reason": self.reason,
        }


# ═══════════════════════════════════════════════════════════
#  Детектор GPU
# ═══════════════════════════════════════════════════════════

def _check_vulkan_runtime() -> bool:
    """Проверяет наличие Vulkan runtime (libvulkan).

    Returns:
        True если libvulkan доступна в системе.
    """
    # Способ 1: vulkaninfo утилита
    if shutil.which("vulkaninfo"):
        try:
            result = subprocess.run(
                ["vulkaninfo", "--summary"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and "deviceName" in result.stdout:
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Способ 2: проверяем libvulkan.so
    try:
        import ctypes
        ctypes.cdll.LoadLibrary("libvulkan.so.1")
        return True
    except OSError:
        pass

    return False


def _check_llama_vulkan() -> bool:
    """Проверяет, скомпилирован ли llama-cpp-python с Vulkan.

    Returns:
        True если библиотека поддерживает Vulkan backend.
    """
    try:
        import llama_cpp
        # llama-cpp-python с Vulkan поддерживает n_gpu_layers > 0
        # когда LLAMA_VULKAN=ON при сборке
        # Проверяем наличие атрибутов, указывающих на GPU support
        return hasattr(llama_cpp, 'LLAMA_SUPPORTS_GPU_OFFLOAD') or \
               hasattr(llama_cpp, 'llama_supports_gpu_offload')
    except ImportError:
        return False


def _detect_gpu_lspci() -> GPUInfo:
    """Определяет GPU через lspci.

    Returns:
        GPUInfo с данными о GPU (или пустой если не найден).
    """
    info = GPUInfo()

    if not shutil.which("lspci"):
        return info

    try:
        result = subprocess.run(
            ["lspci", "-nn"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return info

        for line in result.stdout.splitlines():
            line_lower = line.lower()
            # VGA compatible controller или Display controller
            if "vga" not in line_lower and "display" not in line_lower:
                continue

            info.available = True
            info.name = line.split(": ", 1)[-1] if ": " in line else line

            # Определяем vendor
            if "amd" in line_lower or "radeon" in line_lower:
                info.vendor = GPUVendor.AMD
            elif "intel" in line_lower:
                info.vendor = GPUVendor.INTEL
            elif "nvidia" in line_lower:
                info.vendor = GPUVendor.NVIDIA

            # Предпочитаем discrete GPU (не Intel iGPU)
            if info.vendor != GPUVendor.INTEL:
                break

    except (subprocess.TimeoutExpired, OSError):
        pass

    return info


def _estimate_gpu_layers(gpu: GPUInfo, model_ram_mb: int) -> int:
    """Рассчитывает рекомендуемое количество GPU-слоёв.

    Для iGPU с shared RAM — ограничиваем слои.
    Для discrete GPU — можно больше.

    Args:
        gpu: Информация о GPU.
        model_ram_mb: Ожидаемое потребление RAM моделью.

    Returns:
        Рекомендуемое количество слоёв (0 = CPU only).
    """
    if not gpu.available or not gpu.vulkan_supported:
        return 0

    # iGPU (Intel / AMD APU) — shared RAM, осторожно
    if gpu.vendor == GPUVendor.INTEL:
        # Intel iGPU: максимум 4 слоя для маленьких моделей, 0 для больших
        if model_ram_mb < 2000:
            return 4  # маленькая модель — часть на iGPU
        return 0      # full — слишком тяжело для iGPU

    if gpu.vendor == GPUVendor.AMD:
        # AMD APU: аналогично Intel iGPU
        if gpu.vram_mb > 0 and gpu.vram_mb >= 4096:
            # Discrete AMD GPU с >=4GB VRAM
            if model_ram_mb < 2000:
                return 32  # маленькая модель — почти вся на GPU
            return 16      # full — часть слоёв
        else:
            # AMD APU (shared)
            if model_ram_mb < 2000:
                return 8
            return 0

    if gpu.vendor == GPUVendor.NVIDIA:
        # NVIDIA: обычно discrete, хорошо работает
        if model_ram_mb < 2000:
            return 32
        return 20

    return 0


# ═══════════════════════════════════════════════════════════
#  Публичный API
# ═══════════════════════════════════════════════════════════

def detect_gpu() -> GPUInfo:
    """Обнаруживает GPU и его возможности.

    Проверяет:
      1. lspci — наличие GPU
      2. Vulkan runtime — libvulkan
      3. llama-cpp Vulkan support — компиляция

    Returns:
        GPUInfo с полной информацией.
    """
    gpu = _detect_gpu_lspci()
    gpu.vulkan_supported = _check_vulkan_runtime()
    gpu.llama_vulkan = _check_llama_vulkan()

    logger.info(
        "GPU detection: available=%s, vendor=%s, name=%s, "
        "vulkan=%s, llama_vulkan=%s",
        gpu.available, gpu.vendor.value, gpu.name,
        gpu.vulkan_supported, gpu.llama_vulkan,
    )

    return gpu


def configure_backend(
    model_ram_mb: int = 1200,
    force_cpu: bool = False,
) -> BackendConfig:
    """Определяет оптимальный бэкенд для inference.

    Стратегия:
      1. Если force_cpu=True → CPU
      2. Если GPU + Vulkan + llama_vulkan → Vulkan
      3. Иначе → CPU (fallback)

    Args:
        model_ram_mb: Ожидаемое потребление RAM моделью.
        force_cpu: Принудительно CPU.

    Returns:
        BackendConfig с оптимальными параметрами.
    """
    if force_cpu:
        return BackendConfig(
            backend=BackendType.CPU,
            reason="force_cpu=True",
        )

    gpu = detect_gpu()

    # Проверяем полную цепочку: GPU + Vulkan + llama compile
    vulkan_ready = (
        gpu.available
        and gpu.vulkan_supported
        and gpu.llama_vulkan
    )

    if vulkan_ready:
        layers = _estimate_gpu_layers(gpu, model_ram_mb)
        gpu.recommended_layers = layers

        if layers > 0:
            return BackendConfig(
                backend=BackendType.VULKAN,
                n_gpu_layers=layers,
                gpu_info=gpu,
                reason=f"Vulkan GPU: {gpu.name}, {layers} layers",
            )

    # Fallback → CPU
    return BackendConfig(
        backend=BackendType.CPU,
        gpu_info=gpu,
        reason="Vulkan не доступен, используется CPU"
        if gpu.available else "GPU не обнаружен",
    )


def format_backend_status(cfg: BackendConfig) -> str:
    """Форматирует статус бэкенда для CLI.

    Args:
        cfg: Конфигурация бэкенда.

    Returns:
        Человекочитаемая строка.
    """
    gpu = cfg.gpu_info
    lines = [
        f"Backend: {cfg.backend.value.upper()}",
        f"  GPU: {gpu.name}" if gpu.available else "  GPU: не обнаружен",
    ]
    if gpu.available:
        lines.append(f"  Vendor: {gpu.vendor.value}")
        lines.append(f"  Vulkan: {'✓' if gpu.vulkan_supported else '✗'}")
        lines.append(f"  llama Vulkan: {'✓' if gpu.llama_vulkan else '✗'}")
        if cfg.n_gpu_layers > 0:
            lines.append(f"  GPU layers: {cfg.n_gpu_layers}")
    lines.append(f"  CPU threads: {cfg.n_threads}")
    lines.append(f"  Reason: {cfg.reason}")
    return "\n".join(lines)
