"""Tests for the daily cache refresher (no network; fake provider/fetch + fixed clock).

The refresher is exercised through two seams. Delta-only top-up, completed-candle-only
persistence, the cold-start horizon bound and the summary counts use the *real*
:class:`~trader.historical_data.CcxtHistoricalDataProvider` driven by an injected,
call-recording fetch and a fixed clock over a temp cache directory (prior art:
``tests/test_historical_data.py``). Failure isolation uses a pure in-memory fake provider
that raises for one symbol (prior art: ``tests/test_backtester_provider.py``).
"""

from __future__ import annotations

import threading

import pandas as pd

from trader.cache_refresher import CacheRefresher
from trader.concurrent_loader import ConcurrentHistoryLoader
from trader.config import BacktestConfig, Config
from trader.historical_data import CcxtHistoricalDataProvider
from trader.market_data import OHLCV_COLUMNS, Candles

_STEP = 3_600_000  # 1h in ms
_DAY = 86_400_000  # 24h in ms (== 24 * _STEP)


def _row(ts: int) -> list[float]:
    return [float(ts), 100.0, 101.0, 99.0, 100.5, 1_000.0]


class _RecordingFetch:
    """A thread-safe fake ccxt fetch over a fixed 1h grid that records every call.

    Serves up to ``limit`` consecutive bars from ``since`` on the grid ``[grid_start,
    grid_end)``; a ``since`` at/after ``grid_end`` yields an empty page.
    """

    def __init__(self, grid_start: int, grid_end: int) -> None:
        self._grid_start = grid_start
        self._grid_end = grid_end
        self.calls: list[tuple[str, str, int, int]] = []
        self._lock = threading.Lock()

    def __call__(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]:
        with self._lock:
            self.calls.append((symbol, timeframe, since, limit))
        rows: list[list[float]] = []
        ts = max(since, self._grid_start)
        while ts < self._grid_end and len(rows) < limit:
            rows.append(_row(ts))
            ts += _STEP
        return rows

    def sinces(self) -> list[int]:
        with self._lock:
            return [c[2] for c in self.calls]


def _config(cache_dir: str, *, history_horizon_days: int = 1, max_workers: int = 4) -> Config:
    return Config(
        watchlist=["AAA"],
        timeframes=["1h"],
        reference_timeframe="1h",
        lead_timeframe="1h",
        htf_timeframes=["1h"],
        backtest=BacktestConfig(
            cache_dir=cache_dir,
            history_horizon_days=history_horizon_days,
            max_workers=max_workers,
        ),
    )


def _provider(cache_dir: str, fetch: _RecordingFetch, now: int) -> CcxtHistoricalDataProvider:
    return CcxtHistoricalDataProvider(
        cache_dir=cache_dir,
        fetch=fetch,
        warmup_bars=0,
        page_limit=1000,
        clock=lambda: now,
    )


def test_fully_warm_cache_makes_zero_fetch_calls(tmp_path) -> None:  # type: ignore[no-untyped-def]
    now = 1_000 * _STEP  # on a candle boundary
    config = _config(str(tmp_path))
    fetch = _RecordingFetch(grid_start=now - _DAY, grid_end=now + 5 * _STEP)
    provider = _provider(str(tmp_path), fetch, now)
    loader = ConcurrentHistoryLoader()

    # Cold fill: 24 completed hourly candles per symbol (AAA + BTC), forming bar dropped.
    first = CacheRefresher().refresh(config, provider, loader, now=now)
    assert first.coins_refreshed == 2  # AAA + BTC context
    assert first.candles_added == 48  # 24 candles x 2 symbols
    assert first.skipped == ()
    assert len(fetch.calls) > 0

    # A second refresh at the same instant is fully warm -> not a single fetch call.
    fetch.calls.clear()
    second = CacheRefresher().refresh(config, provider, loader, now=now)
    assert fetch.calls == []
    assert second.candles_added == 0
    assert second.coins_refreshed == 2


