"""Pure multi-timeframe scoring.

``ScoringEngine`` combines a coin's per-timeframe recommendations into a single
weighted score. It performs no I/O and is fully deterministic given its inputs.
Longer timeframes are expected to carry larger weights, so they dominate the score.
"""

from __future__ import annotations

from dataclasses import dataclass

from trader.provider import (
    BUY,
    NEUTRAL,
    SELL,
    STRONG_BUY,
    STRONG_SELL,
    TimeframeResult,
)

# Numeric value assigned to each recommendation label.
LABEL_VALUES: dict[str, int] = {
    STRONG_BUY: 2,
    BUY: 1,
    NEUTRAL: 0,
    SELL: -1,
    STRONG_SELL: -2,
}


@dataclass(frozen=True)
class CoinScore:
    """A coin's weighted score together with the breakdown that produced it."""

    symbol: str
    score: float
    breakdown: tuple[TimeframeResult, ...]


class ScoringEngine:
    """Combines per-timeframe recommendations into one weighted score. Pure, no I/O."""

    def score(
        self,
        symbol: str,
        results: list[TimeframeResult],
        weights: dict[str, float],
    ) -> CoinScore:
        """Map each label to its numeric value, multiply by the timeframe weight, and sum.

        A timeframe with no configured weight defaults to a weight of ``0`` so it does
        not contribute to the score. Unknown recommendation labels are treated as
        ``NEUTRAL`` (value ``0``).
        """

        total = 0.0
        for result in results:
            value = LABEL_VALUES.get(result.recommendation, 0)
            weight = weights.get(result.interval, 0.0)
            total += value * weight

        return CoinScore(symbol=symbol, score=total, breakdown=tuple(results))
