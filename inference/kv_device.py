# -*- coding: utf-8 -*-
"""
Lina Inference — Device selection and management for KV-cache.

Provides automatic hardware detection and optimal device placement
for KV-cache tensors and attention computations.

Device hierarchy (best → worst):
  1. CUDA (discrete NVIDIA GPU) — best for large batch / long context
  2. CUDA (integrated GPU / shared VRAM) — good, avoids CPU↔GPU copies
  3. CPU — fallback, always available

Design principles:
  - All tensors for a single cache live on ONE device (no cross-device ops).
  - Device is selected once at cache creation time.
  - No hidden .cpu() / .cuda() calls in hot paths.
  - On integrated GPUs (shared memory), torch.device("cuda") works
    natively — the memory is physically shared, so "copies" are free.

Usage:
    from lina.inference.kv_device import select_device, DeviceInfo

    device = select_device()                   # auto-detect best device
    device = select_device(prefer_gpu=False)   # force CPU
    device = select_device(device="cuda:1")    # explicit device

    info = device_info()                       # detailed hardware info

Phase 10+ — AI Runtime KV-cache compression.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Union

import torch

logger = logging.getLogger("lina.inference.kv_device")


# ═══════════════════════════════════════════════════════════════════════════════
#  Device Info
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DeviceInfo:
    """Information about the selected compute device.

    Attributes:
        device:       torch.device for tensor allocation.
        name:         human-readable device name (e.g. "NVIDIA RTX 4090").
        is_gpu:       True if device is a GPU (CUDA).
        is_igpu:      True if device has shared/unified memory (iGPU).
        vram_bytes:   total VRAM in bytes (0 for CPU).
        vram_free:    free VRAM in bytes (0 for CPU).
        compute_cap:  CUDA compute capability tuple, e.g. (8, 6). None for CPU.
        supports_bf16: True if device supports bfloat16 natively.
        supports_fp16: True if device supports float16 natively.
    """
    device: torch.device
    name: str = "CPU"
    is_gpu: bool = False
    is_igpu: bool = False
    vram_bytes: int = 0
    vram_free: int = 0
    compute_cap: Optional[tuple] = None
    supports_bf16: bool = False
    supports_fp16: bool = True  # CPU always supports fp16 via software


# ═══════════════════════════════════════════════════════════════════════════════
#  Detection Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _is_integrated_gpu(device_idx: int = 0) -> bool:
    """Heuristic: detect if a CUDA device is an integrated GPU (shared VRAM).

    Integrated GPUs (e.g. Intel Arc in iGPU mode, NVIDIA iGPU on Jetson,
    AMD APUs) typically have:
      - Small VRAM (< 4 GB dedicated, but shared with system RAM)
      - Unified memory architecture

    On systems with shared VRAM, torch.cuda still works — the memory
    is physically the same, so copies between "host" and "device" are
    essentially free (just pointer passing).
    """
    if not torch.cuda.is_available():
        return False
    try:
        props = torch.cuda.get_device_properties(device_idx)
        # Jetson / iGPU: typically < 4GB and integrated flag
        # torch doesn't expose an "integrated" flag directly,
        # but we can check if total memory is suspiciously small
        # or if the device name suggests integrated graphics.
        name_lower = props.name.lower()
        is_integrated = any(kw in name_lower for kw in [
            "tegra", "orin", "xavier",  # NVIDIA Jetson
            "intel", "iris", "uhd",     # Intel iGPU
            "radeon graphics",          # AMD APU
            "vega",                     # AMD APU (older)
        ])
        # Also heuristic: < 2GB VRAM is almost certainly integrated/shared
        if props.total_mem < 2 * 1024 ** 3 and not is_integrated:
            is_integrated = True
        return is_integrated
    except Exception:
        return False


def _get_cuda_info(device_idx: int = 0) -> DeviceInfo:
    """Gather detailed info about a CUDA device."""
    props = torch.cuda.get_device_properties(device_idx)
    device = torch.device(f"cuda:{device_idx}")

    total_mem = props.total_mem
    # Free memory: use memory_reserved to account for PyTorch allocator
    try:
        free_mem = total_mem - torch.cuda.memory_allocated(device_idx)
    except Exception:
        free_mem = total_mem

    cc = (props.major, props.minor)
    # bfloat16 requires compute capability >= 8.0 (Ampere+)
    supports_bf16 = cc >= (8, 0)
    is_igpu = _is_integrated_gpu(device_idx)

    return DeviceInfo(
        device=device,
        name=props.name,
        is_gpu=True,
        is_igpu=is_igpu,
        vram_bytes=total_mem,
        vram_free=free_mem,
        compute_cap=cc,
        supports_bf16=supports_bf16,
        supports_fp16=True,  # All CUDA devices support fp16
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Main API: select_device()
# ═══════════════════════════════════════════════════════════════════════════════

def select_device(
    prefer_gpu: bool = True,
    device: Optional[Union[str, torch.device]] = None,
) -> torch.device:
    """Select the optimal compute device for KV-cache operations.

    Device selection strategy:
      1. If `device` is explicitly provided, use it (user override).
      2. If `prefer_gpu=True` and CUDA is available, use "cuda".
      3. Otherwise, fall back to "cpu".

    On integrated GPUs (shared VRAM), "cuda" maps to the iGPU.
    Data lives in shared memory — no PCIe copies needed.

    Args:
        prefer_gpu: If True (default), prefer GPU over CPU when available.
        device:     Explicit device override (e.g. "cuda:0", "cpu").
                    When provided, prefer_gpu is ignored.

    Returns:
        torch.device — the selected device.

    Examples:
        select_device()                  # auto: cuda if available, else cpu
        select_device(prefer_gpu=False)  # force CPU
        select_device(device="cuda:1")   # explicit GPU index
    """
    # ── Explicit user override ──
    if device is not None:
        try:
            resolved = torch.device(device)
        except RuntimeError:
            logger.warning(
                "Invalid device string '%s'. Falling back to CPU.", device,
            )
            return torch.device("cpu")
        # Validate CUDA is actually available if requested
        if resolved.type == "cuda" and not torch.cuda.is_available():
            logger.warning(
                "CUDA device requested (%s) but not available. Falling back to CPU.",
                resolved,
            )
            return torch.device("cpu")
        logger.info("Device override: %s", resolved)
        return resolved

    # ── Auto-detection ──
    if prefer_gpu and torch.cuda.is_available():
        # Select the default CUDA device (usually cuda:0)
        dev = torch.device("cuda")
        try:
            name = torch.cuda.get_device_name(0)
            igpu = _is_integrated_gpu(0)
            logger.info(
                "Auto-selected device: %s (%s%s)",
                dev, name, " [integrated/shared VRAM]" if igpu else "",
            )
        except Exception:
            logger.info("Auto-selected device: %s", dev)
        return dev

    # ── CPU fallback ──
    logger.info("Using CPU (CUDA not available or not preferred)")
    return torch.device("cpu")


def device_info(
    device: Optional[Union[str, torch.device]] = None,
) -> DeviceInfo:
    """Get detailed information about a device.

    Args:
        device: Device to query. If None, queries the auto-selected device.

    Returns:
        DeviceInfo dataclass with hardware details.
    """
    if device is None:
        device = select_device()
    else:
        device = torch.device(device)

    if device.type == "cuda" and torch.cuda.is_available():
        idx = device.index if device.index is not None else 0
        return _get_cuda_info(idx)

    # CPU info
    return DeviceInfo(
        device=torch.device("cpu"),
        name="CPU",
        is_gpu=False,
        is_igpu=False,
        supports_bf16=True,   # PyTorch supports bfloat16 on CPU
        supports_fp16=True,
    )


def optimal_compute_dtype(
    device: Optional[torch.device] = None,
) -> torch.dtype:
    """Select the best compute dtype for a device.

    Strategy:
      - Ampere+ GPU (cc >= 8.0): bfloat16 (better range, same speed)
      - Older GPU: float16 (native tensor core support)
      - CPU: float32 (fp16/bf16 compute is slower on CPU)

    Args:
        device: Device to select dtype for. If None, auto-detects.

    Returns:
        torch.dtype — recommended compute dtype.
    """
    if device is None:
        device = select_device()

    if device.type == "cuda" and torch.cuda.is_available():
        idx = device.index if device.index is not None else 0
        try:
            cc = torch.cuda.get_device_capability(idx)
            if cc >= (8, 0):
                return torch.bfloat16   # Ampere+: native bf16
            return torch.float16        # Pre-Ampere: native fp16
        except Exception:
            return torch.float16

    # CPU: float16 is fine for storage, but compute in float32
    # (x86 CPUs don't have fp16 ALUs; ARM has some support)
    return torch.float16


def ensure_device(
    tensor: torch.Tensor,
    target_device: torch.device,
) -> torch.Tensor:
    """Move tensor to target device only if necessary (zero-copy when same).

    This is the ONLY place where cross-device movement should happen.
    In the hot path (add/get/attention), tensors should already be on
    the correct device. This function is a safety net for the API boundary.

    On integrated GPUs with shared memory, this is essentially free
    even when "copying" between cpu and cuda.

    Args:
        tensor: input tensor.
        target_device: where it should live.

    Returns:
        tensor on target_device (same object if already there).
    """
    if tensor.device == target_device:
        return tensor
    # Check if both are the same physical device (e.g. cuda:0 == cuda)
    if (tensor.device.type == target_device.type and
            (target_device.index is None or tensor.device.index == target_device.index)):
        return tensor
    return tensor.to(target_device, non_blocking=True)


def format_device_info(info: DeviceInfo) -> str:
    """Format device info for human-readable display."""
    lines = [f"Device: {info.name} ({info.device})"]
    if info.is_gpu:
        vram_gb = info.vram_bytes / (1024 ** 3)
        free_gb = info.vram_free / (1024 ** 3)
        lines.append(f"  VRAM: {vram_gb:.1f} GB total, {free_gb:.1f} GB free")
        if info.is_igpu:
            lines.append("  Type: Integrated GPU (shared memory)")
        else:
            lines.append("  Type: Discrete GPU")
        if info.compute_cap:
            lines.append(f"  Compute Capability: {info.compute_cap[0]}.{info.compute_cap[1]}")
        lines.append(f"  bfloat16: {'yes' if info.supports_bf16 else 'no'}")
    else:
        lines.append("  Type: CPU")
    return "\n".join(lines)
