# -*- coding: utf-8 -*-
"""
Lina Inference — KV-cache quantization primitives.

Implements low-level operations for compressing KV-cache tensors:

  1. Polar factorization   : v → (magnitude, direction)
  2. Symmetric quantization: direction → low-bit integers
  3. Bit packing / unpacking: int8 → packed uint8 (2/3/4 bits)
  4. QJL residual compression: residual → 1-bit random projection

All operations are fully vectorized (no Python loops in hot path).
All tensors stay on the same device (CPU or CUDA).

Math overview
─────────────
Given a vector v ∈ ℝ^d:

  mag   = ‖v‖₂
  dir   = v / (mag + ε)                              # unit sphere
  scale = max(|dir|)                                  # per-vector
  q     = round(dir / scale × (2^(b-1) − 1))         # symmetric quant
  packed = pack_bits(q + offset, bits=b)              # into uint8

Dequantize:
  dir̂  = unpack(packed) × scale / (2^(b-1) − 1)
  v̂    = dir̂ × mag

Residual (QJL-inspired):
  R    = v − v̂
  proj = sign(R @ Φ)   where Φ is a fixed random ±1 matrix
  store proj as packed bits (1-bit per projection dimension)

Reconstruct:
  R̂   ≈ proj @ Φᵀ × (‖R‖ / √m)    (approximate JL recovery)
  v̂   += R̂

References:
  - TurboQuant (Google, 2024): polar quantization for KV-cache
  - QJL (2024): 1-bit JL residual correction for quantized KV
  - Johnson-Lindenstrauss lemma: random projection preserves distances

Phase 10+ — AI Runtime KV-cache compression.
"""

import math
import logging
from typing import Tuple, Optional

import torch

logger = logging.getLogger("lina.inference.kv_quantization")

# ═══════════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════════

EPS = 1e-8  # Numerical stability for division by norms


# ═══════════════════════════════════════════════════════════════════════════════
#  1. POLAR FACTORIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def polar_encode(
    vectors: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """Decompose vectors into magnitude + unit-direction.

    Args:
        vectors: shape [..., D] — arbitrary leading dims, last dim = head_dim.

    Returns:
        magnitudes: shape [...] — L2 norm of each vector.
        directions: shape [..., D] — unit vectors (L2-normalized).

    Example:
        v = torch.randn(32, 128)          # 32 heads, dim=128
        mag, dir = polar_encode(v)         # mag: [32], dir: [32, 128]
        assert torch.allclose(dir.norm(dim=-1), torch.ones(32), atol=1e-5)
    """
    magnitudes = vectors.float().norm(dim=-1)                    # [...,]
    safe_mag = magnitudes.unsqueeze(-1).clamp(min=1e-7)         # fp32-safe EPS
    directions = vectors.float() / safe_mag                     # [..., D]
    # Zero vectors → zero direction (not NaN)
    directions = directions.to(vectors.dtype)
    magnitudes = magnitudes.to(vectors.dtype)
    return magnitudes, directions


def polar_decode(
    magnitudes: torch.Tensor,
    directions: torch.Tensor,
) -> torch.Tensor:
    """Reconstruct vectors from magnitude + direction.

    Args:
        magnitudes: shape [...]
        directions: shape [..., D]

    Returns:
        vectors: shape [..., D]
    """
    return directions * magnitudes.unsqueeze(-1)


# ═══════════════════════════════════════════════════════════════════════════════
#  2. SYMMETRIC LOW-BIT QUANTIZATION
# ═══════════════════════════════════════════════════════════════════════════════

# Supported bit widths and their quantization level counts.
#   bits=2 → levels: {-1, 0, +1}          — 3 levels, stored as {0,1,2}
#   bits=3 → levels: {-3, -2, -1, 0, +1, +2, +3} — 7 levels
#   bits=4 → levels: {-7 ... +7}          — 15 levels

def _qmax(bits: int) -> int:
    """Maximum positive quantization level for given bit width.

    For b bits, we use symmetric range [−(2^(b−1)−1), +(2^(b−1)−1)].
    This wastes one code but avoids asymmetric clipping artifacts.
    """
    return (1 << (bits - 1)) - 1


