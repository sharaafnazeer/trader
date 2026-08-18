"""Tests for the momentum ``run_movers`` orchestration and the ``movers`` CLI command.

No network: a fake :class:`~trader.market_data.MarketDataProvider` serves hand-built
candles and order books and records every call, so the once-per-run BTC benchmark, the
ranking, the surfaced-vs-retained gating, and graceful per-coin skip are all observable
without wall-clock throttling (``sleep`` is stubbed). The BTC benchmark is exercised
through the real :class:`~trader.movers.MarketDataBenchmark` seam so the "fetched
exactly once regardless of watchlist size" acceptance criterion is asserted on the
provider's actual call count.
"""

from __future__ import annotations

import re

import pandas as pd
from rich.console import Console
from typer.testing import CliRunner

from trader.cli import app, run_movers_cli
from trader.config import Config
from trader.market_data import Candles, OrderBook
from trader.movers import MarketDataBenchmark, run_movers
from trader.runner import DEFAULT_BTC_SYMBOL


def _frame(rows: list[list[float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _linear_up(symbol: str, rows: int = 60, volume: float = 1_000.0) -> Candles:
    """A steady linear uptrend on flat volume (a LONG drift, modest score)."""
    data = [[i, 100.0 + i - 1.0, 100.0 + i + 1.0, 100.0 + i - 2.0, 100.0 + i, volume]
            for i in range(rows)]
    return Candles(symbol=symbol, timeframe="1d", frame=_frame(data))


def _breakout(symbol: str, rows: int = 60) -> Candles:
    """A range that breaks to a new high on triple volume (a strong LONG mover)."""
    data = []
    for i in range(rows - 1):
        close = 100.0 + (i % 3)
        data.append([i, close - 0.5, close + 1.0, close - 1.0, close, 1_000.0])
    data.append([rows - 1, 103.0, 130.0, 102.0, 128.0, 3_000.0])
    return Candles(symbol=symbol, timeframe="1d", frame=_frame(data))


# ``min_depth`` is a quote-notional floor (base depth × mid price); at a ~100 mid price
# a 1,000-unit book is ~100k notional (liquid) and a 1-unit book ~100 notional (thin).
_LIQUID = OrderBook(
    symbol="x", best_bid=100.0, best_ask=100.2, bid_depth=1_000.0, ask_depth=1_000.0
)
_THIN = OrderBook(symbol="x", best_bid=100.0, best_ask=100.2, bid_depth=1.0, ask_depth=1.0)


class FakeMarketData:
    """Serves canned candles/books per symbol and records every call it receives."""

    def __init__(
        self,
        candles: dict[str, Candles],
        books: dict[str, OrderBook],
        fail_ohlcv_for: set[str] | None = None,
    ) -> None:
        self._candles = candles
        self._books = books
        self._fail = fail_ohlcv_for or set()
        self.ohlcv_calls: list[str] = []
        self.book_calls: list[str] = []

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        self.ohlcv_calls.append(symbol)
        if symbol in self._fail:
            raise RuntimeError("Symbol not found")
        return self._candles[symbol]

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        self.book_calls.append(symbol)
        return self._books[symbol]


def _btc() -> Candles:
    return _linear_up(DEFAULT_BTC_SYMBOL)


def _no_sleep(_: float) -> None:
    return None


def test_btc_benchmark_fetched_exactly_once_regardless_of_watchlist_size() -> None:
    watchlist = ["AAA", "BBB", "CCC"]
    candles = {DEFAULT_BTC_SYMBOL: _btc(), **{s: _breakout(s) for s in watchlist}}
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, **{s: _LIQUID for s in watchlist}}
    market = FakeMarketData(candles, books)

    run = run_movers(
        market,
        MarketDataBenchmark(market),
        watchlist=watchlist,
        momentum_threshold=0.0,
        sleep=_no_sleep,
    )

    # BTC's daily candles are fetched exactly once for the whole run, not per coin.
    assert market.ohlcv_calls.count(DEFAULT_BTC_SYMBOL) == 1
    assert len(run.coins) == 3
    # Every coin was scored against that single benchmark return.
    assert all(coin.score.symbol in watchlist for coin in run.coins)


def test_surfaced_movers_ranked_by_score_descending() -> None:
    # A strong breakout mover should outscore a flat drift; both are liquid and clear a
    # zero threshold, so both surface and must be ranked highest-first.
    candles = {
        DEFAULT_BTC_SYMBOL: _btc(),
        "STRONG": _breakout("STRONG"),
        "WEAK": _linear_up("WEAK"),
    }
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, "STRONG": _LIQUID, "WEAK": _LIQUID}
    market = FakeMarketData(candles, books)

    run = run_movers(
        market,
        MarketDataBenchmark(market),
        watchlist=["WEAK", "STRONG"],
        momentum_threshold=0.0,
        sleep=_no_sleep,
    )

    surfaced = [score.symbol for score in run.movers]
    assert surfaced == ["STRONG", "WEAK"]
    scores = [score.score for score in run.movers]
    assert scores == sorted(scores, reverse=True)


def test_below_threshold_coins_retained_but_not_surfaced() -> None:
    # An unreachable threshold surfaces nobody, yet every fetched coin is retained so a
    # later ``--all`` view is purely additive.
    candles = {DEFAULT_BTC_SYMBOL: _btc(), "AAA": _breakout("AAA"), "BBB": _linear_up("BBB")}
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, "AAA": _LIQUID, "BBB": _LIQUID}
    market = FakeMarketData(candles, books)

    run = run_movers(
        market,
        MarketDataBenchmark(market),
        watchlist=["AAA", "BBB"],
        momentum_threshold=200.0,
        sleep=_no_sleep,
    )

    assert run.movers == ()
    assert {coin.symbol for coin in run.coins} == {"AAA", "BBB"}
    assert all(not coin.surfaced for coin in run.coins)


