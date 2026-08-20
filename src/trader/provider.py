"""The data-fetch boundary.

``AnalysisProvider`` is the single seam through which the bot reads TradingView's
technical-analysis recommendation. It is a Protocol so tests can substitute a fake
without touching the network; the concrete :class:`TradingViewProvider` wraps the
``tradingview-ta`` library.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Recommendation labels as returned by TradingView's summary.
STRONG_BUY = "STRONG_BUY"
BUY = "BUY"
NEUTRAL = "NEUTRAL"
SELL = "SELL"
STRONG_SELL = "STRONG_SELL"

# Map internal (ccxt-style) timeframe codes to the interval strings ``tradingview-ta``
# expects. Only the weekly code differs: ccxt uses ``"1w"`` while TradingView uses
# ``"1W"``. Monthly (``"1M"``) and the intraday/daily codes are identical, so they
# pass through unchanged.
_TRADINGVIEW_INTERVALS: dict[str, str] = {"1w": "1W"}


def to_tradingview_interval(interval: str) -> str:
    """Translate an internal timeframe code to the ``tradingview-ta`` interval string."""

    return _TRADINGVIEW_INTERVALS.get(interval, interval)


@dataclass(frozen=True)
class TimeframeResult:
    """TradingView's recommendation for one symbol at one timeframe."""

    symbol: str
    interval: str
    recommendation: str
    buy: int
    neutral: int
    sell: int


class AnalysisProvider(Protocol):
    """Fetches TradingView recommendations, per symbol or in a batch."""

    def get_analysis(
        self, symbol: str, exchange: str, screener: str, interval: str
    ) -> TimeframeResult: ...

    def get_analysis_batch(
        self, symbols: list[str], exchange: str, screener: str, interval: str
    ) -> dict[str, TimeframeResult]:
        """Fetch one timeframe for many symbols, keyed by plain watchlist symbol.

        Symbols absent from the underlying response are omitted from the result.
        """
        ...


def _as_int(value: object) -> int:
    """Coerce a summary count (int or float from the library) to int; 0 if absent."""
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def map_summary(symbol: str, interval: str, summary: dict[str, object]) -> TimeframeResult:
    """Map a ``tradingview-ta`` analysis summary dict into a :class:`TimeframeResult`.

    Factored out so the mapping can be unit-tested against a canned response without
    performing any network I/O.
    """

    recommendation = str(summary.get("RECOMMENDATION", NEUTRAL))
    return TimeframeResult(
        symbol=symbol,
        interval=interval,
        recommendation=recommendation,
        buy=_as_int(summary.get("BUY")),
        neutral=_as_int(summary.get("NEUTRAL")),
        sell=_as_int(summary.get("SELL")),
    )


def map_multiple_analysis(
    symbols: list[str],
    exchange: str,
    interval: str,
    response: Mapping[str, Any],
) -> dict[str, TimeframeResult]:
    """Map a ``get_multiple_analysis`` response into plain-symbol-keyed results.

    The request symbol for each watchlist ``symbol`` is ``f"{exchange}:{symbol}"``
    upper-cased, and the internal ``interval`` is translated via
    :func:`to_tradingview_interval` (e.g. ``"1w"`` -> ``"1W"``). The response is keyed
    by those request symbols (``{"EXCHANGE:SYMBOL": Analysis}``); each present entry is
    mapped back to the plain watchlist symbol via :func:`map_summary`. Symbols absent
    from the response are omitted. Pure and network-free so it can be unit-tested
    against a canned response.
    """

    tv_interval = to_tradingview_interval(interval)
    results: dict[str, TimeframeResult] = {}
    for symbol in symbols:
        request_symbol = f"{exchange}:{symbol}".upper()
        analysis = response.get(request_symbol)
        if analysis is None:
            continue
        results[symbol] = map_summary(symbol, tv_interval, analysis.summary)
    return results


# Signature of the underlying multi-symbol fetch: ``(screener, interval, symbols)`` ->
# ``{"EXCHANGE:SYMBOL": Analysis}`` (each value exposing ``.summary``). Injectable so
# tests substitute a fake and no network is touched.
MultiFetch = Callable[[str, str, list[str]], Mapping[str, Any]]

# Injectable sleep for retry backoff, so tests exercise retries without wall-clock delay.
SleepFn = Callable[[float], None]

