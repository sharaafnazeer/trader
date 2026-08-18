"""Tests for the paginating, disk-cached historical-data provider (no network).

The concrete provider's network fetch is never exercised; instead a fake/injected fetch
function stands in, so these tests cover the behaviour that matters: pagination assembles
more than one page over a range plus warm-up, the on-disk cache is written then reused on
a second run without any further fetch (verified with a call-counting fake), and a canned
ccxt-shaped response maps into the existing :class:`Candles` type with the expected rows
and columns.
"""

from __future__ import annotations

import sys
import threading
import types

import pandas as pd

from trader.historical_data import (
    OHLCV_COLUMNS,
    CcxtHistoricalDataProvider,
    HistoricalDataProvider,
)
from trader.market_data import Candles

_STEP = 3_600_000  # 1h in ms


def _row(ts: int) -> list[float]:
    # A deterministic ccxt-shaped row [timestamp, open, high, low, close, volume].
    base = 100.0 + ts / _STEP
    return [float(ts), base, base + 1.0, base - 1.0, base + 0.5, 1_000.0]


class _PagingFetch:
    """A fake ccxt fetch: returns up to ``limit`` consecutive 1h bars from ``since``.

    Records every call so a test can assert how many pages were requested. Bars exist on
    the grid ``[grid_start, grid_end)``; a ``since`` beyond the grid yields an empty page.
    """

    def __init__(self, grid_start: int, grid_end: int) -> None:
        self._grid_start = grid_start
        self._grid_end = grid_end
        self.calls: list[tuple[str, str, int, int]] = []

    def __call__(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]:
        self.calls.append((symbol, timeframe, since, limit))
        start = max(since, self._grid_start)
        rows: list[list[float]] = []
        ts = start
        while ts < self._grid_end and len(rows) < limit:
            rows.append(_row(ts))
            ts += _STEP
        return rows


def test_provider_satisfies_protocol() -> None:
    provider: HistoricalDataProvider = CcxtHistoricalDataProvider(cache_dir="unused")
    assert hasattr(provider, "get_history")


