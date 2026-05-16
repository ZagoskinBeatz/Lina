# -*- coding: utf-8 -*-
"""
Lina Inference — Compressed KV-cache.

Drop-in replacement for standard KV-cache that compresses K/V tensors
on-the-fly using TurboQuant-inspired polar quantization + QJL residuals.

Memory savings:
  ──────────────────────────────────────────────────────────────────────
  Standard (fp16)  : 2 × seq_len × num_heads × head_dim × 2 bytes
  Compressed (4-bit): magnitude(2) + packed_dir(D/2) + scale(2)
                    = ~(D/2 + 4) bytes per vector
  With residual    : + proj_packed(m/8) + norm(2)
  ──────────────────────────────────────────────────────────────────────
  Example: D=128, 4-bit, m=64
    Standard : 256 bytes / vector
    Compressed: 64 + 4 = 68 bytes / vector  → 3.8× savings
    + Residual: 68 + 10 = 78 bytes / vector → 3.3× savings
  ──────────────────────────────────────────────────────────────────────
  With 2-bit:
    Compressed: 32 + 4 = 36 bytes / vector  → 7.1× savings
  ──────────────────────────────────────────────────────────────────────

Architecture:
  ┌─────────────────────────────────────────────────────────────────┐
  │                   CompressedKVCache                              │
  │                                                                 │
  │  ┌─────────┐     ┌──────────────┐     ┌─────────────────────┐  │
  │  │ add(k,v)│ ──→ │ polar_encode │ ──→ │ symmetric_quantize  │  │
  │  └─────────┘     │ + pack_bits  │     │ + optional residual │  │
  │                   └──────────────┘     └─────────────────────┘  │
  │                                                                 │
  │  ┌─────────┐     ┌──────────────┐     ┌─────────────────────┐  │
  │  │ get(i)  │ ──→ │ unpack_bits  │ ──→ │ dequantize + polar  │  │
  │  │         │     │ + residual   │     │ decode              │  │
  │  └─────────┘     └──────────────┘     └─────────────────────┘  │
  │                                                                 │
  │  ┌────────────────┐                                             │
  │  │ get_all_keys() │ ──→ batch decompress → [seq, heads, dim]   │
  │  │ get_all_values()│                                            │
  │  └────────────────┘                                             │
  └─────────────────────────────────────────────────────────────────┘

Usage:
    cache = CompressedKVCache(
        max_seq_len=2048,
        num_heads=32,
        head_dim=128,
        bits=4,
        use_residual=True,
    )

    # During generation:
    cache.add(key, value)               # key/value: [num_heads, head_dim]
    k_all, v_all = cache.get_all()      # → [seq_len, num_heads, head_dim]

    # Single token retrieval:
    k_i, v_i = cache.get(i)             # → ([num_heads, head_dim], ...)

    # Memory tracking:
    print(cache.memory_bytes())

Phase 10+ — AI Runtime KV-cache compression.
"""

import logging
import math
from typing import Tuple, Optional

import torch

from lina.inference.kv_quantization import (
    polar_encode,
    polar_decode,
    symmetric_quantize,
    symmetric_dequantize,
    pack_bits,
    unpack_bits,
    ResidualCompressor,
    EPS,
    _qmax,
    fused_unpack_dequant_polar,
    fused_unpack_dequant,
)
from lina.inference.kv_device import (
    select_device,
    ensure_device,
    device_info,
    optimal_compute_dtype,
    DeviceInfo,
)

logger = logging.getLogger("lina.inference.kv_cache_compressed")


# ═══════════════════════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════════════════════

