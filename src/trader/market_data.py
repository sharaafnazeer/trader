"""The Binance market-data boundary.

``MarketDataProvider`` is the seam through which the bot reads live market data
(OHLCV candles and an order-book snapshot). It is a Protocol so tests can substitute
a fake without touching the network; the concrete :class:`CcxtBinanceProvider` wraps
``ccxt``'s Binance spot public endpoints (no API key required).

This mirrors the v1 :class:`~trader.provider.AnalysisProvider` pattern. The mapping
from raw ``ccxt`` responses into the typed :class:`Candles` / :class:`OrderBook`
values is factored into pure functions so it can be unit-tested against canned
responses with no network I/O.

This module belongs to the analysis core; it does not import ``typer`` or ``rich``.
``ccxt`` is imported lazily so importing this module (and the core package) does not
require the network-bound dependency merely to run the pure-logic tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd

# Column order of a ``ccxt`` OHLCV row: [timestamp, open, high, low, close, volume].
OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

DEFAULT_OHLCV_LIMIT = 300
DEFAULT_ORDER_BOOK_DEPTH = 20


@dataclass(frozen=True, eq=False)
class Candles:
    """A symbol's OHLCV candles for one timeframe, wrapping a pandas frame.

    ``eq=False`` because the wrapped :class:`pandas.DataFrame` has no scalar
    equality; identity comparison is sufficient for how this value is used.
    """

    symbol: str
    timeframe: str
    frame: pd.DataFrame

    @property
    def latest_close(self) -> float:
        """The close price of the most recent candle."""
        return float(self.frame["close"].iloc[-1])

    @property
    def latest_open_time(self) -> int:
        """The open time of the most recent candle, in epoch milliseconds.

        The live fetch includes the *forming* candle, so this value stays fixed for the
        whole life of a bar and steps forward exactly when a new one opens. That makes it
        the natural "has the market actually moved on?" key for anything that must not
        repeat work within a single candle.
        """

        return int(self.frame["timestamp"].iloc[-1])


@dataclass(frozen=True)
class OrderBook:
    """A snapshot of the top of the order book plus cumulative depth.

    ``bid_depth`` / ``ask_depth`` are the cumulative base-asset amounts across the
    returned levels; later tasks narrow them to a configurable price band.
    """

    symbol: str
    best_bid: float
    best_ask: float
    bid_depth: float
    ask_depth: float

    @property
    def spread(self) -> float:
        """Absolute spread: best ask minus best bid."""
        return self.best_ask - self.best_bid


class MarketDataProvider(Protocol):
    """Fetches live market data for a single symbol."""

    def get_ohlcv(
        self, symbol: str, timeframe: str, limit: int = DEFAULT_OHLCV_LIMIT
    ) -> Candles: ...

    def get_order_book(
        self, symbol: str, depth: int = DEFAULT_ORDER_BOOK_DEPTH
    ) -> OrderBook: ...


def map_ohlcv(symbol: str, timeframe: str, raw: list[list[float]]) -> Candles:
    """Map a ``ccxt`` ``fetch_ohlcv`` response into a :class:`Candles` frame.

    Each raw row is ``[timestamp, open, high, low, close, volume]``. Factored out so
    the mapping can be unit-tested against a canned response with no network I/O.
    """

    frame = pd.DataFrame(raw, columns=OHLCV_COLUMNS)
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def map_order_book(symbol: str, raw: dict[str, object]) -> OrderBook:
    """Map a ``ccxt`` ``fetch_order_book`` response into an :class:`OrderBook`.

    ``raw`` has ``bids`` and ``asks`` lists of ``[price, amount]`` levels sorted
    best-first. Factored out so the mapping can be unit-tested against a canned
    response with no network I/O.
    """

    bids = raw.get("bids") or []
    asks = raw.get("asks") or []
    if not isinstance(bids, list) or not bids:
        raise ValueError(f"order book for {symbol} has no bids")
    if not isinstance(asks, list) or not asks:
        raise ValueError(f"order book for {symbol} has no asks")

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    bid_depth = sum(float(level[1]) for level in bids)
    ask_depth = sum(float(level[1]) for level in asks)

    return OrderBook(
        symbol=symbol,
        best_bid=best_bid,
        best_ask=best_ask,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
    )


class CcxtBinanceProvider:
    """Concrete :class:`MarketDataProvider` backed by ``ccxt``'s Binance spot API."""

    def __init__(self) -> None:
        # ccxt is untyped (Any); the client is stored as Any so attribute access on
        # its unified API (fetch_ohlcv / fetch_order_book) type-checks under --strict.
        self._client: Any = None

    def _exchange(self) -> Any:
        # Imported and constructed lazily so the core package imports without ccxt,
        # and so a single client is reused across requests within a run.
        if self._client is None:
            import ccxt

            self._client = ccxt.binance({"enableRateLimit": True})
        return self._client

    def get_ohlcv(
        self, symbol: str, timeframe: str, limit: int = DEFAULT_OHLCV_LIMIT
    ) -> Candles:
        raw = self._exchange().fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return map_ohlcv(symbol, timeframe, raw)

    def get_order_book(
        self, symbol: str, depth: int = DEFAULT_ORDER_BOOK_DEPTH
    ) -> OrderBook:
        raw = self._exchange().fetch_order_book(symbol, limit=depth)
        return map_order_book(symbol, raw)
