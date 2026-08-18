"""Unit tests for the pure ScoringEngine (no network, deterministic)."""

from __future__ import annotations

from trader.provider import (
    NEUTRAL,
    STRONG_BUY,
    STRONG_SELL,
    TimeframeResult,
)
from trader.scoring import ScoringEngine


def _tf(interval: str, recommendation: str) -> TimeframeResult:
    return TimeframeResult(
        symbol="BTCUSDT",
        interval=interval,
        recommendation=recommendation,
        buy=0,
        neutral=0,
        sell=0,
    )


def test_longer_timeframe_weight_yields_pinned_negative_score() -> None:
    # 15m=STRONG_BUY (+2 * weight 1) + 4h=STRONG_SELL (-2 * weight 3) = 2 - 6 = -4.
    results = [_tf("15m", STRONG_BUY), _tf("4h", STRONG_SELL)]
    weights = {"15m": 1.0, "4h": 3.0}

    score = ScoringEngine().score("BTCUSDT", results, weights)

    assert score.score == -4
    assert len(score.breakdown) == 2


def test_all_neutral_scores_exactly_zero() -> None:
    results = [_tf("15m", NEUTRAL), _tf("1h", NEUTRAL), _tf("4h", NEUTRAL)]
    weights = {"15m": 1.0, "1h": 2.0, "4h": 3.0}

    score = ScoringEngine().score("BTCUSDT", results, weights)

    assert score.score == 0


def test_longer_timeframe_flips_outcome() -> None:
    # Short timeframe bullish, long timeframe bearish.
    results = [_tf("15m", STRONG_BUY), _tf("4h", STRONG_SELL)]

    # Equal weights -> +2 + (-2) = 0 (not positive, but not negative either); use a
    # short bull + long bear split where equal weights would be positive.
    results = [
        _tf("15m", STRONG_BUY),  # +2
        _tf("1h", STRONG_BUY),  # +2
        _tf("4h", STRONG_SELL),  # -2
    ]

    equal_weights = {"15m": 1.0, "1h": 1.0, "4h": 1.0}
    equal = ScoringEngine().score("BTCUSDT", results, equal_weights)
    assert equal.score > 0  # equal weights: +2 +2 -2 = +2

    long_biased_weights = {"15m": 1.0, "1h": 1.0, "4h": 5.0}
    biased = ScoringEngine().score("BTCUSDT", results, long_biased_weights)
    assert biased.score < 0  # +2 +2 -10 = -6, longer timeframe dominates


def test_breakdown_is_retained_for_display() -> None:
    results = [_tf("15m", STRONG_BUY), _tf("4h", STRONG_SELL)]

    score = ScoringEngine().score("BTCUSDT", results, {"15m": 1.0, "4h": 3.0})

    assert [r.interval for r in score.breakdown] == ["15m", "4h"]
    assert [r.recommendation for r in score.breakdown] == [STRONG_BUY, STRONG_SELL]
