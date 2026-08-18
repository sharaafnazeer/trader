"""Unit tests for the pure momentum ("movers") scoring model and its helpers.

No network and no I/O: every input is a hand-built candle frame and a pinned BTC
benchmark return. Covers the relative-strength effect (holding raw return constant so
it is isolated), breakout + volume vs a flat drift, weight sensitivity, direction
tagging, the populated per-factor breakdown, the pure helpers on pinned values (long
and the short mirror), and the liquidity filter.
"""

from __future__ import annotations

import pandas as pd
import pytest

from trader.config import (
    DEFAULT_MOMENTUM_WEIGHTS,
    MOMENTUM_FACTOR_ACCELERATION,
    MOMENTUM_FACTOR_BREAKOUT,
    MOMENTUM_FACTOR_RELATIVE_STRENGTH,
    MOMENTUM_FACTOR_VOLUME,
)
from trader.direction import Direction
from trader.market_data import Candles, OrderBook
from trader.momentum import (
    MomentumModel,
    breakout_fraction,
    passes_liquidity,
    period_return,
    relative_strength,
)


def _frame(rows: list[list[float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _linear_up(symbol: str = "AAA", rows: int = 60, volume: float = 1_000.0) -> Candles:
    """A steady linear uptrend on flat volume (a drift, no volume expansion)."""
    data = []
    for i in range(rows):
        close = 100.0 + i
        data.append([i, close - 1.0, close + 1.0, close - 2.0, close, volume])
    return Candles(symbol=symbol, timeframe="1d", frame=_frame(data))


def _linear_down(symbol: str = "AAA", rows: int = 60, volume: float = 1_000.0) -> Candles:
    """A steady linear downtrend on flat volume."""
    data = []
    for i in range(rows):
        close = 200.0 - i
        data.append([i, close + 1.0, close + 2.0, close - 1.0, close, volume])
    return Candles(symbol=symbol, timeframe="1d", frame=_frame(data))


def _breakout_candles(symbol: str = "AAA", rows: int = 60) -> Candles:
    """A range that then breaks to a new high on a large-volume final candle."""
    data = []
    for i in range(rows - 1):
        # A tight range around 100 on average volume.
        close = 100.0 + (i % 3)
        data.append([i, close - 0.5, close + 1.0, close - 1.0, close, 1_000.0])
    # Final candle: a decisive breakout well above the range on triple volume.
    data.append([rows - 1, 103.0, 130.0, 102.0, 128.0, 3_000.0])
    return Candles(symbol=symbol, timeframe="1d", frame=_frame(data))


# --- helpers ---------------------------------------------------------------


def test_relative_strength_is_the_return_difference() -> None:
    assert relative_strength(0.30, 0.05) == 0.25
    assert relative_strength(0.10, 0.10) == 0.0
    assert relative_strength(0.05, 0.20) == pytest.approx(-0.15)


def test_breakout_fraction_long_atr_normalized() -> None:
    # A full ATR above the trailing high earns full credit; inside the range earns none.
    assert breakout_fraction(110.0, 100.0, 10.0, Direction.LONG) == 1.0
    assert breakout_fraction(105.0, 100.0, 10.0, Direction.LONG) == 0.5
    assert breakout_fraction(95.0, 100.0, 10.0, Direction.LONG) == 0.0
    # A non-positive ATR yields no credit rather than dividing by zero.
    assert breakout_fraction(110.0, 100.0, 0.0, Direction.LONG) == 0.0


def test_breakout_fraction_short_mirror() -> None:
    # For a short the mirror measures how far below the trailing low the close is.
    assert breakout_fraction(90.0, 100.0, 10.0, Direction.SHORT) == 1.0
    assert breakout_fraction(95.0, 100.0, 10.0, Direction.SHORT) == 0.5
    assert breakout_fraction(105.0, 100.0, 10.0, Direction.SHORT) == 0.0


def test_period_return_over_lookback() -> None:
    candles = _linear_up(rows=31)  # closes 100..130
    # Return over the last 30 candles: 130 / 100 - 1 = 0.30.
    assert period_return(candles, 30) == 130.0 / 100.0 - 1.0


# --- relative-strength effect (raw return held constant) -------------------


def test_relative_strength_effect_isolated() -> None:
    """Two coins with identical candles (identical raw return) scored against a
    different BTC benchmark: the outperformer earns more RS credit and a higher total."""

    coin = _linear_up()
    model = MomentumModel()

    # Same coin, same raw return; only the benchmark differs.
    outperformer = model.score("OUT", coin, btc_return=0.0)  # BTC flat -> coin leads
    market_neutral = model.score("MKT", coin, btc_return=period_return(coin, 30))

    rs_out = outperformer.factor(MOMENTUM_FACTOR_RELATIVE_STRENGTH)
    rs_mkt = market_neutral.factor(MOMENTUM_FACTOR_RELATIVE_STRENGTH)
    assert rs_out is not None and rs_mkt is not None
    assert rs_out.fraction > rs_mkt.fraction
    assert outperformer.score > market_neutral.score


def test_breakout_and_volume_beat_flat_drift() -> None:
    model = MomentumModel()
    breakout = model.score("BRK", _breakout_candles(), btc_return=0.0)
    drift = model.score("DFT", _linear_up(), btc_return=0.0)

    brk_breakout = breakout.factor(MOMENTUM_FACTOR_BREAKOUT)
    brk_volume = breakout.factor(MOMENTUM_FACTOR_VOLUME)
    dft_breakout = drift.factor(MOMENTUM_FACTOR_BREAKOUT)
    dft_volume = drift.factor(MOMENTUM_FACTOR_VOLUME)
    assert brk_breakout is not None and dft_breakout is not None
    assert brk_volume is not None and dft_volume is not None

    assert brk_breakout.fraction > dft_breakout.fraction
    assert brk_volume.fraction > dft_volume.fraction
    assert breakout.score > drift.score


# --- weight sensitivity ----------------------------------------------------


def test_weight_increase_raises_score_for_earned_factor() -> None:
    coin = _breakout_candles()
    model = MomentumModel()

    base = model.score("AAA", coin, btc_return=0.0, weights=dict(DEFAULT_MOMENTUM_WEIGHTS))
    # The breakout candle earns clear breakout credit; doubling its weight must lift the
    # total (holding the other weights fixed).
    heavier = dict(DEFAULT_MOMENTUM_WEIGHTS)
    heavier[MOMENTUM_FACTOR_BREAKOUT] = heavier[MOMENTUM_FACTOR_BREAKOUT] * 2.0
    boosted = model.score("AAA", coin, btc_return=0.0, weights=heavier)

    breakout = base.factor(MOMENTUM_FACTOR_BREAKOUT)
    assert breakout is not None and breakout.fraction > 0.0
    assert boosted.score > base.score


# --- direction + breakdown -------------------------------------------------


def test_up_momentum_is_long_and_down_is_short() -> None:
    model = MomentumModel()
    assert model.score("UP", _linear_up(), btc_return=0.0).direction is Direction.LONG
    assert model.score("DN", _linear_down(), btc_return=0.0).direction is Direction.SHORT


def test_breakdown_is_populated_for_all_factors() -> None:
    score = MomentumModel().score("AAA", _breakout_candles(), btc_return=0.0)
    names = {factor.name for factor in score.factors}
    assert names == {
        MOMENTUM_FACTOR_RELATIVE_STRENGTH,
        MOMENTUM_FACTOR_BREAKOUT,
        MOMENTUM_FACTOR_VOLUME,
        MOMENTUM_FACTOR_ACCELERATION,
    }
    for factor in score.factors:
        assert 0.0 <= factor.fraction <= 1.0
        assert factor.points == factor.fraction * factor.weight
    assert score.score == sum(f.points for f in score.factors)


def test_short_mover_scores_on_downside_momentum() -> None:
    # A falling coin against a flat/rising BTC is a strong short candidate: it earns RS
    # credit (it underperforms) and acceleration credit (ROC strongly negative).
    score = MomentumModel().score("DN", _linear_down(), btc_return=0.0)
    assert score.direction is Direction.SHORT
    rs = score.factor(MOMENTUM_FACTOR_RELATIVE_STRENGTH)
    accel = score.factor(MOMENTUM_FACTOR_ACCELERATION)
    assert rs is not None and accel is not None
    assert rs.fraction > 0.0
    assert accel.fraction > 0.0


# --- liquidity filter ------------------------------------------------------


def _book(bid_depth: float, ask_depth: float, best_bid: float, best_ask: float) -> OrderBook:
    return OrderBook(
        symbol="AAA",
        best_bid=best_bid,
        best_ask=best_ask,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
    )


def test_liquidity_filter_excludes_thin_or_wide_books() -> None:
    # ``min_depth`` is a quote-notional (USDT) threshold: base depth × mid price, so it
    # is unit-consistent across coins of any unit price.
    liquid = _book(1_000.0, 1_000.0, 100.0, 100.2)  # ~100k notional, tight spread
    assert passes_liquidity(liquid, min_depth=25_000.0, max_spread=0.005)

    thin = _book(10.0, 1_000.0, 100.0, 100.2)  # binding side ~1k notional, below floor
    assert not passes_liquidity(thin, min_depth=25_000.0, max_spread=0.005)

    wide = _book(1_000.0, 1_000.0, 100.0, 105.0)  # deep but ~4.9% spread, above max
    assert not passes_liquidity(wide, min_depth=25_000.0, max_spread=0.005)


def test_high_priced_coin_is_not_mis_flagged_illiquid() -> None:
    # Regression: a high-unit-price coin with only a few base units of depth is still
    # deeply liquid in notional terms — 3 BTC near $100k is ~$300k, far above the floor.
    # A flat *base-asset* floor (e.g. 25,000 units) wrongly rejected exactly these coins.
    btc = _book(3.0, 3.0, 100_000.0, 100_050.0)
    assert passes_liquidity(btc, min_depth=25_000.0, max_spread=0.005)
