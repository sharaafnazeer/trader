"""The backtester sourcing per-coin history through the provider seam (no network).

Exercises :meth:`Backtester.run_from_provider` with a fake in-memory provider: a coin
with history is backtested normally, and a coin for which the provider yields no candles
is recorded in :attr:`BacktestRun.skipped` while the run completes for the rest without
aborting.
"""

from __future__ import annotations

import pandas as pd

from trader.backtester import BTC_CONTEXT_SYMBOL, Backtester
from trader.config import Config, StrategyConfig
from trader.market_data import Candles

_HOUR = 3_600_000


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


def _aligned(symbol: str, reference_bars: int = 260) -> dict[str, Candles]:
    span = 4 * _HOUR * reference_bars
    return {
        "1h": _bullish_frame(symbol, "1h", _HOUR, span // _HOUR),
        "4h": _bullish_frame(symbol, "4h", 4 * _HOUR, reference_bars),
    }


class _FakeProvider:
    """Serves canned per-(symbol, timeframe) frames; empty candles for unknown symbols."""

    def __init__(self, frames: dict[str, dict[str, Candles]]) -> None:
        self._frames = frames

    def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
        by_tf = self._frames.get(symbol)
        if by_tf is None or timeframe not in by_tf:
            empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
            return Candles(symbol=symbol, timeframe=timeframe, frame=empty)
        return by_tf[timeframe]


def _config() -> Config:
    return Config(
        watchlist=["AAA", "NODATA"],
        timeframes=["1h", "4h"],
        reference_timeframe="4h",
        lead_timeframe="4h",
        htf_timeframes=["4h"],
        # Replay machinery under test, not the method: a synthetic series cannot satisfy a
        # seven-condition checklist, and leaving it on would pin zero trades.
        strategies=StrategyConfig(enabled=False),
    )


def test_coin_without_history_is_skipped_and_run_completes_for_the_rest() -> None:
    config = _config()
    # AAA has history; NODATA has none; BTC context has history.
    provider = _FakeProvider(
        {"AAA": _aligned("AAA"), BTC_CONTEXT_SYMBOL: _aligned(BTC_CONTEXT_SYMBOL)}
    )

    run = Backtester().run_from_provider(config, provider, start=0, end=4 * _HOUR * 260)

    # NODATA is recorded as skipped, not fatal; AAA still produced its trade(s).
    assert [c.symbol for c in run.skipped] == ["NODATA"]
    assert run.skipped[0].reason
    assert len(run.outcomes) >= 1
    assert all(o.symbol == "AAA" for o in run.outcomes)


def test_provider_sourced_run_matches_direct_injection() -> None:
    # Sourcing AAA through the provider yields the same outcomes as injecting the frames.
    single = Config(
        watchlist=["AAA"],
        timeframes=["1h", "4h"],
        reference_timeframe="4h",
        lead_timeframe="4h",
        htf_timeframes=["4h"],
        # Replay machinery under test, not the method: a synthetic series cannot satisfy a
        # seven-condition checklist, and leaving it on would pin zero trades.
        strategies=StrategyConfig(enabled=False),
    )
    frames = {"AAA": _aligned("AAA"), BTC_CONTEXT_SYMBOL: _aligned(BTC_CONTEXT_SYMBOL)}
    provider = _FakeProvider(frames)

    via_provider = Backtester().run_from_provider(single, provider, start=0, end=4 * _HOUR * 260)
    via_injection = Backtester().run(
        single, {"AAA": frames["AAA"]}, frames[BTC_CONTEXT_SYMBOL]
    )

    assert via_provider.outcomes == via_injection.outcomes
    assert via_provider.skipped == ()


class _RaisingProvider:
    """Serves frames, but raises a network-style error for one designated symbol."""

    def __init__(self, frames: dict[str, dict[str, Candles]], failing: str) -> None:
        self._frames = frames
        self._failing = failing

    def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
        if symbol == self._failing:
            raise ConnectionError(f"network error fetching {symbol}")
        by_tf = self._frames.get(symbol)
        if by_tf is None or timeframe not in by_tf:
            empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
            return Candles(symbol=symbol, timeframe=timeframe, frame=empty)
        return by_tf[timeframe]


def test_coin_whose_fetch_raises_is_skipped_and_run_completes() -> None:
    # BBB's fetch raises (a transient error that survived provider retries); the run must
    # skip BBB and still backtest AAA rather than aborting.
    config = Config(
        watchlist=["AAA", "BBB"],
        timeframes=["1h", "4h"],
        reference_timeframe="4h",
        lead_timeframe="4h",
        htf_timeframes=["4h"],
        # Replay machinery under test, not the method: a synthetic series cannot satisfy a
        # seven-condition checklist, and leaving it on would pin zero trades.
        strategies=StrategyConfig(enabled=False),
    )
    provider = _RaisingProvider(
        {"AAA": _aligned("AAA"), BTC_CONTEXT_SYMBOL: _aligned(BTC_CONTEXT_SYMBOL)},
        failing="BBB",
    )

    run = Backtester().run_from_provider(config, provider, start=0, end=4 * _HOUR * 260)

    assert [c.symbol for c in run.skipped] == ["BBB"]
    assert "fetch failed" in run.skipped[0].reason
    assert len(run.outcomes) >= 1
    assert all(o.symbol == "AAA" for o in run.outcomes)


def test_btc_context_fetch_failure_degrades_to_neutral() -> None:
    # If BTC's own history fetch raises, the run still completes (neutral market context).
    config = Config(
        watchlist=["AAA"],
        timeframes=["1h", "4h"],
        reference_timeframe="4h",
        lead_timeframe="4h",
        htf_timeframes=["4h"],
        # Replay machinery under test, not the method: a synthetic series cannot satisfy a
        # seven-condition checklist, and leaving it on would pin zero trades.
        strategies=StrategyConfig(enabled=False),
    )
    provider = _RaisingProvider({"AAA": _aligned("AAA")}, failing=BTC_CONTEXT_SYMBOL)

    run = Backtester().run_from_provider(config, provider, start=0, end=4 * _HOUR * 260)

    # No crash; AAA is still evaluated despite BTC context being unavailable.
    assert all(o.symbol == "AAA" for o in run.outcomes)


def test_concurrent_path_equals_sequential_backtest_and_metrics() -> None:
    """Equivalence: the concurrent loader assembles the same history as a plain sequential
    get_history loop, and the resulting backtest report metrics are identical — so parallel
    fetching changes speed only, not the numbers.
    """
    from trader.concurrent_loader import ConcurrentHistoryLoader
    from trader.metrics import summarize

    config = Config(
        watchlist=["AAA", "CCC"],
        timeframes=["1h", "4h"],
        reference_timeframe="4h",
        lead_timeframe="4h",
        htf_timeframes=["4h"],
        # Replay machinery under test, not the method: a synthetic series cannot satisfy a
        # seven-condition checklist, and leaving it on would pin zero trades.
        strategies=StrategyConfig(enabled=False),
    )
    frames = {
        "AAA": _aligned("AAA"),
        "CCC": _aligned("CCC"),
        BTC_CONTEXT_SYMBOL: _aligned(BTC_CONTEXT_SYMBOL),
    }
    provider = _FakeProvider(frames)
    symbols = [*config.watchlist, BTC_CONTEXT_SYMBOL]

    # 1) Loader's assembled histories == a direct sequential get_history loop.
    concurrent_hist, skipped = ConcurrentHistoryLoader().load(
        provider, symbols, config.timeframes, 0, 4 * _HOUR * 80, max_workers=4
    )
    assert skipped == ()
    for sym in symbols:
        for tf in config.timeframes:
            seq = provider.get_history(sym, tf, 0, 4 * _HOUR * 80)
            assert concurrent_hist[sym][tf].frame.equals(seq.frame)

    # 2) Backtest via the concurrent provider path == a direct sequential injection, and
    #    the summarized report metrics match exactly.
    via_provider = Backtester().run_from_provider(config, provider, start=0, end=4 * _HOUR * 260)
    seq_hist = {s: {tf: provider.get_history(s, tf, 0, 4 * _HOUR * 80) for tf in config.timeframes}
                for s in config.watchlist}
    via_sequential = Backtester().run(config, seq_hist, frames[BTC_CONTEXT_SYMBOL])

    assert via_provider.outcomes == via_sequential.outcomes
    rc = summarize(via_provider.outcomes)
    rs = summarize(via_sequential.outcomes)
    assert (rc.win_rate, rc.expectancy, rc.profit_factor, rc.max_drawdown, rc.resolved_trades) == (
        rs.win_rate, rs.expectancy, rs.profit_factor, rs.max_drawdown, rs.resolved_trades
    )
