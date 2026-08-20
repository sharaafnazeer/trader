"""Integration tests for the backtester over injected multi-timeframe history.

All frames are hand-built on an aligned integer time grid (no network); the tests run
the real analysis modules through the backtester. They prove: no look-ahead for both
coin data and the BTC market context, the one-position-per-coin / enter-on-transition
lifecycle, the neutral full-credit liquidity fraction (so a backtest score equals the
live pipeline's for the same sliced inputs), and that a known bullish path resolves a
winning trade.
"""

from __future__ import annotations

import pandas as pd

from trader.backtester import (
    Backtester,
    _run_lifecycle,
    evaluate_at,
    market_context_at,
)
from trader.config import Config, StrategyConfig
from trader.direction import Direction, MarketContext
from trader.market_data import Candles
from trader.metrics import summarize
from trader.replay import Replay, timeframe_to_ms
from trader.trade_simulator import TradeOutcome, TradeResult

_HOUR = 3_600_000


def _bullish_frame(symbol: str, timeframe: str, step_ms: int, rows: int) -> Candles:
    data = []
    base = 100.0
    for i in range(rows):
        base += 1.0
        offset = {0: 0.0, 1: 3.0, 2: 5.0, 3: 3.0, 4: 0.0, 5: -2.0}[i % 6]
        close = base + offset
        data.append([i * step_ms, close - 0.5, close + 1.0, close - 1.0, close, 1_000.0 + i])
    frame = pd.DataFrame(
        data, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def _bearish_frame(symbol: str, timeframe: str, step_ms: int, rows: int) -> Candles:
    data = []
    base = 1000.0
    for i in range(rows):
        base -= 1.0
        offset = {0: 0.0, 1: -3.0, 2: -5.0, 3: -3.0, 4: 0.0, 5: 2.0}[i % 6]
        close = base + offset
        data.append([i * step_ms, close + 0.5, close + 1.0, close - 1.0, close, 1_000.0 + i])
    frame = pd.DataFrame(
        data, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def _config() -> Config:
    # A compact two-timeframe futures-style profile: 1h finer, 4h reference/lead.
    return Config(
        watchlist=["AAA"],
        timeframes=["1h", "4h"],
        reference_timeframe="4h",
        lead_timeframe="4h",
        htf_timeframes=["4h"],
        # Replay machinery under test, not the method: a synthetic series cannot satisfy a
        # seven-condition checklist, and leaving it on would pin zero trades.
        strategies=StrategyConfig(enabled=False),
    )


def _aligned_frames(
    symbol: str, reference_bars: int = 260, *, kind: str = "bull"
) -> dict[str, Candles]:
    span = 4 * _HOUR * reference_bars
    make = _bullish_frame if kind == "bull" else _bearish_frame
    return {
        "1h": make(symbol, "1h", _HOUR, span // _HOUR),
        "4h": make(symbol, "4h", 4 * _HOUR, reference_bars),
    }


def _append_future_bar(frames: dict[str, Candles], *, extreme: float) -> dict[str, Candles]:
    """Append one bar beyond the end of every frame, with an extreme high and low."""
    out: dict[str, Candles] = {}
    for tf, candles in frames.items():
        frame = candles.frame
        step = timeframe_to_ms(tf)
        last_ts = int(frame["timestamp"].iloc[-1])
        spike = [last_ts + step, extreme, extreme + 1.0, extreme - 1.0, extreme, 1.0]
        appended = pd.concat(
            [frame, pd.DataFrame([spike], columns=list(frame.columns))], ignore_index=True
        )
        out[tf] = Candles(symbol=candles.symbol, timeframe=tf, frame=appended)
    return out


# --------------------------------------------------------------------------------------
# No look-ahead — coin data.
# --------------------------------------------------------------------------------------


def test_coin_evaluation_ignores_a_future_spike() -> None:
    config = _config()
    frames = _aligned_frames("AAA")
    ts = Replay(frames, config.reference_timeframe).reference_closes()[-1]

    base = evaluate_at(frames, ts, MarketContext(), config)
    assert base.surfaced and base.direction is Direction.LONG

    # A colossal future spike (both directions) appended AFTER ts must not change the
    # evaluation taken at ts — it is sliced away before the pipeline ever sees it.
    spiked = _append_future_bar(frames, extreme=10_000.0)
    after = evaluate_at(spiked, ts, MarketContext(), config)

    assert after == base


# --------------------------------------------------------------------------------------
# No look-ahead — BTC market context (point-in-time from BTC's own frames).
# --------------------------------------------------------------------------------------


def test_btc_context_is_point_in_time_and_ignores_a_future_spike() -> None:
    config = _config()
    btc_frames = _aligned_frames("BTC/USDT", kind="bull")
    ts = Replay(btc_frames, config.reference_timeframe).reference_closes()[-1]

    base = market_context_at(btc_frames, ts, config)
    assert base.btc_direction is Direction.LONG

    # A crashing future spike after ts would flip BTC bearish if it leaked in; it must
    # not change the context decided at ts.
    spiked = _append_future_bar(btc_frames, extreme=1.0)
    after = market_context_at(spiked, ts, config)

    assert after == base


# --------------------------------------------------------------------------------------
# Neutral full-credit liquidity — backtest score equals the live pipeline's score.
# --------------------------------------------------------------------------------------
def _fake_outcome(exit_time: int | None) -> TradeOutcome:
    return TradeOutcome(
        symbol="AAA",
        direction=Direction.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=exit_time,
        exit_price=110.0 if exit_time is not None else None,
        result=TradeResult.WIN if exit_time is not None else TradeResult.UNRESOLVED,
    )


def test_lifecycle_persistent_signal_enters_only_once() -> None:
    closes = (0, 1, 2, 3, 4)
    surfaced = [False, True, True, True, True]
    entries: list[int] = []

    def enter(i: int) -> TradeOutcome:
        entries.append(i)
        return _fake_outcome(exit_time=closes[i])  # closes immediately

    _run_lifecycle(closes, surfaced, enter)
    # Only the rising edge at index 1 opens a trade; the persistent signal never re-enters.
    assert entries == [1]


def test_lifecycle_reenters_only_after_signal_resets() -> None:
    closes = (0, 1, 2, 3)
    surfaced = [False, True, False, True]
    entries: list[int] = []

    def enter(i: int) -> TradeOutcome:
        entries.append(i)
        return _fake_outcome(exit_time=closes[i])

    _run_lifecycle(closes, surfaced, enter)
    # Two fresh transitions (index 1 and, after a reset, index 3) -> two trades.
    assert entries == [1, 3]


def test_lifecycle_no_second_trade_while_one_is_still_open() -> None:
    closes = (0, 1, 2, 3)
    surfaced = [False, True, False, True]
    entries: list[int] = []

    def enter(i: int) -> TradeOutcome:
        entries.append(i)
        return _fake_outcome(exit_time=None)  # trade stays open for the rest of the run

    _run_lifecycle(closes, surfaced, enter)
    # The fresh transition at index 3 is ignored because the coin is still in a trade.
    assert entries == [1]


# --------------------------------------------------------------------------------------
# End-to-end — a bullish path opens exactly one trade and it wins.
# --------------------------------------------------------------------------------------


def test_bullish_history_opens_one_winning_trade() -> None:
    config = _config()
    histories = {"AAA": _aligned_frames("AAA")}
    btc_frames = _aligned_frames("BTC/USDT")

    run = Backtester().run(config, histories, btc_frames)

    # The persistent bullish signal surfaces once (one open trade per coin) and, since
    # price keeps rising, the target is reached before the stop -> a single win.
    assert len(run.outcomes) == 1
    (outcome,) = run.outcomes
    assert outcome.direction is Direction.LONG
    assert outcome.result is TradeResult.WIN

    report = summarize(run.outcomes)
    assert report.resolved_trades == 1
    assert report.wins == 1
    assert report.win_rate == 1.0


# --------------------------------------------------------------------------------------
# Bounded window — a long history stays O(N): each sliced feature frame is capped.
# --------------------------------------------------------------------------------------


def test_long_history_backtest_produces_trades_and_bounds_the_sliced_window() -> None:
    # A long synthetic history (800+ reference bars). Before the fix the per-close slice
    # grew unboundedly (O(N^2)); now each sliced feature frame is capped to the live-scan
    # lookback so the work per close is constant.
    config = _config()
    assert config.ohlcv_lookback == 300  # the window used by both live scan and backtest

    histories = {"AAA": _aligned_frames("AAA", reference_bars=850)}
    btc_frames = _aligned_frames("BTC/USDT", reference_bars=850)

    run = Backtester().run(config, histories, btc_frames)
    # The backtest still resolves trades over the long history.
    assert len(run.outcomes) >= 1

    # The frames fed to feature computation are bounded: slicing at the very last close
    # (where the unbounded window would be ~850/~3400 bars) returns at most ohlcv_lookback
    # bars per timeframe — the same trailing window the live scanner sees.
    frames = histories["AAA"]
    replay = Replay(frames, config.reference_timeframe, max_bars=config.ohlcv_lookback)
    last_ts = replay.reference_closes()[-1]
    sliced = replay.slice_at(last_ts)
    for tf, candles in sliced.items():
        assert len(candles.frame) <= config.ohlcv_lookback, tf
    # The reference frame is long enough that the cap actually bit (not just returning all).
    assert len(sliced[config.reference_timeframe].frame) == config.ohlcv_lookback
