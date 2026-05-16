# -*- coding: utf-8 -*-
"""
Lina Inference — Compressed-KV attention integration.

Provides utilities to integrate CompressedKVCache into transformer
attention computations:

  1. compressed_attention()        — standard attention with decompressed KV.
  2. compressed_attention_fused()  — fused attention that decompresses in
     blocks, avoiding full materialization of the KV-cache.
  3. CompressedKVCacheManager      — per-layer cache manager.

Performance hierarchy (best → worst throughput):
  compressed_attention_fused()  ←  partial decompression, ~2× less peak memory
  compressed_attention()        ←  full decompression, simple & correct

The fused variant is the key optimization: instead of decompressing all
seq_len tokens into [seq_len, heads, dim] and then computing attention,
it iterates over blocks of tokens (e.g. 64), decompresses each block,
computes partial attention scores, and accumulates — never materializing
the full decompressed tensor.

This reduces:
  * Peak memory:  O(block_size × H × D) instead of O(seq_len × H × D)
  * Memory bandwidth: each block is streamed from packed→compute→discard

Phase 10+ — AI Runtime KV-cache compression.
"""

import logging
import math
from typing import Tuple, Optional, List

import torch
import torch.nn.functional as F

from lina.inference.kv_cache_compressed import (
    CompressedKVCache,
    KVCacheConfig,
    BaselineKVCache,
)
from lina.inference.kv_quantization import (
    fused_unpack_dequant_polar,
    fused_unpack_dequant,
    unpack_bits,
    symmetric_dequantize,
    polar_decode,
)
from lina.inference.kv_device import ensure_device, select_device

logger = logging.getLogger("lina.inference.kv_attention")


# ═══════════════════════════════════════════════════════════════════════════════
#  Standard Compressed Attention (full decompression)
# ═══════════════════════════════════════════════════════════════════════════════

def compressed_attention(
    query: torch.Tensor,
    keys: torch.Tensor,
    values: torch.Tensor,
    num_heads: int,
    head_dim: int,
    mask: Optional[torch.Tensor] = None,
    scale: Optional[float] = None,
) -> torch.Tensor:
    """Scaled dot-product attention with decompressed KV-cache tensors.

    This function expects keys/values already decompressed from the
    CompressedKVCache (via cache.get_all()).

    Args:
        query:     [batch, num_heads, 1, head_dim] or [num_heads, head_dim]
        keys:      [seq_len, num_heads, head_dim]
        values:    [seq_len, num_heads, head_dim]
        num_heads: number of attention heads.
        head_dim:  dimension per head.
        mask:      optional broadcastable mask.
        scale:     attention scale factor (default: 1/√head_dim).

    Returns:
        output: [num_heads, head_dim]
    """
    if scale is None:
        scale = 1.0 / math.sqrt(head_dim)

    # ── Device alignment: move all tensors to query's device ──
    target_device = query.device
    keys = ensure_device(keys, target_device)
    values = ensure_device(values, target_device)

    # Normalize shapes to [1, num_heads, ?, head_dim]
    if query.dim() == 2:
        q = query.unsqueeze(0).unsqueeze(2)
    elif query.dim() == 3:
        q = query.unsqueeze(2)
    else:
        q = query

    k = keys.permute(1, 0, 2).unsqueeze(0)     # [1, H, S, D]
    v = values.permute(1, 0, 2).unsqueeze(0)    # [1, H, S, D]

    # Attention scores: [1, H, 1, D] @ [1, H, D, S] → [1, H, 1, S]
    attn_weights = torch.matmul(q, k.transpose(-2, -1)) * scale

    if mask is not None:
        attn_weights = attn_weights + mask

    attn_weights = F.softmax(attn_weights, dim=-1, dtype=torch.float32)
    attn_weights = attn_weights.to(v.dtype)

    output = torch.matmul(attn_weights, v)
    output = output.squeeze(0).squeeze(-2)
    return output


# ═══════════════════════════════════════════════════════════════════════════════
#  Fused Compressed Attention (partial decompression in blocks)
# ═══════════════════════════════════════════════════════════════════════════════

