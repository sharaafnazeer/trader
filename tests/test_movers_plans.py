"""Tests for the momentum trade plans and the ``movers`` output controls (Task 02).

Covers the breakout/ATR trade plan (exact-arithmetic stop below the trailing high for a
long and above the trailing low for a short, target floored by ``target_rr``), the
``long_only`` suppression of shorts, the ``--all`` and ``--json`` output views, the
advisory disclaimer, and the additive (backward-compatible) ``TradePlanner`` change. No
network: hand-built candle frames and a fake market-data provider.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from rich.console import Console

from trader.cli import _MOVERS_DISCLAIMER, movers_run_to_dict, run_movers_cli
from trader.config import DEFAULT_ATR_BUFFER, DEFAULT_TARGET_RR, Config
from trader.direction import Direction
from trader.indicators import compute_features
from trader.market_data import Candles, OrderBook
from trader.momentum import MomentumModel, trailing_breakout_level
from trader.movers import MarketDataBenchmark, plan_for_mover, run_movers
from trader.runner import DEFAULT_BTC_SYMBOL
from trader.structure import Structure, StructureState
from trader.trade_planner import TradePlanner


def _frame(rows: list[list[float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _breakout(symbol: str = "AAA", rows: int = 60) -> Candles:
    """A range that breaks to a new high on triple volume (a strong LONG mover)."""
    data = []
    for i in range(rows - 1):
        close = 100.0 + (i % 3)
        data.append([i, close - 0.5, close + 1.0, close - 1.0, close, 1_000.0])
    data.append([rows - 1, 103.0, 130.0, 102.0, 128.0, 3_000.0])
    return Candles(symbol=symbol, timeframe="1d", frame=_frame(data))


def _linear_down(symbol: str = "DN", rows: int = 60) -> Candles:
    """A steady downtrend (a SHORT mover)."""
    data = [[i, 200.0 - i + 1.0, 200.0 - i + 2.0, 200.0 - i - 1.0, 200.0 - i, 1_000.0]
            for i in range(rows)]
    return Candles(symbol=symbol, timeframe="1d", frame=_frame(data))


def _linear_up(symbol: str, rows: int = 60) -> Candles:
    data = [[i, 100.0 + i - 1.0, 100.0 + i + 1.0, 100.0 + i - 2.0, 100.0 + i, 1_000.0]
            for i in range(rows)]
    return Candles(symbol=symbol, timeframe="1d", frame=_frame(data))


# ``min_depth`` is a quote-notional floor (base depth × mid price); at a ~100 mid price
# a 1,000-unit book is ~100k notional (liquid) and a 1-unit book ~100 notional (thin).
_LIQUID = OrderBook(
    symbol="x", best_bid=100.0, best_ask=100.2, bid_depth=1_000.0, ask_depth=1_000.0
)
_THIN = OrderBook(symbol="x", best_bid=100.0, best_ask=100.2, bid_depth=1.0, ask_depth=1.0)


class FakeMarketData:
    def __init__(self, candles: dict[str, Candles], books: dict[str, OrderBook]) -> None:
        self._candles = candles
        self._books = books

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        return self._candles[symbol]

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        return self._books[symbol]


def _no_sleep(_: float) -> None:
    return None


# --- breakout/ATR trade plan (exact arithmetic) ----------------------------


def test_long_mover_stop_is_trailing_high_minus_atr_buffer() -> None:
    candles = _breakout()
    score = MomentumModel().score("AAA", candles, btc_return=0.0)
    assert score.direction is Direction.LONG

    plan = plan_for_mover(candles, score, breakout_lookback=20, atr_buffer=0.5, target_rr=2.0)
    assert plan is not None

    level = trailing_breakout_level(candles, 20, Direction.LONG)
    atr = compute_features(candles).atr
    assert plan.invalidation == level
    assert plan.stop_loss == pytest.approx(level - 0.5 * atr)
    # Reward is floored at target_rr, so the resulting R:R is at least target_rr.
    assert plan.risk_reward >= 2.0
    assert plan.risk_reward == pytest.approx(2.0)


def test_short_mover_stop_is_trailing_low_plus_atr_buffer() -> None:
    candles = _linear_down()
    score = MomentumModel().score("DN", candles, btc_return=0.0)
    assert score.direction is Direction.SHORT

    plan = plan_for_mover(candles, score, breakout_lookback=20, atr_buffer=0.5, target_rr=2.0)
    assert plan is not None

    level = trailing_breakout_level(candles, 20, Direction.SHORT)
    atr = compute_features(candles).atr
    assert plan.invalidation == level
    assert plan.stop_loss == pytest.approx(level + 0.5 * atr)
    assert plan.risk_reward >= 2.0


# --- long-only suppression -------------------------------------------------


def test_long_only_suppresses_short_movers() -> None:
    candles = {DEFAULT_BTC_SYMBOL: _linear_up(DEFAULT_BTC_SYMBOL), "DN": _linear_down("DN")}
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, "DN": _LIQUID}
    market = FakeMarketData(candles, books)

    def go(long_only: bool) -> list[str]:
        run = run_movers(
            market,
            MarketDataBenchmark(market),
            watchlist=["DN"],
            momentum_threshold=0.0,
            long_only=long_only,
            sleep=_no_sleep,
        )
        return [score.symbol for score in run.movers]

    # The down-mover surfaces as a SHORT normally, but long-only suppresses it.
    assert go(long_only=False) == ["DN"]
    assert go(long_only=True) == []


def test_surfaced_movers_carry_a_trade_plan() -> None:
    candles = {DEFAULT_BTC_SYMBOL: _linear_up(DEFAULT_BTC_SYMBOL), "AAA": _breakout("AAA")}
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, "AAA": _LIQUID}
    market = FakeMarketData(candles, books)

    run = run_movers(
        market,
        MarketDataBenchmark(market),
        watchlist=["AAA"],
        momentum_threshold=0.0,
        sleep=_no_sleep,
    )
    coin = next(c for c in run.coins if c.symbol == "AAA")
    assert coin.surfaced and coin.plan is not None
    assert coin.plan.direction is Direction.LONG


# --- --all / --json output controls ----------------------------------------


def _capture(config: Config, market: FakeMarketData, **kwargs: object) -> tuple[str, object]:
    console = Console(width=200, force_terminal=False)
    with console.capture() as capture:
        run = run_movers_cli(market, console, config, sleep=_no_sleep, **kwargs)  # type: ignore[arg-type]
    return capture.get(), run


def test_all_view_includes_below_threshold_and_illiquid_coins() -> None:
    candles = {
        DEFAULT_BTC_SYMBOL: _linear_up(DEFAULT_BTC_SYMBOL),
        "WEAK": _linear_up("WEAK"),
        "THIN": _breakout("THIN"),
    }
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, "WEAK": _LIQUID, "THIN": _THIN}
    market = FakeMarketData(candles, books)
    # An unreachable threshold means nobody surfaces; the illiquid coin is filtered too.
    config = Config(watchlist=["WEAK", "THIN"], momentum_threshold=200.0)

    default_out, _ = _capture(config, market)
    all_out, _ = _capture(config, market, show_all=True)

    # Default view surfaces nobody, so neither symbol appears; --all lists both.
    assert "WEAK" not in default_out and "THIN" not in default_out
    assert "WEAK" in all_out and "THIN" in all_out
    assert "below-threshold" in all_out
    assert "illiquid" in all_out


def test_json_reloads_to_same_movers_scores_and_plans(tmp_path: Path) -> None:
    candles = {DEFAULT_BTC_SYMBOL: _linear_up(DEFAULT_BTC_SYMBOL), "AAA": _breakout("AAA")}
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, "AAA": _LIQUID}
    market = FakeMarketData(candles, books)
    config = Config(watchlist=["AAA"], momentum_threshold=0.0)
    json_path = tmp_path / "movers.json"

    _, run = _capture(config, market, json_path=str(json_path))
    data = json.loads(json_path.read_text(encoding="utf-8"))

    # The reloaded record carries the same surfaced movers, scores, and trade plans.
    assert data["movers"] == [score.symbol for score in run.movers]  # type: ignore[attr-defined]
    coin = next(c for c in run.coins if c.symbol == "AAA")  # type: ignore[attr-defined]
    record = next(c for c in data["coins"] if c["symbol"] == "AAA")
    assert record["score"] == pytest.approx(coin.score.score)
    assert record["surfaced"] is True
    assert record["trade_plan"]["entry"] == pytest.approx(coin.plan.entry)
    assert record["trade_plan"]["stop_loss"] == pytest.approx(coin.plan.stop_loss)
    assert record["trade_plan"]["take_profit"] == pytest.approx(coin.plan.take_profit)


def test_disclaimer_appears_in_table_and_json(tmp_path: Path) -> None:
    candles = {DEFAULT_BTC_SYMBOL: _linear_up(DEFAULT_BTC_SYMBOL), "AAA": _breakout("AAA")}
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, "AAA": _LIQUID}
    market = FakeMarketData(candles, books)
    config = Config(watchlist=["AAA"], momentum_threshold=0.0)
    json_path = tmp_path / "movers.json"

    output, run = _capture(config, market, json_path=str(json_path))
    data = json.loads(json_path.read_text(encoding="utf-8"))

    # The disclaimer text is a substring of the rendered output (rich may wrap it, so
    # compare on a distinctive phrase) and is carried verbatim in the JSON record.
    assert "top-buying risk" in output
    assert data["disclaimer"] == _MOVERS_DISCLAIMER
    assert movers_run_to_dict(run)["disclaimer"] == _MOVERS_DISCLAIMER  # type: ignore[arg-type]


def test_movers_table_renders_entry_stop_target_for_surfaced() -> None:
    candles = {DEFAULT_BTC_SYMBOL: _linear_up(DEFAULT_BTC_SYMBOL), "AAA": _breakout("AAA")}
    books = {DEFAULT_BTC_SYMBOL: _LIQUID, "AAA": _LIQUID}
    market = FakeMarketData(candles, books)
    config = Config(watchlist=["AAA"], momentum_threshold=0.0)

    output, run = _capture(config, market)
    coin = next(c for c in run.coins if c.symbol == "AAA")  # type: ignore[attr-defined]
    assert "Entry" in output and "SL" in output and "TP" in output
    # The surfaced mover's entry (latest close) is rendered in the table.
    assert f"{coin.plan.entry:g}" in output


# --- additive TradePlanner change (no regression) --------------------------


def test_injected_invalidation_is_additive_and_backward_compatible() -> None:
    candles = _breakout()
    atr = 4.0
    planner = TradePlanner()

    # Default path (no invalidation) still reads the swing structure.
    pivots = StructureState(structure=Structure.BULLISH, swing_highs=(140.0,), swing_lows=(90.0,))
    swing_plan = planner.plan(Direction.LONG, candles, pivots, atr, atr_buffer=0.5, target_rr=2.0)
    assert swing_plan is not None
    assert swing_plan.invalidation == 90.0
    assert swing_plan.stop_loss == pytest.approx(90.0 - 0.5 * atr)

    # Injected invalidation overrides the swing level (same entry/atr).
    injected = planner.plan(
        Direction.LONG, candles, pivots, atr, atr_buffer=0.5, target_rr=2.0, invalidation=110.0
    )
    assert injected is not None
    assert injected.invalidation == 110.0
    assert injected.stop_loss == pytest.approx(110.0 - 0.5 * atr)


def test_plan_defaults_match_config_defaults() -> None:
    # plan_for_mover's atr_buffer / target_rr defaults mirror the shared config defaults.
    candles = _breakout()
    score = MomentumModel().score("AAA", candles, btc_return=0.0)
    plan = plan_for_mover(candles, score)
    level = trailing_breakout_level(candles, 20, Direction.LONG)
    atr = compute_features(candles).atr
    assert plan is not None
    assert plan.stop_loss == pytest.approx(level - DEFAULT_ATR_BUFFER * atr)
    assert plan.risk_reward == pytest.approx(DEFAULT_TARGET_RR)