def test_warm_with_gap_fetches_only_from_cache_end_plus_one_interval(tmp_path) -> None:  # type: ignore[no-untyped-def]
    now1 = 1_000 * _STEP
    now2 = now1 + 10 * _STEP
    config = _config(str(tmp_path))
    fetch = _RecordingFetch(grid_start=now1 - _DAY, grid_end=now2 + 5 * _STEP)
    loader = ConcurrentHistoryLoader()

    # Warm the cache up to now1 (last completed candle opens at now1 - _STEP).
    CacheRefresher().refresh(config, _provider(str(tmp_path), fetch, now1), loader, now=now1)

    fetch.calls.clear()
    summary = CacheRefresher().refresh(
        config, _provider(str(tmp_path), fetch, now2), loader, now=now2
    )

    # Only the gap is fetched: every fetch resumes at cache_end + one interval == now1.
    assert fetch.calls, "the gap since the last refresh must be fetched"
    assert set(fetch.sinces()) == {now1}
    # 10 new completed candles per symbol (now1 .. now2 - _STEP) x 2 symbols.
    assert summary.candles_added == 20


def test_after_refresh_cache_ends_at_latest_completed_and_warm_read_does_no_fetch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    now = 1_000 * _STEP
    config = _config(str(tmp_path))
    fetch = _RecordingFetch(grid_start=now - _DAY, grid_end=now + 5 * _STEP)
    provider = _provider(str(tmp_path), fetch, now)
    loader = ConcurrentHistoryLoader()

    CacheRefresher().refresh(config, provider, loader, now=now)

    persisted = pd.read_csv(provider._cache_path("AAA", "1h"))["timestamp"].tolist()
    assert max(persisted) == now - _STEP  # ends at the latest completed candle
    assert now not in persisted  # the still-forming candle is never persisted

    # A subsequent backtest-style read over the refreshed range does no additional fetch.
    fetch.calls.clear()
    candles = provider.get_history("AAA", "1h", now - _DAY, now)
    assert fetch.calls == []  # served entirely from the warm cache
    assert not candles.frame.empty


class _FrameProvider:
    """In-memory provider: non-empty frames for served symbols, raises for the failing one."""

    def __init__(self, failing: str) -> None:
        self._failing = failing

    def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
        if symbol == self._failing:
            raise ConnectionError(f"network error fetching {symbol}")
        frame = pd.DataFrame([_row(start)], columns=OHLCV_COLUMNS)
        return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def test_persistently_failing_coin_is_skipped_and_refresh_completes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = Config(
        watchlist=["AAA", "BBB"],
        timeframes=["1h"],
        reference_timeframe="1h",
        lead_timeframe="1h",
        htf_timeframes=["1h"],
        backtest=BacktestConfig(cache_dir=str(tmp_path), history_horizon_days=1),
    )
    provider = _FrameProvider(failing="BBB")

    summary = CacheRefresher().refresh(
        config, provider, ConcurrentHistoryLoader(), now=1_000 * _STEP
    )

    assert [c.symbol for c in summary.skipped] == ["BBB"]
    assert "fetch failed" in summary.skipped[0].reason
    # The rest (AAA + BTC context) still refreshed despite the one bad symbol.
    assert summary.coins_refreshed == 2


def test_summary_counts_match_actual_cache_contents(tmp_path) -> None:  # type: ignore[no-untyped-def]
    now = 1_000 * _STEP
    config = _config(str(tmp_path))
    fetch = _RecordingFetch(grid_start=now - _DAY, grid_end=now + 5 * _STEP)
    provider = _provider(str(tmp_path), fetch, now)

    summary = CacheRefresher().refresh(config, provider, ConcurrentHistoryLoader(), now=now)

    total_rows = sum(
        len(pd.read_csv(p)) for p in sorted(tmp_path.glob("*.csv"))
    )
    assert summary.candles_added == total_rows
    assert summary.coins_refreshed == len(list(tmp_path.glob("*.csv")))


def test_cold_start_fill_bounded_by_history_horizon(tmp_path) -> None:  # type: ignore[no-untyped-def]
    now = 1_000 * _STEP
    config = _config(str(tmp_path), history_horizon_days=1)  # 24h horizon
    # The grid stretches far further back than the horizon; the fill must ignore that.
    fetch = _RecordingFetch(grid_start=now - 100 * _STEP, grid_end=now + 5 * _STEP)
    provider = _provider(str(tmp_path), fetch, now)

    CacheRefresher().refresh(config, provider, ConcurrentHistoryLoader(), now=now)

    horizon_start = now - _DAY
    # No fetch reached back before the horizon, and nothing older was persisted.
    assert min(fetch.sinces()) == horizon_start
    persisted = pd.read_csv(provider._cache_path("AAA", "1h"))["timestamp"].tolist()
    assert min(persisted) == horizon_start
