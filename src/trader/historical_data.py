"""Deep historical OHLCV with pagination and an on-disk cache.

The backtester needs far more history than the live scanner's recent-candle fetch: a
full date range plus enough leading bars to warm up the indicators. ``ccxt``'s
``fetch_ohlcv`` returns at most a page (~1000 bars) per call, so this module *paginates*
— it walks a moving ``since`` forward until the requested range is covered — and caches
the assembled candles on disk keyed by ``(symbol, timeframe)`` so a repeated backtest
reuses the cache instead of re-downloading years of data.

``HistoricalDataProvider`` is the seam (a Protocol) through which the backtester reads
history; :class:`CcxtHistoricalDataProvider` is the concrete implementation. It mirrors
the :class:`~trader.market_data.CcxtBinanceProvider` pattern: ``ccxt`` is imported
**lazily** (only when the default network fetch runs) and the raw-row → :class:`Candles`
mapping reuses :func:`~trader.market_data.map_ohlcv`, so the analysis modules consume the
same :class:`Candles` type unchanged. The paginating fetch function is *injectable*, so
tests drive it with a fake and never touch the network.

This module belongs to the analysis core; it does not import ``typer`` or ``rich``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pandas as pd

from trader.market_data import OHLCV_COLUMNS, Candles, map_ohlcv
from trader.replay import timeframe_to_ms

logger = logging.getLogger(__name__)

# The signature of a ``ccxt``-style paginating fetch: ``(symbol, timeframe, since, limit)``
# returns a list of ``[timestamp, open, high, low, close, volume]`` rows whose open
# timestamps start at or after ``since`` (ascending). Injectable so tests need no network.
FetchOHLCV = Callable[[str, str, int, int], list[list[float]]]

# Injectable sleep for retry backoff, so tests exercise retries without wall-clock delay.
SleepFn = Callable[[float], None]

# Injectable "now" as epoch-ms, used to decide which candles are closed. Injectable so
# the completed-candle boundary is deterministic in tests.
ClockFn = Callable[[], int]


def _real_clock() -> int:
    """Current wall-clock time as epoch milliseconds (the default clock)."""
    return int(time.time() * 1000)

# Default retry policy for a page fetch: a deep history hits the network thousands of
# times, so transient blips/rate-limits are expected and absorbed before giving up.
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 1.0

# ccxt returns at most ~1000 bars per fetch_ohlcv call; page at that size by default.
DEFAULT_PAGE_LIMIT = 1000

# Default indicator warm-up: candles fetched *before* the requested start so the longest
# indicator windows (e.g. EMA200) are defined at the first evaluated moment. A backtest
# without this would evaluate an under-warmed engine at the start of its range.
DEFAULT_WARMUP_BARS = 300

# A hard ceiling on pages per (symbol, timeframe) fetch so a misbehaving fetch function
# (e.g. one that never advances) can never loop forever.
_MAX_PAGES = 10_000


class HistoricalDataProvider(Protocol):
    """Fetches deep historical candles for one ``(symbol, timeframe)`` over a range."""

    def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles: ...


def _sanitize(symbol: str) -> str:
    """A filesystem-safe token for ``symbol`` (``BTC/USDT`` -> ``BTC_USDT``)."""
    return symbol.replace("/", "_").replace(":", "_")


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS)


@dataclass(frozen=True, eq=False)
class CcxtHistoricalDataProvider:
    """A paginating, disk-cached :class:`HistoricalDataProvider` backed by ``ccxt``.

    ``cache_dir`` is where per-``(symbol, timeframe)`` CSV caches live. ``fetch`` is the
    injectable paginating fetch function; when ``None`` a lazily-constructed ``ccxt``
    exchange client is used (so importing this module never requires ``ccxt``).
    ``warmup_bars`` candles are fetched before ``start`` so the indicators are warm at
    the first evaluated moment; ``page_limit`` is the per-call page size. ``clock`` returns
    the current epoch-ms and decides which candles are closed (injectable for deterministic
    tests). ``eq=False`` because the mutable per-thread client cache makes value equality
    meaningless.
    """

    cache_dir: str | Path
    fetch: FetchOHLCV | None = None
    warmup_bars: int = DEFAULT_WARMUP_BARS
    page_limit: int = DEFAULT_PAGE_LIMIT
    exchange_id: str = "binance"
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    sleep: SleepFn = time.sleep
    clock: ClockFn = _real_clock
    # Thread-local holder for the lazily-constructed ccxt client. Each worker thread builds
    # and reuses its OWN client, so one provider instance is safe under concurrent
    # ``get_history`` calls (ccxt clients are not thread-safe to share). Excluded from the
    # frozen dataclass's init/repr/eq.
    _thread_local: threading.local = field(
        default_factory=threading.local, compare=False, repr=False
    )

    def _default_fetch(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]:
        # ccxt is imported and the client constructed lazily, per calling thread, so the
        # core imports without ccxt and no client is shared across threads.
        client = getattr(self._thread_local, "client", None)
        if client is None:
            import ccxt

            client = getattr(ccxt, self.exchange_id)({"enableRateLimit": True})
            self._thread_local.client = client
        raw = client.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        return list(raw)

    def _fetch_fn(self) -> FetchOHLCV:
        return self.fetch if self.fetch is not None else self._default_fetch

    def _fetch_with_retry(
        self, fetch: FetchOHLCV, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]:
        """Call ``fetch`` with bounded retry + linear backoff on transient errors.

        Retries up to ``max_retries`` times with increasing backoff before re-raising, so a
        transient network blip or rate-limit is absorbed while a persistent failure still
        surfaces (letting the backtester skip that coin rather than the whole run abort).
        """

        attempt = 0
        while True:
            try:
                return fetch(symbol, timeframe, since, limit)
            except Exception as exc:  # noqa: BLE001 - retried, then re-raised
                attempt += 1
                if attempt > self.max_retries:
                    raise
                backoff = self.retry_backoff * attempt
                logger.warning(
                    "fetch %s %s since=%d failed (attempt %d/%d): %s; backing off %.2fs",
                    symbol,
                    timeframe,
                    since,
                    attempt,
                    self.max_retries,
                    exc,
                    backoff,
                )
                self.sleep(backoff)

    def _drop_forming(self, frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """Return ``frame`` with any still-forming candle removed.

        A candle at open ``ts`` on a timeframe of duration ``d`` is closed iff
        ``ts + d <= now`` (its whole period has elapsed). The still-forming current-period
        candle is therefore never persisted or returned as a final candle.
        """
        if frame.empty:
            return frame
        duration = timeframe_to_ms(timeframe)
        now = self.clock()
        closed = frame[frame["timestamp"] + duration <= now]
        return closed.reset_index(drop=True)

    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        return Path(self.cache_dir) / f"{_sanitize(symbol)}__{timeframe}.csv"

    def _read_cache(self, symbol: str, timeframe: str) -> pd.DataFrame:
        path = self._cache_path(symbol, timeframe)
        if not path.exists():
            return _empty_frame()
        frame = pd.read_csv(path)
        return frame

    def _write_cache(self, symbol: str, timeframe: str, frame: pd.DataFrame) -> None:
        path = self._cache_path(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)

    def _paginate(
        self, symbol: str, timeframe: str, since: int, end: int
    ) -> list[list[float]]:
        """Page ``fetch`` forward from ``since`` until candles reach ``end``.

        Advances ``since`` past the last returned candle each page; stops when a page is
        empty, a short page signals the tail, or the next ``since`` passes ``end``. The
        page ceiling guarantees termination even if a fetch never advances.
        """

        fetch = self._fetch_fn()
        duration = timeframe_to_ms(timeframe)
        rows: list[list[float]] = []
        cursor = since
        for _ in range(_MAX_PAGES):
            if cursor >= end:
                break
            page = self._fetch_with_retry(fetch, symbol, timeframe, cursor, self.page_limit)
            if not page:
                break
            rows.extend(page)
            last_ts = int(page[-1][0])
            next_cursor = last_ts + duration
            logger.debug(
                "%s %s: fetched %d bars, cursor→%d",
                symbol,
                timeframe,
                len(page),
                next_cursor,
            )
            # No forward progress (fetch pinned to the same bar): stop to avoid a loop.
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(page) < self.page_limit:
                break
        return rows

    def get_history(self, symbol: str, timeframe: str, start: int, end: int) -> Candles:
        """Assembled candles for ``[start - warm-up, end]``, cached and incremental.

        Fetches to cover ``start`` minus ``warmup_bars`` candles through ``end``. The
        on-disk cache is read first; only the span beyond what the cache already holds is
        fetched (nothing at all on a fully-cached repeat), the results are merged,
        de-duplicated by timestamp, sorted, and written back. Returns the merged history
        clipped to ``[fetch_start, end]`` as a :class:`Candles` (via the shared
        :func:`~trader.market_data.map_ohlcv` mapping), or an empty frame when no candles
        exist at all.
        """

        logger.info(
            "fetching history %s %s over [%d, %d]", symbol, timeframe, start, end
        )
        duration = timeframe_to_ms(timeframe)
        fetch_start = start - self.warmup_bars * duration

        cached = self._read_cache(symbol, timeframe)
        if not cached.empty:
            have_max = int(cached["timestamp"].max())
            # Resume just past the newest cached candle; if the cache already reaches the
            # requested end there is nothing to fetch (the repeat-run no-fetch guarantee).
            resume = have_max + duration
        else:
            resume = fetch_start

        fetched = self._paginate(symbol, timeframe, resume, end)

        if not fetched:
            logger.info(
                "cache already satisfies %s %s; no fetch needed", symbol, timeframe
            )

        if fetched:
            new_frame = pd.DataFrame(fetched, columns=OHLCV_COLUMNS)
            merged = pd.concat([cached, new_frame], ignore_index=True)
            merged = (
                merged.drop_duplicates(subset="timestamp")
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
            # Drop the still-forming candle before persisting so the cache holds only
            # completed, immutable candles.
            merged = self._drop_forming(merged, timeframe)
            self._write_cache(symbol, timeframe, merged)
        else:
            merged = cached

        # Never return the still-forming candle as a final candle, even when serving a
        # fully-cached read that did no fetch.
        merged = self._drop_forming(merged, timeframe)

        if merged.empty:
            return Candles(symbol=symbol, timeframe=timeframe, frame=_empty_frame())

        clipped = merged[
            (merged["timestamp"] >= fetch_start) & (merged["timestamp"] <= end)
        ].reset_index(drop=True)
        return map_ohlcv(symbol, timeframe, clipped.to_numpy().tolist())
