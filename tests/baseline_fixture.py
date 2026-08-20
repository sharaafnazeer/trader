"""A deterministic multi-trade backtest fixture, shared by the measurement tasks.

The obvious choice does not work. ``backtest --demo`` produces **zero** trades: the demo
history sizes each timeframe by wall-clock span, leaving the daily frame with ~13 bars
against the backtester's 30-bar warm-up floor, so every evaluation resolves to no
direction. Worse, even with a longer demo history it produces exactly **one** trade,
because the backtester opens a trade only on a *fresh transition* into a qualifying state
and a monotonically bullish series qualifies continuously and never re-transitions.

A fixture pinning an empty — or single-trade — report would be satisfied by almost any
change to the scoring or replay path, which is the opposite of what a regression fixture
is for.

So this builds a series that alternates: a grinding advance that qualifies, then a decline
that disqualifies and resets the transition, repeated. One trade per cycle, wins and losses
both represented, and a short coin as well as a long one so the per-direction breakdown is
exercised too. Fully synthetic, no network, no wall clock.
"""

from __future__ import annotations

import pandas as pd

from trader.config import Config, StrategyConfig
from trader.market_data import Candles
from trader.replay import timeframe_to_ms

_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

# The fixture's config. Two timeframes keep the aligned grids small. There is no quality
# threshold to set any more — the 0-100 score it gated was retired with the indicators
# behind it — so what the fixture now pins is the direction rule, the replay and the
# planner: a change in any of them shows up as different R-multiples rather than silence.
FIXTURE_CONFIG = Config(
    watchlist=["UPCOIN", "DOWNCOIN"],
    timeframes=["1h", "4h"],
    reference_timeframe="4h",
    lead_timeframe="4h",
    htf_timeframes=["4h"],
    btc_veto=False,
    # The catalogue is off here on purpose. This fixture is a regression guard on the
    # *replay machinery* — slicing, the direction rule, the planner, the lifecycle — and a
    # synthetic series cannot satisfy a seven-condition checklist, so leaving it on would
    # pin zero trades and be satisfied by almost any change. The checklist's effect on the
    # backtest is pinned separately in test_backtest_checklist.py.
    strategies=StrategyConfig(enabled=False),
)

# Cycle shape: bars advancing, then bars declining. Sized so each cycle crosses the
# warm-up floor and completes a full qualify/disqualify round trip.
_ADVANCE_BARS = 70
_DECLINE_BARS = 40
_CYCLES = 8


def _cycling_frame(
    symbol: str, timeframe: str, *, rising: bool, scale: int
) -> Candles:
    """Alternating qualifying and disqualifying legs on a fixed time grid from t=0.

    ``scale`` multiplies the bar counts for finer timeframes so every timeframe spans the
    same wall-clock period and the grids align for point-in-time slicing. ``rising``
    inverts the shape so a coin trends down and produces shorts.
    """

    step = timeframe_to_ms(timeframe)
    up_bars, down_bars = _ADVANCE_BARS * scale, _DECLINE_BARS * scale
    trend, counter = (1.006, 0.991) if rising else (0.994, 1.009)

    rows: list[list[float]] = []
    base, stamp = 100.0, 0
    for _cycle in range(_CYCLES):
        for i in range(up_bars):
            base *= trend
            offset = {0: 0.0, 1: 0.004, 2: 0.007, 3: 0.004, 4: 0.0, 5: -0.003}[i % 6]
            close = base * (1.0 + offset)
            rows.append([stamp, close * 0.997, close * 1.010, close * 0.990, close, 1_000.0 + i])
            stamp += step
        for i in range(down_bars):
            base *= counter
            rows.append([stamp, base * 1.003, base * 1.008, base * 0.992, base, 1_000.0 + i])
            stamp += step

    return Candles(symbol=symbol, timeframe=timeframe, frame=pd.DataFrame(rows, columns=_COLUMNS))


def build_history() -> tuple[dict[str, dict[str, Candles]], dict[str, Candles]]:
    """The fixture's per-coin histories and the BTC context frames."""

    reference_step = timeframe_to_ms(FIXTURE_CONFIG.reference_timeframe)
    scales = {tf: reference_step // timeframe_to_ms(tf) for tf in FIXTURE_CONFIG.timeframes}

    histories = {
        "UPCOIN": {
            tf: _cycling_frame("UPCOIN", tf, rising=True, scale=scales[tf])
            for tf in FIXTURE_CONFIG.timeframes
        },
        "DOWNCOIN": {
            tf: _cycling_frame("DOWNCOIN", tf, rising=False, scale=scales[tf])
            for tf in FIXTURE_CONFIG.timeframes
        },
    }
    btc = {
        tf: _cycling_frame("BTC/USDT", tf, rising=True, scale=scales[tf])
        for tf in FIXTURE_CONFIG.timeframes
    }
    return histories, btc
