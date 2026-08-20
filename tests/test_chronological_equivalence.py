"""Proves the chronological replay changes nothing.

The backtest now walks a merged timeline so that portfolio-level rules can be expressed at
all. That restructuring is only safe if it is provably behaviour-preserving: if the cap and
the restructure both landed at once, a changed result would be ambiguous — cap effect, or
refactor bug? Every later measurement in this feature rests on the assertions here.

Note what is *not* asserted: that the two produce trades in the same order. They do not, by
construction — the chronological replay emits trades in time order while the per-coin replay
groups them by coin. Comparing element-wise in production order would fail on ordering alone
even when every trade is identical, so trades are compared as sorted sequences.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from baseline_fixture import FIXTURE_CONFIG, build_history
from trader.backtester import Backtester
from trader.config import Config
from trader.market_data import Candles
from trader.metrics import summarize
from trader.replay import timeframe_to_ms
from trader.trade_simulator import TradeOutcome

_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def _key(outcome: TradeOutcome) -> tuple[object, ...]:
    """Everything about a trade that must match, in a comparable form."""
    return (
        outcome.entry_time,
        outcome.symbol,
        outcome.direction.value,
        outcome.entry_price,
        outcome.exit_time,
        outcome.exit_price,
        outcome.result.value,
        outcome.r_multiple,
    )


def _sorted_trades(run) -> list[tuple[object, ...]]:  # type: ignore[no-untyped-def]
    return sorted(_key(o) for o in run.outcomes)


def _both(config: Config, histories, btc):  # type: ignore[no-untyped-def]
    engine = Backtester()
    return (
        engine.run(config, histories, btc),
        engine.run_per_coin(config, histories, btc),
    )


# --- The shared fixture ----------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_runs():  # type: ignore[no-untyped-def]
    histories, btc = build_history()
    return _both(FIXTURE_CONFIG, histories, btc)


def test_both_replays_produce_the_same_set_of_trades(fixture_runs) -> None:  # type: ignore[no-untyped-def]
    chronological, per_coin = fixture_runs

    assert _sorted_trades(chronological) == _sorted_trades(per_coin)
    assert len(chronological.outcomes) == len(per_coin.outcomes) > 0


def test_both_replays_produce_identical_reports(fixture_runs) -> None:  # type: ignore[no-untyped-def]
    # The stronger claim, and the one that actually matters for measurement. It holds
    # because the equity curve sorts by entry time before computing, so a report does not
    # depend on the order outcomes were collected in.
    chronological, per_coin = fixture_runs
    a, b = summarize(chronological.outcomes), summarize(per_coin.outcomes)

    assert a.resolved_trades == b.resolved_trades
    assert a.wins == b.wins and a.losses == b.losses
    assert a.win_rate == pytest.approx(b.win_rate)
    assert a.expectancy == pytest.approx(b.expectancy)
    assert a.profit_factor == pytest.approx(b.profit_factor)
    assert a.max_drawdown == pytest.approx(b.max_drawdown)
    assert a.equity_curve == pytest.approx(b.equity_curve)


def test_the_orders_genuinely_differ(fixture_runs) -> None:  # type: ignore[no-untyped-def]
    # Guards the test above from passing vacuously: if both replays happened to emit in the
    # same order, the sorted comparison would prove less than it appears to.
    chronological, per_coin = fixture_runs
    chrono_order = [(o.symbol, o.entry_time) for o in chronological.outcomes]
    coin_order = [(o.symbol, o.entry_time) for o in per_coin.outcomes]

    assert chrono_order != coin_order
    assert chrono_order == sorted(chrono_order, key=lambda p: p[1])


# --- Simultaneous qualification --------------------------------------------------


def _synchronised(symbol: str, timeframe: str, scale: int, *, rising: bool) -> Candles:
    """Cycling history on a shared grid, so several coins qualify at the same instants."""
    step = timeframe_to_ms(timeframe)
    up, down = 70 * scale, 40 * scale
    trend, counter = (1.006, 0.991) if rising else (0.994, 1.009)
    rows: list[list[float]] = []
    base, stamp = 100.0, 0
    for _cycle in range(6):
        for i in range(up):
            base *= trend
            offset = {0: 0.0, 1: 0.004, 2: 0.007, 3: 0.004, 4: 0.0, 5: -0.003}[i % 6]
            close = base * (1.0 + offset)
            rows.append([stamp, close * 0.997, close * 1.010, close * 0.990, close, 1_000.0 + i])
            stamp += step
        for i in range(down):
            base *= counter
            rows.append([stamp, base * 1.003, base * 1.008, base * 0.992, base, 1_000.0 + i])
            stamp += step
    return Candles(symbol=symbol, timeframe=timeframe, frame=pd.DataFrame(rows, columns=_COLUMNS))


def test_simultaneous_qualification_is_equivalent_too() -> None:
    # Four coins on an identical grid all qualify at the same instants — the case a
    # portfolio cap will later have to arbitrate, and the one most likely to expose an
    # ordering bug in the merged walk.
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    config = replace(FIXTURE_CONFIG, watchlist=symbols)
    ref_step = timeframe_to_ms(config.reference_timeframe)
    scales = {tf: ref_step // timeframe_to_ms(tf) for tf in config.timeframes}
    histories = {
        s: {tf: _synchronised(s, tf, scales[tf], rising=True) for tf in config.timeframes}
        for s in symbols
    }
    btc = {tf: _synchronised("BTC/USDT", tf, scales[tf], rising=True) for tf in config.timeframes}

    chronological, per_coin = _both(config, histories, btc)

    assert len(chronological.outcomes) > 0
    assert _sorted_trades(chronological) == _sorted_trades(per_coin)
    # And several trades really do share an entry instant, or this proves nothing.
    entry_times = [o.entry_time for o in chronological.outcomes]
    assert len(entry_times) != len(set(entry_times))


# --- Lifecycle rules survive the move --------------------------------------------


def test_one_open_trade_per_coin_is_preserved(fixture_runs) -> None:  # type: ignore[no-untyped-def]
    # A coin that re-qualifies while its own trade is still open must not open a second.
    chronological, _ = fixture_runs
    by_symbol: dict[str, list[TradeOutcome]] = {}
    for o in chronological.outcomes:
        by_symbol.setdefault(o.symbol, []).append(o)

    for symbol, trades in by_symbol.items():
        trades.sort(key=lambda o: o.entry_time)
        for earlier, later in zip(trades, trades[1:], strict=False):
            assert earlier.exit_time is not None, f"{symbol} left a trade open then re-entered"
            assert later.entry_time >= earlier.exit_time, f"{symbol} overlapped its own trades"


# --- Coins with missing or partial history ---------------------------------------


def test_a_coin_with_no_history_is_skipped_identically() -> None:
    histories, btc = build_history()
    config = replace(FIXTURE_CONFIG, watchlist=[*FIXTURE_CONFIG.watchlist, "NOHISTORY"])

    chronological, per_coin = _both(config, histories, btc)

    assert _sorted_trades(chronological) == _sorted_trades(per_coin)
    assert not any(o.symbol == "NOHISTORY" for o in chronological.outcomes)


def test_a_coin_whose_history_starts_late_is_handled_identically() -> None:
    # Different coins then have different reference closes, so the merged timeline is a
    # union rather than a shared grid — the case the union is there to handle.
    histories, btc = build_history()
    late = {
        tf: Candles(
            symbol="UPCOIN",
            timeframe=tf,
            frame=candles.frame.iloc[len(candles.frame) // 2 :].reset_index(drop=True),
        )
        for tf, candles in histories["UPCOIN"].items()
    }
    histories = {**histories, "UPCOIN": late}

    chronological, per_coin = _both(FIXTURE_CONFIG, histories, btc)

    assert _sorted_trades(chronological) == _sorted_trades(per_coin)
    assert len(chronological.outcomes) > 0