def test_pagination_assembles_more_than_one_page(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Range of 50 bars plus 10 bars of warm-up, fetched in pages of 20 -> at least 3 pages.
    start = 100 * _STEP
    end = start + 50 * _STEP
    grid_start = start - 100 * _STEP  # plenty of history behind the warm-up window
    fetch = _PagingFetch(grid_start=grid_start, grid_end=end + _STEP)
    provider = CcxtHistoricalDataProvider(
        cache_dir=str(tmp_path), fetch=fetch, warmup_bars=10, page_limit=20
    )

    candles = provider.get_history("AAA", "1h", start, end)

    assert isinstance(candles, Candles)
    # 10 warm-up bars + 50 range bars + the end bar, all one span -> far more than a page.
    assert len(candles.frame) > 20
    assert len(fetch.calls) > 1  # more than a single fetch call: it paginated


def test_cache_write_then_reuse_does_not_fetch_again(tmp_path) -> None:  # type: ignore[no-untyped-def]
    start = 100 * _STEP
    end = start + 30 * _STEP
    grid_start = start - 100 * _STEP
    fetch = _PagingFetch(grid_start=grid_start, grid_end=end + _STEP)
    provider = CcxtHistoricalDataProvider(
        cache_dir=str(tmp_path), fetch=fetch, warmup_bars=5, page_limit=20
    )

    first = provider.get_history("AAA", "1h", start, end)
    calls_after_first = len(fetch.calls)
    assert calls_after_first > 0

    # A fresh provider instance over the SAME cache dir must reuse the on-disk cache and
    # not call fetch again for the identical range.
    fetch2 = _PagingFetch(grid_start=grid_start, grid_end=end + _STEP)
    provider2 = CcxtHistoricalDataProvider(
        cache_dir=str(tmp_path), fetch=fetch2, warmup_bars=5, page_limit=20
    )
    second = provider2.get_history("AAA", "1h", start, end)

    assert len(fetch2.calls) == 0  # fully served from cache, no re-download
    assert len(second.frame) == len(first.frame)
    assert list(second.frame["timestamp"]) == list(first.frame["timestamp"])


def test_canned_ccxt_response_maps_to_candles_rows_and_columns(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A canned, ccxt-shaped response returned as a single page.
    canned = [
        [1_700_000_000_000, 100.0, 110.0, 95.0, 105.0, 1_000.0],
        [1_700_003_600_000, 105.0, 115.0, 104.0, 112.0, 1_200.0],
        [1_700_007_200_000, 112.0, 120.0, 111.0, 118.0, 900.0],
    ]
    calls: list[int] = []

    def fetch(symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        calls.append(since)
        # Return the canned page once, then nothing (short page ends pagination).
        return canned if not calls[:-1] else []

    provider = CcxtHistoricalDataProvider(
        cache_dir=str(tmp_path), fetch=fetch, warmup_bars=0, page_limit=1000
    )
    candles = provider.get_history("BTC/USDT", "1h", 1_700_000_000_000, 1_700_007_200_000)

    assert isinstance(candles, Candles)
    assert candles.symbol == "BTC/USDT"
    assert candles.timeframe == "1h"
    assert list(candles.frame.columns) == OHLCV_COLUMNS
    assert len(candles.frame) == 3
    assert candles.latest_close == 118.0


def test_no_history_returns_empty_candles(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def fetch(symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        return []  # the exchange has nothing for this symbol

    provider = CcxtHistoricalDataProvider(cache_dir=str(tmp_path), fetch=fetch)
    candles = provider.get_history("NOPE", "1h", 0, 10 * _STEP)

    assert isinstance(candles, Candles)
    assert candles.frame.empty


def test_transient_fetch_error_is_retried_then_succeeds(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A blip on the first attempts is absorbed by bounded retry; the page still loads."""
    grid = _PagingFetch(0, 5 * _STEP)
    attempts = {"n": 0}

    def flaky(symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        attempts["n"] += 1
        if attempts["n"] <= 2:  # fail the first two attempts, then succeed
            raise ConnectionError("transient network blip")
        return grid(symbol, timeframe, since, limit)

    sleeps: list[float] = []
    provider = CcxtHistoricalDataProvider(
        cache_dir=str(tmp_path),
        fetch=flaky,
        warmup_bars=0,
        page_limit=1000,
        max_retries=3,
        sleep=sleeps.append,  # record backoff instead of sleeping
    )
    candles = provider.get_history("AAA", "1h", 0, 5 * _STEP)
    assert not candles.frame.empty
    assert attempts["n"] == 3  # two failures + one success
    assert len(sleeps) == 2  # backoff slept once per retry, no wall clock


def test_persistent_fetch_error_reraises_after_retries(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A fetch that always fails re-raises after the retry budget, so the caller can skip."""
    def always_fail(symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        raise ConnectionError("persistent outage")

    provider = CcxtHistoricalDataProvider(
        cache_dir=str(tmp_path),
        fetch=always_fail,
        warmup_bars=0,
        max_retries=2,
        sleep=lambda _: None,
    )
    try:
        provider.get_history("AAA", "1h", 0, 5 * _STEP)
    except ConnectionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ConnectionError to propagate after retries")


def test_default_fetch_uses_distinct_thread_local_client_per_thread(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Each worker thread lazily builds and reuses its OWN ccxt client (no shared state)."""

    class _FakeExchange:
        def __init__(self, opts: dict[str, object]) -> None:
            self.opts = opts

        def fetch_ohlcv(
            self, symbol: str, timeframe: str, since: int, limit: int
        ) -> list[list[float]]:
            return [_row(since)]

    fake_ccxt = types.ModuleType("ccxt")
    fake_ccxt.binance = lambda opts: _FakeExchange(opts)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ccxt", fake_ccxt)

    # No injected fetch -> the default lazy-ccxt, thread-local client path is exercised.
    provider = CcxtHistoricalDataProvider(cache_dir=str(tmp_path))

    barrier = threading.Barrier(3)
    lock = threading.Lock()
    # Keep the client objects alive so identity (and id()) stays stable after the threads
    # exit — otherwise a garbage-collected client's id could be reused by another.
    clients: dict[int, object] = {}
    reused: dict[int, bool] = {}

    def worker() -> None:
        barrier.wait()  # maximise the chance of concurrent client construction
        provider._default_fetch("AAA", "1h", 0, 10)
        first = provider._thread_local.client
        # A second call on the same thread must reuse the very same client instance.
        provider._default_fetch("AAA", "1h", 10, 10)
        second = provider._thread_local.client
        with lock:
            clients[threading.get_ident()] = first
            reused[threading.get_ident()] = first is second

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(clients) == 3
    assert len({id(c) for c in clients.values()}) == 3  # a distinct client per thread
    assert all(reused.values())  # each thread reused its own client on the second call


def test_concurrent_get_history_returns_correct_candles_per_thread(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """One provider instance, called concurrently, returns the right candles per symbol."""
    start = 100 * _STEP
    end = start + 20 * _STEP
    grid_start = start - 50 * _STEP
    symbols = ["AAA", "BBB", "CCC", "DDD"]

    calling_threads: dict[str, set[int]] = {s: set() for s in symbols}
    lock = threading.Lock()

    def fetch(symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        with lock:
            calling_threads[symbol].add(threading.get_ident())
        start_ts = max(since, grid_start)
        rows: list[list[float]] = []
        ts = start_ts
        while ts < end + _STEP and len(rows) < limit:
            # Encode the symbol into the close so we can prove no cross-thread mixing.
            base = float(ord(symbol[0]))
            rows.append([float(ts), base, base + 1.0, base - 1.0, base, 1_000.0])
            ts += _STEP
        return rows

    provider = CcxtHistoricalDataProvider(
        cache_dir=str(tmp_path),
        fetch=fetch,
        warmup_bars=5,
        page_limit=1000,
        clock=lambda: end + _STEP,
    )

    results: dict[str, Candles] = {}
    barrier = threading.Barrier(len(symbols))

    def worker(symbol: str) -> None:
        barrier.wait()
        candles = provider.get_history(symbol, "1h", start, end)
        with lock:
            results[symbol] = candles

    threads = [threading.Thread(target=worker, args=(s,)) for s in symbols]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for symbol in symbols:
        candles = results[symbol]
        assert not candles.frame.empty
        expected = float(ord(symbol[0]))
        # Every returned candle carries this symbol's close -> no cross-thread mixing.
        assert set(candles.frame["close"]) == {expected}


def test_forming_candle_excluded_from_returned_and_persisted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """With an injected clock, a newest row still inside its period is dropped everywhere."""
    t0 = 100 * _STEP
    rows = [_row(t0 + i * _STEP) for i in range(5)]  # opens t0 .. t0+4*_STEP

    def fetch(symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        return [r for r in rows if r[0] >= since]

    # Newest row opens at t0+4*_STEP and closes at t0+5*_STEP. Clock sits mid-period, so
    # that candle is still forming (t0+4*_STEP + _STEP > now).
    now_forming = t0 + 4 * _STEP + _STEP // 2
    provider = CcxtHistoricalDataProvider(
        cache_dir=str(tmp_path), fetch=fetch, warmup_bars=0, clock=lambda: now_forming
    )

    candles = provider.get_history("AAA", "1h", t0, t0 + 4 * _STEP)
    returned = list(candles.frame["timestamp"])
    assert (t0 + 4 * _STEP) not in returned  # forming candle excluded from the result
    assert (t0 + 3 * _STEP) in returned  # the last closed candle is present

    persisted = list(pd.read_csv(provider._cache_path("AAA", "1h"))["timestamp"])
    assert (t0 + 4 * _STEP) not in persisted  # and never persisted to the cache
    assert (t0 + 3 * _STEP) in persisted


def test_fully_closed_newest_candle_is_included(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A newest row whose period has fully elapsed IS returned and persisted."""
    t0 = 100 * _STEP
    rows = [_row(t0 + i * _STEP) for i in range(5)]

    def fetch(symbol: str, timeframe: str, since: int, limit: int) -> list[list[float]]:
        return [r for r in rows if r[0] >= since]

    # Clock at exactly t0+5*_STEP: the newest candle (open t0+4*_STEP) is closed since
    # ts + d == now (<= holds).
    now_closed = t0 + 5 * _STEP
    provider = CcxtHistoricalDataProvider(
        cache_dir=str(tmp_path), fetch=fetch, warmup_bars=0, clock=lambda: now_closed
    )

    candles = provider.get_history("AAA", "1h", t0, t0 + 4 * _STEP)
    assert (t0 + 4 * _STEP) in list(candles.frame["timestamp"])

    persisted = list(pd.read_csv(provider._cache_path("AAA", "1h"))["timestamp"])
    assert (t0 + 4 * _STEP) in persisted
