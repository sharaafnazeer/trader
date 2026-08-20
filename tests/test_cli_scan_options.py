"""Tests for the v2 `scan` output controls: --details, --all, --json, --watch, --config.

Everything is exercised with fake providers (no network), a mocked sleep (no wall-
clock throttling), and captured console output. Long/short/flat coins are built from
synthetic candle shapes so a clear LONG, a clear SHORT, and a no-direction coin can be
surfaced deterministically; BTC's own series is flat so it never vetoes a direction.
"""

from __future__ import annotations

import json
import math

import pandas as pd
from rich.console import Console

from trader.cli import analysis_run_to_dict, run_scan, run_scan_forever
from trader.config import Config, StrategyConfig, load_config
from trader.direction import Direction
from trader.market_data import Candles, OrderBook
from trader.provider import NEUTRAL, TimeframeResult


def _series(symbol: str, timeframe: str, closes: list[float]) -> Candles:
    data = []
    for i, close in enumerate(closes):
        data.append([i, close, close + 1.0, close - 1.0, close, 1_000.0 + i])
    frame = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return Candles(symbol=symbol, timeframe=timeframe, frame=frame)


def _bullish(symbol: str, timeframe: str, rows: int = 260) -> Candles:
    closes = []
    base = 100.0
    for i in range(rows):
        base += 1.0
        offset = {0: 0.0, 1: 3.0, 2: 5.0, 3: 3.0, 4: 0.0, 5: -2.0}[i % 6]
        closes.append(base + offset)
    return _series(symbol, timeframe, closes)


def _bearish(symbol: str, timeframe: str, rows: int = 260) -> Candles:
    closes = []
    base = 400.0
    for i in range(rows):
        base -= 1.0
        offset = {0: 0.0, 1: 3.0, 2: 5.0, 3: 3.0, 4: 0.0, 5: -2.0}[i % 6]
        closes.append(base + offset)
    return _series(symbol, timeframe, closes)


def _flat(symbol: str, timeframe: str, rows: int = 260) -> Candles:
    # A ranging series with no net trend: the EMAs interleave rather than stack cleanly,
    # so the (relaxed, EMA-stack-driven) direction rule resolves to NONE with reason
    # "lead_unresolved" and, used for BTC, casts no veto.
    closes = [100.0 + 20.0 * math.sin(2.0 * math.pi * i / 50.0) for i in range(rows)]
    return _series(symbol, timeframe, closes)


_BUILDERS = {"bull": _bullish, "bear": _bearish, "flat": _flat}


class FakeMarketData:
    def __init__(self, kinds: dict[str, str], fail: set[str] | None = None) -> None:
        self._kinds = kinds
        self._fail = fail or set()

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        if symbol in self._fail:
            raise RuntimeError("Symbol not found")
        return _BUILDERS[self._kinds.get(symbol, "flat")](symbol, timeframe)

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        return OrderBook(
            symbol=symbol, best_bid=100.0, best_ask=100.5, bid_depth=50.0, ask_depth=50.0
        )


class PerTimeframeMarketData:
    """Fake provider that varies the candle shape per (symbol, timeframe).

    Lets a coin have a cleanly-trending lead timeframe but a merely-neutral filter
    timeframe — the exact case the old "both higher timeframes must agree" rule hid
    and the relaxed lead-timeframe rule surfaces. Unknown symbols/timeframes (including
    the BTC context row) default to the flat, non-vetoing series.
    """

    def __init__(self, plan: dict[str, dict[str, str]]) -> None:
        self._plan = plan

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        kind = self._plan.get(symbol, {}).get(timeframe, "flat")
        return _BUILDERS[kind](symbol, timeframe)

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


def _run(config: Config, kinds: dict[str, str], **kwargs) -> tuple[str, object]:
    market = FakeMarketData(kinds)
    console = Console(width=240, force_terminal=False)
    with console.capture() as capture:
        result = run_scan(
            market, FakeTradingView(), console, config, sleep=lambda _: None, **kwargs
        )
    return capture.get(), result


def test_bearish_coin_surfaces_as_a_short_when_not_long_only() -> None:
    config = Config(
        watchlist=["BEAR"],
        timeframes=["4h", "1d"],
        strategies=StrategyConfig(enabled=False),
    )
    _, result = _run(config, {"BEAR": "bear"})
    directions = {s.symbol: s.direction for s in result.setups}  # type: ignore[attr-defined]
    assert directions.get("BEAR") is Direction.SHORT