def compressed_attention_fused(
    query: torch.Tensor,
    cache: CompressedKVCache,
    mask: Optional[torch.Tensor] = None,
    scale: Optional[float] = None,
    block_size: int = 64,
) -> torch.Tensor:
    """Fused attention that decompresses KV-cache in blocks.

    Instead of decompressing the entire KV-cache into a [seq, H, D] tensor
    and then computing attention, this function:

      1. Iterates over the sequence in blocks of `block_size` tokens.
      2. For each block:
         a. Decompresses packed_dirs → directions → vectors (fused).
         b. Computes Q·K^T for the block → partial logits.
      3. Uses the log-sum-exp trick to stably combine softmax across blocks.
      4. Accumulates weighted V sums incrementally.

    Memory savings: peak decompressed memory is O(block_size × H × D)
    instead of O(seq_len × H × D). For seq_len=4096 and block_size=64,
    this is a 64× reduction in peak decompressed tensor size.

    The log-sum-exp accumulation ensures numerical stability identical
    to standard softmax attention.

    Args:
        query:      [num_heads, head_dim] — query for current token.
        cache:      CompressedKVCache with stored K/V.
        mask:       optional [seq_len] additive mask (e.g., -inf for masked).
        scale:      attention scale (default: 1/√head_dim).
        block_size: number of tokens to decompress at a time (tune for
                    L2 cache / shared memory size; 64 is good default).

    Returns:
        output: [num_heads, head_dim] — attention output.
    """
    seq_len = cache.seq_len
    if seq_len == 0:
        return torch.zeros_like(query)

    num_heads = cache.config.num_heads
    head_dim = cache.config.head_dim
    compute_dtype = cache.config.compute_dtype
    device = cache.config.device

    if scale is None:
        scale = 1.0 / math.sqrt(head_dim)

    # Ensure query is on cache device (API boundary).
    query = ensure_device(query, device)
    # Query: [H, D] → [H, D, 1] for batched matmul
    q = query.to(compute_dtype)  # [H, D]

    # ── Handle adaptive window (recent tokens at full precision) ──
    adaptive_keys = None
    adaptive_vals = None
    compressed_len = seq_len
    if cache.config.adaptive and hasattr(cache, '_recent_count') and cache._recent_count > 0:
        compressed_len = seq_len - cache._recent_count
        adaptive_keys = cache._recent_keys[:cache._recent_count].to(compute_dtype)
        adaptive_vals = cache._recent_vals[:cache._recent_count].to(compute_dtype)

    # ── Accumulators for online softmax (log-sum-exp trick) ──
    # m_i = running max of logits (per head)
    # l_i = running sum of exp(logits - m_i) (per head)
    # o_i = running weighted sum of values (per head, per dim)
    m_prev = torch.full((num_heads,), float('-inf'), dtype=torch.float32, device=device)
    l_prev = torch.zeros(num_heads, dtype=torch.float32, device=device)
    o_prev = torch.zeros(num_heads, head_dim, dtype=torch.float32, device=device)

    # ── Process compressed blocks ──
    k_store = cache.key_store
    v_store = cache.val_store
    use_fused = not cache.has_residual

    for blk_start in range(0, compressed_len, block_size):
        blk_end = min(blk_start + block_size, compressed_len)
        blk_n = blk_end - blk_start

        # Decompress this block (fused or standard path)
        if use_fused:
            k_block = k_store.load_range(blk_start, blk_end, target_dtype=compute_dtype)
            v_block = v_store.load_range(blk_start, blk_end, target_dtype=compute_dtype)
        else:
            k_block = k_store.load_range(blk_start, blk_end, target_dtype=compute_dtype)
            v_block = v_store.load_range(blk_start, blk_end, target_dtype=compute_dtype)

        # k_block: [blk_n, H, D] → need scores per head
        # scores[h] = q[h] · k_block[:, h, :].T → [blk_n]
        # Efficient: k_block.permute(1, 0, 2) → [H, blk_n, D]
        # q: [H, D] → [H, 1, D]
        # scores = bmm([H, 1, D], [H, D, blk_n]) → [H, 1, blk_n] → [H, blk_n]
        k_perm = k_block.permute(1, 0, 2)  # [H, blk_n, D]
        v_perm = v_block.permute(1, 0, 2)  # [H, blk_n, D]

        scores = torch.bmm(
            q.unsqueeze(1).float(),           # [H, 1, D]
            k_perm.transpose(1, 2).float(),   # [H, D, blk_n]
        ).squeeze(1) * scale          # [H, blk_n]

        # Apply mask for this block
        if mask is not None:
            scores = scores + mask[blk_start:blk_end].unsqueeze(0)  # broadcast [1, blk_n]

        # ── Online softmax update (numerically stable) ──
        # m_new = max(m_prev, max(scores))
        m_block = scores.max(dim=-1).values  # [H]
        m_new = torch.maximum(m_prev, m_block)

        # Rescale previous accumulator
        exp_prev = torch.exp(m_prev - m_new)     # [H]
        # Current block weights
        exp_scores = torch.exp(scores - m_new.unsqueeze(-1))  # [H, blk_n]

        l_new = l_prev * exp_prev + exp_scores.sum(dim=-1)  # [H]

        # Weighted value sum: [H, blk_n] @ [H, blk_n, D] → [H, D]
        v_weighted = torch.bmm(
            exp_scores.unsqueeze(1),  # [H, 1, blk_n]
            v_perm.float(),           # [H, blk_n, D] — float32 for accumulation
        ).squeeze(1)                  # [H, D]

        o_new = o_prev * exp_prev.unsqueeze(-1) + v_weighted

        m_prev = m_new
        l_prev = l_new
        o_prev = o_new

    # ── Process adaptive (uncompressed) tokens if any ──
    if adaptive_keys is not None and adaptive_keys.shape[0] > 0:
        k_perm = adaptive_keys.permute(1, 0, 2)  # [H, n_adaptive, D]
        v_perm = adaptive_vals.permute(1, 0, 2)   # [H, n_adaptive, D]

        scores = torch.bmm(
            q.unsqueeze(1).float(),
            k_perm.transpose(1, 2).float(),
        ).squeeze(1) * scale

        if mask is not None:
            adaptive_mask = mask[compressed_len:seq_len]
            scores = scores + adaptive_mask.unsqueeze(0)

        m_block = scores.max(dim=-1).values
        m_new = torch.maximum(m_prev, m_block)
        exp_prev = torch.exp(m_prev - m_new)
        exp_scores = torch.exp(scores - m_new.unsqueeze(-1))

        l_new = l_prev * exp_prev + exp_scores.sum(dim=-1)
        v_weighted = torch.bmm(
            exp_scores.unsqueeze(1),
            v_perm.float(),
        ).squeeze(1)

        o_prev = o_prev * exp_prev.unsqueeze(-1) + v_weighted
        l_prev = l_new
        m_prev = m_new

    # ── Final normalization ──
    # output = o / l  (per head)
    output = o_prev / l_prev.unsqueeze(-1).clamp(min=1e-8)

    return output.to(compute_dtype)


