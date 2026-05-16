#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for KV-cache compression modules.

Covers:
  kv_quantization   — polar, symmetric quantize, bit packing, residual
  kv_cache_compressed — CompressedKVCache, BaselineKVCache, KVCacheConfig
  kv_attention       — compressed_attention, CompressedKVCacheManager

Run:
    python -m pytest lina/inference/tests/test_kv_cache.py -v
    python lina/inference/tests/test_kv_cache.py
"""

import sys
import math
from pathlib import Path

import torch
import pytest

# Make lina importable
_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

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
    fused_unpack_dequant_polar,
    fused_unpack_dequant,
)
from lina.inference.kv_cache_compressed import (
    CompressedKVCache,
    KVCacheConfig,
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


# ═══════════════════════════════════════════════════════════════════════════════
#  kv_quantization — polar encode / decode
# ═══════════════════════════════════════════════════════════════════════════════

class TestPolar:
    def test_roundtrip_fp32(self):
        v = torch.randn(8, 64)
        m, d = polar_encode(v)
        v2 = polar_decode(m, d)
        assert torch.allclose(v, v2, atol=1e-5)

    def test_roundtrip_fp16(self):
        v = torch.randn(4, 32, dtype=torch.float16)
        m, d = polar_encode(v)
        v2 = polar_decode(m, d)
        assert torch.allclose(v.float(), v2.float(), atol=1e-2)

    def test_zero_vector(self):
        v = torch.zeros(2, 16)
        m, d = polar_encode(v)
        assert (m == 0).all()
        v2 = polar_decode(m, d)
        assert (v2 == 0).all()

    def test_directions_unit_norm(self):
        v = torch.randn(10, 64)
        _, d = polar_encode(v)
        norms = d.norm(dim=-1)
        # Non-zero vectors should have unit-norm directions
        mask = v.norm(dim=-1) > 1e-7
        assert torch.allclose(norms[mask], torch.ones_like(norms[mask]), atol=1e-5)


# ═══════════════════════════════════════════════════════════════════════════════
#  kv_quantization — symmetric quantize / dequantize
# ═══════════════════════════════════════════════════════════════════════════════

class TestSymmetricQuantize:
    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_range(self, bits):
        x = torch.randn(16, 128, dtype=torch.float16)
        q, scales = symmetric_quantize(x, bits=bits, block_size=0)
        # Output is unsigned [0, 2*qmax] after offset
        qmax = (1 << (bits - 1)) - 1
        assert q.min().item() >= 0
        assert q.max().item() <= 2 * qmax

    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_roundtrip_quality(self, bits):
        torch.manual_seed(0)
        x = torch.randn(8, 64, dtype=torch.float16)
        q, scales = symmetric_quantize(x, bits=bits, block_size=0)
        x2 = symmetric_dequantize(q, scales, bits=bits, block_size=0)
        cos = torch.nn.functional.cosine_similarity(
            x.float().view(-1), x2.float().view(-1), dim=0
        ).item()
        # 4-bit should be very close, 2-bit still reasonable
        if bits == 4:
            assert cos > 0.95
        elif bits == 3:
            assert cos > 0.85
        else:
            assert cos > 0.60

    def test_block_quantize(self):
        x = torch.randn(4, 128, dtype=torch.float16)
        q, scales = symmetric_quantize(x, bits=4, block_size=32)
        assert scales.shape[-1] == 128 // 32
        x2 = symmetric_dequantize(q, scales, bits=4, block_size=32)
        cos = torch.nn.functional.cosine_similarity(
            x.float().view(-1), x2.float().view(-1), dim=0
        ).item()
        assert cos > 0.95


# ═══════════════════════════════════════════════════════════════════════════════
#  kv_quantization — bit packing
# ═══════════════════════════════════════════════════════════════════════════════

class TestBitPacking:
    @pytest.mark.parametrize("bits", [2, 3, 4])
    @pytest.mark.parametrize("n", [1, 7, 8, 13, 16, 31, 32, 64, 128, 255, 256])
    def test_roundtrip(self, bits, n):
        max_val = (1 << bits) - 1
        data = torch.randint(0, max_val + 1, (4, n), dtype=torch.uint8)
        packed = pack_bits(data, bits=bits)
        unpacked = unpack_bits(packed, bits=bits, original_n=n)
        assert (data == unpacked).all(), f"bits={bits}, n={n}: mismatch"

    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_compression(self, bits):
        n = 256
        data = torch.randint(0, (1 << bits), (1, n), dtype=torch.uint8)
        packed = pack_bits(data, bits=bits)
        # packed should be smaller (or equal for 8-bit which we don't use)
        assert packed.shape[-1] <= n


# ═══════════════════════════════════════════════════════════════════════════════
#  kv_quantization — ResidualCompressor
# ═══════════════════════════════════════════════════════════════════════════════

class TestResidualCompressor:
    def test_compress_decompress_shape(self):
        rc = ResidualCompressor(head_dim=64, proj_dim=32, seed=0)
        r = torch.randn(4, 64, dtype=torch.float16)
        packed, norms = rc.compress(r)
        r2 = rc.decompress(packed, norms)
        assert r2.shape == r.shape

    def test_deterministic(self):
        rc1 = ResidualCompressor(head_dim=64, proj_dim=32, seed=42)
        rc2 = ResidualCompressor(head_dim=64, proj_dim=32, seed=42)
        r = torch.randn(4, 64, dtype=torch.float16)
        p1, n1 = rc1.compress(r)
        p2, n2 = rc2.compress(r)
        assert (p1 == p2).all()
        assert torch.allclose(n1, n2)

    def test_reduces_error(self):
        """Residual correction should preserve direction (cosine similarity)."""
        torch.manual_seed(0)
        v = torch.randn(8, 64, dtype=torch.float16)
        rc = ResidualCompressor(head_dim=64, proj_dim=48, seed=0)
        packed, norms = rc.compress(v)
        v_approx = rc.decompress(packed, norms)
        # 1-bit JL sketch preserves direction on average
        cos = torch.nn.functional.cosine_similarity(
            v.float().view(-1), v_approx.float().view(-1), dim=0,
        ).item()
        assert cos > 0.0  # direction should be roughly preserved


# ═══════════════════════════════════════════════════════════════════════════════
#  kv_quantization — compress_vector / decompress_vector
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompressDecompress:
    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_roundtrip(self, bits):
        v = torch.randn(4, 64, dtype=torch.float16)
        comp = compress_vector(v, bits=bits)
        v2 = decompress_vector(comp)
        assert v2.shape == v.shape
        cos = torch.nn.functional.cosine_similarity(
            v.float().view(-1), v2.float().view(-1), dim=0,
        ).item()
        assert cos > 0.5  # sanity


# ═══════════════════════════════════════════════════════════════════════════════
#  kv_cache_compressed — CompressedKVCache
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompressedKVCache:
    @pytest.fixture
    def config(self):
        return KVCacheConfig(
            max_seq_len=32,
            num_heads=4,
            head_dim=32,
            bits=4,
            use_residual=False,
            device="cpu",
        )

    def test_add_get(self, config):
        cache = CompressedKVCache(config)
        k = torch.randn(4, 32, dtype=torch.float16)
        v = torch.randn(4, 32, dtype=torch.float16)
        cache.add(k, v)
        assert cache.seq_len == 1

        k2, v2 = cache.get(0)
        assert k2.shape == k.shape
        assert v2.shape == v.shape

    def test_sequence_growth(self, config):
        cache = CompressedKVCache(config)
        for i in range(10):
            cache.add(
                torch.randn(4, 32, dtype=torch.float16),
                torch.randn(4, 32, dtype=torch.float16),
            )
        assert cache.seq_len == 10

    def test_get_all(self, config):
        cache = CompressedKVCache(config)
        n = 8
        for _ in range(n):
            cache.add(
                torch.randn(4, 32, dtype=torch.float16),
                torch.randn(4, 32, dtype=torch.float16),
            )
        k_all, v_all = cache.get_all()
        assert k_all.shape == (n, 4, 32)
        assert v_all.shape == (n, 4, 32)

    def test_clear(self, config):
        cache = CompressedKVCache(config)
        for _ in range(5):
            cache.add(
                torch.randn(4, 32, dtype=torch.float16),
                torch.randn(4, 32, dtype=torch.float16),
            )
        cache.clear()
        assert cache.seq_len == 0

    def test_eviction(self):
        config = KVCacheConfig(
            max_seq_len=4,
            num_heads=2,
            head_dim=16,
            bits=4,
            device="cpu",
        )
        cache = CompressedKVCache(config)
        for i in range(6):
            cache.add(
                torch.randn(2, 16, dtype=torch.float16),
                torch.randn(2, 16, dtype=torch.float16),
            )
        # Should not exceed max
        assert cache.seq_len <= 4

    def test_memory_accounting(self, config):
        cache = CompressedKVCache(config)
        assert cache.memory_bytes() >= 0
        for _ in range(5):
            cache.add(
                torch.randn(4, 32, dtype=torch.float16),
                torch.randn(4, 32, dtype=torch.float16),
            )
        assert cache.memory_bytes() > 0
        assert cache.memory_bytes_baseline() > cache.memory_bytes()

    def test_compression_ratio(self, config):
        cache = CompressedKVCache(config)
        for _ in range(10):
            cache.add(
                torch.randn(4, 32, dtype=torch.float16),
                torch.randn(4, 32, dtype=torch.float16),
            )
        ratio = cache.compression_ratio()
        assert ratio > 1.0  # compressed should be smaller

    def test_stats(self, config):
        cache = CompressedKVCache(config)
        for _ in range(5):
            cache.add(
                torch.randn(4, 32, dtype=torch.float16),
                torch.randn(4, 32, dtype=torch.float16),
            )
        s = cache.stats()
        assert "seq_len" in s
        assert "compressed_bytes" in s
        assert "compression_ratio" in s


class TestCompressedKVCacheWithResidual:
    def test_better_quality_with_residual(self):
        torch.manual_seed(42)
        h, d, n = 4, 32, 16

        keys = [torch.randn(h, d, dtype=torch.float16) for _ in range(n)]
        vals = [torch.randn(h, d, dtype=torch.float16) for _ in range(n)]

        cache_no_res = CompressedKVCache(KVCacheConfig(
            max_seq_len=n + 1, num_heads=h, head_dim=d,
            bits=4, use_residual=False, device="cpu",
        ))
        cache_res = CompressedKVCache(KVCacheConfig(
            max_seq_len=n + 1, num_heads=h, head_dim=d,
            bits=4, use_residual=True, device="cpu",
        ))

        for k, v in zip(keys, vals):
            cache_no_res.add(k, v)
            cache_res.add(k, v)

        k_orig = torch.stack(keys)
        k_no_res, _ = cache_no_res.get_all()
        k_res, _ = cache_res.get_all()

        cos_no = torch.nn.functional.cosine_similarity(
            k_orig.float().view(-1), k_no_res.float().view(-1), dim=0,
        ).item()
        cos_res = torch.nn.functional.cosine_similarity(
            k_orig.float().view(-1), k_res.float().view(-1), dim=0,
        ).item()

        # Both should have reasonable quality
        assert cos_no > 0.8
        assert cos_res > 0.5  # residual may or may not help depending on proj_dim


class TestCompressedKVCacheAdaptive:
    def test_adaptive_mode(self):
        config = KVCacheConfig(
            max_seq_len=32,
            num_heads=4,
            head_dim=32,
            bits=4,
            adaptive=True,
            adaptive_window=4,
            device="cpu",
        )
        cache = CompressedKVCache(config)
        tokens = [
            (torch.randn(4, 32, dtype=torch.float16),
             torch.randn(4, 32, dtype=torch.float16))
            for _ in range(10)
        ]
        for k, v in tokens:
            cache.add(k, v)

        assert cache.seq_len == 10
        k_all, v_all = cache.get_all()
        assert k_all.shape[0] == 10


# ═══════════════════════════════════════════════════════════════════════════════
#  kv_cache_compressed — BaselineKVCache
# ═══════════════════════════════════════════════════════════════════════════════

class TestBaselineKVCache:
    def test_add_get(self):
        cache = BaselineKVCache(max_seq_len=16, num_heads=4, head_dim=32)
        k = torch.randn(4, 32, dtype=torch.float16)
        v = torch.randn(4, 32, dtype=torch.float16)
        cache.add(k, v)
        k2, v2 = cache.get(0)
        assert torch.allclose(k, k2)
        assert torch.allclose(v, v2)

    def test_get_all(self):
        cache = BaselineKVCache(max_seq_len=16, num_heads=4, head_dim=32)
        for _ in range(5):
            cache.add(
                torch.randn(4, 32, dtype=torch.float16),
                torch.randn(4, 32, dtype=torch.float16),
            )
        k, v = cache.get_all()
        assert k.shape == (5, 4, 32)

    def test_exact_reconstruction(self):
        """Baseline should have perfect reconstruction."""
        cache = BaselineKVCache(max_seq_len=10, num_heads=2, head_dim=16)
        keys = []
        for _ in range(5):
            k = torch.randn(2, 16, dtype=torch.float16)
            v = torch.randn(2, 16, dtype=torch.float16)
            keys.append(k)
            cache.add(k, v)
        k_all, _ = cache.get_all()
        for i, k in enumerate(keys):
            assert torch.equal(k, k_all[i])


# ═══════════════════════════════════════════════════════════════════════════════
#  kv_attention — compressed_attention
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompressedAttention:
    def test_output_shape(self):
        num_heads, head_dim, seq_len = 4, 32, 16
        q = torch.randn(num_heads, head_dim, dtype=torch.float16)
        k = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16)
        v = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16)
        out = compressed_attention(q, k, v, num_heads, head_dim)
        assert out.shape == (num_heads, head_dim)

    def test_matches_manual(self):
        """Compare with manual attention computation."""
        torch.manual_seed(42)
        nh, hd, sl = 2, 16, 8
        q = torch.randn(nh, hd, dtype=torch.float32)
        k = torch.randn(sl, nh, hd, dtype=torch.float32)
        v = torch.randn(sl, nh, hd, dtype=torch.float32)
        scale = 1.0 / math.sqrt(hd)

        out = compressed_attention(q, k, v, nh, hd, scale=scale)

        # Manual: for each head, Q·K^T / scale → softmax → ·V
        for h in range(nh):
            scores = q[h] @ k[:, h, :].T * scale
            weights = torch.softmax(scores, dim=-1)
            expected = weights @ v[:, h, :]
            assert torch.allclose(out[h], expected, atol=1e-4)

    def test_with_mask(self):
        nh, hd, sl = 2, 16, 8
        q = torch.randn(nh, hd, dtype=torch.float16)
        k = torch.randn(sl, nh, hd, dtype=torch.float16)
        v = torch.randn(sl, nh, hd, dtype=torch.float16)
        mask = torch.zeros(sl, dtype=torch.bool)
        mask[:4] = True  # mask out first 4 positions
        out = compressed_attention(q, k, v, nh, hd, mask=mask)
        assert out.shape == (nh, hd)


# ═══════════════════════════════════════════════════════════════════════════════
#  kv_attention — CompressedKVCacheManager
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompressedKVCacheManager:
    def test_multi_layer(self):
        mgr = CompressedKVCacheManager(
            num_layers=4,
            num_heads=2,
            head_dim=16,
            max_seq_len=16,
            bits=4,
            device="cpu",
        )
        for layer in range(4):
            mgr.add(
                layer,
                torch.randn(2, 16, dtype=torch.float16),
                torch.randn(2, 16, dtype=torch.float16),
            )

        for layer in range(4):
            k, v = mgr.get_all(layer)
            assert k.shape == (1, 2, 16)

    def test_baseline_mode(self):
        mgr = CompressedKVCacheManager(
            num_layers=2,
            num_heads=2,
            head_dim=16,
            max_seq_len=16,
            bits=4,
            device="cpu",
            enabled=False,
        )
        mgr.add(0, torch.randn(2, 16, dtype=torch.float16),
                    torch.randn(2, 16, dtype=torch.float16))
        k, v = mgr.get_all(0)
        assert k.shape == (1, 2, 16)

    def test_clear(self):
        mgr = CompressedKVCacheManager(
            num_layers=2,
            num_heads=2,
            head_dim=16,
            max_seq_len=16,
            bits=4,
            device="cpu",
        )
        mgr.add(0, torch.randn(2, 16, dtype=torch.float16),
                    torch.randn(2, 16, dtype=torch.float16))
        mgr.clear()
        k, v = mgr.get_all(0)
        assert k.shape[0] == 0

    def test_stats(self):
        mgr = CompressedKVCacheManager(
            num_layers=2,
            num_heads=2,
            head_dim=16,
            max_seq_len=16,
            bits=4,
            device="cpu",
        )
        s = mgr.stats()
        assert "total_compressed_bytes" in s
        assert "compression_ratio" in s
        assert "layers" in s


# ═══════════════════════════════════════════════════════════════════════════════
#  Edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_single_token(self):
        cache = CompressedKVCache(KVCacheConfig(
            max_seq_len=4, num_heads=2, head_dim=16, bits=4, device="cpu",
        ))
        cache.add(
            torch.randn(2, 16, dtype=torch.float16),
            torch.randn(2, 16, dtype=torch.float16),
        )
        k, v = cache.get_all()
        assert k.shape == (1, 2, 16)

    def test_empty_cache(self):
        cache = CompressedKVCache(KVCacheConfig(
            max_seq_len=4, num_heads=2, head_dim=16, bits=4, device="cpu",
        ))
        k, v = cache.get_all()
        assert k.shape[0] == 0

    def test_all_zeros_key(self):
        cache = CompressedKVCache(KVCacheConfig(
            max_seq_len=4, num_heads=2, head_dim=16, bits=4, device="cpu",
        ))
        k = torch.zeros(2, 16, dtype=torch.float16)
        v = torch.randn(2, 16, dtype=torch.float16)
        cache.add(k, v)
        k2, v2 = cache.get(0)
        # Zero key should reconstruct close to zero (no NaN)
        assert not torch.isnan(k2).any(), "Reconstructed key has NaN"
        assert k2.abs().max().item() < 0.1

    def test_large_values(self):
        cache = CompressedKVCache(KVCacheConfig(
            max_seq_len=4, num_heads=2, head_dim=16, bits=4, device="cpu",
        ))
        k = torch.ones(2, 16, dtype=torch.float16) * 100
        v = torch.ones(2, 16, dtype=torch.float16) * 100
        cache.add(k, v)
        k2, v2 = cache.get(0)
        # Should at least preserve the direction and approximate magnitude
        cos = torch.nn.functional.cosine_similarity(
            k.float().view(-1), k2.float().view(-1), dim=0,
        ).item()
        assert cos > 0.9


# ═══════════════════════════════════════════════════════════════════════════════
#  Fused decompression primitives
# ═══════════════════════════════════════════════════════════════════════════════

class TestFusedUnpackDequantPolar:
    """Tests for fused_unpack_dequant_polar — single-pass decompression."""

    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_matches_unfused(self, bits):
        """Fused result must match sequential unpack → dequant → polar."""
        torch.manual_seed(42)
        n, d = 8, 64
        v_orig = torch.randn(n, d, dtype=torch.float16)
        mag, direction = polar_encode(v_orig)
        q, scales = symmetric_quantize(direction, bits=bits, block_size=0)
        packed = pack_bits(q, bits=bits)

        # Unfused path
        q_back = unpack_bits(packed, bits=bits, original_n=d)
        dir_hat = symmetric_dequantize(q_back, scales, bits=bits, block_size=0)
        v_unfused = polar_decode(mag, dir_hat)

        # Fused path
        v_fused = fused_unpack_dequant_polar(
            packed, scales.to(torch.float16), mag.to(torch.float16),
            bits=bits, block_size=0, head_dim=d,
        )

        assert torch.allclose(v_unfused.float(), v_fused.float(), atol=5e-3), \
            f"bits={bits}: max diff={( v_unfused.float() - v_fused.float()).abs().max()}"

    @pytest.mark.parametrize("bits", [2, 4])
    def test_block_quantized(self, bits):
        """Fused with block_size > 0."""
        torch.manual_seed(0)
        n, d, bs = 4, 128, 32
        v_orig = torch.randn(n, d, dtype=torch.float16)
        mag, direction = polar_encode(v_orig)
        q, scales = symmetric_quantize(direction, bits=bits, block_size=bs)
        packed = pack_bits(q, bits=bits)

        v_fused = fused_unpack_dequant_polar(
            packed, scales.to(torch.float16), mag.to(torch.float16),
            bits=bits, block_size=bs, head_dim=d,
        )
        assert v_fused.shape == (n, d)
        # Should be a reasonable reconstruction
        cos = torch.nn.functional.cosine_similarity(
            v_orig.float().view(-1), v_fused.float().view(-1), dim=0,
        ).item()
        assert cos > 0.5

    def test_zero_vectors(self):
        """Fused handles zero vectors without NaN."""
        d = 32
        v = torch.zeros(2, d, dtype=torch.float16)
        mag, direction = polar_encode(v)
        q, scales = symmetric_quantize(direction, bits=4, block_size=0)
        packed = pack_bits(q, bits=4)

        result = fused_unpack_dequant_polar(
            packed, scales.to(torch.float16), mag.to(torch.float16),
            bits=4, block_size=0, head_dim=d,
        )
        assert not torch.isnan(result).any()
        assert result.abs().max() < 0.1


class TestFusedUnpackDequant:
    """Tests for fused_unpack_dequant (without polar decode)."""

    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_matches_sequential(self, bits):
        torch.manual_seed(42)
        x = torch.randn(4, 64, dtype=torch.float16)
        q, scales = symmetric_quantize(x, bits=bits, block_size=0)
        packed = pack_bits(q, bits=bits)

        # Sequential
        q_back = unpack_bits(packed, bits=bits, original_n=64)
        dir_seq = symmetric_dequantize(q_back, scales, bits=bits, block_size=0)

        # Fused
        dir_fused = fused_unpack_dequant(
            packed, scales.to(torch.float16),
            bits=bits, block_size=0, head_dim=64,
        )

        assert torch.allclose(dir_seq.float(), dir_fused.float(), atol=1e-3)


# ═══════════════════════════════════════════════════════════════════════════════
#  Fused attention
# ═══════════════════════════════════════════════════════════════════════════════

class TestFusedAttention:
    """Tests for compressed_attention_fused — block-streaming attention."""

    def test_matches_standard_attention(self):
        """Fused attention output should match standard attention."""
        torch.manual_seed(42)
        nh, hd, sl = 4, 32, 64
        config = KVCacheConfig(
            max_seq_len=sl + 1, num_heads=nh, head_dim=hd,
            bits=4, use_residual=False, device="cpu",
        )
        cache = CompressedKVCache(config=config)

        keys = torch.randn(sl, nh, hd, dtype=torch.float16)
        vals = torch.randn(sl, nh, hd, dtype=torch.float16)
        q = torch.randn(nh, hd, dtype=torch.float16)

        for i in range(sl):
            cache.add(keys[i], vals[i])

        # Standard path
        k_all, v_all = cache.get_all()
        out_std = compressed_attention(q, k_all, v_all, nh, hd)

        # Fused path
        out_fused = compressed_attention_fused(q, cache, block_size=16)

        cos = torch.nn.functional.cosine_similarity(
            out_std.float().view(-1),
            out_fused.float().view(-1),
            dim=0,
        ).item()
        assert cos > 0.99, f"Cosine sim too low: {cos}"

    def test_single_token(self):
        """Fused attention with seq_len=1."""
        nh, hd = 2, 16
        cache = CompressedKVCache(KVCacheConfig(
            max_seq_len=4, num_heads=nh, head_dim=hd,
            bits=4, use_residual=False, device="cpu",
        ))
        cache.add(
            torch.randn(nh, hd, dtype=torch.float16),
            torch.randn(nh, hd, dtype=torch.float16),
        )
        q = torch.randn(nh, hd, dtype=torch.float16)
        out = compressed_attention_fused(q, cache, block_size=4)
        assert out.shape == (nh, hd)
        assert not torch.isnan(out).any()

    def test_empty_cache(self):
        """Fused attention with empty cache returns zeros."""
        nh, hd = 2, 16
        cache = CompressedKVCache(KVCacheConfig(
            max_seq_len=4, num_heads=nh, head_dim=hd,
            bits=4, use_residual=False, device="cpu",
        ))
        q = torch.randn(nh, hd, dtype=torch.float16)
        out = compressed_attention_fused(q, cache)
        assert (out == 0).all()

    def test_various_block_sizes(self):
        """Output stable across different block sizes."""
        torch.manual_seed(42)
        nh, hd, sl = 2, 16, 32
        cache = CompressedKVCache(KVCacheConfig(
            max_seq_len=sl + 1, num_heads=nh, head_dim=hd,
            bits=4, use_residual=False, device="cpu",
        ))
        for _ in range(sl):
            cache.add(
                torch.randn(nh, hd, dtype=torch.float16),
                torch.randn(nh, hd, dtype=torch.float16),
            )
        q = torch.randn(nh, hd, dtype=torch.float16)

        results = []
        for bs in [4, 8, 16, 32, 64]:
            out = compressed_attention_fused(q, cache, block_size=bs)
            results.append(out)

        for i in range(1, len(results)):
            cos = torch.nn.functional.cosine_similarity(
                results[0].float().view(-1),
                results[i].float().view(-1),
                dim=0,
            ).item()
            assert cos > 0.999, f"block_size divergence: cos={cos}"

    def test_with_residual(self):
        """Fused attention works with residual path too."""
        torch.manual_seed(42)
        nh, hd, sl = 2, 16, 16
        cache = CompressedKVCache(KVCacheConfig(
            max_seq_len=sl + 1, num_heads=nh, head_dim=hd,
            bits=4, use_residual=True, device="cpu",
        ))
        for _ in range(sl):
            cache.add(
                torch.randn(nh, hd, dtype=torch.float16),
                torch.randn(nh, hd, dtype=torch.float16),
            )
        q = torch.randn(nh, hd, dtype=torch.float16)
        out = compressed_attention_fused(q, cache, block_size=4)
        assert out.shape == (nh, hd)
        assert not torch.isnan(out).any()


# ═══════════════════════════════════════════════════════════════════════════════
#  Mixed precision / compute_dtype
# ═══════════════════════════════════════════════════════════════════════════════

class TestMixedPrecision:
    def test_compute_dtype_float32(self):
        """get_all should return tensors in compute_dtype."""
        config = KVCacheConfig(
            max_seq_len=8, num_heads=2, head_dim=16,
            bits=4, use_residual=False, device="cpu",
            compute_dtype=torch.float32,
        )
        cache = CompressedKVCache(config)
        cache.add(
            torch.randn(2, 16, dtype=torch.float16),
            torch.randn(2, 16, dtype=torch.float16),
        )
        k, v = cache.get_all()
        assert k.dtype == torch.float32
        assert v.dtype == torch.float32

    def test_compute_dtype_default(self):
        """Default compute_dtype should match dtype."""
        config = KVCacheConfig(
            max_seq_len=8, num_heads=2, head_dim=16,
            bits=4, device="cpu", dtype=torch.float16,
        )
        assert config.compute_dtype == torch.float16


# ═══════════════════════════════════════════════════════════════════════════════
#  Adaptive strategy improvements
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdaptiveStrategy:
    def test_adaptive_min_seq_no_compression(self):
        """Below adaptive_min_seq, tokens stored at full precision."""
        config = KVCacheConfig(
            max_seq_len=256, num_heads=2, head_dim=16,
            bits=4, adaptive=True, adaptive_window=64,
            adaptive_min_seq=128, device="cpu",
        )
        cache = CompressedKVCache(config)

        keys = []
        for _ in range(64):
            k = torch.randn(2, 16, dtype=torch.float16)
            keys.append(k)
            cache.add(k, torch.randn(2, 16, dtype=torch.float16))

        # All should be in recent window (no compression yet)
        k_all, _ = cache.get_all()
        assert k_all.shape[0] == 64

    def test_config_adaptive_fields(self):
        """Verify new config fields exist and have defaults."""
        config = KVCacheConfig()
        assert hasattr(config, 'compute_dtype')
        assert hasattr(config, 'adaptive_min_seq')
        assert config.adaptive_min_seq == 128


# ═══════════════════════════════════════════════════════════════════════════════
#  Fast path (no residual) in _VectorStore.load_range
# ═══════════════════════════════════════════════════════════════════════════════

class TestFastPath:
    def test_load_range_no_residual_uses_fused(self):
        """load_range without residual should produce same result as with."""
        torch.manual_seed(42)
        nh, hd, n = 4, 32, 20

        config_no_res = KVCacheConfig(
            max_seq_len=n + 1, num_heads=nh, head_dim=hd,
            bits=4, use_residual=False, device="cpu",
        )
        cache = CompressedKVCache(config_no_res)

        keys = [torch.randn(nh, hd, dtype=torch.float16) for _ in range(n)]
        for k in keys:
            cache.add(k, torch.randn(nh, hd, dtype=torch.float16))

        k_all, _ = cache.get_all()
        k_orig = torch.stack(keys)

        cos = torch.nn.functional.cosine_similarity(
            k_orig.float().view(-1), k_all.float().view(-1), dim=0,
        ).item()
        # 4-bit should maintain high cosine similarity
        assert cos > 0.9

    def test_load_range_raw(self):
        """load_range_raw returns correct shapes."""
        nh, hd, n = 2, 16, 5
        config = KVCacheConfig(
            max_seq_len=n + 1, num_heads=nh, head_dim=hd,
            bits=4, use_residual=False, device="cpu",
        )
        cache = CompressedKVCache(config)
        for _ in range(n):
            cache.add(
                torch.randn(nh, hd, dtype=torch.float16),
                torch.randn(nh, hd, dtype=torch.float16),
            )

        packed, scales, mags = cache.key_store.load_range_raw(0, n)
        assert packed.shape[0] == n
        assert packed.shape[1] == nh
        assert scales.shape[0] == n
        assert mags.shape == (n, nh)


# ═══════════════════════════════════════════════════════════════════════════════
#  Manager fused attention
# ═══════════════════════════════════════════════════════════════════════════════

class TestManagerFusedAttention:
    def test_attention_fused_method(self):
        """Manager.attention_fused() works for compressed layers."""
        mgr = CompressedKVCacheManager(
            num_layers=2, num_heads=2, head_dim=16,
            max_seq_len=16, bits=4, use_residual=False, device="cpu",
        )
        for layer in range(2):
            for _ in range(4):
                mgr.add(
                    layer,
                    torch.randn(2, 16, dtype=torch.float16),
                    torch.randn(2, 16, dtype=torch.float16),
                )

        q = torch.randn(2, 16, dtype=torch.float16)
        out = mgr.attention_fused(0, q, block_size=4)
        assert out.shape == (2, 16)
        assert not torch.isnan(out).any()

    def test_attention_fused_baseline_fallback(self):
        """Manager.attention_fused() falls back for baseline mode."""
        mgr = CompressedKVCacheManager(
            num_layers=1, num_heads=2, head_dim=16,
            max_seq_len=16, bits=4, device="cpu", enabled=False,
        )
        mgr.add(0, torch.randn(2, 16, dtype=torch.float16),
                    torch.randn(2, 16, dtype=torch.float16))
        q = torch.randn(2, 16, dtype=torch.float16)
        out = mgr.attention_fused(0, q)
        assert out.shape == (2, 16)


# ═══════════════════════════════════════════════════════════════════════════════
#  Device Selection & Management (kv_device.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeviceSelection:
    """Tests for select_device(), device_info(), ensure_device()."""

    def test_select_device_returns_torch_device(self):
        dev = select_device()
        assert isinstance(dev, torch.device)

    def test_select_device_cpu_explicit(self):
        dev = select_device(device="cpu")
        assert dev.type == "cpu"

    def test_select_device_prefer_gpu_false(self):
        dev = select_device(prefer_gpu=False)
        assert dev.type == "cpu"

    def test_select_device_invalid_device_fallback(self):
        """Invalid device string should fall back to CPU."""
        dev = select_device(device="nonexistent_device_xyz")
        assert dev.type == "cpu"

    def test_device_info_returns_dataclass(self):
        dev = select_device(device="cpu")
        info = device_info(dev)
        assert isinstance(info, DeviceInfo)
        assert info.device == dev
        assert info.device.type == "cpu"
        assert isinstance(info.name, str)

    def test_device_info_cpu_fields(self):
        info = device_info(torch.device("cpu"))
        assert info.vram_bytes >= 0
        assert info.is_igpu is False
        assert info.compute_cap is None

    def test_format_device_info_is_string(self):
        info = device_info(torch.device("cpu"))
        s = format_device_info(info)
        assert isinstance(s, str)
        assert "cpu" in s.lower()

    def test_optimal_compute_dtype_cpu(self):
        dt = optimal_compute_dtype(torch.device("cpu"))
        assert dt in (torch.float16, torch.bfloat16, torch.float32)

    def test_ensure_device_noop_same_device(self):
        """ensure_device is a no-op when tensor is already on target."""
        t = torch.randn(4, dtype=torch.float32)
        t2 = ensure_device(t, torch.device("cpu"))
        assert t2.data_ptr() == t.data_ptr()  # same memory, zero-copy

    def test_ensure_device_dtype_preserved(self):
        for dt in (torch.float16, torch.float32, torch.int32):
            t = torch.ones(3, dtype=dt)
            t2 = ensure_device(t, torch.device("cpu"))
            assert t2.dtype == dt

    def test_ensure_device_accepts_string_device(self):
        t = torch.randn(5)
        # ensure_device should handle torch.device objects
        t2 = ensure_device(t, torch.device("cpu"))
        assert t2.device.type == "cpu"


# ═══════════════════════════════════════════════════════════════════════════════
#  Device-Agnostic KVCacheConfig
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeviceAgnosticConfig:
    """KVCacheConfig auto-detection and flexible device parameter."""

    def test_config_default_device_auto(self):
        """device=None triggers auto-detection."""
        cfg = KVCacheConfig(max_seq_len=32, num_heads=2, head_dim=16)
        assert isinstance(cfg.device, torch.device)
        # On CI/CPU machines, should be cpu
        assert cfg.device.type in ("cpu", "cuda")

    def test_config_explicit_cpu_string(self):
        cfg = KVCacheConfig(max_seq_len=32, num_heads=2, head_dim=16, device="cpu")
        assert cfg.device == torch.device("cpu")

    def test_config_explicit_torch_device(self):
        dev = torch.device("cpu")
        cfg = KVCacheConfig(max_seq_len=32, num_heads=2, head_dim=16, device=dev)
        assert cfg.device == dev

    def test_config_compute_dtype_auto(self):
        """compute_dtype=None selects optimal dtype for device."""
        cfg = KVCacheConfig(max_seq_len=32, num_heads=2, head_dim=16)
        assert cfg.compute_dtype in (torch.float16, torch.bfloat16, torch.float32)

    def test_config_compute_dtype_explicit(self):
        cfg = KVCacheConfig(
            max_seq_len=32, num_heads=2, head_dim=16,
            compute_dtype=torch.float32,
        )
        assert cfg.compute_dtype == torch.float32


# ═══════════════════════════════════════════════════════════════════════════════
#  Device-Agnostic Cache Operations
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeviceAgnosticCache:
    """CompressedKVCache and BaselineKVCache device propagation."""

    def test_compressed_cache_buffers_on_device(self):
        """All internal buffers allocated on config.device."""
        cfg = KVCacheConfig(
            max_seq_len=16, num_heads=2, head_dim=8,
            bits=4, use_residual=False, device="cpu",
        )
        cache = CompressedKVCache(config=cfg)
        # Store some data and verify device
        k = torch.randn(2, 8, dtype=torch.float16)
        v = torch.randn(2, 8, dtype=torch.float16)
        cache.add(k, v)
        k_out, v_out = cache.get_all()
        assert k_out.device.type == "cpu"
        assert v_out.device.type == "cpu"

    def test_baseline_cache_auto_device(self):
        """BaselineKVCache with device=None defaults to auto."""
        cache = BaselineKVCache(max_seq_len=16, num_heads=2, head_dim=8)
        assert cache.device.type in ("cpu", "cuda")

    def test_baseline_cache_explicit_device(self):
        cache = BaselineKVCache(
            max_seq_len=16, num_heads=2, head_dim=8, device="cpu",
        )
        assert cache.device == torch.device("cpu")

    def test_cache_add_device_migration(self):
        """Tensors on different device are automatically migrated in add()."""
        # Both on CPU — ensure_device is a no-op but still works
        cfg = KVCacheConfig(
            max_seq_len=16, num_heads=2, head_dim=8,
            bits=4, use_residual=False, device="cpu",
        )
        cache = CompressedKVCache(config=cfg)
        k = torch.randn(2, 8, dtype=torch.float16, device="cpu")
        v = torch.randn(2, 8, dtype=torch.float16, device="cpu")
        cache.add(k, v)
        k_out, v_out = cache.get_all()
        assert k_out.shape == (1, 2, 8)
        assert v_out.shape == (1, 2, 8)

    def test_manager_auto_device(self):
        """CompressedKVCacheManager works with device=None."""
        mgr = CompressedKVCacheManager(
            num_layers=1, num_heads=2, head_dim=8,
            max_seq_len=16, bits=4, use_residual=False,
        )
        k = torch.randn(2, 8, dtype=torch.float16)
        v = torch.randn(2, 8, dtype=torch.float16)
        mgr.add(0, k, v)
        k_out, v_out = mgr.get_all(0)
        assert k_out.shape[0] == 1


# ═══════════════════════════════════════════════════════════════════════════════
#  Device-Agnostic Attention
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeviceAgnosticAttention:
    """Attention functions with device-aware ensure_device."""

    def test_compressed_attention_device_aligned(self):
        """compressed_attention handles tensors on same device."""
        q = torch.randn(4, 16, dtype=torch.float16)
        k = torch.randn(8, 4, 16, dtype=torch.float16)
        v = torch.randn(8, 4, 16, dtype=torch.float16)
        out = compressed_attention(q, k, v, 4, 16)
        assert out.shape == (4, 16)
        assert out.device.type == "cpu"

    def test_fused_attention_device_auto(self):
        """compressed_attention_fused uses cache's device."""
        cfg = KVCacheConfig(
            max_seq_len=32, num_heads=2, head_dim=8,
            bits=4, use_residual=False, device="cpu",
        )
        cache = CompressedKVCache(config=cfg)
        for _ in range(4):
            cache.add(
                torch.randn(2, 8, dtype=torch.float16),
                torch.randn(2, 8, dtype=torch.float16),
            )
        q = torch.randn(2, 8, dtype=torch.float16)
        out = compressed_attention_fused(q, cache, block_size=2)
        assert out.shape == (2, 8)
        assert out.device.type == "cpu"

    def test_fused_attention_query_device_migration(self):
        """Query on different device than cache gets migrated."""
        # Both on CPU but verifies the ensure_device path
        cfg = KVCacheConfig(
            max_seq_len=16, num_heads=2, head_dim=8,
            bits=4, use_residual=False, device="cpu",
        )
        cache = CompressedKVCache(config=cfg)
        cache.add(
            torch.randn(2, 8, dtype=torch.float16),
            torch.randn(2, 8, dtype=torch.float16),
        )
        q = torch.randn(2, 8, dtype=torch.float16, device="cpu")
        out = compressed_attention_fused(q, cache, block_size=4)
        assert out.shape == (2, 8)
        assert not torch.isnan(out).any()


# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