def symmetric_quantize(
    x: torch.Tensor,
    bits: int = 4,
    block_size: int = 0,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """Quantize floating-point tensor to low-bit signed integers.

    Symmetric quantization: zero maps to zero exactly.

    Args:
        x:          shape [..., D] — values to quantize (typically in [-1, 1]
                    for unit directions, but works for any range).
        bits:       2, 3, or 4.
        block_size: if >0, compute scale per block of this many elements
                    along the last dimension.  If 0, one scale per vector.

    Returns:
        quantized: shape [..., D] — integer values in [0, 2*qmax].
                   Stored as int8 (will be bit-packed later).
                   Offset by +qmax so all values are non-negative.
        scales:    shape [..., num_blocks] — scale factors for dequantization.

    Dequantize:
        x_hat = (quantized - qmax) * scales / qmax
    """
    assert bits in (2, 3, 4), f"Unsupported bit width: {bits}"
    qmax = _qmax(bits)
    D = x.shape[-1]

    if block_size > 0 and D > block_size:
        # Block-wise quantization for better accuracy on long vectors.
        # Reshape last dim: [..., D] → [..., num_blocks, block_size]
        num_blocks = (D + block_size - 1) // block_size
        padded_D = num_blocks * block_size
        if padded_D > D:
            # Pad with zeros (will quantize to zero → no error added)
            pad = x.new_zeros(*x.shape[:-1], padded_D - D)
            x_padded = torch.cat([x, pad], dim=-1)
        else:
            x_padded = x
        x_blocks = x_padded.view(*x.shape[:-1], num_blocks, block_size)

        # Per-block scale: max absolute value
        scales = x_blocks.abs().amax(dim=-1)                   # [..., num_blocks]
        scales = scales.clamp(min=EPS)

        # Quantize within each block
        x_scaled = x_blocks / scales.unsqueeze(-1) * qmax      # in [-qmax, +qmax]
        q = x_scaled.round().clamp(-qmax, qmax).to(torch.int8) # int8

        # Offset to non-negative for packing
        q = (q + qmax).view(*x.shape[:-1], padded_D)
        if padded_D > D:
            q = q[..., :D]  # remove padding

        return q, scales
    else:
        # Per-vector scale
        scales = x.abs().amax(dim=-1)                           # [...]
        scales = scales.clamp(min=EPS)

        x_scaled = x / scales.unsqueeze(-1) * qmax
        q = x_scaled.round().clamp(-qmax, qmax).to(torch.int8)
        q = q + qmax                                           # non-negative offset

        return q, scales.unsqueeze(-1)                          # [..., 1]


def symmetric_dequantize(
    quantized: torch.Tensor,
    scales: torch.Tensor,
    bits: int = 4,
    block_size: int = 0,
    target_dtype: torch.dtype = torch.float16,
) -> torch.Tensor:
    """Dequantize integer tensor back to floating point.

    Args:
        quantized: shape [..., D] — non-negative int8 values (offset by qmax).
        scales:    shape [..., num_blocks] or [..., 1].
        bits:      must match the value used during quantization.
        block_size: must match.
        target_dtype: output dtype.

    Returns:
        x_hat: shape [..., D] — reconstructed values.
    """
    qmax = _qmax(bits)
    D = quantized.shape[-1]

    # Remove offset → signed
    q_signed = quantized.to(torch.float32) - qmax               # [-qmax, +qmax]

    if block_size > 0 and scales.shape[-1] > 1:
        num_blocks = scales.shape[-1]
        padded_D = num_blocks * block_size
        if padded_D > D:
            pad = q_signed.new_zeros(*q_signed.shape[:-1], padded_D - D)
            q_padded = torch.cat([q_signed, pad], dim=-1)
        else:
            q_padded = q_signed
        q_blocks = q_padded.view(*q_signed.shape[:-1], num_blocks, block_size)
        x_blocks = q_blocks / qmax * scales.unsqueeze(-1)
        x_hat = x_blocks.view(*q_signed.shape[:-1], padded_D)
        if padded_D > D:
            x_hat = x_hat[..., :D]
    else:
        x_hat = q_signed / qmax * scales

    return x_hat.to(target_dtype)


# ═══════════════════════════════════════════════════════════════════════════════
#  3. BIT PACKING / UNPACKING
# ═══════════════════════════════════════════════════════════════════════════════

def pack_bits(tensor: torch.Tensor, bits: int) -> torch.Tensor:
    """Pack low-bit integers into uint8.

    Packs `8 // bits` values per byte (for bits ∈ {2, 4}).
    For bits=3, packs 8 values into 3 bytes (24 bits).

    Args:
        tensor: shape [..., N] — non-negative integers in [0, 2^bits - 1].
                Must be int8 or uint8.
        bits:   2, 3, or 4.

    Returns:
        packed: shape [..., packed_N] — uint8.

    The last dimension may have padding zeros if N is not divisible
    by the packing group size.
    """
    assert bits in (2, 3, 4), f"Unsupported bit width: {bits}"
    t = tensor.to(torch.uint8)

    if bits == 4:
        # 2 values per byte: high nibble + low nibble
        N = t.shape[-1]
        if N % 2 != 0:
            t = torch.cat([t, t.new_zeros(*t.shape[:-1], 1)], dim=-1)
            N += 1
        t_even = t[..., 0::2]  # high nibble
        t_odd = t[..., 1::2]   # low nibble
        packed = (t_even << 4) | (t_odd & 0x0F)
        return packed

    elif bits == 2:
        # 4 values per byte
        N = t.shape[-1]
        pad = (4 - N % 4) % 4
        if pad:
            t = torch.cat([t, t.new_zeros(*t.shape[:-1], pad)], dim=-1)
        N_padded = t.shape[-1]
        # Reshape to groups of 4
        t4 = t.view(*t.shape[:-1], N_padded // 4, 4)
        packed = (
            (t4[..., 0] << 6) |
            (t4[..., 1] << 4) |
            (t4[..., 2] << 2) |
            (t4[..., 3])
        )
        return packed

    elif bits == 3:
        # Pack 8 values into 3 bytes (24 bits).
        # Layout: [v0:3][v1:3][v2:3][v3:3][v4:3][v5:3][v6:3][v7:3]
        # Byte0: v0[2:0] v1[2:0] v2[1:0]    = v0<<5 | v1<<2 | v2>>1
        # Byte1: v2[0] v3[2:0] v4[2:0] v5[1:0] = v2<<7 | v3<<4 | v4<<1 | v5>>2
        # Byte2: v5[1:0] v6[2:0] v7[2:0]    = v5<<6 | v6<<3 | v7
        N = t.shape[-1]
        pad = (8 - N % 8) % 8
        if pad:
            t = torch.cat([t, t.new_zeros(*t.shape[:-1], pad)], dim=-1)
        N_padded = t.shape[-1]
        t8 = t.view(*t.shape[:-1], N_padded // 8, 8)

        v = [t8[..., i].to(torch.uint8) for i in range(8)]
        byte0 = (v[0] << 5) | (v[1] << 2) | (v[2] >> 1)
        byte1 = ((v[2] & 1) << 7) | (v[3] << 4) | (v[4] << 1) | (v[5] >> 2)
        byte2 = ((v[5] & 3) << 6) | (v[6] << 3) | v[7]

        packed = torch.stack([byte0, byte1, byte2], dim=-1)
        # Flatten the last two dims: [..., groups, 3] → [..., groups*3]
        packed = packed.view(*t.shape[:-1], -1)
        return packed

    raise ValueError(f"Unsupported bits={bits}")


def unpack_bits(
    packed: torch.Tensor,
    bits: int,
    original_n: int,
) -> torch.Tensor:
    """Unpack uint8 back to low-bit integers.

    Args:
        packed:     shape [..., packed_N] — uint8.
        bits:       2, 3, or 4 — must match pack_bits.
        original_n: the original last-dim size (before padding).

    Returns:
        tensor: shape [..., original_n] — uint8, values in [0, 2^bits - 1].
    """
    assert bits in (2, 3, 4), f"Unsupported bit width: {bits}"
    p = packed.to(torch.uint8)

    if bits == 4:
        high = (p >> 4) & 0x0F
        low = p & 0x0F
        # Interleave: high[0], low[0], high[1], low[1], ...
        out = torch.stack([high, low], dim=-1).view(*p.shape[:-1], -1)
        return out[..., :original_n]

    elif bits == 2:
        v0 = (p >> 6) & 0x03
        v1 = (p >> 4) & 0x03
        v2 = (p >> 2) & 0x03
        v3 = p & 0x03
        out = torch.stack([v0, v1, v2, v3], dim=-1).view(*p.shape[:-1], -1)
        return out[..., :original_n]

    elif bits == 3:
        # Reverse of pack_bits for 3-bit.
        # Each group of 3 bytes → 8 values.
        total_bytes = p.shape[-1]
        num_groups = total_bytes // 3
        p3 = p.view(*p.shape[:-1], num_groups, 3)
        b0, b1, b2 = p3[..., 0], p3[..., 1], p3[..., 2]

        v0 = (b0 >> 5) & 0x07
        v1 = (b0 >> 2) & 0x07
        v2 = ((b0 & 0x03) << 1) | ((b1 >> 7) & 0x01)
        v3 = (b1 >> 4) & 0x07
        v4 = (b1 >> 1) & 0x07
        v5 = ((b1 & 0x01) << 2) | ((b2 >> 6) & 0x03)
        v6 = (b2 >> 3) & 0x07
        v7 = b2 & 0x07

        out = torch.stack([v0, v1, v2, v3, v4, v5, v6, v7], dim=-1)
        out = out.view(*p.shape[:-1], num_groups * 8)
        return out[..., :original_n]

    raise ValueError(f"Unsupported bits={bits}")


# ═══════════════════════════════════════════════════════════════════════════════
#  4. QJL-INSPIRED RESIDUAL COMPRESSION
# ═══════════════════════════════════════════════════════════════════════════════
#
# Johnson-Lindenstrauss lemma: a random projection from ℝ^D → ℝ^m
# preserves distances up to (1 ± ε) with m = O(log(n)/ε²).
#
# Simplified approach (QJL):
#   1. Fix a random ±1 matrix Φ ∈ {-1,+1}^(D×m), generated from a seed.
#   2. Project residual: z = sign(R · Φ)  → m-bit vector.
#   3. Store z as packed bits.
#   4. Approximate reconstruction:  R̂ ≈ z · Φᵀ × (‖R‖ / √m)
#
# The scalar ‖R‖/√m is the expected magnitude of each coordinate after
# random projection with a Rademacher matrix.

class ResidualCompressor:
    """Compresses quantization residuals using 1-bit random projection.

    The projection matrix Φ is a fixed Rademacher matrix (±1) generated
    from a deterministic seed, so it doesn't need to be stored.

    Memory cost per vector: m / 8 bytes  +  2 bytes (residual norm, float16).
    With m=64 (typical): 8 + 2 = 10 bytes per vector.

    Args:
        head_dim:  D — dimensionality of KV vectors.
        proj_dim:  m — projection dimensionality (trade memory vs accuracy).
                   Higher m → better reconstruction, more memory.
                   Default m = D//2 gives a good balance.
        seed:      RNG seed for reproducible Φ.
        device:    torch device.
    """

    def __init__(
        self,
        head_dim: int,
        proj_dim: int = 0,
        seed: int = 42,
        device: torch.device = torch.device("cpu"),
    ):
        self.head_dim = head_dim
        self.proj_dim = proj_dim if proj_dim > 0 else max(head_dim // 2, 16)
        self.device = device

        # Generate Rademacher matrix Φ ∈ {-1, +1}^(D × m).
        # Using a Generator for deterministic behavior.
        gen = torch.Generator(device="cpu").manual_seed(seed)
        # Bernoulli(0.5) → {0, 1} → *2 - 1 → {-1, +1}
        phi_cpu = (torch.randint(
            0, 2, (head_dim, self.proj_dim),
            generator=gen, dtype=torch.int8,
        ) * 2 - 1).to(torch.float16)
        self._phi = phi_cpu.to(device)                          # [D, m]

        # Precompute Φᵀ / √m for reconstruction
        self._phi_t_scaled = self._phi.t() / math.sqrt(self.proj_dim)  # [m, D]

        logger.debug(
            "ResidualCompressor: D=%d, m=%d, Φ memory=%.1f KB",
            head_dim, self.proj_dim,
            self._phi.numel() * 2 / 1024,
        )

    def compress(
        self,
        residual: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """Compress residual vectors to 1-bit projections.

        Args:
            residual: shape [..., D] — quantization residual.

        Returns:
            proj_packed: shape [..., ceil(m/8)] — packed 1-bit projections (uint8).
            res_norms:   shape [...] — L2 norms of residuals (float16).
        """
        # Project: z_float = R · Φ  →  shape [..., m]
        z_float = residual.to(torch.float16) @ self._phi        # [..., m]

        # 1-bit quantization: sign(z) → {0, 1}
        z_bits = (z_float >= 0).to(torch.uint8)                 # [..., m]

        # Pack bits: 8 bits per byte
        m = self.proj_dim
        pad = (8 - m % 8) % 8
        if pad:
            z_bits = torch.cat([
                z_bits,
                z_bits.new_zeros(*z_bits.shape[:-1], pad),
            ], dim=-1)
        z_packed = z_bits.view(*z_bits.shape[:-1], -1, 8)
        # Pack 8 bools into 1 byte
        weights = torch.tensor(
            [128, 64, 32, 16, 8, 4, 2, 1],
            dtype=torch.uint8, device=z_bits.device,
        )
        proj_packed = (z_packed * weights).sum(dim=-1).to(torch.uint8)

        # Residual norms for magnitude scaling
        res_norms = residual.norm(dim=-1).to(torch.float16)

        return proj_packed, res_norms

    def decompress(
        self,
        proj_packed: torch.Tensor,
        res_norms: torch.Tensor,
    ) -> torch.Tensor:
        """Reconstruct approximate residual from compressed form.

        R̂ ≈ sign_vec · Φᵀ × (‖R‖ / √m)

        where sign_vec ∈ {-1, +1}^m is recovered from the packed bits.

        Args:
            proj_packed: shape [..., ceil(m/8)] — packed 1-bit projections.
            res_norms:   shape [...] — original residual L2 norms.

        Returns:
            residual_hat: shape [..., D] — approximate residual.
        """
        # Unpack bits
        bits = proj_packed.to(torch.uint8)
        expanded = []
        for shift in [7, 6, 5, 4, 3, 2, 1, 0]:
            expanded.append((bits >> shift) & 1)
        z_bits = torch.stack(expanded, dim=-1).view(
            *proj_packed.shape[:-1], -1
        )                                                       # [..., m_padded]
        z_bits = z_bits[..., :self.proj_dim]                    # [..., m]

        # Convert {0, 1} → {-1, +1}
        z_signed = (z_bits.to(torch.float16) * 2 - 1)          # [..., m]

        # Reconstruct: R̂ = z_signed · Φᵀ × (‖R‖ / √m)
        #            = z_signed · (Φᵀ / √m) × ‖R‖
        residual_hat = z_signed @ self._phi_t_scaled            # [..., D]
        residual_hat = residual_hat * res_norms.unsqueeze(-1)

        return residual_hat


# ═══════════════════════════════════════════════════════════════════════════════
#  5. COMBINED COMPRESS / DECOMPRESS (convenience)
# ═══════════════════════════════════════════════════════════════════════════════

def compress_vector(
    v: torch.Tensor,
    bits: int = 4,
    block_size: int = 0,
) -> dict:
    """Full compression pipeline for a single vector batch.

    v → polar_encode → symmetric_quantize → pack_bits

    Args:
        v:          shape [..., D]
        bits:       2, 3, or 4
        block_size: 0 for per-vector scale

    Returns:
        dict with keys:
          'magnitudes'  : [...] float16
          'packed'      : [..., packed_N] uint8
          'scales'      : [..., num_blocks] float16
          'bits'        : int
          'block_size'  : int
          'original_d'  : int  — original last dimension
    """
    mag, direction = polar_encode(v)
    q, scales = symmetric_quantize(direction, bits=bits, block_size=block_size)
    packed = pack_bits(q, bits=bits)

    return {
        "magnitudes": mag.to(torch.float16),
        "packed": packed,
        "scales": scales.to(torch.float16),
        "bits": bits,
        "block_size": block_size,
        "original_d": v.shape[-1],
    }


def decompress_vector(compressed: dict) -> torch.Tensor:
    """Full decompression pipeline.

    unpack_bits → symmetric_dequantize → polar_decode

    Returns:
        v_hat: shape [..., D] float16
    """
    bits = compressed["bits"]
    block_size = compressed["block_size"]
    D = compressed["original_d"]

    q = unpack_bits(compressed["packed"], bits=bits, original_n=D)
    direction_hat = symmetric_dequantize(
        q, compressed["scales"],
        bits=bits, block_size=block_size,
        target_dtype=torch.float16,
    )
    v_hat = polar_decode(compressed["magnitudes"], direction_hat)
    return v_hat


# ═══════════════════════════════════════════════════════════════════════════════
#  6. FUSED OPERATIONS (performance-critical)
# ═══════════════════════════════════════════════════════════════════════════════
#
# These functions fuse multiple decompression stages into a single pass
# to reduce memory bandwidth (avoiding materialization of intermediate
# tensors). This is the primary bottleneck in inference: memory movement
# dominates compute at low arithmetic intensity.

def fused_unpack_dequant_polar(
    packed_dirs: torch.Tensor,
    scales: torch.Tensor,
    magnitudes: torch.Tensor,
    bits: int,
    block_size: int,
    head_dim: int,
    target_dtype: torch.dtype = torch.float16,
) -> torch.Tensor:
    """Fused: unpack_bits → symmetric_dequantize → polar_decode in one pass.

    Eliminates intermediate tensor allocations for direction vectors.
    This is the hot path for batch decompression — every saved allocation
    reduces memory bandwidth pressure on GPU.

    Args:
        packed_dirs: [..., packed_dim] uint8 — bit-packed quantized directions.
        scales:      [..., num_blocks] float16 — per-block dequantization scales.
        magnitudes:  [...] float16 — per-vector L2 norms.
        bits:        2, 3, or 4.
        block_size:  block size for block-wise dequant (0 = per-vector).
        head_dim:    original dimension D.
        target_dtype: output dtype (float16 / bfloat16).

    Returns:
        vectors: [..., D] — reconstructed vectors in target_dtype.
    """
    qmax = _qmax(bits)

    # Stage 1: unpack bits → integer codes (uint8, range [0, 2*qmax])
    q = unpack_bits(packed_dirs, bits=bits, original_n=head_dim)

    # Stage 2+3 fused: dequantize + polar decode
    # Instead of: dir = (q - qmax) / qmax * scale; v = dir * mag
    # We compute: v = (q - qmax) / qmax * scale * mag
    # This avoids allocating the intermediate direction tensor.
    q_signed = q.to(torch.float32) - qmax  # [-qmax, +qmax]

    D = head_dim
    if block_size > 0 and scales.shape[-1] > 1:
        num_blocks = scales.shape[-1]
        padded_D = num_blocks * block_size
        if padded_D > D:
            pad = q_signed.new_zeros(*q_signed.shape[:-1], padded_D - D)
            q_signed = torch.cat([q_signed, pad], dim=-1)
        q_blocks = q_signed.view(*q_signed.shape[:-1], num_blocks, block_size)
        # Fuse scale and magnitude into one multiply:
        # combined_scale = scale * mag / qmax
        # But mag has shape [...] and scale has [..., num_blocks],
        # so we broadcast mag into the block dimension.
        mag_expanded = magnitudes.unsqueeze(-1).unsqueeze(-1)  # [..., 1, 1]
        scale_expanded = scales.unsqueeze(-1)  # [..., num_blocks, 1]
        combined = (scale_expanded * mag_expanded) / qmax  # [..., num_blocks, 1]
        v_blocks = q_blocks * combined
        v = v_blocks.reshape(*q_signed.shape[:-1], padded_D)
        if padded_D > D:
            v = v[..., :D]
    else:
        # Per-vector scale: combined_scale = scale * mag / qmax
        combined = (scales * magnitudes.unsqueeze(-1)) / qmax  # [..., 1]
        v = q_signed * combined

    return v.to(target_dtype)


def fused_unpack_dequant(
    packed_dirs: torch.Tensor,
    scales: torch.Tensor,
    bits: int,
    block_size: int,
    head_dim: int,
    target_dtype: torch.dtype = torch.float16,
) -> torch.Tensor:
    """Fused: unpack_bits → symmetric_dequantize (without polar decode).

    Useful for computing attention scores against direction vectors
    before applying magnitude scaling in the score domain.

    Args:
        packed_dirs: [..., packed_dim] uint8.
        scales:      [..., num_blocks] float16.
        bits:        2, 3, or 4.
        block_size:  block size for dequantization.
        head_dim:    original dimension D.
        target_dtype: output dtype.

    Returns:
        directions: [..., D] — dequantized direction vectors.
    """
    qmax = _qmax(bits)
    q = unpack_bits(packed_dirs, bits=bits, original_n=head_dim)
    q_signed = q.to(torch.float32) - qmax

    D = head_dim
    if block_size > 0 and scales.shape[-1] > 1:
        num_blocks = scales.shape[-1]
        padded_D = num_blocks * block_size
        if padded_D > D:
            pad = q_signed.new_zeros(*q_signed.shape[:-1], padded_D - D)
            q_signed = torch.cat([q_signed, pad], dim=-1)
        q_blocks = q_signed.view(*q_signed.shape[:-1], num_blocks, block_size)
        dirs = (q_blocks / qmax * scales.unsqueeze(-1)).reshape(
            *q_signed.shape[:-1], padded_D
        )
        if padded_D > D:
            dirs = dirs[..., :D]
    else:
        dirs = q_signed / qmax * scales

    return dirs.to(target_dtype)