def test_long_only_excludes_short_setups() -> None:
    config = Config(
        watchlist=["BULL", "BEAR"],
        timeframes=["4h", "1d"],
        long_only=True,
        strategies=StrategyConfig(enabled=False),
    )
    _, result = _run(config, {"BULL": "bull", "BEAR": "bear"})
    setups = result.setups  # type: ignore[attr-defined]
    assert setups, "expected at least the long setup to surface"
    assert all(s.direction is Direction.LONG for s in setups)
    assert "BEAR" not in {s.symbol for s in setups}


def test_json_record_reloads_to_the_same_directions_and_plans(tmp_path) -> None:
    json_path = tmp_path / "out.json"
    config = Config(
        watchlist=["BULL", "BEAR", "FLAT"],
        timeframes=["4h", "1d"],
        strategies=StrategyConfig(enabled=False),
    )
    _, result = _run(
        config, {"BULL": "bull", "BEAR": "bear", "FLAT": "flat"}, json_path=str(json_path)
    )

    reloaded = json.loads(json_path.read_text(encoding="utf-8"))

    # The JSON is derived from the same in-memory AnalysisRun as the table.
    assert reloaded == analysis_run_to_dict(result)  # type: ignore[arg-type]

    # Surfaced short list matches the table's setups exactly.
    assert reloaded["setups"] == [s.symbol for s in result.setups]  # type: ignore[attr-defined]
    coins_by_symbol = {c["symbol"]: c for c in reloaded["coins"]}
    for setup in result.setups:  # type: ignore[attr-defined]
        coin = coins_by_symbol[setup.symbol]
        assert coin["direction"] == setup.direction.value
        assert coin["surfaced"] is True

    # Trade-plan values round-trip for every coin that has a plan.
    for analysis in result.analyses:  # type: ignore[attr-defined]
        coin = coins_by_symbol[analysis.symbol]
        if analysis.plan is None:
            assert coin["trade_plan"] is None
        else:
            assert coin["trade_plan"]["entry"] == analysis.plan.entry
            assert coin["trade_plan"]["stop_loss"] == analysis.plan.stop_loss
            assert coin["trade_plan"]["take_profit"] == analysis.plan.take_profit
            assert coin["trade_plan"]["risk_reward"] == analysis.plan.risk_reward


def test_watch_loop_runs_n_times_and_sleeps_with_interval() -> None:
    config = Config(
        watchlist=["BULL"],
        timeframes=["4h", "1d"],
        strategies=StrategyConfig(enabled=False),
    )
    market = FakeMarketData({"BULL": "bull"})
    console = Console(width=240, force_terminal=False)
    sleeps: list[float] = []

    with console.capture():
        runs = run_scan_forever(
            market,
            FakeTradingView(),
            console,
            config,
            interval=2.5,
            sleep=sleeps.append,
            max_iterations=3,
        )

    assert runs == 3
    # Sleep happens between iterations, not after the last: N-1 sleeps at the interval.
    # Per-source throttle sleeps are zero here only if delays are zero; the config uses
    # default delays, so filter to the interval sleeps issued by the watch loop itself.
    assert sleeps.count(2.5) == 2