class KVCacheConfig:
    """Configuration for CompressedKVCache.

    Attributes:
        max_seq_len: Maximum sequence length (preallocated).
        num_heads:   Number of attention heads.
        head_dim:    Dimension per head (D).
        bits:        Quantization bit-width (2, 3, or 4).
        block_size:  Block size for block-wise quantization (0 = per-vector).
        use_residual: Whether to store QJL residual correction.
        residual_proj_dim: Projection dimensionality for residual (0 = auto).
        device:      Where to allocate buffers. Accepts str, torch.device,
                     or None for automatic selection via select_device().
        dtype:       Storage dtype (float16 or bfloat16).
        adaptive:    If True, use higher precision for recent tokens.
        adaptive_window: Number of recent tokens kept at full precision.
        key_bits:    Override bits for keys (0 = use `bits`).
        value_bits:  Override bits for values (0 = use `bits`).
        compute_dtype: Dtype for attention/decompression compute.
                       None = auto-select based on device capabilities.
        adaptive_min_seq: Min seq_len before compression kicks in.
    """
    __slots__ = (
        "max_seq_len", "num_heads", "head_dim", "bits", "block_size",
        "use_residual", "residual_proj_dim", "device", "dtype",
        "adaptive", "adaptive_window", "key_bits", "value_bits",
        "compute_dtype", "adaptive_min_seq",
    )

    def __init__(
        self,
        max_seq_len: int = 2048,
        num_heads: int = 32,
        head_dim: int = 128,
        bits: int = 4,
        block_size: int = 0,
        use_residual: bool = True,
        residual_proj_dim: int = 0,
        device: Optional[str] = None,
        dtype: torch.dtype = torch.float16,
        adaptive: bool = False,
        adaptive_window: int = 64,
        key_bits: int = 0,
        value_bits: int = 0,
        compute_dtype: Optional[torch.dtype] = None,
        adaptive_min_seq: int = 128,
    ):
        self.max_seq_len = max_seq_len
        self.num_heads = num_heads
        self.head_dim = head_dim
        self.bits = bits
        self.block_size = block_size
        self.use_residual = use_residual
        self.residual_proj_dim = residual_proj_dim

        # ── Device selection ──
        # Accepts: "cpu", "cuda", "cuda:0", torch.device(...), or None.
        # None triggers automatic detection via select_device().
        if device is None:
            self.device = select_device(prefer_gpu=True)
        elif isinstance(device, torch.device):
            self.device = device
        else:
            self.device = select_device(device=device)

        self.dtype = dtype
        self.adaptive = adaptive
        self.adaptive_window = adaptive_window
        self.key_bits = key_bits if key_bits > 0 else bits
        self.value_bits = value_bits if value_bits > 0 else bits

        # ── Compute dtype ──
        # If not specified, auto-select based on device capabilities.
        # GPU Ampere+ → bfloat16, older GPU → float16, CPU → float16.
        if compute_dtype is not None:
            self.compute_dtype = compute_dtype
        else:
            self.compute_dtype = optimal_compute_dtype(self.device)

        self.adaptive_min_seq = adaptive_min_seq


# ═══════════════════════════════════════════════════════════════════════════════
#  _VectorStore — internal storage for one of K or V
# ═══════════════════════════════════════════════════════════════════════════════