# ═══════════════════════════════════════════════════════════════════════════════
#  Multi-layer KV Cache Manager
# ═══════════════════════════════════════════════════════════════════════════════

class CompressedKVCacheManager:
    """Manages CompressedKVCache instances for all transformer layers.

    Args:
        num_layers:  number of transformer layers.
        num_heads:   attention heads per layer.
        head_dim:    dimension per head.
        max_seq_len: maximum sequence length.
        bits:        quantization bit width.
        use_residual: whether to use QJL residual correction.
        device:      torch device (str, torch.device, or None for auto).
        enabled:     if False, uses BaselineKVCache (no compression).
        **kwargs:    additional KVCacheConfig parameters.
    """

    def __init__(
        self,
        num_layers: int,
        num_heads: int,
        head_dim: int,
        max_seq_len: int = 2048,
        bits: int = 4,
        use_residual: bool = True,
        device: Optional[str] = None,
        enabled: bool = True,
        **kwargs,
    ):
        self.num_layers = num_layers
        self.enabled = enabled

        if enabled:
            config = KVCacheConfig(
                max_seq_len=max_seq_len,
                num_heads=num_heads,
                head_dim=head_dim,
                bits=bits,
                use_residual=use_residual,
                device=device,
                **kwargs,
            )
            self._caches: List = [
                CompressedKVCache(config=config)
                for _ in range(num_layers)
            ]
        else:
            self._caches = [
                BaselineKVCache(
                    max_seq_len=max_seq_len,
                    num_heads=num_heads,
                    head_dim=head_dim,
                    device=device,
                )
                for _ in range(num_layers)
            ]

        logger.info(
            "KVCacheManager: %d layers, %s compression, bits=%d",
            num_layers,
            "enabled" if enabled else "disabled",
            bits if enabled else 16,
        )

    def __getitem__(self, layer_idx: int):
        """Get cache for a specific layer."""
        return self._caches[layer_idx]

    def add(self, layer_idx: int, key: torch.Tensor, value: torch.Tensor) -> None:
        """Add KV pair for a specific layer."""
        self._caches[layer_idx].add(key, value)

    def get_all(self, layer_idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        """Get all KV pairs for a specific layer."""
        return self._caches[layer_idx].get_all()

    def attention_fused(
        self,
        layer_idx: int,
        query: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
        block_size: int = 64,
    ) -> torch.Tensor:
        """Run fused attention for a specific layer (no full decompression).

        Only works when compression is enabled (not baseline mode).
        Falls back to standard attention for baseline caches.

        Args:
            layer_idx: transformer layer index.
            query:     [num_heads, head_dim] query tensor.
            mask:      optional additive mask.
            block_size: decompression block size.

        Returns:
            output: [num_heads, head_dim] attention output.
        """
        cache = self._caches[layer_idx]
        if self.enabled and isinstance(cache, CompressedKVCache):
            return compressed_attention_fused(
                query, cache, mask=mask, block_size=block_size,
            )
        else:
            # Baseline: decompress all and use standard attention
            k, v = cache.get_all()
            nh = query.shape[0]
            hd = query.shape[1]
            return compressed_attention(query, k, v, nh, hd, mask=mask)

    def clear(self) -> None:
        """Clear all layer caches."""
        for cache in self._caches:
            cache.clear()

    def total_memory_bytes(self) -> int:
        """Total memory across all layers."""
        return sum(c.memory_bytes() for c in self._caches)

    def total_baseline_bytes(self) -> int:
        """Memory that baseline fp16 would use."""
        return sum(c.memory_bytes_baseline() for c in self._caches)

    def compression_ratio(self) -> float:
        """Overall compression ratio."""
        baseline = self.total_baseline_bytes()
        if baseline == 0:
            return 1.0
        return baseline / max(self.total_memory_bytes(), 1)

    def stats(self) -> dict:
        """Aggregate stats across all layers."""
        layer_stats = [c.stats() for c in self._caches]
        return {
            "num_layers": self.num_layers,
            "enabled": self.enabled,
            "total_compressed_bytes": self.total_memory_bytes(),
            "total_baseline_bytes": self.total_baseline_bytes(),
            "compression_ratio": round(self.compression_ratio(), 2),
            "seq_len": layer_stats[0]["seq_len"] if layer_stats else 0,
            "layers": layer_stats,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  Example integration snippet for HuggingFace-like models
# ═══════════════════════════════════════════════════════════════════════════════

INTEGRATION_EXAMPLE = """
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Example: Integrating CompressedKVCache into a simple transformer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import torch
from lina.inference.kv_cache_compressed import CompressedKVCache, KVCacheConfig
from lina.inference.kv_attention import (
    compressed_attention,
    compressed_attention_fused,
)

# ── Setup ──
num_heads = 32
head_dim = 128
max_seq = 4096

cache = CompressedKVCache(
    max_seq_len=max_seq,
    num_heads=num_heads,
    head_dim=head_dim,
    bits=4,                          # 4-bit quantization (~4x compression)
    use_residual=False,              # disable residual for max speed
    device="cpu",                    # or "cuda"
    compute_dtype=torch.float16,     # mixed precision: compute in fp16
)

# ── Simulated generation loop ──
for step in range(100):
    q = torch.randn(num_heads, head_dim, dtype=torch.float16)
    k = torch.randn(num_heads, head_dim, dtype=torch.float16)
    v = torch.randn(num_heads, head_dim, dtype=torch.float16)

    cache.add(k, v)

    # ── Option A: Fused attention (recommended) ──
    # Does NOT decompress entire KV-cache. Processes in blocks of 64.
    # Peak memory: O(64 * H * D) instead of O(seq_len * H * D).
    attn_fused = compressed_attention_fused(q, cache, block_size=64)

    # ── Option B: Standard attention (full decompression) ──
    # k_all, v_all = cache.get_all()
    # attn_standard = compressed_attention(q, k_all, v_all, num_heads, head_dim)

print(f"Cache: {cache.stats()}")
print(f"Compression ratio: {cache.compression_ratio():.2f}x")
"""
