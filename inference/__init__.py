# -*- coding: utf-8 -*-
"""
Lina Inference — Оптимизация вывода LLM.

Подмодули:
  - backend   : Vulkan/CPU backend detection + конфигурация
  - threading : Умная настройка потоков
  - optimizer : Context window оптимизация
  - cache     : Детерминистический кэш ответов
  - batch     : Группировка запросов

Phase 10 — AI Runtime v2.
"""

__version__ = "0.8.0"

from lina.inference.backend import (
    BackendType,
    GPUVendor,
    GPUInfo,
    BackendConfig,
    detect_gpu,
    configure_backend,
    format_backend_status,
)
from lina.inference.threading import (
    ThreadConfig,
    optimize_threads,
    format_thread_config,
)
from lina.inference.optimizer import (
    ContextConfig,
    estimate_tokens,
    optimize_context,
    format_context_config,
)
from lina.inference.cache import (
    CacheStats,
    CacheEntry,
    InferenceCache,
)
from lina.inference.batch import (
    RequestPriority,
    BatchRequest,
    BatchStats,
    BatchManager,
)
from lina.inference.kv_quantization import (
    polar_encode,
    polar_decode,
    symmetric_quantize,
    symmetric_dequantize,
    pack_bits,
    unpack_bits,
    compress_vector,
    decompress_vector,
    ResidualCompressor,
)
from lina.inference.kv_cache_compressed import (
    KVCacheConfig,
    CompressedKVCache,
    BaselineKVCache,
)
from lina.inference.kv_attention import (
    compressed_attention,
    compressed_attention_fused,
    CompressedKVCacheManager,
)
from lina.inference.kv_device import (
    select_device,
    device_info,
    DeviceInfo,
    ensure_device,
    optimal_compute_dtype,
    format_device_info,
)

__all__ = [
    # backend
    "BackendType", "GPUVendor", "GPUInfo", "BackendConfig",
    "detect_gpu", "configure_backend", "format_backend_status",
    # threading
    "ThreadConfig", "optimize_threads", "format_thread_config",
    # optimizer
    "ContextConfig", "estimate_tokens", "optimize_context",
    "format_context_config",
    # cache
    "CacheStats", "CacheEntry", "InferenceCache",
    # batch
    "RequestPriority", "BatchRequest", "BatchStats", "BatchManager",
    # kv-cache compression
    "polar_encode", "polar_decode",
    "symmetric_quantize", "symmetric_dequantize",
    "pack_bits", "unpack_bits",
    "compress_vector", "decompress_vector",
    "ResidualCompressor",
    "KVCacheConfig", "CompressedKVCache", "BaselineKVCache",
    "compressed_attention", "compressed_attention_fused",
    "CompressedKVCacheManager",
    # device management
    "select_device", "device_info", "DeviceInfo",
    "ensure_device", "optimal_compute_dtype", "format_device_info",
]