class _VectorStore:
    """Preallocated compressed storage for a set of vectors (keys or values).

    All buffers are contiguous tensors allocated once at init.

    Internal layout (per token position):
      - magnitudes:   [num_heads]       float16   (2 * H bytes)
      - packed_dirs:  [num_heads, P]    uint8     (H * P bytes)
      - scales:       [num_heads, nblk] float16   (2 * H * nblk bytes)
      - (optional) residual_packed: [num_heads, R] uint8
      - (optional) residual_norms:  [num_heads]    float16

    Where:
      H = num_heads
      P = packed_size = ceil(D * bits / 8)   (approx)
      nblk = num blocks for block-wise scale
      R = ceil(proj_dim / 8)
    """

    def __init__(
        self,
        max_seq: int,
        num_heads: int,
        head_dim: int,
        bits: int,
        block_size: int,
        device: torch.device,
        residual: Optional[ResidualCompressor] = None,
    ):
        self.max_seq = max_seq
        self.num_heads = num_heads
        self.head_dim = head_dim
        self.bits = bits
        self.block_size = block_size
        self.device = device
        self.residual = residual

        # Compute packed dimension
        self._packed_dim = self._compute_packed_dim(head_dim, bits)

        # Number of scale blocks
        if block_size > 0:
            self._num_blocks = (head_dim + block_size - 1) // block_size
        else:
            self._num_blocks = 1

        # ── Preallocate buffers ──
        self.magnitudes = torch.zeros(
            max_seq, num_heads,
            dtype=torch.float16, device=device,
        )
        self.packed_dirs = torch.zeros(
            max_seq, num_heads, self._packed_dim,
            dtype=torch.uint8, device=device,
        )
        self.scales = torch.zeros(
            max_seq, num_heads, self._num_blocks,
            dtype=torch.float16, device=device,
        )

        # Residual buffers
        if residual is not None:
            rpd = residual.proj_dim
            self._res_packed_dim = (rpd + 7) // 8
            self.res_packed = torch.zeros(
                max_seq, num_heads, self._res_packed_dim,
                dtype=torch.uint8, device=device,
            )
            self.res_norms = torch.zeros(
                max_seq, num_heads,
                dtype=torch.float16, device=device,
            )
        else:
            self._res_packed_dim = 0
            self.res_packed = None
            self.res_norms = None

    @staticmethod
    def _compute_packed_dim(head_dim: int, bits: int) -> int:
        """Compute the byte-count of the packed direction vector."""
        if bits == 4:
            return (head_dim + 1) // 2
        elif bits == 2:
            return (head_dim + 3) // 4
        elif bits == 3:
            groups = (head_dim + 7) // 8
            return groups * 3
        else:
            raise ValueError(f"Unsupported bits={bits}")

    def store(self, pos: int, vectors: torch.Tensor) -> None:
        """Compress and store vectors at position `pos`.

        Args:
            pos:     sequence position index.
            vectors: shape [num_heads, head_dim] — float16 or float32.
        """
        # 1. Polar decomposition: v → (mag, dir)
        mag, direction = polar_encode(vectors)  # [H], [H, D]

        # 2. Symmetric quantization of direction → packed bits
        q, scales = symmetric_quantize(
            direction, bits=self.bits, block_size=self.block_size,
        )                                                       # [H, D], [H, nblk]
        packed = pack_bits(q, bits=self.bits)                   # [H, P]

        # 3. Write to preallocated buffers
        self.magnitudes[pos] = mag.to(torch.float16)
        self.packed_dirs[pos] = packed
        self.scales[pos] = scales.to(torch.float16)

        # 4. Optional: residual compression
        if self.residual is not None:
            # Reconstruct approximate vector
            q_back = unpack_bits(packed, bits=self.bits, original_n=self.head_dim)
            dir_hat = symmetric_dequantize(
                q_back, scales.to(torch.float16),
                bits=self.bits, block_size=self.block_size,
                target_dtype=torch.float16,
            )
            v_hat = polar_decode(mag.to(torch.float16), dir_hat)

            # Residual
            residual = vectors.to(torch.float16) - v_hat        # [H, D]
            rp, rn = self.residual.compress(residual)           # [H, R], [H]
            self.res_packed[pos] = rp
            self.res_norms[pos] = rn

    def load(self, pos: int) -> torch.Tensor:
        """Decompress and return vectors at position `pos`.

        Returns:
            vectors: shape [num_heads, head_dim] — float16.
        """
        packed = self.packed_dirs[pos]                          # [H, P]
        scales = self.scales[pos]                               # [H, nblk]
        mag = self.magnitudes[pos]                              # [H]

        q = unpack_bits(packed, bits=self.bits, original_n=self.head_dim)
        dir_hat = symmetric_dequantize(
            q, scales,
            bits=self.bits, block_size=self.block_size,
            target_dtype=torch.float16,
        )
        v_hat = polar_decode(mag, dir_hat)                      # [H, D]

        # Add residual correction if available
        if self.residual is not None:
            rp = self.res_packed[pos]
            rn = self.res_norms[pos]
            res_hat = self.residual.decompress(rp, rn)          # [H, D]
            v_hat = v_hat + res_hat

        return v_hat

    def load_range(
        self,
        start: int,
        end: int,
        target_dtype: torch.dtype = torch.float16,
    ) -> torch.Tensor:
        """Batch-decompress positions [start, end).

        Performance notes:
        - Uses fused unpack+dequant+polar when residual is disabled (fast path).
        - All slicing uses contiguous views to maximize memory coalescing.
        - Single flatten+reshape avoids repeated view operations.

        Args:
            start: start position (inclusive).
            end:   end position (exclusive).
            target_dtype: output dtype (float16 / bfloat16 for mixed precision).

        Returns:
            shape [end-start, num_heads, head_dim] in target_dtype.
        """
        n = end - start
        if n == 0:
            return torch.zeros(
                0, self.num_heads, self.head_dim,
                dtype=target_dtype, device=self.device,
            )

        # ── Contiguous batch reads (large sequential access) ──
        packed = self.packed_dirs[start:end]                    # [n, H, P]
        scales = self.scales[start:end]                         # [n, H, nblk]
        mag = self.magnitudes[start:end]                        # [n, H]

        # Flatten batch dims once for vectorized operations
        nH = n * self.num_heads
        flat_packed = packed.reshape(nH, -1)
        flat_scales = scales.reshape(nH, -1)
        flat_mag = mag.reshape(nH)

        # ── Fast path: fused unpack → dequant → polar (no residual) ──
        if self.residual is None:
            v_flat = fused_unpack_dequant_polar(
                flat_packed, flat_scales, flat_mag,
                bits=self.bits,
                block_size=self.block_size,
                head_dim=self.head_dim,
                target_dtype=target_dtype,
            )
            return v_flat.view(n, self.num_heads, self.head_dim)

        # ── Slow path: separate stages + residual correction ──
        q_flat = unpack_bits(flat_packed, bits=self.bits, original_n=self.head_dim)
        dir_flat = symmetric_dequantize(
            q_flat, flat_scales,
            bits=self.bits, block_size=self.block_size,
            target_dtype=target_dtype,
        )
        v_flat = polar_decode(flat_mag.to(target_dtype), dir_flat)

        rp = self.res_packed[start:end].reshape(nH, -1)
        rn = self.res_norms[start:end].reshape(nH)
        res_hat = self.residual.decompress(rp, rn)
        v_flat = v_flat + res_hat.to(target_dtype)

        return v_flat.view(n, self.num_heads, self.head_dim)

    def load_range_raw(
        self,
        start: int,
        end: int,
    ) -> tuple:
        """Return raw compressed buffers for a range (zero-copy slices).

        Used by fused attention to avoid full decompression.

        Returns:
            (packed_dirs, scales, magnitudes) — contiguous slices.
            packed_dirs: [n, H, P], scales: [n, H, nblk], magnitudes: [n, H]
        """
        return (
            self.packed_dirs[start:end],
            self.scales[start:end],
            self.magnitudes[start:end],
        )

    def memory_bytes(self, seq_len: int) -> int:
        """Compute actual memory used for `seq_len` tokens."""
        per_token = (
            self.num_heads * 2                                  # magnitudes (fp16)
            + self.num_heads * self._packed_dim                 # packed dirs
            + self.num_heads * self._num_blocks * 2             # scales (fp16)
        )
        if self.residual is not None:
            per_token += (
                self.num_heads * self._res_packed_dim           # res packed
                + self.num_heads * 2                            # res norms (fp16)
            )
        return per_token * seq_len


