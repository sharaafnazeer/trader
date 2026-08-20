"""Replaying coins across processes must change the speed and nothing else.

The parallel path exists only to make measurement affordable. If it changed a single trade
it would be worse than useless — a fast number that disagrees with the slow one is a number
you cannot act on. So the assertion here is equality, not similarity: the same trades, in
the same order, with the same R-multiples.

Coins are independent, which is what makes this safe: nothing in a coin's point-in-time
replay reads another coin. The lifecycle, where coins *do* interact, stays in the parent.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from baseline_fixture import FIXTURE_CONFIG, build_history
from trader.backtester import Backtester, _resolve_workers
from trader.config import BacktestConfig
from trader.metrics import summarize


class _Provider:
    """Serves the shared fixture's frames, so the provider-sourced path can be exercised."""

    def __init__(self, histories, btc):  # type: ignore[no-untyped-def]
        self._frames = {**histories, "BTC/USDT": btc}

    def get_history(self, symbol: str, timeframe: str, start: int, end: int):  # type: ignore[no-untyped-def]
        import pandas as pd

        from trader.market_data import Candles

        by_tf = self._frames.get(symbol)
        if by_tf is None or timeframe not in by_tf:
            empty = pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            return Candles(symbol=symbol, timeframe=timeframe, frame=empty)
        return by_tf[timeframe]


def _run(workers: int):  # type: ignore[no-untyped-def]
    histories, btc = build_history()
    config = replace(
        FIXTURE_CONFIG,
        backtest=BacktestConfig(replay_workers=workers),
    )
    return Backtester().run_from_provider(
        config, _Provider(histories, btc), start=0, end=10**13
    )


@pytest.fixture(scope="module")
def sequential():  # type: ignore[no-untyped-def]
    return _run(workers=1)


@pytest.fixture(scope="module")
def parallel():  # type: ignore[no-untyped-def]
    return _run(workers=3)


def test_the_fixture_actually_trades(sequential) -> None:  # type: ignore[no-untyped-def]
    """Equality between two empty runs would prove nothing."""

    assert len(sequential.outcomes) > 0


def test_parallel_produces_the_same_trades(sequential, parallel) -> None:  # type: ignore[no-untyped-def]
    def key(run):  # type: ignore[no-untyped-def]
        return sorted(
            (o.symbol, o.entry_time, o.exit_time, o.direction, round(o.r_multiple, 12))
            for o in run.outcomes
        )

    assert key(parallel) == key(sequential)


def test_parallel_produces_the_same_report(sequential, parallel) -> None:  # type: ignore[no-untyped-def]
    a, b = summarize(sequential.outcomes), summarize(parallel.outcomes)

    assert (a.resolved_trades, a.wins, a.losses) == (b.resolved_trades, b.wins, b.losses)
    assert a.expectancy == pytest.approx(b.expectancy, rel=1e-12)
    assert a.profit_factor == pytest.approx(b.profit_factor, rel=1e-12)
    assert a.max_drawdown == pytest.approx(b.max_drawdown, rel=1e-12)


def test_the_same_coins_are_skipped_either_way(sequential, parallel) -> None:  # type: ignore[no-untyped-def]
    assert {c.symbol for c in parallel.skipped} == {c.symbol for c in sequential.skipped}


# --- Choosing the worker count ------------------------------------------------------


def test_an_explicit_worker_count_is_honoured_but_never_exceeds_the_coins() -> None:
    assert _resolve_workers(4, coins=20) == 4
    assert _resolve_workers(4, coins=2) == 2


def test_auto_leaves_headroom_and_is_capped() -> None:
    import os

    auto = _resolve_workers(0, coins=100)

    assert auto >= 1
    assert auto <= 16
    assert auto <= max(1, (os.cpu_count() or 1) - 2)


def test_auto_never_spawns_more_workers_than_coins() -> None:
    assert _resolve_workers(0, coins=1) == 1


def test_a_single_worker_stays_in_process() -> None:
    """No pool, no pickling — the path a small run and every test takes."""

    assert _resolve_workers(1, coins=50) == 1