def test_illiquid_coin_retained_but_not_surfaced() -> None:
    # A thin book fails the liquidity filter: excluded from the surfaced movers but still
    # retained (marked not-liquid) in the run; the liquid coin surfaces.
    candles = {DEFAULT_BTC_SYMBOL: _btc(), "LIQ": _breakout("LIQ"), "THIN": _breakout("THIN")}
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, "LIQ": _LIQUID, "THIN": _THIN}
    market = FakeMarketData(candles, books)

    run = run_movers(
        market,
        MarketDataBenchmark(market),
        watchlist=["LIQ", "THIN"],
        momentum_threshold=0.0,
        sleep=_no_sleep,
    )

    assert [score.symbol for score in run.movers] == ["LIQ"]
    thin = next(coin for coin in run.coins if coin.symbol == "THIN")
    assert thin.liquid is False and thin.surfaced is False


def test_failed_market_data_is_skipped_and_recorded() -> None:
    candles = {DEFAULT_BTC_SYMBOL: _btc(), "OK": _breakout("OK")}
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, "OK": _LIQUID}
    market = FakeMarketData(candles, books, fail_ohlcv_for={"BAD"})

    run = run_movers(
        market,
        MarketDataBenchmark(market),
        watchlist=["OK", "BAD"],
        momentum_threshold=0.0,
        sleep=_no_sleep,
    )

    assert [failure.symbol for failure in run.failures] == ["BAD"]
    assert [coin.symbol for coin in run.coins] == ["OK"]
    assert [score.symbol for score in run.movers] == ["OK"]


def test_movers_command_renders_ranked_list() -> None:
    candles = {DEFAULT_BTC_SYMBOL: _btc(), "AAA": _breakout("AAA")}
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, "AAA": _LIQUID}
    market = FakeMarketData(candles, books)
    config = Config(watchlist=["AAA"], momentum_threshold=0.0)

    console = Console(width=200, force_terminal=False)
    with console.capture() as capture:
        run = run_movers_cli(market, console, config, sleep=_no_sleep)
    output = capture.get()

    assert "Momentum movers" in output
    assert "AAA" in output
    assert [score.symbol for score in run.movers] == ["AAA"]


def test_movers_command_help_exits_zero() -> None:
    result = CliRunner().invoke(app, ["movers", "--help"])
    assert result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--config" in plain
