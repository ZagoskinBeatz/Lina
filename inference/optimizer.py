# -*- coding: utf-8 -*-
"""
Lina Inference — Оптимизатор контекстного окна (Context Optimizer).

Оптимизация использования контекстного окна модели:
  1. Динамический n_ctx в зависимости от запроса
  2. Интеграция с TokenBudget для auto_trim
  3. Рекомендации по n_ctx для full модели
  4. Мониторинг usage и предупреждения

Phase 10 — AI Runtime v2.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger("lina.inference.optimizer")


# ═══════════════════════════════════════════════════════════
#  Константы
# ═══════════════════════════════════════════════════════════

# Среднее количество символов на токен (русский язык)
CHARS_PER_TOKEN_RU = 3.5

# Минимальный n_ctx
MIN_CONTEXT = 512

# Порог предупреждения (% использования контекста)
WARNING_THRESHOLD = 0.90

# Рекомендованные n_ctx для модели
RECOMMENDED_CTX = {
    "full": {
        "default": 4096,    # Сниженный для скорости (train=8192)
        "min": 2048,
        "max": 8192,        # Максимум для 7B Q4
    },
}


# ═══════════════════════════════════════════════════════════
#  Результат оптимизации
# ═══════════════════════════════════════════════════════════

@dataclass
class ContextConfig:
    """Результат оптимизации контекстного окна.

    Attributes:
        n_ctx: Рекомендованный размер контекстного окна.
        tier: Тип модели.
        estimated_input_tokens: Оценка входных токенов.
        estimated_output_tokens: Оценка выходных токенов.
        headroom_tokens: Свободные токены для ответа.
        usage_ratio: Предполагаемый процент использования.
        strategy: Описание стратегии.
        warnings: Предупреждения.
    """
    n_ctx: int = 4096
    tier: str = "full"
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    headroom_tokens: int = 0
    usage_ratio: float = 0.0
    strategy: str = "default"
    warnings: list = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация."""
        return {
            "n_ctx": self.n_ctx,
            "tier": self.tier,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "headroom_tokens": self.headroom_tokens,
            "usage_ratio": round(self.usage_ratio, 3),
            "strategy": self.strategy,
            "warnings": self.warnings,
        }


# ═══════════════════════════════════════════════════════════
#  Утилиты
# ═══════════════════════════════════════════════════════════

def estimate_tokens(text: str, chars_per_token: float = CHARS_PER_TOKEN_RU) -> int:
    """Оценивает количество токенов в тексте.

    Args:
        text: Входной текст.
        chars_per_token: Символов на токен.

    Returns:
        Приблизительное количество токенов.
    """
    if not text:
        return 0
    return max(1, int(len(text) / chars_per_token))


# ═══════════════════════════════════════════════════════════
#  Публичный API
# ═══════════════════════════════════════════════════════════

def optimize_context(
    tier: str = "full",
    prompt_text: str = "",
    max_output_tokens: int = 0,
    force_n_ctx: Optional[int] = None,
) -> ContextConfig:
    """Оптимизирует размер контекстного окна.

    Стратегия:
      1. Если force_n_ctx → используем его
      2. Оцениваем входные токены
      3. Добавляем max_output_tokens
      4. Выбираем подходящий n_ctx
      5. Проверяем использование и warns

    Args:
        tier: Тип модели.
        prompt_text: Текст промпта (для оценки размера).
        max_output_tokens: Максимум токенов ответа.
        force_n_ctx: Принудительный n_ctx.

    Returns:
        ContextConfig с рекомендациями.
    """
    rec = RECOMMENDED_CTX.get(tier, RECOMMENDED_CTX["full"])

    config = ContextConfig(tier=tier)

    # 1. Принудительный n_ctx
    if force_n_ctx is not None and force_n_ctx >= MIN_CONTEXT:
        config.n_ctx = force_n_ctx
        config.strategy = f"forced={force_n_ctx}"
    else:
        config.n_ctx = rec["default"]
        config.strategy = "auto"

    # 2. Оценка входных токенов
    input_tokens = estimate_tokens(prompt_text) if prompt_text else 0
    config.estimated_input_tokens = input_tokens

    # 3. Оценка выходных токенов
    if max_output_tokens <= 0:
        # Дефолты из ModelProfile
        max_output_tokens = 1024
    config.estimated_output_tokens = max_output_tokens

    # 4. Подбор n_ctx (если auto)
    total_needed = input_tokens + max_output_tokens + 64  # 64 = safety margin
    if force_n_ctx is None:
        if total_needed > rec["default"]:
            # Нужен больший контекст
            config.n_ctx = min(total_needed + 128, rec["max"])
            config.strategy = f"auto_expanded: needed={total_needed}"
        elif total_needed < rec["default"] // 2:
            # Можно сэкономить (меньший контекст = быстрее)
            config.n_ctx = max(
                total_needed + 128,
                rec["min"],
            )
            config.strategy = f"auto_reduced: needed={total_needed}"

    # 5. Headroom
    config.headroom_tokens = max(0, config.n_ctx - input_tokens)

    # 6. Usage ratio
    if config.n_ctx > 0:
        config.usage_ratio = total_needed / config.n_ctx

    # 7. Предупреждения
    if config.usage_ratio > WARNING_THRESHOLD:
        config.warnings.append(
            f"High context usage: {config.usage_ratio:.0%} "
            f"({total_needed}/{config.n_ctx} tokens)"
        )

    if input_tokens > config.n_ctx:
        config.warnings.append(
            f"Input exceeds context window! "
            f"input={input_tokens} > n_ctx={config.n_ctx}"
        )

    logger.info(
        "Context config: tier=%s, n_ctx=%d, "
        "input=%d, output=%d, usage=%.0f%% (%s)",
        tier, config.n_ctx, input_tokens,
        max_output_tokens, config.usage_ratio * 100,
        config.strategy,
    )

    return config


def format_context_config(cfg: ContextConfig) -> str:
    """Форматирует конфигурацию контекста для CLI.

    Args:
        cfg: Конфигурация контекста.

    Returns:
        Человекочитаемая строка.
    """
    lines = [
        f"Context Window: {cfg.n_ctx} tokens",
        f"  Tier: {cfg.tier}",
        f"  Input estimate: {cfg.estimated_input_tokens} tokens",
        f"  Output max: {cfg.estimated_output_tokens} tokens",
        f"  Headroom: {cfg.headroom_tokens} tokens",
        f"  Usage: {cfg.usage_ratio:.0%}",
        f"  Strategy: {cfg.strategy}",
    ]
    if cfg.warnings:
        for w in cfg.warnings:
            lines.append(f"  ⚠ {w}")
    return "\n".join(lines)
