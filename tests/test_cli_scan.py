"""Tests for the CLI `scan` path: renders the ranked short list plus a failure summary.

Exercises the CLI wiring with fake providers (no network), a mocked sleep (no wall-
clock throttling), and captured console output. A surviving coin is built from
bullish, clearly-structured candles so it earns a direction and a score and surfaces
in the short list; a failing coin is skipped and summarized. The command's
registration is verified via `scan --help` (exit code 0).
"""

from __future__ import annotations

import re

import pandas as pd
from rich.console import Console
from typer.testing import CliRunner

from trader.cli import app, run_scan
from trader.config import Config
from trader.market_data import Candles, OrderBook
from trader.provider import NEUTRAL, TimeframeResult


def _bullish_candles(symbol: str, timeframe: str, rows: int = 260) -> Candles:
    """A clearly bullish series: steady uptrend with a sawtooth so swings step up.

    Enough rows for EMA200 to be defined and enough swing highs/lows for the structure
    detector to classify it BULLISH, so the coin earns a LONG direction and a score.
    """

    data = []
    base = 100.0
    for i in range(rows):
        base += 1.0
        offset = {0: 0.0, 1: 3.0, 2: 5.0, 3: 3.0, 4: 0.0, 5: -2.0}[i % 6]
        close = base + offset
        data.append([i, close - 0.5, close + 1.0, close - 1.0, close, 1_000.0 + i])
    frame = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


class FakeMarketData:
    def __init__(self, fail_ohlcv_for: set[str] | None = None) -> None:
        self._fail_ohlcv_for = fail_ohlcv_for or set()

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        if symbol in self._fail_ohlcv_for:
            raise RuntimeError("Symbol not found")
        return _bullish_candles(symbol, timeframe)

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        return OrderBook(
            symbol=symbol, best_bid=100.0, best_ask=100.5, bid_depth=50.0, ask_depth=50.0
        )


class FakeTradingView:
    def get_analysis(
        self, symbol: str, exchange: str, screener: str, interval: str
    ) -> TimeframeResult:
        return TimeframeResult(
            symbol=symbol, interval=interval, recommendation=NEUTRAL, buy=0, neutral=0, sell=0
        )

    def get_analysis_batch(
        self, symbols: list[str], exchange: str, screener: str, interval: str
    ) -> dict[str, TimeframeResult]:
        return {
            symbol: TimeframeResult(
                symbol=symbol, interval=interval, recommendation=NEUTRAL, buy=0, neutral=0, sell=0
            )
            for symbol in symbols
        }


def _config() -> Config:
    # A permissive threshold so the bullish coin clears the (sub-100) core-only total.
    return Config(watchlist=["AAA", "BAD"], timeframes=["4h", "1d"], quality_threshold=40.0)


def _render(config: Config, market: FakeMarketData) -> tuple[str, object]:
    console = Console(width=200, force_terminal=False)
    with console.capture() as capture:
        result = run_scan(market, FakeTradingView(), console, config, sleep=lambda _: None)
    return capture.get(), result


def test_scan_renders_ranked_short_list_and_failure_summary() -> None:
    market = FakeMarketData(fail_ohlcv_for={"BAD"})
    output, result = _render(_config(), market)

    # The surfaced coin appears in the ranked short list with its direction and score;
    # the failed coin is skipped and summarized instead of aborting the run.
    assert "High-conviction setups" in output
    assert "Score/100" in output
    assert "AAA" in output
    assert "LONG" in output
    assert "Skipped coins" in output
    assert "BAD" in output
    assert "Symbol not found" in output
    # AAA surfaced as a setup with a positive total; BAD is a failure.
    setups = result.setups  # type: ignore[attr-defined]
    assert [s.symbol for s in setups] == ["AAA"]
    assert setups[0].total > 0.0
    assert str(setups[0].direction) == "LONG"
    assert [f.symbol for f in result.failures] == ["BAD"]  # type: ignore[attr-defined]


def test_scan_command_help_exits_zero() -> None:
    result = CliRunner().invoke(app, ["scan", "--help"])
    assert result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--config" in plain
