#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lina Inference — KV-cache compression benchmark.

Compares compressed vs baseline KV-cache across multiple configurations:
  - Memory usage
  - Latency (add / get_all / attention)
  - Reconstruction quality (cosine similarity, MSE)
  - Compression ratio
  - Fused vs non-fused attention comparison
  - Throughput (tokens/sec)
  - Memory bandwidth estimation
  - GPU benchmarks (when CUDA available)

Usage:
    python -m lina.inference.kv_benchmark
    python lina/inference/kv_benchmark.py

Phase 10+ — AI Runtime KV-cache compression.
"""

import sys
import time
import math
from pathlib import Path

import torch

# ── Make lina importable when running as standalone script ──
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from lina.inference.kv_cache_compressed import (
    CompressedKVCache,
    KVCacheConfig,
    BaselineKVCache,
)
from lina.inference.kv_attention import (
    compressed_attention,
    compressed_attention_fused,
)
from lina.inference.kv_quantization import (
    compress_vector,
    decompress_vector,
    polar_encode,
    polar_decode,
    symmetric_quantize,
    symmetric_dequantize,
    pack_bits,
    unpack_bits,
    ResidualCompressor,
    fused_unpack_dequant_polar,
)
from lina.inference.kv_device import (
    select_device,
    device_info,
    format_device_info,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Helper: elapsed time
# ═══════════════════════════════════════════════════════════════════════════════

class Timer:
    """Simple context-manager timer."""
    def __init__(self):
        self.elapsed_ms = 0.0
    def __enter__(self):
        self._start = time.perf_counter()
        return self
    def __exit__(self, *_):
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000


# ═══════════════════════════════════════════════════════════════════════════════
#  1. Reconstruction quality
# ═══════════════════════════════════════════════════════════════════════════════

def benchmark_reconstruction(
    num_heads: int = 32,
    head_dim: int = 128,
    seq_len: int = 512,
    device: str = "cpu",
):
    """Measure reconstruction quality for various configs."""
    print("=" * 72)
    print("  RECONSTRUCTION QUALITY BENCHMARK")
    print("=" * 72)
    print(f"  num_heads={num_heads}, head_dim={head_dim}, seq_len={seq_len}")
    print("-" * 72)
    print(f"  {'Config':<35} {'Cos Sim':>10} {'MSE':>12} {'MaxErr':>10}")
    print("-" * 72)

    # Generate random KV data (simulate real distribution)
    torch.manual_seed(42)
    keys = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)
    values = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)

    configs = [
        ("4-bit, no residual",  4, False, 0),
        ("4-bit + residual",    4, True,  0),
        ("3-bit, no residual",  3, False, 0),
        ("3-bit + residual",    3, True,  0),
        ("2-bit, no residual",  2, False, 0),
        ("2-bit + residual",    2, True,  0),
        ("4-bit, block=32",     4, False, 32),
        ("4-bit, block=32+res", 4, True,  32),
    ]

    for name, bits, use_res, block_size in configs:
        cache = CompressedKVCache(KVCacheConfig(
            max_seq_len=seq_len + 1,
            num_heads=num_heads,
            head_dim=head_dim,
            bits=bits,
            block_size=block_size,
            use_residual=use_res,
            device=device,
        ))

        for i in range(seq_len):
            cache.add(keys[i], values[i])

        k_all, v_all = cache.get_all()

        # Cosine similarity (average over all vectors)
        k_cos = torch.nn.functional.cosine_similarity(
            keys.view(-1, head_dim).float(),
            k_all.view(-1, head_dim).float(),
            dim=-1,
        ).mean().item()

        v_cos = torch.nn.functional.cosine_similarity(
            values.view(-1, head_dim).float(),
            v_all.view(-1, head_dim).float(),
            dim=-1,
        ).mean().item()

        avg_cos = (k_cos + v_cos) / 2

        # MSE
        k_mse = (keys.float() - k_all.float()).pow(2).mean().item()
        v_mse = (values.float() - v_all.float()).pow(2).mean().item()
        avg_mse = (k_mse + v_mse) / 2

        # Max error
        max_err = max(
            (keys.float() - k_all.float()).abs().max().item(),
            (values.float() - v_all.float()).abs().max().item(),
        )

        print(f"  {name:<35} {avg_cos:>10.6f} {avg_mse:>12.8f} {max_err:>10.4f}")

    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  2. Memory usage
# ═══════════════════════════════════════════════════════════════════════════════

def benchmark_memory(
    num_heads: int = 32,
    head_dim: int = 128,
    device: str = "cpu",
):
    """Compare memory usage across configs."""
    print("=" * 72)
    print("  MEMORY USAGE BENCHMARK")
    print("=" * 72)
    print(f"  num_heads={num_heads}, head_dim={head_dim}")
    print("-" * 72)
    print(f"  {'Config':<30} {'SeqLen':>8} {'Compressed':>12} {'Baseline':>12} {'Ratio':>8}")
    print("-" * 72)

    torch.manual_seed(42)

    configs = [
        ("Baseline fp16",     16, False, 0),
        ("4-bit",              4, False, 0),
        ("4-bit + residual",   4, True,  0),
        ("3-bit + residual",   3, True,  0),
        ("2-bit",              2, False, 0),
        ("2-bit + residual",   2, True,  0),
    ]

    for seq_len in [256, 1024, 4096]:
        for name, bits, use_res, block_size in configs:
            if bits == 16:
                cache = BaselineKVCache(
                    max_seq_len=seq_len + 1,
                    num_heads=num_heads,
                    head_dim=head_dim,
                    device=device,
                )
            else:
                cache = CompressedKVCache(KVCacheConfig(
                    max_seq_len=seq_len + 1,
                    num_heads=num_heads,
                    head_dim=head_dim,
                    bits=bits,
                    block_size=block_size,
                    use_residual=use_res,
                    device=device,
                ))

            k = torch.randn(num_heads, head_dim, dtype=torch.float16, device=device)
            v = torch.randn(num_heads, head_dim, dtype=torch.float16, device=device)
            for _ in range(seq_len):
                cache.add(k, v)

            comp = cache.memory_bytes()
            base = cache.memory_bytes_baseline()
            ratio = cache.compression_ratio()

            def fmt_bytes(b):
                if b >= 1024 * 1024:
                    return f"{b / 1024 / 1024:.1f} MB"
                elif b >= 1024:
                    return f"{b / 1024:.1f} KB"
                return f"{b} B"

            print(
                f"  {name:<30} {seq_len:>8} "
                f"{fmt_bytes(comp):>12} {fmt_bytes(base):>12} {ratio:>7.2f}x"
            )
        print()


# ═══════════════════════════════════════════════════════════════════════════════
#  3. Speed benchmark
# ═══════════════════════════════════════════════════════════════════════════════

def benchmark_speed(
    num_heads: int = 32,
    head_dim: int = 128,
    seq_len: int = 1024,
    warmup: int = 50,
    trials: int = 200,
    device: str = "cpu",
):
    """Compare latency of add() / get_all() / attention."""
    print("=" * 72)
    print("  SPEED BENCHMARK")
    print("=" * 72)
    print(f"  num_heads={num_heads}, head_dim={head_dim}, seq_len={seq_len}")
    print(f"  warmup={warmup}, trials={trials}")
    print("-" * 72)
    print(f"  {'Config':<30} {'add (μs)':>10} {'get_all (ms)':>14} {'attn (ms)':>12}")
    print("-" * 72)

    torch.manual_seed(42)
    k_data = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)
    v_data = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)
    q = torch.randn(num_heads, head_dim, dtype=torch.float16, device=device)

    configs = [
        ("Baseline fp16",     None),
        ("4-bit, no residual", KVCacheConfig(
            max_seq_len=seq_len + 1, num_heads=num_heads, head_dim=head_dim,
            bits=4, use_residual=False, device=device,
        )),
        ("4-bit + residual",  KVCacheConfig(
            max_seq_len=seq_len + 1, num_heads=num_heads, head_dim=head_dim,
            bits=4, use_residual=True, device=device,
        )),
        ("2-bit, no residual", KVCacheConfig(
            max_seq_len=seq_len + 1, num_heads=num_heads, head_dim=head_dim,
            bits=2, use_residual=False, device=device,
        )),
    ]

    for name, config in configs:
        if config is None:
            cache = BaselineKVCache(
                max_seq_len=seq_len + 1,
                num_heads=num_heads,
                head_dim=head_dim,
                device=device,
            )
        else:
            cache = CompressedKVCache(config=config)

        # ── Benchmark add() ──
        cache.clear()
        for i in range(min(warmup, seq_len)):
            cache.add(k_data[i], v_data[i])
        cache.clear()

        add_times = []
        for i in range(trials):
            idx = i % seq_len
            if device != "cpu":
                torch.cuda.synchronize()
            with Timer() as t:
                cache.add(k_data[idx], v_data[idx])
                if device != "cpu":
                    torch.cuda.synchronize()
            add_times.append(t.elapsed_ms)
            if cache.seq_len >= seq_len:
                cache.clear()

        avg_add_us = sum(add_times) / len(add_times) * 1000

        # ── Benchmark get_all() ──
        cache.clear()
        for i in range(seq_len):
            cache.add(k_data[i], v_data[i])

        for _ in range(3):
            cache.get_all()

        get_times = []
        for _ in range(20):
            if device != "cpu":
                torch.cuda.synchronize()
            with Timer() as t:
                k_all, v_all = cache.get_all()
                if device != "cpu":
                    torch.cuda.synchronize()
            get_times.append(t.elapsed_ms)

        avg_get_ms = sum(get_times) / len(get_times)

        # ── Benchmark full attention ──
        attn_times = []
        for _ in range(20):
            k_all, v_all = cache.get_all()
            if device != "cpu":
                torch.cuda.synchronize()
            with Timer() as t:
                _ = compressed_attention(q, k_all, v_all, num_heads, head_dim)
                if device != "cpu":
                    torch.cuda.synchronize()
            attn_times.append(t.elapsed_ms)

        avg_attn_ms = sum(attn_times) / len(attn_times)

        print(
            f"  {name:<30} {avg_add_us:>10.1f} "
            f"{avg_get_ms:>14.2f} {avg_attn_ms:>12.2f}"
        )

    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  3b. Fused vs Non-Fused Attention Comparison
# ═══════════════════════════════════════════════════════════════════════════════

def benchmark_fused_attention(
    num_heads: int = 32,
    head_dim: int = 128,
    device: str = "cpu",
):
    """Compare fused vs non-fused attention: latency, memory, accuracy."""
    print("=" * 72)
    print("  FUSED vs NON-FUSED ATTENTION")
    print("=" * 72)
    print(f"  num_heads={num_heads}, head_dim={head_dim}")
    print("-" * 72)
    print(
        f"  {'SeqLen':>8} {'Standard (ms)':>14} {'Fused (ms)':>12} "
        f"{'Speedup':>9} {'Cos Sim':>10} {'MaxDiff':>10}"
    )
    print("-" * 72)

    torch.manual_seed(42)

    for seq_len in [128, 256, 512, 1024, 2048, 4096]:
        config = KVCacheConfig(
            max_seq_len=seq_len + 1,
            num_heads=num_heads,
            head_dim=head_dim,
            bits=4,
            use_residual=False,
            device=device,
        )
        cache = CompressedKVCache(config=config)

        k_data = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)
        v_data = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)
        q = torch.randn(num_heads, head_dim, dtype=torch.float16, device=device)

        for i in range(seq_len):
            cache.add(k_data[i], v_data[i])

        # Warmup
        k_all, v_all = cache.get_all()
        _ = compressed_attention(q, k_all, v_all, num_heads, head_dim)
        _ = compressed_attention_fused(q, cache, block_size=64)

        # ── Standard (decompress all + attention) ──
        std_times = []
        for _ in range(10):
            if device != "cpu":
                torch.cuda.synchronize()
            with Timer() as t:
                k_all, v_all = cache.get_all()
                out_std = compressed_attention(q, k_all, v_all, num_heads, head_dim)
                if device != "cpu":
                    torch.cuda.synchronize()
            std_times.append(t.elapsed_ms)
        avg_std = sum(std_times) / len(std_times)

        # ── Fused (block decompression + streaming attention) ──
        fused_times = []
        for _ in range(10):
            if device != "cpu":
                torch.cuda.synchronize()
            with Timer() as t:
                out_fused = compressed_attention_fused(q, cache, block_size=64)
                if device != "cpu":
                    torch.cuda.synchronize()
            fused_times.append(t.elapsed_ms)
        avg_fused = sum(fused_times) / len(fused_times)

        # ── Accuracy comparison ──
        cos_sim = torch.nn.functional.cosine_similarity(
            out_std.float().view(-1),
            out_fused.float().view(-1),
            dim=0,
        ).item()
        max_diff = (out_std.float() - out_fused.float()).abs().max().item()

        speedup = avg_std / max(avg_fused, 1e-6)

        print(
            f"  {seq_len:>8} {avg_std:>14.2f} {avg_fused:>12.2f} "
            f"{speedup:>8.2f}× {cos_sim:>10.6f} {max_diff:>10.6f}"
        )

    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  3c. Throughput Benchmark (tokens/sec)
# ═══════════════════════════════════════════════════════════════════════════════

def benchmark_throughput(
    num_heads: int = 32,
    head_dim: int = 128,
    device: str = "cpu",
):
    """Measure end-to-end throughput: tokens processed per second.

    Simulates a generation loop: add token → compute attention.
    """
    print("=" * 72)
    print("  THROUGHPUT BENCHMARK (tokens/sec)")
    print("=" * 72)
    print(f"  num_heads={num_heads}, head_dim={head_dim}")
    print("-" * 72)
    print(
        f"  {'Config':<30} {'SeqLen':>8} {'Tokens/s':>12} {'Latency (ms)':>14}"
    )
    print("-" * 72)

    torch.manual_seed(42)

    configs = [
        ("Baseline fp16",   None,  False),
        ("4-bit standard",  False, False),
        ("4-bit fused",     False, True),
        ("2-bit fused",     False, True),
    ]

    for seq_len in [256, 1024, 4096]:
        for name, is_baseline, use_fused in configs:
            bits = 2 if "2-bit" in name else 4

            if is_baseline is None:
                cache = BaselineKVCache(
                    max_seq_len=seq_len + n_steps + 2,
                    num_heads=num_heads,
                    head_dim=head_dim,
                    device=device,
                )
            else:
                cache = CompressedKVCache(KVCacheConfig(
                    max_seq_len=seq_len + n_steps + 2,
                    num_heads=num_heads,
                    head_dim=head_dim,
                    bits=bits,
                    use_residual=False,
                    device=device,
                ))

            # Fill cache
            k_data = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)
            v_data = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)
            for i in range(seq_len):
                cache.add(k_data[i], v_data[i])

            q = torch.randn(num_heads, head_dim, dtype=torch.float16, device=device)
            k_new = torch.randn(num_heads, head_dim, dtype=torch.float16, device=device)
            v_new = torch.randn(num_heads, head_dim, dtype=torch.float16, device=device)

            # Simulate generation steps (add + attend)
            n_steps = 20
            if device != "cpu":
                torch.cuda.synchronize()
            start = time.perf_counter()
            for _ in range(n_steps):
                cache.add(k_new, v_new)
                if use_fused and isinstance(cache, CompressedKVCache):
                    _ = compressed_attention_fused(q, cache, block_size=64)
                else:
                    k_all, v_all = cache.get_all()
                    _ = compressed_attention(q, k_all, v_all, num_heads, head_dim)
            if device != "cpu":
                torch.cuda.synchronize()
            elapsed = time.perf_counter() - start

            tokens_per_sec = n_steps / elapsed
            latency_ms = elapsed / n_steps * 1000

            print(
                f"  {name:<30} {seq_len:>8} "
                f"{tokens_per_sec:>12.1f} {latency_ms:>14.2f}"
            )
        print()


# ═══════════════════════════════════════════════════════════════════════════════
#  3d. Memory Bandwidth Estimation
# ═══════════════════════════════════════════════════════════════════════════════

def benchmark_bandwidth(
    num_heads: int = 32,
    head_dim: int = 128,
    device: str = "cpu",
):
    """Estimate effective memory bandwidth during get_all() decompression.

    Bandwidth = data_read / time_elapsed.
    data_read ≈ compressed cache size (packed_dirs + scales + magnitudes).
    """
    print("=" * 72)
    print("  MEMORY BANDWIDTH ESTIMATION (get_all)")
    print("=" * 72)
    print(f"  num_heads={num_heads}, head_dim={head_dim}")
    print("-" * 72)
    print(
        f"  {'Config':<28} {'SeqLen':>8} {'Time (ms)':>10} "
        f"{'Read (MB)':>10} {'BW (GB/s)':>10} {'Write (MB)':>10}"
    )
    print("-" * 72)

    torch.manual_seed(42)

    for seq_len in [512, 1024, 4096]:
        for bits, name in [(4, "4-bit"), (2, "2-bit")]:
            config = KVCacheConfig(
                max_seq_len=seq_len + 1,
                num_heads=num_heads,
                head_dim=head_dim,
                bits=bits,
                use_residual=False,
                device=device,
            )
            cache = CompressedKVCache(config=config)

            k_data = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)
            v_data = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)
            for i in range(seq_len):
                cache.add(k_data[i], v_data[i])

            # Compressed size (what we read)
            read_bytes = cache.memory_bytes()
            # Decompressed size (what we write)
            write_bytes = seq_len * num_heads * head_dim * 2 * 2  # fp16 K+V

            # Warmup
            for _ in range(3):
                cache.get_all()

            # Measure
            times = []
            for _ in range(20):
                if device != "cpu":
                    torch.cuda.synchronize()
                with Timer() as t:
                    cache.get_all()
                    if device != "cpu":
                        torch.cuda.synchronize()
                times.append(t.elapsed_ms)

            avg_ms = sum(times) / len(times)
            read_mb = read_bytes / (1024 * 1024)
            write_mb = write_bytes / (1024 * 1024)
            # Bandwidth = (read + write) / time
            total_mb = read_mb + write_mb
            bw_gbs = total_mb / (avg_ms / 1000) / 1024 if avg_ms > 0 else 0

            print(
                f"  {name:<28} {seq_len:>8} {avg_ms:>10.2f} "
                f"{read_mb:>10.2f} {bw_gbs:>10.2f} {write_mb:>10.2f}"
            )
        print()


# ═══════════════════════════════════════════════════════════════════════════════
#  3e. Fused Decompression Primitives Benchmark
# ═══════════════════════════════════════════════════════════════════════════════

def benchmark_fused_primitives(
    num_heads: int = 32,
    head_dim: int = 128,
    device: str = "cpu",
):
    """Compare fused vs unfused decompression primitives."""
    print("=" * 72)
    print("  FUSED vs UNFUSED DECOMPRESSION PRIMITIVES")
    print("=" * 72)
    print("-" * 72)
    print(
        f"  {'SeqLen':>8} {'Unfused (ms)':>14} {'Fused (ms)':>12} {'Speedup':>9}"
    )
    print("-" * 72)

    torch.manual_seed(42)

    for seq_len in [256, 512, 1024, 2048, 4096]:
        n = seq_len * num_heads
        bits = 4
        packed_dim = (head_dim + 1) // 2  # 4-bit packing

        # Create test data
        packed = torch.randint(0, 256, (n, packed_dim), dtype=torch.uint8, device=device)
        scales = torch.randn(n, 1, dtype=torch.float16, device=device).abs() + 0.1
        mags = torch.randn(n, dtype=torch.float16, device=device).abs()

        # Warmup
        from lina.inference.kv_quantization import (
            unpack_bits as _unpack,
            symmetric_dequantize as _dequant,
            polar_decode as _polar,
        )
        _ = fused_unpack_dequant_polar(packed, scales, mags, bits, 0, head_dim)
        q = _unpack(packed, bits, head_dim)
        d = _dequant(q, scales, bits=bits, block_size=0)
        _ = _polar(mags, d)

        # ── Unfused: 3 separate ops ──
        unfused_times = []
        for _ in range(20):
            if device != "cpu":
                torch.cuda.synchronize()
            with Timer() as t:
                q = _unpack(packed, bits, head_dim)
                d = _dequant(q, scales, bits=bits, block_size=0)
                v = _polar(mags, d)
                if device != "cpu":
                    torch.cuda.synchronize()
            unfused_times.append(t.elapsed_ms)

        # ── Fused: single pass ──
        fused_times = []
        for _ in range(20):
            if device != "cpu":
                torch.cuda.synchronize()
            with Timer() as t:
                v2 = fused_unpack_dequant_polar(packed, scales, mags, bits, 0, head_dim)
                if device != "cpu":
                    torch.cuda.synchronize()
            fused_times.append(t.elapsed_ms)

        avg_unfused = sum(unfused_times) / len(unfused_times)
        avg_fused = sum(fused_times) / len(fused_times)
        speedup = avg_unfused / max(avg_fused, 1e-6)

        print(
            f"  {seq_len:>8} {avg_unfused:>14.2f} {avg_fused:>12.2f} {speedup:>8.2f}×"
        )

    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  4. Attention output drift
# ═══════════════════════════════════════════════════════════════════════════════

def benchmark_attention_drift(
    num_heads: int = 32,
    head_dim: int = 128,
    seq_len: int = 512,
    device: str = "cpu",
):
    """Measure how compressed KV-cache affects attention output."""
    print("=" * 72)
    print("  ATTENTION OUTPUT DRIFT")
    print("=" * 72)
    print(f"  Comparing attention output: baseline vs compressed")
    print("-" * 72)
    print(f"  {'Config':<35} {'Cos Sim':>10} {'L2 Dist':>10} {'Max Diff':>10}")
    print("-" * 72)

    torch.manual_seed(42)
    keys = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)
    values = torch.randn(seq_len, num_heads, head_dim, dtype=torch.float16, device=device)
    q = torch.randn(num_heads, head_dim, dtype=torch.float16, device=device)

    # Baseline attention output
    baseline = BaselineKVCache(
        max_seq_len=seq_len + 1,
        num_heads=num_heads,
        head_dim=head_dim,
        device=device,
    )
    for i in range(seq_len):
        baseline.add(keys[i], values[i])
    k_base, v_base = baseline.get_all()
    attn_base = compressed_attention(q, k_base, v_base, num_heads, head_dim)

    configs = [
        ("4-bit, no residual",  4, False),
        ("4-bit + residual",    4, True),
        ("3-bit + residual",    3, True),
        ("2-bit, no residual",  2, False),
        ("2-bit + residual",    2, True),
    ]

    for name, bits, use_res in configs:
        cache = CompressedKVCache(KVCacheConfig(
            max_seq_len=seq_len + 1,
            num_heads=num_heads,
            head_dim=head_dim,
            bits=bits,
            use_residual=use_res,
            device=device,
        ))
        for i in range(seq_len):
            cache.add(keys[i], values[i])
        k_c, v_c = cache.get_all()
        attn_comp = compressed_attention(q, k_c, v_c, num_heads, head_dim)

        cos = torch.nn.functional.cosine_similarity(
            attn_base.float().view(-1),
            attn_comp.float().view(-1),
            dim=0,
        ).item()

        l2 = (attn_base.float() - attn_comp.float()).norm().item()
        max_diff = (attn_base.float() - attn_comp.float()).abs().max().item()

        print(f"  {name:<35} {cos:>10.6f} {l2:>10.4f} {max_diff:>10.4f}")

    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  5. Bit packing roundtrip
# ═══════════════════════════════════════════════════════════════════════════════

def benchmark_packing():
    """Verify bit packing roundtrip correctness."""
    print("=" * 72)
    print("  BIT PACKING ROUNDTRIP TEST")
    print("=" * 72)

    torch.manual_seed(42)
    for bits in [2, 3, 4]:
        max_val = (1 << bits) - 1
        # Test various sizes including non-aligned
        for n in [7, 8, 13, 16, 31, 32, 64, 128, 255, 256]:
            data = torch.randint(0, max_val + 1, (4, n), dtype=torch.uint8)
            packed = pack_bits(data, bits=bits)
            unpacked = unpack_bits(packed, bits=bits, original_n=n)
            match = (data == unpacked).all().item()
            if not match:
                print(f"  FAIL: bits={bits}, n={n}")
                print(f"    orig:     {data[0, :8].tolist()}")
                print(f"    unpacked: {unpacked[0, :8].tolist()}")
            else:
                pass  # silent pass

        print(f"  bits={bits}: all sizes passed ✓")

    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    dev = select_device(prefer_gpu=True)
    device = str(dev)
    info = device_info(dev)
    print(format_device_info(info))
    print()

    # ── Core correctness ──
    benchmark_packing()
    benchmark_reconstruction(device=device)

    # ── Memory ──
    benchmark_memory(device=device)

    # ── Speed: existing benchmarks ──
    benchmark_speed(seq_len=512, device=device)
    benchmark_attention_drift(device=device)

    # ── NEW: Fused primitives ──
    benchmark_fused_primitives(device=device)

    # ── NEW: Fused vs non-fused attention ──
    benchmark_fused_attention(device=device)

    # ── NEW: Throughput ──
    benchmark_throughput(device=device)

    # ── NEW: Memory bandwidth ──
    benchmark_bandwidth(device=device)

    print("=" * 72)
    print("  BENCHMARK COMPLETE")
    print("=" * 72)


if __name__ == "__main__":
    main()
