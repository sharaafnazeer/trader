"""Tests for the data-fetch boundary mapping (no network)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from trader.provider import (
    BUY,
    SELL,
    TimeframeResult,
    TradingViewProvider,
    map_multiple_analysis,
    map_summary,
)


def test_map_summary_maps_canned_tradingview_response() -> None:
    # Shape of tradingview-ta's Analysis.summary.
    canned = {"RECOMMENDATION": BUY, "BUY": 12, "SELL": 3, "NEUTRAL": 11}

    result = map_summary("BTCUSDT", "1d", canned)

    assert result == TimeframeResult(
        symbol="BTCUSDT",
        interval="1d",
        recommendation=BUY,
        buy=12,
        neutral=11,
        sell=3,
    )


def test_map_summary_defaults_when_fields_missing() -> None:
    result = map_summary("ETHUSDT", "4h", {})

    assert result.recommendation == "NEUTRAL"
    assert (result.buy, result.neutral, result.sell) == (0, 0, 0)


@dataclass(frozen=True)
class _FakeAnalysis:
    """Shape of a ``get_multiple_analysis`` response value: exposes ``.summary``."""

    summary: dict[str, Any]


def test_map_multiple_analysis_maps_canned_response_to_plain_keys() -> None:
    # Response keyed by EXCHANGE:SYMBOL (the shape of get_multiple_analysis output).
    response = {
        "BINANCE:BTCUSDT": _FakeAnalysis(
            {"RECOMMENDATION": BUY, "BUY": 12, "SELL": 3, "NEUTRAL": 11}
        ),
        "BINANCE:ETHUSDT": _FakeAnalysis(
            {"RECOMMENDATION": SELL, "BUY": 1, "SELL": 9, "NEUTRAL": 2}
        ),
    }

    result = map_multiple_analysis(["BTCUSDT", "ETHUSDT"], "BINANCE", "1d", response)

    assert set(result) == {"BTCUSDT", "ETHUSDT"}
    assert result["BTCUSDT"] == TimeframeResult(
        symbol="BTCUSDT", interval="1d", recommendation=BUY, buy=12, neutral=11, sell=3
    )
    assert result["ETHUSDT"] == TimeframeResult(
        symbol="ETHUSDT", interval="1d", recommendation=SELL, buy=1, neutral=2, sell=9
    )


def test_map_multiple_analysis_uppercases_request_symbol_and_maps_interval() -> None:
    # A lowercase watchlist symbol/exchange must be matched via the uppercased
    # EXCHANGE:SYMBOL request key, and the internal interval mapped (1w -> 1W).
    response = {
        "BINANCE:BTCUSDT": _FakeAnalysis({"RECOMMENDATION": BUY, "BUY": 5, "SELL": 1, "NEUTRAL": 2})
    }

    result = map_multiple_analysis(["btcusdt"], "binance", "1w", response)

    assert set(result) == {"btcusdt"}  # keyed by the original plain watchlist symbol
    assert result["btcusdt"].interval == "1W"
    assert result["btcusdt"].recommendation == BUY


def test_map_multiple_analysis_omits_symbol_absent_from_response() -> None:
    response = {
        "BINANCE:BTCUSDT": _FakeAnalysis({"RECOMMENDATION": BUY, "BUY": 5, "SELL": 1, "NEUTRAL": 2})
    }

    result = map_multiple_analysis(["BTCUSDT", "ETHUSDT"], "BINANCE", "1d", response)

    assert set(result) == {"BTCUSDT"}


def test_get_analysis_batch_builds_request_and_maps_via_injected_fetch() -> None:
    # No network: an injected fetch records its args and returns a canned response.
    recorded: dict[str, Any] = {}

    def fake_fetch(screener: str, interval: str, symbols: list[str]) -> dict[str, Any]:
        recorded["args"] = (screener, interval, tuple(symbols))
        return {
            "BINANCE:BTCUSDT": _FakeAnalysis(
                {"RECOMMENDATION": BUY, "BUY": 7, "SELL": 1, "NEUTRAL": 2}
            )
        }

    provider = TradingViewProvider(multi_fetch=fake_fetch)
    result = provider.get_analysis_batch(["btcusdt"], "binance", "crypto", "1w")

    # Request symbols are EXCHANGE:SYMBOL upper-cased; the interval is mapped 1w -> 1W.
    assert recorded["args"] == ("crypto", "1W", ("BINANCE:BTCUSDT",))
    assert set(result) == {"btcusdt"}
    assert result["btcusdt"].recommendation == BUY


def test_get_analysis_batch_chunks_a_watchlist_larger_than_the_cap() -> None:
    # N=250 with batch_size=100 -> ceil(250/100) = 3 calls (distinguishes ceil from floor).
    symbols = [f"C{i}USDT" for i in range(250)]
    chunk_sizes: list[int] = []

    def fake_fetch(screener: str, interval: str, request_symbols: list[str]) -> dict[str, Any]:
        chunk_sizes.append(len(request_symbols))
        # Echo back every requested symbol so the merged result covers all of them.
        return {
            rs: _FakeAnalysis({"RECOMMENDATION": BUY, "BUY": 3, "SELL": 1, "NEUTRAL": 1})
            for rs in request_symbols
        }

    provider = TradingViewProvider(multi_fetch=fake_fetch, batch_size=100)
    result = provider.get_analysis_batch(symbols, "BINANCE", "crypto", "1d")

    assert len(chunk_sizes) == 3
    assert chunk_sizes == [100, 100, 50]  # ceil split, last chunk is the remainder
    assert set(result) == set(symbols)  # merged result covers every requested symbol


def test_get_analysis_batch_retries_a_transient_failure_then_succeeds() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky_fetch(screener: str, interval: str, request_symbols: list[str]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("transient blip")
        return {
            "BINANCE:BTCUSDT": _FakeAnalysis(
                {"RECOMMENDATION": BUY, "BUY": 5, "SELL": 1, "NEUTRAL": 2}
            )
        }

    provider = TradingViewProvider(
        multi_fetch=flaky_fetch, max_retries=2, sleep=sleeps.append
    )
    result = provider.get_analysis_batch(["BTCUSDT"], "BINANCE", "crypto", "1d")

    assert calls["n"] == 2  # failed once, retried once, then succeeded
    assert len(sleeps) == 1  # backoff slept once, no wall clock
    assert result["BTCUSDT"].recommendation == BUY


def test_get_analysis_batch_raises_when_fetch_always_fails() -> None:
    def always_fail(screener: str, interval: str, request_symbols: list[str]) -> dict[str, Any]:
        raise ConnectionError("still down")

    provider = TradingViewProvider(
        multi_fetch=always_fail, max_retries=2, sleep=lambda _: None
    )
    # Propagates after exhausting the retry budget so the runner degrades the interval.
    with pytest.raises(ConnectionError):
        provider.get_analysis_batch(["BTCUSDT"], "BINANCE", "crypto", "1d")