# ``get_multiple_analysis`` accepts many symbols per request but very large lists risk
# oversized/failing requests, so the watchlist is split into chunks of this size by
# default. Configurable via YAML (``tradingview_batch_size``).
DEFAULT_BATCH_SIZE = 100

# A batch failure now affects a whole timeframe, so a transient blip is absorbed with a
# small bounded retry + linear backoff before propagating (mirrors ``historical_data``).
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF = 1.0


@dataclass(frozen=True)
class TradingViewProvider:
    """Concrete :class:`AnalysisProvider` backed by the ``tradingview-ta`` library.

    ``multi_fetch`` is the injectable underlying batch fetch; when ``None`` a
    lazily-imported wrapper around ``tradingview_ta.get_multiple_analysis`` is used, so
    importing this module never requires the network-bound dependency. ``batch_size``
    caps how many symbols go into a single underlying request (a larger watchlist fans
    out into several merged calls). Each underlying call is retried up to
    ``max_retries`` times with linear ``retry_backoff`` on any exception before
    propagating, using the injectable ``sleep`` so tests need no wall clock.
    """

    multi_fetch: MultiFetch | None = None
    batch_size: int = DEFAULT_BATCH_SIZE
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    sleep: SleepFn = time.sleep

    def get_analysis(
        self, symbol: str, exchange: str, screener: str, interval: str
    ) -> TimeframeResult:
        # Imported lazily so importing this module (and the core package) does not
        # require the network-bound dependency to be present for pure-logic tests.
        from tradingview_ta import TA_Handler  # type: ignore[import-untyped]

        handler = TA_Handler(
            symbol=symbol,
            screener=screener,
            exchange=exchange,
            interval=to_tradingview_interval(interval),
        )
        analysis = handler.get_analysis()
        return map_summary(symbol, interval, analysis.summary)

    def _default_multi_fetch(
        self, screener: str, interval: str, symbols: list[str]
    ) -> Mapping[str, Any]:
        # Imported lazily (mirrors the ``TA_Handler`` path in :meth:`get_analysis`).
        from tradingview_ta import get_multiple_analysis

        result = get_multiple_analysis(screener, interval, symbols)
        return dict(result) if result is not None else {}

    def _fetch_with_retry(
        self, fetch: MultiFetch, screener: str, interval: str, request_symbols: list[str]
    ) -> Mapping[str, Any]:
        """Call ``fetch`` with bounded retry + linear backoff on any error.

        Retries up to ``max_retries`` times with increasing backoff before re-raising, so
        a transient TradingView blip/rate-limit is absorbed while a persistent failure
        still surfaces (letting the runner degrade that whole interval to no contribution).
        """

        attempt = 0
        while True:
            try:
                return fetch(screener, interval, request_symbols)
            except Exception as exc:  # noqa: BLE001 - retried, then re-raised
                attempt += 1
                if attempt > self.max_retries:
                    raise
                backoff = self.retry_backoff * attempt
                logger.warning(
                    "TradingView batch fetch (interval %s, %d symbols) failed "
                    "(attempt %d/%d): %s; backing off %.2fs",
                    interval,
                    len(request_symbols),
                    attempt,
                    self.max_retries,
                    exc,
                    backoff,
                )
                self.sleep(backoff)

    def get_analysis_batch(
        self, symbols: list[str], exchange: str, screener: str, interval: str
    ) -> dict[str, TimeframeResult]:
        """Fetch one timeframe for ``symbols``, chunked into ``batch_size`` groups.

        The list is split into chunks of at most ``batch_size``, each fetched in one
        underlying request (with bounded retry) and merged into a single plain-symbol-keyed
        result. On a chunk's final failure the exception propagates so the runner degrades
        that interval.
        """

        fetch = self.multi_fetch if self.multi_fetch is not None else self._default_multi_fetch
        tv_interval = to_tradingview_interval(interval)
        results: dict[str, TimeframeResult] = {}
        for start in range(0, len(symbols), self.batch_size):
            chunk = symbols[start : start + self.batch_size]
            request_symbols = [f"{exchange}:{symbol}".upper() for symbol in chunk]
            response = self._fetch_with_retry(fetch, screener, tv_interval, request_symbols)
            results.update(map_multiple_analysis(chunk, exchange, interval, response))
        return results