# ═══════════════════════════════════════════════════════════════════════════════
#  CompressedKVCache — main API
# ═══════════════════════════════════════════════════════════════════════════════

class CompressedKVCache:
    """Drop-in compressed KV-cache for transformer attention layers.

    Replaces the standard [seq, heads, dim] float16 tensor pair with
    compressed polar-quantized storage.

    Thread safety: NOT thread-safe (designed for single-stream generation).

    Args:
        config: KVCacheConfig with all parameters.
        **kwargs: alternatively, pass parameters directly:
            max_seq_len, num_heads, head_dim, bits, use_residual, device ...
    """

    def __init__(
        self,
        config: Optional[KVCacheConfig] = None,
        **kwargs,
    ):
        if config is None:
            config = KVCacheConfig(**kwargs)
        self.config = config
        self._seq_len = 0

        # Create residual compressors (one each for K and V)
        k_res = v_res = None
        if config.use_residual:
            k_res = ResidualCompressor(
                head_dim=config.head_dim,
                proj_dim=config.residual_proj_dim,
                seed=42,
                device=config.device,
            )
            v_res = ResidualCompressor(
                head_dim=config.head_dim,
                proj_dim=config.residual_proj_dim,
                seed=137,  # Different seed for V residuals
                device=config.device,
            )

        # Create stores for K and V
        self._key_store = _VectorStore(
            max_seq=config.max_seq_len,
            num_heads=config.num_heads,
            head_dim=config.head_dim,
            bits=config.key_bits,
            block_size=config.block_size,
            device=config.device,
            residual=k_res,
        )
        self._val_store = _VectorStore(
            max_seq=config.max_seq_len,
            num_heads=config.num_heads,
            head_dim=config.head_dim,
            bits=config.value_bits,
            block_size=config.block_size,
            device=config.device,
            residual=v_res,
        )

        # Adaptive: keep recent tokens uncompressed
        if config.adaptive:
            self._recent_keys = torch.zeros(
                config.adaptive_window, config.num_heads, config.head_dim,
                dtype=config.dtype, device=config.device,
            )
            self._recent_vals = torch.zeros(
                config.adaptive_window, config.num_heads, config.head_dim,
                dtype=config.dtype, device=config.device,
            )
            self._recent_count = 0
        else:
            self._recent_keys = None
            self._recent_vals = None

        logger.info(
            "CompressedKVCache: max_seq=%d, heads=%d, dim=%d, "
            "k_bits=%d, v_bits=%d, residual=%s, adaptive=%s",
            config.max_seq_len, config.num_heads, config.head_dim,
            config.key_bits, config.value_bits,
            config.use_residual, config.adaptive,
        )

    @property
    def seq_len(self) -> int:
        """Current number of stored tokens."""
        return self._seq_len

    # ──────────────────────────────────────────────────
    #  Core API
    # ──────────────────────────────────────────────────

    def add(self, key: torch.Tensor, value: torch.Tensor) -> None:
        """Add one token's KV to the cache.

        Args:
            key:   shape [num_heads, head_dim] or [1, num_heads, head_dim]
            value: shape [num_heads, head_dim] or [1, num_heads, head_dim]

        Note: If tensors are on a different device than the cache, they are
        moved automatically. For best performance, pre-allocate tensors on
        the same device as the cache (cache.config.device).
        """
        # Squeeze batch dim if present
        if key.dim() == 3:
            key = key.squeeze(0)
        if value.dim() == 3:
            value = value.squeeze(0)

        # ── Device safety: move to cache device if needed ──
        # This is the API boundary — in normal usage, tensors should
        # already be on config.device. ensure_device is a no-op in that case.
        key = ensure_device(key, self.config.device)
        value = ensure_device(value, self.config.device)

        pos = self._seq_len
        if pos >= self.config.max_seq_len:
            logger.warning(
                "KV-cache overflow at pos=%d (max=%d). Dropping oldest.",
                pos, self.config.max_seq_len,
            )
            self._evict_oldest()
            pos = self._seq_len

        if self.config.adaptive:
            # Dynamic adaptive window: scale window size with sequence length.
            # Short context → larger window (less compression).
            # Long context → smaller window (more aggressive compression).
            base_window = self.config.adaptive_window
            min_seq = self.config.adaptive_min_seq
            if self._seq_len < min_seq:
                # Below threshold: no compression at all, full precision.
                w = self._seq_len + 1  # effectively infinite window
            elif self._seq_len < min_seq * 4:
                # Medium context: use configured window.
                w = base_window
            else:
                # Long context: shrink window for aggressive compression.
                w = max(base_window // 2, 16)

            effective_w = min(w, self.config.adaptive_window)  # cap at max

            if self._recent_count < effective_w:
                self._recent_keys[self._recent_count] = key.to(self.config.dtype)
                self._recent_vals[self._recent_count] = value.to(self.config.dtype)
                self._recent_count += 1
            else:
                # Shift window: compress the oldest, add new at end
                oldest_k = self._recent_keys[0].clone()
                oldest_v = self._recent_vals[0].clone()
                self._key_store.store(pos - self._recent_count, oldest_k)
                self._val_store.store(pos - self._recent_count, oldest_v)
                # Shift buffer (avoid per-element copy)
                self._recent_keys[:-1] = self._recent_keys[1:].clone()
                self._recent_vals[:-1] = self._recent_vals[1:].clone()
                self._recent_keys[self._recent_count - 1] = key.to(self.config.dtype)
                self._recent_vals[self._recent_count - 1] = value.to(self.config.dtype)
        else:
            self._key_store.store(pos, key)
            self._val_store.store(pos, value)

        self._seq_len = pos + 1

    def get(self, index: int) -> Tuple[torch.Tensor, torch.Tensor]:
        """Retrieve one token's KV pair.

        Args:
            index: position in the sequence (0-based).

        Returns:
            (key, value) each shape [num_heads, head_dim], float16.
        """
        assert 0 <= index < self._seq_len, f"Index {index} out of range [0, {self._seq_len})"

        if self.config.adaptive and self._recent_count > 0:
            compressed_end = self._seq_len - self._recent_count
            if index >= compressed_end:
                ri = index - compressed_end
                return (
                    self._recent_keys[ri],
                    self._recent_vals[ri],
                )

        k = self._key_store.load(index)
        v = self._val_store.load(index)
        return k, v

    def get_all(self) -> Tuple[torch.Tensor, torch.Tensor]:
        """Retrieve all stored KV tensors (batch decompression).

        Uses compute_dtype from config for output type (mixed precision).

        Returns:
            keys:   shape [seq_len, num_heads, head_dim] in compute_dtype
            values: shape [seq_len, num_heads, head_dim] in compute_dtype
        """
        out_dtype = self.config.compute_dtype
        if self._seq_len == 0:
            shape = (0, self.config.num_heads, self.config.head_dim)
            empty = torch.zeros(shape, dtype=out_dtype, device=self.config.device)
            return empty, empty

        if self.config.adaptive and self._recent_count > 0:
            compressed_len = self._seq_len - self._recent_count
            parts_k = []
            parts_v = []
            if compressed_len > 0:
                parts_k.append(self._key_store.load_range(
                    0, compressed_len, target_dtype=out_dtype,
                ))
                parts_v.append(self._val_store.load_range(
                    0, compressed_len, target_dtype=out_dtype,
                ))
            parts_k.append(self._recent_keys[:self._recent_count].to(out_dtype))
            parts_v.append(self._recent_vals[:self._recent_count].to(out_dtype))
            return torch.cat(parts_k, dim=0), torch.cat(parts_v, dim=0)
        else:
            keys = self._key_store.load_range(
                0, self._seq_len, target_dtype=out_dtype,
            )
            vals = self._val_store.load_range(
                0, self._seq_len, target_dtype=out_dtype,
            )
            return keys, vals

    @property
    def key_store(self) -> '_VectorStore':
        """Direct access to key store (for fused attention)."""
        return self._key_store

    @property
    def val_store(self) -> '_VectorStore':
        """Direct access to value store (for fused attention)."""
        return self._val_store

    @property
    def has_residual(self) -> bool:
        """True if residual compression is enabled."""
        return self.config.use_residual

    def clear(self) -> None:
        """Reset the cache (does not free memory — buffers are reused)."""
        self._seq_len = 0
        if self.config.adaptive:
            self._recent_count = 0

    def _evict_oldest(self) -> None:
        """Simple eviction: shift everything left by half the cache."""
        half = self.config.max_seq_len // 2
        keep = self._seq_len - half
        if keep <= 0:
            self.clear()
            return

        # Shift compressed buffers
        for store in (self._key_store, self._val_store):
            store.magnitudes[:keep] = store.magnitudes[half:self._seq_len].clone()
            store.packed_dirs[:keep] = store.packed_dirs[half:self._seq_len].clone()
            store.scales[:keep] = store.scales[half:self._seq_len].clone()
            if store.res_packed is not None:
                store.res_packed[:keep] = store.res_packed[half:self._seq_len].clone()
                store.res_norms[:keep] = store.res_norms[half:self._seq_len].clone()

        self._seq_len = keep
        logger.info("KV-cache eviction: dropped %d oldest tokens, kept %d", half, keep)

    # ──────────────────────────────────────────────────
    #  Memory accounting
    # ──────────────────────────────────────────────────

    def memory_bytes(self) -> int:
        """Actual memory used by compressed data (not allocated buffers)."""
        n = self._seq_len
        total = (
            self._key_store.memory_bytes(n)
            + self._val_store.memory_bytes(n)
        )
        if self.config.adaptive and self._recent_count > 0:
            total += (
                self._recent_count
                * self.config.num_heads
                * self.config.head_dim
                * 2  # fp16 = 2 bytes
                * 2  # keys + values
            )
        return total

    def memory_bytes_baseline(self) -> int:
        """Memory that standard fp16 KV-cache would use for same seq_len."""
        return (
            self._seq_len
            * self.config.num_heads
            * self.config.head_dim
            * 2   # fp16
            * 2   # keys + values
        )

    def compression_ratio(self) -> float:
        """How much smaller compressed cache is vs baseline."""
        baseline = self.memory_bytes_baseline()
        if baseline == 0:
            return 1.0
        return baseline / max(self.memory_bytes(), 1)

    def stats(self) -> dict:
        """Return cache statistics as a dict."""
        return {
            "seq_len": self._seq_len,
            "max_seq_len": self.config.max_seq_len,
            "compressed_bytes": self.memory_bytes(),
            "baseline_bytes": self.memory_bytes_baseline(),
            "compression_ratio": round(self.compression_ratio(), 2),
            "key_bits": self.config.key_bits,
            "value_bits": self.config.value_bits,
            "use_residual": self.config.use_residual,
            "adaptive": self.config.adaptive,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  BaselineKVCache — for benchmarking comparison
# ═══════════════════════════════════════════════════════════════════════════════

class BaselineKVCache:
    """Standard uncompressed KV-cache (preallocated fp16 tensors).

    Same API as CompressedKVCache for fair benchmarking.
    """

    def __init__(
        self,
        max_seq_len: int = 2048,
        num_heads: int = 32,
        head_dim: int = 128,
        device: Optional[str] = None,
    ):
        self.max_seq_len = max_seq_len
        self.num_heads = num_heads
        self.head_dim = head_dim
        # Device: None = auto-detect, str = explicit
        if device is None:
            self.device = select_device(prefer_gpu=True)
        elif isinstance(device, torch.device):
            self.device = device
        else:
            self.device = select_device(device=device)
        self._seq_len = 0

        self._keys = torch.zeros(
            max_seq_len, num_heads, head_dim,
            dtype=torch.float16, device=self.device,
        )
        self._vals = torch.zeros(
            max_seq_len, num_heads, head_dim,
            dtype=torch.float16, device=self.device,
        )

    @property
    def seq_len(self) -> int:
        return self._seq_len

    def add(self, key: torch.Tensor, value: torch.Tensor) -> None:
        if key.dim() == 3:
            key = key.squeeze(0)
        if value.dim() == 3:
            value = value.squeeze(0)
        key = ensure_device(key, self.device)
        value = ensure_device(value, self.device)
        self._keys[self._seq_len] = key.to(torch.float16)
        self._vals[self._seq_len] = value.to(torch.float16)
        self._seq_len += 1

    def get(self, index: int) -> Tuple[torch.Tensor, torch.Tensor]:
        return self._keys[index], self._vals[index]

    def get_all(self) -> Tuple[torch.Tensor, torch.Tensor]:
        return self._keys[:self._seq_len], self._vals[:self._seq_len]

    def clear(self) -> None:
        self._seq_len = 0

    def memory_bytes(self) -> int:
        return self._seq_len * self.num_heads * self.head_dim * 2 * 2

    def memory_bytes_baseline(self) -> int:
        return self.memory_bytes()

    def compression_ratio(self) -> float:
        return 1.0

    def stats(self) -> dict:
        return {
            "seq_len": self._seq_len,
            "max_seq_len": self.max_seq_len,
            "compressed_bytes": self.memory_bytes(),
            "baseline_bytes": self.memory_bytes(),
            "compression_ratio": 1.0,
        }
