"""Tests for the concurrent per-(symbol, timeframe) history loader (no network).

Covers the behaviour that matters: real concurrency bounded by ``max_workers`` (a barrier
plus a peak-counter proves the bound is both saturated and never exceeded), deterministic
order-independent assembly, per-symbol failure isolation, and equivalence with a plain
sequential ``get_history`` loop.
"""

from __future__ import annotations

import threading
import time

import pandas as pd

from trader.concurrent_loader import ConcurrentHistoryLoader, SkippedCoin
from trader.market_data import Candles

_STEP = 3_600_000  # 1h in ms


def _frame(symbol: str, timeframe: str, rows: int = 5) -> Candles:
    # Deterministic per-(symbol, timeframe) content so mixing would be detectable.
    base = float(ord(symbol[0]) + len(timeframe))
    data = [
        [float(i * _STEP), base, base + 1.0, base - 1.0, base + 0.5, 1_000.0 + i]
        for i in range(rows)
    ]
    frame = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def _empty(symbol: str, timeframe: str) -> Candles:
    frame = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def _digest(histories: dict[str, dict[str, Candles]]) -> dict[str, dict[str, list[float]]]:
    """A comparable, order-independent view of assembled histories (closes per frame)."""
    return {
        symbol: {tf: list(c.frame["close"]) for tf, c in frames.items()}
        for symbol, frames in histories.items()
    }


class _CountingProvider:
    """Serves canned frames and records how often each symbol was fetched."""

    def __init__(self, known: set[str]) -> None:
        self._known = known
        self.calls: dict[str, int] = {}
        self._lock = threading.Lock()

    def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
        with self._lock:
            self.calls[symbol] = self.calls.get(symbol, 0) + 1
        if symbol not in self._known:
            return _empty(symbol, timeframe)
        return _frame(symbol, timeframe)


def test_peak_concurrency_equals_max_workers() -> None:
    max_workers = 3
    symbols = ["AAA", "BBB", "CCC"]
    timeframes = ["1h", "4h"]  # 3 x 2 = 6 tasks == 2 barrier cycles of 3
    barrier = threading.Barrier(max_workers, timeout=5)
    lock = threading.Lock()
    state = {"current": 0, "peak": 0}

    class _BarrierProvider:
        def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
            with lock:
                state["current"] += 1
                state["peak"] = max(state["peak"], state["current"])
            # Block until exactly ``max_workers`` tasks are in flight together: this both
            # proves real concurrency and forces the observed peak up to the bound.
            barrier.wait()
            with lock:
                state["current"] -= 1
            return _frame(symbol, timeframe)

    histories, skipped = ConcurrentHistoryLoader().load(
        _BarrierProvider(), symbols, timeframes, 0, 10 * _STEP, max_workers=max_workers
    )

    assert state["peak"] == max_workers  # saturated AND never exceeded
    assert skipped == ()
    assert set(histories) == set(symbols)
    assert all(set(histories[s]) == set(timeframes) for s in symbols)


def test_assembly_is_deterministic_regardless_of_completion_order() -> None:
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    timeframes = ["1h", "4h"]

    class _JitterProvider:
        """Sleeps a symbol-dependent amount so tasks finish out of submission order."""

        def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
            # Later-listed symbols return first, scrambling completion vs submission order.
            time.sleep((len(symbols) - symbols.index(symbol)) * 0.005)
            return _frame(symbol, timeframe)

    loader = ConcurrentHistoryLoader()
    first, _ = loader.load(_JitterProvider(), symbols, timeframes, 0, 10 * _STEP, max_workers=4)
    second, _ = loader.load(_JitterProvider(), symbols, timeframes, 0, 10 * _STEP, max_workers=1)

    # Same assembled content regardless of concurrency / completion order.
    assert _digest(first) == _digest(second)
    assert list(first) == symbols  # symbol order preserved
    assert all(list(first[s]) == timeframes for s in symbols)  # timeframe order preserved


def test_loader_matches_sequential_get_history_loop() -> None:
    symbols = ["AAA", "BBB", "CCC"]
    timeframes = ["1h", "4h"]
    provider = _CountingProvider(known=set(symbols))

    concurrent, skipped = ConcurrentHistoryLoader().load(
        provider, symbols, timeframes, 0, 10 * _STEP, max_workers=4
    )

    # A plain sequential reference over the same provider/inputs.
    sequential: dict[str, dict[str, Candles]] = {}
    ref = _CountingProvider(known=set(symbols))
    for symbol in symbols:
        sequential[symbol] = {
            tf: ref.get_history(symbol, tf, 0, 10 * _STEP) for tf in timeframes
        }

    assert skipped == ()
    assert _digest(concurrent) == _digest(sequential)


def test_failing_symbol_is_skipped_others_assemble() -> None:
    symbols = ["AAA", "BBB", "CCC"]
    timeframes = ["1h", "4h"]

    class _PartlyFailingProvider:
        def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
            if symbol == "BBB":
                raise ConnectionError("persistent outage after retries")
            return _frame(symbol, timeframe)

    histories, skipped = ConcurrentHistoryLoader().load(
        _PartlyFailingProvider(), symbols, timeframes, 0, 10 * _STEP, max_workers=3
    )

    assert set(histories) == {"AAA", "CCC"}  # the healthy coins still assembled
    assert [s.symbol for s in skipped] == ["BBB"]
    assert isinstance(skipped[0], SkippedCoin)
    assert "fetch failed" in skipped[0].reason


def test_symbol_missing_a_timeframe_is_skipped_as_no_data() -> None:
    symbols = ["AAA", "NODATA"]
    timeframes = ["1h", "4h"]
    provider = _CountingProvider(known={"AAA"})  # NODATA yields empty frames

    histories, skipped = ConcurrentHistoryLoader().load(
        provider, symbols, timeframes, 0, 10 * _STEP, max_workers=2
    )

    assert set(histories) == {"AAA"}
    assert [s.symbol for s in skipped] == ["NODATA"]
    assert "no historical data" in skipped[0].reason


def test_duplicate_symbols_are_loaded_once() -> None:
    provider = _CountingProvider(known={"AAA"})
    histories, _ = ConcurrentHistoryLoader().load(
        provider, ["AAA", "AAA"], ["1h"], 0, 10 * _STEP, max_workers=2
    )
    assert list(histories) == ["AAA"]
    assert provider.calls["AAA"] == 1  # deduped: fetched exactly once
