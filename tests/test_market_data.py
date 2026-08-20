"""Tests for the Binance market-data boundary mapping (no network).

The concrete provider hits ccxt/Binance over the wire and is not unit-tested;
instead the pure mapping functions are exercised against canned ccxt-shaped
responses, mirroring the v1 ``map_summary`` canned-response test.
"""

from __future__ import annotations

import pytest

from trader.market_data import (
    OHLCV_COLUMNS,
    Candles,
    OrderBook,
    map_ohlcv,
    map_order_book,
)


def _canned_ohlcv() -> list[list[float]]:
    # ccxt fetch_ohlcv rows: [timestamp, open, high, low, close, volume].
    return [
        [1_700_000_000_000, 100.0, 110.0, 95.0, 105.0, 1_000.0],
        [1_700_000_600_000, 105.0, 115.0, 104.0, 112.0, 1_200.0],
        [1_700_001_200_000, 112.0, 120.0, 111.0, 118.0, 900.0],
    ]


def test_map_ohlcv_produces_frame_with_expected_rows_and_columns() -> None:
    candles = map_ohlcv("BTC/USDT", "4h", _canned_ohlcv())

    assert isinstance(candles, Candles)
    assert candles.symbol == "BTC/USDT"
    assert candles.timeframe == "4h"
    assert list(candles.frame.columns) == OHLCV_COLUMNS
    assert len(candles.frame) == 3


def test_candles_latest_close_is_last_row_close() -> None:
    candles = map_ohlcv("BTC/USDT", "4h", _canned_ohlcv())

    assert candles.latest_close == 118.0
    assert isinstance(candles.latest_close, float)


def test_candles_latest_open_time_is_last_row_timestamp() -> None:
    candles = map_ohlcv("BTC/USDT", "4h", _canned_ohlcv())

    assert candles.latest_open_time == 1_700_001_200_000
    assert isinstance(candles.latest_open_time, int)


def test_map_order_book_spread_is_best_ask_minus_best_bid() -> None:
    # ccxt fetch_order_book shape: bids/asks are [price, amount] levels, best first.
    canned = {
        "bids": [[100.0, 2.0], [99.5, 3.0], [99.0, 5.0]],
        "asks": [[100.5, 1.0], [101.0, 4.0], [101.5, 2.0]],
    }

    order_book = map_order_book("BTC/USDT", canned)

    assert isinstance(order_book, OrderBook)
    assert order_book.best_bid == 100.0
    assert order_book.best_ask == 100.5
    assert order_book.spread == pytest.approx(0.5)
    # Cumulative depth across the returned levels.
    assert order_book.bid_depth == pytest.approx(10.0)
    assert order_book.ask_depth == pytest.approx(7.0)


def test_map_order_book_rejects_empty_sides() -> None:
    with pytest.raises(ValueError):
        map_order_book("BTC/USDT", {"bids": [], "asks": [[100.0, 1.0]]})
    with pytest.raises(ValueError):
        map_order_book("BTC/USDT", {"bids": [[100.0, 1.0]], "asks": []})
