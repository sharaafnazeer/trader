"""Technical-feature computation from raw candles.

Turns a :class:`~trader.market_data.Candles` frame into a
:class:`TimeframeFeatures` bundle of the indicators the scoring model consumes:
EMA 20/50/200, RSI, MACD (line/signal/histogram), ROC, ATR, OBV (value + slope),
Bollinger-band width, and relative volume (current vs a rolling average).

This module belongs to the analysis core: it is pure and deterministic, performs no
I/O, and does not import ``typer`` or ``rich``. It builds on ``ta`` (pure-Python,
pandas-based) plus a little bespoke pandas/numpy for the OBV slope and relative
volume, so ``uv sync`` stays self-contained (no C toolchain).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from ta.momentum import ROCIndicator, RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import OnBalanceVolumeIndicator

from trader.market_data import Candles

# Default indicator windows. Kept here as in-code defaults for this task; task 07
# lifts the tunable subset onto the validated YAML config surface.
EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200
RSI_WINDOW = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
ROC_WINDOW = 12
ATR_WINDOW = 14
BOLLINGER_WINDOW = 20
VOLUME_WINDOW = 20
OBV_SLOPE_WINDOW = 20


@dataclass(frozen=True)
class TimeframeFeatures:
    """Computed technical features for one symbol on one timeframe.

    Every field is the latest (most recent candle) value of its indicator, except
    ``obv_slope`` and ``relative_volume`` which summarize recent behavior. Windows
    that lack enough history yield ``NaN`` from ``ta``; the longest EMA (200) needs at
    least 200 candles. When history is shorter than that, :func:`compute_features`
    degrades gracefully — it fills the slow EMA slot from the longest window the data
    supports (a "20/50"-style stack) and sets ``limited_history`` so callers can mark
    the result as lower-confidence. ``limited_history`` is ``False`` on a full frame.
    """

    symbol: str
    timeframe: str
    ema20: float
    ema50: float
    ema200: float
    rsi: float
    macd: float
    macd_signal: float
    macd_hist: float
    roc: float
    atr: float
    obv: float
    obv_slope: float
    bollinger_width: float
    relative_volume: float
    limited_history: bool = False


def _last(series: pd.Series) -> float:
    """The most recent value of a series as a plain ``float`` (may be ``NaN``)."""
    return float(series.iloc[-1])


def _slope(series: pd.Series, window: int) -> float:
    """Least-squares slope of a series' last ``window`` points (per-step).

    A positive value means the series is trending up over the window. Fewer than two
    finite points cannot define a slope, so ``0.0`` is returned in that case.
    """

    values = series.to_numpy(dtype=float)[-window:]
    finite = values[np.isfinite(values)]
    if finite.size < 2:
        return 0.0
    x = np.arange(finite.size, dtype=float)
    slope, _intercept = np.polyfit(x, finite, 1)
    return float(slope)


def compute_features(
    candles: Candles,
    *,
    volume_window: int = VOLUME_WINDOW,
    obv_slope_window: int = OBV_SLOPE_WINDOW,
) -> TimeframeFeatures:
    """Compute the technical features for one timeframe's candles.

    ``volume_window`` is the rolling window for the relative-volume average and
    ``obv_slope_window`` is the window over which the OBV slope is measured; both are
    exposed for tests and future configuration.
    """

    frame = candles.frame
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    volume = frame["volume"]

    def _ema(window: int) -> float:
        return _last(EMAIndicator(close, window=window).ema_indicator())

    ema20 = _ema(EMA_FAST)
    ema50 = _ema(EMA_MID)
    ema200 = _ema(EMA_SLOW)

    # Long-horizon timeframes (weekly/monthly) on younger coins often lack the 200
    # candles the slow EMA needs, leaving ``ema200`` (and sometimes ``ema50``) NaN.
    # Rather than dropping the coin, fall back to the longest EMA the available history
    # supports so the stack still tiers 20/50-style and resolves a trend, and flag the
    # result as limited-history so it can be surfaced as lower-confidence.
    n = len(close)
    limited_history = not math.isfinite(ema200)
    if limited_history:
        ema200 = _ema(min(EMA_SLOW, n))
        if not math.isfinite(ema50):
            ema50 = _ema(min(EMA_MID, n))

    rsi = _last(RSIIndicator(close, window=RSI_WINDOW).rsi())

    macd_ind = MACD(
        close,
        window_slow=MACD_SLOW,
        window_fast=MACD_FAST,
        window_sign=MACD_SIGNAL,
    )
    macd = _last(macd_ind.macd())
    macd_signal = _last(macd_ind.macd_signal())
    macd_hist = _last(macd_ind.macd_diff())

    roc = _last(ROCIndicator(close, window=ROC_WINDOW).roc())

    atr = _last(
        AverageTrueRange(high=high, low=low, close=close, window=ATR_WINDOW).average_true_range()
    )

    obv_series = OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume()
    obv = _last(obv_series)
    obv_slope = _slope(obv_series, obv_slope_window)

    bands = BollingerBands(close, window=BOLLINGER_WINDOW)
    hband = _last(bands.bollinger_hband())
    lband = _last(bands.bollinger_lband())
    mavg = _last(bands.bollinger_mavg())
    # Normalized band width; guard the zero-mean edge case.
    bollinger_width = (hband - lband) / mavg if mavg else 0.0

    # Relative volume: latest candle's volume over the mean of the last
    # ``volume_window`` candles (inclusive). 1.0 means an average-sized candle.
    avg_volume = float(volume.iloc[-volume_window:].mean())
    relative_volume = float(volume.iloc[-1]) / avg_volume if avg_volume else 0.0

    return TimeframeFeatures(
        symbol=candles.symbol,
        timeframe=candles.timeframe,
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        rsi=rsi,
        macd=macd,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        roc=roc,
        atr=atr,
        obv=obv,
        obv_slope=obv_slope,
        bollinger_width=bollinger_width,
        relative_volume=relative_volume,
        limited_history=limited_history,
    )
