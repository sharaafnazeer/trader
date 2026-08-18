"""Unit tests for the technical-feature computation (pure, no network).

Hand-built OHLCV frames with known shapes drive the indicators; assertions pin the
externally-observable outcomes (EMA ordering, RSI sign, ATR positivity, OBV slope,
relative-volume ratio) rather than internal mechanics.
"""

from __future__ import annotations

import math

import pandas as pd

from trader.indicators import compute_features
from trader.market_data import Candles


def _candles_from_closes(closes: list[float], volumes: list[float] | None = None) -> Candles:
    """Build a Candles frame from a close series.

    High/low straddle the close by a fixed band so ATR is well-defined; volume is
    flat unless overridden.
    """

    if volumes is None:
        volumes = [1_000.0] * len(closes)
    rows = []
    for i, (close, volume) in enumerate(zip(closes, volumes, strict=True)):
        prev = closes[i - 1] if i > 0 else close
        open_ = prev
        high = max(open_, close) + 1.0
        low = min(open_, close) - 1.0
        rows.append([i, open_, high, low, close, volume])
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return Candles(symbol="TEST/USDT", timeframe="4h", frame=frame)


def test_rising_series_has_stacked_emas_and_rsi_above_50() -> None:
    closes = [100.0 + i for i in range(260)]  # strictly rising
    features = compute_features(_candles_from_closes(closes))

    assert features.ema20 > features.ema50 > features.ema200
    assert features.rsi > 50.0


def test_falling_series_has_inverted_emas_and_rsi_below_50() -> None:
    closes = [1000.0 - i for i in range(260)]  # strictly falling
    features = compute_features(_candles_from_closes(closes))

    assert features.ema20 < features.ema50 < features.ema200
    assert features.rsi < 50.0


def test_atr_positive_and_relative_volume_is_current_over_rolling_average() -> None:
    closes = [100.0 + (i % 5) for i in range(60)]
    # Flat volume of 100 for the window, then a final spike so the ratio is known.
    volumes = [100.0] * 59 + [400.0]
    candles = _candles_from_closes(closes, volumes)

    features = compute_features(candles, volume_window=20)

    assert features.atr > 0.0
    # Rolling average over the last 20 candles = (19*100 + 400)/20 = 115.0.
    expected_avg = (19 * 100.0 + 400.0) / 20
    assert math.isclose(features.relative_volume, 400.0 / expected_avg, rel_tol=1e-9)


def test_obv_slope_positive_when_price_and_volume_rise_together() -> None:
    closes = [100.0 + i for i in range(60)]  # rising price
    volumes = [100.0 + 10.0 * i for i in range(60)]  # rising volume
    features = compute_features(_candles_from_closes(closes, volumes))

    # Rising closes accumulate positive volume into OBV, so its recent slope is up.
    assert features.obv_slope > 0.0


def test_obv_slope_negative_when_price_falls() -> None:
    closes = [1000.0 - i for i in range(60)]  # falling price
    volumes = [100.0 + 10.0 * i for i in range(60)]
    features = compute_features(_candles_from_closes(closes, volumes))

    # Falling closes subtract volume from OBV, so its recent slope is down.
    assert features.obv_slope < 0.0


def test_full_history_frame_is_not_limited_and_has_finite_slow_ema() -> None:
    closes = [100.0 + i for i in range(260)]  # >= EMA200 window
    features = compute_features(_candles_from_closes(closes))

    assert features.limited_history is False
    assert math.isfinite(features.ema200)


def test_short_frame_is_limited_history_with_a_fallback_stack() -> None:
    # 120 candles: enough for EMA20/EMA50 but short of the 200 the slow EMA needs.
    closes = [100.0 + i for i in range(120)]  # strictly rising
    features = compute_features(_candles_from_closes(closes))

    # Degraded rather than dropped: flagged limited-history with a finite fallback slow
    # EMA, and the fallback stack still tiers cleanly so a trend resolves (20/50-style).
    assert features.limited_history is True
    assert math.isfinite(features.ema200)
    assert features.ema20 > features.ema50 > features.ema200
