"""Technical-feature computation from raw candles.

Turns a :class:`~trader.market_data.Candles` frame into a :class:`TimeframeFeatures`
bundle. The set of indicators is **closed** and is exactly the one the trader's method
uses — see ``.sdd/strategy-catalogue/rules.md``:

    EMA10, EMA21, EMA50, SMA200, Stochastic RSI (%K/%D), MACD (line/signal/histogram),
    ATR, volume (raw and relative).

Nothing else is computed. Indicators the engine once carried but the method does not use
— EMA20, EMA200, standalone RSI, rate of change, on-balance volume and Bollinger band
width — were removed on 2026-08-20 rather than left unread. Adding to this list is a
decision for the trader, not a convenience for a caller that wants one more number.

Note that the 200 is a **simple** moving average, not an exponential one. That is a
deliberate part of the method, not an oversight: it is the long-term filter the whole
stack is judged against.

This module belongs to the analysis core: it is pure and deterministic, performs no
I/O, and does not import ``typer`` or ``rich``. It builds on ``ta`` (pure-Python,
pandas-based) plus a little bespoke pandas for relative volume, so ``uv sync`` stays
self-contained (no C toolchain).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd
from ta.momentum import StochRSIIndicator
from ta.trend import MACD, EMAIndicator, SMAIndicator
from ta.volatility import AverageTrueRange

from trader.config import CROSS_DETECTION_WINDOW
from trader.market_data import Candles

# The method's moving-average stack. The 200 is simple; the rest are exponential.
EMA_FAST = 10
EMA_MID = 21
EMA_SLOW = 50
SMA_LONG = 200

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
ATR_WINDOW = 14
VOLUME_WINDOW = 20

# Stochastic RSI: the RSI's own position within its recent range, smoothed twice. It
# answers a question plain RSI cannot — whether momentum is *turning* inside a trend —
# which is why the method reads it as a cross from an extreme rather than as a level.
STOCH_RSI_WINDOW = 14
STOCH_RSI_SMOOTH_K = 3
STOCH_RSI_SMOOTH_D = 3



# The names the closed indicator set is allowed to contain. The guard test enumerates the
# dataclass against this, so adding a field without a decision fails the build rather than
# quietly widening the set.
CLOSED_INDICATOR_SET: frozenset[str] = frozenset(
    {
        "symbol",
        "timeframe",
        "ema10",
        "ema21",
        "ema50",
        "sma200",
        "macd",
        "macd_signal",
        "macd_hist",
        "macd_crossed_up",
        "macd_crossed_down",
        "macd_cross_bars_ago",
        "atr",
        "volume",
        "relative_volume",
        "stoch_rsi_k",
        "stoch_rsi_d",
        "stoch_crossed_up",
        "stoch_crossed_down",
        "stoch_cross_bars_ago",
        "stoch_cross_extreme",
        "limited_history",
    }
)


@dataclass(frozen=True)
class TimeframeFeatures:
    """The method's indicators for one symbol on one timeframe.

    Every value is the latest candle's, except ``relative_volume`` which compares the
    latest candle against a rolling average, and the four crossover flags which describe
    an *event* within the last :data:`DEFAULT_CROSS_LOOKBACK` bars rather than a level.

    Windows that lack enough history yield ``NaN``. ``sma200`` in particular needs 200
    candles and is **not** back-filled from a shorter window: a 60-bar average reported in
    the 200's place would be a different indicator wearing its name, and the stack
    condition is judged on it. ``limited_history`` marks that case so callers can render
    the coin as lower-confidence; a coin whose long-term filter is absent cannot resolve a
    direction, which is the intended outcome rather than a gap to paper over.
    """

    symbol: str
    timeframe: str
    ema10: float
    ema21: float
    ema50: float
    sma200: float
    macd: float
    macd_signal: float
    macd_hist: float
    atr: float
    volume: float
    relative_volume: float
    stoch_rsi_k: float = math.nan
    stoch_rsi_d: float = math.nan
    # Crossover *events*: the line crossed its counterpart within the lookback, in the
    # named direction. Both false is the ordinary case — no recent cross either way.
    macd_crossed_up: bool = False
    macd_crossed_down: bool = False
    # How many bars ago the MACD line last crossed its signal (``0`` on the crossing bar), or
    # ``None`` when none happened inside the detection window. The line's side of zero needs
    # no field of its own — it is the sign of ``macd``.
    macd_cross_bars_ago: int | None = None
    stoch_crossed_up: bool = False
    stoch_crossed_down: bool = False
    # How many bars ago the most recent Stochastic RSI crossing happened (``0`` on the
    # crossing bar itself), or ``None`` when none happened inside the detection window.
    stoch_cross_bars_ago: int | None = None
    # The extreme %K reached going *into* that cross — the lowest for an upward cross, the
    # highest for a downward one. This is the raw measurement; whether it counts as oversold
    # or overbought is a policy the strategy applies against its configured thresholds.
    stoch_cross_extreme: float = math.nan
    limited_history: bool = False


def _last(series: pd.Series) -> float:
    """The most recent value of a series as a plain ``float`` (may be ``NaN``)."""
    return float(series.iloc[-1])


def _latest_cross(
    fast: pd.Series, slow: pd.Series, window: int
) -> tuple[int | None, bool]:
    """The most recent crossing of ``fast`` over ``slow``: how long ago, and which way.

    A cross is a *change of side* between two consecutive bars, so it is an event with a
    date rather than a standing condition — which is the whole point, because "momentum is
    above its signal" is true for long stretches of any trend while "momentum just turned"
    is not. Bars where either series is undefined are not crossings; an indicator warming up
    has no side to change.

    Returns ``(bars_ago, was_upward)``, with ``bars_ago`` of ``0`` on the crossing bar
    itself, or ``(None, False)`` when nothing crossed inside ``window``.
    """

    f = fast.to_numpy(dtype=float)
    s = slow.to_numpy(dtype=float)
    n = min(len(f), len(s))
    if n < 2:
        return None, False

    # Walk backwards so the first crossing found is the most recent one.
    for i in range(n - 1, max(0, n - 1 - window), -1):
        prev_f, prev_s, cur_f, cur_s = f[i - 1], s[i - 1], f[i], s[i]
        if not all(math.isfinite(v) for v in (prev_f, prev_s, cur_f, cur_s)):
            continue
        if prev_f <= prev_s and cur_f > cur_s:
            return n - 1 - i, True
        if prev_f >= prev_s and cur_f < cur_s:
            return n - 1 - i, False
    return None, False


def _extreme_into_cross(
    series: pd.Series, bars_ago: int | None, upward: bool, window: int
) -> float:
    """The extreme value ``series`` reached in the bars leading up to a cross.

    The lowest for an upward cross and the highest for a downward one — "where did momentum
    turn *from*". Measured over the ``window`` bars ending at the crossing bar, so a turn
    that began deep in oversold territory is distinguishable from one that happened
    mid-range, which is the distinction between a signal and noise.
    """

    if bars_ago is None:
        return math.nan
    values = series.to_numpy(dtype=float)
    end = len(values) - bars_ago  # exclusive: up to and including the crossing bar
    start = max(0, end - window)
    segment = [v for v in values[start:end] if math.isfinite(v)]
    if not segment:
        return math.nan
    return float(min(segment)) if upward else float(max(segment))


def compute_features(
    candles: Candles,
    *,
    volume_window: int = VOLUME_WINDOW,
    cross_window: int = CROSS_DETECTION_WINDOW,
) -> TimeframeFeatures:
    """Compute the method's indicators for one timeframe's candles.

    ``volume_window`` is the rolling window for the relative-volume average;
    ``cross_window`` is how far back a MACD or Stochastic RSI crossing is *detected* (not
    how recent it must be to matter — that is the strategy's policy). Both are exposed for
    tests.
    """

    frame = candles.frame
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    volume = frame["volume"]

    def _ema(window: int) -> float:
        return _last(EMAIndicator(close, window=window).ema_indicator())

    ema10 = _ema(EMA_FAST)
    ema21 = _ema(EMA_MID)
    ema50 = _ema(EMA_SLOW)
    sma200 = _last(SMAIndicator(close, window=SMA_LONG).sma_indicator())

    # A missing long-term filter is reported, never substituted. See the class docstring.
    limited_history = not math.isfinite(sma200)

    macd_ind = MACD(
        close,
        window_slow=MACD_SLOW,
        window_fast=MACD_FAST,
        window_sign=MACD_SIGNAL,
    )
    macd_line = macd_ind.macd()
    macd_sig = macd_ind.macd_signal()
    macd_bars_ago, macd_up = _latest_cross(macd_line, macd_sig, cross_window)

    atr = _last(
        AverageTrueRange(high=high, low=low, close=close, window=ATR_WINDOW).average_true_range()
    )

    # Relative volume: latest candle's volume over the mean of the last
    # ``volume_window`` candles (inclusive). 1.0 means an average-sized candle.
    latest_volume = float(volume.iloc[-1])
    avg_volume = float(volume.iloc[-volume_window:].mean())
    relative_volume = latest_volume / avg_volume if avg_volume else 0.0

    # Stochastic RSI. Left as NaN rather than defaulted: a fabricated 0 or 0.5 would read as
    # "momentum is at the bottom of its range" or "mid-range", both of which are claims.
    stoch = StochRSIIndicator(
        close,
        window=STOCH_RSI_WINDOW,
        smooth1=STOCH_RSI_SMOOTH_K,
        smooth2=STOCH_RSI_SMOOTH_D,
        fillna=False,
    )
    stoch_k = stoch.stochrsi_k()
    stoch_d = stoch.stochrsi_d()
    stoch_bars_ago, stoch_up = _latest_cross(stoch_k, stoch_d, cross_window)
    stoch_extreme = _extreme_into_cross(stoch_k, stoch_bars_ago, stoch_up, cross_window)

    return TimeframeFeatures(
        symbol=candles.symbol,
        timeframe=candles.timeframe,
        ema10=ema10,
        ema21=ema21,
        ema50=ema50,
        sma200=sma200,
        macd=_last(macd_line),
        macd_signal=_last(macd_sig),
        macd_hist=_last(macd_ind.macd_diff()),
        atr=atr,
        volume=latest_volume,
        relative_volume=relative_volume,
        stoch_rsi_k=_last(stoch_k),
        stoch_rsi_d=_last(stoch_d),
        macd_crossed_up=macd_bars_ago is not None and macd_up,
        macd_crossed_down=macd_bars_ago is not None and not macd_up,
        macd_cross_bars_ago=macd_bars_ago,
        stoch_crossed_up=stoch_bars_ago is not None and stoch_up,
        stoch_crossed_down=stoch_bars_ago is not None and not stoch_up,
        stoch_cross_bars_ago=stoch_bars_ago,
        stoch_cross_extreme=stoch_extreme,
        limited_history=limited_history,
    )