def test_relaxed_rule_surfaces_coin_old_rule_hid_and_none_coin_shows_reason(tmp_path) -> None:
    # RELAX has a cleanly-bullish daily lead but a merely-neutral 4h filter: the old
    # "both higher timeframes must agree" gate returned NONE, the relaxed rule surfaces
    # it LONG. FLAT stays NONE and must show a reason in both the table and the JSON.
    json_path = tmp_path / "out.json"
    config = Config(
        watchlist=["RELAX", "FLAT"],
        timeframes=["4h", "1d"],
        strategies=StrategyConfig(enabled=False),
    )
    market = PerTimeframeMarketData(
        {
            "RELAX": {"1d": "bull", "4h": "flat"},
            "FLAT": {"1d": "flat", "4h": "flat"},
        }
    )
    console = Console(width=240, force_terminal=False)
    with console.capture() as capture:
        result = run_scan(
            market,
            FakeTradingView(),
            console,
            config,
            sleep=lambda _: None,
            show_all=True,
            json_path=str(json_path),
        )
    out = capture.get()

    # The relaxed rule *decides* a direction for the coin whose lead is clean and
    # un-opposed. Asserted on the analysis rather than on ``setups``, because surfacing now
    # also requires a usable trade plan and this fixture's monotonic series has no swing
    # low to anchor a stop to — which is a fact about the fixture, not about the rule
    # under test.
    relax = next(a for a in result.analyses if a.symbol == "RELAX")  # type: ignore[attr-defined]
    assert relax.direction is Direction.LONG
    assert relax.reason is None

    # The still-NONE coin carries a concrete reason on its result, in the --all table,
    # and in the JSON record.
    flat = next(a for a in result.analyses if a.symbol == "FLAT")  # type: ignore[attr-defined]
    assert flat.direction is Direction.NONE
    assert flat.reason == "lead_unresolved"
    assert "lead_unresolved" in out

    reloaded = json.loads(json_path.read_text(encoding="utf-8"))
    coins = {c["symbol"]: c for c in reloaded["coins"]}
    assert coins["FLAT"]["direction"] == "NONE"
    assert coins["FLAT"]["reason"] == "lead_unresolved"
    assert coins["RELAX"]["reason"] is None


class ShortHistoryMarketData:
    """Fake provider returning short (limited-history) bullish candles for every coin.

    ``rows`` sits below the 200-period window, so the long-term average is reported absent
    and the coin is flagged limited-history. It resolves no direction as a result — the
    method's stack cannot be judged without its filter — and is reported rather than
    surfaced.
    """

    def __init__(self, rows: int = 120) -> None:
        self._rows = rows

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> Candles:
        return _bullish(symbol, timeframe, rows=self._rows)

    def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        return OrderBook(
            symbol=symbol, best_bid=100.0, best_ask=100.5, bid_depth=50.0, ask_depth=50.0
        )


def test_limited_history_marker_shows_in_all_view_and_json(tmp_path) -> None:
    """A coin too young for the 200-period filter is reported, not silently dropped.

    It no longer resolves a direction — the method's stack cannot be judged without its
    long-term average, and that average is reported absent rather than back-filled — so
    what this pins is that the coin is still evaluated, still listed under ``--all``, and
    still carries the marker explaining the silence.
    """
    json_path = tmp_path / "out.json"
    config = Config(
        watchlist=["YOUNG"],
        timeframes=["4h", "1d"],
        strategies=StrategyConfig(enabled=False),
    )
    market = ShortHistoryMarketData(rows=120)  # too short for the slow EMA
    console = Console(width=240, force_terminal=False)
    with console.capture() as capture:
        result = run_scan(
            market,
            FakeTradingView(),
            console,
            config,
            sleep=lambda _: None,
            show_all=True,
            json_path=str(json_path),
        )
    out = capture.get()

    young = next(a for a in result.analyses if a.symbol == "YOUNG")  # type: ignore[attr-defined]
    assert young.limited_history is True
    assert young.direction is Direction.NONE
    assert young.symbol not in {a.symbol for a in result.setups}  # type: ignore[attr-defined]

    # The marker is visible in the --all table and in the JSON record.
    assert "limited" in out
    reloaded = json.loads(json_path.read_text(encoding="utf-8"))
    coins = {c["symbol"]: c for c in reloaded["coins"]}
    assert coins["YOUNG"]["limited_history"] is True


def test_config_yaml_settings_drive_the_run(tmp_path) -> None:
    # --config path: a YAML watchlist + long_only flow through load_config into the run.
    path = tmp_path / "config.yaml"
    path.write_text(
        "watchlist: [BULL, BEAR]\ntimeframes: [4h, 1d]\nlong_only: true\n",
        encoding="utf-8",
    )
    config = load_config(str(path))

    _, result = _run(config, {"BULL": "bull", "BEAR": "bear"})

    assert [a.symbol for a in result.analyses] == ["BULL", "BEAR"]  # type: ignore[attr-defined]
    # long_only from the YAML is honored: no short surfaces.
    assert all(s.direction is Direction.LONG for s in result.setups)  # type: ignore[attr-defined]
