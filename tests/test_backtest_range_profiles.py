"""Date-range gating and profile-awareness of the backtester (no network).

Proves that only trades whose entry falls within the requested range are counted while
earlier candles still warm up the indicators, and that the backtest reuses the live
configuration under both the futures and spot profiles over injected history.
"""

from __future__ import annotations

import pandas as pd

from trader.backtester import Backtester
from trader.config import Config, StrategyConfig, resolve_profile
from trader.direction import Direction
from trader.market_data import Candles
from trader.replay import Replay, timeframe_to_ms


def _bullish_frame(symbol: str, timeframe: str, step_ms: int, rows: int) -> Candles:
    data = []
    base = 100.0
    for i in range(rows):
        base += 1.0
        offset = {0: 0.0, 1: 3.0, 2: 5.0, 3: 3.0, 4: 0.0, 5: -2.0}[i % 6]
        close = base + offset
        data.append([i * step_ms, close - 0.5, close + 1.0, close - 1.0, close, 1_000.0 + i])
    frame = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def _aligned(symbol: str, timeframes: list[str], reference_tf: str, reference_bars: int):  # type: ignore[no-untyped-def]
    span = timeframe_to_ms(reference_tf) * reference_bars
    out: dict[str, Candles] = {}
    for tf in timeframes:
        step = timeframe_to_ms(tf)
        rows = max(1, round(span / step))
        out[tf] = _bullish_frame(symbol, tf, step, rows)
    return out


def _futures_config() -> Config:
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


# --------------------------------------------------------------------------------------
# Date-range gating — only in-range entries counted; earlier bars only warm up.
# --------------------------------------------------------------------------------------


def test_only_trades_entered_within_the_range_are_counted() -> None:
    config = _futures_config()
    histories = {"AAA": _aligned("AAA", ["1h", "4h"], "4h", 260)}
    btc = _aligned("BTC/USDT", ["1h", "4h"], "4h", 260)

    # Ungated: the persistent bullish signal enters once, at its natural first close.
    ungated = Backtester().run(config, histories, btc)
    assert len(ungated.outcomes) == 1
    natural_entry = ungated.outcomes[0].entry_time

    # Gate the entry window to strictly after that natural entry. The pre-start qualifying
    # bars must NOT open a trade (they only warm up the indicators); the trade that IS
    # counted enters at or after the range start.
    ref_closes = Replay(histories["AAA"], "4h").reference_closes()
    entry_start = natural_entry + timeframe_to_ms("4h")
    entry_end = ref_closes[-1]
    assert natural_entry < entry_start  # the natural entry lies in the warm-up region

    gated = Backtester().run(config, histories, btc, entry_start=entry_start, entry_end=entry_end)
    assert len(gated.outcomes) == 1  # still trades -> indicators were warm from earlier bars
    assert all(entry_start <= o.entry_time <= entry_end for o in gated.outcomes)
    assert gated.outcomes[0].entry_time >= entry_start


# --------------------------------------------------------------------------------------
# Profiles — the backtest reuses the live config and runs on futures and spot.
# --------------------------------------------------------------------------------------


def test_futures_profile_produces_trades() -> None:
    config = _futures_config()
    histories = {"AAA": _aligned("AAA", ["1h", "4h"], "4h", 260)}
    btc = _aligned("BTC/USDT", ["1h", "4h"], "4h", 260)

    run = Backtester().run(config, histories, btc)
    assert len(run.outcomes) >= 1
    assert run.outcomes[0].direction is Direction.LONG


def test_spot_profile_produces_trades() -> None:
    resolved = resolve_profile("spot")
    config = Config(
        watchlist=["AAA"],
        profile="spot",
        timeframes=resolved.timeframes,
        reference_timeframe=resolved.reference_timeframe,
        lead_timeframe=resolved.lead_timeframe,
        htf_timeframes=resolved.htf_timeframes,
        # Replay machinery under test, not the method: a synthetic series cannot satisfy a
        # seven-condition checklist, and leaving it on would pin zero trades.
        strategies=StrategyConfig(enabled=False),
    )
    # 900 weekly reference bars. The coarsest timeframe here is monthly, and the method's
    # long-term filter is a 200-period simple average that is reported absent rather than
    # back-filled — so the fixture needs ~200 months (900 weeks ≈ 210) before the lead
    # timeframe can resolve a direction at all.
    histories = {"AAA": _aligned("AAA", resolved.timeframes, resolved.reference_timeframe, 900)}
    btc = _aligned("BTC/USDT", resolved.timeframes, resolved.reference_timeframe, 900)

    run = Backtester().run(config, histories, btc)
    assert len(run.outcomes) >= 1
    assert run.outcomes[0].direction is Direction.LONG
